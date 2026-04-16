"""Dependency graph over loaded manifests.

Nodes are ``(kind, name)`` tuples; edges point from a referring object to
each ``ObjectRef`` declared in its spec. Folder is carried alongside on
the resolved-ref view but is not part of the node identity because
Automic names are globally unique within a client.

Topological ordering returns *levels* rather than a flat list so a future
executor can parallelise within a level. When multiple kinds land in the
same level, we apply a stable kind-precedence tiebreaker so output is
deterministic:

    Calendars → Variables → Jobs → Schedules → Workflows
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from op_aromic.engine.loader import LoadedManifest

# (kind, name). Folder is not part of identity — Automic names are
# globally unique within a client.
NodeKey = tuple[str, str]

# Lower index = earlier in the apply order when levels are otherwise
# equivalent. Used as a stable tiebreaker within a single topological
# level and as the sort key at level-0 for ref-less roots.
_KIND_PRECEDENCE: dict[str, int] = {
    "Calendar": 0,
    "Variable": 1,
    "Job": 2,
    "Schedule": 3,
    "Workflow": 4,
}


class CyclicDependencyError(Exception):
    """Raised by ``topological_order`` when the graph contains a cycle."""

    def __init__(self, cycle_nodes: list[NodeKey]) -> None:
        self.cycle_nodes = cycle_nodes
        rendered = " -> ".join(f"{k}/{n}" for k, n in cycle_nodes)
        super().__init__(
            f"dependency cycle detected: {rendered}. "
            "Break the cycle by removing one of the cross-references.",
        )


@dataclass(frozen=True)
class DependencyGraph:
    """Immutable adjacency map over manifest-declared references.

    ``nodes`` is the set of every node that appears either as a source
    (from a loaded manifest) or as a target (ref target). ``edges`` maps
    each source node to the frozenset of its dependencies (targets).
    """

    nodes: frozenset[NodeKey]
    edges: dict[NodeKey, frozenset[NodeKey]] = field(default_factory=dict)

    def dependencies_of(self, node: NodeKey) -> frozenset[NodeKey]:
        return self.edges.get(node, frozenset())


def _extract_refs_from_spec(kind: str, spec: dict[str, Any]) -> list[NodeKey]:
    """Pull outbound ObjectRefs out of a per-kind spec dict.

    Kept explicit per kind so additions to the reference surface of a kind
    show up as a diff here rather than in generic traversal code.
    """
    out: list[NodeKey] = []
    if kind == "Workflow":
        for task in spec.get("tasks", []) or []:
            ref = task.get("ref") or {}
            target_kind = ref.get("kind")
            target_name = ref.get("name")
            if isinstance(target_kind, str) and isinstance(target_name, str):
                out.append((target_kind, target_name))
    elif kind == "Schedule":
        for entry in spec.get("entries", []) or []:
            task = entry.get("task") or {}
            target_kind = task.get("kind")
            target_name = task.get("name")
            if isinstance(target_kind, str) and isinstance(target_name, str):
                out.append((target_kind, target_name))
    # Other kinds have no outbound refs in the current schema.
    return out


def build_graph(loaded: list[LoadedManifest]) -> DependencyGraph:
    """Build a DependencyGraph over every loaded manifest + every referenced node."""
    edges: dict[NodeKey, frozenset[NodeKey]] = {}
    nodes: set[NodeKey] = set()

    for lm in loaded:
        src: NodeKey = (lm.manifest.kind, lm.manifest.metadata.name)
        nodes.add(src)
        deps = _extract_refs_from_spec(lm.manifest.kind, lm.manifest.spec)
        # Targets may not correspond to a declared manifest — validator owns
        # the dangling-reference rule; the graph accepts it as a leaf node.
        for dep in deps:
            nodes.add(dep)
        edges[src] = frozenset(deps)

    return DependencyGraph(nodes=frozenset(nodes), edges=edges)


def _kind_rank(node: NodeKey) -> tuple[int, str]:
    """Sort key used inside a level: kind precedence, then name for stability."""
    kind, name = node
    return (_KIND_PRECEDENCE.get(kind, len(_KIND_PRECEDENCE)), name)


def topological_order(graph: DependencyGraph) -> list[list[NodeKey]]:
    """Kahn's algorithm returning levels (breadth waves).

    Objects in the same level have no dependency on each other and can
    safely be applied in parallel. Within a level we stable-sort by kind
    precedence so output is deterministic across runs.
    """
    # Forward edges are stored src→targets. For Kahn we need indegree and
    # reverse adjacency (target→src list).
    indegree: dict[NodeKey, int] = defaultdict(int)
    reverse: dict[NodeKey, list[NodeKey]] = defaultdict(list)
    for node in graph.nodes:
        indegree[node] = 0
    for src, deps in graph.edges.items():
        indegree[src] = len(deps)
        for dep in deps:
            reverse[dep].append(src)

    levels: list[list[NodeKey]] = []
    ready = [n for n, d in indegree.items() if d == 0]
    visited: set[NodeKey] = set()

    while ready:
        ready.sort(key=_kind_rank)
        levels.append(ready)
        visited.update(ready)
        next_ready: list[NodeKey] = []
        for node in ready:
            for dependent in reverse.get(node, []):
                indegree[dependent] -= 1
                if indegree[dependent] == 0 and dependent not in visited:
                    next_ready.append(dependent)
        ready = next_ready

    if len(visited) != len(graph.nodes):
        # Whatever's left is on a cycle. Reconstruct a representative path
        # through the unvisited set for the error message.
        remaining = graph.nodes - visited
        cycle = _find_cycle(graph, remaining)
        raise CyclicDependencyError(cycle)

    return levels


def _find_cycle(graph: DependencyGraph, candidates: frozenset[NodeKey]) -> list[NodeKey]:
    """Best-effort path through a strongly-connected component for error output.

    Not a minimal cycle — just enough nodes to be actionable in the error.
    """
    if not candidates:
        return []
    start = min(candidates, key=_kind_rank)
    path: list[NodeKey] = [start]
    seen: set[NodeKey] = {start}
    current = start
    while True:
        deps = graph.dependencies_of(current) & candidates
        unseen = [d for d in deps if d not in seen]
        if unseen:
            nxt = min(unseen, key=_kind_rank)
            path.append(nxt)
            seen.add(nxt)
            current = nxt
            continue
        # Close the loop with any dep we've already seen.
        loop = [d for d in deps if d in seen]
        if loop:
            path.append(loop[0])
        return path


__all__ = [
    "CyclicDependencyError",
    "DependencyGraph",
    "NodeKey",
    "build_graph",
    "topological_order",
]
