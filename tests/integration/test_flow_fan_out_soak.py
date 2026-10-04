"""FLOW-PARALLEL-1 phase 4 — fan-out on real Postgres, the evidence the default flip rests on.

The unit suite proves the engine's contract with FAKE sessions, and its one real-database test runs
the SEQUENTIAL path. Nothing had run concurrent branches on real per-branch sessions. This does,
through the real runner (`run_flow`), with branch nodes that touch Postgres on the session the
engine hands them:

* the branches overlap in time, each on its own connection (distinct backend pids), and each
  branch's own write is committed;
* the run's state carries every branch's result, and `partial` never appears without a failed
  branch behind it (the absence signal `SOAK_REGISTER.md` item 6 names);
* several runs fanning out at once never exceed the process-wide width (SYSMAX-5's budget), and all
  complete;
* the same flow with the flag off runs the branches one at a time and ends in the same state.
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_BRANCHES = ("fo_a", "fo_b", "fo_c", "fo_d")
_SLEEP_S = 0.4


@pytest.fixture
def engine(test_engine):
    if test_engine.dialect.name != "postgresql":
        pytest.skip("fan-out is soaked on Postgres")
    with test_engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS fan_out_soak (run text, branch text, pid int)"))
    yield test_engine
    with test_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS fan_out_soak"))


class _Probe:
    """Counts branches in flight; records each branch's window and backend pid."""

    def __init__(self):
        self.lock = threading.Lock()
        self.live = 0
        self.peak = 0
        self.windows: list[tuple[float, float]] = []

    def enter(self):
        with self.lock:
            self.live += 1
            self.peak = max(self.peak, self.live)
            return time.monotonic()

    def leave(self, started):
        with self.lock:
            self.live -= 1
            self.windows.append((started, time.monotonic()))


