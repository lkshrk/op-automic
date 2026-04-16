"""Tests for the applier — two-pass upsert, idempotency, concurrent-edit abort."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from op_aromic.client.api import AutomicAPI
from op_aromic.client.http import _AUTH_PATH, AutomicClient
from op_aromic.config.settings import AutomicSettings
from op_aromic.engine.applier import (
    ApplyResult,
    FailedApply,
    SuccessfulApply,
    apply,
)
from op_aromic.engine.dependency import build_graph
from op_aromic.engine.loader import LoadedManifest
from op_aromic.engine.planner import build_plan
from op_aromic.models.base import Manifest


def _settings() -> AutomicSettings:
    return AutomicSettings(
        url="http://apply.test/ae/api/v1",
        client_id=100,
        user="U",
        department="D",
        password="pw",
        verify_ssl=False,
        max_retries=0,
    )


def _mock_auth(mock: respx.MockRouter, settings: AutomicSettings) -> None:
    mock.post(f"{settings.url}{_AUTH_PATH}").mock(
        return_value=httpx.Response(200, json={"token": "t", "expires_in": 3600}),
    )


def _loaded_job(name: str, host: str = "h") -> LoadedManifest:
    manifest = Manifest.model_validate(
        {
            "apiVersion": "aromic.io/v1",
            "kind": "Job",
            "metadata": {"name": name, "folder": "/PROD"},
            "spec": {"host": host, "login": "L", "script": "s"},
        },
    )
    return LoadedManifest(source_path=Path(f"{name}.yaml"), doc_index=0, manifest=manifest)


def _loaded_workflow(name: str, task_refs: list[str]) -> LoadedManifest:
    tasks = [
        {"name": f"STEP_{i}", "ref": {"kind": "Job", "name": tr}}
        for i, tr in enumerate(task_refs)
    ]
    manifest = Manifest.model_validate(
        {
            "apiVersion": "aromic.io/v1",
            "kind": "Workflow",
            "metadata": {"name": name, "folder": "/PROD"},
            "spec": {"tasks": tasks},
        },
    )
    return LoadedManifest(source_path=Path(f"{name}.yaml"), doc_index=0, manifest=manifest)


def _automic_job(name: str, host: str = "h") -> dict[str, Any]:
    return {
        "Name": name,
        "Type": "JOBS",
        "Folder": "/PROD",
        "Host": host,
        "Login": "L",
        "Script": "s",
        "ScriptType": "OS",
    }


def _automic_workflow(name: str, task_refs: list[str]) -> dict[str, Any]:
    return {
        "Name": name,
        "Type": "JOBP",
        "Folder": "/PROD",
        "Title": "",
        "Tasks": [
            {"Name": f"STEP_{i}", "Ref": {"Kind": "Job", "Name": tr}, "After": []}
            for i, tr in enumerate(task_refs)
        ],
    }


def test_apply_creates_new_job() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded_job("NEW.JOB")]
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/NEW.JOB").mock(return_value=httpx.Response(404))
        post_route = mock.post(f"{base}/objects").mock(
            return_value=httpx.Response(201, json={"Name": "NEW.JOB"}),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(loaded, api)
            graph = build_graph(loaded)
            result = apply(plan, client, graph)
    assert isinstance(result, ApplyResult)
    assert result.status == "success"
    assert post_route.called
    assert len(result.successes) == 1
    assert result.successes[0].action == "create"


def test_apply_updates_existing_job() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded_job("X", "NEW")]
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(200, json=_automic_job("X", "OLD")),
        )
        put_route = mock.put(f"{base}/objects/X").mock(
            return_value=httpx.Response(200, json=_automic_job("X", "NEW")),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(loaded, api)
            graph = build_graph(loaded)
            result = apply(plan, client, graph)
    assert put_route.called
    assert result.status == "success"


def test_apply_two_pass_workflow_wires_refs() -> None:
    # Workflow referencing a Job → pass 1 creates ref-stripped, pass 2
    # wires the refs. Observed as two PUTs on the workflow.
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded_job("J1"), _loaded_workflow("WF", ["J1"])]

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        # Both objects absent on the server → creates.
        mock.get(f"{base}/objects/J1").mock(return_value=httpx.Response(404))
        mock.get(f"{base}/objects/WF").mock(return_value=httpx.Response(404))
        post_route = mock.post(f"{base}/objects").mock(
            return_value=httpx.Response(201, json={"Name": "X"}),
        )
        put_route = mock.put(f"{base}/objects/WF").mock(
            return_value=httpx.Response(200, json={"Name": "WF"}),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(loaded, api)
            graph = build_graph(loaded)
            result = apply(plan, client, graph)
    # Pass 1: POST both objects (workflow with empty Tasks).
    assert post_route.call_count == 2
    # Pass 2: only the workflow carries refs → PUT once to wire them.
    assert put_route.call_count == 1
    assert result.status == "success"


def test_apply_dry_run_makes_no_writes() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded_job("X", "NEW")]
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(200, json=_automic_job("X", "OLD")),
        )
        # Writes would explode if attempted: register strict routes.
        put_route = mock.put(f"{base}/objects/X").mock(
            return_value=httpx.Response(500, text="should not be called"),
        )
        post_route = mock.post(f"{base}/objects").mock(
            return_value=httpx.Response(500, text="should not be called"),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(loaded, api)
            graph = build_graph(loaded)
            result = apply(plan, client, graph, dry_run=True)
    assert not put_route.called
    assert not post_route.called
    assert result.status == "success"


def test_apply_partial_failure_reports_remaining_as_skipped() -> None:
    # Two creates; first POST fails → second is skipped, status=partial.
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded_job("A"), _loaded_job("B")]
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/A").mock(return_value=httpx.Response(404))
        mock.get(f"{base}/objects/B").mock(return_value=httpx.Response(404))
        # Both POSTs hit the same path; first fails, second would succeed
        # but we expect it to be skipped. side_effect lets us discriminate.
        calls = {"n": 0}

        def post_responder(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(500, text="boom")
            return httpx.Response(201, json={})

        mock.post(f"{base}/objects").mock(side_effect=post_responder)
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(loaded, api)
            graph = build_graph(loaded)
            result = apply(plan, client, graph)
    assert result.status == "partial"
    assert len(result.failures) == 1
    assert len(result.skipped) == 1


def test_apply_twice_is_noop_the_second_time() -> None:
    # First apply creates; second apply sees the server in the desired
    # state and does nothing.
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded_job("X", "h")]

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        # Track what the "server" has.
        state: dict[str, dict[str, Any] | None] = {"X": None}

        def get_responder(request: httpx.Request) -> httpx.Response:
            obj = state["X"]
            if obj is None:
                return httpx.Response(404)
            return httpx.Response(200, json=obj)

        def post_responder(request: httpx.Request) -> httpx.Response:
            # Store the submitted payload so subsequent GETs see it.
            import json
            payload = json.loads(request.content)
            state["X"] = payload
            return httpx.Response(201, json=payload)

        def put_responder(request: httpx.Request) -> httpx.Response:
            import json
            payload = json.loads(request.content)
            state["X"] = payload
            return httpx.Response(200, json=payload)

        mock.get(f"{base}/objects/X").mock(side_effect=get_responder)
        post_route = mock.post(f"{base}/objects").mock(side_effect=post_responder)
        put_route = mock.put(f"{base}/objects/X").mock(side_effect=put_responder)

        with AutomicClient(settings) as client:
            api = AutomicAPI(client)

            # First apply: creates.
            plan1 = build_plan(loaded, api)
            graph = build_graph(loaded)
            result1 = apply(plan1, client, graph)
            assert result1.status == "success"
            assert len(result1.successes) == 1
            first_post_count = post_route.call_count
            first_put_count = put_route.call_count

            # Second apply: plan is empty (server has it) → no writes.
            plan2 = build_plan(loaded, api)
            assert not plan2.has_changes, (
                f"idempotency broken: second plan still sees changes {plan2.creates=} "
                f"{plan2.updates=}"
            )
            result2 = apply(plan2, client, graph)

    # Second apply made zero writes.
    assert post_route.call_count == first_post_count
    assert put_route.call_count == first_put_count
    assert result2.status == "success"
    assert result2.successes == []


def test_apply_concurrent_edit_aborts_without_force() -> None:
    # Plan time captures marker T1; between plan and apply another client
    # bumped LastModified to T2. The applier's pre-write refetch sees T2
    # and refuses the write.
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded_job("X", "NEW")]

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        # Every GET now returns T2 — the applier's refetch sees the drift
        # against the caller-provided T1 marker.
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(
                200,
                json={**_automic_job("X", "NEWER"), "LastModified": "T2"},
            ),
        )
        put_route = mock.put(f"{base}/objects/X").mock(
            return_value=httpx.Response(200, json={}),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(loaded, api)
            graph = build_graph(loaded)
            result = apply(
                plan,
                client,
                graph,
                force=False,
                plan_markers={("Job", "X"): "T1"},
            )
    assert not put_route.called
    assert result.status == "partial"
    assert len(result.failures) == 1
    assert isinstance(result.failures[0], FailedApply)
    assert "concurrent" in result.failures[0].reason.lower()


def test_apply_force_overrides_concurrent_edit_detection() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded_job("X", "NEW")]

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(
                200,
                json={**_automic_job("X", "NEWER"), "LastModified": "T2"},
            ),
        )
        put_route = mock.put(f"{base}/objects/X").mock(
            return_value=httpx.Response(200, json={}),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(loaded, api)
            graph = build_graph(loaded)
            result = apply(
                plan,
                client,
                graph,
                force=True,
                plan_markers={("Job", "X"): "T1"},
            )
    assert put_route.called
    assert result.status == "success"


def test_apply_noop_plan_returns_success_with_no_work() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded_job("X", "h")]
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(200, json=_automic_job("X", "h")),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(loaded, api)
            graph = build_graph(loaded)
            result = apply(plan, client, graph)
    assert result.status == "success"
    assert result.successes == []
    assert result.failures == []


def test_apply_progress_callback_is_invoked() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded_job("A"), _loaded_job("B")]
    events: list[tuple[str, str]] = []

    def on_progress(event: str, ref_name: str) -> None:
        events.append((event, ref_name))

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/A").mock(return_value=httpx.Response(404))
        mock.get(f"{base}/objects/B").mock(return_value=httpx.Response(404))
        mock.post(f"{base}/objects").mock(
            return_value=httpx.Response(201, json={}),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(loaded, api)
            graph = build_graph(loaded)
            apply(plan, client, graph, on_progress=on_progress)
    # Exact event stream is implementation-detail; just verify we saw
    # both object names flow through.
    seen_names = {name for _, name in events}
    assert "A" in seen_names
    assert "B" in seen_names


def test_apply_delete_calls_delete_endpoint() -> None:
    # Plan with a prune-delete; applier should DELETE on the target.
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"

    from op_aromic.engine.differ import ObjectDiff
    from op_aromic.engine.planner import Plan

    delete_diff = ObjectDiff(
        action="delete",
        kind="Job",
        name="ORPHAN",
        folder="/PROD",
        desired=None,
        actual={"kind": "Job", "name": "ORPHAN", "folder": "/PROD"},
    )
    plan = Plan(creates=[], updates=[], deletes=[delete_diff], noops=[])

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        # Pre-write re-fetch.
        mock.get(f"{base}/objects/ORPHAN").mock(
            return_value=httpx.Response(200, json={"Name": "ORPHAN"}),
        )
        delete_route = mock.delete(f"{base}/objects/ORPHAN").mock(
            return_value=httpx.Response(204),
        )
        graph = build_graph([])
        with AutomicClient(settings) as client:
            result = apply(plan, client, graph)
    assert delete_route.called
    assert result.status == "success"
    assert len(result.successes) == 1
    assert result.successes[0].action == "delete"


def test_successful_apply_is_frozen() -> None:
    # SuccessfulApply must be a frozen dataclass per the project style.
    import dataclasses

    success = SuccessfulApply(kind="Job", name="X", action="create")
    with pytest.raises(dataclasses.FrozenInstanceError):
        success.name = "Y"  # type: ignore[misc]
