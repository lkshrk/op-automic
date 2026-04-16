"""Smoke tests for the structured logging helper."""

from __future__ import annotations

import pytest

from op_aromic.logging import setup_logging


def test_setup_logging_default() -> None:
    setup_logging()


def test_setup_logging_verbose() -> None:
    setup_logging(verbose=True)


def test_setup_logging_json_output() -> None:
    setup_logging(json_output=True)


def test_setup_logging_ci_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    setup_logging()
