"""Tests for AutomicClient — error mapping, PUT on update, auth URL shape."""

from __future__ import annotations

import httpx
import pytest
import respx

from op_aromic.client.errors import (
    AuthError,
    AutomicError,
    ConflictError,
    NotFoundError,
    RateLimitError,
)
from op_aromic.client.http import _AUTH_PATH, _UPDATE_METHOD, AutomicClient
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
    auth_url = f"{settings.url}{_AUTH_PATH}"
    mock.post(auth_url).mock(
        return_value=httpx.Response(
            200,
            json={"token": "tok", "expires_in": 3600},
        ),
    )


def test_auth_path_constant_is_authenticate() -> None:
    # Captures the current default and makes future verification a single-line flip.
    assert _AUTH_PATH == "/authenticate"


def test_update_method_is_put() -> None:
    # Default assumption per docs/ISSUES.md: Automic requires full-body PUT.
    assert _UPDATE_METHOD == "PUT"


def test_get_object_returns_json() -> None:
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/FOO").mock(
            return_value=httpx.Response(200, json={"name": "FOO"}),
        )
        with AutomicClient(settings) as client:
            result = client.get_object("FOO")
    assert result == {"name": "FOO"}


def test_get_object_404_raises_not_found() -> None:
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/MISSING").mock(
            return_value=httpx.Response(404, text="not found"),
        )
        with AutomicClient(settings) as client, pytest.raises(NotFoundError):
            client.get_object("MISSING")


def test_get_object_or_none_returns_none_on_404() -> None:
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/MISSING").mock(
            return_value=httpx.Response(404, text="not found"),
        )
        with AutomicClient(settings) as client:
            result = client.get_object_or_none("MISSING")
    assert result is None


def test_get_object_or_none_propagates_non_404() -> None:
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/ERR").mock(
            return_value=httpx.Response(500, text="boom"),
        )
        with AutomicClient(settings) as client, pytest.raises(AutomicError):
            client.get_object_or_none("ERR")


def test_create_object_409_raises_conflict() -> None:
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.post(f"{base}/objects").mock(
            return_value=httpx.Response(409, text="exists"),
        )
        with AutomicClient(settings) as client, pytest.raises(ConflictError):
            client.create_object({"Name": "X"})


def test_update_uses_put_method() -> None:
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        route = mock.put(f"{base}/objects/X").mock(
            return_value=httpx.Response(200, json={"Name": "X"}),
        )
        with AutomicClient(settings) as client:
            client.update_object("X", {"Name": "X"})
        assert route.called


def test_rate_limit_maps_to_rate_limit_error() -> None:
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/R").mock(
            return_value=httpx.Response(429, text="slow down"),
        )
        with AutomicClient(settings) as client, pytest.raises(RateLimitError):
            client.get_object("R")


def test_auth_failure_401_raises_auth_error() -> None:
    settings = _make_settings()
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{settings.url}{_AUTH_PATH}").mock(
            return_value=httpx.Response(401, text="bad creds"),
        )
        # Actual failure happens lazily on first request.
        base = f"{settings.url}/{settings.client_id}"
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(200, json={}),
        )
        with AutomicClient(settings) as client, pytest.raises(AuthError):
            client.get_object("X")


def test_delete_object_sends_delete() -> None:
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        route = mock.delete(f"{base}/objects/X").mock(
            return_value=httpx.Response(204),
        )
        with AutomicClient(settings) as client:
            client.delete_object("X")
        assert route.called


def test_list_objects_paginates() -> None:
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    page1 = {"data": [{"Name": f"OBJ{i}"} for i in range(100)]}
    page2 = {"data": [{"Name": "OBJ_LAST"}]}
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        call_count = {"n": 0}

        def responder(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return httpx.Response(200, json=page1)
            return httpx.Response(200, json=page2)

        mock.get(f"{base}/objects").mock(side_effect=responder)
        with AutomicClient(settings) as client:
            items = list(client.list_objects())

    assert len(items) == 101
