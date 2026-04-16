"""Tests for the cross-document manifest validator."""

from __future__ import annotations

from pathlib import Path

from op_aromic.engine.loader import LoadedManifest
from op_aromic.engine.validator import (
    Severity,
    validate_manifests,
)
from op_aromic.models.base import Manifest, Metadata


def _loaded(
    kind: str,
    name: str,
    folder: str = "/A",
    spec: dict[str, object] | None = None,
    client: int | None = None,
    path: str = "x.yaml",
    doc_index: int = 0,
) -> LoadedManifest:
    manifest = Manifest(
        apiVersion="aromic.io/v1",
        kind=kind,
        metadata=Metadata(name=name, folder=folder, client=client),
        spec=spec or {},
    )
    return LoadedManifest(source_path=Path(path), doc_index=doc_index, manifest=manifest)


def test_clean_manifests_produce_no_issues() -> None:
    loaded = [
        _loaded("Job", "ETL.JOB.A", spec={"host": "H", "login": "L", "script": "s"}),
        _loaded("Variable", "ETL.VAR", spec={"entries": []}),
    ]
    report = validate_manifests(loaded)
    assert report.errors == []
    assert report.warnings == []
    assert report.ok


def test_duplicate_identity_is_error() -> None:
    loaded = [
        _loaded("Job", "ETL.JOB.A", spec={"host": "H", "login": "L", "script": "s"}),
        _loaded(
            "Job",
            "ETL.JOB.A",
            spec={"host": "H", "login": "L", "script": "s"},
            path="other.yaml",
        ),
    ]
    report = validate_manifests(loaded)
    assert any("duplicate" in e.message.lower() for e in report.errors)


def test_duplicate_across_different_clients_is_ok() -> None:
    loaded = [
        _loaded(
            "Job",
            "ETL.JOB.A",
            spec={"host": "H", "login": "L", "script": "s"},
            client=100,
        ),
        _loaded(
            "Job",
            "ETL.JOB.A",
            spec={"host": "H", "login": "L", "script": "s"},
            client=200,
        ),
    ]
    report = validate_manifests(loaded)
    assert report.errors == []


def test_invalid_name_chars() -> None:
    loaded = [
        _loaded("Job", "etl.job.a", spec={"host": "H", "login": "L", "script": "s"}),
    ]
    report = validate_manifests(loaded)
    assert any("name" in e.message.lower() for e in report.errors)


def test_name_too_long() -> None:
    name = "A" * 201
    loaded = [
        _loaded("Job", name, spec={"host": "H", "login": "L", "script": "s"}),
    ]
    report = validate_manifests(loaded)
    assert any("length" in e.message.lower() or "200" in e.message for e in report.errors)


def test_folder_must_start_with_slash() -> None:
    loaded = [
        _loaded(
            "Job",
            "ETL.JOB.A",
            folder="PROD/ETL",
            spec={"host": "H", "login": "L", "script": "s"},
        ),
    ]
    report = validate_manifests(loaded)
    assert any("folder" in e.message.lower() for e in report.errors)


def test_dangling_reference_is_error() -> None:
    loaded = [
        _loaded(
            "Workflow",
            "DAILY.WF",
            spec={
                "tasks": [
                    {
                        "name": "STEP.A",
                        "ref": {"kind": "Job", "name": "MISSING.JOB"},
                    },
                ],
            },
        ),
    ]
    report = validate_manifests(loaded)
    assert any("reference" in e.message.lower() for e in report.errors)
    assert any(e.severity is Severity.ERROR for e in report.errors)


def test_reference_resolves_when_declared() -> None:
    loaded = [
        _loaded("Job", "ETL.JOB.A", spec={"host": "H", "login": "L", "script": "s"}),
        _loaded(
            "Workflow",
            "DAILY.WF",
            spec={
                "tasks": [
                    {"name": "STEP.A", "ref": {"kind": "Job", "name": "ETL.JOB.A"}},
                ],
            },
        ),
    ]
    report = validate_manifests(loaded)
    assert report.errors == []


def test_schedule_reference_resolves() -> None:
    loaded = [
        _loaded(
            "Workflow",
            "DAILY.WF",
            spec={"tasks": []},
        ),
        _loaded(
            "Schedule",
            "NIGHTLY",
            spec={
                "entries": [
                    {
                        "task": {"kind": "Workflow", "name": "DAILY.WF"},
                        "start_time": "02:00",
                    },
                ],
            },
        ),
    ]
    report = validate_manifests(loaded)
    assert report.errors == []


def test_report_ok_false_on_errors() -> None:
    loaded = [
        _loaded("Job", "lowercase", spec={"host": "H", "login": "L", "script": "s"}),
    ]
    report = validate_manifests(loaded)
    assert report.ok is False


def test_empty_input_is_ok() -> None:
    report = validate_manifests([])
    assert report.ok
    assert report.errors == []
    assert report.warnings == []
