"""Root CLI application."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from op_aromic import __version__
from op_aromic.cli.output import plan_to_json_dict, render_plan
from op_aromic.client.api import AutomicAPI
from op_aromic.client.errors import AutomicError
from op_aromic.client.http import AutomicClient
from op_aromic.config.settings import AutomicSettings
from op_aromic.engine.errors import EngineError
from op_aromic.engine.loader import load_manifests
from op_aromic.engine.planner import build_plan
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
    # Fail fast on invalid manifests — same pipeline as `validate`.
    target_path = Path(path)
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
            "fix manifests before planning.",
        )
        raise typer.Exit(code=1)

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


@app.command()
def apply(
    path: str = typer.Argument(".", help="Path to YAML manifests."),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Skip confirmation prompt."),
) -> None:
    """Apply changes to Automic. Prompts for confirmation."""
    typer.echo(f"Applying changes from {path}...")


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
    confirm: bool = typer.Option(False, "--confirm", help="Required to actually destroy."),
) -> None:
    """Remove managed objects from Automic. Requires --confirm."""
    if not confirm:
        typer.echo("Error: --confirm flag is required for destroy.", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Destroying objects from {path}...")


def main() -> None:
    app()
