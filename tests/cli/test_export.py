"""Tests for `aromic export`.

The CLI is a thin wrapper around :func:`engine.exporter.export`; these
tests focus on flag parsing and the glue that constructs the API client
from settings. Deeper behavioural coverage lives in
``tests/engine/test_exporter.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from op_aromic.cli.app import app
from op_aromic.client.http import _AUTH_PATH

AUTOMIC_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "automic"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMIC_URL", "http://export.test/ae/api/v1")
    monkeypatch.setenv("AUTOMIC_CLIENT_ID", "100")
    monkeypatch.setenv("AUTOMIC_USER", "U")
    monkeypatch.setenv("AUTOMIC_DEPARTMENT", "D")
    monkeypatch.setenv("AUTOMIC_PASSWORD", "P")
    monkeypatch.setenv("AUTOMIC_VERIFY_SSL", "false")
    monkeypatch.setenv("AUTOMIC_MAX_RETRIES", "0")


def _load_fixture(name: str) -> dict:
    return json.loads((AUTOMIC_FIXTURES / name).read_text())


def _install_routes(mock: respx.MockRouter, base: str, job: dict) -> None:
    mock.post(f"http://export.test/ae/api/v1{_AUTH_PATH}").mock(
        return_value=httpx.Response(200, json={"token": "t", "expires_in": 3600}),
    )

    def list_responder(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params.get("type") == "JOBS":
            return httpx.Response(200, json={"data": [job]})
        return httpx.Response(200, json={"data": []})

    mock.get(f"{base}/objects").mock(side_effect=list_responder)
    mock.get(f"{base}/objects/{job['Name']}").mock(
        return_value=httpx.Response(200, json=job),
    )


def test_export_writes_files_with_filter(tmp_path: Path) -> None:
    runner = CliRunner()
    base = "http://export.test/ae/api/v1/100"
    job = _load_fixture("job.json")

    with respx.mock(assert_all_called=False) as mock:
        _install_routes(mock, base, job)
        result = runner.invoke(
            app,
            [
                "export",
                "--output-dir",
                str(tmp_path),
                "--filter",
                "Job",
                "--layout",
                "by-kind",
            ],
        )

    assert result.exit_code == 0, result.output
    job_file = tmp_path / "jobs" / "ETL.EXTRACT.yaml"
    assert job_file.exists()
    # Output should mention the count / paths.
    assert "1" in result.output


def test_export_dry_run_makes_no_http_calls(tmp_path: Path) -> None:
    runner = CliRunner()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__regex=r".*").mock(
            return_value=httpx.Response(500, text="should not be called"),
        )
        result = runner.invoke(
            app,
            [
                "export",
                "--output-dir",
                str(tmp_path),
                "--dry-run",
            ],
        )

    assert result.exit_code == 0, result.output
    assert not route.called
    # Nothing should be written.
    assert list(tmp_path.iterdir()) == []


def test_export_overwrite_flag_passes_through(tmp_path: Path) -> None:
    runner = CliRunner()
    base = "http://export.test/ae/api/v1/100"
    job = _load_fixture("job.json")

    target = tmp_path / "jobs" / "ETL.EXTRACT.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("# existing\n")

    with respx.mock(assert_all_called=False) as mock:
        _install_routes(mock, base, job)
        result = runner.invoke(
            app,
            [
                "export",
                "--output-dir",
                str(tmp_path),
                "--filter",
                "Job",
                "--layout",
                "by-kind",
                "--overwrite",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "# existing" not in target.read_text()


def test_export_folder_filter(tmp_path: Path) -> None:
    # B4: folder-scoped export uses GET /folderobjects/{path} not /objects?folder=
    runner = CliRunner()
    base = "http://export.test/ae/api/v1/100"
    job_a = _load_fixture("job.json")  # Folder: /PROD/ETL

    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"http://export.test/ae/api/v1{_AUTH_PATH}").mock(
            return_value=httpx.Response(200, json={"token": "t", "expires_in": 3600}),
        )
        # Folder-scoped list: GET /folderobjects/PROD/ETL returns only job_a.
        mock.get(f"{base}/folderobjects/PROD/ETL").mock(
            return_value=httpx.Response(200, json={"data": [job_a]}),
        )
        mock.get(f"{base}/objects/ETL.EXTRACT").mock(
            return_value=httpx.Response(200, json=job_a),
        )
        # OTHER.JOB is never listed so never fetched; no mock needed.

        result = runner.invoke(
            app,
            [
                "export",
                "--output-dir",
                str(tmp_path),
                "--filter",
                "Job",
                "--folder",
                "/PROD/ETL",
                "--layout",
                "by-kind",
            ],
        )

    assert result.exit_code == 0, result.output
    # Only the /PROD/ETL job should have been written.
    job_file = tmp_path / "jobs" / "ETL.EXTRACT.yaml"
    other_file = tmp_path / "jobs" / "OTHER.JOB.yaml"
    assert job_file.exists()
    assert not other_file.exists()


def test_export_unknown_layout_exits_one(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "export",
            "--output-dir",
            str(tmp_path),
            "--layout",
            "sideways",
        ],
    )
    assert result.exit_code != 0
