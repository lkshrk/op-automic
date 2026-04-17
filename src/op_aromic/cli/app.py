"""Root CLI application."""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Literal

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
)

from op_aromic import __version__
from op_aromic.cli.output import envelope, plan_to_json_dict, render_plan
from op_aromic.cli.prompts import (
    confirm_apply,
    confirm_destroy,
    failure_hint,
    preview_destroy,
    summarise_destroy,
)
from op_aromic.client.api import AutomicAPI
from op_aromic.client.errors import AutomicError
from op_aromic.client.http import AutomicClient
from op_aromic.config.settings import AutomicSettings
from op_aromic.engine.applier import (
    ApplyResult,
    ProgressCallback,
    capture_plan_markers,
)
from op_aromic.engine.applier import (
    apply as apply_plan,
)
from op_aromic.engine.dependency import CyclicDependencyError, build_graph
from op_aromic.engine.destroyer import destroy as destroy_objects
from op_aromic.engine.differ import FieldChange, ObjectDiff
from op_aromic.engine.errors import EngineError
from op_aromic.engine.exporter import Layout
from op_aromic.engine.exporter import export as export_manifests
from op_aromic.engine.ledger import ledger_root, path_for as ledger_path_for, read_rows
from op_aromic.engine.loader import load_manifests
from op_aromic.engine.planner import Plan, build_plan, build_plan_parallel
from op_aromic.engine.validator import Issue, Severity, validate_manifests
from op_aromic.observability.logging import configure_logging

app = typer.Typer(
    name="aromic",
    help="GitOps CLI for Broadcom Automic Workload Automation.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Module-level console so tests can capture via CliRunner's StringIO.
_console = Console()
_error_console = Console(stderr=True)


# Output-mode sentinel for the global callback. Commands read it off the
# Typer ctx.obj when emitting JSON or human output. Kept as a module-level
# dict-with-string so the callback → command handoff has a single shape.
_OutputMode = str  # "human" | "json"

_VALID_LOG_LEVELS: tuple[str, ...] = (
    "debug", "info", "warning", "warn", "error", "critical",
)
_VALID_LOG_FORMATS: tuple[str, ...] = ("text", "json")
_VALID_OUTPUTS: tuple[str, ...] = ("human", "json")


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"aromic {__version__}")
        raise typer.Exit()


