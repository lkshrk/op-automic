"""Normalizer tests for Variable (VARA) — v21 nested shape.

Tests:
- from_automic against real fixture (tests/fixtures/automic/real/static_variable_VARA.json)
- from_manifest against examples/variable.yaml
- Round-trip: both produce structurally equivalent canonical dicts
- column_count semantics: value1..value{N} per row
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


def test_vara_from_automic_real_fixture_name() -> None:
    fixture = _load_real_fixture("static_variable_VARA.json")
    inner = _inner(fixture, "vara")
    inner["_envelope_path"] = fixture["path"]
    canonical = to_canonical_from_automic("Variable", inner)
    assert canonical["name"] == "STATIC_VARIABLE"


def test_vara_from_automic_real_fixture_folder() -> None:
    fixture = _load_real_fixture("static_variable_VARA.json")
    inner = _inner(fixture, "vara")
    inner["_envelope_path"] = fixture["path"]
    canonical = to_canonical_from_automic("Variable", inner)
    assert canonical["folder"] == "EXAMPLES/VARAS"


def test_vara_from_automic_real_fixture_kind() -> None:
    fixture = _load_real_fixture("static_variable_VARA.json")
    inner = _inner(fixture, "vara")
    canonical = to_canonical_from_automic("Variable", inner)
    assert canonical["kind"] == "Variable"


def test_vara_from_automic_real_fixture_var_type() -> None:
    """sub_type STATIC maps to var_type STATIC."""
    fixture = _load_real_fixture("static_variable_VARA.json")
    inner = _inner(fixture, "vara")
    canonical = to_canonical_from_automic("Variable", inner)
    assert canonical["var_type"] == "STATIC"


def test_vara_from_automic_real_fixture_entry_count() -> None:
    """Real fixture has 2 rows (Continents, Planets)."""
    fixture = _load_real_fixture("static_variable_VARA.json")
    inner = _inner(fixture, "vara")
    canonical = to_canonical_from_automic("Variable", inner)
    assert len(canonical["entries"]) == 2


def test_vara_from_automic_real_fixture_entry_keys() -> None:
    fixture = _load_real_fixture("static_variable_VARA.json")
    inner = _inner(fixture, "vara")
    canonical = to_canonical_from_automic("Variable", inner)
    keys = {e["key"] for e in canonical["entries"]}
    assert keys == {"Continents", "Planets"}


def test_vara_from_automic_real_fixture_multicolumn_values() -> None:
    """column_count=5 → entries have 'values' list (not single 'value')."""
    fixture = _load_real_fixture("static_variable_VARA.json")
    inner = _inner(fixture, "vara")
    canonical = to_canonical_from_automic("Variable", inner)
    continents = next(e for e in canonical["entries"] if e["key"] == "Continents")
    # Multi-column entry uses 'values' list.
    assert "values" in continents
    assert len(continents["values"]) == 5
    assert "Asia" in continents["values"]
    assert "Africa" in continents["values"]


def test_vara_from_automic_real_fixture_continents_all_values() -> None:
    fixture = _load_real_fixture("static_variable_VARA.json")
    inner = _inner(fixture, "vara")
    canonical = to_canonical_from_automic("Variable", inner)
    continents = next(e for e in canonical["entries"] if e["key"] == "Continents")
    assert continents["values"] == ["Asia", "Africa", "Europe", "North America", "South America"]


# ---------------------------------------------------------------------------
# from_manifest: example YAML
# ---------------------------------------------------------------------------


def test_vara_from_manifest_example_name() -> None:
    manifest = _load_manifest("variable.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert canonical["name"] == "STATIC_VARIABLE"


def test_vara_from_manifest_example_folder() -> None:
    manifest = _load_manifest("variable.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert canonical["folder"] == "EXAMPLES/VARAS"


def test_vara_from_manifest_example_var_type() -> None:
    manifest = _load_manifest("variable.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert canonical["var_type"] == "STATIC"


def test_vara_from_manifest_example_entries() -> None:
    manifest = _load_manifest("variable.yaml")
    canonical = to_canonical_from_manifest(manifest)
    assert len(canonical["entries"]) == 2
    keys = {e["key"] for e in canonical["entries"]}
    assert keys == {"Continents", "Planets"}


# ---------------------------------------------------------------------------
# Round-trip: semantically equivalent inputs → same identity + type
# ---------------------------------------------------------------------------


def test_vara_round_trip_identity_fields() -> None:
    """Name, folder, kind, var_type match between automic canonical and manifest canonical."""
    fixture = _load_real_fixture("static_variable_VARA.json")
    inner = _inner(fixture, "vara")
    inner["_envelope_path"] = fixture["path"]
    automic_canonical = to_canonical_from_automic("Variable", inner)

    manifest = _load_manifest("variable.yaml")
    manifest_canonical = to_canonical_from_manifest(manifest)

    assert automic_canonical["name"] == manifest_canonical["name"]
    assert automic_canonical["folder"] == manifest_canonical["folder"]
    assert automic_canonical["kind"] == manifest_canonical["kind"]
    assert automic_canonical["var_type"] == manifest_canonical["var_type"]


def test_vara_round_trip_entry_keys_match() -> None:
    """Entry keys match between automic and manifest canonical forms."""
    fixture = _load_real_fixture("static_variable_VARA.json")
    inner = _inner(fixture, "vara")
    inner["_envelope_path"] = fixture["path"]
    automic_canonical = to_canonical_from_automic("Variable", inner)

    manifest = _load_manifest("variable.yaml")
    manifest_canonical = to_canonical_from_manifest(manifest)

    automic_keys = {e["key"] for e in automic_canonical["entries"]}
    manifest_keys = {e["key"] for e in manifest_canonical["entries"]}
    assert automic_keys == manifest_keys
