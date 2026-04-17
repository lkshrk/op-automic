"""Append-only revision ledger.

One JSONL file per ``(Kind, Name)`` identity under a configurable root
(default ``./revisions``). Every successful apply appends a row; the
file is git-tracked so the log is durable, auditable, and does not
require an external store.

Each row is a flat JSON object. Keys are camelCase to match the YAML
envelope convention. The file format is newline-delimited JSON so
``tail -f`` and ``jq`` work without extra tooling.

Row schema::

    {
      "ts":                 "2026-04-17T12:00:00Z",      # UTC, ISO 8601
      "action":             "create" | "update" | "delete",
      "revision":           "sha256:<hex>" | null,       # null on delete
      "gitSha":             "<short sha>" | null,
      "automicVersionBefore": int | null,
      "automicVersionAfter":  int | null,
      "by":                 "<user or \"unknown\">"
    }

Writing is intentionally best-effort: a ledger failure must not poison
an otherwise-successful apply, because the apply already mutated
Automic and the ledger is advisory metadata. Failures are logged but
never raised out of :func:`append_row`.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

_LOG = logging.getLogger(__name__)

Action = Literal["create", "update", "delete"]

_ENV_DIR = "AROMIC_LEDGER_DIR"
_DEFAULT_DIR = Path("revisions")


@dataclass(frozen=True)
class LedgerRow:
    """One append-only entry in a manifest's revision history."""

    ts: str
    action: Action
    revision: str | None
    gitSha: str | None  # noqa: N815 — field name is the on-disk column
    automicVersionBefore: int | None  # noqa: N815
    automicVersionAfter: int | None  # noqa: N815
    by: str


def _now() -> str:
    # ``timespec="seconds"`` keeps the file human-greppable; the runtime
    # ledger is not a profiling tool.
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_short_sha() -> str | None:
    """Return the current git short SHA, or None when unavailable.

    Silent on error: running outside a git repo is a legitimate case
    (e.g. inside CI images that ``cp``-stage the manifests), so we must
    not explode.
    """
    try:
        result = subprocess.run(  # noqa: S603 — trusted argv
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 — absolute path not guaranteed
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = result.stdout.strip()
    return sha or None


def _current_user() -> str:
    return (
        os.environ.get("AROMIC_USER")
        or os.environ.get("USER")
        or os.environ.get("USERNAME")
        or "unknown"
    )


def ledger_root(override: Path | None = None) -> Path:
    """Resolve the ledger root directory.

    Priority: explicit override > ``AROMIC_LEDGER_DIR`` env > default
    ``./revisions`` relative to CWD.
    """
    if override is not None:
        return override
    env = os.environ.get(_ENV_DIR)
    if env:
        return Path(env)
    return _DEFAULT_DIR


def path_for(kind: str, name: str, *, root: Path | None = None) -> Path:
    """Return the ledger file path for a given identity.

    Names are written verbatim; Automic forbids path separators in
    object names so escaping is not required.
    """
    return ledger_root(root) / kind / f"{name}.jsonl"


def append_row(
    *,
    kind: str,
    name: str,
    action: Action,
    revision: str | None,
    automic_version_before: int | None = None,
    automic_version_after: int | None = None,
    root: Path | None = None,
) -> LedgerRow | None:
    """Append one row to the ledger. Returns the row written, or None on failure.

    Best-effort: IO/git errors are logged and swallowed so a broken
    ledger cannot fail an apply that already mutated Automic state.
    """
    row = LedgerRow(
        ts=_now(),
        action=action,
        revision=revision,
        gitSha=_git_short_sha(),
        automicVersionBefore=automic_version_before,
        automicVersionAfter=automic_version_after,
        by=_current_user(),
    )
    target = path_for(kind, name, root=root)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(asdict(row), ensure_ascii=False))
            fp.write("\n")
    except OSError as exc:
        _LOG.warning(
            "ledger append failed for %s/%s: %s", kind, name, exc,
        )
        return None
    return row


def read_rows(
    kind: str, name: str, *, root: Path | None = None,
) -> list[dict[str, Any]]:
    """Read and parse every row for ``(kind, name)``. Missing file → []."""
    target = path_for(kind, name, root=root)
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    with target.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # Corrupt line — keep going so a single bad row does not
                # hide the rest of the history.
                _LOG.warning("skipping corrupt ledger line in %s", target)
                continue
    return rows


__all__ = [
    "Action",
    "LedgerRow",
    "append_row",
    "ledger_root",
    "path_for",
    "read_rows",
]
