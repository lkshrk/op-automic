"""Manifest revision computation.

A manifest's *revision* is a content-addressed fingerprint of the
diff-relevant content: kind + spec in canonical form. Metadata (name,
folder, revision itself) and the server-populated ``status`` block are
excluded — they are either identity (already part of the ledger key)
or presentation (volatile, would thrash the revision).

The hash is computed over the canonical form used by the differ so two
manifests that produce an empty diff also produce the same revision.

Format: ``sha256:<64 hex chars>``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from op_aromic.engine.normalizer import to_canonical_from_manifest
from op_aromic.models.base import Manifest

_PREFIX = "sha256:"


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Produce deterministic bytes for ``payload``.

    ``sort_keys=True`` makes the output insensitive to dict insertion
    order; ``separators`` drops insignificant whitespace; UTF-8 makes the
    byte stream portable.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _hash_of_canonical(canonical: dict[str, Any]) -> str:
    # Drop identity fields from the hash input: name/folder/client are part
    # of the ledger key, not the content fingerprint. Keeping them would
    # make a rename look like a new revision and defeat cross-environment
    # promotion.
    content = {k: v for k, v in canonical.items() if k not in {"name", "folder", "client", "kind"}}
    # ``kind`` is re-added explicitly so two different kinds with the same
    # spec body never collide.
    content["kind"] = canonical.get("kind")
    digest = hashlib.sha256(_canonical_bytes(content)).hexdigest()
    return f"{_PREFIX}{digest}"


def compute_revision(manifest: Manifest) -> str:
    """Return the ``sha256:...`` revision for ``manifest``."""
    canonical = to_canonical_from_manifest(manifest)
    return _hash_of_canonical(canonical)


def compute_revision_from_canonical(canonical: dict[str, Any]) -> str:
    """Return the revision for an already-canonicalised dict.

    Used by the applier: the planner hands it ``ObjectDiff.desired`` in
    canonical form already, so going back to a Manifest just to re-hash
    would be wasted work.
    """
    return _hash_of_canonical(canonical)


def is_revision(value: str | None) -> bool:
    """True when ``value`` looks like a valid ``sha256:<hex>`` revision."""
    if not isinstance(value, str) or not value.startswith(_PREFIX):
        return False
    hexpart = value[len(_PREFIX):]
    return len(hexpart) == 64 and all(c in "0123456789abcdef" for c in hexpart)


__all__ = ["compute_revision", "compute_revision_from_canonical", "is_revision"]
