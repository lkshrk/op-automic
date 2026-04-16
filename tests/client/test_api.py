"""Tests for AutomicAPI — typed wrappers, pagination, 404 handling."""

from __future__ import annotations

import httpx
import pytest
import respx

from op_aromic.client.api import AutomicAPI
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
