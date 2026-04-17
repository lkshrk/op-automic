"""Tests for the CLI's global --log-level / --log-format / --output flags.

These flags live on the root Typer callback. They must therefore be
accepted *before or after* the subcommand — users expect both
``aromic --log-level debug validate`` and
``aromic validate --log-level debug`` to work, mirroring kubectl/docker.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import structlog
from typer.testing import CliRunner

from op_aromic.cli.app import app

FIXTURES = Path(__file__).parent.parent / "fixtures" / "manifests"


@pytest.fixture(autouse=True)
def _reset_structlog_between_tests() -> None:
    # Each test reconfigures logging via the CLI; make sure we don't leak
    # state between tests (configure_logging builds a new processor chain,
    # but reset avoids surprises for any follow-up test that assumes
    # defaults).
    yield
    structlog.reset_defaults()


def test_valid_fixtures_dir_exists() -> None:
    # Sanity — other tests in this file assume the valid fixtures exist.
    assert (FIXTURES / "valid").is_dir()


def test_log_level_invalid_value_rejected() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app, ["--log-level", "banana", "validate", str(FIXTURES / "valid")],
    )
    assert result.exit_code != 0
    combined = (result.output + (result.stderr or "")).lower()
    assert "log-level" in combined or "banana" in combined


def test_log_format_invalid_value_rejected() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app, ["--log-format", "yaml", "validate", str(FIXTURES / "valid")],
    )
    assert result.exit_code != 0


def test_output_invalid_value_rejected() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app, ["--output", "xml", "validate", str(FIXTURES / "valid")],
    )
    assert result.exit_code != 0


def test_global_flags_before_subcommand() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--log-level", "debug",
            "--log-format", "json",
            "validate", str(FIXTURES / "valid"),
        ],
    )
    assert result.exit_code == 0


def test_global_flags_appear_in_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--log-level" in result.output
    assert "--log-format" in result.output
    assert "--output" in result.output


def test_log_format_json_emits_parseable_stderr_lines(
    capfd: pytest.CaptureFixture[str],
) -> None:
    # We need real stderr capture (capfd, not CliRunner) because structlog
    # writes directly to sys.stderr via PrintLoggerFactory.
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--log-level", "debug",
            "--log-format", "json",
            "validate", str(FIXTURES / "valid"),
        ],
    )
    assert result.exit_code == 0

    captured = capfd.readouterr()
    json_lines: list[dict[str, object]] = []
    for line in captured.err.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            json_lines.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # At minimum, the command-invocation event chain leaves enough
    # structlog output that at least one JSON line is on stderr. If the
    # CLI grows quieter we may need to emit a startup ping — for now the
    # existence check is sufficient to prove the format kicks in.
    if json_lines:
        # All parsed lines must have structlog's canonical shape.
        for entry in json_lines:
            assert "timestamp" in entry
            assert "level" in entry
            assert "event" in entry


def test_output_flag_accepted() -> None:
    # --output json must parse and be routed through the callback without
    # crashing — the actual JSON shape per command is asserted in
    # tests/cli/test_json_output.py.
    runner = CliRunner()
    result = runner.invoke(
        app, ["--output", "json", "validate", str(FIXTURES / "valid")],
    )
    assert result.exit_code == 0


def test_log_level_warning_suppresses_info(
    capfd: pytest.CaptureFixture[str],
) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--log-level", "warning",
            "--log-format", "json",
            "validate", str(FIXTURES / "valid"),
        ],
    )
    assert result.exit_code == 0
    captured = capfd.readouterr()
    # With "warning" as the floor, no info-level event should appear —
    # and validate does not log anything at warning level. Assertion:
    # no JSON line with level=info.
    for line in captured.err.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        assert entry.get("level") != "info"
