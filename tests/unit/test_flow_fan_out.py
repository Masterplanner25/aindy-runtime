"""FLOW-PARALLEL-1 phase 1 — declared fan-out, bounded width, per-branch sessions.

Design: `docs/runtime/FLOW_PARALLEL_DESIGN.md`. Phase 0 (#603) widened the transaction — ordinals
allocated for a whole superstep at the barrier, the merge moved out of per-node status handling.
Both already took a list of one. This is what lengthens it.

★★ **The two tests that matter most are not the happy path:**

- `test_existing_flow_signatures_are_unchanged` — §7's *"the one that would bite"*. Every
  suspended run is hashed under today's canonicalisation, so a canonicaliser that re-encoded the
  existing edge shapes would quarantine every in-flight run on upgrade.
- `test_flow_fan_out_leaves_db_headroom` — §3(a). A branch holds a DB session, and that budget is
  **shared with request handling**. `SYSMAX-5` was fixed by isolation rather than capacity on
  exactly this finding, so an unbounded width is an API availability regression, not just
  slowness.

★ **The flag gates CONCURRENCY, not SEMANTICS**, and `test_sequential_and_concurrent_agree` is
what pins that: turning it off must change timing and nothing else, or "off" becomes a second,
less-tested semantics.
"""

from __future__ import annotations

import threading
import time

import pytest

pytestmark = pytest.mark.runtime_only

from AINDY.runtime.flow_engine.fan_out import (  # noqa: E402
    FanOutEdgeGroup,
    fan_out_concurrency_enabled,
    max_fan_out_width,
    reset_branch_executor_for_tests,
    run_fan_out_branches,
)
from AINDY.runtime.flow_engine.graph_signature import flow_topology_signature  # noqa: E402
from AINDY.runtime.flow_engine.node_executor import resolve_frontier  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_pool():
    reset_branch_executor_for_tests()
    yield
    reset_branch_executor_for_tests()


# ── ★ §7 — the upgrade-safety regression ─────────────────────────────────────


def test_existing_flow_signatures_are_unchanged():
    """★ Pinned against literals captured BEFORE the third shape existed.

    Not a self-comparison: these digests were computed from the previous canonicaliser and
    written down. If introducing `FanOutEdgeGroup` had reordered or re-spelled either existing
    shape, every suspended run planned against these flows would quarantine on upgrade — which
    is precisely what `FLOW-GRAPH-SIGNATURE-1`'s *absent ≠ mismatch* rule exists to prevent.
    """
    from AINDY.runtime.nodus_adapter import AGENT_FLOW, NODUS_COMPILE_AND_RUN_FLOW
    from AINDY.runtime.nodus_runtime_adapter import NODUS_SCRIPT_FLOW

    expected = {
        "AGENT_FLOW": "23df7b6edd577ce22afd34c9a07695c0a6d3c528fa5ad8d3dc4806daa59886e3",
        "NODUS_COMPILE_AND_RUN_FLOW":
            "9fbd6b33e5a04a1b40f16f447db2c8db3e239c6ee72edb211b9b0c26f2d730a4",
        "NODUS_SCRIPT_FLOW": "77c1d6deefda49e6aeff69dc0cb868880096df8f1bbd9b9af04cc847a0770f44",
    }
    actual = {
        "AGENT_FLOW": flow_topology_signature(AGENT_FLOW),
        "NODUS_COMPILE_AND_RUN_FLOW": flow_topology_signature(NODUS_COMPILE_AND_RUN_FLOW),
        "NODUS_SCRIPT_FLOW": flow_topology_signature(NODUS_SCRIPT_FLOW),
    }
    assert actual == expected, (
        "a live flow's graph signature changed. Every suspended run recorded under the old "
        "signature would be QUARANTINED on upgrade. If a flow's topology genuinely changed, "
        "that is correct and this expectation moves; if only the canonicaliser changed, it is "
        "the bug FLOW-GRAPH-SIGNATURE-1 warns about."
    )


