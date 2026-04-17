"""Automic REST API client.

Raw HTTP wrapper. API-level semantics (None on 404, typed listings with
pagination handling) live in ``op_aromic.client.api`` on top of this.

Also implements 429-aware retry: Automic exposes a Retry-After header
(seconds or HTTP-date). We honour it up to ``_MAX_429_RETRIES`` times,
falling back to a capped exponential backoff when the header is missing
or malformed. Non-429 errors (including 4xx like 409) are not retried.

Retry parameters (base delay, cap, status codes) are driven by
``AutomicSettings`` so operators can tune without code changes.
"""

from __future__ import annotations

import email.utils as _email_utils
import time
from collections.abc import Iterator
from typing import Any, cast
from urllib.parse import urlsplit

import httpx

from op_aromic.client.auth import TokenAuth
from op_aromic.client.errors import (
    AutomicError,
    ConflictError,
    NotFoundError,
    RateLimitError,
)
from op_aromic.config.settings import AutomicSettings
from op_aromic.observability.logging import get_logger

# Kept for backward compatibility with existing tests; B1 will remove it.
# Points to the bearer-token endpoint — which does not exist in v21 per
# swagger (see ISSUES.md "Auth method confirmed"). Removed from active use.
_AUTH_PATH = "/authenticate"

# Maximum total number of attempts per logical request before raising on
# a persistent 429. "3 attempts" = 1 initial + 2 retries.
_MAX_429_ATTEMPTS = 3

_STATUS_MAP: dict[int, type[AutomicError]] = {
    404: NotFoundError,
    409: ConflictError,
    429: RateLimitError,
}

_logger = get_logger("op_aromic.client.http")


def _url_path_only(url: str) -> str:
    """Strip scheme/host/query — we log path only to avoid leaking query secrets."""
    split = urlsplit(url)
    return split.path or "/"


def _parse_retry_after(
    header_value: str | None,
    attempt: int,
    backoff_schedule: tuple[float, ...],
) -> float:
    """Return the number of seconds the caller should sleep before retry.

    Accepts either ``"<seconds>"`` (integer or float string) or an RFC 7231
    HTTP-date. Anything else falls back to the exponential backoff schedule
    keyed by attempt index.
    """
    if header_value:
        stripped = header_value.strip()
        # Integer seconds is the common case per RFC 7231 §7.1.3.
        try:
            return max(0.0, float(stripped))
        except ValueError:
            pass
        # HTTP-date — compute delta from now. parsedate_to_datetime raises
        # ValueError on malformed input; treat that as "unknown, back off".
        try:
            parsed = _email_utils.parsedate_to_datetime(stripped)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None:
            delta = parsed.timestamp() - time.time()
            if delta >= 0:
                return delta
    # Fall through to exponential backoff.
    if 0 <= attempt < len(backoff_schedule):
        return backoff_schedule[attempt]
    return backoff_schedule[-1]


