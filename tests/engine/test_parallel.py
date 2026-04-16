"""Tests for the bounded parallel_map helper.

The semantics under test (and documented in ``engine/parallel.py``):

* All-success → list of results in input order
* Single failure → ``ParallelExecutionError`` with exactly one entry
* Multiple failures → ``ParallelExecutionError`` lists all of them
* max_workers=1 → sequential, order-preserving, same error shape
"""

from __future__ import annotations

import threading
import time

import pytest

from op_aromic.engine.parallel import ParallelExecutionError, parallel_map


class TestSuccesses:
    def test_all_succeed_preserves_order(self) -> None:
        out = parallel_map(lambda x: x * 2, [1, 2, 3, 4, 5], max_workers=4)
        assert out == [2, 4, 6, 8, 10]

    def test_empty_input_returns_empty(self) -> None:
        assert parallel_map(lambda x: x, [], max_workers=4) == []

    def test_single_item_uses_sequential_path(self) -> None:
        # Single item short-circuits the pool per docstring contract.
        out = parallel_map(lambda x: x + 1, [42], max_workers=8)
        assert out == [43]

    def test_accepts_any_iterable(self) -> None:
        # Generators: materialised once.
        out = parallel_map(lambda x: x * 2, (i for i in range(3)), max_workers=2)
        assert out == [0, 2, 4]

    def test_actually_parallel(self) -> None:
        # If parallel_map were serial, total wall time would be >= N * sleep.
        # With max_workers == N and sleep 0.1s we expect < ~0.3s even on a
        # slow runner; give ourselves 0.5s slack.
        started = threading.Event()
        waiter = threading.Event()
        counter = {"n": 0}
        lock = threading.Lock()

        def fn(_: int) -> int:
            with lock:
                counter["n"] += 1
                if counter["n"] == 4:
                    started.set()
            started.wait(timeout=1.0)
            waiter.set()
            return 0

        t0 = time.monotonic()
        parallel_map(fn, [1, 2, 3, 4], max_workers=4)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5, f"expected parallel execution, elapsed={elapsed:.2f}s"
        assert waiter.is_set()


class TestFailures:
    def test_one_failure_out_of_n_raises_aggregated(self) -> None:
        def fn(x: int) -> int:
            if x == 3:
                raise ValueError("boom")
            return x

        with pytest.raises(ParallelExecutionError) as excinfo:
            parallel_map(fn, [1, 2, 3, 4, 5], max_workers=4)
        assert len(excinfo.value.failures) == 1
        item, err = excinfo.value.failures[0]
        assert item == 3
        assert isinstance(err, ValueError)

    def test_multiple_failures_all_reported(self) -> None:
        def fn(x: int) -> int:
            if x % 2 == 0:
                raise RuntimeError(f"even-{x}")
            return x

        with pytest.raises(ParallelExecutionError) as excinfo:
            parallel_map(fn, [1, 2, 3, 4, 5, 6], max_workers=4)
        failures = excinfo.value.failures
        assert len(failures) == 3
        assert [item for item, _ in failures] == [2, 4, 6]
        assert all(isinstance(err, RuntimeError) for _, err in failures)

    def test_all_fail(self) -> None:
        def fn(_: int) -> int:
            raise KeyError("nope")

        with pytest.raises(ParallelExecutionError) as excinfo:
            parallel_map(fn, [1, 2, 3], max_workers=4)
        assert len(excinfo.value.failures) == 3

    def test_sequential_one_failure(self) -> None:
        # max_workers=1 path has its own code path — cover it separately.
        def fn(x: int) -> int:
            if x == 2:
                raise ValueError("seq-boom")
            return x

        with pytest.raises(ParallelExecutionError) as excinfo:
            parallel_map(fn, [1, 2, 3], max_workers=1)
        assert len(excinfo.value.failures) == 1

    def test_error_str_gives_preview(self) -> None:
        err = ParallelExecutionError(failures=((1, ValueError("x")),))
        assert "1 parallel task(s) failed" in str(err)


class TestSequentialMode:
    def test_max_workers_one_preserves_order_under_delays(self) -> None:
        # In parallel mode, sleeps would invert the completion order of
        # faster items. max_workers=1 must run in submitted order regardless.
        observed_order: list[int] = []

        def fn(x: int) -> int:
            # Larger x returns faster in parallel; here we force it serial.
            time.sleep(0.01 if x == 1 else 0.0)
            observed_order.append(x)
            return x

        parallel_map(fn, [1, 2, 3], max_workers=1)
        assert observed_order == [1, 2, 3]