def test_declaring_a_group_does_change_the_signature():
    """★ The other half, and it is the mechanism WORKING rather than a problem.

    Adding a group is a topology change, so a run planned against the sequential shape must
    quarantine. A test asserting only the previous property would be satisfied by a
    canonicaliser that ignored fan-out groups entirely.
    """
    sequential = {"start": "a", "edges": {"a": ["b"], "b": []}, "end": ["b"]}
    parallel = {"start": "a", "edges": {"a": [FanOutEdgeGroup(["b", "c"])], "b": [], "c": []},
                "end": ["b"]}

    assert flow_topology_signature(sequential) != flow_topology_signature(parallel)


def test_the_group_encoding_is_order_sensitive():
    """Declaration order is topology: it decides merge order and ordinal order."""
    ab = {"edges": {"a": [FanOutEdgeGroup(["b", "c"])]}}
    ba = {"edges": {"a": [FanOutEdgeGroup(["c", "b"])]}}

    assert flow_topology_signature(ab) != flow_topology_signature(ba)


# ── The declared shape ───────────────────────────────────────────────────────


def test_a_group_of_one_is_refused():
    """A group of one is a sequential edge, and declaring it would change the signature for
    no behaviour — a quarantine with nothing to show for it."""
    with pytest.raises(ValueError, match="at least two targets"):
        FanOutEdgeGroup(["only"])


def test_duplicate_targets_are_refused():
    """The same node twice would allocate two ordinals for one node and merge its patch against
    itself."""
    with pytest.raises(ValueError, match="duplicate targets"):
        FanOutEdgeGroup(["a", "b", "a"])


def test_targets_are_immutable():
    """★ Declaration order is the contract; a mutable sequence could be reordered after the
    graph signature was taken."""
    group = FanOutEdgeGroup(["a", "b"])
    assert isinstance(group.targets, tuple)


# ── resolve_frontier: the existing path must be byte-for-byte ────────────────


def test_a_bare_string_edge_still_yields_a_frontier_of_one():
    flow = {"edges": {"a": ["b"]}}
    assert resolve_frontier("a", {}, flow) == ["b"]


def test_a_conditional_edge_still_yields_a_frontier_of_one():
    flow = {"edges": {"a": [
        {"target": "b", "condition": lambda s: s.get("go") == "b"},
        {"target": "c", "condition": lambda s: True},
    ]}}
    assert resolve_frontier("a", {"go": "b"}, flow) == ["b"]
    assert resolve_frontier("a", {"go": "z"}, flow) == ["c"]


def test_a_terminal_node_yields_an_empty_frontier():
    assert resolve_frontier("a", {}, {"edges": {"a": []}}) == []


def test_a_group_yields_every_target_in_declaration_order():
    flow = {"edges": {"a": [FanOutEdgeGroup(["x", "y", "z"])]}}
    assert resolve_frontier("a", {}, flow) == ["x", "y", "z"]


def test_a_group_mixed_with_sibling_edges_is_refused():
    """A group IS the successor set for a node; mixing has no defined order."""
    flow = {"edges": {"a": [FanOutEdgeGroup(["x", "y"]), "z"]}}
    with pytest.raises(ValueError, match="alongside"):
        resolve_frontier("a", {}, flow)


# ── The flag: concurrency, not semantics ─────────────────────────────────────


def test_the_flag_is_off_by_default(monkeypatch):
    monkeypatch.delenv("AINDY_FLOW_FAN_OUT", raising=False)
    assert fan_out_concurrency_enabled() is False


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("on", True), ("yes", True),
    ("0", False), ("false", False), ("", False), ("  ", False), ("perhaps", False),
])
def test_the_flag_is_opt_in(monkeypatch, value, expected):
    monkeypatch.setenv("AINDY_FLOW_FAN_OUT", value)
    assert fan_out_concurrency_enabled() is expected


