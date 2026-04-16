"""Tests for render_plan / plan_to_json_dict."""

from __future__ import annotations

import io

from rich.console import Console

from op_aromic.cli.output import plan_to_json_dict, render_plan
from op_aromic.engine.differ import FieldChange, ObjectDiff
from op_aromic.engine.planner import Plan


def _console_buffer() -> tuple[Console, io.StringIO]:
    # force_terminal=False keeps ANSI codes out of the captured string.
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, no_color=True, width=120), buf


def test_render_plan_no_changes_says_up_to_date() -> None:
    console, buf = _console_buffer()
    plan = Plan(noops=[ObjectDiff(
        action="noop", kind="Job", name="X", folder="/A", desired={}, actual={},
    )])
    render_plan(plan, console)
    assert "up to date" in buf.getvalue().lower()


def test_render_plan_empty_plan_says_nothing() -> None:
    console, buf = _console_buffer()
    plan = Plan()
    render_plan(plan, console)
    assert "nothing" in buf.getvalue().lower()


def test_render_plan_shows_each_action() -> None:
    console, buf = _console_buffer()
    plan = Plan(
        creates=[ObjectDiff(
            action="create", kind="Job", name="NEW", folder="/A",
            desired={"host": "h"}, actual=None,
        )],
        updates=[ObjectDiff(
            action="update", kind="Job", name="UPD", folder="/A",
            desired={"host": "new"}, actual={"host": "old"},
            changes=[
                FieldChange(path="host", before="old", after="new", kind="changed"),
                FieldChange(path="extra", before=None, after="v", kind="added"),
                FieldChange(path="gone", before="v", after=None, kind="removed"),
            ],
        )],
        deletes=[ObjectDiff(
            action="delete", kind="Job", name="DEL", folder="/A",
            desired=None, actual={"host": "h"},
        )],
    )
    render_plan(plan, console)
    output = buf.getvalue()
    assert "CREATE" in output
    assert "UPDATE" in output
    assert "DELETE" in output
    assert "host" in output
    # Summary counts.
    assert "1 to create" in output
    assert "1 to update" in output
    assert "1 to delete" in output


def test_plan_to_json_dict_shape() -> None:
    plan = Plan(
        creates=[ObjectDiff(
            action="create", kind="Job", name="N", folder="/A",
            desired={"host": "h"}, actual=None,
            changes=[FieldChange(path="host", before=None, after="h", kind="added")],
        )],
    )
    data = plan_to_json_dict(plan)
    assert data["creates"][0]["action"] == "create"
    assert data["creates"][0]["changes"][0]["path"] == "host"
    assert data["updates"] == []