@app.callback()
def _callback(
    ctx: typer.Context,
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
        help="Path to config file (sets AROMIC_CONFIG_FILE).",
    ),
    log_level: str = typer.Option(
        "info",
        "--log-level",
        help=f"Logging level. One of: {', '.join(_VALID_LOG_LEVELS)}.",
        case_sensitive=False,
    ),
    log_format: str = typer.Option(
        "text",
        "--log-format",
        help=f"Logging format. One of: {', '.join(_VALID_LOG_FORMATS)}.",
        case_sensitive=False,
    ),
    output: str = typer.Option(
        "human",
        "--output",
        help=f"Output format for command results. One of: {', '.join(_VALID_OUTPUTS)}.",
        case_sensitive=False,
    ),
    # ---- Automic connection overrides ------------------------------------
    automic_url: str | None = typer.Option(
        None, "--automic-url", help="Override Automic API base URL.", envvar=None,
    ),
    automic_client: int | None = typer.Option(
        None, "--automic-client", help="Override Automic client number.", envvar=None,
    ),
    automic_user: str | None = typer.Option(
        None, "--automic-user", help="Override Automic username.", envvar=None,
    ),
    automic_department: str | None = typer.Option(
        None, "--automic-department", help="Override Automic department.", envvar=None,
    ),
    automic_password: str | None = typer.Option(
        None,
        "--automic-password",
        help="Override Automic password (not echoed).",
        envvar=None,
        hide_input=True,
    ),
    # ---- Behaviour overrides --------------------------------------------
    auto_create_folders: bool | None = typer.Option(
        None,
        "--auto-create-folders/--no-auto-create-folders",
        help="Override auto_create_folders setting.",
    ),
    retry_base_delay_ms: int | None = typer.Option(
        None, "--retry-base-delay-ms", help="Override retry base delay (ms).",
    ),
    retry_max_backoff_s: float | None = typer.Option(
        None, "--retry-max-backoff-s", help="Override retry max backoff (seconds).",
    ),
    update_method: str | None = typer.Option(
        None, "--update-method", help="Override update method: POST_IMPORT or PUT.",
    ),
    auth_method: str | None = typer.Option(
        None, "--auth-method", help="Override auth method: basic or bearer.",
    ),
) -> None:
    """GitOps CLI for Broadcom Automic Workload Automation."""
    if log_level.lower() not in _VALID_LOG_LEVELS:
        raise typer.BadParameter(
            f"--log-level must be one of {', '.join(_VALID_LOG_LEVELS)}; got {log_level!r}",
        )
    if log_format.lower() not in _VALID_LOG_FORMATS:
        raise typer.BadParameter(
            f"--log-format must be one of {', '.join(_VALID_LOG_FORMATS)}; got {log_format!r}",
        )
    if output.lower() not in _VALID_OUTPUTS:
        raise typer.BadParameter(
            f"--output must be one of {', '.join(_VALID_OUTPUTS)}; got {output!r}",
        )

    # Store --config path in ctx.obj; _settings_from_ctx will apply it
    # temporarily when constructing AutomicSettings so the process env is
    # not permanently mutated across sub-commands or tests.
    if config is not None:
        ctx.ensure_object(dict)
        ctx.obj["config_file"] = config

    # ``verbose`` is legacy; it bumps effective log level when enabled, but
    # --log-level wins if explicitly set to a non-default.
    effective_level = "debug" if verbose and log_level == "info" else log_level
    fmt: Literal["text", "json"] = (
        "json" if log_format.lower() == "json" else "text"
    )
    configure_logging(level=effective_level, format=fmt)

    # Collect CLI-level setting overrides into a dict passed to sub-commands
    # via ctx.obj so they can construct AutomicSettings(**overrides).
    from typing import Any as _Any
    settings_overrides: dict[str, _Any] = {}
    if automic_url is not None:
        settings_overrides["url"] = automic_url
    if automic_client is not None:
        settings_overrides["client_id"] = automic_client
    if automic_user is not None:
        settings_overrides["user"] = automic_user
    if automic_department is not None:
        settings_overrides["department"] = automic_department
    if automic_password is not None:
        settings_overrides["password"] = automic_password
    if auto_create_folders is not None:
        settings_overrides["auto_create_folders"] = auto_create_folders
    if retry_base_delay_ms is not None:
        settings_overrides["retry_base_delay_ms"] = retry_base_delay_ms
    if retry_max_backoff_s is not None:
        settings_overrides["retry_max_backoff_s"] = retry_max_backoff_s
    if update_method is not None:
        settings_overrides["update_method"] = update_method
    if auth_method is not None:
        settings_overrides["auth_method"] = auth_method

    # Expose the output mode to sub-commands via Typer's context object.
    ctx.ensure_object(dict)
    ctx.obj["output"] = output.lower()
    ctx.obj["log_level"] = effective_level
    ctx.obj["log_format"] = fmt
    ctx.obj["settings_overrides"] = settings_overrides


def _output_mode(ctx: typer.Context) -> str:
    """Return 'human' or 'json' from the global callback context.

    Defaults to 'human' when the callback did not run (e.g. a command
    invoked directly from Python tests bypassing the root app).
    """
    if ctx.obj and isinstance(ctx.obj, dict):
        mode = ctx.obj.get("output", "human")
        if isinstance(mode, str):
            return mode
    return "human"


def _emit_json(doc: dict[str, object]) -> None:
    """Print the canonical JSON envelope on stdout as a single line.

    Stdout is the one machine-readable channel; logs go to stderr. Tests
    parse this by splitting on the last ``\\n``.
    """
    typer.echo(json.dumps(doc))


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
    ctx: typer.Context,
    path: str = typer.Argument(".", help="Path to YAML manifests."),
    strict: bool = typer.Option(False, "--strict", help="Fail on warnings."),
) -> None:
    """Validate YAML manifests against the schema. No network calls.

    Exit codes:
      0 — clean (no errors; warnings only allowed when --strict is off)
      1 — one or more errors (parse, schema, or cross-doc rules)
      2 — warnings present and --strict is set
    """
    output_mode = _output_mode(ctx)
    target = Path(path)
    try:
        loaded = load_manifests(target)
    except EngineError as exc:
        if output_mode == "json":
            _emit_json(
                envelope(
                    command="validate",
                    status="errors",
                    summary={"errors": 1, "warnings": 0, "manifests": 0},
                    details={"error": str(exc)},
                ),
            )
        else:
            _error_console.print(f"[bold red]ERROR[/] {exc}")
        raise typer.Exit(code=1) from exc

    report = validate_manifests(loaded)

    if output_mode == "json":
        if report.errors:
            status = "errors"
            code = 1
        elif report.warnings and strict:
            status = "warnings"
            code = 2
        else:
            status = "ok"
            code = 0
        _emit_json(
            envelope(
                command="validate",
                status=status,
                summary={
                    "errors": len(report.errors),
                    "warnings": len(report.warnings),
                    "manifests": len(loaded),
                },
                details={
                    "errors": [
                        {"path": str(i.source_path), "message": i.message}
                        for i in report.errors
                    ],
                    "warnings": [
                        {"path": str(i.source_path), "message": i.message}
                        for i in report.warnings
                    ],
                },
            ),
        )
        if code:
            raise typer.Exit(code=code)
        return

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


