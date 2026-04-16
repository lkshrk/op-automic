"""Bearer token authentication for Automic REST API."""

from __future__ import annotations

import logging
import time
from collections.abc import Generator

import httpx

from op_aromic.client.errors import AuthError

_logger = logging.getLogger(__name__)

# See docs/ISSUES.md "Auth endpoint URL shape" — ``base_url`` already
# contains ``/ae/api/v1`` so the resulting URL is
# ``/ae/api/v1/authenticate``. Not verified against a live AWA instance.
_AUTH_PATH = "/authenticate"


class TokenAuth(httpx.Auth):
    """Acquires and refreshes Automic bearer tokens."""

    def __init__(
        self,
        base_url: str,
        user: str,
        department: str,
        password: str,
    ) -> None:
        self._base_url = base_url
        self._user = user
        self._department = department
        self._password = password
        self._token: str | None = None
        self._expires_at: float = 0

    def auth_flow(
        self, request: httpx.Request,
    ) -> Generator[httpx.Request, httpx.Response, None]:
        if self._token is None or time.monotonic() >= self._expires_at:
            self._authenticate()
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request

    def _authenticate(self) -> None:
        auth_url = f"{self._base_url}{_AUTH_PATH}"
        _logger.debug("authenticating to %s", auth_url)
        response = httpx.post(
            auth_url,
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
