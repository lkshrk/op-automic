"""Tests for the destroyer — reverse-order deletes, managed-only guard, dry_run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import respx

from op_aromic.client.http import _AUTH_PATH, AutomicClient
from op_aromic.config.settings import AutomicSettings
from op_aromic.engine.dependency import build_graph
from op_aromic.engine.destroyer import DestroyResult, destroy
from op_aromic.engine.loader import LoadedManifest
from op_aromic.models.base import Manifest


def _settings() -> AutomicSettings:
    return AutomicSettings(
        url="http://destroy.test/ae/api/v1",
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


def _loaded(kind: str, name: str, spec: dict[str, Any] | None = None) -> LoadedManifest:
    manifest = Manifest.model_validate(
        {
            "apiVersion": "aromic.io/v1",
            "kind": kind,
            "metadata": {"name": name, "folder": "/T"},
            "spec": spec or {},
        },
    )
    return LoadedManifest(source_path=Path(f"{name}.yaml"), doc_index=0, manifest=manifest)


def _managed_payload(kind: str, name: str) -> dict[str, Any]:
    return {
        "Name": name,
        "Type": kind,
        "Folder": "/T",
        "Annotations": {"aromic.io/managed-by": "op-aromic"},
    }


def _unmanaged_payload(kind: str, name: str) -> dict[str, Any]:
    return {"Name": name, "Type": kind, "Folder": "/T"}


def test_destroy_reverse_order_workflow_before_job() -> None:
    # Loaded: Job + Workflow. Destroy must DELETE Workflow first, Job second.
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [
        _loaded("Job", "J1"),
        _loaded("Workflow", "WF", {"tasks": []}),
    ]
    call_order: list[str] = []

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        # Pre-delete GET: both return managed payloads.
        mock.get(f"{base}/objects/J1").mock(
            return_value=httpx.Response(200, json=_managed_payload("JOBS", "J1")),
        )
        mock.get(f"{base}/objects/WF").mock(
            return_value=httpx.Response(200, json=_managed_payload("JOBP", "WF")),
        )

        def delete_responder(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            # Record only deletions of interest.
            for name in ("J1", "WF"):
                if path.endswith(f"/objects/{name}"):
                    call_order.append(name)
                    break
            return httpx.Response(204)

        mock.delete(f"{base}/objects/J1").mock(side_effect=delete_responder)
        mock.delete(f"{base}/objects/WF").mock(side_effect=delete_responder)

        with AutomicClient(settings) as client:
            graph = build_graph(loaded)
            result = destroy(loaded, client, graph, only_managed=True)
    assert result.status == "success"
    # Workflow first, then Job — reverse dependency order.
    assert call_order == ["WF", "J1"]


def test_destroy_refuses_unmanaged_by_default() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded("Job", "J1")]

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/J1").mock(
            return_value=httpx.Response(200, json=_unmanaged_payload("JOBS", "J1")),
        )
        delete_route = mock.delete(f"{base}/objects/J1").mock(
            return_value=httpx.Response(204),
        )
        with AutomicClient(settings) as client:
            graph = build_graph(loaded)
            result = destroy(loaded, client, graph, only_managed=True)
    assert not delete_route.called
    assert len(result.refused) == 1
    assert result.refused[0].name == "J1"


def test_destroy_only_managed_false_deletes_anything() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded("Job", "J1")]

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/J1").mock(
            return_value=httpx.Response(200, json=_unmanaged_payload("JOBS", "J1")),
        )
        delete_route = mock.delete(f"{base}/objects/J1").mock(
            return_value=httpx.Response(204),
        )
        with AutomicClient(settings) as client:
            graph = build_graph(loaded)
            result = destroy(loaded, client, graph, only_managed=False)
    assert delete_route.called
    assert result.status == "success"


def test_destroy_dry_run_makes_no_calls() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded("Job", "J1")]

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/J1").mock(
            return_value=httpx.Response(200, json=_managed_payload("JOBS", "J1")),
        )
        delete_route = mock.delete(f"{base}/objects/J1").mock(
            return_value=httpx.Response(500, text="should not be called"),
        )
        with AutomicClient(settings) as client:
            graph = build_graph(loaded)
            result = destroy(loaded, client, graph, dry_run=True)
    assert not delete_route.called
    assert isinstance(result, DestroyResult)
    # Dry-run logs the intended deletes as successes so the CLI can render them.
    assert result.status == "success"
    assert len(result.successes) == 1


def test_destroy_missing_object_is_noop_not_failure() -> None:
    # If a declared object doesn't exist on the server, destroy treats that
    # as "already gone" — success, no DELETE call.
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded("Job", "GHOST")]
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/GHOST").mock(return_value=httpx.Response(404))
        delete_route = mock.delete(f"{base}/objects/GHOST").mock(
            return_value=httpx.Response(500),
        )
        with AutomicClient(settings) as client:
            graph = build_graph(loaded)
            result = destroy(loaded, client, graph, only_managed=True)
    assert not delete_route.called
    assert result.status == "success"


def test_destroy_kind_precedence_within_reverse_order() -> None:
    # Independent declarations of every kind land in one level; reverse
    # order must be Workflow → Schedule → Job → Variable → Calendar.
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [
        _loaded("Calendar", "CAL", {"keywords": []}),
        _loaded("Variable", "VAR", {"entries": [{"key": "K", "value": "v"}]}),
        _loaded("Job", "JOB"),
        _loaded("Schedule", "SCH", {"entries": []}),
        _loaded("Workflow", "WF", {"tasks": []}),
    ]
    call_order: list[str] = []

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        for name in ("CAL", "VAR", "JOB", "SCH", "WF"):
            mock.get(f"{base}/objects/{name}").mock(
                return_value=httpx.Response(200, json=_managed_payload("T", name)),
            )

            def delete_responder(
                request: httpx.Request, _name: str = name,
            ) -> httpx.Response:
                call_order.append(_name)
                return httpx.Response(204)

            mock.delete(f"{base}/objects/{name}").mock(side_effect=delete_responder)

        with AutomicClient(settings) as client:
            graph = build_graph(loaded)
            destroy(loaded, client, graph, only_managed=True)

    assert call_order == ["WF", "SCH", "JOB", "VAR", "CAL"]


def test_destroy_continues_past_failed_delete() -> None:
    # One DELETE fails; the rest of the reverse order should still run.
    # Failures land in result.failures; successes in successes.
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [
        _loaded("Job", "GOOD"),
        _loaded("Workflow", "BAD", {"tasks": []}),
    ]
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/GOOD").mock(
            return_value=httpx.Response(200, json=_managed_payload("JOBS", "GOOD")),
        )
        mock.get(f"{base}/objects/BAD").mock(
            return_value=httpx.Response(200, json=_managed_payload("JOBP", "BAD")),
        )
        mock.delete(f"{base}/objects/BAD").mock(
            return_value=httpx.Response(500, text="boom"),
        )
        good_route = mock.delete(f"{base}/objects/GOOD").mock(
            return_value=httpx.Response(204),
        )
        with AutomicClient(settings) as client:
            graph = build_graph(loaded)
            result = destroy(loaded, client, graph, only_managed=True)
    # BAD failed; GOOD still tried and succeeded.
    assert good_route.called
    assert result.status == "partial"
    assert len(result.failures) == 1
    assert result.failures[0].name == "BAD"
