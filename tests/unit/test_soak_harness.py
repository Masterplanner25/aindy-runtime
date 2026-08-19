"""Guards the soak harness itself — it needs no database, so it runs in the unit suite.

★ **Why an instrument needs its own test here specifically.** This repository's catalogue is
mostly about verification that looks like it works and does not: a suite collected by no job, a
skip that reads green, a check whose condition the release does not contain. A concurrency
harness that quietly fails to create concurrency is the same shape and worse, because its output
is *evidence* — a soak result that reads as proof and is not would be cited in a flag-flip
decision.

So the property that matters most is tested directly: :func:`drive_concurrently` must hold its
workers until all have arrived. If it serialises, every soak assertion written on top of it
measures sequential behaviour while claiming to measure contention.

★ Two timing-based versions of that test were written and both were killed by mutation testing
before this one survived — see ``test_every_worker_is_held_at_a_barrier_until_all_have_arrived``
for what each got wrong. Recorded because the failure mode is general: a concurrency assertion
can look rigorous and be measuring the thread pool.
"""

from __future__ import annotations

import threading
import time

import pytest

from tests.integration.soak_harness import (
    DriveResult,
    count_distinct,
    drive_concurrently,
    metric_exists,
    metric_window,
    read_metric,
)

pytestmark = pytest.mark.runtime_only


# ── The property the whole harness rests on ──────────────────────────────────


def test_every_worker_is_held_at_a_barrier_until_all_have_arrived(monkeypatch):
    """★ The harness's central property. If workers are not held, every soak built on it
    measures ramp-up latency rather than contention.

    ★ This asserts on the mechanism rather than on timing, and that is a deliberate retreat
    after mutation testing killed two timing-based versions:

    * measuring *peak overlap* survived removing the barrier entirely — with
      ``max_workers == workers`` the pool overlaps on its own, so the test was proving the pool
      had enough slots while reading as proof of the barrier;
    * measuring *arrival order* with a stagger was worse — the stagger ran **after** the
      barrier, so it observed nothing at all.

    The honest position: with ``max_workers == workers`` the barrier's contribution **is not
    observable by timing**, because the pool already supplies the parallelism. It is
    defence-in-depth for the case that actually bites — short work, where thread ramp-up lets
    worker 0 finish before worker 7 starts. A test that cannot distinguish its presence by
    observation must assert its presence directly, or admit it is testing nothing.
    """
    seen: dict = {}
    real_barrier = threading.Barrier

    def _spy(parties, *a, **kw):
        seen["parties"] = parties
        barrier = real_barrier(parties, *a, **kw)
        seen["waits"] = 0
        original_wait = barrier.wait

        def _counting_wait(*wa, **wkw):
            seen["waits"] += 1
            return original_wait(*wa, **wkw)

        barrier.wait = _counting_wait  # type: ignore[method-assign]
        return barrier

    monkeypatch.setattr("tests.integration.soak_harness.threading.Barrier", _spy)

    workers = 5
    outcome = drive_concurrently(lambda i: i, workers=workers)

    assert outcome.ok, outcome.failures
    assert seen.get("parties") == workers, (
        f"no barrier was constructed for {workers} workers — the drive does not hold them, so "
        f"any soak built on it may be measuring sequential execution"
    )
    assert seen.get("waits") == workers, (
        f"{seen.get('waits')} of {workers} workers waited on the barrier; every worker must"
    )


def test_without_the_barrier_overlap_is_not_guaranteed():
    """The control for the control: `barrier=False` exists and is a different code path.

    Deliberately asserts only that it runs and reports every worker — timing-dependent overlap
    is exactly the flakiness the barrier exists to remove, so this must not assert on peak.
    """
    outcome = drive_concurrently(lambda i: i, workers=4, barrier=False)
    assert outcome.workers == 4
    assert sorted(outcome.results) == [0, 1, 2, 3]


# ── Exception surfacing ──────────────────────────────────────────────────────


def test_worker_exceptions_are_surfaced_not_swallowed():
    """★ ThreadPoolExecutor holds exceptions until .result(); a driver that skips that produces
    a green test over N failed workers."""
    outcome = drive_concurrently(
        lambda i: (_ for _ in ()).throw(ValueError(f"boom {i}")), workers=4
    )

    assert outcome.ok is False
    assert len(outcome.failures) == 4
    assert all(isinstance(e, ValueError) for e in outcome.failures)
    assert outcome.results == []


def test_assert_all_succeeded_reports_the_first_failure():
    outcome = drive_concurrently(
        lambda i: 1 / 0 if i == 2 else i, workers=4
    )
    with pytest.raises(AssertionError, match="ZeroDivisionError"):
        outcome.assert_all_succeeded()


def test_partial_failure_keeps_both_halves():
    outcome = drive_concurrently(lambda i: 1 / 0 if i % 2 else i, workers=4)
    assert len(outcome.results) == 2
    assert len(outcome.failures) == 2


def test_assert_exactly_one_succeeded():
    ok = DriveResult(results=["winner"], failures=[RuntimeError("x")] * 3, workers=4)
    assert ok.assert_exactly_one_succeeded() == "winner"

    with pytest.raises(AssertionError, match="expected exactly 1 winner"):
        DriveResult(results=[1, 2], failures=[], workers=2).assert_exactly_one_succeeded()


# ── Metric readback ──────────────────────────────────────────────────────────


def test_an_unregistered_metric_raises_rather_than_reading_zero():
    """★ The trap this guards: `get_sample_value` returns None for an unknown name, and
    None-as-zero makes 'the counter did not move' and 'the counter does not exist'
    indistinguishable. A soak assertion against a renamed metric would pass forever."""
    with pytest.raises(AssertionError, match="not registered"):
        read_metric("aindy_definitely_not_a_metric")


def test_a_registered_counter_reads_even_when_never_incremented():
    """Zero must remain readable, or the guard above would make every unused counter unusable."""
    assert read_metric("aindy_db_pool_exhaustion_events_total") >= 0.0


def test_counter_names_resolve_with_or_without_the_total_suffix():
    """prometheus_client stores counters as `<name>_total`; assertions are written both ways."""
    assert metric_exists("aindy_db_pool_exhaustion_events_total")
    assert metric_exists("aindy_db_pool_exhaustion_events")


def test_metric_exists_is_false_for_unknown_names():
    assert metric_exists("aindy_nope_not_here") is False


def test_metric_window_captures_a_real_delta():
    from AINDY.platform_layer.metrics import db_pool_exhaustion_events_total

    name = "aindy_db_pool_exhaustion_events_total"
    with metric_window(name) as m:
        db_pool_exhaustion_events_total.inc()

    assert m.delta(name) == 1.0
    m.assert_increased(name)

    with pytest.raises(AssertionError, match="moved by 1.0"):
        m.assert_unchanged(name)


def test_metric_window_detects_no_movement():
    name = "aindy_db_pool_exhaustion_events_total"
    with metric_window(name) as m:
        pass

    m.assert_unchanged(name)
    with pytest.raises(AssertionError, match="expected at least"):
        m.assert_increased(name)


def test_count_distinct():
    assert count_distinct([{"a": 1}, {"a": 1}, {"a": 2}], key=lambda v: v["a"]) == 2
