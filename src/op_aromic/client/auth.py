"""HTTP Basic authentication for Automic REST API (swagger v21).

Automic AE REST v1 uses HTTP Basic authentication only. The credential
string is ``[CLIENT/]USERNAME[/DEPARTMENT]:PASSWORD``, Base64-encoded in
ISO-8859-1 (Latin-1) per RFC 7617.

Bearer tokens were added in Automic 24.2+; that path is stubbed below for
future use — see ISSUES.md "Auth method confirmed".
"""

from __future__ import annotations

import base64
from collections.abc import Generator
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from op_aromic.config.settings import AutomicSettings


def _build_credential_string(
    *,
    client_id: int | None,
    user: str,
    department: str,
    password: str,
) -> str:
    """Assemble the Automic credential string.

    Format: ``[CLIENT/]USER[/DEPT]:PASSWORD``

    Rules (per swagger v21):
    - Client prefix is included only when client_id is provided.
    - Department suffix is included only when non-empty.
    """
    parts: list[str] = []
    if client_id is not None:
        parts.append(str(client_id))
    parts.append(user)
    if department:
        parts.append(department)
    return "/".join(parts) + ":" + password


def _encode_basic(credential: str) -> str:
    """Return the ``Basic <token>`` header value for ``credential``.

    Automic docs specify ISO-8859-1 encoding per RFC 7617 §2.
    """
    encoded = base64.b64encode(credential.encode("iso-8859-1")).decode("ascii")
    return f"Basic {encoded}"


class _BasicAuth(httpx.Auth):
    """httpx.Auth subclass that attaches a static Basic auth header."""

    def __init__(self, header_value: str) -> None:
        self._header_value = header_value

    def auth_flow(
        self, request: httpx.Request,
    ) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = self._header_value
        yield request


def build_auth(settings: AutomicSettings) -> httpx.Auth:
    """Return an httpx.Auth for the configured auth_method.

    ``basic`` (default, swagger v21 canonical): Constructs HTTP Basic with
    the Automic credential string ``[CLIENT/]USER[/DEPT]:PASSWORD``.

    ``bearer``: Not yet implemented — Automic 24.2+ introduced bearer token
    support, but op-aromic targets v21. Raises NotImplementedError; the
    ``auth_method`` setting exists for future use.
    """
    if settings.auth_method == "bearer":
        raise NotImplementedError(
            "Bearer token auth is not yet implemented. "
            "Automic AE REST v21 uses HTTP Basic only. "
            "Bearer support was added in Automic 24.2+; set auth_method='basic' "
            "unless you are targeting that version and have implemented the token flow."
        )

    credential = _build_credential_string(
        client_id=settings.client_id,
        user=settings.user,
        department=settings.department,
        password=settings.password.get_secret_value(),
    )
    return _BasicAuth(_encode_basic(credential))


# Keep TokenAuth name importable for any legacy tests still referencing it;
# this shim simply wraps build_auth so callers get the same behaviour.
class TokenAuth:  # pragma: no cover  -- legacy shim, not exercised by new tests
    """Deprecated: use build_auth() instead.

    Kept so Phase-5 test imports don't break immediately; will be removed
    once all callers migrate.
    """

    def __init__(
        self,
        base_url: str,
        user: str,
        department: str,
        password: str,
    ) -> None:
        # Stash fields so http.py can still construct it; the actual
        # auth header is produced by build_auth when AutomicClient is used.
        self._base_url = base_url
        self._user = user
        self._department = department
        self._password = password
        self._token: str | None = None
        self._expires_at: float = 0

    def auth_flow(
        self, request: httpx.Request,
    ) -> Generator[httpx.Request, httpx.Response, None]:
        # Delegate to the basic-auth behaviour inline for compatibility.
        credential = f"{self._user}/{self._department}:{self._password}"
        encoded = base64.b64encode(credential.encode("iso-8859-1")).decode("ascii")
        request.headers["Authorization"] = f"Basic {encoded}"
        yield request
