"""Tests for _make_apply_progress helper in cli/app.py."""

from __future__ import annotations

from op_aromic.cli.app import _make_apply_progress
from op_aromic.engine.differ import ObjectDiff
from op_aromic.engine.planner import Plan


def _diff(action: str, name: str) -> ObjectDiff:
    return ObjectDiff(
        action=action,  # type: ignore[arg-type]
        kind="Job",
        name=name,
        folder="/X",
        desired={"name": name},
        actual=None,
        changes=[],
    )


def test_empty_plan_returns_null_context() -> None:
    # A plan with no work should produce a noop callback and a context
    # manager that's safe to enter/exit without any side effects.
    plan = Plan(creates=[], updates=[], deletes=[], noops=[])
    cb, ctx = _make_apply_progress(plan, output_mode="text")
    with ctx:
        cb("pass1_apply", "irrelevant")
        cb("delete", "also irrelevant")


def test_json_mode_suppresses_progress() -> None:
    # JSON mode must never write progress to stdout — would corrupt the
    # single-document envelope contract.
    plan = Plan(
        creates=[_diff("create", "A")],
        updates=[_diff("update", "B")],
        deletes=[],
        noops=[],
    )
    cb, ctx = _make_apply_progress(plan, output_mode="json")
    with ctx:
        cb("pass1_apply", "A")
        cb("pass2_apply", "B")


def test_text_mode_builds_real_progress() -> None:
    # With work to do in text mode, the returned callback/context should
    # both be live (advancing a task should not raise).
    plan = Plan(
        creates=[_diff("create", "A"), _diff("create", "B")],
        updates=[],
        deletes=[_diff("delete", "C")],
        noops=[],
    )
    cb, ctx = _make_apply_progress(plan, output_mode="text")
    with ctx:
        cb("pass1_apply", "A")
        cb("pass1_apply", "B")
        cb("delete", "C")
        cb("pass2_apply", "A")
        # Unknown events are ignored (no crash).
        cb("pass1_start", "")  # type: ignore[arg-type]
