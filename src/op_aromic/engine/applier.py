"""Two-pass applier: execute a Plan against Automic.

Pass 1 upserts every create/update with outbound references stripped so
no write can fail on a forward reference. Pass 2 PUTs the full payload
for objects whose ref-stripped form differs from the full form. Objects
with no outbound refs (Calendars, Variables, some Jobs) skip pass 2.

Failures inside a pass do not roll back — Automic has no cross-object
transactions. Instead we stop the pass, mark the rest of the pass as
skipped, and return an ApplyResult the caller can print. Re-running
apply is idempotent and will converge the remainder.

Concurrent-edit detection: before each write we GET the target and
compare its ``LastModified`` marker against what the plan saw. A
mismatch aborts that item as a FailedApply(reason=concurrent). Pass
``force=True`` to skip the check.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from op_aromic.client.errors import NotFoundError
from op_aromic.client.http import AutomicClient
from op_aromic.engine.dependency import (
    _KIND_PRECEDENCE,  # reused for stable apply order when the plan lacks a graph
    DependencyGraph,
)
from op_aromic.engine.differ import ObjectDiff
from op_aromic.engine.planner import Plan
from op_aromic.engine.serializer import manifest_to_automic_payload
from op_aromic.models.base import Manifest

# Field Automic uses to expose its optimistic-concurrency marker. The
# exact wire name is unverified (see docs/ISSUES.md); we check the most
# likely candidates and fall back to equality on a handful of other
# timestamp-ish fields. If none are present we treat the check as "passed".
_CONCURRENCY_FIELDS: tuple[str, ...] = (
    "LastModified",
    "OH_LASTMODIFIED",
    "ModifiedAt",
)

_ProgressEvent = Literal[
    "pass1_start", "pass1_apply", "pass2_start", "pass2_apply", "delete",
]
ProgressCallback = Callable[[_ProgressEvent, str], None]


@dataclass(frozen=True)
class SuccessfulApply:
    """One (kind, name) that was applied successfully."""

    kind: str
    name: str
    action: Literal["create", "update", "delete"]


@dataclass(frozen=True)
class FailedApply:
    """One (kind, name) that failed to apply."""

    kind: str
    name: str
    action: Literal["create", "update", "delete"]
    reason: str


@dataclass(frozen=True)
class Skipped:
    """One (kind, name) that was deferred because an earlier item failed."""

    kind: str
    name: str
    action: Literal["create", "update", "delete"]


_ApplyStatus = Literal["success", "partial"]


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of an ``apply`` call."""

    successes: list[SuccessfulApply] = field(default_factory=list)
    failures: list[FailedApply] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    dry_run: bool = False

    @property
    def status(self) -> _ApplyStatus:
        if self.failures or self.skipped:
            return "partial"
        return "success"


def _noop_progress(event: _ProgressEvent, name: str) -> None:
    del event, name  # intentionally no-op


