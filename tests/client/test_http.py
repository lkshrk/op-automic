"""Tests for AutomicClient — error mapping, retry config, auth URL shape."""

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


def test_request_logging_emits_structured_event(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Configure JSON logging so we can parse exactly one event off stderr.
    import json

    import structlog

    from op_aromic.observability.logging import configure_logging

    monkeypatch.delenv("CI", raising=False)
    structlog.reset_defaults()
    configure_logging(level="info", format="json")

    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/FOO").mock(
            return_value=httpx.Response(200, json={"Name": "FOO"}),
        )
        with AutomicClient(settings) as client:
            client.get_object("FOO")

    err_lines = [
        line for line in capsys.readouterr().err.splitlines() if line.strip()
    ]
    request_events = [
        json.loads(line)
        for line in err_lines
        if '"event": "http.request"' in line
    ]
    assert request_events, "expected at least one http.request event"
    # The actual GET to /objects/FOO must have been logged with its path
    # and numeric status; crucially no Authorization header leaks.
    for ev in request_events:
        assert "Authorization" not in json.dumps(ev)
        assert "Bearer" not in json.dumps(ev)
    got = next(ev for ev in request_events if ev["method"] == "GET")
    assert got["path"] == f"/ae/api/v1/{settings.client_id}/objects/FOO"
    assert got["status"] == 200
    assert isinstance(got["duration_ms"], (int, float))

    structlog.reset_defaults()


def test_update_method_default_is_post_import() -> None:
    # Default update_method per swagger v21 is POST_IMPORT (stored on instance as "POST").
    settings = _make_settings()
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        with AutomicClient(settings) as client:
            assert client._update_method == "POST"


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


def test_update_uses_put_method_when_configured() -> None:
    settings = AutomicSettings(
        url="http://example.test/ae/api/v1",
        client_id=100,
        user="USER",
        department="DEPT",
        password="pw",
        verify_ssl=False,
        max_retries=0,
        update_method="PUT",
    )
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
    # Three consecutive 429s (no Retry-After) → retry with exponential
    # backoff, then raise RateLimitError.
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/R").mock(
            return_value=httpx.Response(429, text="slow down"),
        )
        with AutomicClient(settings) as client, pytest.raises(RateLimitError):
            client.get_object("R")


def test_rate_limit_retry_with_retry_after_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    # First response 429 with Retry-After: 1 → second succeeds.
    sleeps: list[float] = []
    monkeypatch.setattr("op_aromic.client.http.time.sleep", sleeps.append)

    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        call_count = {"n": 0}

        def responder(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "1"}, text="wait")
            return httpx.Response(200, json={"Name": "R"})

        mock.get(f"{base}/objects/R").mock(side_effect=responder)
        with AutomicClient(settings) as client:
            result = client.get_object("R")
    assert result == {"Name": "R"}
    assert sleeps == [1.0]


def test_rate_limit_retry_with_http_date(monkeypatch: pytest.MonkeyPatch) -> None:
    # Retry-After as an HTTP-date 2 seconds into the future.
    import email.utils as eut
    import time as real_time

    sleeps: list[float] = []
    monkeypatch.setattr("op_aromic.client.http.time.sleep", sleeps.append)
    # Freeze time() so the parser's delta is predictable.
    monkeypatch.setattr(
        "op_aromic.client.http.time.time",
        lambda: 1_000_000.0,
    )
    future_http_date = eut.formatdate(1_000_000.0 + 2.0, usegmt=True)
    del real_time  # only needed indirectly above

    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        calls = {"n": 0}

        def responder(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(
                    429, headers={"Retry-After": future_http_date}, text="wait",
                )
            return httpx.Response(200, json={"Name": "R"})

        mock.get(f"{base}/objects/R").mock(side_effect=responder)
        with AutomicClient(settings) as client:
            result = client.get_object("R")
    assert result == {"Name": "R"}
    # Allow minor float slop: we expect ~2.0s.
    assert len(sleeps) == 1
    assert 1.9 <= sleeps[0] <= 2.1


def test_rate_limit_malformed_retry_after_falls_back_to_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Malformed Retry-After → exponential backoff 0.5, 1.0, then raise.
    sleeps: list[float] = []
    monkeypatch.setattr("op_aromic.client.http.time.sleep", sleeps.append)

    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/R").mock(
            return_value=httpx.Response(
                429, headers={"Retry-After": "not-a-number"}, text="wait",
            ),
        )
        with AutomicClient(settings) as client, pytest.raises(RateLimitError):
            client.get_object("R")
    assert sleeps == [0.5, 1.0]


def test_non_429_4xx_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("op_aromic.client.http.time.sleep", sleeps.append)

    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        route = mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(409, text="exists"),
        )
        with AutomicClient(settings) as client, pytest.raises(ConflictError):
            client.get_object("X")
    assert route.call_count == 1
    assert sleeps == []


def test_auth_failure_401_raises_auth_error() -> None:
    # With Basic auth the API itself returns 401 on bad credentials.
    settings = _make_settings()
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{base}/objects/X").mock(
            return_value=httpx.Response(401, text="Unauthorized"),
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


def test_retry_base_delay_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # retry_base_delay_ms=1000 → first backoff sleep is 1.0s.
    sleeps: list[float] = []
    monkeypatch.setattr("op_aromic.client.http.time.sleep", sleeps.append)

    settings = AutomicSettings(
        url="http://example.test/ae/api/v1",
        client_id=100,
        user="USER",
        department="DEPT",
        password="pw",
        verify_ssl=False,
        max_retries=0,
        retry_base_delay_ms=1000,
    )
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        calls = {"n": 0}

        def responder(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, text="slow")
            return httpx.Response(200, json={"Name": "X"})

        mock.get(f"{base}/objects/X").mock(side_effect=responder)
        with AutomicClient(settings) as client:
            client.get_object("X")

    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(1.0)


def test_retry_custom_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    # retry_statuses=[503] → 503 triggers retry, 429 does not.
    sleeps: list[float] = []
    monkeypatch.setattr("op_aromic.client.http.time.sleep", sleeps.append)

    settings = AutomicSettings(
        url="http://example.test/ae/api/v1",
        client_id=100,
        user="USER",
        department="DEPT",
        password="pw",
        verify_ssl=False,
        max_retries=0,
        retry_statuses=[503],
    )
    base = f"{settings.url}/{settings.client_id}"
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        calls = {"n": 0}

        def responder(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503, text="unavailable")
            return httpx.Response(200, json={"Name": "X"})

        mock.get(f"{base}/objects/X").mock(side_effect=responder)
        with AutomicClient(settings) as client:
            result = client.get_object("X")

    assert result == {"Name": "X"}
    assert len(sleeps) == 1  # retried once


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
