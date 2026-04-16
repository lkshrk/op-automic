"""Tests for build_plan."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import respx

from op_aromic.client.api import AutomicAPI
from op_aromic.client.http import _AUTH_PATH, AutomicClient
from op_aromic.config.settings import AutomicSettings
from op_aromic.engine.loader import LoadedManifest
from op_aromic.engine.planner import build_plan, build_plan_parallel
from op_aromic.models.base import Manifest


def _settings() -> AutomicSettings:
    return AutomicSettings(
        url="http://plan.test/ae/api/v1",
        client_id=100,
        user="USER",
        department="DEPT",
        password="pw",
        verify_ssl=False,
        max_retries=0,
    )


def _mock_auth(mock: respx.MockRouter, settings: AutomicSettings) -> None:
    mock.post(f"{settings.url}{_AUTH_PATH}").mock(
        return_value=httpx.Response(200, json={"token": "t", "expires_in": 3600}),
    )


def _loaded_job(name: str, host: str) -> LoadedManifest:
    manifest = Manifest.model_validate(
        {
            "apiVersion": "aromic.io/v1",
            "kind": "Job",
            "metadata": {"name": name, "folder": "/PROD"},
            "spec": {"host": host, "login": "L", "script": "s"},
        },
    )
    return LoadedManifest(source_path=Path(f"{name}.yaml"), doc_index=0, manifest=manifest)


def _automic_job(name: str, host: str, managed: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "Name": name,
        "Type": "JOBS",
        "Folder": "/PROD",
        "Host": host,
        "Login": "L",
        "Script": "s",
        "ScriptType": "OS",
    }
    if managed:
        payload["Annotations"] = {"aromic.io/managed-by": "op-aromic"}
    return payload


def test_build_plan_create_when_not_on_server() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/NEW.JOB").mock(return_value=httpx.Response(404))
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan([_loaded_job("NEW.JOB", "h")], api)
    assert len(plan.creates) == 1
    assert plan.creates[0].action == "create"
    assert plan.total_changes == 1


def test_build_plan_noop_when_equal() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(200, json=_automic_job("X", "h")),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan([_loaded_job("X", "h")], api)
    assert plan.noops
    assert not plan.has_changes


def test_build_plan_update_when_host_differs() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(200, json=_automic_job("X", "OLD")),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan([_loaded_job("X", "NEW")], api)
    assert len(plan.updates) == 1
    assert any(c.path == "host" for c in plan.updates[0].changes)


def test_build_plan_target_filter() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/ONE").mock(return_value=httpx.Response(404))
        # Planner should only look up "ONE" when target="ONE".
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(
                [_loaded_job("ONE", "h"), _loaded_job("TWO", "h")],
                api,
                target="ONE",
            )
    assert len(plan.creates) == 1
    assert plan.creates[0].name == "ONE"


def test_build_plan_prune_adds_delete_for_managed_orphan() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    managed_orphan = _automic_job("ORPHAN", "h", managed=True)
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(200, json=_automic_job("X", "h")),
        )

        def list_responder(request: httpx.Request) -> httpx.Response:
            # Return one object only for the JOBS call; empty for every other kind.
            query = str(request.url)
            if "type=JOBS" in query:
                return httpx.Response(200, json={"data": [managed_orphan]})
            return httpx.Response(200, json={"data": []})

        mock.get(f"{base}/objects").mock(side_effect=list_responder)

        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan([_loaded_job("X", "h")], api, prune=True)

    assert len(plan.deletes) == 1
    assert plan.deletes[0].name == "ORPHAN"


def test_build_plan_prune_skips_unmanaged_orphan() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    unmanaged = _automic_job("FOREIGN", "h", managed=False)
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(200, json=_automic_job("X", "h")),
        )

        def list_responder(request: httpx.Request) -> httpx.Response:
            if "type=JOBS" in str(request.url):
                return httpx.Response(200, json={"data": [unmanaged]})
            return httpx.Response(200, json={"data": []})

        mock.get(f"{base}/objects").mock(side_effect=list_responder)
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan([_loaded_job("X", "h")], api, prune=True)

    assert plan.deletes == []


def test_build_plan_parallel_matches_sequential_output() -> None:
    # With the same mock, sequential and parallel planners must produce
    # equivalent Plans. Parallelism is a runtime optimisation — never a
    # behavioural change.
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    manifests = [_loaded_job(f"J{i}", "h") for i in range(5)]
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        for lm in manifests:
            name = lm.manifest.metadata.name
            mock.get(f"{base}/objects/{name}").mock(
                return_value=httpx.Response(200, json=_automic_job(name, "h")),
            )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            serial = build_plan(manifests, api)
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            parallel = build_plan_parallel(manifests, api, max_workers=4)

    assert [d.name for d in serial.noops] == [d.name for d in parallel.noops]
    assert serial.total_changes == parallel.total_changes


def test_build_plan_parallel_sequential_fallback() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(200, json=_automic_job("X", "h")),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            # max_workers=1 must delegate to build_plan; output shape identical.
            plan = build_plan_parallel([_loaded_job("X", "h")], api, max_workers=1)

    assert plan.noops
    assert not plan.has_changes


def test_build_plan_parallel_prune_detects_orphan() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    managed_orphan = _automic_job("ORPHAN", "h", managed=True)
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(200, json=_automic_job("X", "h")),
        )

        def list_responder(request: httpx.Request) -> httpx.Response:
            if "type=JOBS" in str(request.url):
                return httpx.Response(200, json={"data": [managed_orphan]})
            return httpx.Response(200, json={"data": []})

        mock.get(f"{base}/objects").mock(side_effect=list_responder)

        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan_parallel(
                [_loaded_job("X", "h")], api, max_workers=4, prune=True,
            )

    assert len(plan.deletes) == 1
    assert plan.deletes[0].name == "ORPHAN"
