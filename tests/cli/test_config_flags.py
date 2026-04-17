"""Tests: CLI flag overrides beat env vars beat yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from op_aromic.cli.app import app

runner = CliRunner()


def _captured_settings(invoke_result: Any) -> Any:
    """Extract the AutomicSettings captured by the mock."""
    return invoke_result


def test_cli_url_flag_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """--automic-url beats AUTOMIC_URL env var."""
    monkeypatch.setenv("AUTOMIC_URL", "http://env-host/ae/api/v1")
    monkeypatch.setenv("AUTOMIC_USER", "u")
    monkeypatch.setenv("AUTOMIC_PASSWORD", "p")

    captured: dict[str, Any] = {}

    def fake_client(settings: Any) -> Any:
        captured["settings"] = settings
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.list_objects = MagicMock(return_value=iter([]))
        return m

    with patch("op_aromic.cli.app._build_api_client", side_effect=fake_client):
        runner.invoke(
            app,
            ["--automic-url", "http://cli-host/ae/api/v1", "auth", "check"],
        )

    assert captured["settings"].url == "http://cli-host/ae/api/v1"


def test_cli_update_method_flag_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """--update-method CLI flag beats AUTOMIC_UPDATE_METHOD env var."""
    monkeypatch.setenv("AUTOMIC_UPDATE_METHOD", "POST_IMPORT")
    monkeypatch.setenv("AUTOMIC_USER", "u")
    monkeypatch.setenv("AUTOMIC_PASSWORD", "p")

    captured: dict[str, Any] = {}

    def fake_client(settings: Any) -> Any:
        captured["settings"] = settings
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.list_objects = MagicMock(return_value=iter([]))
        return m

    with patch("op_aromic.cli.app._build_api_client", side_effect=fake_client):
        runner.invoke(
            app,
            ["--update-method", "PUT", "auth", "check"],
        )

    assert captured["settings"].update_method == "PUT"


def test_cli_auto_create_folders_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """--no-auto-create-folders sets auto_create_folders=False."""
    monkeypatch.setenv("AUTOMIC_USER", "u")
    monkeypatch.setenv("AUTOMIC_PASSWORD", "p")

    captured: dict[str, Any] = {}

    def fake_client(settings: Any) -> Any:
        captured["settings"] = settings
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.list_objects = MagicMock(return_value=iter([]))
        return m

    with patch("op_aromic.cli.app._build_api_client", side_effect=fake_client):
        runner.invoke(
            app,
            ["--no-auto-create-folders", "auth", "check"],
        )

    assert captured["settings"].auto_create_folders is False


def test_cli_config_file_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--config loads YAML file and supplies settings."""
    config = tmp_path / "aromic.yaml"
    config.write_text("retry_base_delay_ms: 9999\n")
    monkeypatch.delenv("AUTOMIC_RETRY_BASE_DELAY_MS", raising=False)
    monkeypatch.delenv("AROMIC_CONFIG_FILE", raising=False)
    monkeypatch.setenv("AUTOMIC_USER", "u")
    monkeypatch.setenv("AUTOMIC_PASSWORD", "p")

    captured: dict[str, Any] = {}

    def fake_client(settings: Any) -> Any:
        captured["settings"] = settings
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.list_objects = MagicMock(return_value=iter([]))
        return m

    with patch("op_aromic.cli.app._build_api_client", side_effect=fake_client):
        runner.invoke(
            app,
            ["--config", str(config), "auth", "check"],
        )

    assert captured["settings"].retry_base_delay_ms == 9999
