"""FR-31 — a run parked across a restart must be resumable on a fresh process, first try.

Filed by the app team from the 2.17.0 upgrade's own verification step: a Nodus run parked on
2.16.0, resumed after the rebuild, answered `resumed: true, payload_injected: true` and stayed
`waiting` forever, with one WARNING — `flow='nodus_execute' not in FLOW_REGISTRY … skipping
resume`. `nodus_execute` was registered LAZILY (first script run of a process); the rehydrated
callback looked it up at wake time, found nothing, returned — after the wake had consumed the
run's registration. A second resume did nothing. Restart → run any script → resume → `success`.

Reading it found the sibling: `agent_execution` (the AGENT_FLOW backend's label) was NEVER in
FLOW_REGISTRY — the orchestration hands `AGENT_FLOW` to the runner directly — so an agent run
parked by the authority WAIT gate (#681) could not have survived a restart either.

Three asks, each pinned here through the REAL rehydration + REAL callback on a process that has
NOT run a script (the registry is emptied of the runtime flows first):

1. the runtime's dynamic flows resolve at boot / on miss — `nodus_execute` registered (in the
   API's `register_all_flows()` and the worker's boot), `agent_execution` resolvable for RESUME
   ONLY (never publicly registered: that would make it startable via `sys.v1.flow.run` with no
   `execution_token`, bypassing approval — pinned);
2. a flow the process genuinely does not hold does NOT consume the registration: the wait is
   RE-ARMED under the run id and a later wake (after the flow appears) resumes the run;
3. the resume route reports `woken` — `payload_injected: true` with nothing registered reads
   `woken: false`, `resumed: false`, and warns.

Fixtures: a PRIVATE file-backed SQLite engine (the FR-30 pattern) with short-lived sessions.
The callback and `_reregister_wait` open and close their own sessions; under the shared
`db_session` fixture a `close()` rolls back the test's own rows, and a long-lived test session
holds a SQLite read lock the callback's connection trips over (`database is locked`).

Mutation-checked: drop `ensure_runtime_flows_registered()` from `register_all_flows()` → the
boot test fails; drop the on-miss resolve in the callback → the fresh-process test fails on
`waiting`; drop `_reregister_wait` → the re-arm test finds no entry; drop the `woken` field →
the route test fails; register `agent_execution` publicly → the exposure test fails.
"""
from __future__ import annotations

import logging
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.runtime_only

EVENT = "review.approved"
SCRIPT = """
let received = get_state("nodus_received_events")
if (received == nil) {
    set_state("nodus_wait_requested", true)
    set_state("nodus_wait_event_type", "review.approved")
} else {
    set_state("reviewer", received["review.approved"]["reviewer"])
}
"""


# ── private engine ───────────────────────────────────────────────────────────


@pytest.fixture
def test_engine(tmp_path):
    from tests.fixtures.db import _import_model_registry

    from AINDY.db.database import Base

    _import_model_registry()
    engine = create_engine(
        f"sqlite:///{tmp_path / 'fr31.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        try:
            cur.execute("PRAGMA foreign_keys=OFF")
        finally:
            cur.close()

    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session_factory(test_engine):
    return sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=test_engine)


@pytest.fixture
def testing_session_factory(db_session_factory):
    return db_session_factory


def _with(factory, fn):
    """Run ``fn(db)`` on a short-lived session, commit, and CLOSE it."""
    db = factory()
    try:
        out = fn(db)
        db.commit()
        return out
    finally:
        db.close()


def _rehydrate(factory) -> int:
    from AINDY.core.flow_run_rehydration import rehydrate_waiting_flow_runs

    return _with(factory, rehydrate_waiting_flow_runs)


def _status(factory, run_id):
    from AINDY.db.models.flow_run import FlowRun

    return _with(factory, lambda db: db.query(FlowRun).filter(FlowRun.id == run_id).one().status)


def _state(factory, run_id):
    from AINDY.db.models.flow_run import FlowRun

    return _with(factory, lambda db: dict(db.query(FlowRun).filter(FlowRun.id == run_id).one().state or {}))


# ── process + scheduler fixtures ─────────────────────────────────────────────


@pytest.fixture
def fresh_process():
    """A process that has NOT run a script: the runtime's dynamic flows are absent from the
    registry, exactly as on a fresh boot before this fix. Restored afterwards."""
    from AINDY.runtime.flow_engine import registry as reg

    saved = {k: reg.FLOW_REGISTRY.pop(k) for k in ("nodus_execute", "agent_execution") if k in reg.FLOW_REGISTRY}
    yield reg
    reg.FLOW_REGISTRY.pop("nodus_execute", None)
    reg.FLOW_REGISTRY.pop("agent_execution", None)
    reg.FLOW_REGISTRY.update(saved)


@pytest.fixture
def in_process_worker(monkeypatch):
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.runtime import nodus_worker
    from AINDY.runtime import nodus_worker_pool as pool_mod

    class _InProcessPool:
        def execute(self, payload, *, timeout_s):
            return nodus_worker.run_one(payload)

    monkeypatch.setenv("AINDY_NODUS_WARM_POOL", "1")
    monkeypatch.setattr(pool_mod, "get_pool", lambda: _InProcessPool())


