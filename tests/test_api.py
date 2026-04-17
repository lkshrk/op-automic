"""Library-mode tests for :mod:`op_aromic.api`.

These exercise the façade end-to-end against respx-mocked Automic HTTP
and a real filesystem — they do NOT go through Typer. The goal is to
prove that third-party embedders can drive the full pipeline without
ever importing from ``op_aromic.cli``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

import op_aromic
from op_aromic import api
from op_aromic.client.http import _AUTH_PATH
from op_aromic.config.settings import AutomicSettings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> AutomicSettings:
    return AutomicSettings(
        url="http://api.test/ae/api/v1",
        client_id=100,
        user="U",
        department="D",
        password="pw",
        verify_ssl=False,
        max_retries=0,
        update_method="PUT",
    )


def _mock_auth(mock: respx.MockRouter, settings: AutomicSettings) -> None:
    mock.post(f"{settings.url}{_AUTH_PATH}").mock(
        return_value=httpx.Response(200, json={"token": "t", "expires_in": 3600}),
    )


def _job_yaml(name: str = "API.NEW", folder: str = "/API") -> str:
    return (
        "apiVersion: aromic.io/v1\n"
        "kind: Job\n"
        "metadata:\n"
        f"  name: {name}\n"
        f"  folder: {folder}\n"
        "spec:\n"
        "  host: h\n"
        "  login: L\n"
        "  script: s\n"
    )


# ---------------------------------------------------------------------------
# Surface checks
# ---------------------------------------------------------------------------


def test_public_api_exposes_expected_symbols() -> None:
    # Dual-path import: both the submodule and the package root must
    # expose the headline names. Breaking either breaks downstream docs.
    for name in (
        "load", "validate", "plan", "apply", "destroy", "export",
        "history", "rollback", "rollback_plan", "open_client",
        "compute_revision", "Manifest", "Status", "Plan", "ApplyResult",
    ):
        assert hasattr(api, name), f"op_aromic.api missing {name!r}"
        assert hasattr(op_aromic, name), f"op_aromic missing {name!r}"


def test_api_and_root_share_symbols() -> None:
    # The root-level re-exports must be the same objects as the api
    # module, so ``from op_aromic import load`` and ``from op_aromic.api
    # import load`` are interchangeable.
    assert op_aromic.load is api.load
    assert op_aromic.apply is api.apply
    assert op_aromic.Manifest is api.Manifest


# ---------------------------------------------------------------------------
# Load + validate
# ---------------------------------------------------------------------------


def test_load_reads_manifest_dir(tmp_path: Path) -> None:
    (tmp_path / "j.yaml").write_text(_job_yaml())
    loaded = api.load(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].manifest.kind == "Job"
    assert loaded[0].manifest.metadata.name == "API.NEW"


def test_validate_returns_report(tmp_path: Path) -> None:
    (tmp_path / "j.yaml").write_text(_job_yaml())
    loaded = api.load(tmp_path)
    report = api.validate(loaded)
    assert report.errors == []


def test_validate_strict_raises_on_errors(tmp_path: Path) -> None:
    # Two manifests with the same (kind, name) → duplicate error.
    (tmp_path / "a.yaml").write_text(_job_yaml(name="DUP"))
    (tmp_path / "b.yaml").write_text(_job_yaml(name="DUP"))
    loaded = api.load(tmp_path)
    with pytest.raises(api.ValidationFailed) as excinfo:
        api.validate(loaded, strict=True)
    # Report is attached so callers can introspect without re-running.
    assert excinfo.value.report.errors


# ---------------------------------------------------------------------------
# Plan + apply (no CLI)
# ---------------------------------------------------------------------------


def test_plan_and_apply_happy_path(
    tmp_path: Path, settings: AutomicSettings,
) -> None:
    (tmp_path / "j.yaml").write_text(_job_yaml())
    loaded = api.load(tmp_path)
    base = f"{settings.url}/{settings.client_id}"

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/API.NEW").mock(return_value=httpx.Response(404))
        post_route = mock.post(f"{base}/objects").mock(
            return_value=httpx.Response(201, json={"Name": "API.NEW"}),
        )
        with api.open_client(settings) as client:
            plan = api.plan(loaded, client=client)
            assert len(plan.creates) == 1
            # Same client drives apply; ledger goes into tmp_path/revisions.
            result = api.apply(
                plan,
                client=client,
                manifests=loaded,
                ledger_dir=tmp_path / "revisions",
            )
        assert post_route.called
        assert result.status == "success"
        # Ledger actually written through the facade.
        ledger_file = tmp_path / "revisions" / "Job" / "API.NEW.jsonl"
        assert ledger_file.exists()


def test_plan_and_apply_combined_helper(
    tmp_path: Path, settings: AutomicSettings,
) -> None:
    (tmp_path / "j.yaml").write_text(_job_yaml(name="COMBO"))
    loaded = api.load(tmp_path)
    base = f"{settings.url}/{settings.client_id}"

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/COMBO").mock(return_value=httpx.Response(404))
        mock.post(f"{base}/objects").mock(
            return_value=httpx.Response(201, json={"Name": "COMBO"}),
        )
        plan, result = api.plan_and_apply(
            loaded,
            settings=settings,
            ledger_dir=tmp_path / "revisions",
        )
    assert len(plan.creates) == 1
    assert result.status == "success"


def test_apply_dry_run_skips_ledger_writes(
    tmp_path: Path, settings: AutomicSettings,
) -> None:
    (tmp_path / "j.yaml").write_text(_job_yaml(name="DRY"))
    loaded = api.load(tmp_path)
    base = f"{settings.url}/{settings.client_id}"

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/DRY").mock(return_value=httpx.Response(404))
        with api.open_client(settings) as client:
            plan = api.plan(loaded, client=client)
            result = api.apply(
                plan,
                client=client,
                manifests=loaded,
                ledger_dir=tmp_path / "revisions",
                dry_run=True,
            )
    assert result.status == "success"
    # Dry run reports success but never writes ledger/HTTP mutations.
    assert not (tmp_path / "revisions").exists()


# ---------------------------------------------------------------------------
# History + rollback (no network)
# ---------------------------------------------------------------------------


def test_history_and_rollback_plan_without_network(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "revisions"
    manifests_root = tmp_path / "manifests"
    manifests_root.mkdir()
    (manifests_root / "j.yaml").write_text(_job_yaml(name="R.JOB"))

    # Manually inject a ledger row carrying a synthetic gitSha.
    target = ledger_dir / "Job" / "R.JOB.jsonl"
    target.parent.mkdir(parents=True)
    row: dict[str, Any] = {
        "ts": "2026-04-17T12:00:00Z",
        "action": "create",
        "revision": "sha256:" + "a" * 64,
        "gitSha": "deadbee",
        "automicVersionBefore": None,
        "automicVersionAfter": None,
        "by": "tester",
    }
    import json

    target.write_text(json.dumps(row) + "\n")

    rows = api.history("Job", "R.JOB", ledger_dir=ledger_dir)
    assert len(rows) == 1
    assert rows[0]["revision"].startswith("sha256:")

    plan = api.rollback_plan(
        "Job",
        "R.JOB",
        to="a" * 8,  # short form
        manifests_root=manifests_root,
        ledger_dir=ledger_dir,
    )
    assert plan.git_sha == "deadbee"
    assert plan.manifest_file == manifests_root / "j.yaml"
    assert plan.command[:3] == ["git", "checkout", "deadbee"]


def test_rollback_plan_raises_when_revision_unknown(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "revisions"
    manifests_root = tmp_path / "manifests"
    manifests_root.mkdir()
    (manifests_root / "j.yaml").write_text(_job_yaml(name="X"))
    # Seed an unrelated ledger row.
    api.ledger_append(
        kind="Job",
        name="X",
        action="create",
        revision="sha256:" + "c" * 64,
        root=ledger_dir,
    )
    with pytest.raises(api.RollbackUnresolved):
        api.rollback_plan(
            "Job",
            "X",
            to="deadbeef",
            manifests_root=manifests_root,
            ledger_dir=ledger_dir,
        )


# ---------------------------------------------------------------------------
# compute_revision re-export smoke
# ---------------------------------------------------------------------------


def test_compute_revision_roundtrips_through_api() -> None:
    m = api.Manifest.model_validate(
        {
            "apiVersion": "aromic.io/v1",
            "kind": "Job",
            "metadata": {"name": "R", "folder": "/X"},
            "spec": {"host": "h", "login": "L", "script": "s"},
        },
    )
    rev = api.compute_revision(m)
    assert rev.startswith("sha256:")
    assert api.is_revision(rev)
