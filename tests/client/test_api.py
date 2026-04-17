"""Tests for AutomicAPI — typed wrappers, pagination, 404 handling, envelope unwrap."""

from __future__ import annotations

import httpx
import pytest
import respx

from op_aromic.client.api import AutomicAPI, _unwrap_v21_envelope
from op_aromic.client.http import _AUTH_PATH, AutomicClient
from op_aromic.config.settings import AutomicSettings


def _make_settings() -> AutomicSettings:
    return AutomicSettings(
        url="http://example.test/ae/api/v1",
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


def test_get_object_typed_returns_dict() -> None:
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/FOO").mock(
            return_value=httpx.Response(200, json={"Name": "FOO"}),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            result = api.get_object_typed("Workflow", "FOO")
    assert result == {"Name": "FOO"}


def test_get_object_typed_returns_none_on_404() -> None:
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/MISSING").mock(
            return_value=httpx.Response(404),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            assert api.get_object_typed("Workflow", "MISSING") is None


def test_list_objects_typed_paginates() -> None:
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    page1 = {"data": [{"Name": f"W{i}"} for i in range(100)]}
    page2 = {"data": [{"Name": "W_LAST"}]}
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        call_count = {"n": 0}

        def responder(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            assert "type=JOBP" in str(request.url)
            if call_count["n"] == 1:
                return httpx.Response(200, json=page1)
            return httpx.Response(200, json=page2)

        mock.get(f"{base}/objects").mock(side_effect=responder)
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            items = list(api.list_objects_typed("Workflow"))
    assert len(items) == 101


def test_list_objects_typed_rejects_unknown_kind() -> None:
    settings = _make_settings()
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            with pytest.raises(ValueError, match="unknown kind"):
                list(api.list_objects_typed("NotAKind"))


def test_list_objects_typed_passes_folder() -> None:
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        captured: dict[str, str] = {}

        def responder(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"data": []})

        mock.get(f"{base}/objects").mock(side_effect=responder)
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            list(api.list_objects_typed("Job", folder="/PROD/ETL"))
    assert "folder=%2FPROD%2FETL" in captured["url"] or "folder=/PROD/ETL" in captured["url"]


def test_object_exists_true() -> None:
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(200, json={"Name": "X"}),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            assert api.object_exists("X") is True


def test_get_object_typed_unwraps_v21_envelope_jobs() -> None:
    """v21 envelope {total, data:{jobs:{...}}, ...} → inner dict."""
    inner = {
        "general_attributes": {"name": "MY.JOB", "type": "JOBS"},
        "scripts": [],
    }
    envelope = {"total": 1, "data": {"jobs": inner}, "path": "", "client": 100, "hasmore": False}
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/MY.JOB").mock(
            return_value=httpx.Response(200, json=envelope),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            result = api.get_object_typed("Job", "MY.JOB")
    assert result == inner


def test_get_object_typed_unwraps_v21_envelope_jobp() -> None:
    """v21 envelope for Workflow kind → jobp inner key."""
    inner = {"general_attributes": {"name": "MY.WF", "type": "JOBP"}}
    envelope = {"total": 1, "data": {"jobp": inner}, "path": "", "client": 100, "hasmore": False}
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/MY.WF").mock(
            return_value=httpx.Response(200, json=envelope),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            result = api.get_object_typed("Workflow", "MY.WF")
    assert result == inner


def test_get_object_typed_flat_response_passes_through() -> None:
    """Flat (non-envelope) responses are returned unchanged."""
    flat = {"Name": "FOO", "Type": "JOBS"}
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/FOO").mock(
            return_value=httpx.Response(200, json=flat),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            result = api.get_object_typed("Job", "FOO")
    assert result == flat


# --- Unit tests for _unwrap_v21_envelope ---


def test_unwrap_v21_envelope_extracts_vara() -> None:
    inner = {"general_attributes": {"name": "VAR", "type": "VARA"}}
    envelope = {"total": 1, "data": {"vara": inner}, "path": "", "client": 100, "hasmore": False}
    assert _unwrap_v21_envelope(envelope, "Variable") == inner


def test_unwrap_v21_envelope_extracts_cale() -> None:
    inner = {"general_attributes": {"name": "CAL", "type": "CALE"}}
    envelope = {"total": 1, "data": {"cale": inner}, "path": "", "client": 100, "hasmore": False}
    assert _unwrap_v21_envelope(envelope, "Calendar") == inner


def test_unwrap_v21_envelope_extracts_jsch() -> None:
    inner = {"general_attributes": {"name": "SCH", "type": "JSCH"}}
    envelope = {"total": 1, "data": {"jsch": inner}, "path": "", "client": 100, "hasmore": False}
    assert _unwrap_v21_envelope(envelope, "Schedule") == inner


def test_unwrap_v21_envelope_flat_passes_through() -> None:
    flat = {"Name": "X", "Type": "JOBS"}
    assert _unwrap_v21_envelope(flat, "Job") == flat


def test_unwrap_v21_envelope_missing_key_returns_data_dict() -> None:
    # Envelope present but inner key not in data → return data dict and log warning.
    data = {"unexpected_key": {"a": 1}}
    envelope = {"total": 1, "data": data, "path": "", "client": 100, "hasmore": False}
    result = _unwrap_v21_envelope(envelope, "Job")
    assert result == data


def test_object_exists_false() -> None:
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(404),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            assert api.object_exists("X") is False
