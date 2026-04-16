"""Automic REST API client.

Raw HTTP wrapper. API-level semantics (None on 404, typed listings with
pagination handling) live in ``op_aromic.client.api`` on top of this.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, cast

import httpx

from op_aromic.client.auth import TokenAuth
from op_aromic.client.errors import (
    AutomicError,
    ConflictError,
    NotFoundError,
    RateLimitError,
)
from op_aromic.config.settings import AutomicSettings

# Path segment appended to the configured base URL (which already contains
# ``/ae/api/v1``) to obtain the bearer token. The live AWA REST shape is not
# verified yet — see docs/ISSUES.md "Auth endpoint URL shape". Kept as a named
# constant so future verification is a single-line flip.
_AUTH_PATH = "/authenticate"

# HTTP method used for ``update_object``. Automic generally requires a full-body
# replacement per docs/ISSUES.md "PATCH vs PUT for updates"; defaulting to PUT.
_UPDATE_METHOD = "PUT"

_STATUS_MAP: dict[int, type[AutomicError]] = {
    404: NotFoundError,
    409: ConflictError,
    429: RateLimitError,
}

_logger = logging.getLogger(__name__)


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

    def get_object(self, name: str) -> dict[str, Any]:
        response = self._http.get(f"{self._base}/objects/{name}")
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
        response = self._http.post(f"{self._base}/objects", json=payload)
        self._raise_for_status(response)
        return cast(dict[str, Any], response.json())

    def update_object(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._http.request(
            _UPDATE_METHOD,
            f"{self._base}/objects/{name}",
            json=payload,
        )
        self._raise_for_status(response)
        return cast(dict[str, Any], response.json())

    def delete_object(self, name: str) -> None:
        response = self._http.delete(f"{self._base}/objects/{name}")
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
            response = self._http.get(f"{self._base}/objects", params=params)
            self._raise_for_status(response)
            data = response.json()
            objects = data.get("data", [])
            if not objects:
                break
            yield from objects
            if len(objects) < params["max_rows"]:
                break
            params["start_row"] += params["max_rows"]
