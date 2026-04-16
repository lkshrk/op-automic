"""Bearer token authentication for Automic REST API."""

from __future__ import annotations

import time

import httpx

from op_aromic.client.errors import AuthError


class TokenAuth(httpx.Auth):
    """Acquires and refreshes Automic bearer tokens."""

    def __init__(self, base_url: str, user: str, department: str, password: str) -> None:
        self._base_url = base_url
        self._user = user
        self._department = department
        self._password = password
        self._token: str | None = None
        self._expires_at: float = 0

    def auth_flow(self, request: httpx.Request) -> httpx.Auth.EventHook:
        if self._token is None or time.monotonic() >= self._expires_at:
            self._authenticate()
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request

    def _authenticate(self) -> None:
        response = httpx.post(
            f"{self._base_url}/authenticate",
            json={
                "user": self._user,
                "department": self._department,
                "password": self._password,
            },
        )
        if response.status_code != 200:
            raise AuthError(
                f"Authentication failed: {response.status_code} {response.text}",
                status_code=response.status_code,
            )
        data = response.json()
        self._token = data.get("token") or data.get("access_token")
        ttl = data.get("expires_in", 3600)
        self._expires_at = time.monotonic() + ttl - 60
