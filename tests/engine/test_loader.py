"""Tests for the YAML manifest loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from op_aromic.engine.errors import ManifestError
from op_aromic.engine.loader import load_manifests


def _write(tmp: Path, name: str, body: str) -> Path:
    path = tmp / name
    path.write_text(body)
    return path


def test_loads_single_doc(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "job.yaml",
        """\
apiVersion: aromic.io/v1
kind: Job
metadata:
  name: ETL.JOB.A
  folder: /PROD/ETL
spec:
  host: HOST.WIN
  login: LOGIN.ADMIN
  script: echo hi
""",
    )

    result = load_manifests(tmp_path)

    assert len(result) == 1
    assert result[0].manifest.kind == "Job"
    assert result[0].doc_index == 0
    assert result[0].source_path.name == "job.yaml"


def test_loads_multi_doc(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "many.yaml",
        """\
apiVersion: aromic.io/v1
kind: Job
metadata:
  name: ETL.JOB.A
  folder: /PROD/ETL
spec:
  host: H
  login: L
  script: s
---
apiVersion: aromic.io/v1
kind: Variable
metadata:
  name: ETL.VAR
  folder: /PROD/ETL
spec:
  entries:
    - key: K
      value: V
""",
    )

    result = load_manifests(tmp_path)

    assert [lm.manifest.kind for lm in result] == ["Job", "Variable"]
    assert [lm.doc_index for lm in result] == [0, 1]


def test_ignores_non_yaml_files(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "not-yaml.txt",
        "garbage contents",
    )
    _write(
        tmp_path,
        "job.yaml",
        """\
apiVersion: aromic.io/v1
kind: Job
metadata:
  name: ETL.JOB.A
  folder: /PROD/ETL
spec:
  host: H
  login: L
  script: s
""",
    )

    result = load_manifests(tmp_path)

    assert len(result) == 1


def test_rejects_malformed_yaml_with_file_line(tmp_path: Path) -> None:
    path = _write(tmp_path, "bad.yaml", "this: : is: : invalid:\n")

    with pytest.raises(ManifestError) as exc:
        load_manifests(tmp_path)

    assert str(path) in str(exc.value)
    assert exc.value.source_path == path


def test_rejects_unknown_kind(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad.yaml",
        """\
apiVersion: aromic.io/v1
kind: Frobnicator
metadata:
  name: X
  folder: /A
spec: {}
""",
    )

    with pytest.raises(ManifestError) as exc:
        load_manifests(tmp_path)

    assert "Frobnicator" in str(exc.value)


def test_rejects_missing_kind(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad.yaml",
        """\
apiVersion: aromic.io/v1
metadata:
  name: X
  folder: /A
spec: {}
""",
    )

    with pytest.raises(ManifestError):
        load_manifests(tmp_path)


def test_reports_line_for_invalid_spec_field(tmp_path: Path) -> None:
    # Job requires `host` — missing it must surface as a Manifest error
    # with some file:line context for the offending document.
    path = _write(
        tmp_path,
        "bad.yaml",
        """\
apiVersion: aromic.io/v1
kind: Job
metadata:
  name: X
  folder: /A
spec:
  login: L
  script: s
""",
    )

    with pytest.raises(ManifestError) as exc:
        load_manifests(tmp_path)

    msg = str(exc.value)
    assert str(path) in msg
    # spec starts around line 6; we expect the error to reference it.
    assert exc.value.source_path == path
    assert exc.value.line is not None
    assert exc.value.line >= 1


def test_empty_document_ignored(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "mixed.yaml",
        """\
---
---
apiVersion: aromic.io/v1
kind: Job
metadata:
  name: ETL.JOB.A
  folder: /PROD/ETL
spec:
  host: H
  login: L
  script: s
""",
    )

    result = load_manifests(tmp_path)

    assert len(result) == 1
    assert result[0].manifest.kind == "Job"


def test_load_single_file_path(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "job.yaml",
        """\
apiVersion: aromic.io/v1
kind: Job
metadata:
  name: ETL.JOB.A
  folder: /PROD/ETL
spec:
  host: H
  login: L
  script: s
""",
    )

    result = load_manifests(path)

    assert len(result) == 1


def test_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(ManifestError):
        load_manifests(tmp_path / "nonexistent")
