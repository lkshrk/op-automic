"""CLI tests for ``aromic apply``.

Exercises the prompt, --auto-approve, --plan-file round-trip, --dry-run.
respx blocks all network calls; anything that escapes fails loudly.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from op_aromic.cli.app import app
from op_aromic.client.http import _AUTH_PATH
from op_aromic.config.settings import AutomicSettings

_runner = CliRunner()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMIC_URL", "http://cli.test/ae/api/v1")
    monkeypatch.setenv("AUTOMIC_CLIENT_ID", "100")
    monkeypatch.setenv("AUTOMIC_USER", "U")
    monkeypatch.setenv("AUTOMIC_DEPARTMENT", "D")
    monkeypatch.setenv("AUTOMIC_PASSWORD", "pw")
    monkeypatch.setenv("AUTOMIC_VERIFY_SSL", "false")


def _write_manifest(tmp_path: Path, name: str = "X") -> Path:
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


def _settings() -> AutomicSettings:
    return AutomicSettings(
        url="http://cli.test/ae/api/v1",
        client_id=100,
        user="U",
        department="D",
        password="pw",
        verify_ssl=False,
        max_retries=0,
    )


def _mock_auth(mock: respx.MockRouter) -> None:
    settings = _settings()
    mock.post(f"{settings.url}{_AUTH_PATH}").mock(
        return_value=httpx.Response(200, json={"token": "t", "expires_in": 3600}),
    )


def _mock_empty_server(mock: respx.MockRouter, name: str) -> None:
    """404 on GET, 201 on POST — exercises a clean create path."""
    base = "http://cli.test/ae/api/v1/100"
    mock.get(f"{base}/objects/{name}").mock(return_value=httpx.Response(404))
    mock.post(f"{base}/objects").mock(
        return_value=httpx.Response(201, json={"Name": name}),
    )


def test_apply_prompt_accepts_yes(env: None, tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock)
        _mock_empty_server(mock, "X")
        result = _runner.invoke(app, ["apply", str(tmp_path)], input="yes\n")
    assert result.exit_code == 0, result.output


def test_apply_prompt_rejects_y_shorthand(env: None, tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock)
        _mock_empty_server(mock, "X")
        result = _runner.invoke(app, ["apply", str(tmp_path)], input="y\n")
    # Rejected prompt → exit 1 with no writes.
    assert result.exit_code == 1
    assert "aborted" in result.output.lower() or "cancelled" in result.output.lower()


def test_apply_prompt_rejects_yes_all_caps(env: None, tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock)
        _mock_empty_server(mock, "X")
        result = _runner.invoke(app, ["apply", str(tmp_path)], input="YES\n")
    assert result.exit_code == 1


def test_apply_auto_approve_skips_prompt(env: None, tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock)
        _mock_empty_server(mock, "X")
        # No input provided — auto-approve must not read stdin.
        result = _runner.invoke(app, ["apply", str(tmp_path), "--auto-approve"])
    assert result.exit_code == 0, result.output


def test_apply_dry_run_makes_no_http_calls(env: None, tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock)
        base = "http://cli.test/ae/api/v1/100"
        mock.get(f"{base}/objects/X").mock(return_value=httpx.Response(404))
        post_route = mock.post(f"{base}/objects").mock(
            return_value=httpx.Response(500, text="should not be called"),
        )
        result = _runner.invoke(
            app, ["apply", str(tmp_path), "--dry-run", "--auto-approve"],
        )
    assert not post_route.called
    assert result.exit_code == 0


def test_apply_plan_file_round_trip(env: None, tmp_path: Path) -> None:
    _write_manifest(tmp_path, name="Y")
    plan_json = tmp_path / "plan.json"

    base = "http://cli.test/ae/api/v1/100"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock)
        mock.get(f"{base}/objects/Y").mock(return_value=httpx.Response(404))
        # Step 1: plan writes the file; exit code 2 means "changes pending".
        result = _runner.invoke(
            app, ["plan", str(tmp_path), "--out", str(plan_json)],
        )
    assert plan_json.exists()
    # plan exit code is 2 when there are pending changes.
    assert result.exit_code == 2

    # Step 2: apply --plan-file trusts the file and applies it.
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock)
        post_route = mock.post(f"{base}/objects").mock(
            return_value=httpx.Response(201, json={"Name": "Y"}),
        )
        result = _runner.invoke(
            app,
            [
                "apply",
                str(tmp_path),
                "--plan-file",
                str(plan_json),
                "--auto-approve",
            ],
        )
    assert post_route.called
    assert result.exit_code == 0


def test_apply_invalid_manifest_exits_1(env: None, tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("not: a valid: manifest: shape:")
    result = _runner.invoke(app, ["apply", str(tmp_path), "--auto-approve"])
    assert result.exit_code == 1


def test_apply_exit_code_2_on_partial_success(env: None, tmp_path: Path) -> None:
    # Server returns 500 → applier records a failure → exit 2.
    _write_manifest(tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock)
        base = "http://cli.test/ae/api/v1/100"
        mock.get(f"{base}/objects/X").mock(return_value=httpx.Response(404))
        mock.post(f"{base}/objects").mock(
            return_value=httpx.Response(500, text="boom"),
        )
        result = _runner.invoke(
            app, ["apply", str(tmp_path), "--auto-approve"],
        )
    assert result.exit_code == 2
