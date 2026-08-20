"""Soak: the warm Nodus worker pool under CONTENTION, against REAL worker subprocesses.

`NODUS-WARMPOOL-1`'s remaining work is recorded as "soak, then flip". This is the soak, and it
exists because of a gap the existing suite names in its own docstring:

> `test_nodus_worker_pool.py`: *"…against fake processes/workers (no real subprocess) … "
> "End-to-end (a real warm worker serving a nodus script) is **app-side PG-tier integration**."*

**That deferral is the whole problem.** It handed the only end-to-end evidence to a consumer that
does not exercise it (`SUBSTRATE-WITNESS-1`), so the pool has run against fakes here and against
nothing there. Meanwhile CI has had `AINDY_NODUS_WARM_POOL=1` set on every PR — real evidence,
but **functional and sequential**: it shows the pool serves requests, not that it serves
*concurrent* ones correctly.

★ What only a concurrent test against real workers can reach
------------------------------------------------------------
The pool speaks **length-prefixed JSON over one worker's stdin/stdout**, and correctness rests on
`_checkout`/`_checkin` giving each caller exclusive use of a worker for the duration of a request.
If that exclusion is ever wrong, two callers interleave frames on one pipe and **one caller
receives another's result** — a silent, cross-tenant wrong answer.

Fakes cannot establish this (they have no pipe), and a sequential test cannot reach it (there is
never a second caller). So the central assertion here is **response correlation**: every caller
sends a distinguishable payload and must get *its own* result back.

★ It also tests a claim the module makes about itself
-----------------------------------------------------
`nodus_worker_pool`'s docstring says enabling the pool *"can never make execution worse than the
default"*, because any warm-path failure falls back to a fresh subprocess. That is an **absence
claim** — variant 6 — and it passes trivially against fakes. Here, `N > pool size` forces the
`PoolBusy` spill path with real processes.

★ The layer matters, and the first version of this file got it wrong
--------------------------------------------------------------------
`pool.execute()` **raises `PoolBusy`** when every worker is busy — that is its documented
contract, not a failure. The **adapter** is what spills to a fresh subprocess, via an
`except Exception` around the warm path. The first draft asserted that the *pool* spills, and
three tests failed for a reason that was entirely the test's fault.

Recorded because the mistake is easy to repeat: *"enabling the pool can never make execution
worse than the default"* is a claim about the **adapter path**. Asserting it against the pool
tests a different component and produces a red that looks like a product defect.

So the tests below split by layer: correlation and boundedness at the pool, backpressure as the
`PoolBusy` contract, and the never-worse claim where it actually lives.

**Cost note:** this spawns real Python subprocesses and is slower than the rest of the unit suite.
That is the point; it is why the evidence did not exist.
"""

from __future__ import annotations

import uuid

import pytest

import AINDY.runtime.nodus_worker_pool as pool_mod
from tests.integration.soak_harness import drive_concurrently

pytestmark = pytest.mark.runtime_only

pytest.importorskip("nodus.runtime.embedding")

POOL_SIZE = 2
WORKERS = 6  # deliberately > POOL_SIZE, to force contention and the spill path


@pytest.fixture
def _warm_pool(monkeypatch):
    """A small real pool, torn down afterwards.

    ★ `reset_pool()` on both sides. A leaked warm worker is a live subprocess that outlives the
    test and serves a later one, which would make the next run's isolation assertions meaningless.
    """
    monkeypatch.setenv("AINDY_NODUS_WARM_POOL", "1")
    monkeypatch.setenv("AINDY_NODUS_WARM_POOL_SIZE", str(POOL_SIZE))
    monkeypatch.setenv("AINDY_NODUS_WARM_PREWARM", "0")
    # ★ A long acquire timeout is what makes this a CONTENTION test rather than a PoolBusy test.
    # At the 2s default, callers beyond the pool size give up and raise instead of queueing — so
    # a worker is never handed from one caller to the next, which is precisely the reuse path
    # where a stale frame left in the pipe would surface.
    monkeypatch.setenv("AINDY_NODUS_WARM_ACQUIRE_TIMEOUT_MS", "120000")
    pool_mod.reset_pool()
    yield pool_mod.get_pool()
    pool_mod.reset_pool()


def _payload(marker: str) -> dict:
    """A script whose output uniquely identifies its caller."""
    return {
        "script": f'set_state("marker", "{marker}")\n',
        "state": {},
        "context": {"user_id": f"soak-{marker}"},
    }