def _recording_execute(order, lock, delay_for=None):
    def _execute(node, state, context):
        if delay_for and node in delay_for:
            time.sleep(delay_for[node])
        with lock:
            order.append(node)
        return {"status": "SUCCESS", "output_patch": {node: True}}
    return _execute


def test_results_are_in_declaration_order_even_when_completion_is_reversed(monkeypatch):
    """★★ The property the whole entry exists for.

    The first branch is made the slowest, so completion order is the REVERSE of declaration
    order. `state_merge` resolves `last_write_wins` in declaration order, so returning completion
    order would make merged state depend on a race.
    """
    monkeypatch.setenv("AINDY_FLOW_FAN_OUT", "1")
    reset_branch_executor_for_tests()

    completion, lock = [], threading.Lock()
    execute = _recording_execute(completion, lock, delay_for={"a": 0.25, "b": 0.10})

    results = run_fan_out_branches(["a", "b", "c"], {}, {}, execute)

    assert [r["node"] for r in results] == ["a", "b", "c"], "results were not declaration-ordered"
    assert completion != ["a", "b", "c"], (
        "the branches did not actually finish out of order, so this test proved nothing "
        f"(completion was {completion}) — the delays are the control"
    )


def test_sequential_and_concurrent_agree(monkeypatch):
    """★ The flag must change timing and NOTHING else.

    If off and on produced different results, "off" would be a second semantics that only the
    rollback path exercises.
    """
    def execute(node, state, context):
        return {"status": "SUCCESS", "output_patch": {"seen": node}}

    monkeypatch.delenv("AINDY_FLOW_FAN_OUT", raising=False)
    off = run_fan_out_branches(["a", "b", "c"], {}, {}, execute)

    monkeypatch.setenv("AINDY_FLOW_FAN_OUT", "1")
    reset_branch_executor_for_tests()
    on = run_fan_out_branches(["a", "b", "c"], {}, {}, execute)

    assert [r["node"] for r in off] == [r["node"] for r in on]
    assert [r["result"] for r in off] == [r["result"] for r in on]


def test_a_branch_failure_is_reported_not_raised(monkeypatch):
    """One bad branch must not abandon the rest — the runner decides what a failure means."""
    monkeypatch.setenv("AINDY_FLOW_FAN_OUT", "1")
    reset_branch_executor_for_tests()

    def execute(node, state, context):
        if node == "b":
            raise RuntimeError("branch b exploded")
        return {"status": "SUCCESS", "output_patch": {}}

    results = run_fan_out_branches(["a", "b", "c"], {}, {}, execute)

    assert [r["node"] for r in results] == ["a", "b", "c"]
    assert results[1]["error"] and "exploded" in results[1]["error"]
    assert results[0]["error"] is None and results[2]["error"] is None


def test_a_branch_cannot_mutate_the_runners_state(monkeypatch):
    """★ Branches return patches; the runner merges centrally (§3c). A branch writing state
    directly would need cross-session coordination the runtime has no primitive for."""
    monkeypatch.setenv("AINDY_FLOW_FAN_OUT", "1")
    reset_branch_executor_for_tests()

    shared = {"untouched": True}

    def execute(node, state, context):
        state["mutated_by"] = node
        return {"status": "SUCCESS", "output_patch": {}}

    run_fan_out_branches(["a", "b"], shared, {}, execute)

    assert shared == {"untouched": True}, "a branch mutated the runner's state dict"


# ── ★ §3(a) — the bound, accounted against the shared DB budget ──────────────


