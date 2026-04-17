"""Normalizer tests for Workflow (JOBP) — v21 nested shape.

Tests:
- from_automic against real fixture (tests/fixtures/automic/real/workflow_JOBP.json)
- from_manifest against examples/workflow.yaml
- Round-trip: both produce structurally equivalent canonical dicts for
  semantically matching inputs
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from op_aromic.engine.normalizer import to_canonical_from_automic, to_canonical_from_manifest
from op_aromic.models.base import Manifest

REAL_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "automic" / "real"
EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples"


def _load_real_fixture(name: str) -> dict:
    return json.loads((REAL_FIXTURES / name).read_text())


def _load_manifest(name: str) -> Manifest:
    raw = yaml.safe_load((EXAMPLES_DIR / name).read_text())
    return Manifest.model_validate(raw)


def _inner(fixture_data: dict, key: str) -> dict:
    """Extract inner object from v21 envelope."""
    return fixture_data["data"][key]


# ---------------------------------------------------------------------------
# from_automic: real fixture
# ---------------------------------------------------------------------------


def test_workflow_from_automic_real_fixture_name() -> None:
    """Name is read from general_attributes.name."""
    fixture = _load_real_fixture("workflow_JOBP.json")
    inner = _inner(fixture, "jobp")
    inner["_envelope_path"] = fixture["path"]
    canonical = to_canonical_from_automic("Workflow", inner)
    assert canonical["name"] == "WORKFLOW"


def test_workflow_from_automic_real_fixture_folder() -> None:
    """Folder is read from _envelope_path."""
    fixture = _load_real_fixture("workflow_JOBP.json")
    inner = _inner(fixture, "jobp")
    inner["_envelope_path"] = fixture["path"]
    canonical = to_canonical_from_automic("Workflow", inner)
    assert canonical["folder"] == "EXAMPLES/WORKFLOWS"


def test_workflow_from_automic_real_fixture_kind() -> None:
    fixture = _load_real_fixture("workflow_JOBP.json")
    inner = _inner(fixture, "jobp")
    canonical = to_canonical_from_automic("Workflow", inner)
    assert canonical["kind"] == "Workflow"


def test_workflow_from_automic_real_fixture_tasks_excludes_sentinels() -> None:
    """START and END pseudo-nodes must be excluded from tasks."""
    fixture = _load_real_fixture("workflow_JOBP.json")
    inner = _inner(fixture, "jobp")
    canonical = to_canonical_from_automic("Workflow", inner)
    task_names = [t["name"] for t in canonical["tasks"]]
    assert "START" not in task_names
    assert "END" not in task_names


def test_workflow_from_automic_real_fixture_tasks_present() -> None:
    """Real fixture has 3 non-sentinel tasks."""
    fixture = _load_real_fixture("workflow_JOBP.json")
    inner = _inner(fixture, "jobp")
    canonical = to_canonical_from_automic("Workflow", inner)
    assert len(canonical["tasks"]) == 3


def test_workflow_from_automic_real_fixture_task_names() -> None:
    fixture = _load_real_fixture("workflow_JOBP.json")
    inner = _inner(fixture, "jobp")
    canonical = to_canonical_from_automic("Workflow", inner)
    task_names = {t["name"] for t in canonical["tasks"]}
    assert task_names == {"SIMPLE_SCRIPT", "WINDOWS_JOB", "ADVANCED_SCRIPT"}


def test_workflow_from_automic_real_fixture_after_sorted() -> None:
    """After lists are sorted for stable diffs."""
    fixture = _load_real_fixture("workflow_JOBP.json")
    inner = _inner(fixture, "jobp")
    canonical = to_canonical_from_automic("Workflow", inner)
    for task in canonical["tasks"]:
        assert task["after"] == sorted(task["after"])


def test_workflow_from_automic_real_fixture_ref_kind_mapped() -> None:
    """object_type is mapped to manifest kind via _AUTOMIC_TYPE_TO_KIND."""
    fixture = _load_real_fixture("workflow_JOBP.json")
    inner = _inner(fixture, "jobp")
    canonical = to_canonical_from_automic("Workflow", inner)
    # WINDOWS_JOB has object_type JOBS → kind Job
    windows = next(t for t in canonical["tasks"] if t["name"] == "WINDOWS_JOB")
    assert windows["ref"]["kind"] == "Job"


def test_workflow_from_automic_no_envelope_path_gives_empty_folder() -> None:
    """Without _envelope_path, folder is empty string."""
    fixture = _load_real_fixture("workflow_JOBP.json")
    inner = dict(_inner(fixture, "jobp"))
    # No _envelope_path injected.
    canonical = to_canonical_from_automic("Workflow", inner)
    assert canonical["folder"] == ""


# ---------------------------------------------------------------------------
# from_manifest: example YAML
# ---------------------------------------------------------------------------


def test_workflow_from_manifest_example_name() -> None:
    manifest = _load_manifest("workflow.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert canonical["name"] == "WORKFLOW"


def test_workflow_from_manifest_example_folder() -> None:
    manifest = _load_manifest("workflow.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert canonical["folder"] == "EXAMPLES/WORKFLOWS"


def test_workflow_from_manifest_example_task_count() -> None:
    manifest = _load_manifest("workflow.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert len(canonical["tasks"]) == 3


def test_workflow_from_manifest_example_after_sorted() -> None:
    manifest = _load_manifest("workflow.yaml")
    canonical = to_canonical_from_manifest(manifest)
    for task in canonical["tasks"]:
        assert task["after"] == sorted(task["after"])


# ---------------------------------------------------------------------------
# Round-trip: semantically equivalent inputs → identical canonical dicts
# ---------------------------------------------------------------------------


def test_workflow_round_trip_real_fixture_vs_manifest() -> None:
    """Canonical form from real fixture matches canonical form from manifest
    for the same workflow (WORKFLOW in EXAMPLES/WORKFLOWS).
    """
    fixture = _load_real_fixture("workflow_JOBP.json")
    inner = _inner(fixture, "jobp")
    inner["_envelope_path"] = fixture["path"]
    automic_canonical = to_canonical_from_automic("Workflow", inner)

    manifest = _load_manifest("workflow.yaml")
    manifest_canonical = to_canonical_from_manifest(manifest)

    # Identity fields must match.
    assert automic_canonical["name"] == manifest_canonical["name"]
    assert automic_canonical["folder"] == manifest_canonical["folder"]
    assert automic_canonical["kind"] == manifest_canonical["kind"]

    # Task names must match (order-insensitive).
    automic_names = {t["name"] for t in automic_canonical["tasks"]}
    manifest_names = {t["name"] for t in manifest_canonical["tasks"]}
    assert automic_names == manifest_names
