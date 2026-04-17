"""Rich-based rendering of a Plan.

The renderer is deterministic: same Plan produces byte-identical output
aside from ANSI control codes, which the caller can suppress by passing a
non-colour Console. Tests rely on this to assert ``--no-color`` dropping
ANSI without comparing image-level output.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

from op_aromic.engine.differ import FieldChange, ObjectDiff
from op_aromic.engine.planner import Plan

_ACTION_STYLE: dict[str, str] = {
    "create": "bold green",
    "update": "bold yellow",
    "delete": "bold red",
    "noop": "dim",
}

_ACTION_PREFIX: dict[str, str] = {
    "create": "+",
    "update": "~",
    "delete": "-",
    "noop": " ",
}


def _fmt_value(value: Any) -> str:
    if value is None:
        return "<none>"
    if isinstance(value, str):
        return value
    return repr(value)


def _render_diff(diff: ObjectDiff, console: Console) -> None:
    prefix = _ACTION_PREFIX[diff.action]
    style = _ACTION_STYLE[diff.action]
    header = f"{prefix} {diff.action.upper():<6} {diff.kind:<9} {diff.folder}/{diff.name}"
    console.print(header, style=style)
    for change in diff.changes:
        _render_change(change, console)


def _render_change(change: FieldChange, console: Console) -> None:
    # Two-line rendering per path makes diffs copy-pasteable into tickets.
    path = change.path
    if change.kind == "added":
        console.print(f"    + {path}: {_fmt_value(change.after)}", style="green")
    elif change.kind == "removed":
        console.print(f"    - {path}: {_fmt_value(change.before)}", style="red")
    else:
        console.print(f"    ~ {path}: {_fmt_value(change.before)} -> {_fmt_value(change.after)}",
                      style="yellow")


def render_plan(plan: Plan, console: Console) -> None:
    """Print a Plan to ``console`` with one ObjectDiff per block."""
    if not plan.has_changes and not plan.noops:
        console.print("No manifests matched; nothing to plan.", style="dim")
        return

    for diff in plan.creates:
        _render_diff(diff, console)
    for diff in plan.updates:
        _render_diff(diff, console)
    for diff in plan.deletes:
        _render_diff(diff, console)

    if not plan.has_changes:
        console.print("No changes. Infrastructure is up to date.", style="bold green")
        return

    summary = (
        f"{len(plan.creates)} to create, "
        f"{len(plan.updates)} to update, "
        f"{len(plan.deletes)} to delete"
    )
    console.print(summary, style="bold")


def plan_to_json_dict(plan: Plan) -> dict[str, Any]:
    """Serialise a Plan to a JSON-compatible dict (for --out plan.json)."""
    return {
        "creates": [_diff_to_dict(d) for d in plan.creates],
        "updates": [_diff_to_dict(d) for d in plan.updates],
        "deletes": [_diff_to_dict(d) for d in plan.deletes],
        "noops": [_diff_to_dict(d) for d in plan.noops],
    }


def envelope(
    *,
    command: str,
    status: str,
    summary: dict[str, Any],
    details: dict[str, Any] | list[Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical --output json envelope.

    Shape: ``{"command": ..., "status": ..., "summary": {...}, "details": {...}}``.
    ``status`` is one of ``"ok"``, ``"changes"``, ``"errors"``, ``"partial"``,
    ``"aborted"``. Callers wire these to their own exit code conventions.

    Kept here (and not in the app module) so tests can import and reuse
    the envelope shape without dragging in Typer.
    """
    return {
        "command": command,
        "status": status,
        "summary": summary,
        "details": details if details is not None else {},
    }


def _diff_to_dict(diff: ObjectDiff) -> dict[str, Any]:
    return {
        "action": diff.action,
        "kind": diff.kind,
        "name": diff.name,
        "folder": diff.folder,
        "desired": diff.desired,
        "actual": diff.actual,
        "changes": [
            {
                "path": c.path,
                "before": c.before,
                "after": c.after,
                "kind": c.kind,
            }
            for c in diff.changes
        ],
    }


__all__ = ["envelope", "plan_to_json_dict", "render_plan"]