@pytest.fixture
def engine(testing_session_factory):
    """A real SchedulerEngine; every `SessionLocal` the runtime opens comes from the private
    engine for the whole test, so the callback, the re-arm and the backup writer all see one DB."""
    from AINDY.kernel.scheduler.engine import SchedulerEngine

    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    with patch("AINDY.kernel.scheduler_engine.get_scheduler_engine", return_value=eng), patch(
        "AINDY.core.flow_run_rehydration.get_scheduler_engine", return_value=eng
    ), patch("AINDY.db.database.SessionLocal", testing_session_factory), patch(
        "AINDY.db.SessionLocal", testing_session_factory
    ):
        yield eng


def _parked_nodus_run(factory, *, flow_name="nodus_execute", user_id=None, with_event=True, current_node="nodus.execute"):
    """What the tables hold after a restart: a `waiting` Nodus run with its WAIT patch merged
    (post-#654) and, when ``with_event``, the payload already injected by the route (post-#678).
    Returns ``(run_id, trace_id)``."""
    from AINDY.db.models.flow_run import FlowRun

    run_id, trace = str(uuid.uuid4()), str(uuid.uuid4())
    state = {"nodus_script": SCRIPT, "nodus_wait_event_type": EVENT, "nodus_status": "waiting"}
    if with_event:
        state["event"] = {"reviewer": "shawn", "approved": True}

    def _add(db):
        db.add(FlowRun(
            id=run_id, flow_name=flow_name, workflow_type="nodus", status="waiting",
            current_node=current_node, waiting_for=EVENT, trace_id=trace, user_id=user_id, state=state,
        ))

    _with(factory, _add)
    return run_id, trace


def _fire(engine, run_id):
    """What a wake does: the scheduler DELETES the entry, then dispatches the callback. The
    delete is what makes a skipped callback terminal (FR-31 ask 2) — a helper that left the
    entry in place could not tell a re-armed wait from one that was never consumed (it did,
    in this file's first draft, and the re-arm mutation survived)."""
    with engine._lock:
        cb = engine._waiting.pop(run_id)["callback"]
    cb()


# ── ask 1: the fresh-process resume ──────────────────────────────────────────


def test_a_nodus_run_parked_across_a_restart_resumes_on_the_first_wake(
    testing_session_factory, fresh_process, in_process_worker, engine
):
    """★★ FR-31 as filed. No script has run in this process; rehydrate; wake; the run must
    complete — not log `not in FLOW_REGISTRY` and stay `waiting`."""
    assert "nodus_execute" not in fresh_process.FLOW_REGISTRY
    run_id, _ = _parked_nodus_run(testing_session_factory)
    assert _rehydrate(testing_session_factory) == 1
    assert engine.waiting_for(run_id) == EVENT

    _fire(engine, run_id)

    status = _status(testing_session_factory, run_id)
    assert status == "success", (
        f"the run reads {status!r} after the wake — the rehydrated callback did not find "
        "`nodus_execute` on a fresh process (FR-31)"
    )
    assert (_state(testing_session_factory, run_id).get("nodus_output_state") or {}).get("reviewer") == "shawn"


def test_register_all_flows_registers_nodus_execute_at_boot(fresh_process):
    from AINDY.runtime.flow_definitions import register_all_flows

    register_all_flows()
    assert "nodus_execute" in fresh_process.FLOW_REGISTRY, "the runtime's script flow must not wait for a first script"


def test_the_worker_boot_registers_the_runtime_flows_too():
    """The FR-15 worker rebuilds resumes; it ran `register_flows()` (plugins) but never
    `register_all_flows()` (runtime-owned) — so it could not have rebuilt a `nodus_execute`
    resume either. Source-derived: the call must be in the worker's boot."""
    import ast
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[2] / "AINDY" / "worker" / "__main__.py").read_text(encoding="utf-8-sig")
    calls = {getattr(n.func, "attr", None) or getattr(n.func, "id", None) for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)}
    assert "register_all_flows" in calls


def test_agent_execution_is_resolvable_for_resume_but_never_publicly_registered(fresh_process):
    """★ The exposure that must not happen: `agent_execute_step` checks tool capability only
    when the state carries an `execution_token`, so a publicly registered `agent_execution`
    would be startable via `sys.v1.flow.run` with no token — approval bypassed."""
    from AINDY.runtime.flow_definitions import register_all_flows
    from AINDY.runtime.nodus_adapter import AGENT_FLOW
    from AINDY.runtime.nodus_execution_service import resolve_resumable_flow

    register_all_flows()
    assert "agent_execution" not in fresh_process.FLOW_REGISTRY, (
        "agent_execution entered FLOW_REGISTRY — anyone with `flow.run` can now start the agent "
        "flow with no execution_token"
    )
    assert resolve_resumable_flow("agent_execution") is AGENT_FLOW
    assert resolve_resumable_flow("nodus_execute") is not None
    assert resolve_resumable_flow("not_a_flow") is None


