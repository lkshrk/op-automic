"""Tests for `aromic plan`.

Every respx mock must be active for the entire Typer invocation; we wrap
the runner call in a ``with respx.mock`` block and register every route
the CLI will hit.
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

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "manifests"
AUTOMIC_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "automic"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deterministic settings for every plan test.
    monkeypatch.setenv("AUTOMIC_URL", "http://plan.test/ae/api/v1")
    monkeypatch.setenv("AUTOMIC_CLIENT_ID", "100")
    monkeypatch.setenv("AUTOMIC_USER", "U")
    monkeypatch.setenv("AUTOMIC_DEPARTMENT", "D")
    monkeypatch.setenv("AUTOMIC_PASSWORD", "P")
    monkeypatch.setenv("AUTOMIC_VERIFY_SSL", "false")
    monkeypatch.setenv("AUTOMIC_MAX_RETRIES", "0")


def _setup_mocks(mock: respx.MockRouter) -> None:
    mock.post("http://plan.test/ae/api/v1/authenticate").mock(
        return_value=httpx.Response(200, json={"token": "t", "expires_in": 3600}),
    )


def _load_fixture(name: str) -> dict:
    return json.loads((AUTOMIC_FIXTURES / name).read_text())


def test_plan_exit_zero_when_all_equal() -> None:
    runner = CliRunner()
    base = "http://plan.test/ae/api/v1/100"
    with respx.mock(assert_all_called=False) as mock:
        _setup_mocks(mock)
        # Every declared object is present with an equivalent payload.
        mock.get(f"{base}/objects/WORK.DAYS").mock(
            return_value=httpx.Response(200, json=_load_fixture("calendar.json")),
        )
        mock.get(f"{base}/objects/ETL.CONFIG").mock(
            return_value=httpx.Response(200, json=_load_fixture("variable.json")),
        )
        mock.get(f"{base}/objects/ETL.EXTRACT").mock(
            return_value=httpx.Response(200, json=_load_fixture("job.json")),
        )
        mock.get(f"{base}/objects/ETL.LOAD").mock(
            return_value=httpx.Response(
                200,
                json={
                    "Name": "ETL.LOAD",
                    "Type": "JOBS",
                    "Folder": "/PROD/ETL",
                    "Title": "Load to warehouse",
                    "Host": "HOST.ETL.01",
                    "Login": "LOGIN.ETL.SVC",
                    "Script": "/opt/etl/load.sh",
                    "ScriptType": "OS",
                },
            ),
        )
        mock.get(f"{base}/objects/ETL.DAILY").mock(
            return_value=httpx.Response(200, json=_load_fixture("workflow.json")),
        )
        mock.get(f"{base}/objects/ETL.NIGHTLY").mock(
            return_value=httpx.Response(200, json=_load_fixture("schedule.json")),
        )

        result = runner.invoke(app, ["plan", str(FIXTURES / "valid")])

    assert result.exit_code == 0, result.output
    assert "up to date" in result.output.lower()


def test_plan_exit_two_on_pending_changes() -> None:
    runner = CliRunner()
    base = "http://plan.test/ae/api/v1/100"
    with respx.mock(assert_all_called=False) as mock:
        _setup_mocks(mock)
        # Everything 404 → every manifest becomes a "create".
        mock.get(f"{base}/objects/WORK.DAYS").mock(return_value=httpx.Response(404))
        mock.get(f"{base}/objects/ETL.CONFIG").mock(return_value=httpx.Response(404))
        mock.get(f"{base}/objects/ETL.EXTRACT").mock(return_value=httpx.Response(404))
        mock.get(f"{base}/objects/ETL.LOAD").mock(return_value=httpx.Response(404))
        mock.get(f"{base}/objects/ETL.DAILY").mock(return_value=httpx.Response(404))
        mock.get(f"{base}/objects/ETL.NIGHTLY").mock(return_value=httpx.Response(404))

        result = runner.invoke(app, ["plan", str(FIXTURES / "valid")])

    assert result.exit_code == 2, result.output
    assert "create" in result.output.lower()


def test_plan_exit_one_when_manifests_invalid() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["plan", str(FIXTURES / "invalid" / "duplicate.yaml")])
    assert result.exit_code == 1
    assert "duplicate" in result.output.lower() or "validation" in result.output.lower()


def test_plan_out_writes_valid_json(tmp_path: Path) -> None:
    runner = CliRunner()
    base = "http://plan.test/ae/api/v1/100"
    out_path = tmp_path / "plan.json"
    with respx.mock(assert_all_called=False) as mock:
        _setup_mocks(mock)
        mock.get(f"{base}/objects/WORK.DAYS").mock(return_value=httpx.Response(404))
        mock.get(f"{base}/objects/ETL.CONFIG").mock(return_value=httpx.Response(404))
        mock.get(f"{base}/objects/ETL.EXTRACT").mock(return_value=httpx.Response(404))
        mock.get(f"{base}/objects/ETL.LOAD").mock(return_value=httpx.Response(404))
        mock.get(f"{base}/objects/ETL.DAILY").mock(return_value=httpx.Response(404))
        mock.get(f"{base}/objects/ETL.NIGHTLY").mock(return_value=httpx.Response(404))

        result = runner.invoke(
            app, ["plan", str(FIXTURES / "valid"), "--out", str(out_path)],
        )

    assert result.exit_code == 2
    data = json.loads(out_path.read_text())
    assert set(data.keys()) == {"creates", "updates", "deletes", "noops"}
    assert len(data["creates"]) == 6


def test_plan_api_error_exits_one() -> None:
    runner = CliRunner()
    base = "http://plan.test/ae/api/v1/100"
    with respx.mock(assert_all_called=False) as mock:
        _setup_mocks(mock)
        mock.get(f"{base}/objects/WORK.DAYS").mock(
            return_value=httpx.Response(500, text="boom"),
        )
        result = runner.invoke(app, ["plan", str(FIXTURES / "valid")])

    assert result.exit_code == 1
    assert "api error" in result.output.lower() or "error" in result.output.lower()


def test_plan_target_filter_only_requests_target() -> None:
    runner = CliRunner()
    base = "http://plan.test/ae/api/v1/100"
    with respx.mock(assert_all_called=True) as mock:
        _setup_mocks(mock)
        # Only the --target=ETL.EXTRACT manifest should be queried.
        route = mock.get(f"{base}/objects/ETL.EXTRACT").mock(
            return_value=httpx.Response(200, json=_load_fixture("job.json")),
        )

        result = runner.invoke(
            app, ["plan", str(FIXTURES / "valid"), "--target", "ETL.EXTRACT"],
        )

    assert result.exit_code == 0, result.output
    assert route.called
    assert route.call_count == 1


def test_plan_assert_from_constant_auth_path() -> None:
    # Guards against accidental rename of the auth path constant during refactor.
    assert _AUTH_PATH == "/authenticate"
