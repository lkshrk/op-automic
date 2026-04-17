"""Tests for the Automic-to-YAML exporter.

The exporter is the read-only inverse of applier: it pulls everything we
know how to model, turns Automic JSON back into :class:`Manifest` objects,
and writes deterministic multi-doc YAML files grouped by a caller-chosen
layout.

All HTTP is mocked via respx; no network access in this module.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import yaml

from op_aromic.client.api import AutomicAPI
from op_aromic.client.http import _AUTH_PATH, AutomicClient
from op_aromic.config.settings import AutomicSettings
from op_aromic.engine.exporter import export

AUTOMIC_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "automic"


def _settings() -> AutomicSettings:
    return AutomicSettings(
        url="http://export.test/ae/api/v1",
        client_id=100,
        user="U",
        department="D",
        password="P",
        verify_ssl=False,
        max_retries=0,
    )


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((AUTOMIC_FIXTURES / name).read_text())


def _mock_auth(mock: respx.MockRouter, settings: AutomicSettings) -> None:
    mock.post(f"{settings.url}{_AUTH_PATH}").mock(
        return_value=httpx.Response(200, json={"token": "t", "expires_in": 3600}),
    )


def _install_list_and_get(
    mock: respx.MockRouter,
    base: str,
    by_type: dict[str, list[dict[str, Any]]],
) -> None:
    """Install list and get-by-name responders for a fixture set.

    Handles two listing endpoints:
    - ``GET /objects?type=...`` — used when no folder is specified.
    - ``GET /folderobjects/{path}?type=...`` — used when a folder is specified
      (B4: canonical folder-scoped listing per swagger v21).
    """

    def list_responder(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        typ = params.get("type")
        data = by_type.get(typ, []) if typ else []
        # For /objects endpoint, honour the ``folder`` query param as fallback.
        folder_filter = params.get("folder")
        if folder_filter:
            data = [d for d in data if d.get("Folder") == folder_filter]
        start = int(params.get("start_row", 0))
        max_rows = int(params.get("max_rows", 100))
        return httpx.Response(200, json={"data": data[start : start + max_rows]})

    def folder_list_responder(request: httpx.Request) -> httpx.Response:
        # Extract the folder path from the URL: /folderobjects/PROD/ETL → /PROD/ETL
        path_parts = request.url.path.split("/folderobjects/", 1)
        folder_path = "/" + path_parts[1] if len(path_parts) > 1 else "/"
        params = dict(request.url.params)
        typ = params.get("type")
        data = by_type.get(typ, []) if typ else []
        # Filter by folder path matching the URL segment.
        data = [d for d in data if d.get("Folder") == folder_path]
        start = int(params.get("start_row", 0))
        max_rows = int(params.get("max_rows", 100))
        return httpx.Response(200, json={"data": data[start : start + max_rows]})

    mock.get(f"{base}/objects").mock(side_effect=list_responder)
    mock.get(url__regex=rf"{re.escape(base)}/folderobjects/.*").mock(
        side_effect=folder_list_responder,
    )

    for objs in by_type.values():
        for obj in objs:
            mock.get(f"{base}/objects/{obj['Name']}").mock(
                return_value=httpx.Response(200, json=obj),
            )


def test_export_per_kind_happy_path(tmp_path: Path) -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    by_type = {
        "CALE": [_load_fixture("calendar.json")],
        "VARA": [_load_fixture("variable.json")],
        "JOBS": [_load_fixture("job.json")],
        "JOBP": [_load_fixture("workflow.json")],
        "JSCH": [_load_fixture("schedule.json")],
    }
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        _install_list_and_get(mock, base, by_type)
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            result = export(api, tmp_path)

    assert result.objects_exported == 5
    assert len(result.files_written) >= 1
    # Every file must be valid YAML.
    for p in result.files_written:
        assert p.exists()
        docs = list(yaml.safe_load_all(p.read_text()))
        assert any(d is not None for d in docs)


def test_export_layout_by_folder_mirrors_tree(tmp_path: Path) -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    by_type = {
        "JOBS": [_load_fixture("job.json")],  # Folder: /PROD/ETL
        "CALE": [_load_fixture("calendar.json")],  # Folder: /PROD/CAL
    }
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        _install_list_and_get(mock, base, by_type)
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            result = export(api, tmp_path, layout="by-folder")

    expected = {tmp_path / "PROD" / "ETL" / "ETL.yaml", tmp_path / "PROD" / "CAL" / "CAL.yaml"}
    assert set(result.files_written) == expected


def test_export_layout_by_kind_groups_per_kind_dir(tmp_path: Path) -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    by_type = {"JOBS": [_load_fixture("job.json")]}
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        _install_list_and_get(mock, base, by_type)
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            result = export(api, tmp_path, layout="by-kind", kinds=["Job"])

    assert result.objects_exported == 1
    job_file = tmp_path / "jobs" / "ETL.EXTRACT.yaml"
    assert job_file in result.files_written
    assert job_file.exists()


def test_export_layout_flat_one_file_per_folder_no_nesting(tmp_path: Path) -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    by_type = {
        "JOBS": [_load_fixture("job.json")],
        "CALE": [_load_fixture("calendar.json")],
    }
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        _install_list_and_get(mock, base, by_type)
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            result = export(api, tmp_path, layout="flat")

    # Flat: file names encode the folder, no subdirectories.
    for p in result.files_written:
        assert p.parent == tmp_path


def test_export_refuses_overwrite_without_flag(tmp_path: Path) -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    by_type = {"JOBS": [_load_fixture("job.json")]}

    # Pre-create the target file with non-empty content to force skip.
    pre_existing = tmp_path / "jobs" / "ETL.EXTRACT.yaml"
    pre_existing.parent.mkdir(parents=True)
    pre_existing.write_text("# pre-existing\n")

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        _install_list_and_get(mock, base, by_type)
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            result = export(api, tmp_path, layout="by-kind", kinds=["Job"])

    assert ("Job", "ETL.EXTRACT") in [(k, n) for k, n, _ in result.skipped]
    assert pre_existing.read_text() == "# pre-existing\n"  # untouched


def test_export_overwrite_replaces_existing(tmp_path: Path) -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    by_type = {"JOBS": [_load_fixture("job.json")]}

    pre_existing = tmp_path / "jobs" / "ETL.EXTRACT.yaml"
    pre_existing.parent.mkdir(parents=True)
    pre_existing.write_text("# old\n")

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        _install_list_and_get(mock, base, by_type)
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            export(api, tmp_path, layout="by-kind", kinds=["Job"], overwrite=True)

    assert "# old" not in pre_existing.read_text()


def test_export_filter_by_kind(tmp_path: Path) -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    by_type = {
        "JOBS": [_load_fixture("job.json")],
        "JOBP": [_load_fixture("workflow.json")],
    }
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        _install_list_and_get(mock, base, by_type)
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            result = export(api, tmp_path, kinds=["Workflow"], layout="by-kind")

    assert result.objects_exported == 1
    # No jobs/*.yaml — only workflows/*.yaml.
    assert all("workflows" in str(p) for p in result.files_written)


def test_export_filter_by_folder(tmp_path: Path) -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    # Two jobs in different folders; only /PROD/ETL should make it through.
    job_a = _load_fixture("job.json")  # /PROD/ETL
    job_b = {**_load_fixture("job.json"), "Name": "OTHER.JOB", "Folder": "/TEST/OTHER"}
    by_type = {"JOBS": [job_a, job_b]}

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        _install_list_and_get(mock, base, by_type)
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            result = export(
                api,
                tmp_path,
                kinds=["Job"],
                folders=["/PROD/ETL"],
                layout="by-kind",
            )

    assert result.objects_exported == 1
    assert any("ETL.EXTRACT" in str(p) for p in result.files_written)


def test_export_preserves_unmodelled_fields_in_spec_raw(tmp_path: Path) -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    # Field "CustomFlag" is not in our JobSpec model; exporter must round-trip
    # it through spec.raw so a later re-import does not drop it.
    job_with_extra = {**_load_fixture("job.json"), "CustomFlag": "Y", "ExtraArray": [1, 2]}
    by_type = {"JOBS": [job_with_extra]}

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        _install_list_and_get(mock, base, by_type)
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            export(api, tmp_path, kinds=["Job"], layout="by-kind")

    written = (tmp_path / "jobs" / "ETL.EXTRACT.yaml").read_text()
    loaded = next(d for d in yaml.safe_load_all(written) if d is not None)
    raw = loaded["spec"].get("raw", {})
    assert raw.get("CustomFlag") == "Y"
    assert raw.get("ExtraArray") == [1, 2]


def test_export_unknown_layout_raises() -> None:
    settings = _settings()
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            with pytest.raises(ValueError, match="layout"):
                export(api, Path("/tmp"), layout="bogus")  # type: ignore[arg-type]
