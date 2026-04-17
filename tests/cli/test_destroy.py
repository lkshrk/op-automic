"""CLI tests for ``aromic destroy``."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from op_aromic.cli.app import app

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
        "  annotations:\n"
        "    aromic.io/managed-by: op-aromic\n"
        "spec:\n"
        "  host: h\n"
        "  login: L\n"
        "  script: s\n",
    )
    return tmp_path


def _mock_auth(mock: respx.MockRouter) -> None:
    mock.post("http://cli.test/ae/api/v1/authenticate").mock(
        return_value=httpx.Response(200, json={"token": "t", "expires_in": 3600}),
    )


def test_destroy_without_confirm_exits_1(env: None, tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    result = _runner.invoke(app, ["destroy", str(tmp_path)])
    assert result.exit_code == 1
    assert "--confirm" in result.output


def test_destroy_with_confirm_runs(env: None, tmp_path: Path) -> None:
    # B5: DELETE not supported in v21 — records not_supported, exits 2 (partial).
    _write_manifest(tmp_path)
    base = "http://cli.test/ae/api/v1/100"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock)
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(
                200,
                json={
                    "Name": "X",
                    "Type": "JOBS",
                    "Folder": "/PROD",
                    "Annotations": {"aromic.io/managed-by": "op-aromic"},
                },
            ),
        )
        result = _runner.invoke(
            app,
            ["destroy", str(tmp_path), "--confirm", "--auto-approve"],
        )
    # Partial because not_supported is non-empty; DELETE was not called.
    assert result.exit_code == 2
    assert "not supported" in result.output.lower() or "NOT SUPPORTED" in result.output


def test_destroy_only_managed_refuses_unmanaged(env: None, tmp_path: Path) -> None:
    # Manifest doesn't include the managed-by annotation on the REMOTE (we
    # don't control the server) → default only_managed refuses.
    (tmp_path / "x.yaml").write_text(
        "apiVersion: aromic.io/v1\n"
        "kind: Job\n"
        "metadata:\n"
        "  name: X\n"
        "  folder: /PROD\n"
        "spec:\n"
        "  host: h\n"
        "  login: L\n"
        "  script: s\n",
    )
    base = "http://cli.test/ae/api/v1/100"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock)
        # Remote payload has no managed-by marker.
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(
                200,
                json={"Name": "X", "Type": "JOBS", "Folder": "/PROD"},
            ),
        )
        result = _runner.invoke(
            app,
            ["destroy", str(tmp_path), "--confirm", "--auto-approve"],
        )
    # Refused → partial exit.
    assert result.exit_code == 2


def test_destroy_only_managed_false_records_not_supported(env: None, tmp_path: Path) -> None:
    # B5: even with --no-only-managed, DELETE is not called (v21 limitation).
    (tmp_path / "x.yaml").write_text(
        "apiVersion: aromic.io/v1\n"
        "kind: Job\n"
        "metadata:\n"
        "  name: X\n"
        "  folder: /PROD\n"
        "spec:\n"
        "  host: h\n"
        "  login: L\n"
        "  script: s\n",
    )
    base = "http://cli.test/ae/api/v1/100"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock)
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(
                200,
                json={"Name": "X", "Type": "JOBS", "Folder": "/PROD"},
            ),
        )
        result = _runner.invoke(
            app,
            [
                "destroy",
                str(tmp_path),
                "--confirm",
                "--auto-approve",
                "--no-only-managed",
            ],
        )
    # not_supported → partial exit.
    assert result.exit_code == 2
    assert "not supported" in result.output.lower() or "NOT SUPPORTED" in result.output


def test_destroy_dry_run_makes_no_calls(env: None, tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    base = "http://cli.test/ae/api/v1/100"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock)
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(
                200,
                json={
                    "Name": "X",
                    "Type": "JOBS",
                    "Folder": "/PROD",
                    "Annotations": {"aromic.io/managed-by": "op-aromic"},
                },
            ),
        )
        delete_route = mock.delete(f"{base}/objects/X").mock(
            return_value=httpx.Response(500),
        )
        result = _runner.invoke(
            app,
            [
                "destroy",
                str(tmp_path),
                "--confirm",
                "--auto-approve",
                "--dry-run",
            ],
        )
    assert not delete_route.called
    assert result.exit_code == 0
    # Dry-run preview lists what would be deleted and uses "Would destroy" verb.
    assert "Would delete (reverse-dependency order)" in result.stdout
    assert "Job/X" in result.stdout
    assert "Would destroy:" in result.stdout


def test_destroy_prompt_rejects_non_yes(env: None, tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    base = "http://cli.test/ae/api/v1/100"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock)
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(
                200,
                json={
                    "Name": "X",
                    "Type": "JOBS",
                    "Folder": "/PROD",
                    "Annotations": {"aromic.io/managed-by": "op-aromic"},
                },
            ),
        )
        result = _runner.invoke(
            app,
            ["destroy", str(tmp_path), "--confirm"],
            input="y\n",
        )
    assert result.exit_code == 1
