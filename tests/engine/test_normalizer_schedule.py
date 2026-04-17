"""Normalizer tests for Schedule (JSCH) — v21 nested shape (best-effort).

NOTE: No real JSCH fixture is available from the Broadcom swagger v21 examples.
The v21 nested shape used here is best-effort. Live verification is needed.
See docs/ISSUES.md for the open item.

Tests:
- from_automic against a synthetic v21-shaped payload
- from_automic against the legacy flat fixture (backward compat)
- from_manifest against examples/schedule.yaml
- Round-trip: semantically equivalent inputs produce structurally consistent dicts
"""

from __future__ import annotations

from pathlib import Path

import yaml

from op_aromic.engine.normalizer import to_canonical_from_automic, to_canonical_from_manifest
from op_aromic.models.base import Manifest

EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples"

# Synthetic v21 JSCH payload (best-effort shape derived from swagger patterns).
# Needs live verification — see ISSUES.md.
_SYNTHETIC_V21_JSCH = {
    "metadata": {"version": "21.0.0"},
    "general_attributes": {
        "minimum_ae_version": "11.2",
        "name": "ETL.NIGHTLY",
        "type": "JSCH",
    },
    "schedule_definitions": [
        {
            "object_name": "ETL.DAILY",
            "object_type": "JOBP",
            "start_time": "020000",
            "calendar_keyword": "WEEKDAY",
        },
    ],
    "_envelope_path": "PROD/ETL",
}


def _load_manifest(name: str) -> Manifest:
    raw = yaml.safe_load((EXAMPLES_DIR / name).read_text())
    return Manifest.model_validate(raw)


# ---------------------------------------------------------------------------
# from_automic: synthetic v21 payload
# ---------------------------------------------------------------------------


def test_schedule_from_automic_v21_name() -> None:
    canonical = to_canonical_from_automic("Schedule", _SYNTHETIC_V21_JSCH)
    assert canonical["name"] == "ETL.NIGHTLY"


def test_schedule_from_automic_v21_folder() -> None:
    canonical = to_canonical_from_automic("Schedule", _SYNTHETIC_V21_JSCH)
    assert canonical["folder"] == "PROD/ETL"


def test_schedule_from_automic_v21_kind() -> None:
    canonical = to_canonical_from_automic("Schedule", _SYNTHETIC_V21_JSCH)
    assert canonical["kind"] == "Schedule"


def test_schedule_from_automic_v21_entry_count() -> None:
    canonical = to_canonical_from_automic("Schedule", _SYNTHETIC_V21_JSCH)
    assert len(canonical["entries"]) == 1


def test_schedule_from_automic_v21_entry_task_name() -> None:
    canonical = to_canonical_from_automic("Schedule", _SYNTHETIC_V21_JSCH)
    entry = canonical["entries"][0]
    assert entry["task"]["name"] == "ETL.DAILY"


def test_schedule_from_automic_v21_entry_task_kind_mapped() -> None:
    """JOBP maps to Workflow."""
    canonical = to_canonical_from_automic("Schedule", _SYNTHETIC_V21_JSCH)
    entry = canonical["entries"][0]
    assert entry["task"]["kind"] == "Workflow"


def test_schedule_from_automic_v21_start_time_normalised() -> None:
    """HHMMSS → HH:MM conversion."""
    canonical = to_canonical_from_automic("Schedule", _SYNTHETIC_V21_JSCH)
    assert canonical["entries"][0]["start_time"] == "02:00"


def test_schedule_from_automic_v21_calendar_keyword() -> None:
    canonical = to_canonical_from_automic("Schedule", _SYNTHETIC_V21_JSCH)
    assert canonical["entries"][0]["calendar_keyword"] == "WEEKDAY"


# ---------------------------------------------------------------------------
# from_automic: legacy flat payload (backward compat)
# ---------------------------------------------------------------------------

_FLAT_JSCH = {
    "Name": "ETL.NIGHTLY",
    "Type": "JSCH",
    "Folder": "/PROD/ETL",
    "OH_LASTMODIFIED": "2025-01-03",
    "Entries": [
        {
            "Task": {"Kind": "Workflow", "Name": "ETL.DAILY"},
            "StartTime": "02:00",
            "CalendarKeyword": "WEEKDAY",
        },
    ],
}


def test_schedule_from_automic_flat_name() -> None:
    canonical = to_canonical_from_automic("Schedule", _FLAT_JSCH)
    assert canonical["name"] == "ETL.NIGHTLY"


def test_schedule_from_automic_flat_folder() -> None:
    canonical = to_canonical_from_automic("Schedule", _FLAT_JSCH)
    assert canonical["folder"] == "/PROD/ETL"


def test_schedule_from_automic_flat_entries() -> None:
    canonical = to_canonical_from_automic("Schedule", _FLAT_JSCH)
    assert len(canonical["entries"]) == 1
    entry = canonical["entries"][0]
    assert entry["task"]["name"] == "ETL.DAILY"
    assert entry["start_time"] == "02:00"
    assert entry["calendar_keyword"] == "WEEKDAY"


def test_schedule_from_automic_flat_volatile_stripped() -> None:
    """OH_LASTMODIFIED must be stripped."""
    canonical = to_canonical_from_automic("Schedule", _FLAT_JSCH)
    assert "OH_LASTMODIFIED" not in canonical


# ---------------------------------------------------------------------------
# from_manifest: example YAML
# ---------------------------------------------------------------------------


def test_schedule_from_manifest_name() -> None:
    manifest = _load_manifest("schedule.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert canonical["name"] == "ETL.NIGHTLY"


def test_schedule_from_manifest_folder() -> None:
    manifest = _load_manifest("schedule.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert canonical["folder"] == "PROD/ETL"


def test_schedule_from_manifest_entries() -> None:
    manifest = _load_manifest("schedule.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert len(canonical["entries"]) == 1
    entry = canonical["entries"][0]
    assert entry["task"]["name"] == "ETL.DAILY"
    assert entry["task"]["kind"] == "Workflow"
    assert entry["start_time"] == "02:00"
    assert entry["calendar_keyword"] == "WEEKDAY"


# ---------------------------------------------------------------------------
# Round-trip: v21 synthetic vs manifest
# ---------------------------------------------------------------------------


def test_schedule_round_trip_v21_vs_manifest() -> None:
    automic_canonical = to_canonical_from_automic("Schedule", _SYNTHETIC_V21_JSCH)
    manifest = _load_manifest("schedule.yaml")
    manifest_canonical = to_canonical_from_manifest(manifest)

    assert automic_canonical["name"] == manifest_canonical["name"]
    assert automic_canonical["kind"] == manifest_canonical["kind"]
    assert len(automic_canonical["entries"]) == len(manifest_canonical["entries"])
    a_entry = automic_canonical["entries"][0]
    m_entry = manifest_canonical["entries"][0]
    assert a_entry["task"]["name"] == m_entry["task"]["name"]
    assert a_entry["start_time"] == m_entry["start_time"]
    assert a_entry["calendar_keyword"] == m_entry["calendar_keyword"]
