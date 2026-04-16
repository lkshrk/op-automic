"""Automic REST API client.

Raw HTTP wrapper. API-level semantics (None on 404, typed listings with
pagination handling) live in ``op_aromic.client.api`` on top of this.

Also implements 429-aware retry: Automic exposes a Retry-After header
(seconds or HTTP-date). We honour it up to ``_MAX_429_RETRIES`` times,
falling back to a capped exponential backoff when the header is missing
or malformed. Non-429 errors (including 4xx like 409) are not retried.
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

# Path segment appended to the configured base URL (which already contains
# ``/ae/api/v1``) to obtain the bearer token. The live AWA REST shape is not
# verified yet — see docs/ISSUES.md "Auth endpoint URL shape". Kept as a named
# constant so future verification is a single-line flip.
_AUTH_PATH = "/authenticate"

# HTTP method used for ``update_object``. Automic generally requires a full-body
# replacement per docs/ISSUES.md "PATCH vs PUT for updates"; defaulting to PUT.
_UPDATE_METHOD = "PUT"

# Maximum total number of attempts per logical request before raising on
# a persistent 429. "3 attempts" = 1 initial + 2 retries.
_MAX_429_ATTEMPTS = 3

# Fallback backoff when Retry-After is absent or malformed. One entry per
# *sleep between attempts*, i.e. (_MAX_429_ATTEMPTS - 1) entries.
_BACKOFF_SCHEDULE: tuple[float, ...] = (0.5, 1.0)

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


def _parse_retry_after(header_value: str | None, attempt: int) -> float:
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
    if 0 <= attempt < len(_BACKOFF_SCHEDULE):
        return _BACKOFF_SCHEDULE[attempt]
    return _BACKOFF_SCHEDULE[-1]


class AutomicClient:
    """Synchronous client for Automic REST API object CRUD."""

    def __init__(self, settings: AutomicSettings) -> None:
        self._base = f"{settings.url}/{settings.client_id}"
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
        """Send an HTTP request, retrying on 429 per Retry-After/backoff.

        Non-429 responses are returned unchanged for the caller's
        ``_raise_for_status`` mapping to handle. Only 429 triggers the
        retry loop. Emits ``event="http.request"`` on every completed
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
            if response.status_code != 429:
                return response
            if attempt == _MAX_429_ATTEMPTS - 1:
                return response
            sleep_for = _parse_retry_after(
                response.headers.get("Retry-After"), attempt,
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
        response = self._send_with_retry(
            _UPDATE_METHOD,
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
