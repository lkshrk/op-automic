"""Tests for AutomicSettings — defaults, new fields, env overrides."""

from __future__ import annotations

import pytest

from op_aromic.config.settings import AutomicSettings


def test_default_auto_create_folders() -> None:
    s = AutomicSettings(url="http://x", user="u", password="p")
    assert s.auto_create_folders is True


def test_default_retry_base_delay_ms() -> None:
    s = AutomicSettings(url="http://x", user="u", password="p")
    assert s.retry_base_delay_ms == 500


def test_default_retry_max_backoff_s() -> None:
    s = AutomicSettings(url="http://x", user="u", password="p")
    assert s.retry_max_backoff_s == 5.0


def test_default_retry_statuses() -> None:
    s = AutomicSettings(url="http://x", user="u", password="p")
    assert s.retry_statuses == [429]


def test_default_update_method() -> None:
    s = AutomicSettings(url="http://x", user="u", password="p")
    assert s.update_method == "POST_IMPORT"


def test_default_auth_method() -> None:
    s = AutomicSettings(url="http://x", user="u", password="p")
    assert s.auth_method == "basic"


def test_env_override_auto_create_folders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMIC_AUTO_CREATE_FOLDERS", "false")
    s = AutomicSettings(url="http://x", user="u", password="p")
    assert s.auto_create_folders is False


def test_env_override_retry_base_delay_ms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMIC_RETRY_BASE_DELAY_MS", "1000")
    s = AutomicSettings(url="http://x", user="u", password="p")
    assert s.retry_base_delay_ms == 1000


def test_env_override_retry_max_backoff_s(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMIC_RETRY_MAX_BACKOFF_S", "10.0")
    s = AutomicSettings(url="http://x", user="u", password="p")
    assert s.retry_max_backoff_s == 10.0


def test_env_override_update_method(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMIC_UPDATE_METHOD", "PUT")
    s = AutomicSettings(url="http://x", user="u", password="p")
    assert s.update_method == "PUT"


def test_env_override_auth_method(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMIC_AUTH_METHOD", "bearer")
    s = AutomicSettings(url="http://x", user="u", password="p")
    assert s.auth_method == "bearer"


def test_invalid_update_method_raises() -> None:
    with pytest.raises(Exception):
        AutomicSettings(
            url="http://x", user="u", password="p", update_method="PATCH",  # type: ignore[arg-type]
        )


def test_invalid_auth_method_raises() -> None:
    with pytest.raises(Exception):
        AutomicSettings(
            url="http://x", user="u", password="p", auth_method="digest",  # type: ignore[arg-type]
        )
