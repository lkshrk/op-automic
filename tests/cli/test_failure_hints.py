"""Unit tests for failure_hint() mapping in cli/prompts.py."""

from __future__ import annotations

import pytest

from op_aromic.cli.prompts import failure_hint


@pytest.mark.parametrize(
    ("reason", "expected_substring"),
    [
        ("HTTP 401 Unauthorized", "auth check"),
        ("Unauthorized: bad creds", "auth check"),
        ("HTTP 403 Forbidden", "Automic role"),
        ("Forbidden: missing perm", "Automic role"),
        ("auto_create_folders=False so refused", "auto-create-folders"),
        ("Folder '/X' does not exist", "metadata.folder"),
        ("concurrent edit detected on OBJ", "re-plan or pass --force"),
        ("HTTP 429 Too Many Requests", "retry"),
        ("Too Many Requests for /objects", "retry"),
        ("HTTP 500 Internal Server Error", "server error"),
        ("HTTP 502 Bad Gateway", "server error"),
        ("HTTP 503 Service Unavailable", "server error"),
    ],
)
def test_failure_hint_matches(reason: str, expected_substring: str) -> None:
    hint = failure_hint(reason)
    assert hint is not None, f"no hint for {reason!r}"
    assert expected_substring in hint


def test_failure_hint_unknown_returns_none() -> None:
    assert failure_hint("something totally unrelated") is None


def test_failure_hint_first_match_wins() -> None:
    # Reason containing both "401" and "Folder" should match the earlier
    # "401" hint (more specific to the failure source).
    reason = "HTTP 401 Unauthorized while creating Folder '/X'"
    hint = failure_hint(reason)
    assert hint is not None
    assert "auth check" in hint