def _settings_from_ctx(ctx: typer.Context) -> AutomicSettings:
    """Build AutomicSettings, applying any CLI overrides from ctx.obj.

    If ``--config`` was supplied, AROMIC_CONFIG_FILE is temporarily set so
    _YamlSource picks it up, then restored after construction.
    """
    import os as _os
    from typing import Any

    overrides: dict[str, Any] = {}
    config_file: str | None = None
    if ctx.obj and isinstance(ctx.obj, dict):
        raw = ctx.obj.get("settings_overrides")
        if isinstance(raw, dict):
            overrides = raw
        raw_config = ctx.obj.get("config_file")
        if isinstance(raw_config, str):
            config_file = raw_config

    if config_file is not None:
        prev = _os.environ.get("AROMIC_CONFIG_FILE")
        _os.environ["AROMIC_CONFIG_FILE"] = config_file
        try:
            return AutomicSettings(**overrides)
        finally:
            if prev is None:
                _os.environ.pop("AROMIC_CONFIG_FILE", None)
            else:
                _os.environ["AROMIC_CONFIG_FILE"] = prev

    return AutomicSettings(**overrides)


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
    # Revision-mismatch warnings: the on-disk revision disagreed with the
    # recomputed hash for one or more manifests. Surface non-fatally so
    # normal workflows (hand-edit + re-apply) stay ergonomic.
    mismatches = [lm for lm in loaded if lm.revision_mismatch]
    for lm in mismatches:
        _error_console.print(
            f"[yellow]revision mismatch[/] "
            f"{lm.manifest.kind}/{lm.manifest.metadata.name} "
            f"(declared {lm.declared_revision}, "
            f"computed {lm.manifest.metadata.revision}) — will re-stamp on apply.",
        )
    return loaded