def test_flow_fan_out_leaves_db_headroom():
    """★★ The constraint that makes "just raise the width" wrong.

    Every branch can hold a DB session, and `DB_POOL_SIZE + DB_MAX_OVERFLOW` is the budget
    **shared with request handling and the scheduler**. `SYSMAX-5` was fixed by isolation rather
    than capacity on exactly this finding: more threads on one budget starve the API instead of
    speeding anything up.

    Asserted as a ratio, mirroring `test_total_scheduler_threads_leave_db_headroom`, so it keeps
    meaning something if either side is retuned.
    """
    from AINDY.config import settings

    capacity = settings.DB_POOL_SIZE + settings.DB_MAX_OVERFLOW
    width = max_fan_out_width()

    assert width <= capacity // 4, (
        f"fan-out width {width} against {capacity} shared DB connections. Branches compete with "
        "request handling AND the scheduler's lanes for one budget."
    )


def test_scheduler_lanes_and_fan_out_together_leave_request_headroom():
    """★ Neither pool is sized against the other today, and this is where that would surface.

    The scheduler's own test asserts its lanes alone stay under half the budget. Fan-out is a
    SECOND process-wide pool drawing on the same connections, so the sum is what an operator
    actually experiences.
    """
    from AINDY.config import settings

    capacity = settings.DB_POOL_SIZE + settings.DB_MAX_OVERFLOW
    scheduler_threads = 10 + 2 + 1  # default / recovery / waits — scheduler_service.py
    total = scheduler_threads + max_fan_out_width()

    assert total <= capacity - (capacity // 3), (
        f"scheduler lanes ({scheduler_threads}) plus fan-out width ({max_fan_out_width()}) "
        f"= {total} of {capacity} shared connections, leaving too little for request handling. "
        "RT-MEMTXN-LEAK-1 is what this looks like when it goes wrong: a 42s login."
    )


def test_the_width_is_a_resource_ceiling_not_a_declaration_limit(monkeypatch):
    """A group wider than the pool still runs — in waves. Width bounds concurrency, not shape."""
    monkeypatch.setenv("AINDY_FLOW_FAN_OUT", "1")
    monkeypatch.setenv("AINDY_FLOW_FAN_OUT_MAX_WIDTH", "2")
    reset_branch_executor_for_tests()

    def execute(node, state, context):
        return {"status": "SUCCESS", "output_patch": {}}

    results = run_fan_out_branches(["a", "b", "c", "d", "e"], {}, {}, execute)
    assert [r["node"] for r in results] == ["a", "b", "c", "d", "e"]


def test_concurrency_is_actually_bounded(monkeypatch):
    """★ Liveness for the bound: without this, the width could be ignored and every test above
    would still pass."""
    monkeypatch.setenv("AINDY_FLOW_FAN_OUT", "1")
    monkeypatch.setenv("AINDY_FLOW_FAN_OUT_MAX_WIDTH", "2")
    reset_branch_executor_for_tests()

    live, peak, lock = 0, [0], threading.Lock()

    def execute(node, state, context):
        nonlocal live
        with lock:
            live += 1
            peak[0] = max(peak[0], live)
        time.sleep(0.05)
        with lock:
            live -= 1
        return {"status": "SUCCESS", "output_patch": {}}

    run_fan_out_branches(["a", "b", "c", "d", "e", "f"], {}, {}, execute)

    assert peak[0] <= 2, f"{peak[0]} branches ran at once against a declared width of 2"
    assert peak[0] > 1, "nothing ran concurrently, so the bound was not what limited it"


# ── ★★ §5/§3 — the constraint the whole design is shaped around ──────────────


