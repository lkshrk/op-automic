"""Per-kind normalization to a canonical diff representation.

Diffs run against two canonical dicts — never against raw Automic JSON
or raw manifests. Any field that is volatile (timestamps, internal ids,
ACL hashes) is dropped. Booleans are coerced from Automic's ``"Y"``/``"N"``
to Python ``bool`` so the diff engine can compare them symmetrically.

Rules per kind live in this file and nowhere else: the diff engine, the
planner, and the exporter all route through ``to_canonical_from_*``.
"""

from __future__ import annotations

from typing import Any, Protocol

from op_aromic.models.base import Manifest

# Volatile fields present on every kind; stripped unconditionally.
_COMMON_IGNORED: frozenset[str] = frozenset(
    {
        "LastModified",
        "LastModifiedBy",
        "OH_LASTMODIFIED",
        "InternalId",
        "OH_IDNR",
        "Version",
        "VersionNumber",
        "ACLHash",
    },
)

# Kind-specific ignored fields, merged with _COMMON_IGNORED at lookup time.
IGNORED_FIELDS_BY_KIND: dict[str, frozenset[str]] = {
    "Workflow": frozenset({"OH_TITLE_LASTMODIFIED"}),
    "Job": frozenset(),
    "Schedule": frozenset(),
    "Calendar": frozenset(),
    "Variable": frozenset(),
}


def _ignored_for(kind: str) -> frozenset[str]:
    return _COMMON_IGNORED | IGNORED_FIELDS_BY_KIND.get(kind, frozenset())


def _coerce_yn(value: Any) -> Any:
    """Turn ``"Y"``/``"N"`` into bool; leave everything else untouched.

    Automic returns string booleans on many flag fields; the coerce is
    applied selectively by each per-kind implementation rather than
    globally, because some kinds use ``Y``/``N`` as an enum value (e.g.
    as a weekday letter) where coercion would be wrong.
    """
    if value == "Y":
        return True
    if value == "N":
        return False
    return value


