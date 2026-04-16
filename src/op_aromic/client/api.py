"""Typed API wrappers over AutomicClient.

Separates HTTP plumbing (``http.py``) from API semantics: kind-aware list
filtering, 404-as-None, existence probes. Every caller in the engine goes
through this layer; nothing below ``engine`` should import ``http.py``
directly.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from op_aromic.client.http import AutomicClient

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


class AutomicAPI:
    """Typed façade around ``AutomicClient``.

    Kept intentionally thin: the engine needs ``get``, ``list``, ``exists`` and
    a 404-tolerant ``get``. Mutations stay on the raw client until Phase 3.
    """

    def __init__(self, client: AutomicClient) -> None:
        self._client = client

    def get_object_typed(self, kind: str, name: str) -> dict[str, Any] | None:
        """Fetch a single object by name; return None if it does not exist.

        ``kind`` is accepted so callers can be explicit at the call site; the
        live client does not require it to GET by name (names are globally
        unique within a client). Retained in the signature to future-proof
        against a possible kind-scoped lookup endpoint.
        """
        del kind  # unused today; see docstring
        return self._client.get_object_or_none(name)

    def list_objects_typed(
        self,
        kind: str,
        folder: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """List all objects of a given kind, optionally scoped to a folder.

        Pagination is handled by the underlying ``list_objects`` iterator.
        """
        automic_type = _KIND_TO_AUTOMIC_TYPE.get(kind)
        if automic_type is None:
            raise ValueError(f"unknown kind for listing: {kind!r}")
        yield from self._client.list_objects(object_type=automic_type, folder=folder)

    def object_exists(self, name: str) -> bool:
        """Cheap existence probe. True iff a GET for ``name`` returns 200."""
        return self._client.get_object_or_none(name) is not None


__all__ = ["AutomicAPI"]