@app.command()
def plan(
    ctx: typer.Context,
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
    max_workers: int = typer.Option(
        8,
        "--max-workers",
        help="Parallel get_object workers. 1 forces sequential.",
        min=1,
    ),
) -> None:
    """Show what would change without applying. Read-only.

    Exit codes:
      0 — no changes pending
      1 — error (bad manifests, auth failure, transport failure)
      2 — one or more changes pending
    """
    output_mode = _output_mode(ctx)
    target_path = Path(path)
    loaded = _load_and_validate(target_path)

    settings = _settings_from_ctx(ctx)
    render_console = Console(no_color=no_color, force_terminal=not no_color)

    try:
        with _build_api_client(settings) as client:
            api = AutomicAPI(client)
            the_plan = build_plan_parallel(
                loaded,
                api,
                max_workers=max_workers,
                prune=prune,
                target=target,
            )
    except AutomicError as exc:
        if output_mode == "json":
            _emit_json(
                envelope(
                    command="plan",
                    status="errors",
                    summary={"error": str(exc)},
                    details={},
                ),
            )
        else:
            _error_console.print(f"[bold red]API error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    if out is not None:
        Path(out).write_text(json.dumps(plan_to_json_dict(the_plan), indent=2))

    if output_mode == "json":
        _emit_json(
            envelope(
                command="plan",
                status="changes" if the_plan.has_changes else "ok",
                summary={
                    "creates": len(the_plan.creates),
                    "updates": len(the_plan.updates),
                    "deletes": len(the_plan.deletes),
                    "noops": len(the_plan.noops),
                    "total_changes": the_plan.total_changes,
                },
                details=plan_to_json_dict(the_plan),
            ),
        )
        if the_plan.has_changes:
            raise typer.Exit(code=2)
        return

    render_plan(the_plan, render_console)
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


class _NullProgressCtx(AbstractContextManager["_NullProgressCtx"]):
    """No-op context manager used when progress output is suppressed.

    Enables the same ``with`` block in ``apply`` regardless of whether a
    live Rich Progress is driving the bar or not. Avoids a conditional
    wrapper at every call site.
    """

    def __exit__(self, *_args: object) -> None:
        return None


def _make_apply_progress(
    plan: Plan, *, output_mode: str,
) -> tuple[ProgressCallback, AbstractContextManager[object]]:
    """Build a (callback, context_manager) pair for apply progress.

    Progress rendering is skipped in JSON mode (would corrupt the single-
    document stdout contract) and when there's nothing to do. In both
    cases a noop callback + null context manager are returned so the
    apply code path stays uniform.

    Two tasks drive the bar:
      - "apply" (pass 1 + deletes) — every create/update/delete contributes
      - "wire"  (pass 2) — only kinds that needed ref-stripping contribute

    ``pass2`` has an upper bound of creates+updates; we size the task at
    that cap and let advances stop early. The bar's `completed < total` at
    the end is fine — pass 2 naturally skips refless kinds.
    """
    pass1_total = len(plan.creates) + len(plan.updates) + len(plan.deletes)
    if output_mode == "json" or pass1_total == 0:
        return lambda _event, _name: None, _NullProgressCtx()

    progress = Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("[dim]{task.fields[current]}"),
        TimeElapsedColumn(),
        transient=True,
    )
    progress.start()
    apply_task = progress.add_task(
        "apply", total=pass1_total, current="",
    )
    wire_total = len(plan.creates) + len(plan.updates)
    wire_task = progress.add_task(
        "wire", total=wire_total or 1, current="",
    )

    def _on_progress(event: str, name: str) -> None:
        if event in ("pass1_apply", "delete"):
            progress.update(apply_task, advance=1, current=name)
        elif event == "pass2_apply":
            progress.update(wire_task, advance=1, current=name)

    class _ProgressCtx(AbstractContextManager["_ProgressCtx"]):
        def __exit__(self, *_args: object) -> None:
            progress.stop()

    return _on_progress, _ProgressCtx()