# ── Liveness control ─────────────────────────────────────────────────────────


def test_liveness_a_single_warm_execution_works(_warm_pool):
    """★ If this fails, every assertion below is about a pool that cannot run anything."""
    marker = uuid.uuid4().hex[:8]
    result = _warm_pool.execute(_payload(marker), timeout_s=60.0)

    assert result.get("status") == "success", result
    assert result["output_state"]["marker"] == marker


# ── The property fakes and sequential tests cannot reach ─────────────────────


def test_no_caller_ever_receives_another_callers_result(_warm_pool):
    """★ THE assertion. Frame interleaving on a shared pipe would surface here and nowhere else.

    Six concurrent callers, a pool of two, each sending a unique marker. Every caller must get
    back the marker **it sent**. A mismatch is a silent cross-tenant wrong answer.
    """
    markers = [uuid.uuid4().hex[:8] for _ in range(WORKERS)]

    def _one(i: int) -> tuple[str, str]:
        result = _warm_pool.execute(_payload(markers[i]), timeout_s=90.0)
        assert result.get("status") == "success", result
        return markers[i], result["output_state"]["marker"]

    outcome = drive_concurrently(_one, workers=WORKERS)
    pairs = outcome.assert_all_succeeded()

    mismatches = [(sent, got) for sent, got in pairs if sent != got]
    assert not mismatches, (
        f"{len(mismatches)} of {WORKERS} callers received another caller's result: {mismatches}. "
        f"The pool's checkout/checkin exclusion is broken and two requests interleaved frames on "
        f"one worker's pipe — a silent cross-tenant wrong answer."
    )
    assert len({got for _, got in pairs}) == WORKERS, "results were not distinct"


def test_more_callers_than_workers_all_succeed_when_they_can_queue(_warm_pool):
    """With a generous acquire timeout, contention must serialise — never fail, never mix up.

    This is the worker-REUSE path: each of the six callers eventually gets one of two workers,
    so a worker is handed from one caller to the next while the pool is under load. That is where
    a stale frame left in a pipe would surface, and it cannot be reached sequentially.
    """
    statuses = drive_concurrently(
        lambda i: _warm_pool.execute(_payload(f"queue{i}"), timeout_s=120.0).get("status"),
        workers=WORKERS,
    ).assert_all_succeeded()

    assert set(statuses) == {"success"}, (
        f"{WORKERS} concurrent callers against a pool of {POOL_SIZE} produced {statuses}"
    )


def test_backpressure_is_poolbusy_not_a_failed_execution(monkeypatch):
    """★ The documented contract, at the layer that owns it.

    The pool does NOT spill. When every worker is busy past the acquire timeout it raises
    `PoolBusy`, and the ADAPTER catches it and falls back to a fresh subprocess. Asserting
    "the pool spills" tests the wrong component — the first draft of this file did exactly that
    and produced three reds that looked like a product defect.
    """
    monkeypatch.setenv("AINDY_NODUS_WARM_POOL", "1")
    monkeypatch.setenv("AINDY_NODUS_WARM_POOL_SIZE", "1")
    monkeypatch.setenv("AINDY_NODUS_WARM_PREWARM", "0")
    # ★ ZERO, not "small". At 200ms this assertion was TIMING-DEPENDENT: it passed locally 3/3
    # and failed in CI, where the runner was fast enough that every caller finished inside the
    # window and backpressure never fired. A soak that asserts a race outcome by racing is the
    # exact failure mode this suite exists to avoid, and CI caught me doing it.
    #
    # With 0, `remaining <= 0` short-circuits before any wait: a caller that finds the pool
    # saturated raises immediately. The barrier releases all four at once against a pool of one,
    # so exactly one wins and the rest raise — deterministic, not probable.
    monkeypatch.setenv("AINDY_NODUS_WARM_ACQUIRE_TIMEOUT_MS", "0")
    pool_mod.reset_pool()
    pool = pool_mod.get_pool()
    try:
        outcome = drive_concurrently(
            lambda i: pool.execute(_payload(f"busy{i}"), timeout_s=60.0), workers=4
        )
    finally:
        pool_mod.reset_pool()

    assert outcome.failures, (
        "a pool of 1 under 4 simultaneous callers with a zero acquire timeout must exert "
        "backpressure; no caller was refused, so the saturation path did not run"
    )
    assert len(outcome.failures) == 3, (
        f"exactly one caller should win the single worker and three should be refused; got "
        f"{len(outcome.results)} served and {len(outcome.failures)} refused"
    )
    assert all(isinstance(e, pool_mod.PoolBusy) for e in outcome.failures), (
        f"backpressure must be PoolBusy so the adapter can recognise it and spill; got "
        f"{[type(e).__name__ for e in outcome.failures]}"
    )


