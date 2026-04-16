"""Tests for manifest_to_automic_payload."""

from __future__ import annotations

import pytest

from op_aromic.engine.serializer import _bool_yn, manifest_to_automic_payload
from op_aromic.models.base import Manifest


def _make_manifest(kind: str, spec: dict) -> Manifest:
    return Manifest.model_validate(
        {
            "apiVersion": "aromic.io/v1",
            "kind": kind,
            "metadata": {"name": f"T.{kind.upper()}", "folder": "/TST", "client": 100},
            "spec": spec,
        },
    )


def test_bool_yn_maps_true_false() -> None:
    assert _bool_yn(True) == "Y"
    assert _bool_yn(False) == "N"


def test_workflow_payload_uses_pascal_case() -> None:
    manifest = _make_manifest(
        "Workflow",
        {
            "title": "T",
            "tasks": [
                {"name": "S1", "ref": {"kind": "Job", "name": "J1"}, "after": []},
            ],
        },
    )
    payload = manifest_to_automic_payload(manifest)
    assert payload["Name"] == "T.WORKFLOW"
    assert payload["Type"] == "JOBP"
    assert payload["Folder"] == "/TST"
    assert payload["Client"] == 100
    assert payload["Tasks"][0]["Ref"] == {"Kind": "Job", "Name": "J1"}


def test_job_payload() -> None:
    manifest = _make_manifest(
        "Job",
        {
            "title": "Job title",
            "host": "H",
            "login": "L",
            "script": "s",
            "script_type": "OS",
        },
    )
    payload = manifest_to_automic_payload(manifest)
    assert payload["Type"] == "JOBS"
    assert payload["Host"] == "H"
    assert payload["Login"] == "L"
    assert payload["ScriptType"] == "OS"


def test_schedule_payload() -> None:
    manifest = _make_manifest(
        "Schedule",
        {
            "entries": [
                {
                    "task": {"kind": "Workflow", "name": "W1"},
                    "start_time": "02:00",
                    "calendar_keyword": "WEEKDAY",
                },
            ],
        },
    )
    payload = manifest_to_automic_payload(manifest)
    assert payload["Type"] == "JSCH"
    assert payload["Entries"][0] == {
        "Task": {"Kind": "Workflow", "Name": "W1"},
        "StartTime": "02:00",
        "CalendarKeyword": "WEEKDAY",
    }


def test_calendar_payload() -> None:
    manifest = _make_manifest(
        "Calendar",
        {
            "keywords": [
                {"name": "WD", "type": "WEEKDAY", "values": ["MON", "TUE"]},
            ],
        },
    )
    payload = manifest_to_automic_payload(manifest)
    assert payload["Type"] == "CALE"
    assert payload["Keywords"][0]["Values"] == ["MON", "TUE"]


def test_variable_payload() -> None:
    manifest = _make_manifest(
        "Variable",
        {
            "var_type": "STATIC",
            "entries": [{"key": "K", "value": "V"}],
        },
    )
    payload = manifest_to_automic_payload(manifest)
    assert payload["Type"] == "VARA"
    assert payload["VarType"] == "STATIC"
    assert payload["Entries"][0] == {"Key": "K", "Value": "V"}


def test_unknown_kind_raises() -> None:
    # We build a bare Manifest with a bogus kind so the serializer sees it.
    m = Manifest.model_construct(
        api_version="aromic.io/v1",
        kind="Unknown",
        metadata=Manifest.model_validate(
            {
                "apiVersion": "aromic.io/v1",
                "kind": "Job",
                "metadata": {"name": "X", "folder": "/A"},
                "spec": {"host": "h", "login": "l", "script": "s"},
            },
        ).metadata,
        spec={},
    )
    with pytest.raises(ValueError, match="no serializer"):
        manifest_to_automic_payload(m)


def test_raw_fields_pass_through() -> None:
    manifest = _make_manifest(
        "Job",
        {
            "host": "H",
            "login": "L",
            "script": "s",
            "raw": {"CustomField": "v"},
        },
    )
    payload = manifest_to_automic_payload(manifest)
    assert payload["CustomField"] == "v"
