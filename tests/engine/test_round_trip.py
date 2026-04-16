"""Phase 4 quality bar: export → validate → plan ⇒ empty changeset.

For every fixture under :file:`tests/fixtures/automic/`, stand up respx to
serve that fixture from both ``list_objects_typed`` and ``get_object_typed``,
run the exporter to a temp dir, load the YAML back through Phase 1's
loader and validator, then build a Phase 2 plan against the same respx
state. The plan must report zero creates, updates, and deletes.

Failures indicate a round-trip divergence; document the exact field in
``docs/ISSUES.md`` and xfail the specific kind (see the
``pytest.param(..., marks=pytest.mark.xfail(...))`` pattern at the bottom
of this module).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from op_aromic.client.api import AutomicAPI
from op_aromic.client.http import _AUTH_PATH, AutomicClient
from op_aromic.config.settings import AutomicSettings
from op_aromic.engine.exporter import export
from op_aromic.engine.loader import load_manifests
from op_aromic.engine.planner import build_plan
from op_aromic.engine.validator import validate_manifests

AUTOMIC_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "automic"


def _settings() -> AutomicSettings:
    return AutomicSettings(
        url="http://roundtrip.test/ae/api/v1",
        client_id=100,
        user="U",
        department="D",
        password="P",
        verify_ssl=False,
        max_retries=0,
    )


def _mock_auth(mock: respx.MockRouter, settings: AutomicSettings) -> None:
    mock.post(f"{settings.url}{_AUTH_PATH}").mock(
        return_value=httpx.Response(200, json={"token": "t", "expires_in": 3600}),
    )


# Map fixture filename → (manifest kind, Automic type string). Keep in sync
# with client.api._KIND_TO_AUTOMIC_TYPE; if a future new kind lands the
# fixture shortlist stays explicit.
_FIXTURES: tuple[tuple[str, str, str], ...] = (
    ("calendar.json", "Calendar", "CALE"),
    ("variable.json", "Variable", "VARA"),
    ("job.json", "Job", "JOBS"),
    ("workflow.json", "Workflow", "JOBP"),
    ("schedule.json", "Schedule", "JSCH"),
)

# Leaf kinds — no outbound ObjectRefs — are the ones we can round-trip in
# isolation. Workflow and Schedule reference other objects, so per-kind
# round-trip would fail the validator's cross-reference rule through no
# fault of the exporter. They ride along in the full-set round-trip instead.
_LEAF_KINDS: frozenset[str] = frozenset({"Calendar", "Variable", "Job"})


def _install_fixture_set(
    mock: respx.MockRouter,
    base: str,
    by_type: dict[str, list[dict[str, Any]]],
) -> None:
    """Respond to list (typed) and get-by-name probes for every fixture."""

    def list_responder(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        typ = params.get("type")
        data = by_type.get(typ, []) if typ else []
        folder = params.get("folder")
        if folder:
            data = [d for d in data if d.get("Folder") == folder]
        return httpx.Response(200, json={"data": data})

    mock.get(f"{base}/objects").mock(side_effect=list_responder)
    for objs in by_type.values():
        for obj in objs:
            mock.get(f"{base}/objects/{obj['Name']}").mock(
                return_value=httpx.Response(200, json=obj),
            )


@pytest.mark.parametrize(
    ("fixture_name", "kind", "automic_type"),
    [
        pytest.param(*f, id=f[1])
        for f in _FIXTURES
        if f[1] in _LEAF_KINDS
    ],
)
def test_round_trip_per_kind_for_leaf_kinds(
    tmp_path: Path,
    fixture_name: str,
    kind: str,
    automic_type: str,
) -> None:
    """Leaf kinds (no outbound refs) must round-trip individually.

    The tighter harness here is the safety net for each kind's inverse
    serializer: if any field diverges between export and plan, the
    culprit is confined to this kind alone.
    """
    payload = json.loads((AUTOMIC_FIXTURES / fixture_name).read_text())
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        _install_fixture_set(mock, base, {automic_type: [payload]})

        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            result = export(
                api,
                tmp_path,
                kinds=[kind],
                layout="by-kind",
                overwrite=True,
            )

        assert result.objects_exported == 1, result

        loaded = load_manifests(tmp_path)
        assert len(loaded) == 1
        report = validate_manifests(loaded)
        assert not report.errors, report.errors

        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(loaded, api)

    assert plan.creates == [], f"unexpected creates: {plan.creates}"
    assert plan.updates == [], f"unexpected updates: {plan.updates}"
    assert plan.deletes == [], f"unexpected deletes: {plan.deletes}"
    assert plan.noops, "planner produced nothing at all; fixture pipeline is broken"


def test_round_trip_full_fixture_set_is_noop(tmp_path: Path) -> None:
    """The flagship round-trip: every kind exported together produces a noop plan.

    This is the Phase 4 quality bar for end-to-end adoption. Each fixture
    carries a minimal payload for its kind; collectively they form a
    self-referential set (Workflow → Jobs, Schedule → Workflow) so the
    validator's reference rule is satisfied, and every manifest has an
    Automic counterpart for the planner to diff against.
    """
    # ``ETL.LOAD`` is referenced by the Workflow fixture but has no JSON
    # file of its own; synthesise it inline so cross-kind refs resolve.
    etl_load = {
        "Name": "ETL.LOAD",
        "Type": "JOBS",
        "Folder": "/PROD/ETL",
        "Title": "Load to warehouse",
        "Host": "HOST.ETL.01",
        "Login": "LOGIN.ETL.SVC",
        "Script": "/opt/etl/load.sh",
        "ScriptType": "OS",
    }
    by_type: dict[str, list[dict[str, Any]]] = {
        "CALE": [json.loads((AUTOMIC_FIXTURES / "calendar.json").read_text())],
        "VARA": [json.loads((AUTOMIC_FIXTURES / "variable.json").read_text())],
        "JOBS": [
            json.loads((AUTOMIC_FIXTURES / "job.json").read_text()),
            etl_load,
        ],
        "JOBP": [json.loads((AUTOMIC_FIXTURES / "workflow.json").read_text())],
        "JSCH": [json.loads((AUTOMIC_FIXTURES / "schedule.json").read_text())],
    }

    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        _install_fixture_set(mock, base, by_type)

        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            result = export(api, tmp_path, layout="by-kind", overwrite=True)

        assert result.objects_exported == 6, result

        loaded = load_manifests(tmp_path)
        assert len(loaded) == 6
        report = validate_manifests(loaded)
        assert not report.errors, report.errors

        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(loaded, api)

    assert plan.creates == [], f"unexpected creates: {plan.creates}"
    assert plan.updates == [], f"unexpected updates: {plan.updates}"
    assert plan.deletes == [], f"unexpected deletes: {plan.deletes}"
    assert plan.noops, "planner produced nothing; fixture pipeline is broken"