@app.command()
def apply(
    ctx: typer.Context,
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
    output_mode = _output_mode(ctx)
    target_path = Path(path)
    loaded = _load_and_validate(target_path)

    try:
        graph = build_graph(loaded)
    except CyclicDependencyError as exc:
        if output_mode == "json":
            _emit_json(
                envelope(
                    command="apply",
                    status="errors",
                    summary={"error": f"cycle: {exc}"},
                ),
            )
        else:
            _error_console.print(f"[bold red]cycle:[/] {exc}")
        raise typer.Exit(code=1) from exc

    # Refuse interactive confirmation in JSON mode before any network
    # round-trip — an interactive prompt would corrupt the single-document
    # stdout contract and callers also want to fail fast, not after a plan.
    if not auto_approve and output_mode == "json":
        _emit_json(
            envelope(
                command="apply",
                status="aborted",
                summary={"reason": "--auto-approve required in JSON mode"},
            ),
        )
        raise typer.Exit(code=1)

    settings = _settings_from_ctx(ctx)
    try:
        with _build_api_client(settings) as client:
            api = AutomicAPI(client)

            if plan_file is not None:
                the_plan = _plan_from_file(Path(plan_file))
            else:
                the_plan = build_plan(loaded, api, prune=prune, target=target)

            if not auto_approve:
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
            on_progress, progress_ctx = _make_apply_progress(
                the_plan, output_mode=output_mode,
            )
            with progress_ctx:
                result = apply_plan(
                    the_plan,
                    client,
                    graph,
                    dry_run=dry_run,
                    force=force,
                    plan_markers=markers,
                    on_progress=on_progress,
                )
    except AutomicError as exc:
        if output_mode == "json":
            _emit_json(
                envelope(
                    command="apply",
                    status="errors",
                    summary={"error": str(exc)},
                ),
            )
        else:
            _error_console.print(f"[bold red]API error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    if output_mode == "json":
        _emit_json(
            envelope(
                command="apply",
                status="ok" if result.status == "success" else "partial",
                summary={
                    "successes": len(result.successes),
                    "failures": len(result.failures),
                    "skipped": len(result.skipped),
                },
                details={
                    "failures": [
                        {
                            "kind": f.kind,
                            "name": f.name,
                            "reason": f.reason,
                        }
                        for f in result.failures
                    ],
                    "skipped": [
                        {"kind": s.kind, "name": s.name, "action": s.action}
                        for s in result.skipped
                    ],
                },
            ),
        )
        raise typer.Exit(code=_exit_code_for_apply(result))

    _console.print(
        f"[bold]Applied:[/] {len(result.successes)} ok, "
        f"{len(result.failures)} failed, "
        f"{len(result.skipped)} skipped",
    )
    for failure in result.failures:
        _error_console.print(
            f"[red]FAIL[/] {failure.kind}/{failure.name}: {failure.reason}",
        )
        hint = failure_hint(failure.reason)
        if hint is not None:
            _error_console.print(f"  [dim]hint:[/] {hint}")
    raise typer.Exit(code=_exit_code_for_apply(result))


_VALID_LAYOUTS: tuple[str, ...] = ("by-folder", "by-kind", "flat")

# Module-level singletons to dodge B008: Typer inspects the default value
# identity to detect option/argument shape, so each must exist as a stable
# module-level name rather than an inline call in the function signature.
_OPT_OUTPUT_DIR = typer.Option(
    "./out", "--output-dir", "-o", help="Output directory for YAML.",
)
_OPT_FILTER = typer.Option(
    None,
    "--filter",
    "-f",
    help="Restrict to one or more manifest kinds (repeatable).",
)
_OPT_FOLDER = typer.Option(
    None,
    "--folder",
    help="Restrict to one or more Automic folder paths (repeatable).",
)
_OPT_LAYOUT = typer.Option(
    "by-folder",
    "--layout",
    help=f"File layout. One of: {', '.join(_VALID_LAYOUTS)}.",
)
_OPT_OVERWRITE = typer.Option(
    False, "--overwrite", help="Replace existing non-empty files.",
)
_OPT_DRY_RUN = typer.Option(
    False, "--dry-run", help="List what would be written; no HTTP, no files.",
)


@app.command(name="export")
def export_cmd(
    ctx: typer.Context,
    output_dir: str = _OPT_OUTPUT_DIR,
    filters: list[str] | None = _OPT_FILTER,
    folders: list[str] | None = _OPT_FOLDER,
    layout: str = _OPT_LAYOUT,
    overwrite: bool = _OPT_OVERWRITE,
    dry_run: bool = _OPT_DRY_RUN,
) -> None:
    """Export Automic objects to local YAML manifests.

    Exit codes:
      0 — success (including --dry-run)
      1 — error (bad flag, auth failure, transport failure)
    """
    output_mode = _output_mode(ctx)
    if layout not in _VALID_LAYOUTS:
        if output_mode == "json":
            _emit_json(
                envelope(
                    command="export",
                    status="errors",
                    summary={"error": f"invalid layout: {layout!r}"},
                ),
            )
        else:
            _error_console.print(
                f"[bold red]ERROR[/] --layout must be one of "
                f"{', '.join(_VALID_LAYOUTS)}; got {layout!r}",
            )
        raise typer.Exit(code=1)

    out_path = Path(output_dir)

    if dry_run:
        if output_mode == "json":
            _emit_json(
                envelope(
                    command="export",
                    status="ok",
                    summary={
                        "dry_run": True,
                        "output_dir": str(out_path),
                        "layout": layout,
                        "kinds": filters or [],
                        "folders": folders or [],
                        "overwrite": overwrite,
                    },
                ),
            )
            return
        # Zero HTTP calls: just echo the planned configuration.
        _console.print(
            f"[bold]dry-run:[/] would export to {out_path} "
            f"(layout={layout}, kinds={filters or 'all'}, "
            f"folders={folders or 'all'}, overwrite={overwrite})",
        )
        return

    settings = _settings_from_ctx(ctx)
    try:
        with _build_api_client(settings) as client:
            api = AutomicAPI(client)
            result = export_manifests(
                api,
                out_path,
                kinds=list(filters) if filters else None,
                folders=list(folders) if folders else None,
                layout=layout,  # type: ignore[arg-type]
                overwrite=overwrite,
            )
    except AutomicError as exc:
        if output_mode == "json":
            _emit_json(
                envelope(
                    command="export",
                    status="errors",
                    summary={"error": str(exc)},
                ),
            )
        else:
            _error_console.print(f"[bold red]API error:[/] {exc}")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        if output_mode == "json":
            _emit_json(
                envelope(
                    command="export",
                    status="errors",
                    summary={"error": str(exc)},
                ),
            )
        else:
            _error_console.print(f"[bold red]ERROR[/] {exc}")
        raise typer.Exit(code=1) from exc

    if output_mode == "json":
        _emit_json(
            envelope(
                command="export",
                status="ok",
                summary={
                    "objects_exported": result.objects_exported,
                    "files_written": len(result.files_written),
                    "skipped": len(result.skipped),
                    "output_dir": str(out_path),
                },
                details={
                    "files_written": [str(p) for p in result.files_written],
                    "skipped": [
                        {"kind": k, "name": n, "reason": r}
                        for k, n, r in result.skipped
                    ],
                },
            ),
        )
        return

    _console.print(
        f"[bold green]Exported[/] {result.objects_exported} object(s) to "
        f"{len(result.files_written)} file(s) under {out_path}",
    )
    for kind, name, reason in result.skipped:
        _error_console.print(
            f"[yellow]SKIP[/] {kind}/{name}: {reason}",
        )


# Re-export so type-checkers see it as used when imported elsewhere.
_ = Layout


@app.command()
def destroy(
    ctx: typer.Context,
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
    output_mode = _output_mode(ctx)
    if not confirm:
        if output_mode == "json":
            _emit_json(
                envelope(
                    command="destroy",
                    status="errors",
                    summary={"error": "--confirm flag is required for destroy"},
                ),
            )
        else:
            typer.echo("Error: --confirm flag is required for destroy.", err=True)
        raise typer.Exit(code=1)

    target_path = Path(path)
    loaded = _load_and_validate(target_path)

    try:
        graph = build_graph(loaded)
    except CyclicDependencyError as exc:
        if output_mode == "json":
            _emit_json(
                envelope(
                    command="destroy",
                    status="errors",
                    summary={"error": f"cycle: {exc}"},
                ),
            )
        else:
            _error_console.print(f"[bold red]cycle:[/] {exc}")
        raise typer.Exit(code=1) from exc

    if not auto_approve:
        # JSON mode refuses interactive prompts to preserve the single-
        # document stdout contract; callers must pass --auto-approve.
        if output_mode == "json":
            _emit_json(
                envelope(
                    command="destroy",
                    status="aborted",
                    summary={"reason": "--auto-approve required in JSON mode"},
                ),
            )
            raise typer.Exit(code=1)
        try:
            approved = confirm_destroy(loaded, console=_console)
        except EOFError:
            approved = False
        if not approved:
            _console.print("[yellow]aborted[/]: destroy cancelled by user.")
            raise typer.Exit(code=1)

    settings = _settings_from_ctx(ctx)
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
        if output_mode == "json":
            _emit_json(
                envelope(
                    command="destroy",
                    status="errors",
                    summary={"error": str(exc)},
                ),
            )
        else:
            _error_console.print(f"[bold red]API error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    if output_mode == "json":
        _emit_json(
            envelope(
                command="destroy",
                status="ok" if result.status == "success" else "partial",
                summary={
                    "successes": len(result.successes),
                    "failures": len(result.failures),
                    "refused": len(result.refused),
                    "not_supported": len(result.not_supported),
                },
                details={
                    "failures": [
                        {"kind": f.kind, "name": f.name, "reason": f.reason}
                        for f in result.failures
                    ],
                    "refused": [
                        {"kind": r.kind, "name": r.name, "reason": r.reason}
                        for r in result.refused
                    ],
                    "not_supported": [
                        {"kind": ns.kind, "name": ns.name, "reason": ns.reason}
                        for ns in result.not_supported
                    ],
                },
            ),
        )
        raise typer.Exit(code=0 if result.status == "success" else 2)

    if result.dry_run:
        preview_destroy(result, console=_console)
    summarise_destroy(result, console=_console)
    for failure in result.failures:
        _error_console.print(
            f"[red]FAIL[/] {failure.kind}/{failure.name}: {failure.reason}",
        )
        hint = failure_hint(failure.reason)
        if hint is not None:
            _error_console.print(f"  [dim]hint:[/] {hint}")
    for refusal in result.refused:
        _error_console.print(
            f"[yellow]REFUSED[/] {refusal.kind}/{refusal.name}: {refusal.reason}",
        )
    for ns in result.not_supported:
        _error_console.print(
            f"[yellow]NOT SUPPORTED[/] {ns.kind}/{ns.name}: {ns.reason}",
        )
    raise typer.Exit(code=0 if result.status == "success" else 2)


auth_app = typer.Typer(
    name="auth",
    help="Debug helpers for Automic authentication.",
    no_args_is_help=True,
)
app.add_typer(auth_app, name="auth")


@auth_app.command("check")
def auth_check(
    ctx: typer.Context,
) -> None:
    """Verify Basic credentials authenticate against the configured Automic URL.

    Issues a single cheap read call (GET /objects/AROMIC_AUTH_PROBE) which
    Automic will return 404 for a valid credential (the object doesn't exist)
    or 401 for an invalid one. Both cases are handled: 404 → success,
    401 → failure. No mutation, no object listing, no secrets echoed.

    Exit codes:
      0 — authentication succeeded (server returned 404 or 200 on probe)
      1 — authentication or transport failure (401, network error, etc.)
    """
    import contextlib as _contextlib

    from op_aromic.client.errors import AuthError as _AuthError
    from op_aromic.client.errors import NotFoundError as _NotFoundError

    output_mode = _output_mode(ctx)
    settings = _settings_from_ctx(ctx)
    _probe_name = "AROMIC_AUTH_PROBE_9X8Y7Z"
    try:
        with _build_api_client(settings) as client, _contextlib.suppress(_NotFoundError):
            # 404 means auth succeeded (object absent); 401 raises AuthError.
            client.get_object(_probe_name)
    except (_AuthError, AutomicError) as exc:
        if output_mode == "json":
            _emit_json(
                envelope(
                    command="auth.check",
                    status="errors",
                    summary={"error": str(exc), "url": settings.url},
                ),
            )
        else:
            _error_console.print(f"[bold red]auth failed:[/] {exc}")
        raise typer.Exit(code=1) from exc

    if output_mode == "json":
        _emit_json(
            envelope(
                command="auth.check",
                status="ok",
                summary={
                    "url": settings.url,
                    "client_id": settings.client_id,
                    "user": settings.user,
                },
            ),
        )
        return

    _console.print(
        f"[bold green]OK[/] authenticated against {settings.url} "
        f"(client={settings.client_id}, user={settings.user})",
    )


@app.command()
def history(
    ctx: typer.Context,
    target: str = typer.Argument(
        ...,
        help="Object identity as 'Kind/Name' (e.g. 'Workflow/ETL.DAILY').",
    ),
    ledger_dir: str | None = typer.Option(
        None,
        "--ledger-dir",
        help=(
            "Ledger root directory. Defaults to $AROMIC_LEDGER_DIR, "
            "else ./revisions."
        ),
    ),
    limit: int = typer.Option(
        20, "--limit", "-n", min=1, help="Show only the most recent N rows.",
    ),
) -> None:
    """Print the revision history ledger for one object."""
    output_mode = _output_mode(ctx)
    if "/" not in target:
        _error_console.print(
            "[bold red]ERROR[/] target must be 'Kind/Name'",
        )
        raise typer.Exit(code=1)
    kind, name = target.split("/", 1)
    root = Path(ledger_dir) if ledger_dir else None
    rows = read_rows(kind, name, root=root)
    recent = rows[-limit:]

    if output_mode == "json":
        _emit_json(
            envelope(
                command="history",
                status="ok",
                summary={"rows": len(recent), "total": len(rows)},
                details={"rows": recent},
            ),
        )
        return

    if not rows:
        _console.print(
            f"[dim]no ledger entries for {kind}/{name} under "
            f"{ledger_path_for(kind, name, root=root)}[/dim]",
        )
        return

    _console.print(
        f"[bold]{kind}/{name}[/]  ({len(recent)} of {len(rows)} shown)",
    )
    for row in recent:
        rev = row.get("revision") or "-"
        short = rev.split(":", 1)[-1][:8] if rev and rev != "-" else "-"
        ver_before = row.get("automicVersionBefore")
        ver_after = row.get("automicVersionAfter")
        ver_frag = ""
        if ver_before is not None or ver_after is not None:
            ver_frag = f"  v:{ver_before or '-'}→{ver_after or '-'}"
        _console.print(
            f"  {row.get('ts','?')}  {row.get('action','?'):<6}  "
            f"rev:{short}  git:{row.get('gitSha') or '-'}{ver_frag}  "
            f"by:{row.get('by','unknown')}",
        )


@app.command()
def rollback(
    ctx: typer.Context,
    target: str = typer.Argument(
        ...,
        help="Object identity as 'Kind/Name' (e.g. 'Workflow/ETL.DAILY').",
    ),
    to: str = typer.Option(
        ...,
        "--to",
        help=(
            "Target revision. Accepts the full 'sha256:<hex>' or the "
            "8-char short form as printed by 'history'."
        ),
    ),
    manifests_path: str = typer.Option(
        ".",
        "--manifests",
        "-m",
        help="Manifests root — used to locate the file to restore.",
    ),
    ledger_dir: str | None = typer.Option(
        None, "--ledger-dir", help="Ledger root (see 'history').",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the git command without executing.",
    ),
) -> None:
    """Restore an object's manifest file to the git commit that produced ``--to``.

    Strategy: look up the ledger row whose revision matches ``--to``, grab
    its ``gitSha``, then ``git checkout <sha> -- <manifest file>``. The
    caller re-runs ``aromic apply`` afterwards to push the restored
    content to Automic — keeping rollback as an explicit two-step flow so
    an operator can review the diff before writing.
    """
    import subprocess

    output_mode = _output_mode(ctx)
    if "/" not in target:
        _error_console.print("[bold red]ERROR[/] target must be 'Kind/Name'")
        raise typer.Exit(code=1)
    kind, name = target.split("/", 1)

    root = Path(ledger_dir) if ledger_dir else None
    rows = read_rows(kind, name, root=root)
    if not rows:
        _error_console.print(
            f"[bold red]ERROR[/] no ledger history for {kind}/{name} under "
            f"{ledger_root(root)}",
        )
        raise typer.Exit(code=1)

    # Short form matches any row whose revision's hex-suffix starts with the query.
    wanted = to.lower()
    hit = None
    for row in reversed(rows):  # newest first: prefer the most recent match
        rev = row.get("revision") or ""
        suffix = rev.split(":", 1)[-1]
        if rev == wanted or (len(wanted) >= 4 and suffix.startswith(wanted)):
            hit = row
            break
    if hit is None:
        _error_console.print(
            f"[bold red]ERROR[/] no ledger row matches revision {to!r}",
        )
        raise typer.Exit(code=1)

    git_sha = hit.get("gitSha")
    if not git_sha:
        _error_console.print(
            f"[bold red]ERROR[/] matching ledger row has no gitSha — "
            "the original apply ran outside a git repo. Rollback needs git history.",
        )
        raise typer.Exit(code=1)

    # Locate the manifest file by scanning manifests_path. Not pinned by
    # the ledger because file paths can change across renames.
    manifests_root = Path(manifests_path)
    manifest_file: Path | None = None
    try:
        loaded = load_manifests(manifests_root)
    except EngineError as exc:
        _error_console.print(f"[bold red]ERROR[/] loading manifests: {exc}")
        raise typer.Exit(code=1) from exc
    for lm in loaded:
        if lm.manifest.kind == kind and lm.manifest.metadata.name == name:
            manifest_file = lm.source_path
            break
    if manifest_file is None:
        _error_console.print(
            f"[bold red]ERROR[/] {kind}/{name} not found under {manifests_root}; "
            "rollback needs a source file to restore.",
        )
        raise typer.Exit(code=1)

    cmd = ["git", "checkout", git_sha, "--", str(manifest_file)]

    if output_mode == "json":
        _emit_json(
            envelope(
                command="rollback",
                status="ok" if not dry_run else "dry-run",
                summary={
                    "kind": kind,
                    "name": name,
                    "revision": hit.get("revision"),
                    "gitSha": git_sha,
                    "file": str(manifest_file),
                    "dry_run": dry_run,
                },
                details={"command": cmd},
            ),
        )
        if dry_run:
            return

    if dry_run:
        _console.print(
            f"[dim]would run:[/] {' '.join(cmd)}",
        )
        _console.print(
            "[dim]then run `aromic apply` to push the restored content.[/]",
        )
        return

    try:
        subprocess.run(cmd, check=True)  # noqa: S603 — argv built from vetted parts
    except (OSError, subprocess.SubprocessError) as exc:
        _error_console.print(f"[bold red]ERROR[/] git checkout failed: {exc}")
        raise typer.Exit(code=1) from exc

    _console.print(
        f"[bold green]OK[/] restored {manifest_file} to revision "
        f"{hit.get('revision')} (git {git_sha}). Run `aromic apply` to push.",
    )


def main() -> None:
    app()
