"""Public Python API — library-mode entrypoints.

This module re-exports the engine, client, and model surfaces under a
single flat namespace so third-party callers can embed op-aromic
without touching CLI code::

    from op_aromic import api

    with api.open_client() as client:
        loaded = api.load("manifests/")
        issues = api.validate(loaded)
        if any(i.severity == "error" for i in issues):
            raise SystemExit(1)

        plan = api.plan(loaded, client=client)
        result = api.apply(plan, client=client, ledger_dir="./revisions")

Nothing in here duplicates engine logic — every function is a thin
wrapper that orchestrates the existing primitives the CLI already
uses, so library behavior tracks CLI behavior automatically.

Design rules:

* Prefer explicit keyword args over positional; this is the stable
  boundary and positional order will otherwise ossify by accident.
* Accept ``str | Path`` for any filesystem argument.
* When a caller passes a live ``AutomicClient`` *and* ``AutomicSettings``,
  the client wins — settings are only read to build a client when no
  client was supplied.
* No Typer/Rich imports. The library must be usable from code paths
  that have no terminal (e.g. pipelines, notebooks, web services).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from op_aromic.client.api import AutomicAPI
from op_aromic.client.http import AutomicClient
from op_aromic.config.settings import AutomicSettings
from op_aromic.engine.applier import (
    ApplyResult,
    PlanMarkers,
    ProgressCallback,
)
from op_aromic.engine.applier import (
    apply as _apply,
)
from op_aromic.engine.applier import (
    capture_plan_markers as _capture_plan_markers,
)
from op_aromic.engine.dependency import DependencyGraph
from op_aromic.engine.dependency import build_graph as _build_graph
from op_aromic.engine.destroyer import DestroyResult
from op_aromic.engine.destroyer import destroy as _destroy
from op_aromic.engine.differ import FieldChange, ObjectDiff
from op_aromic.engine.exporter import ExportResult, Layout
from op_aromic.engine.exporter import export as _export
from op_aromic.engine.ledger import (
    LedgerRow,
)
from op_aromic.engine.ledger import (
    append_row as ledger_append,
)
from op_aromic.engine.ledger import (
    ledger_root as _ledger_root,
)
from op_aromic.engine.ledger import (
    path_for as ledger_path_for,
)
from op_aromic.engine.ledger import (
    read_rows as _read_rows,
)
from op_aromic.engine.loader import LoadedManifest
from op_aromic.engine.loader import load_manifests as _load
from op_aromic.engine.planner import (
    Plan,
)
from op_aromic.engine.planner import (
    build_plan as _build_plan,
)
from op_aromic.engine.planner import (
    build_plan_parallel as _build_plan_parallel,
)
from op_aromic.engine.revision import (
    compute_revision,
    compute_revision_from_canonical,
    is_revision,
)
from op_aromic.engine.validator import (
    Issue,
    Severity,
    ValidationReport,
)
from op_aromic.engine.validator import (
    validate_manifests as _validate,
)
from op_aromic.models.base import Manifest, Metadata, Status

# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------


@contextmanager
def open_client(
    settings: AutomicSettings | None = None,
) -> Iterator[AutomicClient]:
    """Context manager yielding an authenticated ``AutomicClient``.

    When ``settings`` is ``None`` the usual pydantic-settings sources
    apply (env vars ``AUTOMIC_*``, ``.env``, ``aromic.yaml``). Pass an
    explicit ``AutomicSettings`` instance to bypass the filesystem
    lookup — useful for tests and multi-tenant runners.
    """
    resolved = settings or AutomicSettings()
    with AutomicClient(resolved) as client:
        yield client


def open_api(
    settings: AutomicSettings | None = None,
) -> AbstractApiContext:
    """Return a context manager yielding ``(client, api)``.

    Sugar over :func:`open_client` for callers that want the typed
    :class:`AutomicAPI` façade without repeating the wrapping boilerplate.
    """
    return AbstractApiContext(settings)


class AbstractApiContext:
    """Context manager yielding ``(AutomicClient, AutomicAPI)``."""

    def __init__(self, settings: AutomicSettings | None) -> None:
        self._settings = settings
        self._client: AutomicClient | None = None

    def __enter__(self) -> tuple[AutomicClient, AutomicAPI]:
        resolved = self._settings or AutomicSettings()
        self._client = AutomicClient(resolved).__enter__()
        return self._client, AutomicAPI(self._client)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._client is not None:
            self._client.__exit__(exc_type, exc, tb)  # type: ignore[arg-type]
            self._client = None


# ---------------------------------------------------------------------------
# Manifest I/O
# ---------------------------------------------------------------------------


def load(path: str | Path) -> list[LoadedManifest]:
    """Load manifests from a file or directory.

    Mirrors :func:`op_aromic.engine.loader.load_manifests`. Multi-doc
    YAML is fanned out into individual :class:`LoadedManifest` entries.
    """
    return _load(Path(path))


def validate(
    manifests: Sequence[LoadedManifest],
    *,
    strict: bool = False,
) -> ValidationReport:
    """Run cross-document validation.

    When ``strict=True`` and any error is found, raises
    :class:`ValidationFailed` so callers embedding this in a pipeline
    can fail fast without inspecting the report.
    """
    report = _validate(list(manifests))
    if strict and report.errors:
        raise ValidationFailed(report)
    return report


# ---------------------------------------------------------------------------
# Planning + applying
# ---------------------------------------------------------------------------


def plan(
    manifests: Sequence[LoadedManifest],
    *,
    client: AutomicClient | None = None,
    settings: AutomicSettings | None = None,
    prune: bool = False,
    target: str | None = None,
    parallel: int = 1,
) -> Plan:
    """Compute a plan against a live Automic instance.

    Exactly one of ``client`` or ``settings`` is typically passed; when
    neither is given, the default ``AutomicSettings`` (env-loaded) is
    used to build a transient client. Long-running callers should open
    a client explicitly via :func:`open_client` and reuse it across
    multiple operations.
    """
    with _resolve_client(client, settings) as (_client, api):
        if parallel > 1 and not target:
            return _build_plan_parallel(
                list(manifests), api, max_workers=parallel, prune=prune,
            )
        return _build_plan(list(manifests), api, prune=prune, target=target)


def apply(
    plan_: Plan,
    *,
    client: AutomicClient | None = None,
    settings: AutomicSettings | None = None,
    graph: DependencyGraph | None = None,
    manifests: Sequence[LoadedManifest] | None = None,
    ledger_dir: str | Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    capture_markers: bool = True,
    on_progress: ProgressCallback | None = None,
    auto_create_folders: bool = True,
) -> ApplyResult:
    """Execute ``plan_`` against Automic.

    The dependency graph is required for correct two-pass ordering;
    callers usually compute it from the same ``LoadedManifest`` list
    they planned from. If ``graph`` is ``None`` but ``manifests`` is
    supplied, the graph is built automatically. If both are ``None`` an
    empty graph is used — safe only for plans containing one-level-deep
    creates (no cross-object references).

    ``capture_markers`` (default ``True``) reads the concurrency marker
    for every update and delete in the plan just before writing, so a
    mid-flight edit aborts with a ``concurrent`` reason instead of
    silently overwriting. Set to ``False`` when driving apply from a
    plan produced seconds earlier by the same caller.
    """
    resolved_graph: DependencyGraph
    if graph is not None:
        resolved_graph = graph
    elif manifests is not None:
        resolved_graph = _build_graph(list(manifests))
    else:
        resolved_graph = _build_graph([])

    ledger_path = Path(ledger_dir) if ledger_dir is not None else None

    with _resolve_client(client, settings) as (active_client, _api):
        markers: PlanMarkers | None = None
        if capture_markers and not dry_run:
            markers = _capture_plan_markers(plan_, active_client)
        return _apply(
            plan_,
            active_client,
            resolved_graph,
            dry_run=dry_run,
            force=force,
            on_progress=on_progress,
            plan_markers=markers,
            auto_create_folders=auto_create_folders,
            ledger_dir=ledger_path,
        )


def plan_and_apply(
    manifests: Sequence[LoadedManifest],
    *,
    client: AutomicClient | None = None,
    settings: AutomicSettings | None = None,
    ledger_dir: str | Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    prune: bool = False,
    target: str | None = None,
    parallel: int = 1,
    on_progress: ProgressCallback | None = None,
) -> tuple[Plan, ApplyResult]:
    """Convenience helper: plan then apply using the same client.

    The client is held open for both phases so concurrency markers
    captured during ``apply`` are read against the same session that
    planned the diff.
    """
    with _resolve_client(client, settings) as (active_client, api):
        if parallel > 1 and not target:
            p = _build_plan_parallel(
                list(manifests), api, max_workers=parallel, prune=prune,
            )
        else:
            p = _build_plan(list(manifests), api, prune=prune, target=target)
        graph = _build_graph(list(manifests))
        markers = (
            _capture_plan_markers(p, active_client) if not dry_run else None
        )
        result = _apply(
            p,
            active_client,
            graph,
            dry_run=dry_run,
            force=force,
            on_progress=on_progress,
            plan_markers=markers,
            ledger_dir=Path(ledger_dir) if ledger_dir is not None else None,
        )
        return p, result


# ---------------------------------------------------------------------------
# Export + destroy
# ---------------------------------------------------------------------------


def export(
    output_dir: str | Path,
    *,
    client: AutomicClient | None = None,
    settings: AutomicSettings | None = None,
    kinds: list[str] | None = None,
    folders: list[str] | None = None,
    layout: Layout = "by-folder",
    overwrite: bool = False,
) -> ExportResult:
    """Pull objects from Automic and materialise them as YAML manifests."""
    with _resolve_client(client, settings) as (_client, api):
        return _export(
            api,
            Path(output_dir),
            kinds=kinds,
            folders=folders,
            layout=layout,
            overwrite=overwrite,
        )


def destroy(
    manifests: Sequence[LoadedManifest],
    *,
    client: AutomicClient | None = None,
    settings: AutomicSettings | None = None,
    only_managed: bool = True,
    dry_run: bool = False,
) -> DestroyResult:
    """Delete every loaded manifest in reverse-dependency order.

    Defaults to ``only_managed=True`` so an unintended ``destroy`` call
    cannot wipe operator-authored objects lacking the managed-by marker.
    """
    graph = _build_graph(list(manifests))
    with _resolve_client(client, settings) as (active_client, _api):
        return _destroy(
            list(manifests),
            active_client,
            graph,
            only_managed=only_managed,
            dry_run=dry_run,
        )


# ---------------------------------------------------------------------------
# History + rollback (no network; pure filesystem + git)
# ---------------------------------------------------------------------------


def history(
    kind: str,
    name: str,
    *,
    ledger_dir: str | Path | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return ledger rows for ``(kind, name)``, oldest-first.

    ``limit`` (when given) clips to the N most recent rows.
    """
    root = Path(ledger_dir) if ledger_dir is not None else None
    rows = _read_rows(kind, name, root=root)
    if limit is not None and limit >= 0:
        rows = rows[-limit:]
    return rows


