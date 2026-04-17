"""Normalizer tests for Calendar (CALE) — v21 nested shape (best-effort).

NOTE: No real CALE fixture is available from the Broadcom swagger v21 examples.
The v21 nested shape used here is best-effort, derived from patterns observed
in the JOBP/JOBS/VARA real fixtures. Live verification is needed.
See docs/ISSUES.md for the open item.

Tests:
- from_automic against a synthetic v21-shaped payload
- from_automic against the legacy flat fixture (backward compat)
- from_manifest against examples/calendar.yaml
- Round-trip: semantically equivalent inputs produce identical canonical dicts
"""

from __future__ import annotations

from pathlib import Path

import yaml

from op_aromic.engine.normalizer import to_canonical_from_automic, to_canonical_from_manifest
from op_aromic.models.base import Manifest

EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples"

# Synthetic v21 CALE payload (best-effort shape derived from swagger patterns).
# Needs live verification — see ISSUES.md.
_SYNTHETIC_V21_CALE = {
    "metadata": {"version": "21.0.0"},
    "general_attributes": {
        "minimum_ae_version": "11.2",
        "name": "WORK.DAYS",
        "type": "CALE",
    },
    "calendar_definitions": [
        {
            "keyword": "WEEKDAY",
            "type": "WEEKDAY",
            "entries": ["MON", "TUE", "WED", "THU", "FRI"],
        },
    ],
    "_envelope_path": "PROD/CAL",
}


def _load_manifest(name: str) -> Manifest:
    raw = yaml.safe_load((EXAMPLES_DIR / name).read_text())
    return Manifest.model_validate(raw)


# ---------------------------------------------------------------------------
# from_automic: synthetic v21 payload
# ---------------------------------------------------------------------------


def test_calendar_from_automic_v21_name() -> None:
    canonical = to_canonical_from_automic("Calendar", _SYNTHETIC_V21_CALE)
    assert canonical["name"] == "WORK.DAYS"


def test_calendar_from_automic_v21_folder() -> None:
    canonical = to_canonical_from_automic("Calendar", _SYNTHETIC_V21_CALE)
    assert canonical["folder"] == "PROD/CAL"


def test_calendar_from_automic_v21_kind() -> None:
    canonical = to_canonical_from_automic("Calendar", _SYNTHETIC_V21_CALE)
    assert canonical["kind"] == "Calendar"


def test_calendar_from_automic_v21_keywords() -> None:
    canonical = to_canonical_from_automic("Calendar", _SYNTHETIC_V21_CALE)
    assert len(canonical["keywords"]) == 1
    kw = canonical["keywords"][0]
    assert kw["name"] == "WEEKDAY"
    assert kw["type"] == "WEEKDAY"
    assert kw["values"] == ["MON", "TUE", "WED", "THU", "FRI"]


# ---------------------------------------------------------------------------
# from_automic: legacy flat payload (backward compat)
# ---------------------------------------------------------------------------

_FLAT_CALE = {
    "Name": "WORK.DAYS",
    "Type": "CALE",
    "Folder": "/PROD/CAL",
    "Version": 3,
    "Keywords": [
        {"Name": "WEEKDAY", "Type": "WEEKDAY", "Values": ["MON", "TUE", "WED", "THU", "FRI"]},
    ],
}


def test_calendar_from_automic_flat_name() -> None:
    canonical = to_canonical_from_automic("Calendar", _FLAT_CALE)
    assert canonical["name"] == "WORK.DAYS"


def test_calendar_from_automic_flat_folder() -> None:
    canonical = to_canonical_from_automic("Calendar", _FLAT_CALE)
    assert canonical["folder"] == "/PROD/CAL"


def test_calendar_from_automic_flat_keywords() -> None:
    canonical = to_canonical_from_automic("Calendar", _FLAT_CALE)
    assert len(canonical["keywords"]) == 1
    kw = canonical["keywords"][0]
    assert kw["name"] == "WEEKDAY"
    assert kw["values"] == ["MON", "TUE", "WED", "THU", "FRI"]


def test_calendar_from_automic_flat_volatile_stripped() -> None:
    """Version is a volatile field and must be stripped."""
    canonical = to_canonical_from_automic("Calendar", _FLAT_CALE)
    assert "Version" not in canonical


# ---------------------------------------------------------------------------
# from_manifest: example YAML
# ---------------------------------------------------------------------------


def test_calendar_from_manifest_name() -> None:
    manifest = _load_manifest("calendar.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert canonical["name"] == "WORK.DAYS"


def test_calendar_from_manifest_folder() -> None:
    manifest = _load_manifest("calendar.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert canonical["folder"] == "PROD/CAL"


def test_calendar_from_manifest_keywords() -> None:
    manifest = _load_manifest("calendar.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert len(canonical["keywords"]) == 1
    kw = canonical["keywords"][0]
    assert kw["name"] == "WEEKDAY"
    assert kw["type"] == "WEEKDAY"
    assert "MON" in kw["values"]


# ---------------------------------------------------------------------------
# Round-trip: v21 synthetic payload vs manifest
# ---------------------------------------------------------------------------


def test_calendar_round_trip_v21_vs_manifest() -> None:
    automic_canonical = to_canonical_from_automic("Calendar", _SYNTHETIC_V21_CALE)
    manifest = _load_manifest("calendar.yaml")
    manifest_canonical = to_canonical_from_manifest(manifest)

    assert automic_canonical["name"] == manifest_canonical["name"]
    assert automic_canonical["folder"] == manifest_canonical["folder"]
    assert automic_canonical["kind"] == manifest_canonical["kind"]
    # Keyword names must match.
    a_names = {k["name"] for k in automic_canonical["keywords"]}
    m_names = {k["name"] for k in manifest_canonical["keywords"]}
    assert a_names == m_names
