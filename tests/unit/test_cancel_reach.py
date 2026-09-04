"""`CANCEL-REACH-1` — a cancelled run stops at the next effect, not at the next segment.

`sys.v1.agent.cancel` commits a terminal status in a separate session, and the Nodus chain
observed it only **between segments** — its own comment says *"before this segment's tools run …
halts the chain between steps"*. So every remaining tool in the current segment ran to
completion, and a tool already inside `entry["fn"](…)` ran to completion too.

This narrows the window from segment granularity to effect granularity. It does **not** preempt:
a tool already executing is not interrupted; the *next* one is refused. Hard-kill is a function
of isolation class and belongs to `TOOL-SEAM-ISOLATION-1`.

★★ THE TWO PROPERTIES THAT ARE EASY TO GET BACKWARDS
------------------------------------------------------
**It fails OPEN**, unlike every other guard here. An unreadable cancellation state must mean "not
cancelled": refusing an effect because a database blip made the answer unreadable aborts live
work nobody cancelled, and that is not recoverable by retrying the check. **A missed cancel costs
one more effect; a false cancel costs the run.** A test that only checks "cancelled runs are
refused" would pass just as happily against a predicate that refuses everything — so the
fail-open cases carry as much weight here as the positive one.

**It must not query per effect.** `RT-MEMTXN-LEAK-1` exhausted the connection pool holding a
transaction across a slow call on a request-shared session; `MEM-RECALL-N1-1` was an N+1 in the
same family. A cancellation check called in a tool loop is exactly the shape that reproduces
both, so the query count is asserted, not assumed.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.runtime_only


@pytest.fixture(autouse=True)
def _clean_cache():
    from AINDY.kernel.cancellation import reset_cancellation_cache

    reset_cancellation_cache()
    yield
    reset_cancellation_cache()


# ── the predicate ────────────────────────────────────────────────────────────


def test_a_cancelled_run_is_observed():
    from AINDY.kernel import cancellation

    with patch.object(cancellation, "_read_status", return_value="cancelled"):
        assert cancellation.is_run_cancelled("run-1") is True


def test_a_live_run_is_not():
    """★ Liveness control. Without it, a predicate that returns True unconditionally passes the
    test above — and would refuse every effect in the runtime."""
    from AINDY.kernel import cancellation

    with patch.object(cancellation, "_read_status", return_value="executing"):
        assert cancellation.is_run_cancelled("run-2") is False


def test_no_run_id_is_never_cancelled():
    """The out-of-process tool worker passes `run_id=None`.

    An effect with no run to belong to cannot be cancelled by one, so `False` is the right return.

    ★ But it is a GAP, and this docstring said otherwise when it shipped. The justification —
    "that path is hard-killable by its isolation class instead" — names a capability nothing
    invokes: the worker dies on `subprocess.run(timeout=…)` and on nothing else, so a cancelled
    run's in-flight isolated tool still runs to completion. `CANCEL-REACH-1` residual 2.
    """
    from AINDY.kernel.cancellation import is_run_cancelled

    assert is_run_cancelled(None) is False
    assert is_run_cancelled("") is False


def test_an_unreadable_status_fails_open():
    """★★ THE PROPERTY MOST LIKELY TO BE 'FIXED' INTO A BUG.

    Every other guard in this runtime fails closed. This one must not: refusing an effect
    because the answer was unreadable aborts work nobody cancelled, and an aborted effect is not
    recovered by retrying the check.
    """
    from AINDY.kernel import cancellation

    with patch.object(cancellation, "_read_status", side_effect=RuntimeError("db down")):
        assert cancellation.is_run_cancelled("run-3") is False, (
            "an unreadable cancellation state refused the effect. A missed cancel costs one "
            "more effect; a false cancel costs the run."
        )


def test_a_missing_run_is_not_cancelled():
    """A row that does not exist is not a cancelled row."""
    from AINDY.kernel import cancellation

    with patch.object(cancellation, "_read_status", return_value=None):
        assert cancellation.is_run_cancelled("run-4") is False


# ── the constraint this repository has paid for twice ────────────────────────


def test_repeated_checks_do_not_query_per_effect():
    """★★ `RT-MEMTXN-LEAK-1` and `MEM-RECALL-N1-1`, both in this exact shape.

    A cancellation check called in a tool loop is precisely how an N+1 and a pool exhaustion get
    reintroduced. At most one read per run per TTL window.
    """
    from AINDY.kernel import cancellation

    with patch.object(cancellation, "_read_status", return_value="executing") as read:
        for _ in range(50):
            cancellation.is_run_cancelled("run-5", ttl_seconds=60.0)

    assert read.call_count == 1, (
        f"{read.call_count} status reads for 50 checks. A per-effect query on a hot path is the "
        f"shape that exhausted the connection pool once already."
    )


def test_a_cancelled_answer_is_never_re_read():
    """Cancellation is terminal — a run cannot un-cancel, so re-asking is pure cost.

    Asserted with a zero TTL, so only the terminal-caching path can keep the count at one.
    """
    from AINDY.kernel import cancellation

    with patch.object(cancellation, "_read_status", return_value="cancelled") as read:
        for _ in range(20):
            assert cancellation.is_run_cancelled("run-6", ttl_seconds=0.0) is True

    assert read.call_count == 1, (
        f"{read.call_count} reads for a run already known cancelled; a terminal answer should "
        f"be cached without expiry"
    )


def test_a_negative_answer_expires():
    """★ The other half of the caching, and the one that makes a cancel land at all.

    If negatives were cached forever, a run cancelled after its first tool would never be
    observed and this whole change would do nothing.
    """
    from AINDY.kernel import cancellation

    statuses = iter(["executing", "cancelled"])
    with patch.object(cancellation, "_read_status", side_effect=lambda _r: next(statuses)):
        assert cancellation.is_run_cancelled("run-7", ttl_seconds=0.0) is False
        assert cancellation.is_run_cancelled("run-7", ttl_seconds=0.0) is True


def test_the_read_uses_its_own_session_not_the_callers():
    """★ Asserted on the signature, because the caller's session is not even reachable here.

    `_read_status` takes only a run id and opens `SessionLocal` itself. A parameter for a
    caller-supplied session is how the request-shared session gets used on a slow path, which is
    the `RT-MEMTXN-LEAK-1` failure exactly.
    """
    import inspect

    from AINDY.kernel import cancellation

    params = list(inspect.signature(cancellation._read_status).parameters)
    assert params == ["run_id"], (
        f"_read_status takes {params}. It must not accept a session — using the caller's on this "
        f"path is what exhausted the connection pool in RT-MEMTXN-LEAK-1."
    )


# ── the chokepoint ───────────────────────────────────────────────────────────


def _capability_ok():
    """Stub the capability gate, which runs BEFORE this chokepoint.

    Left real, it refuses a fake token first and the test would pass for the wrong reason —
    green on a refusal that has nothing to do with cancellation.
    """
    return patch(
        "AINDY.agents.capability_service.check_tool_capability",
        return_value={"ok": True, "allowed_capabilities": [], "granted_tools": []},
    )


def test_the_tool_chokepoint_refuses_a_cancelled_run():
    """★★ THE BEHAVIOUR THE ENTRY ASKED FOR, at the seam it named.

    Asserted on the tool NOT running, not merely on the return shape — "refused" and "ran and
    then reported a refusal" produce the same envelope and opposite outcomes.
    """
    import AINDY.agents.tool_registry as tr

    ran: list[str] = []
    entry = {"fn": lambda **kw: ran.append("ran"), "name": "probe_tool"}

    with patch.dict(tr.TOOL_REGISTRY, {"probe_tool": entry}, clear=False),             patch.object(tr, "_ensure_tools_loaded", lambda: None),             _capability_ok(),             patch.object(tr, "is_run_cancelled", return_value=True):
        result = tr.execute_tool(
            "probe_tool", {}, user_id="u", db=None, run_id=str(uuid.uuid4()),
            execution_token={"token": "t"},
        )

    assert ran == [], "the tool executed despite its run being cancelled"
    assert result["success"] is False
    assert result.get("cancelled") is True
    assert "cancelled" in (result.get("error") or "")


def test_the_tool_chokepoint_does_not_refuse_a_live_run():
    """★ Liveness control for the chokepoint.

    Without it, a check that refused unconditionally would satisfy the test above while
    stopping every tool in the runtime — the most expensive possible way to pass a test.
    """
    import AINDY.agents.tool_registry as tr

    ran: list[str] = []

    def _fn(**kw):
        ran.append("ran")
        return {"ok": True}

    entry = {"fn": _fn, "name": "probe_tool_live"}

    with patch.dict(tr.TOOL_REGISTRY, {"probe_tool_live": entry}, clear=False),             patch.object(tr, "_ensure_tools_loaded", lambda: None),             _capability_ok(),             patch.object(tr, "is_run_cancelled", return_value=False):
        tr.execute_tool(
            "probe_tool_live", {}, user_id="u", db=None, run_id=str(uuid.uuid4()),
            execution_token={"token": "t"},
        )

    assert ran == ["ran"], "a live run's tool was not executed"
