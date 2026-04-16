"""Tests for the stable YAML writer used by the exporter.

The writer must produce deterministic, byte-identical output across calls
and must lay out keys in Pydantic declaration order so git diffs are
stable and human-editable.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from op_aromic.engine.yaml_writer import write_manifests_to_file
from op_aromic.models.base import Manifest


def _calendar_manifest() -> Manifest:
    return Manifest.model_validate(
        {
            "apiVersion": "aromic.io/v1",
            "kind": "Calendar",
            "metadata": {"name": "WORK.DAYS", "folder": "/PROD/CAL"},
            "spec": {
                "title": "Workdays",
                "keywords": [
                    {"name": "WEEKDAY", "type": "WEEKDAY", "values": ["MON", "TUE"]},
                ],
            },
        },
    )


def _workflow_manifest() -> Manifest:
    return Manifest.model_validate(
        {
            "apiVersion": "aromic.io/v1",
            "kind": "Workflow",
            "metadata": {
                "name": "ETL.DAILY",
                "folder": "/PROD/ETL",
                "annotations": {"aromic.io/managed-by": "op-aromic"},
            },
            "spec": {
                "title": "Daily ETL",
                "tasks": [
                    {"name": "S1", "ref": {"kind": "Job", "name": "J1"}, "after": []},
                ],
            },
        },
    )


def test_write_is_byte_identical_across_calls(tmp_path: Path) -> None:
    path_a = tmp_path / "a.yaml"
    path_b = tmp_path / "b.yaml"
    manifests = [_calendar_manifest(), _workflow_manifest()]
    write_manifests_to_file(path_a, manifests)
    write_manifests_to_file(path_b, manifests)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_keys_follow_pydantic_declaration_order(tmp_path: Path) -> None:
    path = tmp_path / "out.yaml"
    write_manifests_to_file(path, [_calendar_manifest()])
    text = path.read_text()
    # Top-level keys: apiVersion, kind, metadata, spec — in declaration order.
    idx_api = text.index("apiVersion")
    idx_kind = text.index("kind:")
    idx_meta = text.index("metadata:")
    idx_spec = text.index("spec:")
    assert idx_api < idx_kind < idx_meta < idx_spec
    # Metadata: name before folder.
    idx_name = text.index("name:")
    idx_folder = text.index("folder:")
    assert idx_name < idx_folder
    # Spec fields for Calendar: title before keywords.
    idx_title = text.index("title:")
    idx_keywords = text.index("keywords:")
    assert idx_title < idx_keywords


def test_multi_doc_separator(tmp_path: Path) -> None:
    path = tmp_path / "multi.yaml"
    write_manifests_to_file(path, [_calendar_manifest(), _workflow_manifest()])
    text = path.read_text()
    # First doc may start with '---', second doc must be separated by it.
    assert text.count("\n---\n") + (1 if text.startswith("---\n") else 0) >= 1
    # Round-trip: yaml.safe_load_all should return exactly two docs.
    docs = [d for d in yaml.safe_load_all(text) if d is not None]
    assert len(docs) == 2


def test_no_flow_style_no_anchors(tmp_path: Path) -> None:
    path = tmp_path / "style.yaml"
    # Use a manifest with non-empty nested lists / dicts.
    write_manifests_to_file(path, [_calendar_manifest()])
    text = path.read_text()
    # Non-empty flow sequences / mappings use [a, b, c] or {a: b}; we must
    # never emit those for lists with content. Empty `[]`/`{}` are allowed
    # (PyYAML's canonical representation for empty containers).
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.endswith(": []") or stripped.endswith(": {}"):
            continue
        assert ": [" not in stripped, f"flow list leaked: {line!r}"
        assert ": {" not in stripped, f"flow map leaked: {line!r}"
    # No anchors / aliases.
    assert "&" not in text
    assert " *" not in text


def test_trailing_newline(tmp_path: Path) -> None:
    path = tmp_path / "nl.yaml"
    write_manifests_to_file(path, [_calendar_manifest()])
    assert path.read_bytes().endswith(b"\n")


def test_round_trip_load_then_rewrite_is_stable(tmp_path: Path) -> None:
    path_one = tmp_path / "one.yaml"
    path_two = tmp_path / "two.yaml"
    manifests = [_calendar_manifest(), _workflow_manifest()]
    write_manifests_to_file(path_one, manifests)

    # Load what we wrote and rewrite. Byte equality should hold because the
    # writer is deterministic and field order is derived from the model, not
    # from dict iteration order of the loaded YAML.
    reloaded: list[Manifest] = []
    for doc in yaml.safe_load_all(path_one.read_text()):
        if doc is None:
            continue
        reloaded.append(Manifest.model_validate(doc))
    write_manifests_to_file(path_two, reloaded)
    assert path_one.read_bytes() == path_two.read_bytes()
