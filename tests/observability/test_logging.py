"""Tests for the observability logging module.

The processor under test is pure (dict-in, dict-out), so most assertions
invoke it directly without standing up the whole structlog pipeline.
``configure_logging`` is exercised separately for text/JSON output via the
``PrintLogger`` writing to ``stderr``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import structlog

from op_aromic.observability.logging import (
    _REDACTED,
    configure_logging,
    get_logger,
    redact_secrets,
)


def _call(event_dict: dict[str, Any]) -> dict[str, Any]:
    return dict(redact_secrets(None, "info", event_dict))  # type: ignore[arg-type]


class TestRedactSecrets:
    def test_redacts_password_key(self) -> None:
        out = _call({"event": "login", "password": "hunter2"})
        assert out["password"] == _REDACTED
        assert out["event"] == "login"

    def test_redacts_token_key(self) -> None:
        out = _call({"event": "x", "token": "abc123"})
        assert out["token"] == _REDACTED

    def test_redacts_authorization_key_case_insensitive(self) -> None:
        out = _call({"Authorization": "Bearer abc"})
        assert out["Authorization"] == _REDACTED

    def test_redacts_automic_password_key(self) -> None:
        out = _call({"automic_password": "s3cr3t"})
        assert out["automic_password"] == _REDACTED

    def test_redacts_x_api_key(self) -> None:
        out = _call({"x-api-key": "k"})
        assert out["x-api-key"] == _REDACTED

    def test_redacts_secret_key(self) -> None:
        out = _call({"secret": "sshh"})
        assert out["secret"] == _REDACTED

    def test_does_not_redact_similar_but_different_key(self) -> None:
        # ``authorization_policy`` is not on the list.
        out = _call({"authorization_policy": "strict"})
        assert out["authorization_policy"] == "strict"

    def test_redacts_bearer_value_in_any_key(self) -> None:
        # A dict value shaped like ``Bearer ...`` must be redacted even
        # though the key is not on the list.
        out = _call({"headers": {"misc-header": "Bearer eyJhbGciOi..."}})
        assert out["headers"]["misc-header"] == _REDACTED

    def test_redacts_nested_secrets(self) -> None:
        out = _call(
            {
                "event": "http.request",
                "request": {
                    "headers": {"Authorization": "Bearer x"},
                    "method": "GET",
                },
            },
        )
        assert out["request"]["headers"]["Authorization"] == _REDACTED
        assert out["request"]["method"] == "GET"

    def test_redacts_inside_list(self) -> None:
        out = _call({"items": [{"password": "pw"}, {"ok": 1}]})
        assert out["items"][0]["password"] == _REDACTED
        assert out["items"][1]["ok"] == 1


class TestConfigureLogging:
    def test_json_format_emits_parseable_json(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CI", raising=False)
        configure_logging(level="debug", format="json")
        get_logger("test").info("hello", user="alice")
        err = capsys.readouterr().err.strip()
        assert err, "expected at least one log line on stderr"
        payload = json.loads(err.splitlines()[-1])
        assert payload["event"] == "hello"
        assert payload["user"] == "alice"
        assert "timestamp" in payload
        assert payload["level"] == "info"

    def test_ci_env_forces_json(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CI", "true")
        configure_logging(level="info", format="text")
        get_logger().warning("ci-log")
        err = capsys.readouterr().err.strip().splitlines()[-1]
        payload = json.loads(err)
        assert payload["event"] == "ci-log"

    def test_text_format_is_not_json(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CI", raising=False)
        configure_logging(level="info", format="text")
        get_logger().info("human-log", user="bob")
        err = capsys.readouterr().err.strip()
        assert "human-log" in err
        with pytest.raises(json.JSONDecodeError):
            json.loads(err)

    def test_redaction_runs_in_pipeline(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CI", raising=False)
        configure_logging(level="debug", format="json")
        get_logger().info("auth", password="hunter2", token="tok", safe="ok")
        err = capsys.readouterr().err.strip().splitlines()[-1]
        payload = json.loads(err)
        assert payload["password"] == _REDACTED
        assert payload["token"] == _REDACTED
        assert payload["safe"] == "ok"

    def test_level_debug_enables_debug(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CI", raising=False)
        configure_logging(level="debug", format="json")
        get_logger().debug("dbg")
        err = capsys.readouterr().err.strip()
        assert "dbg" in err

    def test_level_warning_suppresses_info(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CI", raising=False)
        configure_logging(level="warning", format="json")
        get_logger().info("should-not-appear")
        err = capsys.readouterr().err
        assert "should-not-appear" not in err


@pytest.fixture(autouse=True)
def _reset_structlog() -> None:
    """Avoid cross-test pollution: tests configure their own pipeline."""
    structlog.reset_defaults()
