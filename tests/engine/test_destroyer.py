"""Tests for the destroyer — reverse-order deletes, managed-only guard, dry_run.

B5: Automic AE REST v21 does not support DELETE on objects. The destroyer now
records NotSupportedDelete entries instead of calling client.delete_object.
Tests have been updated accordingly — no DELETE routes are mocked because no
DELETE calls should occur.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import respx

from op_aromic.client.http import _AUTH_PATH, AutomicClient
from op_aromic.config.settings import AutomicSettings
from op_aromic.engine.dependency import build_graph
from op_aromic.engine.destroyer import DestroyResult, NotSupportedDelete, destroy
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
    # B5: DELETE not supported — records not_supported in reverse dep order.
    # Workflow (depends-on Job) must appear before Job in not_supported list.
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [
        _loaded("Job", "J1"),
        _loaded("Workflow", "WF", {"tasks": []}),
    ]

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/J1").mock(
            return_value=httpx.Response(200, json=_managed_payload("JOBS", "J1")),
        )
        mock.get(f"{base}/objects/WF").mock(
            return_value=httpx.Response(200, json=_managed_payload("JOBP", "WF")),
        )

        with AutomicClient(settings) as client:
            graph = build_graph(loaded)
            result = destroy(loaded, client, graph, only_managed=True)

    # B5: status is partial because not_supported is non-empty.
    assert result.status == "partial"
    assert len(result.not_supported) == 2
    # Reverse dependency order: Workflow before Job.
    names = [ns.name for ns in result.not_supported]
    assert names == ["WF", "J1"]


def test_destroy_refuses_unmanaged_by_default() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded("Job", "J1")]

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/J1").mock(
            return_value=httpx.Response(200, json=_unmanaged_payload("JOBS", "J1")),
        )
        with AutomicClient(settings) as client:
            graph = build_graph(loaded)
            result = destroy(loaded, client, graph, only_managed=True)

    # Unmanaged → refused, not not_supported (the check happens before the delete).
    assert len(result.refused) == 1
    assert result.refused[0].name == "J1"
    assert len(result.not_supported) == 0


def test_destroy_only_managed_false_records_not_supported() -> None:
    # B5: even with only_managed=False, DELETE is not called; not_supported instead.
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded("Job", "J1")]

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/J1").mock(
            return_value=httpx.Response(200, json=_unmanaged_payload("JOBS", "J1")),
        )
        with AutomicClient(settings) as client:
            graph = build_graph(loaded)
            result = destroy(loaded, client, graph, only_managed=False)

    assert len(result.not_supported) == 1
    assert result.not_supported[0].name == "J1"
    assert result.status == "partial"


def test_destroy_dry_run_makes_no_calls() -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded("Job", "J1")]

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/J1").mock(
            return_value=httpx.Response(200, json=_managed_payload("JOBS", "J1")),
        )
        with AutomicClient(settings) as client:
            graph = build_graph(loaded)
            result = destroy(loaded, client, graph, dry_run=True)

    assert isinstance(result, DestroyResult)
    # Dry-run logs the intended deletes as successes so the CLI can render them.
    assert result.status == "success"
    assert len(result.successes) == 1
    # No not_supported in dry run — the API path is never reached.
    assert len(result.not_supported) == 0


def test_destroy_missing_object_is_noop_not_failure() -> None:
    # If a declared object doesn't exist on the server, destroy treats that
    # as "already gone" — success, no not_supported entry.
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded("Job", "GHOST")]
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/GHOST").mock(return_value=httpx.Response(404))
        with AutomicClient(settings) as client:
            graph = build_graph(loaded)
            result = destroy(loaded, client, graph, only_managed=True)
    assert result.status == "success"
    assert len(result.successes) == 1
    assert len(result.not_supported) == 0


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

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        for name in ("CAL", "VAR", "JOB", "SCH", "WF"):
            mock.get(f"{base}/objects/{name}").mock(
                return_value=httpx.Response(200, json=_managed_payload("T", name)),
            )

        with AutomicClient(settings) as client:
            graph = build_graph(loaded)
            result = destroy(loaded, client, graph, only_managed=True)

    # B5: all recorded as not_supported in reverse dependency order.
    names = [ns.name for ns in result.not_supported]
    assert names == ["WF", "SCH", "JOB", "VAR", "CAL"]


def test_destroy_not_supported_carries_reason() -> None:
    # NotSupportedDelete entries have a human-readable reason explaining
    # that the v21 API lacks a DELETE endpoint.
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded("Job", "J1")]

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/J1").mock(
            return_value=httpx.Response(200, json=_managed_payload("JOBS", "J1")),
        )
        with AutomicClient(settings) as client:
            graph = build_graph(loaded)
            result = destroy(loaded, client, graph, only_managed=True)

    assert len(result.not_supported) == 1
    entry = result.not_supported[0]
    assert isinstance(entry, NotSupportedDelete)
    assert entry.kind == "Job"
    assert entry.name == "J1"
    assert "v21" in entry.reason.lower() or "delete" in entry.reason.lower()
