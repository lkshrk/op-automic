"""Root CLI application."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from op_aromic import __version__
from op_aromic.cli.output import plan_to_json_dict, render_plan
from op_aromic.cli.prompts import confirm_apply, confirm_destroy, summarise_destroy
from op_aromic.client.api import AutomicAPI
from op_aromic.client.errors import AutomicError
from op_aromic.client.http import AutomicClient
from op_aromic.config.settings import AutomicSettings
from op_aromic.engine.applier import (
    ApplyResult,
    capture_plan_markers,
)
from op_aromic.engine.applier import (
    apply as apply_plan,
)
from op_aromic.engine.dependency import CyclicDependencyError, build_graph
from op_aromic.engine.destroyer import destroy as destroy_objects
from op_aromic.engine.differ import FieldChange, ObjectDiff
from op_aromic.engine.errors import EngineError
from op_aromic.engine.loader import load_manifests
from op_aromic.engine.planner import Plan, build_plan
from op_aromic.engine.validator import Issue, Severity, validate_manifests

app = typer.Typer(
    name="aromic",
    help="GitOps CLI for Broadcom Automic Workload Automation.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Module-level console so tests can capture via CliRunner's StringIO.
_console = Console()
_error_console = Console(stderr=True)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"aromic {__version__}")
        raise typer.Exit()


@app.callback()
def _callback(
    version: bool | None = typer.Option(
        None,
        "--version",
        "-V",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose output."),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file.",
    ),
) -> None:
    """GitOps CLI for Broadcom Automic Workload Automation."""


def _format_issue(prefix: str, issue: Issue) -> str:
    location = f"{issue.source_path}"
    if issue.doc_index:
        location += f" (doc {issue.doc_index})"
    return f"{prefix} {location}: {issue.message}"


def _print_issues(issues: list[Issue], severity: Severity) -> None:
    if severity is Severity.ERROR:
        style = "bold red"
        prefix = "ERROR"
    else:
        style = "bold yellow"
        prefix = "WARN"
    for issue in issues:
        _error_console.print(_format_issue(prefix, issue), style=style)


@app.command()
def validate(
    path: str = typer.Argument(".", help="Path to YAML manifests."),
    strict: bool = typer.Option(False, "--strict", help="Fail on warnings."),
) -> None:
    """Validate YAML manifests against the schema. No network calls.

    Exit codes:
      0 — clean (no errors; warnings only allowed when --strict is off)
      1 — one or more errors (parse, schema, or cross-doc rules)
      2 — warnings present and --strict is set
    """
    target = Path(path)
    try:
        loaded = load_manifests(target)
    except EngineError as exc:
        _error_console.print(f"[bold red]ERROR[/] {exc}")
        raise typer.Exit(code=1) from exc

    report = validate_manifests(loaded)

    _print_issues(report.errors, Severity.ERROR)
    _print_issues(report.warnings, Severity.WARNING)

    if report.errors:
        _error_console.print(
            f"[bold red]validation failed:[/] {len(report.errors)} error(s), "
            f"{len(report.warnings)} warning(s) across {len(loaded)} manifest(s)",
        )
        raise typer.Exit(code=1)

    if report.warnings and strict:
        _error_console.print(
            f"[bold yellow]--strict:[/] {len(report.warnings)} warning(s) "
            "treated as failures",
        )
        raise typer.Exit(code=2)

    _console.print(
        f"[bold green]OK[/] {len(loaded)} manifest(s) validated, "
        f"{len(report.warnings)} warning(s)",
    )


def _build_api_client(settings: AutomicSettings) -> AutomicClient:
    # Extracted so tests can monkeypatch settings construction.
    return AutomicClient(settings)


def _load_and_validate(target_path: Path) -> list:  # type: ignore[type-arg]
    """Shared loader path for plan/apply/destroy.

    Raises typer.Exit(1) on malformed manifests. We delegate to validator
    so cross-doc rules catch typos before any network round-trip.
    """
    try:
        loaded = load_manifests(target_path)
    except EngineError as exc:
        _error_console.print(f"[bold red]ERROR[/] {exc}")
        raise typer.Exit(code=1) from exc

    report = validate_manifests(loaded)
    if report.errors:
        _print_issues(report.errors, Severity.ERROR)
        _error_console.print(
            f"[bold red]validation failed:[/] {len(report.errors)} error(s); "
            "fix manifests before continuing.",
        )
        raise typer.Exit(code=1)
    return loaded


@app.command()
def plan(
    path: str = typer.Argument(".", help="Path to YAML manifests."),
    target: str | None = typer.Option(
        None, "--target", "-t", help="Plan for a single metadata.name only.",
    ),
    prune: bool = typer.Option(
        False, "--prune", help="Detect managed-but-undeclared objects as deletes.",
    ),
    out: str | None = typer.Option(
        None, "--out", help="Write the plan as JSON to this path.",
    ),
    no_color: bool = typer.Option(
        False, "--no-color", help="Suppress ANSI colour codes in rendered output.",
    ),
) -> None:
    """Show what would change without applying. Read-only.

    Exit codes:
      0 — no changes pending
      1 — error (bad manifests, auth failure, transport failure)
      2 — one or more changes pending
    """
    target_path = Path(path)
    loaded = _load_and_validate(target_path)

    settings = AutomicSettings()
    render_console = Console(no_color=no_color, force_terminal=not no_color)

    try:
        with _build_api_client(settings) as client:
            api = AutomicAPI(client)
            the_plan = build_plan(loaded, api, prune=prune, target=target)
    except AutomicError as exc:
        _error_console.print(f"[bold red]API error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    render_plan(the_plan, render_console)

    if out is not None:
        Path(out).write_text(json.dumps(plan_to_json_dict(the_plan), indent=2))

    if the_plan.has_changes:
        raise typer.Exit(code=2)


def _plan_from_file(path: Path) -> Plan:
    """Reconstruct a Plan from --plan-file JSON (produced by `plan --out`)."""
    data = json.loads(path.read_text())

    def _diff(d: dict) -> ObjectDiff:  # type: ignore[type-arg]
        return ObjectDiff(
            action=d["action"],
            kind=d["kind"],
            name=d["name"],
            folder=d["folder"],
            desired=d.get("desired"),
            actual=d.get("actual"),
            changes=[
                FieldChange(
                    path=c["path"],
                    before=c.get("before"),
                    after=c.get("after"),
                    kind=c["kind"],
                )
                for c in d.get("changes", [])
            ],
        )

    return Plan(
        creates=[_diff(d) for d in data.get("creates", [])],
        updates=[_diff(d) for d in data.get("updates", [])],
        deletes=[_diff(d) for d in data.get("deletes", [])],
        noops=[_diff(d) for d in data.get("noops", [])],
    )


def _exit_code_for_apply(result: ApplyResult) -> int:
    if result.status == "success":
        return 0
    return 2


@app.command()
def apply(
    path: str = typer.Argument(".", help="Path to YAML manifests."),
    auto_approve: bool = typer.Option(
        False, "--auto-approve", help="Skip the yes/no confirmation prompt.",
    ),
    plan_file: str | None = typer.Option(
        None,
        "--plan-file",
        help="Trust a plan.json produced by `aromic plan --out`; skip re-plan.",
    ),
    force: bool = typer.Option(
        False, "--force", help="Skip concurrent-edit detection.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Walk the plan without writing; zero API mutations.",
    ),
    target: str | None = typer.Option(
        None, "--target", "-t", help="Limit plan to a single metadata.name.",
    ),
    prune: bool = typer.Option(
        False, "--prune", help="Include managed-orphan deletes in the plan.",
    ),
) -> None:
    """Apply pending changes to Automic.

    Exit codes:
      0 — success (everything applied or nothing to do)
      1 — fatal error (bad manifests, auth, etc.)
      2 — partial success (some items failed or were skipped)
    """
    target_path = Path(path)
    loaded = _load_and_validate(target_path)

    try:
        graph = build_graph(loaded)
    except CyclicDependencyError as exc:
        _error_console.print(f"[bold red]cycle:[/] {exc}")
        raise typer.Exit(code=1) from exc

    settings = AutomicSettings()
    try:
        with _build_api_client(settings) as client:
            api = AutomicAPI(client)

            if plan_file is not None:
                the_plan = _plan_from_file(Path(plan_file))
            else:
                the_plan = build_plan(loaded, api, prune=prune, target=target)

            if not auto_approve:
                # Prompt before any writes. `input()` raises EOFError if
                # stdin is empty (e.g. Typer CliRunner with no input) —
                # treat that as a "no".
                try:
                    approved = confirm_apply(the_plan, console=_console)
                except EOFError:
                    approved = False
                if not approved:
                    _console.print("[yellow]aborted[/]: apply cancelled by user.")
                    raise typer.Exit(code=1)

            markers = (
                {}
                if dry_run or plan_file is not None
                else capture_plan_markers(the_plan, client)
            )
            result = apply_plan(
                the_plan,
                client,
                graph,
                dry_run=dry_run,
                force=force,
                plan_markers=markers,
            )
    except AutomicError as exc:
        _error_console.print(f"[bold red]API error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    _console.print(
        f"[bold]Applied:[/] {len(result.successes)} ok, "
        f"{len(result.failures)} failed, "
        f"{len(result.skipped)} skipped",
    )
    for failure in result.failures:
        _error_console.print(
            f"[red]FAIL[/] {failure.kind}/{failure.name}: {failure.reason}",
        )
    raise typer.Exit(code=_exit_code_for_apply(result))


@app.command(name="export")
def export_cmd(
    output_dir: str = typer.Option(".", "--output-dir", "-o", help="Output directory for YAML."),
    filter_type: str | None = typer.Option(None, "--filter", "-f", help="Filter by object type."),
) -> None:
    """Export objects from Automic to local YAML files."""
    typer.echo(f"Exporting to {output_dir}...")


@app.command()
def destroy(
    path: str = typer.Argument(".", help="Path to YAML manifests."),
    confirm: bool = typer.Option(
        False, "--confirm", help="Required to actually destroy.",
    ),
    auto_approve: bool = typer.Option(
        False, "--auto-approve", help="Skip the yes/no confirmation prompt.",
    ),
    only_managed: bool = typer.Option(
        True,
        "--only-managed/--no-only-managed",
        help="Refuse objects that don't carry the managed-by marker.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Walk the plan without deleting; zero API calls.",
    ),
) -> None:
    """Delete managed objects from Automic in reverse-dependency order.

    Requires --confirm. Exit codes:
      0 — success
      1 — fatal error or prompt aborted
      2 — partial (some failures or refused objects)
    """
    if not confirm:
        typer.echo("Error: --confirm flag is required for destroy.", err=True)
        raise typer.Exit(code=1)

    target_path = Path(path)
    loaded = _load_and_validate(target_path)

    try:
        graph = build_graph(loaded)
    except CyclicDependencyError as exc:
        _error_console.print(f"[bold red]cycle:[/] {exc}")
        raise typer.Exit(code=1) from exc

    if not auto_approve:
        try:
            approved = confirm_destroy(loaded, console=_console)
        except EOFError:
            approved = False
        if not approved:
            _console.print("[yellow]aborted[/]: destroy cancelled by user.")
            raise typer.Exit(code=1)

    settings = AutomicSettings()
    try:
        with _build_api_client(settings) as client:
            result = destroy_objects(
                loaded,
                client,
                graph,
                only_managed=only_managed,
                dry_run=dry_run,
            )
    except AutomicError as exc:
        _error_console.print(f"[bold red]API error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    summarise_destroy(result, console=_console)
    for failure in result.failures:
        _error_console.print(
            f"[red]FAIL[/] {failure.kind}/{failure.name}: {failure.reason}",
        )
    for refusal in result.refused:
        _error_console.print(
            f"[yellow]REFUSED[/] {refusal.kind}/{refusal.name}: {refusal.reason}",
        )
    raise typer.Exit(code=0 if result.status == "success" else 2)


def main() -> None:
    app()
