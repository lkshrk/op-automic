"""Tests for ``--output json`` on each command.

The JSON envelope is the CI contract: anything that greps/jq's these
lines should find a single well-formed document on stdout with the
canonical ``{command, status, summary, details}`` shape. Structured
logs still go to stderr; this test suite asserts only stdout.

Each command gets its own small schema check so a drift in one command
does not mask a regression in another.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

from op_aromic.cli.app import app
from op_aromic.client.http import _AUTH_PATH

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "manifests"
AUTOMIC_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "automic"

_ENVELOPE_KEYS = {"command", "status", "summary", "details"}


def _assert_envelope(doc: Any, command: str) -> dict[str, Any]:
    """Shared schema check: every stdout doc must have this shape."""
    assert isinstance(doc, dict), f"expected dict, got {type(doc)!r}: {doc!r}"
    assert set(doc.keys()) == _ENVELOPE_KEYS, doc
    assert doc["command"] == command
    assert isinstance(doc["status"], str) and doc["status"]
    assert isinstance(doc["summary"], dict)
    assert isinstance(doc["details"], (dict, list))
    return doc


def _parse_last_json_line(stdout: str) -> dict[str, Any]:
    """Stdout can have one or more lines; the envelope is always the last.

    structlog defaults to stderr so it shouldn't contaminate stdout, but
    if a test-local console ends up on stdout we still want a stable
    parse strategy.
    """
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    assert lines, f"no stdout produced: {stdout!r}"
    parsed = json.loads(lines[-1])
    assert isinstance(parsed, dict)
    return parsed


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMIC_URL", "http://json.test/ae/api/v1")
    monkeypatch.setenv("AUTOMIC_CLIENT_ID", "100")
    monkeypatch.setenv("AUTOMIC_USER", "U")
    monkeypatch.setenv("AUTOMIC_DEPARTMENT", "D")
    monkeypatch.setenv("AUTOMIC_PASSWORD", "P")
    monkeypatch.setenv("AUTOMIC_VERIFY_SSL", "false")
    monkeypatch.setenv("AUTOMIC_MAX_RETRIES", "0")


def _mock_auth(mock: respx.MockRouter) -> None:
    mock.post(f"http://json.test/ae/api/v1{_AUTH_PATH}").mock(
        return_value=httpx.Response(200, json={"token": "t", "expires_in": 3600}),
    )


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_json_ok() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app, ["--output", "json", "validate", str(FIXTURES / "valid")],
    )
    assert result.exit_code == 0, result.output
    doc = _parse_last_json_line(result.output)
    _assert_envelope(doc, command="validate")
    assert doc["status"] == "ok"
    summary = doc["summary"]
    assert summary["errors"] == 0
    assert isinstance(summary["manifests"], int)
    assert summary["manifests"] > 0


def test_validate_json_errors() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--output", "json",
            "validate", str(FIXTURES / "invalid" / "duplicate.yaml"),
        ],
    )
    assert result.exit_code == 1
    doc = _parse_last_json_line(result.output)
    _assert_envelope(doc, command="validate")
    assert doc["status"] == "errors"
    assert doc["summary"]["errors"] >= 1


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def test_plan_json_has_changes(env: None) -> None:
    runner = CliRunner()
    base = "http://json.test/ae/api/v1/100"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock)
        # Every GET returns 404 → every manifest is a create.
        for name in (
            "WORK.DAYS", "ETL.CONFIG", "ETL.EXTRACT",
            "ETL.LOAD", "ETL.DAILY", "ETL.NIGHTLY",
        ):
            mock.get(f"{base}/objects/{name}").mock(
                return_value=httpx.Response(404),
            )

        result = runner.invoke(
            app, ["--output", "json", "plan", str(FIXTURES / "valid")],
        )

    assert result.exit_code == 2, result.output
    doc = _parse_last_json_line(result.output)
    _assert_envelope(doc, command="plan")
    assert doc["status"] == "changes"
    summary = doc["summary"]
    assert summary["creates"] >= 1
    assert summary["total_changes"] == (
        summary["creates"] + summary["updates"] + summary["deletes"]
    )
    # details is the full plan dict
    assert set(doc["details"].keys()) == {"creates", "updates", "deletes", "noops"}


def test_plan_json_error_on_api_500(env: None) -> None:
    runner = CliRunner()
    base = "http://json.test/ae/api/v1/100"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock)
        mock.get(f"{base}/objects/WORK.DAYS").mock(
            return_value=httpx.Response(500, text="boom"),
        )
        result = runner.invoke(
            app,
            [
                "--output", "json",
                "plan", str(FIXTURES / "valid"),
                "--max-workers", "1",
            ],
        )

    assert result.exit_code == 1, result.output
    doc = _parse_last_json_line(result.output)
    _assert_envelope(doc, command="plan")
    assert doc["status"] == "errors"
    assert "error" in doc["summary"]


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def _write_simple_job(tmp_path: Path, name: str = "J") -> Path:
    (tmp_path / f"{name.lower()}.yaml").write_text(
        "apiVersion: aromic.io/v1\n"
        "kind: Job\n"
        "metadata:\n"
        f"  name: {name}\n"
        "  folder: /PROD\n"
        "spec:\n"
        "  host: h\n"
        "  login: L\n"
        "  script: s\n",
    )
    return tmp_path


def test_apply_json_aborts_without_auto_approve(
    env: None, tmp_path: Path,
) -> None:
    # JSON mode refuses the interactive prompt; the caller must pass
    # --auto-approve. This test locks that contract down.
    _write_simple_job(tmp_path)
    result = CliRunner().invoke(
        app, ["--output", "json", "apply", str(tmp_path)],
    )
    assert result.exit_code == 1, result.output
    doc = _parse_last_json_line(result.output)
    _assert_envelope(doc, command="apply")
    assert doc["status"] == "aborted"
    assert "auto-approve" in doc["summary"]["reason"].lower()


def test_apply_json_success_with_auto_approve(
    env: None, tmp_path: Path,
) -> None:
    _write_simple_job(tmp_path, name="J")
    base = "http://json.test/ae/api/v1/100"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock)
        mock.get(f"{base}/objects/J").mock(return_value=httpx.Response(404))
        mock.post(f"{base}/objects").mock(
            return_value=httpx.Response(201, json={"Name": "J"}),
        )

        result = CliRunner().invoke(
            app,
            [
                "--output", "json",
                "apply", str(tmp_path), "--auto-approve",
            ],
        )

    assert result.exit_code == 0, result.output
    doc = _parse_last_json_line(result.output)
    _assert_envelope(doc, command="apply")
    assert doc["status"] == "ok"
    summary = doc["summary"]
    assert summary["failures"] == 0
    assert summary["successes"] >= 1


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def test_export_json_dry_run(env: None, tmp_path: Path) -> None:
    # --dry-run makes zero HTTP calls, so no respx setup required.
    result = CliRunner().invoke(
        app,
        [
            "--output", "json",
            "export",
            "--output-dir", str(tmp_path / "out"),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    doc = _parse_last_json_line(result.output)
    _assert_envelope(doc, command="export")
    assert doc["status"] == "ok"
    summary = doc["summary"]
    assert summary["dry_run"] is True
    assert summary["layout"] == "by-folder"


def test_export_json_invalid_layout(env: None, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "--output", "json",
            "export",
            "--output-dir", str(tmp_path / "out"),
            "--layout", "nonsense",
            "--dry-run",
        ],
    )
    assert result.exit_code == 1
    doc = _parse_last_json_line(result.output)
    _assert_envelope(doc, command="export")
    assert doc["status"] == "errors"
    assert "layout" in doc["summary"]["error"].lower()


# ---------------------------------------------------------------------------
# destroy
# ---------------------------------------------------------------------------


def test_destroy_json_without_confirm_errors(
    env: None, tmp_path: Path,
) -> None:
    _write_simple_job(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "--output", "json",
            "destroy", str(tmp_path),
        ],
    )
    assert result.exit_code == 1, result.output
    doc = _parse_last_json_line(result.output)
    _assert_envelope(doc, command="destroy")
    assert doc["status"] == "errors"
    assert "--confirm" in doc["summary"]["error"]


def test_destroy_json_requires_auto_approve_in_json_mode(
    env: None, tmp_path: Path,
) -> None:
    _write_simple_job(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "--output", "json",
            "destroy", str(tmp_path),
            "--confirm",
        ],
    )
    assert result.exit_code == 1, result.output
    doc = _parse_last_json_line(result.output)
    _assert_envelope(doc, command="destroy")
    assert doc["status"] == "aborted"