def test_each_branch_gets_its_own_session(monkeypatch):
    """★★ `AGENT_WORKING_RULES` §5 is unconditional: never share a SQLAlchemy session across
    threads. The failure is **silent corruption**, not an exception — so nothing else in this
    file would notice it.

    Found by mutation testing: replacing the per-branch session with the runner's survived every
    other test here. A constraint that shapes an entire design and is untested is the shape
    `AUTHORITY-NEGOTIATION-1`'s phase-0 guard existed to prevent.
    """
    handed_out = []
    closed = []

    class _FakeSession:
        def __init__(self, n):
            self.n = n

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            closed.append(self.n)

    counter = {"n": 0}

    def _session_local():
        counter["n"] += 1
        s = _FakeSession(counter["n"])
        handed_out.append(s)
        return s

    import AINDY.db.database as db_mod
    monkeypatch.setattr(db_mod, "SessionLocal", _session_local, raising=True)

    runner_db = object()
    seen = []

    def execute(node, state, context):
        seen.append(context["db"])
        return {"status": "SUCCESS", "output_patch": {}}

    run_fan_out_branches(["a", "b", "c"], {}, {"db": runner_db}, execute)

    assert len(seen) == 3
    assert runner_db not in seen, "a branch executed on the RUNNER's session"
    assert len({id(s) for s in seen}) == 3, (
        f"branches shared a session: {seen}. Sessions are not thread-safe and the failure is "
        "silent corruption."
    )
    assert sorted(closed) == [1, 2, 3], f"a branch session was not closed: closed={closed}"


def test_a_failing_branch_still_closes_its_session(monkeypatch):
    """★ N branches multiply session-holding time by N (`RT-MEMTXN-LEAK-1`), so a leak on the
    failure path is worse here than on the sequential one."""
    closed = []

    class _FakeSession:
        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            closed.append(1)

    import AINDY.db.database as db_mod
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _FakeSession(), raising=True)

    def execute(node, state, context):
        raise RuntimeError("boom")

    run_fan_out_branches(["a", "b"], {}, {}, execute)

    assert len(closed) == 2, f"sessions leaked on the failure path: closed {len(closed)} of 2"


# ── ★ The runner integration: _execute_superstep ─────────────────────────────
#
# Everything above tests the fan-out primitive. None of it proves the RUNNER uses it, refuses a
# WAIT, enforces convergence, or allocates one contiguous block of ordinals for the superstep.
# `CLAUDE.md`'s route rule generalises: reading the handler proves the code was written, not that
# the caller receives its answer.


class _FakeQuery:
    def __init__(self, value=None):
        self._value = value

    def filter(self, *a, **kw):
        return self

    def scalar(self):
        return self._value


class _FakeRunnerDB:
    def __init__(self, highest_ordinal=None):
        self.added = []
        self.commits = 0
        self._highest = highest_ordinal

    def query(self, *a, **kw):
        return _FakeQuery(self._highest)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1


class _Run:
    id = "11111111-1111-1111-1111-111111111111"
    current_node = "fan"


def _runner(flow, highest_ordinal=None):
    """A minimal object carrying the real bound methods under test."""
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    class _R:
        db = _FakeRunnerDB(highest_ordinal)
        _execute_superstep = PersistentFlowRunner._execute_superstep
        _merge_superstep = PersistentFlowRunner._merge_superstep
        _allocate_sequence_numbers = PersistentFlowRunner._allocate_sequence_numbers

    r = _R()
    r.flow = flow
    return r


_CONVERGING_FLOW = {
    "edges": {
        "fan": [FanOutEdgeGroup(["b", "c"])],
        "b": ["join"],
        "c": ["join"],
        "join": [],
    }
}


def test_the_superstep_runs_every_branch_and_converges(monkeypatch):
    monkeypatch.delenv("AINDY_FLOW_FAN_OUT", raising=False)
    import AINDY.runtime.flow_engine as fe

    monkeypatch.setattr(
        fe, "execute_node",
        lambda node, state, ctx: {"status": "SUCCESS", "output_patch": {node: True}},
        raising=False,
    )
    r = _runner(_CONVERGING_FLOW)
    state = {}

    nxt = r._execute_superstep(_Run(), state, {"db": object()}, ["b", "c"], None)

    assert nxt == "join"
    assert state == {"b": True, "c": True}, "the superstep's patches were not merged centrally"


