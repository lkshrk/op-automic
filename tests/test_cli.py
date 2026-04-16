"""CLI smoke tests."""

from typer.testing import CliRunner

from op_aromic.cli.app import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "aromic" in result.output


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "validate" in result.output
    assert "plan" in result.output
    assert "apply" in result.output
    assert "export" in result.output
    assert "destroy" in result.output


def test_destroy_requires_confirm() -> None:
    result = runner.invoke(app, ["destroy"])
    assert result.exit_code == 1
    assert "--confirm" in result.output
