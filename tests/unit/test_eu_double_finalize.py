"""EU-DOUBLE-FINALIZE-1 — an agent run's unit is finalised ONCE, in the unit's vocabulary.

Filed from the app's 2.19.0 verification log (`[EU] invalid transition completed→completed` on
every completed `nodus_vm` run — they called it noise). Two defects, one rule:

* three sites finalised the unit (`execute_run`'s tail, the nodus_vm chain, the agent_flow
  adapter) and only one guarded on "already terminal";
* the chain passed the RUN status `verify_failed` straight through — not a unit status, refused
  every time — so a verify-failed run's unit stayed `executing` forever.

`ExecutionUnitService.finalize_for_run_status` is now the one rule all three call.
"""
from __future__ import annotations

import uuid

import pytest

# the nodus_vm harness's side-effect stubs, reused as-is
from tests.unit.test_agent_vm_execution import _mock_side_effects  # noqa: F401

pytestmark = pytest.mark.runtime_only


@pytest.fixture
def session():
    """The vm harness's private engine, plus `execution_units` — the table this file is about."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import tests.fixtures.db  # noqa: F401  — SQLite compilers for the PG types
    import AINDY.db.model_registry  # noqa: F401
    from AINDY.db.database import Base

    engine = create_engine("sqlite://")
    for name in ("agent_runs", "agent_steps", "execution_units"):
        Base.metadata.tables[name].create(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture
def db_session(session):
    return session


def _unit(db, run_id):
    from AINDY.core.execution_unit_service import ExecutionUnitService

    eus = ExecutionUnitService(db)
    eu = eus.create(eu_type="agent", source_type="agent_run", source_id=str(run_id), user_id=None)
    assert eu is not None
    assert eus.update_status(eu.id, "executing")
    db.flush()
    return eus, eu


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("run_status, unit_status", [
    ("completed", "completed"), ("failed", "failed"), ("verify_failed", "failed"), ("cancelled", "failed"),
])
def test_a_runs_terminal_status_maps_to_the_units_vocabulary(db_session, run_status, unit_status):
    from AINDY.db.models.execution_unit import ExecutionUnit

    eus, eu = _unit(db_session, uuid.uuid4())
    assert eus.finalize_for_run_status(eu.id, run_status) is True
    assert db_session.get(ExecutionUnit, eu.id).status == unit_status


def test_a_second_finalize_is_a_silent_no_op_not_an_invalid_transition(db_session, monkeypatch):
    """The filed symptom: the second writer logged a WARNING per run. It is late, not wrong."""
    from AINDY.core import execution_unit_service as svc

    eus, eu = _unit(db_session, uuid.uuid4())
    transitions: list = []
    real = svc.ExecutionUnitService.update_status
    monkeypatch.setattr(svc.ExecutionUnitService, "update_status",
                        lambda self, eu_id, s: transitions.append(s) or real(self, eu_id, s), raising=True)
    assert eus.finalize_for_run_status(eu.id, "completed") is True
    assert eus.finalize_for_run_status(eu.id, "completed") is False
    assert eus.finalize_for_run_status(eu.id, "failed") is False   # a terminal unit is never re-decided
    assert transitions == ["completed"], transitions


def test_a_non_terminal_run_status_does_nothing(db_session):
    from AINDY.db.models.execution_unit import ExecutionUnit

    eus, eu = _unit(db_session, uuid.uuid4())
    assert eus.finalize_for_run_status(eu.id, "waiting") is False
    assert eus.finalize_for_run_status(eu.id, "") is False
    assert db_session.get(ExecutionUnit, eu.id).status == "executing"


# ---------------------------------------------------------------------------
# Through the real chain on the nodus_vm backend — the backend the app runs
# ---------------------------------------------------------------------------

def _vm_harness():
    import tests.unit.test_agent_vm_execution as vm

    return vm


def test_a_verify_failed_vm_run_finalises_its_unit_as_failed(session, monkeypatch, _mock_side_effects):
    """Before: `_sync_agent_eu_status(db, run_id, "verify_failed")` was refused every time and the
    unit stayed `executing` — this is `EU-FINALIZE-UNCOMMITTED-1`'s shape on a different run."""
    vm = _vm_harness()
    from AINDY.db.models.execution_unit import ExecutionUnit
    from AINDY.runtime import nodus_execution_service as svc

    run = vm._make_run(session)
    eus, eu = _unit(session, run.id)
    plan = {"steps": [{"tool": "search", "args": {}, "risk_level": "low",
                       "expects": {"field": "i", "op": "eq", "value": 99}}]}  # 0 != 99 → verify fails
    run.plan = plan
    session.commit()
    vm._events_capture(monkeypatch)
    monkeypatch.setattr(svc, "run_nodus_script_via_flow", vm._segment_aware_flow)
    import AINDY.core.effect_compensation as ec
    monkeypatch.setattr(ec, "undo_run_effects", lambda run_id, **kw: {"reversed": [], "irreversible": [], "failed": []})

    svc.execute_agent_run_via_workflow(run_id=str(run.id), plan=plan, user_id=str(run.user_id), db=session,
                                       execution_token={"token_hash": "h", "granted_tools": ["search"]})
    session.refresh(run)
    assert run.status == "verify_failed"
    session.expire_all()
    assert session.get(ExecutionUnit, eu.id).status == "failed", "a verify-failed run left its unit executing"


def test_a_completed_vm_run_finalises_its_unit_once(session, monkeypatch, _mock_side_effects):
    vm = _vm_harness()
    from AINDY.core import execution_unit_service as eusvc
    from AINDY.db.models.execution_unit import ExecutionUnit
    from AINDY.runtime import nodus_execution_service as svc

    run = vm._make_run(session)
    eus, eu = _unit(session, run.id)
    plan = {"steps": [{"tool": "search", "args": {}}, {"tool": "summarize", "args": {}}]}
    output_state = {"__step_0_result": {"success": True, "result": None, "error": None},
                    "__step_1_result": {"success": True, "result": None, "error": None}}
    monkeypatch.setattr(svc, "run_nodus_script_via_flow", lambda **kw: vm._flow_result(output_state))
    transitions: list = []
    real = eusvc.ExecutionUnitService.update_status
    monkeypatch.setattr(eusvc.ExecutionUnitService, "update_status",
                        lambda self, eu_id, s: transitions.append(s) or real(self, eu_id, s), raising=True)

    svc.execute_agent_run_via_workflow(run_id=str(run.id), plan=plan, user_id=str(run.user_id), db=session,
                                       execution_token={"token_hash": "h"})
    # the chain's sync ran; now the tail `execute_run` would run — call the rule again as it does
    eus.finalize_for_run_status(eu.id, "completed")
    session.expire_all()
    assert session.get(ExecutionUnit, eu.id).status == "completed"
    assert transitions.count("completed") == 1, transitions
