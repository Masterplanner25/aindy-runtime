"""The CROSS-INSTANCE resume callback resumes the RUN, durably.

`resume_spec._build_execution_unit_resume_callback` is what `_cross_instance_resume` runs for a
wait whose registering instance is gone (the spec comes back from the Redis wait registry). Until
2026-09-16 it was ``with SessionLocal() as db: resume_execution_unit(spec.eu_id)``:

* the ``with`` exited without committing — the unit's transition ROLLED BACK on every fire
  (the fourth own-session-never-commits callback in a week; `test_own_session_commits.py`);
* and it moved only the UNIT. In thread mode — the default — the scheduler runs this closure,
  so a flow parked on an instance that died was "claimed", logged as resumed, and stayed
  ``waiting`` forever. `test_multi_instance_resume.py` patched `resume_execution_unit` to a spy
  and asserted the spy was called (catalogue variant 13: the fixture blinds the test to the
  outcome).

Now the closure rebuilds the real resume from the spec (`resume_reconstruction`, the FR-15
worker's path) and falls back to a COMMITTED unit-only transition when the run cannot be rebuilt
in this process. Both halves are read through a connection that did not share the callback's
transaction (the FR-30 pattern).

Mutation-checked: restore the old one-liner → both tests fail (run still `waiting`; unit still
`waiting`); drop the fallback's ``commit()`` → the fallback test fails; make the fallback run
instead of the rebuild → the flow test fails on the run's status.
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

EVENT = "order.completed"
FLOW_NAME = "xinst_probe_flow"
NODE = "xinst_probe_node"


@pytest.fixture
def test_engine(tmp_path):
    from tests.fixtures.db import _import_model_registry

    from AINDY.db.database import Base

    _import_model_registry()
    engine = create_engine(
        f"sqlite:///{tmp_path / 'xinst.db'}",
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


def _park(factory, *, flow_name=FLOW_NAME, with_event=True):
    from AINDY.core.execution_unit_service import ExecutionUnitService
    from AINDY.db.models.flow_run import FlowRun

    db = factory()
    try:
        run = FlowRun(
            id=str(uuid.uuid4()), flow_name=flow_name, workflow_type="flow", status="waiting",
            current_node=NODE, state={"event": {"ok": True}} if with_event else {},
            waiting_for=EVENT, trace_id=str(uuid.uuid4()), user_id=None,
        )
        db.add(run)
        db.flush()
        eu = ExecutionUnitService(db).create(
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


def _fire(spec, factory):
    """What `_cross_instance_resume` does with the spec it pulled from Redis, then the scheduler
    dispatching the closure — on its own sessions, from a factory with no shared transaction."""
    from AINDY.kernel.resume_spec import build_callback_from_spec

    # The builder binds `SessionLocal` when it runs (as `_cross_instance_resume` does, on the
    # instance that claims), so it must be built under the patch too.
    with patch("AINDY.db.SessionLocal", factory), patch("AINDY.db.database.SessionLocal", factory):
        build_callback_from_spec(spec)()


def test_a_flow_wait_claimed_cross_instance_is_actually_resumed(db_session_factory, probe_flow):
    """★★ The run, not just the unit — and durably."""
    from AINDY.kernel.resume_spec import RESUME_HANDLER_EU, ResumeSpec

    run_id, eu_id = _park(db_session_factory)
    spec = ResumeSpec(handler=RESUME_HANDLER_EU, eu_id=eu_id, tenant_id="t", run_id=run_id, eu_type="flow")

    _fire(spec, db_session_factory)

    run_status, eu_status = _read(db_session_factory, run_id, eu_id)
    assert run_status == "success", (
        f"the run reads {run_status!r} — the cross-instance callback moved (or rolled back) the "
        "unit and never resumed the run, which is what the old one-liner did"
    )
    assert eu_status == "completed", eu_status


def test_a_run_that_cannot_be_rebuilt_here_still_gets_a_committed_unit_transition(
    db_session_factory, caplog
):
    """The fallback: the flow is not registered in this process (the worker-does-not-hold-the-flow
    case), so only the unit can be moved — and that write must survive the session, and say so."""
    from AINDY.kernel.resume_spec import RESUME_HANDLER_EU, ResumeSpec

    run_id, eu_id = _park(db_session_factory, flow_name="not_registered_anywhere")
    spec = ResumeSpec(handler=RESUME_HANDLER_EU, eu_id=eu_id, tenant_id="t", run_id=run_id, eu_type="flow")

    with caplog.at_level(logging.WARNING, logger="AINDY.kernel.resume_spec"):
        _fire(spec, db_session_factory)

    run_status, eu_status = _read(db_session_factory, run_id, eu_id)
    assert run_status == "waiting", "an unrebuildable run must not be claimed or touched"
    assert eu_status == "executing", (
        f"the unit reads {eu_status!r} through a fresh connection — the fallback's transition "
        "did not survive its session"
    )
    assert any("cannot be rebuilt" in r.getMessage() for r in caplog.records)


def test_the_unit_only_fallback_is_a_warning_not_a_silent_partial_resume(db_session_factory, caplog):
    """A spec with no run to rebuild from (pre-FR-15 registrations carried none) takes the
    fallback too — and the log names the run it did NOT resume."""
    from AINDY.kernel.resume_spec import RESUME_HANDLER_EU, ResumeSpec

    run_id, eu_id = _park(db_session_factory)
    spec = ResumeSpec(handler=RESUME_HANDLER_EU, eu_id=eu_id, tenant_id="t", run_id="", eu_type=None)

    with caplog.at_level(logging.WARNING, logger="AINDY.kernel.resume_spec"):
        _fire(spec, db_session_factory)

    assert _read(db_session_factory, run_id, eu_id) == ("waiting", "executing")
    assert any("NOT resumed here" in r.getMessage() for r in caplog.records)
