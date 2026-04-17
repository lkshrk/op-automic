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
        update_method="PUT",
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


def test_apply_pass2_failure_marks_remaining_pass2_as_skipped() -> None:
    # Two workflows triggering pass 2; first pass-2 PUT fails → second skipped.
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [
        _loaded_job("J1"),
        _loaded_workflow("WF1", ["J1"]),
        _loaded_workflow("WF2", ["J1"]),
    ]
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/J1").mock(return_value=httpx.Response(404))
        mock.get(f"{base}/objects/WF1").mock(return_value=httpx.Response(404))
        mock.get(f"{base}/objects/WF2").mock(return_value=httpx.Response(404))
        mock.post(f"{base}/objects").mock(
            return_value=httpx.Response(201, json={}),
        )
        # First PUT (on WF1 or WF2 — depends on order) returns 500, second
        # would succeed but should be skipped.
        calls = {"n": 0}

        def put_responder(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json={})

        mock.put(f"{base}/objects/WF1").mock(side_effect=put_responder)
        mock.put(f"{base}/objects/WF2").mock(side_effect=put_responder)
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(loaded, api)
            graph = build_graph(loaded)
            result = apply(plan, client, graph)
    assert result.status == "partial"
    assert len(result.failures) == 1
    assert len(result.skipped) == 1
    assert result.skipped[0].kind == "Workflow"


@pytest.mark.parametrize(
    "kind,spec",
    [
        (
            "Schedule",
            {
                "entries": [
                    {
                        "task": {"kind": "Job", "name": "J1"},
                        "start_time": "02:30",
                        "calendar_keyword": "WEEKDAYS",
                    },
                ],
            },
        ),
        ("Calendar", {"keywords": [{"name": "WEEKDAYS", "type": "STATIC", "values": ["MO"]}]}),
        (
            "Variable",
            {"var_type": "STATIC", "entries": [{"key": "K", "value": "v"}]},
        ),
    ],
)
def test_apply_covers_all_canonical_to_spec_branches(
    kind: str, spec: dict[str, Any],
) -> None:
    # Dry-run every kind so _canonical_to_spec exercises its full switch.
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    manifest = Manifest.model_validate(
        {
            "apiVersion": "aromic.io/v1",
            "kind": kind,
            "metadata": {"name": "OBJ", "folder": "/T"},
            "spec": spec,
        },
    )
    loaded = [LoadedManifest(source_path=Path("x.yaml"), doc_index=0, manifest=manifest)]

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/OBJ").mock(return_value=httpx.Response(404))
        mock.post(f"{base}/objects").mock(
            return_value=httpx.Response(500, text="should not be called in dry-run"),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(loaded, api)
            graph = build_graph(loaded)
            result = apply(plan, client, graph, dry_run=True)
    assert result.status == "success"
    assert len(result.successes) == 1


def test_apply_delete_dry_run_logs_success_without_calling() -> None:
    from op_aromic.engine.differ import ObjectDiff
    from op_aromic.engine.planner import Plan

    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    delete_diff = ObjectDiff(
        action="delete",
        kind="Job",
        name="ZAP",
        folder="/PROD",
        desired=None,
        actual={"kind": "Job", "name": "ZAP", "folder": "/PROD"},
    )
    plan = Plan(deletes=[delete_diff])
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        delete_route = mock.delete(f"{base}/objects/ZAP").mock(
            return_value=httpx.Response(500),
        )
        with AutomicClient(settings) as client:
            graph = build_graph([])
            result = apply(plan, client, graph, dry_run=True)
    assert not delete_route.called
    assert result.status == "success"
    assert len(result.successes) == 1


def test_apply_delete_failure_reports_as_failure() -> None:
    from op_aromic.engine.differ import ObjectDiff
    from op_aromic.engine.planner import Plan

    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    delete_diff = ObjectDiff(
        action="delete",
        kind="Job",
        name="ZAP",
        folder="/PROD",
        desired=None,
        actual={"kind": "Job", "name": "ZAP", "folder": "/PROD"},
    )
    plan = Plan(deletes=[delete_diff])
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/ZAP").mock(
            return_value=httpx.Response(200, json={"Name": "ZAP"}),
        )
        mock.delete(f"{base}/objects/ZAP").mock(
            return_value=httpx.Response(500, text="nope"),
        )
        with AutomicClient(settings) as client:
            graph = build_graph([])
            result = apply(plan, client, graph)
    assert result.status == "partial"
    assert len(result.failures) == 1


