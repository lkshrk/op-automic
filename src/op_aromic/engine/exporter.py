"""Bootstrap YAML manifests from an existing Automic client.

The exporter pulls live Automic objects, rebuilds :class:`Manifest`
instances from their JSON payloads, groups them per a caller-chosen
layout, and writes them via :mod:`yaml_writer` so the output is
deterministic.

Correctness contract (Phase 4 quality bar):

    export → validate → plan against the same Automic state ⇒ empty
    changeset

That contract is what :mod:`tests.engine.test_round_trip` enforces. To
deliver it we:

- Translate Automic JSON into the modelled spec fields (inverse of
  :mod:`engine.serializer`), stripping volatile/ignored fields first.
- Dump every unmodelled Automic field into ``spec.raw`` so nothing is
  silently lost — losing data would break the round-trip.
- Write via a deterministic YAML writer so byte-level stability is
  guaranteed regardless of dict iteration order on the wire.

None of this touches :mod:`engine.normalizer` or :mod:`engine.serializer`;
if a reverse direction is needed it lives here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from op_aromic.client.api import AutomicAPI
from op_aromic.engine.normalizer import IGNORED_FIELDS_BY_KIND
from op_aromic.engine.yaml_writer import write_manifests_to_file
from op_aromic.models.base import KIND_REGISTRY, Manifest

Layout = Literal["by-folder", "by-kind", "flat"]

# All kinds we currently model. Exporter iterates these when ``kinds`` is
# None. Ordering follows the apply precedence so files written look the way
# a human reading them would expect.
_ALL_KINDS: tuple[str, ...] = ("Calendar", "Variable", "Job", "Schedule", "Workflow")

# Shared volatile-field list with the normalizer so both sides agree on
# what "internal" means. Re-exported here for clarity.
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

# Envelope fields handled explicitly when rebuilding the manifest; never
# leak back into ``spec.raw``.
_ENVELOPE_FIELDS: frozenset[str] = frozenset(
    {"Name", "Type", "Folder", "Client", "Annotations"},
)


@dataclass(frozen=True)
class ExportResult:
    """What the exporter did. Every field is serialisable for CI output.

    - ``files_written``: absolute paths of files that received YAML.
    - ``objects_exported``: count of Automic objects successfully converted
      and written (not counting skipped ones).
    - ``skipped``: ``(kind, name, reason)`` triples — objects the exporter
      refused to touch, e.g. because a pre-existing non-empty file was in
      the way and ``overwrite=False``.
    """

    files_written: list[Path] = field(default_factory=list)
    objects_exported: int = 0
    skipped: list[tuple[str, str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Automic JSON → Manifest (inverse of serializer.manifest_to_automic_payload)
# ---------------------------------------------------------------------------


def _extract_raw(
    payload: dict[str, Any],
    *,
    modelled: Iterable[str],
    kind: str,
) -> dict[str, Any]:
    """Return a dict of Automic fields not modelled on the kind's spec.

    ``modelled`` is the set of Automic field names the per-kind inverse
    already consumes explicitly. Everything else — minus the envelope and
    volatile fields — lands in ``spec.raw`` so the round-trip survives
    even for fields we have not formally typed.
    """
    ignore = (
        _COMMON_IGNORED
        | IGNORED_FIELDS_BY_KIND.get(kind, frozenset())
        | _ENVELOPE_FIELDS
        | frozenset(modelled)
    )
    return {k: v for k, v in payload.items() if k not in ignore}


def _manifest_envelope(payload: dict[str, Any], kind: str) -> dict[str, Any]:
    """Reconstruct the top-level manifest envelope (apiVersion + metadata)."""
    metadata: dict[str, Any] = {
        "name": payload["Name"],
        "folder": payload.get("Folder", ""),
    }
    # ``Client`` is optional on the wire — only surface it when the server
    # echoed one, otherwise the round-trip would introduce a spurious diff.
    if "Client" in payload:
        metadata["client"] = payload["Client"]
    annotations = payload.get("Annotations")
    if isinstance(annotations, dict) and annotations:
        metadata["annotations"] = {str(k): str(v) for k, v in annotations.items()}

    return {
        "apiVersion": "aromic.io/v1",
        "kind": kind,
        "metadata": metadata,
    }


def _inverse_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    tasks_in = payload.get("Tasks", []) or []
    spec: dict[str, Any] = {}
    title = payload.get("Title")
    if title:
        spec["title"] = title
    spec["tasks"] = [
        {
            "name": t.get("Name"),
            "ref": {
                "kind": (t.get("Ref") or {}).get("Kind"),
                "name": (t.get("Ref") or {}).get("Name"),
            },
            "after": list(t.get("After", []) or []),
        }
        for t in tasks_in
    ]
    raw = _extract_raw(payload, modelled={"Title", "Tasks"}, kind="Workflow")
    if raw:
        spec["raw"] = raw
    return spec


def _inverse_job(payload: dict[str, Any]) -> dict[str, Any]:
    spec: dict[str, Any] = {}
    if payload.get("Title"):
        spec["title"] = payload["Title"]
    spec["host"] = payload.get("Host", "")
    spec["login"] = payload.get("Login", "")
    spec["script"] = payload.get("Script", "")
    spec["script_type"] = payload.get("ScriptType", "OS")
    raw = _extract_raw(
        payload,
        modelled={"Title", "Host", "Login", "Script", "ScriptType"},
        kind="Job",
    )
    if raw:
        spec["raw"] = raw
    return spec


def _inverse_schedule(payload: dict[str, Any]) -> dict[str, Any]:
    entries_in = payload.get("Entries", []) or []
    spec: dict[str, Any] = {}
    if payload.get("Title"):
        spec["title"] = payload["Title"]
    spec["entries"] = [
        {
            "task": {
                "kind": (e.get("Task") or {}).get("Kind"),
                "name": (e.get("Task") or {}).get("Name"),
            },
            "start_time": e.get("StartTime", "00:00"),
            "calendar_keyword": e.get("CalendarKeyword") or None,
        }
        for e in entries_in
    ]
    raw = _extract_raw(payload, modelled={"Title", "Entries"}, kind="Schedule")
    if raw:
        spec["raw"] = raw
    return spec


def _inverse_calendar(payload: dict[str, Any]) -> dict[str, Any]:
    keywords_in = payload.get("Keywords", []) or []
    spec: dict[str, Any] = {}
    if payload.get("Title"):
        spec["title"] = payload["Title"]
    spec["keywords"] = [
        {
            "name": k.get("Name"),
            "type": k.get("Type", "STATIC"),
            "values": list(k.get("Values", []) or []),
        }
        for k in keywords_in
    ]
    raw = _extract_raw(payload, modelled={"Title", "Keywords"}, kind="Calendar")
    if raw:
        spec["raw"] = raw
    return spec


def _inverse_variable(payload: dict[str, Any]) -> dict[str, Any]:
    entries_in = payload.get("Entries", []) or []
    spec: dict[str, Any] = {}
    if payload.get("Title"):
        spec["title"] = payload["Title"]
    spec["var_type"] = payload.get("VarType", "STATIC")
    spec["entries"] = [
        {"key": e.get("Key"), "value": e.get("Value", "")} for e in entries_in
    ]
    raw = _extract_raw(
        payload, modelled={"Title", "VarType", "Entries"}, kind="Variable",
    )
    if raw:
        spec["raw"] = raw
    return spec


_INVERSES = {
    "Workflow": _inverse_workflow,
    "Job": _inverse_job,
    "Schedule": _inverse_schedule,
    "Calendar": _inverse_calendar,
    "Variable": _inverse_variable,
}


def _payload_to_manifest(kind: str, payload: dict[str, Any]) -> Manifest:
    """Assemble a fully validated :class:`Manifest` from an Automic payload."""
    if kind not in _INVERSES:
        raise ValueError(f"no inverse serializer for kind {kind!r}")
    envelope = _manifest_envelope(payload, kind)
    envelope["spec"] = _INVERSES[kind](payload)
    return Manifest.model_validate(envelope)


# ---------------------------------------------------------------------------
# Layout strategies: decide which file each manifest is written to
# ---------------------------------------------------------------------------


def _normalise_folder(folder: str) -> tuple[str, ...]:
    """Split a ``/`` folder path into non-empty segments for FS composition."""
    parts = tuple(p for p in folder.split("/") if p)
    return parts or ("ROOT",)


def _flat_filename(folder: str) -> str:
    """Filename for the ``flat`` layout: folder path encoded once per file."""
    parts = _normalise_folder(folder)
    return "__".join(parts) + ".yaml"


def _path_for(output_dir: Path, manifest: Manifest, layout: Layout) -> Path:
    folder = manifest.metadata.folder
    parts = _normalise_folder(folder)
    if layout == "by-folder":
        # Mirror the Automic folder tree; one file per folder.
        leaf = parts[-1]
        return output_dir.joinpath(*parts[:-1], leaf, f"{leaf}.yaml")
    if layout == "by-kind":
        # e.g. "workflows/ETL.DAILY.yaml"
        kind_dir = f"{manifest.kind.lower()}s"
        return output_dir / kind_dir / f"{manifest.metadata.name}.yaml"
    if layout == "flat":
        return output_dir / _flat_filename(folder)
    raise ValueError(f"unknown layout: {layout!r}")


def _group_by_path(
    manifests: list[Manifest],
    output_dir: Path,
    layout: Layout,
) -> dict[Path, list[Manifest]]:
    """Bucket manifests by their target file so writes happen once per file."""
    groups: dict[Path, list[Manifest]] = {}
    for m in manifests:
        key = _path_for(output_dir, m, layout)
        groups.setdefault(key, []).append(m)
    return groups


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def export(
    api: AutomicAPI,
    output_dir: Path,
    *,
    kinds: list[str] | None = None,
    folders: list[str] | None = None,
    layout: Layout = "by-folder",
    overwrite: bool = False,
) -> ExportResult:
    """Pull objects from Automic and write them as YAML manifests.

    Parameters:
        api: read-only API façade; the exporter never mutates state.
        output_dir: filesystem root for the generated files. Created if
            missing.
        kinds: if given, restrict to these manifest kinds. Otherwise every
            kind in ``KIND_REGISTRY`` is pulled.
        folders: if given, only list/export objects whose Automic folder
            path is in this list. When omitted, every folder is included.
        layout: "by-folder" mirrors the Automic tree, "by-kind" groups by
            manifest kind, "flat" emits one file per folder with no nesting.
        overwrite: when False (default), any pre-existing non-empty target
            file causes its objects to be listed in :attr:`ExportResult.skipped`
            instead of being overwritten.

    Returns: :class:`ExportResult` describing what happened.
    """
    if layout not in {"by-folder", "by-kind", "flat"}:
        raise ValueError(f"unknown layout: {layout!r}")

    requested_kinds = kinds or list(_ALL_KINDS)
    for k in requested_kinds:
        if k not in KIND_REGISTRY:
            raise ValueError(f"unknown kind: {k!r}")

    folder_set = set(folders) if folders else None

    manifests: list[Manifest] = []
    skipped: list[tuple[str, str, str]] = []

    for kind in requested_kinds:
        # Apply folder filter server-side when possible; when multiple
        # folders are requested we list per-folder so the server can filter.
        listed: list[dict[str, Any]] = []
        if folder_set:
            for folder in folder_set:
                listed.extend(api.list_objects_typed(kind, folder=folder))
        else:
            listed.extend(api.list_objects_typed(kind))

        for summary in listed:
            name = summary.get("Name")
            if not isinstance(name, str):
                continue
            full = api.get_object_typed(kind, name)
            if full is None:
                # Race: object disappeared between list and get. Not fatal;
                # just record and move on.
                skipped.append((kind, name, "object disappeared mid-export"))
                continue
            # Client-side folder filter as a safety net in case the server
            # ignored the ``folder`` query parameter.
            if folder_set and full.get("Folder") not in folder_set:
                continue
            manifests.append(_payload_to_manifest(kind, full))

    groups = _group_by_path(manifests, output_dir, layout)

    files_written: list[Path] = []
    exported = 0
    for target, bucket in groups.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size > 0 and not overwrite:
            for m in bucket:
                skipped.append((m.kind, m.metadata.name, f"file exists: {target}"))
            continue
        write_manifests_to_file(target, bucket)
        files_written.append(target)
        exported += len(bucket)

    return ExportResult(
        files_written=files_written,
        objects_exported=exported,
        skipped=skipped,
    )


__all__ = ["ExportResult", "Layout", "export"]
