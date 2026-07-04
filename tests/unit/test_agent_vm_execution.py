"""Unit tests for the RTR-1 Phase 2c opt-in VM-backed agent execution path."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from AINDY.db.database import Base
import AINDY.db.model_registry  # noqa: F401  (populate metadata)
from AINDY.runtime import nodus_execution_service as svc


# --------------------------------------------------------------------------- #
# reconstruct_agent_step_results — pure mapping
# --------------------------------------------------------------------------- #

_STEPS = [
    {"index": 0, "tool": "search", "args": {}, "risk_level": "low", "description": "d0", "result_key": "__step_0_result"},
    {"index": 1, "tool": "summarize", "args": {}, "risk_level": "high", "description": "d1", "result_key": "__step_1_result"},
    {"index": 2, "tool": "send", "args": {}, "risk_level": "medium", "description": "d2", "result_key": "__step_2_result"},
]


def test_reconstruct_all_success():
    out = {
        "__step_0_result": {"success": True, "result": {"a": 1}, "error": None},
        "__step_1_result": {"success": True, "result": {"b": 2}, "error": None},
        "__step_2_result": {"success": True, "result": None, "error": None},
    }
    results, any_failed = svc.reconstruct_agent_step_results(_STEPS, out)
    assert any_failed is False
    assert [r["status"] for r in results] == ["success", "success", "success"]
    assert results[0]["result"] == {"a": 1}


def test_reconstruct_with_failure_and_absent_step():
    out = {
        "__step_0_result": {"success": True, "result": None, "error": None},
        "__step_1_result": {"success": False, "result": None, "error": "boom"},
        # step 2 absent — did not run
    }
    results, any_failed = svc.reconstruct_agent_step_results(_STEPS, out)
    assert any_failed is True
    assert len(results) == 2  # step 2 skipped
    assert results[1]["status"] == "failed"
    assert results[1]["error"] == "boom"


def test_reconstruct_non_dict_result_is_failure():
    results, any_failed = svc.reconstruct_agent_step_results(
        _STEPS[:1], {"__step_0_result": "weird"}
    )
    assert any_failed is True
    assert results[0]["status"] == "failed"


# --------------------------------------------------------------------------- #
# Backend selector
# --------------------------------------------------------------------------- #

def test_backend_selector_routes_to_vm_when_flag_set(monkeypatch):
    monkeypatch.setenv("AINDY_AGENT_EXECUTION_BACKEND", "nodus_vm")
    called = {}
    monkeypatch.setattr(
        svc, "execute_agent_run_via_workflow",
        lambda **kw: called.update(kw) or {"status": "SUCCESS", "routed": "vm"},
    )
    out = svc.execute_agent_run_via_nodus(
        run_id="r1", plan={"steps": []}, user_id="u1", db=object(),
    )
    assert out == {"status": "SUCCESS", "routed": "vm"}
    assert called["run_id"] == "r1"


def test_backend_selector_defaults_to_agent_flow(monkeypatch):
    monkeypatch.delenv("AINDY_AGENT_EXECUTION_BACKEND", raising=False)
    monkeypatch.setattr(
        svc, "execute_agent_flow_orchestration",
        lambda **kw: {"status": "SUCCESS", "routed": "agent_flow"},
    )
    monkeypatch.setattr(
        svc, "execute_agent_run_via_workflow",
        lambda **kw: pytest.fail("VM path must not run when flag unset"),
    )
    out = svc.execute_agent_run_via_nodus(
        run_id="r1", plan={"steps": []}, user_id="u1", db=object(),
    )
    assert out["routed"] == "agent_flow"


# --------------------------------------------------------------------------- #
# execute_agent_run_via_workflow — end to end (flow run + capability mocked)
# --------------------------------------------------------------------------- #

@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.tables["agent_runs"].create(bind=engine)
    Base.metadata.tables["agent_steps"].create(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _make_run(db):
    from AINDY.db.models import AgentRun

    run = AgentRun(
        id=uuid.uuid4(), user_id=uuid.uuid4(), goal="test goal",
        status="executing", steps_total=2, plan={"steps": []},
    )
    db.add(run)
    db.commit()
    return run


@pytest.fixture
def _mock_side_effects(monkeypatch):
    """Isolate to AgentStep/status logic — mock capability + event emission."""
    monkeypatch.setattr(
        "AINDY.agents.capability_service.check_execution_capability",
        lambda **kw: {"ok": True},
    )
    monkeypatch.setattr("AINDY.core.execution_signal_helper.queue_system_event", lambda **kw: None)
    monkeypatch.setattr("AINDY.core.execution_signal_helper.record_agent_event", lambda **kw: None)


def _flow_result(output_state, status="SUCCESS"):
    return {
        "status": status,
        "run_id": "flow-run-1",
        "trace_id": "trace-1",
        "state": {"nodus_output_state": output_state, "nodus_status": "success"},
        "data": {"status": "success", "output_state": output_state},
    }


def test_vm_run_all_success(session, monkeypatch, _mock_side_effects):
    run = _make_run(session)
    plan = {"steps": [
        {"tool": "search", "args": {"q": "x"}, "risk_level": "low", "description": "s0"},
        {"tool": "summarize", "args": {"t": "y"}, "risk_level": "high", "description": "s1"},
    ]}
    output_state = {
        "__step_0_result": {"success": True, "result": {"hits": 3}, "error": None},
        "__step_1_result": {"success": True, "result": {"summary": "ok"}, "error": None},
    }
    monkeypatch.setattr(svc, "run_nodus_script_via_flow", lambda **kw: _flow_result(output_state))

    result = svc.execute_agent_run_via_workflow(
        run_id=str(run.id), plan=plan, user_id=str(run.user_id), db=session,
        execution_token={"token_hash": "h", "granted_tools": ["search", "summarize"]},
    )
    assert result["status"] == "SUCCESS"

    from AINDY.db.models import AgentStep
    session.refresh(run)
    assert run.status == "completed"
    assert run.steps_completed == 2
    assert run.flow_run_id == "flow-run-1"
    assert run.result == {"steps": [
        {"step_index": 0, "tool": "search", "status": "success", "result": {"hits": 3}, "error": None},
        {"step_index": 1, "tool": "summarize", "status": "success", "result": {"summary": "ok"}, "error": None},
    ]}
    rows = session.query(AgentStep).order_by(AgentStep.step_index).all()
    assert [r.tool_name for r in rows] == ["search", "summarize"]
    assert [r.status for r in rows] == ["success", "success"]
    assert rows[0].tool_args == {"q": "x"}
    assert rows[1].risk_level == "high"


def test_vm_run_with_failed_step_marks_run_failed(session, monkeypatch, _mock_side_effects):
    run = _make_run(session)
    plan = {"steps": [
        {"tool": "search", "args": {}},
        {"tool": "summarize", "args": {}},
    ]}
    output_state = {
        "__step_0_result": {"success": True, "result": None, "error": None},
        "__step_1_result": {"success": False, "result": None, "error": "tool failed"},
    }
    monkeypatch.setattr(svc, "run_nodus_script_via_flow", lambda **kw: _flow_result(output_state))

    svc.execute_agent_run_via_workflow(
        run_id=str(run.id), plan=plan, user_id=str(run.user_id), db=session,
        execution_token={"token_hash": "h"},
    )
    session.refresh(run)
    assert run.status == "failed"
    assert run.error_message

    from AINDY.db.models import AgentStep
    rows = session.query(AgentStep).order_by(AgentStep.step_index).all()
    assert [r.status for r in rows] == ["success", "failed"]


def test_vm_run_capability_denied_without_token(session, monkeypatch):
    # No capability mock — real gate; no token → denied.
    monkeypatch.setattr("AINDY.core.execution_signal_helper.queue_system_event", lambda **kw: None)
    monkeypatch.setattr("AINDY.core.execution_signal_helper.record_agent_event", lambda **kw: None)
    run = _make_run(session)
    result = svc.execute_agent_run_via_workflow(
        run_id=str(run.id), plan={"steps": [{"tool": "t", "args": {}}]},
        user_id=str(run.user_id), db=session, execution_token=None,
    )
    assert result["status"] == "FAILED"
    session.refresh(run)
    assert run.status == "failed"
    from AINDY.db.models import AgentStep
    assert session.query(AgentStep).count() == 0  # nothing executed


# --------------------------------------------------------------------------- #
# RTR-1 Phase 2e — mid-plan WAIT/RESUME (segment-split, live-process)
# --------------------------------------------------------------------------- #

def _segment_aware_flow(**kw):
    """Fake run_nodus_script_via_flow: succeed exactly the steps in this segment.

    Reads the segment's step indices from input_payload's ``__step_N_tool`` keys
    and returns a success result for each — so a mocked segment run mirrors the
    real per-segment output_state without spawning the VM subprocess.
    """
    ip = kw.get("input_payload") or {}
    indices = sorted(int(k[len("__step_"):-len("_tool")]) for k in ip if k.endswith("_tool"))
    output_state = {
        f"__step_{i}_result": {"success": True, "result": {"i": i}, "error": None}
        for i in indices
    }
    return _flow_result(output_state)


@pytest.fixture
def _capture_agent_wait(monkeypatch, session):
    """Capture the scheduler wait registration and bind resume to the test DB."""
    captured = {}

    class _FakeScheduler:
        def register_wait(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(
        "AINDY.kernel.scheduler_engine.get_scheduler_engine", lambda: _FakeScheduler()
    )
    # The resume callback opens its own SessionLocal — point it at the test engine
    # (same in-memory DB; single-threaded so the connection is shared).
    monkeypatch.setattr(
        "AINDY.db.database.SessionLocal", sessionmaker(bind=session.get_bind())
    )
    return captured


def test_vm_run_waits_then_resumes(session, monkeypatch, _mock_side_effects, _capture_agent_wait):
    run = _make_run(session)
    plan = {"steps": [
        {"tool": "search", "args": {"q": "x"}, "risk_level": "low", "description": "s0"},
        {"wait_for": "approval.received"},
        {"tool": "send", "args": {"to": "y"}, "risk_level": "high", "description": "s2"},
    ]}
    monkeypatch.setattr(svc, "run_nodus_script_via_flow", _segment_aware_flow)

    # ── First call: runs segment 0, then parks on the wait ────────────────────
    result = svc.execute_agent_run_via_workflow(
        run_id=str(run.id), plan=plan, user_id=str(run.user_id), db=session,
        execution_token={"token_hash": "h", "granted_tools": ["search", "send"]},
    )
    assert result["status"] == "WAITING"
    assert result["wait_for"] == "approval.received"

    session.refresh(run)
    assert run.status == "waiting"
    assert run.steps_completed == 1  # only segment 0's single step ran

    from AINDY.db.models import AgentStep
    rows = session.query(AgentStep).order_by(AgentStep.step_index).all()
    assert [r.step_index for r in rows] == [0]  # step 2 has NOT run yet

    # The wait was registered on the scheduler with a resume callback.
    assert _capture_agent_wait["wait_for_event"] == "approval.received"
    assert _capture_agent_wait["eu_type"] == "agent"
    assert callable(_capture_agent_wait["resume_callback"])

    # ── Event fires: resume runs segment 1 (step index 2) and completes ───────
    _capture_agent_wait["resume_callback"]()

    session.refresh(run)
    assert run.status == "completed"
    assert run.steps_completed == 2  # two tool steps total: indices 0 and 1
    rows = session.query(AgentStep).order_by(AgentStep.step_index).all()
    assert [r.step_index for r in rows] == [0, 1]  # no re-run of step 0
    assert [s["step_index"] for s in run.result["steps"]] == [0, 1]


def test_vm_resume_segment_failure_fails_run(session, monkeypatch, _mock_side_effects, _capture_agent_wait):
    run = _make_run(session)
    plan = {"steps": [
        {"tool": "search", "args": {}, "risk_level": "low"},
        {"wait_for": "approval.received"},
        {"tool": "send", "args": {}, "risk_level": "low"},
    ]}

    def _flow(**kw):
        ip = kw.get("input_payload") or {}
        indices = sorted(int(k[len("__step_"):-len("_tool")]) for k in ip if k.endswith("_tool"))
        # Segment 1 (step index 1) fails; segment 0 (step 0) succeeds.
        output_state = {
            f"__step_{i}_result": (
                {"success": False, "result": None, "error": "send blew up"} if i == 1
                else {"success": True, "result": {"i": i}, "error": None}
            )
            for i in indices
        }
        return _flow_result(output_state)

    monkeypatch.setattr(svc, "run_nodus_script_via_flow", _flow)

    svc.execute_agent_run_via_workflow(
        run_id=str(run.id), plan=plan, user_id=str(run.user_id), db=session,
        execution_token={"token_hash": "h"},
    )
    session.refresh(run)
    assert run.status == "waiting"

    _capture_agent_wait["resume_callback"]()
    session.refresh(run)
    assert run.status == "failed"
    assert "send blew up" in (run.error_message or "")
    from AINDY.db.models import AgentStep
    rows = session.query(AgentStep).order_by(AgentStep.step_index).all()
    assert [r.status for r in rows] == ["success", "failed"]  # step 0 kept, step 2 failed


def test_vm_resume_is_idempotent_on_double_fire(session, monkeypatch, _mock_side_effects, _capture_agent_wait):
    """A duplicate event-fire must not run the next segment twice."""
    run = _make_run(session)
    plan = {"steps": [
        {"tool": "search", "args": {}, "risk_level": "low"},
        {"wait_for": "approval.received"},
        {"tool": "send", "args": {}, "risk_level": "low"},
    ]}
    monkeypatch.setattr(svc, "run_nodus_script_via_flow", _segment_aware_flow)

    svc.execute_agent_run_via_workflow(
        run_id=str(run.id), plan=plan, user_id=str(run.user_id), db=session,
        execution_token={"token_hash": "h"},
    )
    resume = _capture_agent_wait["resume_callback"]
    resume()   # first fire → completes
    resume()   # duplicate fire → must no-op (run already out of "waiting")

    session.refresh(run)
    assert run.status == "completed"
    from AINDY.db.models import AgentStep
    rows = session.query(AgentStep).order_by(AgentStep.step_index).all()
    assert [r.step_index for r in rows] == [0, 1]  # step 1 recorded exactly once


def test_vm_no_wait_plan_still_completes_in_one_segment(session, monkeypatch, _mock_side_effects, _capture_agent_wait):
    """Regression: a plan with no wait steps runs as a single segment, no wait registered."""
    run = _make_run(session)
    plan = {"steps": [{"tool": "a", "args": {}}, {"tool": "b", "args": {}}]}
    monkeypatch.setattr(svc, "run_nodus_script_via_flow", _segment_aware_flow)

    svc.execute_agent_run_via_workflow(
        run_id=str(run.id), plan=plan, user_id=str(run.user_id), db=session,
        execution_token={"token_hash": "h"},
    )
    session.refresh(run)
    assert run.status == "completed"
    assert run.steps_completed == 2
    assert _capture_agent_wait == {}  # no wait registered


def test_wait_persists_durable_wait_state(session, monkeypatch, _mock_side_effects, _capture_agent_wait):
    """Parking on a wait writes a durable wait_state descriptor for rehydration."""
    run = _make_run(session)
    plan = {"steps": [
        {"tool": "search", "args": {}, "risk_level": "low"},
        {"wait_for": "approval.received", "correlation_key": "ck-1"},
        {"tool": "send", "args": {}, "risk_level": "low"},
    ]}
    monkeypatch.setattr(svc, "run_nodus_script_via_flow", _segment_aware_flow)
    svc.execute_agent_run_via_workflow(
        run_id=str(run.id), plan=plan, user_id=str(run.user_id), db=session,
        execution_token={"token_hash": "h"},
    )
    session.refresh(run)
    assert run.status == "waiting"
    assert run.wait_state == {
        "event_type": "approval.received",
        "correlation_key": "ck-1",
        "resume_segment_index": 1,
    }
    # Resume clears the descriptor.
    _capture_agent_wait["resume_callback"]()
    session.refresh(run)
    assert run.status == "completed"
    assert run.wait_state is None


# --------------------------------------------------------------------------- #
# RTR-1 Phase 2e — cross-restart rehydration of waiting agent runs
# --------------------------------------------------------------------------- #

def _make_waiting_run(session, *, resume_segment_index=1, event_type="approval.received"):
    """A run already parked mid-plan: segment 0 (step 0) done, waiting before step 1."""
    from AINDY.db.models import AgentRun, AgentStep

    plan = {"steps": [
        {"tool": "search", "args": {}, "risk_level": "low"},
        {"wait_for": event_type},
        {"tool": "send", "args": {}, "risk_level": "low"},
    ]}
    run = AgentRun(
        id=uuid.uuid4(), user_id=uuid.uuid4(), goal="g",
        status="waiting", steps_total=2, steps_completed=1, current_step=1,
        plan=plan,
        result={"steps": [
            {"step_index": 0, "tool": "search", "status": "success", "result": {"i": 0}, "error": None},
        ]},
        wait_state={"event_type": event_type, "correlation_key": None,
                    "resume_segment_index": resume_segment_index},
        capability_token={"token_hash": "h", "granted_tools": ["send"]},
    )
    session.add(run)
    session.add(AgentStep(run_id=run.id, step_index=0, tool_name="search", status="success", result={"i": 0}))
    session.commit()
    return run


def _fake_scheduler(captured, *, already_registered=False):
    class _FakeScheduler:
        def waiting_for(self, rid):
            return "approval.received" if already_registered else None

        def register_wait(self, **kw):
            captured.append(kw)
    return _FakeScheduler()


def test_rehydrate_waiting_agent_run_resumes_after_restart(session, monkeypatch, _mock_side_effects):
    from AINDY.core.agent_run_rehydration import rehydrate_waiting_agent_runs
    from AINDY.db.models import AgentStep

    run = _make_waiting_run(session)
    monkeypatch.setattr(svc, "run_nodus_script_via_flow", _segment_aware_flow)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", sessionmaker(bind=session.get_bind()))

    captured = []
    # Fresh scheduler with nothing registered — models a process restart.
    monkeypatch.setattr(
        "AINDY.kernel.scheduler_engine.get_scheduler_engine",
        lambda: _fake_scheduler(captured),
    )

    n = rehydrate_waiting_agent_runs(session)
    assert n == 1
    assert captured[0]["wait_for_event"] == "approval.received"
    assert captured[0]["eu_type"] == "agent"
    assert callable(captured[0]["resume_callback"])

    # Event fires post-restart → resume runs segment 1 and completes.
    captured[0]["resume_callback"]()
    session.refresh(run)
    assert run.status == "completed"
    assert run.steps_completed == 2
    assert run.wait_state is None
    rows = session.query(AgentStep).order_by(AgentStep.step_index).all()
    assert [r.step_index for r in rows] == [0, 1]  # step 0 NOT re-run; step 1 added


def test_rehydrate_skips_already_registered(session, monkeypatch, _mock_side_effects):
    from AINDY.core.agent_run_rehydration import rehydrate_waiting_agent_runs

    _make_waiting_run(session)
    captured = []
    monkeypatch.setattr(
        "AINDY.kernel.scheduler_engine.get_scheduler_engine",
        lambda: _fake_scheduler(captured, already_registered=True),
    )
    n = rehydrate_waiting_agent_runs(session)
    assert n == 0  # a live registration already survived — do not double-register
    assert captured == []


def test_rehydrate_skips_run_without_wait_state(session, monkeypatch, _mock_side_effects):
    from AINDY.core.agent_run_rehydration import rehydrate_waiting_agent_runs

    run = _make_waiting_run(session)
    run.wait_state = None  # corrupt/missing descriptor
    session.commit()
    captured = []
    monkeypatch.setattr(
        "AINDY.kernel.scheduler_engine.get_scheduler_engine",
        lambda: _fake_scheduler(captured),
    )
    n = rehydrate_waiting_agent_runs(session)
    assert n == 0
    assert captured == []