def _strip_refs(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``payload`` with outbound reference lists emptied.

    Mirror of ``dependency._extract_refs_from_spec`` in Automic-native
    casing. Pass 1 submits this form so creates cannot fail on forward
    references.
    """
    if kind == "Workflow":
        return {**payload, "Tasks": []}
    if kind == "Schedule":
        return {**payload, "Entries": []}
    return payload


def _payloads_differ(full: dict[str, Any], stripped: dict[str, Any]) -> bool:
    """True iff pass 2 must re-submit the full payload to wire refs."""
    return full != stripped


def _concurrency_marker(payload: dict[str, Any] | None) -> Any | None:
    if not payload:
        return None
    for f in _CONCURRENCY_FIELDS:
        if f in payload:
            return payload[f]
    return None


def _desired_payload_from_diff(diff: ObjectDiff) -> dict[str, Any] | None:
    """Rebuild an Automic payload from the diff's desired canonical dict.

    We keep this in sync with ``serializer.manifest_to_automic_payload``
    by round-tripping through a fresh Manifest; this avoids the applier
    owning a second serializer and keeps the two code paths identical.
    """
    if diff.desired is None:
        return None
    desired = diff.desired
    kind = diff.kind
    # Reconstruct a Manifest from canonical; the normalizer is symmetric
    # for the modelled fields.
    spec = _canonical_to_spec(kind, desired)
    manifest = Manifest.model_validate(
        {
            "apiVersion": "aromic.io/v1",
            "kind": kind,
            "metadata": {"name": desired["name"], "folder": desired["folder"]},
            "spec": spec,
        },
    )
    return manifest_to_automic_payload(manifest)


def _canonical_to_spec(kind: str, canonical: dict[str, Any]) -> dict[str, Any]:
    """Inverse of the per-kind normalizer: canonical dict → manifest spec."""
    if kind == "Workflow":
        return {
            "title": canonical.get("title"),
            "tasks": [
                {
                    "name": t["name"],
                    "ref": {"kind": t["ref"]["kind"], "name": t["ref"]["name"]},
                    "after": list(t.get("after", [])),
                }
                for t in canonical.get("tasks", [])
            ],
        }
    if kind == "Job":
        return {
            "title": canonical.get("title"),
            "host": canonical.get("host", ""),
            "login": canonical.get("login", ""),
            "script": canonical.get("script", ""),
            "script_type": canonical.get("script_type", "OS"),
        }
    if kind == "Schedule":
        return {
            "title": canonical.get("title"),
            "entries": [
                {
                    "task": {"kind": e["task"]["kind"], "name": e["task"]["name"]},
                    "start_time": e.get("start_time", "00:00"),
                    "calendar_keyword": e.get("calendar_keyword"),
                }
                for e in canonical.get("entries", [])
            ],
        }
    if kind == "Calendar":
        return {
            "title": canonical.get("title"),
            "keywords": [
                {
                    "name": k["name"],
                    "type": k.get("type", "STATIC"),
                    "values": list(k.get("values", [])),
                }
                for k in canonical.get("keywords", [])
            ],
        }
    if kind == "Variable":
        return {
            "title": canonical.get("title"),
            "var_type": canonical.get("var_type", "STATIC"),
            "entries": [
                {"key": e["key"], "value": e.get("value", "")}
                for e in canonical.get("entries", [])
            ],
        }
    return {}


def _sort_creates_updates(
    diffs: list[ObjectDiff], graph: DependencyGraph,
) -> list[ObjectDiff]:
    """Order pass-1 work by topological level, kind precedence inside a level."""
    from op_aromic.engine.dependency import topological_order

    levels = topological_order(graph)
    order_index: dict[tuple[str, str], int] = {}
    for i, level in enumerate(levels):
        for node in level:
            order_index[node] = i

    def _key(diff: ObjectDiff) -> tuple[int, int, str]:
        node = (diff.kind, diff.name)
        # Nodes missing from the graph (e.g. synthetic delete-only plans)
        # sort after everything known.
        level = order_index.get(node, len(levels))
        rank = _KIND_PRECEDENCE.get(diff.kind, len(_KIND_PRECEDENCE))
        return (level, rank, diff.name)

    return sorted(diffs, key=_key)


def _refetch_marker(client: AutomicClient, name: str) -> Any | None:
    """Return the current concurrency marker for ``name`` or None if absent."""
    try:
        current = client.get_object(name)
    except NotFoundError:
        return None
    return _concurrency_marker(current)


def _check_concurrent_edit(
    *,
    client: AutomicClient,
    diff: ObjectDiff,
    force: bool,
    planned_marker: Any | None,
) -> str | None:
    """Returns a failure reason if the marker drifted, else None.

    The normalizer strips ``LastModified`` from ``diff.actual`` so the
    differ can compare apples-to-apples — which means the applier has to
    capture the marker itself at the moment it starts processing each
    object (``planned_marker``). A second fetch immediately before the
    write (``current_marker``) catches any edit that landed in between.
    This approximates the Terraform-style plan/apply window with a small
    race of our own; ``--force`` skips the check entirely.
    """
    if force or planned_marker is None:
        return None
    current_marker = _refetch_marker(client, diff.name)
    if current_marker is None:
        # Object vanished between the two fetches — let the write surface
        # a NotFoundError naturally instead of masking it here.
        return None
    if current_marker != planned_marker:
        return (
            f"concurrent edit detected: LastModified was "
            f"{planned_marker!r}, now {current_marker!r}. Re-run plan or "
            f"pass --force."
        )
    return None


PlanMarkers = dict[tuple[str, str], Any]


def apply(
    plan: Plan,
    client: AutomicClient,
    graph: DependencyGraph,
    *,
    dry_run: bool = False,
    force: bool = False,
    on_progress: ProgressCallback | None = None,
    plan_markers: PlanMarkers | None = None,
) -> ApplyResult:
    """Execute ``plan`` against ``client`` in two passes (upsert, wire).

    Deletes run first, then pass-1 upserts (ref-stripped), then pass-2
    wiring. Deletion is not ordered through the graph because
    ``engine.destroyer`` is the canonical destroy path; applier-driven
    deletes only happen when ``plan.deletes`` is populated via
    ``planner --prune``.
    """
    progress = on_progress or _noop_progress
    successes: list[SuccessfulApply] = []
    failures: list[FailedApply] = []
    skipped: list[Skipped] = []
    markers = plan_markers or {}

    # ---- Deletes (simple single-pass) ----------------------------------
    for diff in plan.deletes:
        if dry_run:
            progress("delete", diff.name)
            successes.append(
                SuccessfulApply(kind=diff.kind, name=diff.name, action="delete"),
            )
            continue
        planned_marker = markers.get((diff.kind, diff.name))
        reason = _check_concurrent_edit(
            client=client, diff=diff, force=force, planned_marker=planned_marker,
        )
        if reason is not None:
            failures.append(
                FailedApply(
                    kind=diff.kind, name=diff.name, action="delete", reason=reason,
                ),
            )
            continue
        try:
            client.delete_object(diff.name)
        except Exception as exc:
            failures.append(
                FailedApply(
                    kind=diff.kind, name=diff.name, action="delete", reason=str(exc),
                ),
            )
            continue
        progress("delete", diff.name)
        successes.append(
            SuccessfulApply(kind=diff.kind, name=diff.name, action="delete"),
        )

    # ---- Pass 1: upsert with refs stripped -----------------------------
    writes = plan.creates + plan.updates
    ordered = _sort_creates_updates(writes, graph)
    pass2_needed: list[tuple[ObjectDiff, dict[str, Any]]] = []

    progress("pass1_start", "")
    pass1_failed = False
    for diff in ordered:
        if pass1_failed:
            skipped.append(
                Skipped(
                    kind=diff.kind,
                    name=diff.name,
                    action="create" if diff.action == "create" else "update",
                ),
            )
            continue

        full_payload = _desired_payload_from_diff(diff)
        if full_payload is None:
            # Shouldn't happen for create/update, but keep the type checker happy.
            continue
        stripped = _strip_refs(diff.kind, full_payload)

        if dry_run:
            progress("pass1_apply", diff.name)
            successes.append(
                SuccessfulApply(
                    kind=diff.kind,
                    name=diff.name,
                    action="create" if diff.action == "create" else "update",
                ),
            )
            if _payloads_differ(full_payload, stripped):
                pass2_needed.append((diff, full_payload))
            continue

        # Creates have no baseline marker; only check updates where the
        # caller (CLI) captured a plan-time marker.
        planned_marker = (
            markers.get((diff.kind, diff.name)) if diff.action == "update" else None
        )
        reason = _check_concurrent_edit(
            client=client,
            diff=diff,
            force=force,
            planned_marker=planned_marker,
        )
        if reason is not None:
            failures.append(
                FailedApply(
                    kind=diff.kind,
                    name=diff.name,
                    action="create" if diff.action == "create" else "update",
                    reason=reason,
                ),
            )
            pass1_failed = True
            continue

        try:
            if diff.action == "create":
                client.create_object(stripped)
            else:
                client.update_object(diff.name, stripped)
        except Exception as exc:
            failures.append(
                FailedApply(
                    kind=diff.kind,
                    name=diff.name,
                    action="create" if diff.action == "create" else "update",
                    reason=str(exc),
                ),
            )
            pass1_failed = True
            continue

        progress("pass1_apply", diff.name)
        successes.append(
            SuccessfulApply(
                kind=diff.kind,
                name=diff.name,
                action="create" if diff.action == "create" else "update",
            ),
        )
        if _payloads_differ(full_payload, stripped):
            pass2_needed.append((diff, full_payload))

    # ---- Pass 2: wire outbound references ------------------------------
    progress("pass2_start", "")
    pass2_failed = False
    for diff, full_payload in pass2_needed:
        if pass2_failed:
            skipped.append(
                Skipped(kind=diff.kind, name=diff.name, action="update"),
            )
            continue
        if dry_run:
            progress("pass2_apply", diff.name)
            continue
        try:
            client.update_object(diff.name, full_payload)
        except Exception as exc:
            failures.append(
                FailedApply(
                    kind=diff.kind,
                    name=diff.name,
                    action="update",
                    reason=f"pass 2 failed: {exc}",
                ),
            )
            pass2_failed = True
            continue
        progress("pass2_apply", diff.name)

    return ApplyResult(
        successes=successes,
        failures=failures,
        skipped=skipped,
        dry_run=dry_run,
    )


def capture_plan_markers(
    plan: Plan, client: AutomicClient,
) -> PlanMarkers:
    """Snapshot a concurrency marker for every update in ``plan``.

    Intended to be called in the CLI right after ``build_plan`` returns
    and right before ``apply`` runs — the window in which a concurrent
    edit could slip in. Creates and noops are skipped because there is
    nothing to guard against.
    """
    markers: PlanMarkers = {}
    for diff in plan.updates:
        markers[(diff.kind, diff.name)] = _refetch_marker(client, diff.name)
    for diff in plan.deletes:
        markers[(diff.kind, diff.name)] = _refetch_marker(client, diff.name)
    return markers


__all__ = [
    "ApplyResult",
    "FailedApply",
    "PlanMarkers",
    "ProgressCallback",
    "Skipped",
    "SuccessfulApply",
    "apply",
    "capture_plan_markers",
]
