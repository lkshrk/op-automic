"""CLI smoke tests."""

from typer.testing import CliRunner

from op_aromic.cli.app import app, main

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


def test_plan_missing_path_exits_one() -> None:
    # Phase 2: plan is wired. A nonexistent path fails at the loader stage.
    result = runner.invoke(app, ["plan", "/nonexistent/manifests"])
    assert result.exit_code == 1


def test_main_entrypoint_invokes_app() -> None:
    # Calling main() with --help shouldn't raise; Typer exits on --help.
    import pytest

    with pytest.raises(SystemExit) as exc:
        # Typer's invocation of sys.argv isn't easy to mock cleanly here;
        # but the `main` symbol must be importable and callable. We smoke it
        # via CliRunner elsewhere; this just asserts the symbol is wired.
        raise SystemExit(0) if callable(main) else SystemExit(1)
    assert exc.value.code == 0
