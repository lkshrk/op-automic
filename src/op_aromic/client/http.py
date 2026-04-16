"""Automic REST API client."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from op_aromic.client.auth import TokenAuth
from op_aromic.client.errors import (
    AutomicError,
    ConflictError,
    NotFoundError,
    RateLimitError,
)
from op_aromic.config.settings import AutomicSettings

_STATUS_MAP: dict[int, type[AutomicError]] = {
    404: NotFoundError,
    409: ConflictError,
    429: RateLimitError,
}


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
        return response.json()

    def create_object(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._http.post(f"{self._base}/objects", json=payload)
        self._raise_for_status(response)
        return response.json()

    def update_object(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._http.patch(f"{self._base}/objects/{name}", json=payload)
        self._raise_for_status(response)
        return response.json()

    def delete_object(self, name: str) -> None:
        response = self._http.delete(f"{self._base}/objects/{name}")
        self._raise_for_status(response)

    def list_objects(
        self, *, object_type: str | None = None, folder: str | None = None,
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
