"""Tests for `aromic validate`."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from op_aromic.cli.app import app

runner = CliRunner()

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "manifests"


def test_validate_clean_exits_zero() -> None:
    result = runner.invoke(app, ["validate", str(FIXTURES / "valid")])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output or "no issues" in result.output.lower()


def test_validate_reports_duplicate_identity() -> None:
    result = runner.invoke(app, ["validate", str(FIXTURES / "invalid" / "duplicate.yaml")])
    assert result.exit_code == 1
    assert "duplicate" in result.output.lower()


def test_validate_reports_dangling_reference() -> None:
    result = runner.invoke(app, ["validate", str(FIXTURES / "invalid" / "dangling-ref.yaml")])
    assert result.exit_code == 1
    assert "reference" in result.output.lower() or "unresolved" in result.output.lower()


def test_validate_reports_bad_name() -> None:
    result = runner.invoke(app, ["validate", str(FIXTURES / "invalid" / "bad-name.yaml")])
    assert result.exit_code == 1
    assert "name" in result.output.lower()


def test_validate_reports_bad_folder() -> None:
    result = runner.invoke(app, ["validate", str(FIXTURES / "invalid" / "bad-folder.yaml")])
    assert result.exit_code == 1
    assert "folder" in result.output.lower()


def test_validate_reports_malformed_yaml() -> None:
    result = runner.invoke(app, ["validate", str(FIXTURES / "invalid" / "malformed.yaml")])
    assert result.exit_code == 1


def test_validate_missing_path_exits_one() -> None:
    result = runner.invoke(app, ["validate", "/nonexistent/path/does/not/exist"])
    assert result.exit_code == 1


def test_validate_strict_fails_on_warning(tmp_path: Path) -> None:
    # A warning-only fixture: we inject one via a synthetic manifest that
    # loads clean but the CLI layer emits a warning for (future use).
    # For now we simulate strict mode by asserting that with no warnings
    # the exit code stays 0 in strict mode.
    (tmp_path / "clean.yaml").write_text(
        """\
apiVersion: aromic.io/v1
kind: Job
metadata:
  name: CLEAN.JOB
  folder: /X
spec:
  host: H
  login: L
  script: s
""",
    )
    result = runner.invoke(app, ["validate", str(tmp_path), "--strict"])
    assert result.exit_code == 0


def test_validate_strict_exit_code_two_on_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch the validator to inject a warning so we can exercise the exit-2
    # code path in strict mode without contriving a YAML fixture.
    import op_aromic.cli.app as app_module
    from op_aromic.engine.validator import Issue, Severity, ValidationReport

    fake_report = ValidationReport(
        errors=[],
        warnings=[
            Issue(
                severity=Severity.WARNING,
                message="synthetic warning",
                source_path=Path("<test>"),
                doc_index=0,
            ),
        ],
    )
    monkeypatch.setattr(app_module, "validate_manifests", lambda _loaded: fake_report)

    result = runner.invoke(app, ["validate", str(FIXTURES / "valid"), "--strict"])
    assert result.exit_code == 2
    assert "synthetic warning" in result.output
