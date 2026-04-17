"""Tests for the append-only revision ledger."""

from __future__ import annotations

import json
from pathlib import Path

from op_aromic.engine.ledger import append_row, path_for, read_rows


def test_append_and_read_roundtrip(tmp_path: Path) -> None:
    row = append_row(
        kind="Workflow",
        name="FOO",
        action="create",
        revision="sha256:" + "a" * 64,
        automic_version_before=None,
        automic_version_after=None,
        root=tmp_path,
    )
    assert row is not None
    assert row.revision.startswith("sha256:")

    rows = read_rows("Workflow", "FOO", root=tmp_path)
    assert len(rows) == 1
    assert rows[0]["action"] == "create"
    assert rows[0]["revision"] == row.revision
    assert "ts" in rows[0]
    assert "by" in rows[0]


def test_ledger_is_append_only(tmp_path: Path) -> None:
    for action in ("create", "update", "update", "delete"):
        append_row(
            kind="Workflow",
            name="FOO",
            action=action,  # type: ignore[arg-type]
            revision=None if action == "delete" else "sha256:" + "b" * 64,
            root=tmp_path,
        )

    rows = read_rows("Workflow", "FOO", root=tmp_path)
    assert [r["action"] for r in rows] == ["create", "update", "update", "delete"]
    assert rows[-1]["revision"] is None  # delete has no revision


def test_read_missing_returns_empty(tmp_path: Path) -> None:
    assert read_rows("Workflow", "NOPE", root=tmp_path) == []


def test_corrupt_line_is_skipped(tmp_path: Path) -> None:
    # First a valid row.
    append_row(
        kind="Workflow", name="FOO", action="create",
        revision="sha256:" + "c" * 64, root=tmp_path,
    )
    # Then inject a corrupt line and a second valid row.
    target = path_for("Workflow", "FOO", root=tmp_path)
    with target.open("a", encoding="utf-8") as fp:
        fp.write("{not-json\n")
        fp.write(json.dumps({"ts": "x", "action": "update", "revision": None}) + "\n")

    rows = read_rows("Workflow", "FOO", root=tmp_path)
    # Corrupt line dropped; valid rows preserved.
    assert len(rows) == 2
    assert [r["action"] for r in rows] == ["create", "update"]


def test_env_override(monkeypatch, tmp_path: Path) -> None:
    from op_aromic.engine.ledger import ledger_root

    monkeypatch.setenv("AROMIC_LEDGER_DIR", str(tmp_path / "custom"))
    assert ledger_root() == tmp_path / "custom"