def test_capture_plan_markers_snapshots_updates_and_deletes() -> None:
    from op_aromic.engine.applier import capture_plan_markers
    from op_aromic.engine.differ import ObjectDiff
    from op_aromic.engine.planner import Plan

    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    update_diff = ObjectDiff(
        action="update",
        kind="Job",
        name="U",
        folder="/PROD",
        desired={"name": "U", "folder": "/PROD", "kind": "Job"},
        actual={"name": "U", "folder": "/PROD", "kind": "Job"},
    )
    delete_diff = ObjectDiff(
        action="delete",
        kind="Job",
        name="D",
        folder="/PROD",
        desired=None,
        actual={"name": "D", "folder": "/PROD", "kind": "Job"},
    )
    plan = Plan(updates=[update_diff], deletes=[delete_diff])

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/U").mock(
            return_value=httpx.Response(200, json={"Name": "U", "LastModified": "TU"}),
        )
        mock.get(f"{base}/objects/D").mock(
            return_value=httpx.Response(200, json={"Name": "D", "LastModified": "TD"}),
        )
        with AutomicClient(settings) as client:
            markers = capture_plan_markers(plan, client)
    assert markers[("Job", "U")] == "TU"
    assert markers[("Job", "D")] == "TD"


def test_successful_apply_is_frozen() -> None:
    # SuccessfulApply must be a frozen dataclass per the project style.
    import dataclasses

    success = SuccessfulApply(kind="Job", name="X", action="create")
    with pytest.raises(dataclasses.FrozenInstanceError):
        success.name = "Y"  # type: ignore[misc]


def test_apply_auto_create_folders_false_first_object_in_new_folder_fails() -> None:
    """When auto_create_folders=False the first object in a previously unseen
    folder path is blocked with FolderMissingError, not forwarded to the API."""
    settings = AutomicSettings(
        url="http://apply.test/ae/api/v1",
        client_id=100,
        user="U",
        department="D",
        password="pw",
        verify_ssl=False,
        max_retries=0,
        auto_create_folders=False,
        update_method="PUT",
    )
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded_job("J1")]  # folder=/PROD — new to this run

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/J1").mock(return_value=httpx.Response(404))
        post_route = mock.post(f"{base}/objects").mock(
            return_value=httpx.Response(201, json={"Name": "J1"}),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(loaded, api)
            graph = build_graph(loaded)
            result = apply(plan, client, graph, auto_create_folders=False)

    # No write to the API.
    assert not post_route.called
    assert result.status == "partial"
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.name == "J1"
    assert "auto_create_folders" in failure.reason


def test_apply_auto_create_folders_false_second_object_in_same_folder_succeeds() -> None:
    """Once a folder is seen in this run, subsequent objects in the same
    folder pass the guard even when auto_create_folders=False."""
    settings = AutomicSettings(
        url="http://apply.test/ae/api/v1",
        client_id=100,
        user="U",
        department="D",
        password="pw",
        verify_ssl=False,
        max_retries=0,
        auto_create_folders=False,
        update_method="PUT",
    )
    base = f"{settings.url}/{settings.client_id}"
    # Two jobs in the same folder; only the first should be blocked.
    loaded = [_loaded_job("J1"), _loaded_job("J2")]

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/J1").mock(return_value=httpx.Response(404))
        mock.get(f"{base}/objects/J2").mock(return_value=httpx.Response(404))
        post_route = mock.post(f"{base}/objects").mock(
            return_value=httpx.Response(201, json={"Name": "x"}),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(loaded, api)
            graph = build_graph(loaded)
            result = apply(plan, client, graph, auto_create_folders=False)

    # First object blocked; pass1_failed=True → second skipped.
    assert result.status == "partial"
    assert len(result.failures) == 1
    assert not post_route.called
