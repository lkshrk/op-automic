"""Live-instance smoke tests for op-aromic.

These tests authenticate against a real Automic AE and exercise two
read-only paths: ``aromic auth check`` and ``aromic plan`` against a
throwaway manifest directory. They are skipped by default; opt in with
``AROMIC_INTEGRATION=1`` and supply the usual ``AUTOMIC_*`` env vars
(loaded by pydantic-settings inside the CLI). No mutations — both
commands are idempotent, so re-running against the same instance is
safe.

Running locally::

    AROMIC_INTEGRATION=1 \\
    AUTOMIC_URL=https://awa.example.com/ae/api/v1 \\
    AUTOMIC_CLIENT_ID=100 \\
    AUTOMIC_USER=alice \\
    AUTOMIC_DEPARTMENT=ADMIN \\
    AUTOMIC_PASSWORD=... \\
        uv run pytest tests/integration -m integration
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from op_aromic.cli.app import app

pytestmark = [pytest.mark.integration]

# Skip the entire module unless the user opts in. Checking here keeps
# CI green by default and avoids accidentally blasting test credentials
# at unrelated environments.
if os.environ.get("AROMIC_INTEGRATION") != "1":
    pytest.skip(
        "AROMIC_INTEGRATION=1 not set; skipping live integration suite",
        allow_module_level=True,
    )

# Required env vars are consumed internally by pydantic-settings inside
# the CLI — we only assert they are *present* here so a misconfigured
# run fails with a readable message instead of a deep traceback.
_REQUIRED_ENV = (
    "AUTOMIC_URL",
    "AUTOMIC_CLIENT_ID",
    "AUTOMIC_USER",
    "AUTOMIC_DEPARTMENT",
    "AUTOMIC_PASSWORD",
)

_missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
if _missing:
    pytest.skip(
        f"missing env vars for live run: {', '.join(_missing)}",
        allow_module_level=True,
    )


def test_auth_check_against_live_instance() -> None:
    """``aromic auth check`` must succeed with the configured credentials.

    Any non-zero exit indicates a credential, URL, or transport problem
    that other integration tests would also hit — so we stop here with
    a clear, single failure rather than cascading obscure errors.
    """
    runner = CliRunner()
    result = runner.invoke(app, ["--output", "json", "auth", "check"])
    assert result.exit_code == 0, (
        f"auth check failed with exit={result.exit_code}; "
        f"stdout={result.output!r}"
    )
    doc = json.loads(result.output.strip().splitlines()[-1])
    assert doc["command"] == "auth.check"
    assert doc["status"] == "ok"


def test_plan_against_live_instance(tmp_path: Path) -> None:
    """``aromic plan`` over an empty manifest dir is a no-op but proves the
    end-to-end path: settings → auth → API → planner → renderer.
    """
    runner = CliRunner()
    # Empty manifest dir: loader yields nothing, planner has nothing to
    # diff, exit 0. We still talk to Automic for auth.
    result = runner.invoke(
        app,
        [
            "--output", "json",
            "plan", str(tmp_path),
        ],
    )
    assert result.exit_code in (0, 2), (
        f"plan exited {result.exit_code}; stdout={result.output!r}"
    )
    doc = json.loads(result.output.strip().splitlines()[-1])
    assert doc["command"] == "plan"
    assert doc["status"] in ("ok", "changes")
