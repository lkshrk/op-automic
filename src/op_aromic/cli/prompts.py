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
    verb = "Would destroy" if result.dry_run else "Destroyed"
    out.print(
        f"[bold]{verb}:[/] {len(result.successes)} deleted, "
        f"{len(result.failures)} failed, "
        f"{len(result.refused)} refused, "
        f"{len(result.not_supported)} not supported (remove manually)",
    )


# Hint mapping: map substrings in FailedApply.reason → one-line operator
# hint. First match wins; order matters (more specific before more general).
# Hints are appended in gray `[hint: ...]` after the failure line.
_FAILURE_HINTS: tuple[tuple[str, str], ...] = (
    ("401", "check credentials; run `aromic auth check`"),
    ("Unauthorized", "check credentials; run `aromic auth check`"),
    ("403", "user lacks permission on this object; check Automic role"),
    ("Forbidden", "user lacks permission on this object; check Automic role"),
    ("auto_create_folders", "pass --auto-create-folders=true or create FOLD first"),
    ("Folder", "verify metadata.folder exists in Automic or enable auto-create"),
    ("concurrent edit detected", "someone changed the object; re-plan or pass --force"),
    ("429", "rate limited; raise retry_statuses/retry_base_delay_ms in aromic.yaml"),
    ("Too Many Requests", "rate limited; raise retry settings in aromic.yaml"),
    ("500", "server error; check Automic logs and retry"),
    ("502", "server error; check Automic logs and retry"),
    ("503", "server error; check Automic logs and retry"),
)


def failure_hint(reason: str) -> str | None:
    """Return a one-line hint for a FailedApply.reason, or None."""
    for needle, hint in _FAILURE_HINTS:
        if needle in reason:
            return hint
    return None


def preview_destroy(
    result: DestroyResult, console: Console | None = None,
) -> None:
    """Print the ordered list of objects a dry-run destroy would delete.

    Only meaningful for ``result.dry_run=True``. Operators run
    ``destroy --dry-run`` to see exactly what would be removed and in what
    reverse-dependency order before committing to a real destroy.
    """
    out = console or Console()
    if not result.dry_run:
        return
    if result.successes:
        out.print("[bold]Would delete (reverse-dependency order):[/]")
        for i, s in enumerate(result.successes, start=1):
            out.print(f"  {i}. {s.kind}/{s.name}")
    if result.refused:
        out.print(f"[yellow]Would refuse ({len(result.refused)}):[/]")
        for r in result.refused:
            out.print(f"  - {r.kind}/{r.name} — {r.reason}")


__all__ = [
    "confirm_apply",
    "confirm_destroy",
    "failure_hint",
    "preview_destroy",
    "print_apply_result",
    "summarise_destroy",
]