def test_the_adapter_treats_poolbusy_as_a_fallback_not_a_failure():
    """★ Where the "never worse than the default" claim actually lives.

    Pinned by reading the adapter's structure rather than by driving a full execution: the warm
    path is wrapped in `except Exception` that logs and falls through to the fresh-subprocess
    path, so `PoolBusy` cannot become a failed execution. Source-level, and labelled as such —
    `ROUTE-GUARD-1` says a source assertion is a supplement, never the coverage. The behavioural
    half is `test_backpressure_is_poolbusy_not_a_failed_execution` above, which proves the
    exception type the adapter relies on.
    """
    import inspect

    from AINDY.runtime import nodus_runtime_adapter

    src = inspect.getsource(nodus_runtime_adapter)
    warm = src[src.index("warm_pool_enabled()"):]
    # ★ Anchor on a CONTIGUOUS fragment. The full sentence "falling back to a fresh subprocess"
    # is split across two source lines by the string concatenation in that logger call, so
    # searching for it returns -1 — which the first draft did, and the slice silently became the
    # whole rest of the module.
    warm = warm[: warm.index("warm worker failed for")]

    assert "except Exception" in warm, (
        "the adapter no longer catches every warm-path failure — PoolBusy would surface as a "
        "failed execution and enabling the pool WOULD make things worse than the default"
    )


def test_the_pool_never_exceeds_its_configured_size(_warm_pool):
    """★ Under contention the pool must bound itself. An unbounded pool under load is the failure
    that would make enabling this worse than the fresh-subprocess default it replaces."""
    drive_concurrently(
        lambda i: _warm_pool.execute(_payload(f"bound{i}"), timeout_s=90.0),
        workers=WORKERS,
    ).assert_all_succeeded()

    assert _warm_pool._size <= POOL_SIZE, (
        f"pool grew to {_warm_pool._size} workers against a configured size of {POOL_SIZE}"
    )


def test_no_callers_state_bleeds_into_another_on_a_reused_worker(_warm_pool):
    """Cross-request bleed at the POOL layer: a caller's result must contain only its own keys.

    ★ Scope, stated so this is not read as more than it is. The guest-memory leak that made
    `nodus-lang <= 5.0.2` dangerous is a MODULE GLOBAL inside the dependency, and its guard is
    `test_nodus_upgrade_contract.py::test_two_runtimes_in_one_process_do_not_share_guest_memory`
    — two runtimes in one process. This test covers the layer above: two *callers* through one
    pool, where a worker is handed from one to the next under contention. Neither subsumes the
    other, and this one cannot detect an import-bound leak.

    ★ Two of this test's own bugs are worth remembering. The first draft used
    `get_state("leaked") or "clean"`, which the VM rejects — nodus has no `or` in that position,
    so a soak assertion has to be a script the runtime will actually run. The second paired
    `results[i]` with caller `i` while `drive_concurrently` returned completion order, which
    failed 3/3 and read exactly like a cross-request bleed in the pool. **The harness now returns
    worker-index order**, and this test self-correlates anyway rather than trusting position.
    """
    def _one(i: int) -> dict:
        result = _warm_pool.execute(
            {
                "script": f'set_state("key_{i}", "value_{i}")\n',
                "state": {},
                "context": {"user_id": f"tenant-{i}"},
            },
            timeout_s=120.0,
        )
        assert result.get("status") == "success", result
        return i, result["output_state"]

    pairs = drive_concurrently(_one, workers=WORKERS).assert_all_succeeded()

    for i, state in pairs:  # self-correlating: the caller index travels with its own result
        foreign = [k for k in state if k.startswith("key_") and k != f"key_{i}"]
        assert not foreign, (
            f"caller {i} received keys set by another caller: {foreign}. A reused warm worker is "
            f"carrying state across concurrent requests."
        )