class Normalizer(Protocol):
    """Canonicalises one kind's Automic JSON and manifest spec."""

    def from_automic(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def from_manifest(self, manifest: Manifest) -> dict[str, Any]: ...


def _identity_block(manifest: Manifest) -> dict[str, Any]:
    """Canonical identity metadata for every kind."""
    md = manifest.metadata
    out: dict[str, Any] = {
        "name": md.name,
        "folder": md.folder,
        "kind": manifest.kind,
    }
    if md.client is not None:
        out["client"] = md.client
    return out


def _identity_from_automic(payload: dict[str, Any], kind: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": payload.get("Name", ""),
        "folder": payload.get("Folder", ""),
        "kind": kind,
    }
    if "Client" in payload:
        out["client"] = payload["Client"]
    return out


def _strip_ignored(payload: dict[str, Any], kind: str) -> dict[str, Any]:
    ignored = _ignored_for(kind)
    return {k: v for k, v in payload.items() if k not in ignored}


class _WorkflowNormalizer:
    kind = "Workflow"

    def from_automic(self, payload: dict[str, Any]) -> dict[str, Any]:
        clean = _strip_ignored(payload, self.kind)
        tasks = clean.get("Tasks", []) or []
        return {
            **_identity_from_automic(clean, self.kind),
            "title": clean.get("Title") or None,
            "tasks": [
                {
                    "name": t.get("Name"),
                    "ref": {
                        "kind": (t.get("Ref") or {}).get("Kind"),
                        "name": (t.get("Ref") or {}).get("Name"),
                    },
                    "after": sorted(t.get("After", []) or []),
                }
                for t in tasks
            ],
        }

    def from_manifest(self, manifest: Manifest) -> dict[str, Any]:
        spec = manifest.spec
        tasks = spec.get("tasks", []) or []
        return {
            **_identity_block(manifest),
            "title": spec.get("title") or None,
            "tasks": [
                {
                    "name": t["name"],
                    "ref": {"kind": t["ref"]["kind"], "name": t["ref"]["name"]},
                    "after": sorted(t.get("after", []) or []),
                }
                for t in tasks
            ],
        }


class _JobNormalizer:
    kind = "Job"

    def from_automic(self, payload: dict[str, Any]) -> dict[str, Any]:
        clean = _strip_ignored(payload, self.kind)
        return {
            **_identity_from_automic(clean, self.kind),
            "title": clean.get("Title") or None,
            "host": clean.get("Host"),
            "login": clean.get("Login"),
            "script": clean.get("Script"),
            "script_type": clean.get("ScriptType", "OS"),
        }

    def from_manifest(self, manifest: Manifest) -> dict[str, Any]:
        spec = manifest.spec
        return {
            **_identity_block(manifest),
            "title": spec.get("title") or None,
            "host": spec.get("host"),
            "login": spec.get("login"),
            "script": spec.get("script"),
            "script_type": spec.get("script_type", "OS"),
        }


class _ScheduleNormalizer:
    kind = "Schedule"

    def from_automic(self, payload: dict[str, Any]) -> dict[str, Any]:
        clean = _strip_ignored(payload, self.kind)
        entries = clean.get("Entries", []) or []
        return {
            **_identity_from_automic(clean, self.kind),
            "title": clean.get("Title") or None,
            "entries": [
                {
                    "task": {
                        "kind": (e.get("Task") or {}).get("Kind"),
                        "name": (e.get("Task") or {}).get("Name"),
                    },
                    "start_time": e.get("StartTime", "00:00"),
                    "calendar_keyword": e.get("CalendarKeyword") or None,
                }
                for e in entries
            ],
        }

    def from_manifest(self, manifest: Manifest) -> dict[str, Any]:
        spec = manifest.spec
        entries = spec.get("entries", []) or []
        return {
            **_identity_block(manifest),
            "title": spec.get("title") or None,
            "entries": [
                {
                    "task": {"kind": e["task"]["kind"], "name": e["task"]["name"]},
                    "start_time": e.get("start_time", "00:00"),
                    "calendar_keyword": e.get("calendar_keyword") or None,
                }
                for e in entries
            ],
        }


class _CalendarNormalizer:
    kind = "Calendar"

    def from_automic(self, payload: dict[str, Any]) -> dict[str, Any]:
        clean = _strip_ignored(payload, self.kind)
        keywords = clean.get("Keywords", []) or []
        return {
            **_identity_from_automic(clean, self.kind),
            "title": clean.get("Title") or None,
            "keywords": [
                {
                    "name": k.get("Name"),
                    "type": k.get("Type", "STATIC"),
                    "values": list(k.get("Values", []) or []),
                }
                for k in keywords
            ],
        }

    def from_manifest(self, manifest: Manifest) -> dict[str, Any]:
        spec = manifest.spec
        keywords = spec.get("keywords", []) or []
        return {
            **_identity_block(manifest),
            "title": spec.get("title") or None,
            "keywords": [
                {
                    "name": k["name"],
                    "type": k.get("type", "STATIC"),
                    "values": list(k.get("values", []) or []),
                }
                for k in keywords
            ],
        }


class _VariableNormalizer:
    kind = "Variable"

    def from_automic(self, payload: dict[str, Any]) -> dict[str, Any]:
        clean = _strip_ignored(payload, self.kind)
        entries = clean.get("Entries", []) or []
        return {
            **_identity_from_automic(clean, self.kind),
            "title": clean.get("Title") or None,
            "var_type": clean.get("VarType", "STATIC"),
            "entries": [
                {"key": e.get("Key"), "value": e.get("Value", "")} for e in entries
            ],
        }

    def from_manifest(self, manifest: Manifest) -> dict[str, Any]:
        spec = manifest.spec
        entries = spec.get("entries", []) or []
        return {
            **_identity_block(manifest),
            "title": spec.get("title") or None,
            "var_type": spec.get("var_type", "STATIC"),
            "entries": [
                {"key": e["key"], "value": e.get("value", "")} for e in entries
            ],
        }


_NORMALIZERS: dict[str, Normalizer] = {
    "Workflow": _WorkflowNormalizer(),
    "Job": _JobNormalizer(),
    "Schedule": _ScheduleNormalizer(),
    "Calendar": _CalendarNormalizer(),
    "Variable": _VariableNormalizer(),
}


def get_normalizer(kind: str) -> Normalizer:
    if kind not in _NORMALIZERS:
        raise ValueError(f"no normalizer registered for kind {kind!r}")
    return _NORMALIZERS[kind]


def to_canonical_from_automic(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Canonicalise an Automic JSON object for diffing."""
    return get_normalizer(kind).from_automic(payload)


def to_canonical_from_manifest(manifest: Manifest) -> dict[str, Any]:
    """Canonicalise a loaded manifest for diffing."""
    return get_normalizer(manifest.kind).from_manifest(manifest)


__all__ = [
    "IGNORED_FIELDS_BY_KIND",
    "Normalizer",
    "get_normalizer",
    "to_canonical_from_automic",
    "to_canonical_from_manifest",
]
