"""Manifest → Automic-native payload.

Inverse of ``normalizer.to_canonical_from_manifest``. Emits the Automic
JSON shape the REST API expects on create/update: PascalCase field names,
``"Y"``/``"N"`` booleans, raw references as ``{"Kind": ..., "Name": ...}``
so Automic can resolve them on its side.

Kept per-kind so each kind's wire shape is explicit; no generic recursion
that pretends to work for every Automic object.

The wire shapes used here are a defensible default synthesised from
Broadcom AE REST docs. They are not verified against a live instance —
see docs/ISSUES.md "Automic JSON field shape".
"""

from __future__ import annotations

from typing import Any

from op_aromic.models.base import Manifest

# Manifest kind → Automic object type string carried in the payload envelope.
_KIND_TO_TYPE: dict[str, str] = {
    "Workflow": "JOBP",
    "Job": "JOBS",
    "Schedule": "JSCH",
    "Calendar": "CALE",
    "Variable": "VARA",
}


def _bool_yn(value: bool) -> str:
    return "Y" if value else "N"


def _base_envelope(manifest: Manifest) -> dict[str, Any]:
    automic_type = _KIND_TO_TYPE[manifest.kind]
    envelope: dict[str, Any] = {
        "Name": manifest.metadata.name,
        "Type": automic_type,
        "Folder": manifest.metadata.folder,
    }
    if manifest.metadata.client is not None:
        envelope["Client"] = manifest.metadata.client
    return envelope


def _serialize_workflow(manifest: Manifest) -> dict[str, Any]:
    spec = manifest.spec
    tasks = spec.get("tasks", []) or []
    return {
        **_base_envelope(manifest),
        "Title": spec.get("title") or "",
        "Tasks": [
            {
                "Name": t["name"],
                "Ref": {"Kind": t["ref"]["kind"], "Name": t["ref"]["name"]},
                "After": list(t.get("after", [])),
            }
            for t in tasks
        ],
        **spec.get("raw", {}),
    }


def _serialize_job(manifest: Manifest) -> dict[str, Any]:
    spec = manifest.spec
    return {
        **_base_envelope(manifest),
        "Title": spec.get("title") or "",
        "Host": spec["host"],
        "Login": spec["login"],
        "Script": spec["script"],
        "ScriptType": spec.get("script_type", "OS"),
        **spec.get("raw", {}),
    }


def _serialize_schedule(manifest: Manifest) -> dict[str, Any]:
    spec = manifest.spec
    entries = spec.get("entries", []) or []
    return {
        **_base_envelope(manifest),
        "Title": spec.get("title") or "",
        "Entries": [
            {
                "Task": {"Kind": e["task"]["kind"], "Name": e["task"]["name"]},
                "StartTime": e.get("start_time", "00:00"),
                "CalendarKeyword": e.get("calendar_keyword") or "",
            }
            for e in entries
        ],
        **spec.get("raw", {}),
    }


def _serialize_calendar(manifest: Manifest) -> dict[str, Any]:
    spec = manifest.spec
    keywords = spec.get("keywords", []) or []
    return {
        **_base_envelope(manifest),
        "Title": spec.get("title") or "",
        "Keywords": [
            {
                "Name": k["name"],
                "Type": k.get("type", "STATIC"),
                "Values": list(k.get("values", [])),
            }
            for k in keywords
        ],
        **spec.get("raw", {}),
    }


def _serialize_variable(manifest: Manifest) -> dict[str, Any]:
    spec = manifest.spec
    entries = spec.get("entries", []) or []
    return {
        **_base_envelope(manifest),
        "Title": spec.get("title") or "",
        "VarType": spec.get("var_type", "STATIC"),
        "Entries": [
            {"Key": e["key"], "Value": e.get("value", "")} for e in entries
        ],
        **spec.get("raw", {}),
    }


_SERIALIZERS = {
    "Workflow": _serialize_workflow,
    "Job": _serialize_job,
    "Schedule": _serialize_schedule,
    "Calendar": _serialize_calendar,
    "Variable": _serialize_variable,
}


def manifest_to_automic_payload(manifest: Manifest) -> dict[str, Any]:
    """Produce the Automic-native POST/PUT body for a manifest."""
    serializer = _SERIALIZERS.get(manifest.kind)
    if serializer is None:
        raise ValueError(f"no serializer registered for kind {manifest.kind!r}")
    return serializer(manifest)


# Re-exported for use by the normalizer / differ.
__all__ = ["_bool_yn", "manifest_to_automic_payload"]
