"""Root CLI application."""


import typer

from op_aromic import __version__

app = typer.Typer(
    name="aromic",
    help="GitOps CLI for Broadcom Automic Workload Automation.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"aromic {__version__}")
        raise typer.Exit()


@app.callback()
def _callback(
    version: bool | None = typer.Option(
        None, "--version", "-V", callback=version_callback, is_eager=True,
        help="Show version and exit.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose output."),
    config: str | None = typer.Option(
        None, "--config", "-c", help="Path to config file.",
    ),
) -> None:
    """GitOps CLI for Broadcom Automic Workload Automation."""


@app.command()
def validate(
    path: str = typer.Argument(".", help="Path to YAML manifests."),
    strict: bool = typer.Option(False, "--strict", help="Fail on warnings."),
) -> None:
    """Validate YAML manifests against the schema. No network calls."""
    typer.echo(f"Validating {path}...")


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
