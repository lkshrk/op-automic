"""Tests for build_auth — HTTP Basic credential format, ISO-8859-1 encoding."""

from __future__ import annotations

import base64

import httpx
import pytest

from op_aromic.client.auth import _build_credential_string, _encode_basic, build_auth
from op_aromic.config.settings import AutomicSettings


def _decode_basic(header_value: str) -> str:
    """Decode a ``Basic <token>`` header back to the credential string."""
    assert header_value.startswith("Basic ")
    token = header_value[len("Basic "):]
    return base64.b64decode(token).decode("iso-8859-1")


# ---- credential string formatting ----------------------------------------

def test_credential_no_client_no_department() -> None:
    cred = _build_credential_string(client_id=None, user="USER", department="", password="PASS")
    assert cred == "USER:PASS"


def test_credential_with_department_no_client() -> None:
    cred = _build_credential_string(client_id=None, user="USER", department="DEPT", password="PASS")
    assert cred == "USER/DEPT:PASS"


def test_credential_with_client_and_department() -> None:
    cred = _build_credential_string(client_id=100, user="USER", department="DEPT", password="PASS")
    assert cred == "100/USER/DEPT:PASS"


def test_credential_with_client_no_department() -> None:
    cred = _build_credential_string(client_id=100, user="USER", department="", password="PASS")
    assert cred == "100/USER:PASS"


# ---- ISO-8859-1 encoding -------------------------------------------------

def test_encode_basic_roundtrip_ascii() -> None:
    cred = "100/USER/DEPT:PASS"
    header = _encode_basic(cred)
    assert _decode_basic(header) == cred


def test_encode_basic_roundtrip_latin1() -> None:
    # Passwords with characters outside ASCII but within ISO-8859-1.
    cred = "USER:p\xe4ssw\xf6rd"
    header = _encode_basic(cred)
    assert _decode_basic(header) == cred


def test_encode_basic_prefix() -> None:
    header = _encode_basic("USER:PASS")
    assert header.startswith("Basic ")


# ---- build_auth produces correct Authorization header --------------------

def test_build_auth_user_dept_client() -> None:
    """Three-part credential: CLIENT/USER/DEPT:PASS."""
    settings = AutomicSettings(
        url="http://x", client_id=100, user="USER", department="DEPT", password="PASS",
    )
    auth = build_auth(settings)

    # Attach to a dummy request to inspect the header.
    request = httpx.Request("GET", "http://example.com/")
    # httpx.Auth.auth_flow is a generator; consume one item to trigger header attachment.
    gen = auth.auth_flow(request)
    next(gen, None)

    header = request.headers["Authorization"]
    decoded = _decode_basic(header)
    assert decoded == "100/USER/DEPT:PASS"


def test_build_auth_user_no_dept() -> None:
    """Two-part credential: CLIENT/USER:PASS when department is empty."""
    settings = AutomicSettings(
        url="http://x", client_id=100, user="USER", department="", password="SECRET",
    )
    auth = build_auth(settings)
    request = httpx.Request("GET", "http://example.com/")
    gen = auth.auth_flow(request)
    next(gen, None)
    decoded = _decode_basic(request.headers["Authorization"])
    assert decoded == "100/USER:SECRET"


def test_build_auth_bearer_raises_not_implemented() -> None:
    """bearer auth_method is not yet implemented."""
    settings = AutomicSettings(
        url="http://x", client_id=100, user="USER", department="", password="P",
        auth_method="bearer",
    )
    with pytest.raises(NotImplementedError, match="Bearer"):
        build_auth(settings)
