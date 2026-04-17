"""Tests for the history and rollback CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from op_aromic.cli.app import app
from op_aromic.engine.ledger import append_row


runner = CliRunner()


def _seed_ledger(root: Path) -> str:
    """Seed two rows under root and return the most-recent revision."""
    append_row(
        kind="Workflow",
        name="FOO",
        action="create",
        revision="sha256:" + "a" * 64,
        root=root,
    )
    newest = "sha256:" + "b" * 64
    append_row(
        kind="Workflow",
        name="FOO",
        action="update",
        revision=newest,
        root=root,
    )
    return newest


def test_history_prints_rows(tmp_path: Path) -> None:
    _seed_ledger(tmp_path)
    result = runner.invoke(
        app,
        ["history", "Workflow/FOO", "--ledger-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.stdout
    assert "Workflow/FOO" in result.stdout
    assert "create" in result.stdout
    assert "update" in result.stdout


def test_history_json(tmp_path: Path) -> None:
    _seed_ledger(tmp_path)
    result = runner.invoke(
        app,
        ["--output", "json", "history", "Workflow/FOO", "--ledger-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["command"] == "history"
    assert payload["status"] == "ok"
    assert payload["summary"]["rows"] == 2


def test_history_missing_target_format() -> None:
    result = runner.invoke(app, ["history", "invalid"])
    assert result.exit_code == 1
    assert "Kind/Name" in result.stdout or "Kind/Name" in result.stderr


def test_history_no_entries(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["history", "Workflow/NOPE", "--ledger-dir", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "no ledger entries" in result.stdout


def test_rollback_dry_run(tmp_path: Path, monkeypatch) -> None:
    # Ledger row must have a gitSha; the helper fetches it from git which may
    # not exist in CI, so we inject a row directly.
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    row = {
        "ts": "2026-04-17T12:00:00Z",
        "action": "create",
        "revision": "sha256:" + "a" * 64,
        "gitSha": "deadbee",
        "automicVersionBefore": None,
        "automicVersionAfter": None,
        "by": "tester",
    }
    target_file = ledger_dir / "Workflow" / "FOO.jsonl"
    target_file.parent.mkdir(parents=True)
    target_file.write_text(json.dumps(row) + "\n")

    # Provide a manifest file under manifests_root so rollback can locate it.
    manifests_root = tmp_path / "manifests"
    manifests_root.mkdir()
    (manifests_root / "foo.yaml").write_text(
        "apiVersion: aromic.io/v1\n"
        "kind: Workflow\n"
        "metadata:\n"
        "  name: FOO\n"
        "  folder: /X\n"
        "spec:\n"
        "  tasks: []\n",
    )

    result = runner.invoke(
        app,
        [
            "rollback",
            "Workflow/FOO",
            "--to",
            "a" * 8,
            "--manifests",
            str(manifests_root),
            "--ledger-dir",
            str(ledger_dir),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "would run" in result.stdout
    assert "deadbee" in result.stdout


def test_rollback_revision_not_found(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "ledger"
    append_row(
        kind="Workflow",
        name="FOO",
        action="create",
        revision="sha256:" + "c" * 64,
        root=ledger_dir,
    )
    manifests_root = tmp_path / "manifests"
    manifests_root.mkdir()
    (manifests_root / "foo.yaml").write_text(
        "apiVersion: aromic.io/v1\n"
        "kind: Workflow\n"
        "metadata:\n"
        "  name: FOO\n"
        "  folder: /X\n"
        "spec:\n"
        "  tasks: []\n",
    )
    result = runner.invoke(
        app,
        [
            "rollback",
            "Workflow/FOO",
            "--to",
            "deadbeef",
            "--manifests",
            str(manifests_root),
            "--ledger-dir",
            str(ledger_dir),
            "--dry-run",
        ],
    )
    assert result.exit_code == 1
