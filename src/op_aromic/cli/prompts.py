"""Interactive confirmation prompts for destructive CLI commands.

We reject Typer's default y/n confirm because it is too permissive for
plan/apply workflows — an accidental ``y`` on a 500-object delete is
unrecoverable. ``confirm_apply`` and ``confirm_destroy`` require the
exact four-character word ``yes`` (case-sensitive) before returning
``True``.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from op_aromic.engine.destroyer import DestroyResult
from op_aromic.engine.loader import LoadedManifest
from op_aromic.engine.planner import Plan

_EXPECTED = "yes"

# Injected for tests that don't want to drive stdin through CliRunner.
_Reader = Callable[[], str]


def _default_reader() -> str:
    return input()


def _print_apply_summary(plan: Plan, console: Console) -> None:
    console.print(
        f"[bold]Plan summary:[/] "
        f"{len(plan.creates)} to create, "
        f"{len(plan.updates)} to update, "
        f"{len(plan.deletes)} to delete, "
        f"{len(plan.noops)} unchanged",
    )


def confirm_apply(
    plan: Plan,
    *,
    console: Console | None = None,
    reader: _Reader | None = None,
) -> bool:
    """Print plan summary, require exact 'yes' typed verbatim."""
    out = console or Console()
    _print_apply_summary(plan, out)
    out.print("Type 'yes' to apply, anything else to abort: ", end="")
    answer = (reader or _default_reader)()
    return answer == _EXPECTED


def confirm_destroy(
    loaded: list[LoadedManifest],
    *,
    console: Console | None = None,
    reader: _Reader | None = None,
) -> bool:
    """Print destroy summary, require exact 'yes' typed verbatim."""
    out = console or Console()
    out.print(
        f"[bold red]Destroy:[/] will delete up to {len(loaded)} managed object(s).",
    )
    out.print("Type 'yes' to destroy, anything else to abort: ", end="")
    answer = (reader or _default_reader)()
    return answer == _EXPECTED


def print_apply_result(
    result_events: list[str], console: Console | None = None,
) -> None:
    """Utility for `apply`/`destroy` to print their outcome summaries.

    Kept here so the CLI layer has a single place to format result lines.
    """
    out = console or Console()
    for line in result_events:
        out.print(line)


def summarise_destroy(result: DestroyResult, console: Console | None = None) -> None:
    """Render counts for a DestroyResult."""
    out = console or Console()
    out.print(
        f"[bold]Destroyed:[/] {len(result.successes)} deleted, "
        f"{len(result.failures)} failed, "
        f"{len(result.refused)} refused, "
        f"{len(result.not_supported)} not supported (remove manually)",
    )


__all__ = [
    "confirm_apply",
    "confirm_destroy",
    "print_apply_result",
    "summarise_destroy",
]
