"""Normalizer tests for Job (JOBS) — v21 nested shape.

Tests:
- from_automic against real fixture (tests/fixtures/automic/real/unix_job_JOBS.json)
- from_manifest against examples/job.yaml
- Round-trip: both produce structurally equivalent canonical dicts
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
    return fixture_data["data"][key]


# ---------------------------------------------------------------------------
# from_automic: real fixture
# ---------------------------------------------------------------------------


def test_job_from_automic_real_fixture_name() -> None:
    fixture = _load_real_fixture("unix_job_JOBS.json")
    inner = _inner(fixture, "jobs")
    inner["_envelope_path"] = fixture["path"]
    canonical = to_canonical_from_automic("Job", inner)
    assert canonical["name"] == "UNIX_JOB"


def test_job_from_automic_real_fixture_folder_empty() -> None:
    """Real JOBS fixture has empty path → empty folder."""
    fixture = _load_real_fixture("unix_job_JOBS.json")
    inner = _inner(fixture, "jobs")
    inner["_envelope_path"] = fixture["path"]
    canonical = to_canonical_from_automic("Job", inner)
    assert canonical["folder"] == ""


def test_job_from_automic_real_fixture_kind() -> None:
    fixture = _load_real_fixture("unix_job_JOBS.json")
    inner = _inner(fixture, "jobs")
    canonical = to_canonical_from_automic("Job", inner)
    assert canonical["kind"] == "Job"


def test_job_from_automic_real_fixture_host_from_job_attributes() -> None:
    """Host maps from job_attributes.agent."""
    fixture = _load_real_fixture("unix_job_JOBS.json")
    inner = _inner(fixture, "jobs")
    canonical = to_canonical_from_automic("Job", inner)
    assert canonical["host"] == "UNIX01"


def test_job_from_automic_real_fixture_login_from_job_attributes() -> None:
    """Login maps from job_attributes.login."""
    fixture = _load_real_fixture("unix_job_JOBS.json")
    inner = _inner(fixture, "jobs")
    canonical = to_canonical_from_automic("Job", inner)
    assert canonical["login"] == "LOGIN.UNIX01"


def test_job_from_automic_real_fixture_script_from_process_block() -> None:
    """Script lines come from scripts[].process list."""
    fixture = _load_real_fixture("unix_job_JOBS.json")
    inner = _inner(fixture, "jobs")
    canonical = to_canonical_from_automic("Job", inner)
    # Real fixture scripts[0].process = ["sleep 60", "exit"]
    assert canonical["script"] is not None
    assert "sleep 60" in canonical["script"]
    assert "exit" in canonical["script"]


def test_job_from_automic_real_fixture_script_type_os() -> None:
    fixture = _load_real_fixture("unix_job_JOBS.json")
    inner = _inner(fixture, "jobs")
    canonical = to_canonical_from_automic("Job", inner)
    assert canonical["script_type"] == "OS"


def test_job_from_automic_no_envelope_path_gives_empty_folder() -> None:
    fixture = _load_real_fixture("unix_job_JOBS.json")
    inner = dict(_inner(fixture, "jobs"))
    canonical = to_canonical_from_automic("Job", inner)
    assert canonical["folder"] == ""


# ---------------------------------------------------------------------------
# from_manifest: example YAML
# ---------------------------------------------------------------------------


def test_job_from_manifest_example_name() -> None:
    manifest = _load_manifest("job.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert canonical["name"] == "UNIX_JOB"


def test_job_from_manifest_example_folder() -> None:
    manifest = _load_manifest("job.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert canonical["folder"] == "EXAMPLES/JOBS"


def test_job_from_manifest_example_host() -> None:
    manifest = _load_manifest("job.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert canonical["host"] == "UNIX01"


def test_job_from_manifest_example_login() -> None:
    manifest = _load_manifest("job.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert canonical["login"] == "LOGIN.UNIX01"


def test_job_from_manifest_example_script_type() -> None:
    manifest = _load_manifest("job.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert canonical["script_type"] == "OS"


# ---------------------------------------------------------------------------
# Round-trip: semantically equivalent inputs → identical canonical dicts
# ---------------------------------------------------------------------------


def test_job_round_trip_real_fixture_vs_manifest() -> None:
    """Canonical identity + agent/login from fixture must match manifest."""
    fixture = _load_real_fixture("unix_job_JOBS.json")
    inner = _inner(fixture, "jobs")
    inner["_envelope_path"] = ""  # fixture has empty path
    automic_canonical = to_canonical_from_automic("Job", inner)

    manifest = _load_manifest("job.yaml")
    manifest_canonical = to_canonical_from_manifest(manifest)

    # Identity fields.
    assert automic_canonical["name"] == manifest_canonical["name"]
    assert automic_canonical["kind"] == manifest_canonical["kind"]
    assert automic_canonical["host"] == manifest_canonical["host"]
    assert automic_canonical["login"] == manifest_canonical["login"]
    assert automic_canonical["script_type"] == manifest_canonical["script_type"]