@dataclass(frozen=True)
class RollbackPlan:
    """Result of :func:`rollback_plan` — the git command to execute."""

    kind: str
    name: str
    revision: str
    git_sha: str
    manifest_file: Path
    command: list[str] = field(default_factory=list)


def rollback_plan(
    kind: str,
    name: str,
    *,
    to: str,
    manifests_root: str | Path,
    ledger_dir: str | Path | None = None,
) -> RollbackPlan:
    """Resolve a rollback target without running git.

    Looks up the ledger row whose revision matches ``to`` (full
    ``sha256:<hex>`` or an 8+ char short form), finds the corresponding
    manifest file under ``manifests_root``, and returns the ``git
    checkout`` argv that would restore it.

    Raises :class:`RollbackUnresolved` when the ledger row can't be
    found, lacks a git SHA, or the manifest file isn't present.
    """
    root = Path(ledger_dir) if ledger_dir is not None else None
    rows = _read_rows(kind, name, root=root)
    if not rows:
        raise RollbackUnresolved(
            f"no ledger history for {kind}/{name} under {_ledger_root(root)}",
        )

    wanted = to.lower()
    hit: dict[str, Any] | None = None
    for row in reversed(rows):
        rev = (row.get("revision") or "").lower()
        if not rev:
            continue
        suffix = rev.split(":", 1)[-1]
        if rev == wanted or (len(wanted) >= 4 and suffix.startswith(wanted)):
            hit = row
            break
    if hit is None:
        raise RollbackUnresolved(f"no ledger row matches revision {to!r}")

    git_sha = hit.get("gitSha")
    if not git_sha:
        raise RollbackUnresolved(
            "matching ledger row has no gitSha — the original apply "
            "ran outside a git repo. Rollback needs git history.",
        )

    manifests_root_path = Path(manifests_root)
    loaded = _load(manifests_root_path)
    manifest_file: Path | None = None
    for lm in loaded:
        if lm.manifest.kind == kind and lm.manifest.metadata.name == name:
            manifest_file = lm.source_path
            break
    if manifest_file is None:
        raise RollbackUnresolved(
            f"{kind}/{name} not found under {manifests_root_path}; "
            "rollback needs a source file to restore.",
        )

    return RollbackPlan(
        kind=kind,
        name=name,
        revision=hit.get("revision") or "",
        git_sha=str(git_sha),
        manifest_file=manifest_file,
        command=["git", "checkout", str(git_sha), "--", str(manifest_file)],
    )


