"""Concurrency + metric-readback harness for flag soaks.

Why this exists
---------------
Eight registry items have "soak, then flip" as their entire remaining work, and the reason none
of them has ever been flipped is not courage and not production traffic. Measured 2026-08-19:

* **The integration suite is entirely sequential.** Zero ``ThreadPoolExecutor``, zero
  ``asyncio.gather``, zero concurrent drivers under ``tests/integration/``. The one ``threading``
  import in the tree is a ``Lock`` inside a collector.
* **No test reads a metric.** Zero ``get_sample_value``, zero ``generate_latest``, zero
  ``.collect()`` — against **52** registered metrics. ``PERF-BASELINE-1`` is therefore misnamed:
  the instrument exists, nothing consumes it.

Everything else was already here — live Postgres and Redis on every PR, crash simulation, and
the flags themselves. So "soak" had been standing in for an apparatus nobody built, and because
the word sounds like it needs production it got deferred to a consumer that does not exist.

What a soak test is, here
-------------------------
Three assertions, and the third is the one usually missing:

1. **Correctness holds under contention** — the invariant the flag protects survives N callers
   racing, not just two sequential calls.
2. **The metric moves the way the mechanism claims** — read it, do not infer it.
3. **Nothing regresses against flag-off** — the same driver, same assertions, flag off.

★ Two traps this harness is shaped around
-----------------------------------------
**A thread that swallows its exception reports success.** ``ThreadPoolExecutor`` holds
exceptions until ``.result()`` is called; a driver that never calls it produces a green test
over N failed workers. :func:`drive_concurrently` always surfaces them.

**A metric that was never registered reads as zero.** ``get_sample_value`` returns ``None`` for
an unknown name, and ``None``-treated-as-zero makes "the counter did not move" and "the counter
does not exist" indistinguishable — the exact shape of `DOCS-COVERAGE-CLAIM-1`. :func:`read_metric`
raises on an unknown name instead.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from AINDY.platform_layer.metrics import REGISTRY

# ── Metric readback ──────────────────────────────────────────────────────────


def read_metric(name: str, labels: Optional[dict[str, str]] = None) -> float:
    """Current value of a registered metric.

    ★ Raises ``AssertionError`` on an unknown name rather than returning 0. A soak assertion
    written against a typo'd or renamed metric would otherwise pass forever while measuring
    nothing — and it would pass *most convincingly* on the run where the mechanism broke.

    Counters registered but never incremented legitimately read 0; that case is distinguished
    because the name resolves.
    """
    for suffix in ("", "_total"):
        value = REGISTRY.get_sample_value(f"{name}{suffix}", labels or {})
        if value is not None:
            return float(value)

    # ★ A LABELLED metric has no sample for a label combination until `.labels(...)` is first
    # called for it — prometheus_client does not materialise combinations up front. So "the
    # family exists but this label set has not been observed yet" must read 0, while "no such
    # metric" must still raise. Collapsing those two would restore exactly the hazard this
    # function exists to prevent, in the case most likely to matter: the first assertion written
    # against a brand-new counter.
    #
    # Found by using the harness for real. Its first labelled metric failed here, and the guard
    # was right to refuse — it was the RULE that was too coarse, not the check.
    family_names = {
        metric.name for metric in REGISTRY.collect()
    } | {
        f"{metric.name}_total" for metric in REGISTRY.collect()
    }
    if name in family_names or name.removesuffix("_total") in family_names:
        return 0.0

    known = sorted(metric.name for metric in REGISTRY.collect())
    raise AssertionError(
        f"metric {name!r} (labels={labels or {}}) is not registered — a soak assertion against "
        f"an unregistered metric measures nothing and passes forever. Known families include: "
        f"{known[:8]}{' …' if len(known) > 8 else ''}"
    )


def metric_exists(name: str) -> bool:
    """Whether a metric name resolves, without asserting. For skip conditions."""
    try:
        read_metric(name)
        return True
    except AssertionError:
        return False


@dataclass
class MetricDelta:
    """Before/after values for a set of metrics across one driven workload."""

    before: dict[str, float]
    after: dict[str, float] = field(default_factory=dict)

    def delta(self, name: str) -> float:
        return self.after[name] - self.before[name]

    def assert_increased(self, name: str, *, by_at_least: float = 1.0) -> float:
        moved = self.delta(name)
        assert moved >= by_at_least, (
            f"{name} moved by {moved}, expected at least {by_at_least}. The mechanism did not "
            f"do what the flag claims, or the assertion is reading the wrong metric."
        )
        return moved

    def assert_unchanged(self, name: str) -> None:
        moved = self.delta(name)
        assert moved == 0, f"{name} moved by {moved}; expected no change"


class metric_window:
    """Context manager capturing before/after values for named metrics.

    ::

        with metric_window("aindy_db_pool_exhaustion_events_total") as m:
            drive_concurrently(...)
        m.assert_unchanged("aindy_db_pool_exhaustion_events_total")
    """

    def __init__(self, *names: str, labels: Optional[dict[str, str]] = None) -> None:
        self._names = names
        self._labels = labels
        self._result: Optional[MetricDelta] = None

    def __enter__(self) -> MetricDelta:
        self._result = MetricDelta(before={n: read_metric(n, self._labels) for n in self._names})
        return self._result

    def __exit__(self, *exc: Any) -> None:
        assert self._result is not None
        self._result.after = {n: read_metric(n, self._labels) for n in self._names}


# ── Concurrent driver ────────────────────────────────────────────────────────


@dataclass
class DriveResult:
    """Outcome of a concurrent drive. ``failures`` is authoritative — see the class docstring."""

    results: list[Any]
    failures: list[BaseException]
    workers: int

    @property
    def ok(self) -> bool:
        return not self.failures

    def assert_all_succeeded(self) -> list[Any]:
        assert not self.failures, (
            f"{len(self.failures)} of {self.workers} workers raised. First: "
            f"{type(self.failures[0]).__name__}: {self.failures[0]}"
        )
        return self.results

    def assert_exactly_one_succeeded(self) -> Any:
        """For race invariants where exactly one caller may win."""
        assert len(self.results) == 1, (
            f"expected exactly 1 winner, got {len(self.results)} "
            f"(and {len(self.failures)} failures)"
        )
        return self.results[0]


def drive_concurrently(
    fn: Callable[[int], Any],
    *,
    workers: int = 8,
    barrier: bool = True,
) -> DriveResult:
    """Run ``fn(i)`` on ``workers`` threads and collect every result and every exception.

    ★ ``barrier=True`` (the default) holds every thread at a ``threading.Barrier`` until all are
    ready, then releases them together. Without it, thread-pool ramp-up serialises the calls and
    the test measures startup latency rather than contention — a concurrency test that is not
    concurrent, which is the failure mode this whole harness exists to avoid.

    ★ Every exception is surfaced. ``ThreadPoolExecutor`` holds them until ``.result()``, so a
    driver that skips that produces a green test over N failed workers.

    ★ **Each worker must open its own DB session.** A SQLAlchemy ``Session`` is not thread-safe,
    and sharing one across drivers reproduces `RT-MEMTXN-LEAK-1` rather than testing the flag.
    ``fn`` is responsible for that; the harness cannot enforce it.
    """
    gate = threading.Barrier(workers) if barrier else None
    results: list[Any] = []
    failures: list[BaseException] = []
    lock = threading.Lock()

    def _run(i: int) -> None:
        try:
            if gate is not None:
                gate.wait(timeout=30)
            value = fn(i)
        except BaseException as exc:  # noqa: BLE001 — collecting, then re-surfacing
            with lock:
                failures.append(exc)
            return
        with lock:
            results.append(value)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_run, range(workers)))

    return DriveResult(results=results, failures=failures, workers=workers)


def count_distinct(values: Iterable[Any], key: Callable[[Any], Any]) -> int:
    """Distinct ``key(v)`` across values — for "did the handler run once?" style invariants."""
    return len({key(v) for v in values})
