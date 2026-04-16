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
from op_aromic.engine.parallel import ParallelExecutionError, parallel_map

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


def build_plan_parallel(
    loaded: list[LoadedManifest],
    api: AutomicAPI,
    *,
    max_workers: int = 8,
    prune: bool = False,
    target: str | None = None,
) -> Plan:
    """Parallelised variant of :func:`build_plan`.

    Fetches ``get_object_typed`` for every desired manifest concurrently
    through ``parallel_map`` so large manifest sets do not pay one
    round-trip of latency per object. Output order (``creates`` /
    ``updates`` / ``noops``) mirrors input order — the actual HTTP calls
    overlap but the resulting Plan is deterministic for stable snapshots.

    Delegates to :func:`build_plan` when ``max_workers <= 1`` so there is
    one canonical sequential path for tests that assert call order.
    """
    if max_workers <= 1:
        return build_plan(loaded, api, prune=prune, target=target)

    manifests = _filter_target(loaded, target)

    def _fetch(lm: LoadedManifest) -> dict[str, Any] | None:
        return api.get_object_typed(lm.manifest.kind, lm.manifest.metadata.name)

    try:
        actual_raws = parallel_map(_fetch, manifests, max_workers=max_workers)
    except ParallelExecutionError as exc:
        # Unwrap so the CLI's ``except AutomicError`` boundary still catches
        # transport / auth failures. If multiple items failed we surface the
        # first — the parallel error still carries all of them for callers
        # that want richer reporting.
        if exc.failures:
            raise exc.failures[0][1] from exc
        raise

    creates: list[ObjectDiff] = []
    updates: list[ObjectDiff] = []
    noops: list[ObjectDiff] = []
    declared_names: set[tuple[str, str]] = set()

    for lm, actual_raw in zip(manifests, actual_raws, strict=True):
        kind = lm.manifest.kind
        name = lm.manifest.metadata.name
        folder = lm.manifest.metadata.folder
        declared_names.add((kind, name))

        desired_canonical = to_canonical_from_manifest(lm.manifest)
        actual_canonical = (
            to_canonical_from_automic(kind, actual_raw)
            if actual_raw is not None
            else None
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
        # Prune pass still iterates sequentially — list_objects already
        # paginates internally, and the parallel win is tiny relative to
        # the risk of N simultaneous full listings.
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


__all__ = ["Plan", "build_plan", "build_plan_parallel"]
