"""Reverse-dependency destroyer.

Deletes every declared manifest object in the inverse of the apply
order: dependents first, dependencies second. By default it refuses to
delete anything that does not carry the ``aromic.io/managed-by``
annotation — passing ``only_managed=False`` lifts that guard.

A missing target (404) is *not* a failure. Destroy is idempotent: if an
object is already gone, we log a success and move on.

Continues past individual DELETE failures (unlike the applier's
pass-halt behaviour) because destroying siblings in parallel has no
cross-object state that a failure could corrupt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from op_aromic.client.http import AutomicClient
from op_aromic.engine.applier import FailedApply, SuccessfulApply
from op_aromic.engine.dependency import (
    DependencyGraph,
    topological_order,
)
from op_aromic.engine.loader import LoadedManifest

_MANAGED_BY_ANNOTATION = "aromic.io/managed-by"
_MANAGED_BY_VALUE = "op-aromic"

_DestroyStatus = Literal["success", "partial"]


@dataclass(frozen=True)
class RefusedDelete:
    """A declared object skipped because it isn't flagged as managed."""

    kind: str
    name: str
    reason: str


@dataclass(frozen=True)
class DestroyResult:
    """Outcome of a ``destroy`` call."""

    successes: list[SuccessfulApply] = field(default_factory=list)
    failures: list[FailedApply] = field(default_factory=list)
    refused: list[RefusedDelete] = field(default_factory=list)
    dry_run: bool = False

    @property
    def status(self) -> _DestroyStatus:
        if self.failures or self.refused:
            return "partial"
        return "success"


def _is_managed(payload: dict[str, Any]) -> bool:
    """Mirror of planner._is_managed so both detection paths agree.

    Checks two reasonable locations — an Annotations sub-map (if Automic
    exposes one) or a marker substring inside Documentation. See the
    ISSUES.md entry "Managed-object prune heuristic" for the rationale.
    """
    annotations = payload.get("Annotations")
    if (
        isinstance(annotations, dict)
        and annotations.get(_MANAGED_BY_ANNOTATION) == _MANAGED_BY_VALUE
    ):
        return True
    doc = payload.get("Documentation")
    return isinstance(doc, str) and f"{_MANAGED_BY_ANNOTATION}={_MANAGED_BY_VALUE}" in doc


def destroy(
    loaded: list[LoadedManifest],
    client: AutomicClient,
    graph: DependencyGraph,
    *,
    only_managed: bool = True,
    dry_run: bool = False,
) -> DestroyResult:
    """Delete every loaded manifest in reverse-dependency order.

    ``graph`` is the same DependencyGraph the applier uses; we walk its
    topological levels in reverse (last level first, first level last)
    so dependents are gone before their dependencies.
    """
    successes: list[SuccessfulApply] = []
    failures: list[FailedApply] = []
    refused: list[RefusedDelete] = []

    declared: set[tuple[str, str]] = {
        (lm.manifest.kind, lm.manifest.metadata.name) for lm in loaded
    }

    levels = topological_order(graph)
    # Reverse both the level ordering (outer) and the kind precedence
    # within each level (inner) so Workflow deletes before Job, which
    # deletes before Variable, etc.
    for level in reversed(levels):
        for node in reversed(level):
            if node not in declared:
                # Ref-only node (e.g. undeclared referenced target) — skip.
                continue
            kind, name = node
            current = client.get_object_or_none(name)
            if current is None:
                # Already gone — idempotent destroy treats as success.
                successes.append(
                    SuccessfulApply(kind=kind, name=name, action="delete"),
                )
                continue

            if only_managed and not _is_managed(current):
                refused.append(
                    RefusedDelete(
                        kind=kind,
                        name=name,
                        reason=(
                            f"{name} is not tagged aromic.io/managed-by="
                            "op-aromic; pass --only-managed=false to override"
                        ),
                    ),
                )
                continue

            if dry_run:
                successes.append(
                    SuccessfulApply(kind=kind, name=name, action="delete"),
                )
                continue

            try:
                client.delete_object(name)
            except Exception as exc:
                failures.append(
                    FailedApply(
                        kind=kind, name=name, action="delete", reason=str(exc),
                    ),
                )
                continue
            successes.append(
                SuccessfulApply(kind=kind, name=name, action="delete"),
            )

    return DestroyResult(
        successes=successes,
        failures=failures,
        refused=refused,
        dry_run=dry_run,
    )


__all__ = ["DestroyResult", "RefusedDelete", "destroy"]
