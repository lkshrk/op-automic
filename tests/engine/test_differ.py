"""Tests for compute_diff."""

from __future__ import annotations

import pytest

from op_aromic.engine.differ import compute_diff


def _both_none_raises() -> None:
    pass


def test_raises_when_both_sides_missing() -> None:
    with pytest.raises(ValueError):
        compute_diff(
            kind="Job",
            name="X",
            folder="/A",
            desired=None,
            actual=None,
        )


def test_noop_for_identical_inputs() -> None:
    payload = {"name": "X", "folder": "/A", "kind": "Job", "host": "h"}
    diff = compute_diff(
        kind="Job",
        name="X",
        folder="/A",
        desired=payload,
        actual=payload,
    )
    assert diff.action == "noop"
    assert diff.changes == []


def test_create_when_actual_missing() -> None:
    diff = compute_diff(
        kind="Job",
        name="X",
        folder="/A",
        desired={"name": "X"},
        actual=None,
    )
    assert diff.action == "create"
    assert diff.actual is None


def test_delete_when_desired_missing() -> None:
    diff = compute_diff(
        kind="Job",
        name="X",
        folder="/A",
        desired=None,
        actual={"name": "X"},
    )
    assert diff.action == "delete"
    assert diff.desired is None


def test_update_surfaces_individual_field_changes() -> None:
    diff = compute_diff(
        kind="Job",
        name="X",
        folder="/A",
        desired={"name": "X", "host": "new", "login": "L"},
        actual={"name": "X", "host": "old", "login": "L"},
    )
    assert diff.action == "update"
    # Exactly one value changed at path "host".
    paths = [c.path for c in diff.changes]
    assert "host" in paths
    host_change = next(c for c in diff.changes if c.path == "host")
    assert host_change.before == "old"
    assert host_change.after == "new"


def test_update_detects_added_and_removed_keys() -> None:
    diff = compute_diff(
        kind="Job",
        name="X",
        folder="/A",
        desired={"host": "h", "extra": "y"},
        actual={"host": "h", "dropped": "z"},
    )
    assert diff.action == "update"
    kinds = {c.kind for c in diff.changes}
    assert "added" in kinds
    assert "removed" in kinds


def test_ignore_order_collapses_list_reordering() -> None:
    # Lists with the same elements in different order should NOT trigger an update.
    diff = compute_diff(
        kind="Workflow",
        name="W",
        folder="/A",
        desired={"tasks": [{"name": "A"}, {"name": "B"}]},
        actual={"tasks": [{"name": "B"}, {"name": "A"}]},
    )
    assert diff.action == "noop"