def test_a_gate_parked_agent_run_survives_a_restart(testing_session_factory, fresh_process, engine):
    """The sibling FR-31 exposed: an AGENT_FLOW run parked by the authority WAIT gate (#681),
    rehydrated on a fresh process, must resume through `agent_execution` — a name that was
    never in FLOW_REGISTRY. Driven to the point where the flow is resolved and the run is
    claimed; the step itself is stubbed (its tool/capability seams are #681's tests)."""
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine.registry import NODE_REGISTRY
    import AINDY.runtime.nodus_adapter  # noqa: F401 — registers the agent nodes

    run_id = str(uuid.uuid4())

    def _add(db):
        db.add(FlowRun(
            id=run_id, flow_name="agent_execution", workflow_type="agent_execution",
            status="waiting", current_node="agent_execute_step", waiting_for="agent.authority.decision",
            trace_id=str(uuid.uuid4()), user_id=None,
            state={"agent_run_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()), "steps": [],
                   "current_step_index": 0, "step_results": [], "event": {"decision": "skip"}},
        ))

    _with(testing_session_factory, _add)
    seen: list[str] = []

    def _step(state, context):  # noqa: ANN001
        seen.append("ran")
        return {"status": "SUCCESS", "output_patch": {"current_step_index": 1}}

    assert _rehydrate(testing_session_factory) == 1
    with patch.dict(
        NODE_REGISTRY,
        {"agent_execute_step": _step, "agent_finalize_run": lambda s, c: {"status": "SUCCESS", "output_patch": {}}},
    ):
        _fire(engine, run_id)

    assert seen == ["ran"], "the agent flow was not resumed — `agent_execution` did not resolve"
    assert _status(testing_session_factory, run_id) != "waiting"


# ── ask 2: a genuine miss re-arms the wait ───────────────────────────────────


def test_a_flow_this_process_does_not_hold_re_arms_the_wait_instead_of_orphaning_the_run(
    testing_session_factory, fresh_process, engine, caplog
):
    run_id, _ = _parked_nodus_run(testing_session_factory, flow_name="a_plugin_flow_not_loaded_here", current_node="np_stub")
    assert _rehydrate(testing_session_factory) == 1

    with caplog.at_level(logging.WARNING, logger="AINDY.core.flow_run_rehydration"):
        _fire(engine, run_id)

    assert any("RE-REGISTERED" in r.getMessage() for r in caplog.records)
    assert engine.waiting_for(run_id) == EVENT, "the wait was consumed and not re-armed — the run is orphaned until the next boot"
    assert _status(testing_session_factory, run_id) == "waiting", "an unresumable run must not be claimed"

    # The flow appears (a plugin loads); the NEXT wake resumes it.
    fresh_process.register_flow("a_plugin_flow_not_loaded_here", {"start": "np_stub", "end": ["np_stub"], "edges": {}})
    fresh_process.register_node("np_stub")(lambda s, c: {"status": "SUCCESS", "output_patch": {}})
    try:
        _fire(engine, run_id)
        assert _status(testing_session_factory, run_id) == "success"
    finally:
        fresh_process.FLOW_REGISTRY.pop("a_plugin_flow_not_loaded_here", None)
        fresh_process.NODE_REGISTRY.pop("np_stub", None)


# ── ask 3: the route says whether anything was woken ─────────────────────────


def _resume_via_node(factory, run_id, uid):
    from AINDY.runtime.flow_definitions_engine import flow_run_resume_node

    return _with(factory, lambda db: flow_run_resume_node(
        {"run_id": run_id, "event_type": EVENT, "payload": {"reviewer": "shawn", "approved": True}},
        {"db": db, "user_id": str(uid)},
    ))


def test_the_route_reports_woken_false_when_nothing_is_registered(testing_session_factory, engine):
    """FR-31's wire signature: `payload_injected: true` and nothing to wake."""
    uid = uuid.uuid4()
    run_id, _ = _parked_nodus_run(testing_session_factory, user_id=uid, with_event=False)
    # No scheduler entry for this run — the state FR-31's second attempt found.
    result = _resume_via_node(testing_session_factory, run_id, uid)
    out = result["output_patch"]["flow_run_resume_result"]
    assert out["results"] == [{"run_id": run_id, "payload_injected": True, "woken": False}]
    assert out["resumed"] is False, "`resumed` must mean WOKEN, not 'payload stored'"


def test_the_route_reports_woken_true_when_a_wait_is_registered(testing_session_factory, engine):
    uid = uuid.uuid4()
    run_id, trace = _parked_nodus_run(testing_session_factory, user_id=uid, with_event=False)
    engine.register_wait(
        run_id=run_id, wait_for_event=EVENT, tenant_id="t", eu_id="", resume_callback=lambda: None,
        correlation_id=trace, trace_id=trace,
    )

    result = _resume_via_node(testing_session_factory, run_id, uid)
    out = result["output_patch"]["flow_run_resume_result"]
    assert out["results"] == [{"run_id": run_id, "payload_injected": True, "woken": True}]
    assert out["resumed"] is True
