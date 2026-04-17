"""Tests for `aromic auth check` debug subcommand."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from op_aromic.cli.app import app
from op_aromic.client.http import _AUTH_PATH


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMIC_URL", "http://auth.test/ae/api/v1")
    monkeypatch.setenv("AUTOMIC_CLIENT_ID", "100")
    monkeypatch.setenv("AUTOMIC_USER", "alice")
    monkeypatch.setenv("AUTOMIC_DEPARTMENT", "ADMIN")
    monkeypatch.setenv("AUTOMIC_PASSWORD", "pw")
    monkeypatch.setenv("AUTOMIC_VERIFY_SSL", "false")
    monkeypatch.setenv("AUTOMIC_MAX_RETRIES", "0")


def test_auth_check_ok_human() -> None:
    runner = CliRunner()
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"http://auth.test/ae/api/v1{_AUTH_PATH}").mock(
            return_value=httpx.Response(200, json={"token": "t", "expires_in": 3600}),
        )
        # list_objects request — empty page returns nothing, auth succeeds.
        mock.get("http://auth.test/ae/api/v1/100/objects").mock(
            return_value=httpx.Response(200, json={"data": []}),
        )
        result = runner.invoke(app, ["auth", "check"])

    assert result.exit_code == 0, result.output
    assert "OK" in result.output or "authenticated" in result.output.lower()


def test_auth_check_ok_json() -> None:
    runner = CliRunner()
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"http://auth.test/ae/api/v1{_AUTH_PATH}").mock(
            return_value=httpx.Response(200, json={"token": "t", "expires_in": 3600}),
        )
        mock.get("http://auth.test/ae/api/v1/100/objects").mock(
            return_value=httpx.Response(200, json={"data": []}),
        )
        result = runner.invoke(app, ["--output", "json", "auth", "check"])

    assert result.exit_code == 0, result.output
    doc = json.loads(result.output.strip().splitlines()[-1])
    assert doc["command"] == "auth.check"
    assert doc["status"] == "ok"
    # Credentials must not be echoed.
    assert "password" not in json.dumps(doc).lower()


def test_auth_check_failed_auth_exits_one() -> None:
    runner = CliRunner()
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"http://auth.test/ae/api/v1{_AUTH_PATH}").mock(
            return_value=httpx.Response(401, text="nope"),
        )
        result = runner.invoke(app, ["--output", "json", "auth", "check"])

    assert result.exit_code == 1, result.output
    doc = json.loads(result.output.strip().splitlines()[-1])
    assert doc["command"] == "auth.check"
    assert doc["status"] == "errors"


def test_auth_check_appears_in_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["auth", "--help"])
    assert result.exit_code == 0
    assert "check" in result.output
