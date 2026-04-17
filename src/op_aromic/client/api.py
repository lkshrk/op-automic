"""Typed API wrappers over AutomicClient.

Separates HTTP plumbing (``http.py``) from API semantics: kind-aware list
filtering, 404-as-None, existence probes. Every caller in the engine goes
through this layer; nothing below ``engine`` should import ``http.py``
directly.

Response envelope handling (B3)
--------------------------------
Automic AE REST v21 wraps every single-object GET in::

    {
        "total": 1,
        "data": {"<kind_lower>": {...object fields...}},
        "path": "",
        "client": 100,
        "hasmore": false,
    }

``_unwrap_v21_envelope`` detects this shape (presence of ``total``,
``data`` dict, and ``client`` keys) and extracts the inner object dict.
Flat responses (legacy or from the existing test fixtures) pass through
unchanged. All callers keep receiving ``dict[str, Any] | None``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from op_aromic.client.http import AutomicClient
from op_aromic.observability.logging import get_logger

# Map aromic manifest ``kind`` → Automic object type discriminator sent as the
# ``type`` query parameter to ``/objects``. Not verified against live AWA —
# captured here so the map is the single source of truth.
_KIND_TO_AUTOMIC_TYPE: dict[str, str] = {
    "Workflow": "JOBP",
    "Job": "JOBS",
    "Schedule": "JSCH",
    "Calendar": "CALE",
    "Variable": "VARA",
}

# Map Automic type → inner key inside ``data`` in a v21 GET response.
# Derived from Broadcom AE REST swagger v21 real fixtures; lower-cased
# short-name of the object type (confirmed from real API captures).
_AUTOMIC_TYPE_TO_DATA_KEY: dict[str, str] = {
    "JOBP": "jobp",
    "JOBS": "jobs",
    "JSCH": "jsch",
    "CALE": "cale",
    "VARA": "vara",
}

_logger = get_logger("op_aromic.client.api")


def _unwrap_v21_envelope(
    payload: dict[str, Any],
    kind: str,
) -> dict[str, Any]:
    """Strip the v21 response envelope; return the inner object dict.

    Detects the envelope by the presence of **all three** of ``total``,
    ``data`` (as a dict), and ``client`` at the top level. Flat responses
    (no envelope) are returned as-is so existing test fixtures and any
    non-standard Automic instances continue to work.

    If the envelope is present but the expected inner key is missing (e.g.
    the API added an unknown kind), a warning is emitted and the raw
    ``data`` dict is returned so callers still get *something* useful.
    """
    if not (
        "total" in payload
        and isinstance(payload.get("data"), dict)
        and "client" in payload
    ):
        return payload  # flat / non-envelope response — pass through

    inner: dict[str, Any] = payload["data"]
    automic_type = _KIND_TO_AUTOMIC_TYPE.get(kind)
    data_key = _AUTOMIC_TYPE_TO_DATA_KEY.get(automic_type or "")
    if data_key and data_key in inner:
        return dict(inner[data_key])

    # Envelope detected but expected key not found; log and fall back.
    _logger.warning(
        "api.envelope_key_missing",
        kind=kind,
        automic_type=automic_type,
        expected_key=data_key,
        actual_keys=list(inner.keys()),
    )
    return inner


def _extract_envelope_path(payload: dict[str, Any]) -> str:
    """Return the ``path`` field from a v21 response envelope, or ``""`` if absent.

    The ``path`` field carries the Automic folder where the object lives.
    It is present only on v21-envelope responses; flat legacy payloads
    have no path field and return empty string.
    """
    if (
        "total" in payload
        and isinstance(payload.get("data"), dict)
        and "client" in payload
    ):
        return str(payload.get("path") or "")
    return ""


class AutomicAPI:
    """Typed façade around ``AutomicClient``.

    Kept intentionally thin: the engine needs ``get``, ``list``, ``exists`` and
    a 404-tolerant ``get``. Mutations stay on the raw client until Phase 3.
    """

    def __init__(self, client: AutomicClient) -> None:
        self._client = client

    def get_object_typed(self, kind: str, name: str) -> dict[str, Any] | None:
        """Fetch a single object by name; return None if it does not exist.

        Automatically unwraps the v21 response envelope when present so
        callers always receive the inner object dict regardless of whether
        the server returns the v21 envelope or a flat legacy response.

        When the v21 envelope is detected, the ``path`` field (Automic
        folder) is injected into the returned dict as the synthetic key
        ``_envelope_path`` so that the normalizer and exporter can populate
        ``metadata.folder`` without re-fetching the envelope.  Flat legacy
        responses do not carry a path and the key is not injected.

        ``kind`` is required so the envelope unwrapper can locate the
        correct inner key (e.g. ``"jobs"`` for kind ``"Job"``).
        """
        raw = self._client.get_object_or_none(name)
        if raw is None:
            return None
        envelope_path = _extract_envelope_path(raw)
        inner = _unwrap_v21_envelope(raw, kind)
        if envelope_path:
            # Inject as synthetic key; normalizer reads this to set folder.
            # Use a leading underscore to distinguish from real Automic fields.
            inner = {**inner, "_envelope_path": envelope_path}
        return inner

    def list_objects_typed(
        self,
        kind: str,
        folder: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """List all objects of a given kind, optionally scoped to a folder.

        When ``folder`` is provided the request goes through
        ``GET /{client_id}/folderobjects/{folder_path}`` — the canonical
        folder-scoped listing endpoint per Automic AE REST swagger v21.
        Without a folder the legacy ``GET /{client_id}/objects?type=...``
        endpoint is used (unverified against a live instance).

        Pagination is handled by the underlying iterator in both cases.
        """
        automic_type = _KIND_TO_AUTOMIC_TYPE.get(kind)
        if automic_type is None:
            raise ValueError(f"unknown kind for listing: {kind!r}")
        if folder is not None:
            yield from self._client.list_folder_objects(
                folder, object_type=automic_type,
            )
        else:
            yield from self._client.list_objects(object_type=automic_type)

    def object_exists(self, name: str) -> bool:
        """Cheap existence probe. True iff a GET for ``name`` returns 200."""
        return self._client.get_object_or_none(name) is not None


__all__ = ["AutomicAPI", "_extract_envelope_path", "_unwrap_v21_envelope"]
