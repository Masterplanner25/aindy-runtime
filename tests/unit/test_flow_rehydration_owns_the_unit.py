"""`EU-WAIT-SIGNAL-DEAD-1` follow-up — `rehydrate_waiting_eus` is gone; the flow path owns the unit.

Until 2026-09-16 a restart registered TWO scheduler entries per parked flow run: the run's (keyed
by run id, `flow_run_rehydration`) and the unit's (keyed by execution-unit id,
`wait_rehydration.rehydrate_waiting_eus`). The second one's callback moved the unit
``waiting → resumed → executing`` on a session it closed without committing — a rollback on every
fire, since it was written. `flow_run_rehydration`'s own docstring called the pair
"complementary … removing either would leave a broken half-state". Half of that was true: the
run-keyed entry is load-bearing. The unit-keyed one never wrote anything durable.

What is pinned:

* **exactly one scheduler entry per parked run, keyed by the run id** — nothing keyed by the
  unit id, on a real `SchedulerEngine`;
* **the flow callback's unit transition SURVIVES its session** — read through a separate
  connection after the callback returns (the FR-30 pattern: file SQLite, `NullPool`, sessions
  with no shared outer transaction). This is the fact that made the removal safe, and the fact
  the old docstring's "bookkeeping" callback could never have provided;
* the module that held the removed function keeps only the seed, and the startup phase has no
  EU-level rehydration step.

Mutation-checked: re-register a unit-keyed entry in `rehydrate_waiting_flow_runs` → the one-entry
test fails; put the unit's `completed` transition back inside the early-returning
memory-capture hook → the durability test fails (the run has no `user_id`, which is exactly how
that hook skipped it — a real latent gap this test found, fixed by `finalize_flow_unit`).
**A mutation that SURVIVED, and why it is fine:** dropping the flow callback's step 2
(`resume_execution_unit`) changes nothing here, because `PersistentFlowRunner.resume()` recovers
the run's unit itself and performs the same transition on the same session. Two writers, one
session, one commit — belt and braces, not a race. The property pinned is the durable outcome,
not which of the two lines produced it.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.runtime_only

EVENT = "review.approved"
FLOW_NAME = "rehydrate_probe_flow"
NODE = "rehydrate_probe_node"


@pytest.fixture
def test_engine(tmp_path):
    from tests.fixtures.db import _import_model_registry

    from AINDY.db.database import Base

    _import_model_registry()
    engine = create_engine(
        f"sqlite:///{tmp_path / 'rehydrate.db'}",
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


@pytest.fixture
def probe_flow():
    """A one-node flow whose node completes on the resume (it sees the injected event)."""
    from AINDY.runtime.flow_engine import registry as reg

    @reg.register_node(NODE)
    def _n(state, context):  # noqa: ANN001
        if "event" not in state:
            return {"status": "WAIT", "wait_for": EVENT, "output_patch": {}}
        return {"status": "SUCCESS", "output_patch": {"got": state["event"]}}

    flow = {"start": NODE, "end": [NODE], "edges": {}}
    reg.register_flow(FLOW_NAME, flow)
    try:
        yield flow
    finally:
        reg.FLOW_REGISTRY.pop(FLOW_NAME, None)
        reg.NODE_REGISTRY.pop(NODE, None)


def _park(factory):
    """A parked run and its parked unit, committed — what a restart finds in the tables."""
    from AINDY.core.execution_unit_service import ExecutionUnitService
    from AINDY.db.models.flow_run import FlowRun

    db = factory()
    try:
        run = FlowRun(
            id=str(uuid.uuid4()), flow_name=FLOW_NAME, workflow_type="flow", status="waiting",
            current_node=NODE, state={"event": {"approved": True}}, waiting_for=EVENT,
            trace_id=str(uuid.uuid4()), user_id=None,
        )
        db.add(run)
        db.flush()
        eus = ExecutionUnitService(db)
        eu = eus.create(
            eu_type="flow", user_id=str(uuid.uuid4()), source_type="flow_run", source_id=run.id,
            flow_run_id=run.id, status="waiting",
        )
        db.commit()
        return str(run.id), str(eu.id)
    finally:
        db.close()


def _read(factory, run_id, eu_id):
    from AINDY.db.models.execution_unit import ExecutionUnit
    from AINDY.db.models.flow_run import FlowRun

    db = factory()
    try:
        run = db.query(FlowRun).filter(FlowRun.id == run_id).one()
        eu = db.query(ExecutionUnit).filter(ExecutionUnit.id == uuid.UUID(eu_id)).one()
        return run.status, eu.status
    finally:
        db.close()


def test_a_restart_registers_exactly_one_entry_per_parked_run_keyed_by_the_run(db_session_factory, probe_flow):
    from AINDY.core.flow_run_rehydration import rehydrate_waiting_flow_runs
    from AINDY.kernel.scheduler.engine import SchedulerEngine

    run_id, eu_id = _park(db_session_factory)
    eng = SchedulerEngine()
    eng.mark_rehydration_complete()

    db = db_session_factory()
    try:
        with patch("AINDY.core.flow_run_rehydration.get_scheduler_engine", return_value=eng):
            n = rehydrate_waiting_flow_runs(db)
    finally:
        db.close()

    assert n == 1
    assert eng.waiting_for(run_id) == EVENT, "the run's entry is the load-bearing one"
    assert eng.waiting_for(eu_id) is None, (
        "a unit-keyed scheduler entry was registered — that is the removed rehydrate_waiting_eus "
        "shape, whose callback rolled back on every fire"
    )
    with eng._lock:
        keys = set(eng._waiting)
    assert keys == {run_id}, keys


def test_the_flow_callbacks_unit_transition_survives_its_session(db_session_factory, probe_flow):
    """★★ The fact that made the removal safe. The flow callback claims the run, moves the unit
    ``waiting → resumed → executing``, drives the flow to completion — and every one of those
    writes is visible from a connection that did not share its transaction."""
    from AINDY.core.flow_run_rehydration import rehydrate_waiting_flow_runs
    from AINDY.kernel.scheduler.engine import SchedulerEngine

    run_id, eu_id = _park(db_session_factory)
    assert _read(db_session_factory, run_id, eu_id) == ("waiting", "waiting")

    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    db = db_session_factory()
    try:
        with patch("AINDY.core.flow_run_rehydration.get_scheduler_engine", return_value=eng):
            rehydrate_waiting_flow_runs(db)
    finally:
        db.close()

    with eng._lock:
        callback = eng._waiting[run_id]["callback"]

    # Fire it the way the scheduler would: on its own session, from a factory that hands out
    # one connection per session and no outer transaction.
    with patch("AINDY.db.database.SessionLocal", db_session_factory), patch(
        "AINDY.kernel.scheduler_engine.get_scheduler_engine", return_value=eng
    ):
        callback()

    run_status, eu_status = _read(db_session_factory, run_id, eu_id)
    assert run_status == "success", run_status
    assert eu_status == "completed", (
        f"the unit reads {eu_status!r} through a fresh connection — the flow callback's "
        "transition did not survive its session"
    )


def test_the_module_keeps_only_the_seed_and_startup_has_no_eu_rehydration_step():
    import ast
    import inspect

    import AINDY.core.wait_rehydration as wr
    import AINDY.startup as startup

    assert not hasattr(wr, "rehydrate_waiting_eus")
    public = [n for n, v in vars(wr).items() if callable(v) and not n.startswith("_") and getattr(v, "__module__", "") == wr.__name__]
    assert public == ["ensure_waiting_flow_run_row"], public

    src = inspect.getsource(startup._rehydrate_waiting_state)
    calls = {
        getattr(n.func, "attr", None) or getattr(n.func, "id", None)
        for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
    }
    assert "rehydrate_waiting_eus" not in calls
    assert "rehydrate_waiting_flow_runs" in calls
