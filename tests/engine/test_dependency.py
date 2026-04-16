"""Tests for dependency.py — DAG building, topological ordering, cycle detection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from op_aromic.engine.dependency import (
    CyclicDependencyError,
    DependencyGraph,
    build_graph,
    topological_order,
)
from op_aromic.engine.loader import LoadedManifest
from op_aromic.models.base import Manifest


def _loaded(kind: str, name: str, spec: dict[str, Any] | None = None) -> LoadedManifest:
    manifest = Manifest.model_validate(
        {
            "apiVersion": "aromic.io/v1",
            "kind": kind,
            "metadata": {"name": name, "folder": "/T"},
            "spec": spec or {},
        },
    )
    return LoadedManifest(source_path=Path(f"{name}.yaml"), doc_index=0, manifest=manifest)


def _workflow(name: str, task_refs: list[tuple[str, str]]) -> LoadedManifest:
    tasks = [
        {"name": f"STEP_{i}", "ref": {"kind": k, "name": n}}
        for i, (k, n) in enumerate(task_refs)
    ]
    return _loaded("Workflow", name, {"tasks": tasks})


def _schedule(name: str, task_refs: list[tuple[str, str]]) -> LoadedManifest:
    entries = [
        {"task": {"kind": k, "name": n}, "start_time": "01:00"}
        for k, n in task_refs
    ]
    return _loaded("Schedule", name, {"entries": entries})


def test_build_graph_extracts_workflow_task_refs() -> None:
    loaded = [
        _loaded("Job", "J.A"),
        _loaded("Job", "J.B"),
        _workflow("WF", [("Job", "J.A"), ("Job", "J.B")]),
    ]
    g = build_graph(loaded)
    assert isinstance(g, DependencyGraph)
    # Workflow depends on both jobs; jobs have no deps.
    wf_deps = g.dependencies_of(("Workflow", "WF"))
    assert ("Job", "J.A") in wf_deps
    assert ("Job", "J.B") in wf_deps
    assert g.dependencies_of(("Job", "J.A")) == frozenset()


def test_build_graph_extracts_schedule_entry_refs() -> None:
    loaded = [
        _loaded("Workflow", "WF.1"),
        _schedule("S", [("Workflow", "WF.1")]),
    ]
    g = build_graph(loaded)
    sched_deps = g.dependencies_of(("Schedule", "S"))
    assert ("Workflow", "WF.1") in sched_deps


def test_topological_order_returns_levels_not_flat() -> None:
    loaded = [
        _loaded("Job", "J.A"),
        _loaded("Job", "J.B"),
        _workflow("WF", [("Job", "J.A"), ("Job", "J.B")]),
    ]
    g = build_graph(loaded)
    levels = topological_order(g)
    # Two levels: [J.A, J.B] then [WF].
    assert len(levels) == 2
    level0_names = {ref[1] for ref in levels[0]}
    assert level0_names == {"J.A", "J.B"}
    assert [ref[1] for ref in levels[1]] == ["WF"]


def test_cycle_detection_raises() -> None:
    # Workflows referencing each other: WF1 → WF2 → WF1.
    loaded = [
        _workflow("WF1", [("Workflow", "WF2")]),
        _workflow("WF2", [("Workflow", "WF1")]),
    ]
    g = build_graph(loaded)
    with pytest.raises(CyclicDependencyError) as exc_info:
        topological_order(g)
    # The message should name the nodes involved.
    msg = str(exc_info.value)
    assert "WF1" in msg or "WF2" in msg


def test_forward_reference_to_undeclared_is_allowed_in_graph() -> None:
    # Workflow refers to a job that isn't in the manifests — graph still
    # builds (validator catches dangling refs). Dependency node is created
    # as a leaf.
    loaded = [_workflow("WF", [("Job", "MISSING")])]
    g = build_graph(loaded)
    assert ("Job", "MISSING") in g.dependencies_of(("Workflow", "WF"))
    levels = topological_order(g)
    # Implied Job appears first level; Workflow second.
    assert any(("Job", "MISSING") in lvl for lvl in levels[:1])


def test_kind_precedence_tiebreaker_within_same_level() -> None:
    # All five kinds declared independently, no cross-refs → all in one
    # topological level, but ordered by kind precedence:
    # Calendar, Variable, Job, Schedule, Workflow.
    loaded = [
        _loaded("Workflow", "W"),
        _loaded("Variable", "V", {"entries": [{"key": "K", "value": "X"}]}),
        _loaded("Calendar", "C", {"keywords": []}),
        _loaded("Job", "J"),
        _loaded("Schedule", "S", {"entries": []}),
    ]
    g = build_graph(loaded)
    levels = topological_order(g)
    # All five land in a single level (no cross refs).
    assert len(levels) == 1
    kinds_in_order = [ref[0] for ref in levels[0]]
    assert kinds_in_order == ["Calendar", "Variable", "Job", "Schedule", "Workflow"]


def test_level_boundaries_respected() -> None:
    # Variable depends on Calendar. Variable should be in a later level.
    loaded = [
        _loaded("Calendar", "CAL", {"keywords": []}),
        _loaded(
            "Variable",
            "VAR",
            {"entries": [{"key": "K", "value": "X"}]},
        ),
    ]
    # Synthesize a dep: mock a direct edge by adding a fake workflow bridging.
    wf = _workflow("WF", [("Calendar", "CAL"), ("Variable", "VAR")])
    g = build_graph([*loaded, wf])
    levels = topological_order(g)
    # Two levels: [CAL, VAR] (no cross dep among leaves), then [WF].
    assert len(levels) == 2
    assert ("Workflow", "WF") in levels[1]


def test_empty_graph_returns_empty_levels() -> None:
    g = build_graph([])
    assert topological_order(g) == []


def test_dependencies_of_unknown_node_returns_empty() -> None:
    g = build_graph([_loaded("Job", "J")])
    # Asking about a node not in the graph is safe — returns empty.
    assert g.dependencies_of(("Job", "NOT_THERE")) == frozenset()