@pytest.fixture
def fan_out_flow(engine):
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.fan_out import FanOutEdgeGroup

    probe = _Probe()
    name = f"fan_out_soak_{uuid.uuid4().hex[:6]}"
    nodes = []

    def _branch(branch):
        def _node(state, context):  # noqa: ANN001
            started = probe.enter()
            try:
                db = context["db"]
                pid = db.execute(text("SELECT pg_backend_pid()")).scalar()
                idle = db.execute(text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND state = 'idle in transaction' AND pid <> pg_backend_pid()")).scalar()
                with probe.lock:
                    probe.idle_in_txn = max(getattr(probe, "idle_in_txn", 0), int(idle))
                db.execute(text("SELECT pg_sleep(:s)"), {"s": _SLEEP_S})
                db.execute(text("INSERT INTO fan_out_soak (run, branch, pid) VALUES (:r, :b, :p)"),
                           {"r": state["soak_run"], "b": branch, "p": pid})
                if branch == state.get("fail_branch"):
                    return {"status": "FAILURE", "error": f"{branch} broke"}
                return {"status": "SUCCESS", "output_patch": {f"out_{branch}": branch}}
            finally:
                probe.leave(started)
        return _node

    def _start(state, context):  # noqa: ANN001
        return {"status": "SUCCESS", "output_patch": {}}

    def _end(state, context):  # noqa: ANN001
        return {"status": "SUCCESS", "output_patch": {"joined": True}}

    for node_name, fn in [(f"{name}_start", _start), (f"{name}_end", _end)] + [
        (f"{name}_{b}", _branch(b)) for b in _BRANCHES
    ]:
        reg.register_node(node_name)(fn)
        nodes.append(node_name)

    def _register(join):
        flow_name = f"{name}_{join}"
        branch_nodes = [f"{name}_{b}" for b in _BRANCHES]
        reg.register_flow(flow_name, {
            "start": f"{name}_start", "end": [f"{name}_end"],
            "edges": {
                f"{name}_start": [FanOutEdgeGroup(branch_nodes, join=join)],
                **{b: [f"{name}_end"] for b in branch_nodes},
                f"{name}_end": [],
            },
        })
        return flow_name

    flows = {"all": _register("all"), "any": _register("any")}
    try:
        yield flows, probe
    finally:
        for f in flows.values():
            reg.FLOW_REGISTRY.pop(f, None)
        for n in nodes:
            reg.NODE_REGISTRY.pop(n, None)


def _envelope(flow_name, state):
    """`sys.v1.flow.run` through the real dispatcher (what `run_flow` calls; it raises on `partial`)."""
    from AINDY.db.database import SessionLocal
    from AINDY.kernel.syscall_dispatcher import SyscallContext, get_dispatcher

    db = SessionLocal()
    try:
        from tests.integration.test_agent_vm_parity import _committed_user

        ctx = SyscallContext(execution_unit_id="", user_id=str(_committed_user()), capabilities=["flow.run"],
                             trace_id="", metadata={"_db": db})
        return get_dispatcher().dispatch(
            "sys.v1.flow.run", {"flow_name": flow_name, "initial_state": dict(state)}, ctx)
    finally:
        db.close()


def _run(flow_name, state):
    envelope = _envelope(flow_name, state)
    assert envelope["status"] == "success", envelope
    return envelope["data"]["flow_result"]


def _rows(engine, run):
    with engine.connect() as conn:
        return conn.execute(text("SELECT branch, pid FROM fan_out_soak WHERE run = :r"), {"r": run}).all()


def _overlapping(windows) -> bool:
    latest_start = max(s for s, _ in windows)
    earliest_end = min(e for _, e in windows)
    return latest_start < earliest_end


@pytest.fixture
def width(monkeypatch):
    from AINDY.runtime.flow_engine.fan_out import max_fan_out_width, reset_branch_executor_for_tests

    reset_branch_executor_for_tests()
    yield max_fan_out_width()
    reset_branch_executor_for_tests()


def test_branches_run_concurrently_on_their_own_connections(engine, fan_out_flow, width, monkeypatch):
    monkeypatch.delenv("AINDY_FLOW_FAN_OUT", raising=False)  # the default
    flows, probe = fan_out_flow
    run = str(uuid.uuid4())
    t0 = time.monotonic()
    result = _run(flows["all"], {"soak_run": run})
    elapsed = time.monotonic() - t0
    rows = _rows(engine, run)
    print(f"\n[fan-out] all-join, {len(_BRANCHES)} branches x {_SLEEP_S}s: {elapsed:.2f}s, peak {probe.peak}, "
          f"pids {sorted({p for _, p in rows})}, idle-in-txn seen by a branch {getattr(probe, 'idle_in_txn', 0)}")

    assert result["status"] == "SUCCESS", result
    state = result.get("state") or {}
    assert {f"out_{b}" for b in _BRANCHES} <= set(state), f"a branch's result is missing from state: {state}"
    assert sorted(b for b, _ in rows) == sorted(_BRANCHES), "a branch's committed write is missing"
    assert len({p for _, p in rows}) == len(_BRANCHES), "branches shared a connection"
    assert probe.peak == min(width, len(_BRANCHES)) and _overlapping(probe.windows), (
        f"branches did not overlap (peak {probe.peak}): the default is not concurrent"
    )
    assert getattr(probe, "idle_in_txn", 0) == 0, (
        "a connection sat `idle in transaction` while the branches ran: the runner held its "
        "transaction across the superstep (RT-MEMTXN-LEAK-1)"
    )


def test_control_with_the_flag_off_branches_run_one_at_a_time_to_the_same_state(engine, fan_out_flow, width, monkeypatch):
    monkeypatch.setenv("AINDY_FLOW_FAN_OUT", "0")
    flows, probe = fan_out_flow
    run = str(uuid.uuid4())
    result = _run(flows["all"], {"soak_run": run})
    assert result["status"] == "SUCCESS", result
    assert probe.peak == 1, f"the flag is off but {probe.peak} branches ran at once"
    assert {f"out_{b}" for b in _BRANCHES} <= set(result.get("state") or {})
    assert sorted(b for b, _ in _rows(engine, run)) == sorted(_BRANCHES)


def test_partial_appears_only_with_a_failed_branch_behind_it(engine, fan_out_flow, width, monkeypatch):
    monkeypatch.delenv("AINDY_FLOW_FAN_OUT", raising=False)
    flows, _ = fan_out_flow
    clean = _envelope(flows["any"], {"soak_run": str(uuid.uuid4())})
    assert clean["status"] == "success", f"a lenient join with no failed branch read {clean['status']}"
    failed = _envelope(flows["any"], {"soak_run": str(uuid.uuid4()), "fail_branch": "fo_b"})
    print(f"\n[fan-out] any-join with fo_b failing: {failed['status']} {failed.get('outcome')}")
    assert failed["status"] == "partial", failed
    assert [u["branch"].rsplit("_", 2)[-2:] for u in failed["outcome"]["units"]] == [["fo", "b"]], failed["outcome"]
    assert failed["data"]["flow_result"]["status"] == "SUCCESS", "the run itself completed"


def test_concurrent_runs_never_exceed_the_process_wide_width(engine, fan_out_flow, width, monkeypatch):
    """SYSMAX-5: the width is a PROCESS bound shared by every run, sized against the pool."""
    monkeypatch.delenv("AINDY_FLOW_FAN_OUT", raising=False)
    flows, probe = fan_out_flow
    runs = [str(uuid.uuid4()) for _ in range(4)]
    results: dict = {}

    def _go(run):
        results[run] = _run(flows["all"], {"soak_run": run})

    threads = [threading.Thread(target=_go, args=(r,)) for r in runs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    print(f"\n[fan-out] {len(runs)} runs x {len(_BRANCHES)} branches at once: peak {probe.peak} (width {width})")
    assert all(results.get(r, {}).get("status") == "SUCCESS" for r in runs), results
    assert probe.peak <= width, f"{probe.peak} branches in flight across runs; the bound is {width}"
    assert probe.peak > 1, "control: the runs did fan out"
    for r in runs:
        assert sorted(b for b, _ in _rows(engine, r)) == sorted(_BRANCHES), r