def rollback(
    kind: str,
    name: str,
    *,
    to: str,
    manifests_root: str | Path,
    ledger_dir: str | Path | None = None,
    dry_run: bool = False,
) -> RollbackPlan:
    """Plan and (unless ``dry_run``) execute a rollback via ``git checkout``.

    The follow-up ``apply`` is intentionally NOT run here — rollback is
    a two-step flow so an operator (or a separate library call) can
    review the restored diff before writing to Automic.
    """
    import subprocess

    plan_ = rollback_plan(
        kind,
        name,
        to=to,
        manifests_root=manifests_root,
        ledger_dir=ledger_dir,
    )
    if dry_run:
        return plan_
    try:
        subprocess.run(plan_.command, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RollbackFailed(
            f"git checkout failed for {kind}/{name}: {exc}",
        ) from exc
    return plan_


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ApiError(Exception):
    """Base class for library-level errors raised by :mod:`op_aromic.api`."""


class ValidationFailed(ApiError):  # noqa: N818 — public API; "Error" suffix adds no clarity
    """Raised from :func:`validate` when ``strict=True`` and errors exist."""

    def __init__(self, report: ValidationReport) -> None:
        super().__init__(
            f"{len(report.errors)} validation error(s): "
            + "; ".join(i.message for i in report.errors[:3])
            + ("; …" if len(report.errors) > 3 else ""),
        )
        self.report = report


class RollbackUnresolved(ApiError):  # noqa: N818 — public API; see ValidationFailed
    """Raised when :func:`rollback_plan` cannot produce a valid plan."""


class RollbackFailed(ApiError):  # noqa: N818 — public API; see ValidationFailed
    """Raised when ``git checkout`` exits non-zero during :func:`rollback`."""


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


@contextmanager
def _resolve_client(
    client: AutomicClient | None,
    settings: AutomicSettings | None,
) -> Iterator[tuple[AutomicClient, AutomicAPI]]:
    """Yield (client, api), opening a new client only if one wasn't passed.

    When the caller provides ``client`` we do NOT enter/exit it — its
    lifecycle is owned upstream. When we build our own, the inner
    ``AutomicClient`` context manager handles login + cleanup.
    """
    if client is not None:
        yield client, AutomicAPI(client)
        return
    resolved = settings or AutomicSettings()
    with AutomicClient(resolved) as new_client:
        yield new_client, AutomicAPI(new_client)


__all__ = [
    "ApiError",
    "ApplyResult",
    "AutomicAPI",
    "AutomicClient",
    "AutomicSettings",
    "DestroyResult",
    "ExportResult",
    "FieldChange",
    "Issue",
    "LedgerRow",
    "LoadedManifest",
    "Manifest",
    "Metadata",
    "ObjectDiff",
    "Plan",
    "RollbackFailed",
    "RollbackPlan",
    "RollbackUnresolved",
    "Severity",
    "Status",
    "ValidationFailed",
    "ValidationReport",
    "apply",
    "compute_revision",
    "compute_revision_from_canonical",
    "destroy",
    "export",
    "history",
    "is_revision",
    "ledger_append",
    "ledger_path_for",
    "load",
    "open_api",
    "open_client",
    "plan",
    "plan_and_apply",
    "rollback",
    "rollback_plan",
    "validate",
]
