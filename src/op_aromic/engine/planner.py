"""Build a Plan by diffing every desired manifest against live Automic state.

A Plan is a grouped list of ObjectDiff records: creates, updates, noops,
and (when ``prune=True``) deletes for managed-but-no-longer-declared
objects. The planner is strictly read-only — no writes happen here or in
the layers below it on this path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from op_aromic.client.api import AutomicAPI
from op_aromic.engine.differ import ObjectDiff, compute_diff
from op_aromic.engine.loader import LoadedManifest
from op_aromic.engine.normalizer import (
    to_canonical_from_automic,
    to_canonical_from_manifest,
)

_MANAGED_BY_ANNOTATION = "aromic.io/managed-by"
_MANAGED_BY_VALUE = "op-aromic"


@dataclass(frozen=True)
class Plan:
    """Aggregate of ObjectDiffs produced by ``build_plan``."""

    creates: list[ObjectDiff] = field(default_factory=list)
    updates: list[ObjectDiff] = field(default_factory=list)
    deletes: list[ObjectDiff] = field(default_factory=list)
    noops: list[ObjectDiff] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.creates or self.updates or self.deletes)

    @property
    def total_changes(self) -> int:
        return len(self.creates) + len(self.updates) + len(self.deletes)

    def all_diffs(self) -> list[ObjectDiff]:
        return [*self.creates, *self.updates, *self.deletes, *self.noops]


def _filter_target(
    loaded: list[LoadedManifest], target: str | None,
) -> list[LoadedManifest]:
    if target is None:
        return loaded
    return [lm for lm in loaded if lm.manifest.metadata.name == target]


def _is_managed(payload: dict[str, Any]) -> bool:
    """Best-effort: treat an object as managed if it carries the annotation.

    Automic does not natively support annotations the way Kubernetes does, so
    the exporter / applier writes the marker into a Documentation-ish field.
    For now we check two reasonable locations and accept either match.
    """
    annotations = payload.get("Annotations")
    annotated = (
        isinstance(annotations, dict)
        and annotations.get(_MANAGED_BY_ANNOTATION) == _MANAGED_BY_VALUE
    )
    doc = payload.get("Documentation")
    documented = (
        isinstance(doc, str)
        and f"{_MANAGED_BY_ANNOTATION}={_MANAGED_BY_VALUE}" in doc
    )
    return annotated or documented


def build_plan(
    loaded: list[LoadedManifest],
    api: AutomicAPI,
    *,
    prune: bool = False,
    target: str | None = None,
) -> Plan:
    """Compute a Plan against ``api`` for the provided manifests.

    ``target`` narrows the scope to a single manifest by ``metadata.name``.
    ``prune`` enables delete detection for managed-but-undeclared objects
    fetched via ``list_objects_typed``.
    """
    manifests = _filter_target(loaded, target)

    creates: list[ObjectDiff] = []
    updates: list[ObjectDiff] = []
    noops: list[ObjectDiff] = []
    declared_names: set[tuple[str, str]] = set()

    for lm in manifests:
        kind = lm.manifest.kind
        name = lm.manifest.metadata.name
        folder = lm.manifest.metadata.folder
        declared_names.add((kind, name))

        desired_canonical = to_canonical_from_manifest(lm.manifest)
        actual_raw = api.get_object_typed(kind, name)
        actual_canonical = (
            to_canonical_from_automic(kind, actual_raw) if actual_raw is not None else None
        )

        diff = compute_diff(
            kind=kind,
            name=name,
            folder=folder,
            desired=desired_canonical,
            actual=actual_canonical,
        )
        if diff.action == "create":
            creates.append(diff)
        elif diff.action == "update":
            updates.append(diff)
        else:
            noops.append(diff)

    deletes: list[ObjectDiff] = []
    if prune:
        # Only the kinds we know how to diff; skip others to avoid surprises.
        for kind in ("Workflow", "Job", "Schedule", "Calendar", "Variable"):
            for remote in api.list_objects_typed(kind):
                remote_name = remote.get("Name")
                if not isinstance(remote_name, str):
                    continue
                if (kind, remote_name) in declared_names:
                    continue
                if not _is_managed(remote):
                    continue
                remote_canonical = to_canonical_from_automic(kind, remote)
                deletes.append(
                    compute_diff(
                        kind=kind,
                        name=remote_name,
                        folder=remote.get("Folder", ""),
                        desired=None,
                        actual=remote_canonical,
                    ),
                )

    return Plan(creates=creates, updates=updates, deletes=deletes, noops=noops)


__all__ = ["Plan", "build_plan"]
