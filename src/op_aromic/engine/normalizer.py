"""Per-kind normalization to a canonical diff representation.

Diffs run against two canonical dicts — never against raw Automic JSON
or raw manifests. Any field that is volatile (timestamps, internal ids,
ACL hashes) is dropped. Booleans are coerced from Automic's ``"Y"``/``"N"``
to Python ``bool`` so the diff engine can compare them symmetrically.

Rules per kind live in this file and nowhere else: the diff engine, the
planner, and the exporter all route through ``to_canonical_from_*``.

v21 shape support
-----------------
Automic AE REST v21 returns objects in a nested structure::

    {
        "metadata": {"version": "21.0.0"},
        "general_attributes": {"name": "...", "type": "...", ...},
        "<kind>_attributes": {...},
        ...kind-specific arrays...
    }

The ``_envelope_path`` synthetic key (injected by ``client/api.py``) carries
the Automic folder path from the response envelope.  Legacy flat responses
(``{"Name": ..., "Folder": ...}``) are also accepted so existing synthetic
test fixtures continue to work.

Detection: ``"general_attributes" in payload`` → v21 nested; otherwise → flat.
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

# Synthetic key injected by api.py to carry the envelope folder path.
_ENVELOPE_PATH_KEY = "_envelope_path"


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


def _coerce_01(value: Any) -> Any:
    """Turn ``"1"``/``"0"`` into bool; leave everything else untouched.

    Some v21 boolean fields use ``"1"``/``"0"`` string encoding rather than
    ``"Y"``/``"N"``.
    """
    if value == "1":
        return True
    if value == "0":
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


def _is_v21_nested(payload: dict[str, Any]) -> bool:
    """Return True when *payload* has the v21 nested structure."""
    return "general_attributes" in payload


def _identity_from_automic(payload: dict[str, Any], kind: str) -> dict[str, Any]:
    """Extract identity fields from either v21 nested or legacy flat payload."""
    if _is_v21_nested(payload):
        ga = payload.get("general_attributes") or {}
        name = ga.get("name", "")
        # Folder comes from the synthetic envelope-path key injected by api.py.
        folder = payload.get(_ENVELOPE_PATH_KEY, "")
    else:
        # Legacy flat shape.
        name = payload.get("Name", "")
        folder = payload.get("Folder", "")

    out: dict[str, Any] = {
        "name": name,
        "folder": folder,
        "kind": kind,
    }
    # Client present only on v21 flat (legacy synthetic fixtures include it rarely).
    client_val = payload.get("Client")
    if client_val is not None:
        out["client"] = client_val
    return out


def _strip_ignored(payload: dict[str, Any], kind: str) -> dict[str, Any]:
    ignored = _ignored_for(kind) | {_ENVELOPE_PATH_KEY}
    return {k: v for k, v in payload.items() if k not in ignored}


class _WorkflowNormalizer:
    kind = "Workflow"

    def from_automic(self, payload: dict[str, Any]) -> dict[str, Any]:
        identity = _identity_from_automic(payload, self.kind)
        if _is_v21_nested(payload):
            ga = payload.get("general_attributes") or {}
            title = ga.get("title") or None
            # workflow_definitions carries task nodes; <START> and <END>
            # pseudo-nodes are structural sentinels — strip them so the
            # canonical form contains only real object references.
            raw_tasks = payload.get("workflow_definitions") or []
            tasks = [
                t for t in raw_tasks
                if t.get("object_type") not in ("<START>", "<END>")
            ]
            # line_conditions describe predecessor edges between tasks.
            # Build the "after" list per task: for each task's line_number,
            # find all predecessor_line_numbers from line_conditions, then
            # map those line numbers back to object names.
            line_map: dict[str, str] = {
                str(t.get("line_number", "")): t.get("object_name", "")
                for t in tasks
            }
            cond_map: dict[str, list[str]] = {}
            for lc in (payload.get("line_conditions") or []):
                wln = str(lc.get("workflow_line_number", ""))
                pred = str(lc.get("predecessor_line_number", ""))
                if wln and pred and pred in line_map:
                    cond_map.setdefault(wln, []).append(line_map[pred])

            return {
                **identity,
                "title": title,
                "tasks": [
                    {
                        "name": t.get("object_name", ""),
                        "ref": {
                            "kind": _AUTOMIC_TYPE_TO_KIND.get(
                                t.get("object_type", ""), t.get("object_type", "")
                            ),
                            "name": t.get("object_name", ""),
                        },
                        "after": sorted(
                            cond_map.get(str(t.get("line_number", "")), [])
                        ),
                    }
                    for t in tasks
                ],
            }
        # Legacy flat shape.
        clean = _strip_ignored(payload, self.kind)
        tasks = clean.get("Tasks", []) or []
        return {
            **identity,
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
        identity = _identity_from_automic(payload, self.kind)
        if _is_v21_nested(payload):
            ga = payload.get("general_attributes") or {}
            ja = payload.get("job_attributes") or {}
            # Script lines live in the scripts array; process lines form the
            # main script body.
            scripts_block = payload.get("scripts") or []
            script_lines: list[str] = []
            for block in scripts_block:
                if isinstance(block, dict):
                    process = block.get("process")
                    if isinstance(process, list):
                        script_lines.extend(str(ln) for ln in process)
                    elif isinstance(process, str):
                        script_lines.append(process)
            script = "\n".join(script_lines) if script_lines else None
            return {
                **identity,
                "title": ga.get("title") or None,
                "host": ja.get("agent") or None,
                "login": ja.get("login") or None,
                "script": script,
                "script_type": "OS",
            }
        # Legacy flat shape.
        clean = _strip_ignored(payload, self.kind)
        return {
            **identity,
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
        identity = _identity_from_automic(payload, self.kind)
        if _is_v21_nested(payload):
            ga = payload.get("general_attributes") or {}
            # schedule_definitions carries task entries; shape is best-effort
            # (JSCH real fixture not available in swagger examples).
            raw_entries = payload.get("schedule_definitions") or []
            return {
                **identity,
                "title": ga.get("title") or None,
                "entries": [
                    {
                        "task": {
                            "kind": _AUTOMIC_TYPE_TO_KIND.get(
                                e.get("object_type", ""), e.get("object_type", "")
                            ),
                            "name": e.get("object_name", ""),
                        },
                        "start_time": _normalise_time(e.get("start_time", "000000")),
                        "calendar_keyword": e.get("calendar_keyword") or None,
                    }
                    for e in raw_entries
                ],
            }
        # Legacy flat shape.
        clean = _strip_ignored(payload, self.kind)
        entries = clean.get("Entries", []) or []
        return {
            **identity,
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
        identity = _identity_from_automic(payload, self.kind)
        if _is_v21_nested(payload):
            ga = payload.get("general_attributes") or {}
            # calendar_definitions carries keyword entries; shape is best-effort
            # (CALE real fixture not available in swagger examples).
            raw_keywords = payload.get("calendar_definitions") or []
            return {
                **identity,
                "title": ga.get("title") or None,
                "keywords": [
                    {
                        "name": k.get("keyword", k.get("name", "")),
                        "type": k.get("type", "STATIC"),
                        "values": list(k.get("entries", k.get("values", [])) or []),
                    }
                    for k in raw_keywords
                ],
            }
        # Legacy flat shape.
        clean = _strip_ignored(payload, self.kind)
        keywords = clean.get("Keywords", []) or []
        return {
            **identity,
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
        identity = _identity_from_automic(payload, self.kind)
        if _is_v21_nested(payload):
            ga = payload.get("general_attributes") or {}
            var_def = payload.get("variable_definitions") or {}
            var_type = ga.get("sub_type") or var_def.get("type", "STATIC")
            # column_count tells how many value columns exist per row.
            # For STATIC: fixed at column_count (1-5). For dynamic: varies.
            col_count = int(var_def.get("column_count", "1") or "1")
            raw_rows = payload.get("static_values") or []
            entries = []
            for row in raw_rows:
                key = row.get("key", "")
                # Collect value1..valueN up to col_count.
                values = [
                    str(row.get(f"value{i}", "") or "")
                    for i in range(1, col_count + 1)
                ]
                # Canonical form: single-column → plain "value", multi-column → list.
                if col_count == 1:
                    entries.append({"key": key, "value": values[0] if values else ""})
                else:
                    entries.append({"key": key, "values": values})
            return {
                **identity,
                "title": ga.get("title") or None,
                "var_type": var_type,
                "entries": entries,
            }
        # Legacy flat shape.
        clean = _strip_ignored(payload, self.kind)
        entries_raw = clean.get("Entries", []) or []
        return {
            **identity,
            "title": clean.get("Title") or None,
            "var_type": clean.get("VarType", "STATIC"),
            "entries": [
                {"key": e.get("Key"), "value": e.get("Value", "")} for e in entries_raw
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Map Automic object type → manifest kind (inverse of serializer._KIND_TO_TYPE).
_AUTOMIC_TYPE_TO_KIND: dict[str, str] = {
    "JOBP": "Workflow",
    "JOBS": "Job",
    "JSCH": "Schedule",
    "CALE": "Calendar",
    "VARA": "Variable",
    "SCRI": "Script",
}


def _normalise_time(raw: str) -> str:
    """Convert Automic HHMMSS time string to HH:MM.

    Automic stores times as 6-digit strings (``"020000"`` = 02:00).
    Manifests use ``"HH:MM"`` format; already-normalised strings pass
    through unchanged.
    """
    s = str(raw or "").strip()
    if len(s) == 6 and s.isdigit():
        return f"{s[0:2]}:{s[2:4]}"
    # Already HH:MM or unknown — return as-is.
    return s


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

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