def test_the_superstep_allocates_one_contiguous_ordinal_block(monkeypatch):
    """★ §4 — allocated at the barrier in declaration order, not per branch as it finishes.

    `DUR-4`'s fold depends on the ordinal being a deterministic total order; per-branch
    allocation under concurrency would collide.
    """
    monkeypatch.delenv("AINDY_FLOW_FAN_OUT", raising=False)
    import AINDY.runtime.flow_engine as fe

    monkeypatch.setattr(
        fe, "execute_node",
        lambda node, state, ctx: {"status": "SUCCESS", "output_patch": {}},
        raising=False,
    )
    r = _runner(_CONVERGING_FLOW, highest_ordinal=7)

    r._execute_superstep(_Run(), {}, {"db": object()}, ["b", "c"], None)

    rows = [(h.node_name, h.sequence_number) for h in r.db.added]
    assert rows == [("b", 8), ("c", 9)], (
        f"ordinals were not one contiguous block in declaration order: {rows}"
    )


def test_a_wait_inside_a_group_is_refused_before_anything_is_written(monkeypatch):
    """★★ §5 option 3, approved 2026-09-08 — declared and loud, never discovered.

    Refused BEFORE any FlowHistory row is written: holding the other branches' patches across a
    suspension would need a durable partial-superstep record that does not exist.
    """
    from AINDY.runtime.flow_engine.fan_out import FanOutWaitRefused

    monkeypatch.delenv("AINDY_FLOW_FAN_OUT", raising=False)
    import AINDY.runtime.flow_engine as fe

    monkeypatch.setattr(
        fe, "execute_node",
        lambda node, state, ctx: (
            {"status": "WAIT"} if node == "c" else {"status": "SUCCESS", "output_patch": {}}
        ),
        raising=False,
    )
    r = _runner(_CONVERGING_FLOW)

    with pytest.raises(FanOutWaitRefused, match=r"\['c'\]"):
        r._execute_superstep(_Run(), {}, {"db": object()}, ["b", "c"], None)

    assert r.db.added == [], "history was written before the WAIT was refused"


def test_branches_that_do_not_converge_are_refused(monkeypatch):
    """★ Phase 1's degenerate `all` join, ENFORCED rather than picked.

    Silently choosing one successor would make the flow's continuation depend on which branch the
    runner happened to ask first.
    """
    monkeypatch.delenv("AINDY_FLOW_FAN_OUT", raising=False)
    import AINDY.runtime.flow_engine as fe

    monkeypatch.setattr(
        fe, "execute_node",
        lambda node, state, ctx: {"status": "SUCCESS", "output_patch": {}},
        raising=False,
    )
    diverging = {
        "edges": {
            "fan": [FanOutEdgeGroup(["b", "c"])],
            "b": ["join_one"],
            "c": ["join_two"],   # ← disagrees
        }
    }
    r = _runner(diverging)

    with pytest.raises(ValueError, match="did not converge"):
        r._execute_superstep(_Run(), {}, {"db": object()}, ["b", "c"], None)


def test_a_failed_branch_fails_the_superstep(monkeypatch):
    """Phase 1 has no join policy, so a failed branch fails the superstep. `any` and `quorum(k)`
    are phase 2 — this pins that phase 1 does not silently tolerate a loss."""
    monkeypatch.delenv("AINDY_FLOW_FAN_OUT", raising=False)
    import AINDY.runtime.flow_engine as fe

    monkeypatch.setattr(
        fe, "execute_node",
        lambda node, state, ctx: (
            {"status": "FAILURE", "error": "nope"} if node == "c"
            else {"status": "SUCCESS", "output_patch": {}}
        ),
        raising=False,
    )
    r = _runner(_CONVERGING_FLOW)

    with pytest.raises(ValueError, match="failed in the fan-out group"):
        r._execute_superstep(_Run(), {}, {"db": object()}, ["b", "c"], None)

    # ★ History IS written for a failed superstep — the record must show what was attempted.
    assert [h.node_name for h in r.db.added] == ["b", "c"]