class AutomicClient:
    """Synchronous client for Automic REST API object CRUD."""

    def __init__(self, settings: AutomicSettings) -> None:
        self._base = f"{settings.url}/{settings.client_id}"
        # Auth: B1 will replace TokenAuth with build_auth (Basic per swagger v21).
        auth = TokenAuth(
            base_url=settings.url,
            user=settings.user,
            department=settings.department,
            password=settings.password.get_secret_value(),
        )
        transport = httpx.HTTPTransport(retries=settings.max_retries)
        self._http = httpx.Client(
            auth=auth,
            transport=transport,
            timeout=settings.timeout,
            verify=settings.verify_ssl,
        )
        # Retry parameters driven by settings so operators can tune without
        # code changes (see AutomicSettings.retry_*).
        self._retry_statuses: frozenset[int] = frozenset(settings.retry_statuses)
        # Build a two-step exponential backoff schedule from settings:
        # step 0 = base_delay_ms / 1000, step 1 = min(2 * step0, max_backoff).
        _base_s = settings.retry_base_delay_ms / 1000.0
        _cap = settings.retry_max_backoff_s
        self._backoff_schedule: tuple[float, ...] = (
            min(_base_s, _cap),
            min(_base_s * 2.0, _cap),
        )
        self._update_method: str = (
            "PUT" if settings.update_method == "PUT" else "POST"
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> AutomicClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        error_cls = _STATUS_MAP.get(response.status_code, AutomicError)
        raise error_cls(
            f"{response.request.method} {response.request.url}: "
            f"{response.status_code} {response.text}",
            status_code=response.status_code,
        )

    def _send_with_retry(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send an HTTP request, retrying on configured statuses per Retry-After/backoff.

        Non-retried responses are returned unchanged for the caller's
        ``_raise_for_status`` mapping to handle. Retried statuses (default: 429)
        trigger the retry loop. Emits ``event="http.request"`` on every completed
        attempt — headers are never logged; redaction is handled at the
        processor level.
        """
        path = _url_path_only(url)
        for attempt in range(_MAX_429_ATTEMPTS):
            started = time.monotonic()
            response = self._http.request(method, url, **kwargs)
            elapsed_ms = (time.monotonic() - started) * 1000.0
            _logger.info(
                "http.request",
                method=method,
                path=path,
                status=response.status_code,
                duration_ms=round(elapsed_ms, 2),
                attempt=attempt + 1,
            )
            if response.status_code not in self._retry_statuses:
                return response
            if attempt == _MAX_429_ATTEMPTS - 1:
                return response
            sleep_for = _parse_retry_after(
                response.headers.get("Retry-After"),
                attempt,
                self._backoff_schedule,
            )
            _logger.warning(
                "http.rate_limited",
                method=method,
                path=path,
                sleep_seconds=sleep_for,
                attempt=attempt + 1,
                max_attempts=_MAX_429_ATTEMPTS,
            )
            time.sleep(sleep_for)
        # Unreachable: the loop always returns on the last attempt.
        return response  # pragma: no cover

    def get_object(self, name: str) -> dict[str, Any]:
        response = self._send_with_retry("GET", f"{self._base}/objects/{name}")
        self._raise_for_status(response)
        return cast(dict[str, Any], response.json())

    def get_object_or_none(self, name: str) -> dict[str, Any] | None:
        """Return the object JSON, or None if the server replies 404.

        Centralises the 404-is-not-an-error distinction so callers can skip
        wrapping every ``get_object`` in a try/except NotFoundError.
        """
        try:
            return self.get_object(name)
        except NotFoundError:
            return None

    def create_object(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._send_with_retry(
            "POST", f"{self._base}/objects", json=payload,
        )
        self._raise_for_status(response)
        return cast(dict[str, Any], response.json())

    def update_object(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        # update_method is set from settings.update_method at __init__ time:
        # "POST" for POST_IMPORT, "PUT" for legacy. The actual POST endpoint
        # with overwrite=true is handled by B2; here we keep the method
        # configurable for the legacy PUT path.
        response = self._send_with_retry(
            self._update_method,
            f"{self._base}/objects/{name}",
            json=payload,
        )
        self._raise_for_status(response)
        return cast(dict[str, Any], response.json())

    def delete_object(self, name: str) -> None:
        response = self._send_with_retry(
            "DELETE", f"{self._base}/objects/{name}",
        )
        self._raise_for_status(response)

    def list_objects(
        self,
        *,
        object_type: str | None = None,
        folder: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {"max_rows": 100, "start_row": 0}
        if object_type:
            params["type"] = object_type
        if folder:
            params["folder"] = folder

        while True:
            response = self._send_with_retry(
                "GET", f"{self._base}/objects", params=params,
            )
            self._raise_for_status(response)
            data = response.json()
            objects = data.get("data", [])
            if not objects:
                break
            yield from objects
            if len(objects) < params["max_rows"]:
                break
            params["start_row"] += params["max_rows"]
