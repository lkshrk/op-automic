"""Root CLI application."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from op_aromic import __version__
from op_aromic.engine.errors import EngineError
from op_aromic.engine.loader import load_manifests
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


@app.command()
def plan(
    path: str = typer.Argument(".", help="Path to YAML manifests."),
    target: str | None = typer.Option(None, "--target", "-t", help="Target specific object."),
) -> None:
    """Show what would change without applying. Read-only."""
    typer.echo(f"Planning changes for {path}...")


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
