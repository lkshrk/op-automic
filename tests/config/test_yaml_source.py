"""Tests for YAML config file loading in AutomicSettings."""

from __future__ import annotations

from pathlib import Path

import pytest

from op_aromic.config.settings import AutomicSettings


def test_yaml_file_only_sets_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings are loaded from a YAML file when no env overrides exist."""
    config = tmp_path / "aromic.yaml"
    config.write_text("url: http://yaml-host/ae/api/v1\n")
    monkeypatch.setenv("AROMIC_CONFIG_FILE", str(config))
    # Clear any env var that could override
    monkeypatch.delenv("AUTOMIC_URL", raising=False)
    s = AutomicSettings()
    assert s.url == "http://yaml-host/ae/api/v1"


def test_env_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Env var beats YAML config for the same key."""
    config = tmp_path / "aromic.yaml"
    config.write_text("url: http://yaml-host/ae/api/v1\n")
    monkeypatch.setenv("AROMIC_CONFIG_FILE", str(config))
    monkeypatch.setenv("AUTOMIC_URL", "http://env-host/ae/api/v1")
    s = AutomicSettings()
    assert s.url == "http://env-host/ae/api/v1"


def test_missing_yaml_file_is_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No config file on the search path → defaults apply, no error raised."""
    # Point AROMIC_CONFIG_FILE to a directory with no aromic.yaml
    # Use the search-path mechanism: clear the env var and use a tmp cwd
    # that has no aromic.yaml / aromic.yml
    monkeypatch.delenv("AROMIC_CONFIG_FILE", raising=False)
    monkeypatch.delenv("AUTOMIC_URL", raising=False)
    # Change cwd to tmp_path which has no config file
    monkeypatch.chdir(tmp_path)
    # Should not raise
    s = AutomicSettings()
    assert s.url == "http://localhost:8080/ae/api/v1"


def test_bad_yaml_raises_helpful_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed YAML raises ValueError with path in message."""
    config = tmp_path / "aromic.yaml"
    config.write_text("key: [unclosed bracket\n")
    monkeypatch.setenv("AROMIC_CONFIG_FILE", str(config))
    with pytest.raises(ValueError, match="Failed to parse YAML"):
        AutomicSettings()


def test_yaml_non_mapping_raises_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """YAML that parses to a non-mapping raises ValueError."""
    config = tmp_path / "aromic.yaml"
    config.write_text("- item1\n- item2\n")
    monkeypatch.setenv("AROMIC_CONFIG_FILE", str(config))
    with pytest.raises(ValueError, match="must be a mapping"):
        AutomicSettings()


def test_yaml_empty_file_is_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty YAML file → defaults apply, no error raised."""
    config = tmp_path / "aromic.yaml"
    config.write_text("")
    monkeypatch.setenv("AROMIC_CONFIG_FILE", str(config))
    monkeypatch.delenv("AUTOMIC_URL", raising=False)
    s = AutomicSettings()
    assert s.url == "http://localhost:8080/ae/api/v1"


def test_yaml_sets_new_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase-6 fields can be loaded from YAML."""
    config = tmp_path / "aromic.yaml"
    config.write_text(
        "auto_create_folders: false\n"
        "retry_base_delay_ms: 2000\n"
        "update_method: PUT\n"
    )
    monkeypatch.setenv("AROMIC_CONFIG_FILE", str(config))
    monkeypatch.delenv("AUTOMIC_AUTO_CREATE_FOLDERS", raising=False)
    monkeypatch.delenv("AUTOMIC_RETRY_BASE_DELAY_MS", raising=False)
    monkeypatch.delenv("AUTOMIC_UPDATE_METHOD", raising=False)
    s = AutomicSettings()
    assert s.auto_create_folders is False
    assert s.retry_base_delay_ms == 2000
    assert s.update_method == "PUT"
