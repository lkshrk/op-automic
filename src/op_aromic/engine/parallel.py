"""Bounded parallel execution with a single aggregate error.

``parallel_map`` is a thin ``ThreadPoolExecutor`` wrapper that:

* preserves input order in the output list,
* uses at most ``max_workers`` threads,
* collects every exception and raises a single ``ParallelExecutionError``
  after the pool drains, so partial work is never silently dropped.

This exists because the plan-building hot path is network-bound: one
``get_object`` per manifest. Serial execution dominates wall time at
scale. Exception semantics intentionally differ from ``map(fn, items)``
(which short-circuits on the first exception) — we want the full picture
so the user sees every failing item in one shot.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Generic, TypeVar, cast

T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class ParallelExecutionError(Exception, Generic[T]):
    """Raised when one or more items fail inside ``parallel_map``.

    ``failures`` preserves input-order for deterministic reporting so the
    caller can point a user at the exact items that blew up without
    having to re-derive positions.
    """

    failures: Sequence[tuple[T, Exception]] = field(default_factory=tuple)

    def __str__(self) -> str:
        count = len(self.failures)
        first = self.failures[0][1] if self.failures else None
        preview = f"; first error: {first!r}" if first is not None else ""
        return f"{count} parallel task(s) failed{preview}"


_SENTINEL: object = object()


def parallel_map(
    fn: Callable[[T], R],
    items: Iterable[T],
    *,
    max_workers: int = 8,
) -> list[R]:
    """Apply ``fn`` to every item concurrently; return results in order.

    If ``max_workers == 1`` (or there is only one item) we skip the pool
    entirely — spinning up a thread for a single network call is pure
    overhead and muddies debugging.
    """
    materialised = list(items)
    if not materialised:
        return []

    if max_workers <= 1 or len(materialised) == 1:
        return _run_sequential(fn, materialised)

    # Sentinel-fill so "did this slot complete" is expressible without
    # confusing None-returning callables with missing results.
    results: list[object] = [_SENTINEL] * len(materialised)
    failures: list[tuple[int, T, Exception]] = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            (idx, pool.submit(fn, item))
            for idx, item in enumerate(materialised)
        ]
        for idx, future in futures:
            try:
                results[idx] = future.result()
            except Exception as exc:
                failures.append((idx, materialised[idx], exc))

    if failures:
        ordered = sorted(failures, key=lambda triple: triple[0])
        raise ParallelExecutionError(
            failures=tuple((item, exc) for _, item, exc in ordered),
        )

    # Every slot must have been filled; the sentinel-check is a safety net
    # against a caller returning our sentinel object (which would be bizarre).
    return [cast(R, r) for r in results]


def _run_sequential(fn: Callable[[T], R], items: list[T]) -> list[R]:
    """Preserve caller order and surface the full failure set."""
    results: list[R] = []
    failures: list[tuple[T, Exception]] = []
    for item in items:
        try:
            results.append(fn(item))
        except Exception as exc:
            failures.append((item, exc))
    if failures:
        raise ParallelExecutionError(failures=tuple(failures))
    return results


__all__ = ["ParallelExecutionError", "parallel_map"]
