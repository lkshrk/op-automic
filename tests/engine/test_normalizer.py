"""Tests for per-kind Normalizer implementations.

For each kind, a manifest and its hand-written Automic JSON twin must
produce identical canonical dicts. Volatile fields on the Automic side
are stripped by the normalizer.
"""

from __future__ import annotations

import pytest

from op_aromic.engine.normalizer import (
    IGNORED_FIELDS_BY_KIND,
    get_normalizer,
    to_canonical_from_automic,
    to_canonical_from_manifest,
)
from op_aromic.models.base import Manifest


def _manifest(kind: str, name: str, spec: dict) -> Manifest:
    return Manifest.model_validate(
        {
            "apiVersion": "aromic.io/v1",
            "kind": kind,
            "metadata": {"name": name, "folder": "/TST"},
            "spec": spec,
        },
    )


def test_get_normalizer_unknown_raises() -> None:
    with pytest.raises(ValueError, match="no normalizer"):
        get_normalizer("Frobnicator")


def test_ignored_fields_lookup_has_common_set() -> None:
    # sanity check the public map is populated for every known kind
    for kind in ("Workflow", "Job", "Schedule", "Calendar", "Variable"):
        assert kind in IGNORED_FIELDS_BY_KIND


def test_workflow_round_trip() -> None:
    manifest = _manifest(
        "Workflow",
        "ETL.DAILY",
        {
            "title": "Daily ETL",
            "tasks": [
                {
                    "name": "STEP.EXTRACT",
                    "ref": {"kind": "Job", "name": "ETL.EXTRACT"},
                    "after": [],
                },
                {
                    "name": "STEP.LOAD",
                    "ref": {"kind": "Job", "name": "ETL.LOAD"},
                    "after": ["STEP.EXTRACT"],
                },
            ],
        },
    )
    automic_json = {
        "Name": "ETL.DAILY",
        "Type": "JOBP",
        "Folder": "/TST",
        "Title": "Daily ETL",
        # volatile fields that must be stripped
        "LastModified": "2025-01-01T00:00:00Z",
        "OH_IDNR": 12345,
        "Tasks": [
            {
                "Name": "STEP.EXTRACT",
                "Ref": {"Kind": "Job", "Name": "ETL.EXTRACT"},
                "After": [],
            },
            {
                "Name": "STEP.LOAD",
                "Ref": {"Kind": "Job", "Name": "ETL.LOAD"},
                "After": ["STEP.EXTRACT"],
            },
        ],
    }
    assert to_canonical_from_manifest(manifest) == to_canonical_from_automic(
        "Workflow", automic_json,
    )


def test_workflow_normalizer_sorts_after() -> None:
    # Order-insensitive dependency list.
    manifest = _manifest(
        "Workflow",
        "W",
        {
            "tasks": [
                {"name": "A", "ref": {"kind": "Job", "name": "J"}, "after": []},
                {"name": "B", "ref": {"kind": "Job", "name": "J"}, "after": []},
                {
                    "name": "C",
                    "ref": {"kind": "Job", "name": "J"},
                    "after": ["B", "A"],
                },
            ],
        },
    )
    canonical = to_canonical_from_manifest(manifest)
    assert canonical["tasks"][2]["after"] == ["A", "B"]


def test_job_round_trip() -> None:
    manifest = _manifest(
        "Job",
        "ETL.EXTRACT",
        {"host": "H", "login": "L", "script": "s", "script_type": "OS"},
    )
    automic_json = {
        "Name": "ETL.EXTRACT",
        "Type": "JOBS",
        "Folder": "/TST",
        "Host": "H",
        "Login": "L",
        "Script": "s",
        "ScriptType": "OS",
        "LastModifiedBy": "ADMIN",
    }
    assert to_canonical_from_manifest(manifest) == to_canonical_from_automic(
        "Job", automic_json,
    )


def test_schedule_round_trip() -> None:
    manifest = _manifest(
        "Schedule",
        "ETL.NIGHTLY",
        {
            "entries": [
                {
                    "task": {"kind": "Workflow", "name": "ETL.DAILY"},
                    "start_time": "02:00",
                    "calendar_keyword": "WEEKDAY",
                },
            ],
        },
    )
    automic_json = {
        "Name": "ETL.NIGHTLY",
        "Type": "JSCH",
        "Folder": "/TST",
        "OH_LASTMODIFIED": "2025-01-01",
        "Entries": [
            {
                "Task": {"Kind": "Workflow", "Name": "ETL.DAILY"},
                "StartTime": "02:00",
                "CalendarKeyword": "WEEKDAY",
            },
        ],
    }
    assert to_canonical_from_manifest(manifest) == to_canonical_from_automic(
        "Schedule", automic_json,
    )


def test_calendar_round_trip() -> None:
    manifest = _manifest(
        "Calendar",
        "WORK.DAYS",
        {
            "keywords": [
                {"name": "WD", "type": "WEEKDAY", "values": ["MON", "TUE"]},
            ],
        },
    )
    automic_json = {
        "Name": "WORK.DAYS",
        "Type": "CALE",
        "Folder": "/TST",
        "Version": 3,
        "Keywords": [
            {"Name": "WD", "Type": "WEEKDAY", "Values": ["MON", "TUE"]},
        ],
    }
    assert to_canonical_from_manifest(manifest) == to_canonical_from_automic(
        "Calendar", automic_json,
    )


def test_variable_round_trip() -> None:
    manifest = _manifest(
        "Variable",
        "ETL.CONFIG",
        {
            "var_type": "STATIC",
            "entries": [{"key": "K", "value": "V"}],
        },
    )
    automic_json = {
        "Name": "ETL.CONFIG",
        "Type": "VARA",
        "Folder": "/TST",
        "ACLHash": "deadbeef",
        "VarType": "STATIC",
        "Entries": [{"Key": "K", "Value": "V"}],
    }
    assert to_canonical_from_manifest(manifest) == to_canonical_from_automic(
        "Variable", automic_json,
    )


def test_strip_removes_ignored_fields() -> None:
    automic = {
        "Name": "X",
        "Type": "JOBS",
        "Folder": "/A",
        "Host": "h",
        "Login": "l",
        "Script": "s",
        "ScriptType": "OS",
        "LastModified": "t",
        "LastModifiedBy": "u",
        "InternalId": 7,
    }
    canonical = to_canonical_from_automic("Job", automic)
    # None of the volatile keys (even renamed) survive.
    as_values = set(canonical.keys())
    assert "LastModified" not in as_values
    assert "LastModifiedBy" not in as_values
    assert "InternalId" not in as_values
