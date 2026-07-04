"""
RTR-1 — nodus_vm ↔ AGENT_FLOW parity validation against real PostgreSQL.

The opt-in ``nodus_vm`` agent backend runs tool calls inside the nodus_worker
subprocess; that path only works on Linux + a real DB (the Windows dev box blocks
the subprocess, and unit tests mock the flow). These integration tests drive the
FULL path — real subprocess, real flow engine, real capability token, real
``sys.v1.memory.*`` tool execution — against PostgreSQL and assert the two
backends produce equivalent observable outcomes.

Uses the runtime-native ``memory.recall`` tool (a safe read; args are all
optional). Runtime tools are only executable in the subprocess because
``_ensure_tools_loaded`` now also loads the runtime agent defaults — the parity
blocker this suite exists to guard.

Cross-connection caveat (see test_planner_loop_execute_to_completion.py): the
backends commit and open their own SessionLocal, so everything here uses
committed users and fresh SessionLocal reads, not the rolled-back db_session.

Requires: docker-compose -f docker-compose.test.yml up -d  (DATABASE_URL → PostgreSQL).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

pytestmark = [pytest.mark.integration]


@pytest.fixture(autouse=True)
def _restore_request_context():
    """Driving execution directly (outside the ASGI pipeline) sets the request/
    trace ContextVar without the middleware's reset, which would leak into later
    tests (e.g. test_request_context). Restore it around each test."""
    from AINDY.main import _request_id_ctx

    before = _request_id_ctx.get()
    try:
        yield
    finally:
        _request_id_ctx.set(before)


# --------------------------------------------------------------------------- #
# Helpers — all use committed sessions (cross-connection visibility)
# --------------------------------------------------------------------------- #

def _ensure_runtime_tools() -> None:
    """Register the runtime-native tools + capability bundle (idempotent)."""
    from AINDY.platform_layer import runtime_agent_defaults

    runtime_agent_defaults.register()


def _committed_user():
    from AINDY.db.database import SessionLocal
    from AINDY.db.models.user import User
    from AINDY.services.auth_service import hash_password

    s = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        user = User(
            email=f"parity-{suffix}@aindy.test",
            username=f"parity-{suffix}",
            hashed_password=hash_password("parity-pw"),
            is_active=True,
            is_admin=True,
        )
        s.add(user)
        s.commit()
        s.refresh(user)
        return user.id
    finally:
        s.close()


def _create_executing_run(user_id, plan):
    """Create a committed AgentRun in status='executing' with a minted token.

    Mirrors the real approve+execute setup (approvals.py / execution.py) so both
    backends see a normal approved run. Returns (run_id_str, token_dict).
    """
    from AINDY.agents.capability_service import mint_token
    from AINDY.db.database import SessionLocal
    from AINDY.db.models import AgentRun

    _ensure_runtime_tools()
    tool_steps = [st for st in plan["steps"] if st.get("tool")]
    s = SessionLocal()
    try:
        run = AgentRun(
            id=uuid.uuid4(), user_id=user_id, agent_type="default",
            goal="parity probe", plan=plan, status="pending_approval",
            steps_total=len(tool_steps),
        )
        s.add(run)
        s.commit()
        s.refresh(run)
        token = mint_token(str(run.id), str(user_id), plan, s, approval_mode="manual")
        assert token is not None, "mint_token returned None — runtime tools not grantable?"
        run.capability_token = token
        run.execution_token = token["execution_token"]
        run.status = "executing"
        run.started_at = datetime.now(timezone.utc)
        s.commit()
        return str(run.id), token
    finally:
        s.close()


def _execute(backend, *, run_id, plan, token, user_id, monkeypatch):
    from AINDY.db.database import SessionLocal
    from AINDY.runtime.nodus_execution_service import execute_agent_run_via_nodus

    monkeypatch.setenv("AINDY_AGENT_EXECUTION_BACKEND", backend)
    s = SessionLocal()
    try:
        return execute_agent_run_via_nodus(
            run_id=run_id, plan=plan, user_id=str(user_id), db=s,
            correlation_id=run_id, execution_token=token,
        )
    finally:
        s.close()


def _read_run(run_id):
    from AINDY.db.database import SessionLocal
    from AINDY.db.models import AgentRun

    s = SessionLocal()
    try:
        r = s.query(AgentRun).filter(AgentRun.id == uuid.UUID(run_id)).first()
        if r is None:
            return None
        steps = (r.result or {}).get("steps") or []
        return {
            "status": r.status,
            "steps_completed": r.steps_completed,
            "current_step": r.current_step,
            "result_steps": [(st.get("step_index"), st.get("tool"), st.get("status")) for st in steps],
            "has_error": bool(r.error_message),
            "wait_state": r.wait_state,
        }
    finally:
        s.close()


def _read_steps(run_id):
    """Ordered (step_index, tool_name, status) for the run's AgentStep rows."""
    from AINDY.db.database import SessionLocal
    from AINDY.db.models import AgentStep

    s = SessionLocal()
    try:
        rows = (
            s.query(AgentStep)
            .filter(AgentStep.run_id == uuid.UUID(run_id))
            .order_by(AgentStep.step_index)
            .all()
        )
        return [(r.step_index, r.tool_name, r.status) for r in rows]
    finally:
        s.close()


_RECALL_PLAN = {"steps": [
    {"tool": "memory.recall", "args": {"query": "parity-a", "limit": 3}, "risk_level": "low", "description": "recall a"},
    {"tool": "memory.recall", "args": {"query": "parity-b", "limit": 3}, "risk_level": "low", "description": "recall b"},
]}


# --------------------------------------------------------------------------- #
# Success parity — both backends complete the same plan identically
# --------------------------------------------------------------------------- #

def test_success_parity_across_backends(monkeypatch):
    user_id = _committed_user()
    results = {}
    for backend in ("agent_flow", "nodus_vm"):
        run_id, token = _create_executing_run(user_id, _RECALL_PLAN)
        _execute(backend, run_id=run_id, plan=_RECALL_PLAN, token=token, user_id=user_id, monkeypatch=monkeypatch)
        results[backend] = (_read_run(run_id), _read_steps(run_id))

    af_run, af_steps = results["agent_flow"]
    vm_run, vm_steps = results["nodus_vm"]

    # Both backends ran the real tools to completion on real Postgres.
    assert af_run["status"] == "completed", af_run
    assert vm_run["status"] == "completed", vm_run
    assert af_run["steps_completed"] == vm_run["steps_completed"] == 2

    expected_steps = [(0, "memory.recall", "success"), (1, "memory.recall", "success")]
    assert af_steps == expected_steps, af_steps
    assert vm_steps == expected_steps, vm_steps
    # result[steps] (index, tool, status) is identical across backends.
    assert af_run["result_steps"] == vm_run["result_steps"] == expected_steps


# --------------------------------------------------------------------------- #
# Failure parity — an invalid token is denied identically at the flow gate
# --------------------------------------------------------------------------- #

def test_capability_denied_parity(monkeypatch):
    user_id = _committed_user()
    results = {}
    for backend in ("agent_flow", "nodus_vm"):
        run_id, token = _create_executing_run(user_id, _RECALL_PLAN)
        bad_token = dict(token)
        bad_token["token_hash"] = "deadbeef"  # integrity check fails → execute_flow denied
        _execute(backend, run_id=run_id, plan=_RECALL_PLAN, token=bad_token, user_id=user_id, monkeypatch=monkeypatch)
        results[backend] = (_read_run(run_id), _read_steps(run_id))

    for backend, (run, steps) in results.items():
        assert run["status"] == "failed", (backend, run)
        assert steps == [], (backend, steps)  # denied before any step executed


# --------------------------------------------------------------------------- #
# nodus_vm-only — durable mid-plan WAIT → RESUME on real Postgres
# --------------------------------------------------------------------------- #

_WAIT_PLAN = {"steps": [
    {"tool": "memory.recall", "args": {"query": "wait-a", "limit": 1}, "risk_level": "low", "description": "s0"},
    {"wait_for": "parity.approval"},
    {"tool": "memory.recall", "args": {"query": "wait-b", "limit": 1}, "risk_level": "low", "description": "s2"},
]}


def test_nodus_vm_wait_resume_cycle_on_postgres(monkeypatch):
    """A plan with a mid-plan WAIT parks the run (segment 0 executed on PG), then a
    fired resume runs segment 1 to completion — no re-run of segment 0's step."""
    user_id = _committed_user()
    run_id, token = _create_executing_run(user_id, _WAIT_PLAN)

    # The integration env runs no scheduler loop, so capture the wait registration
    # and fire the resume callback directly. register_wait itself is exercised
    # (real) in the live path; here we only need a deterministic resume trigger.
    captured = []

    class _CaptureScheduler:
        def waiting_for(self, _run_id):
            return None

        def register_wait(self, **kw):
            captured.append(kw)

    monkeypatch.setattr(
        "AINDY.kernel.scheduler_engine.get_scheduler_engine", lambda: _CaptureScheduler()
    )

    res = _execute("nodus_vm", run_id=run_id, plan=_WAIT_PLAN, token=token, user_id=user_id, monkeypatch=monkeypatch)

    # Parked mid-plan: segment 0's tool ran on real PG; the run is durably waiting.
    assert res.get("status") == "WAITING", res
    parked = _read_run(run_id)
    assert parked["status"] == "waiting", parked
    assert parked["wait_state"]["event_type"] == "parity.approval"
    assert parked["wait_state"]["resume_segment_index"] == 1
    assert _read_steps(run_id) == [(0, "memory.recall", "success")]
    assert captured and captured[0]["wait_for_event"] == "parity.approval"
    assert captured[0]["eu_type"] == "agent"

    # Fire the resume → segment 1 (step index 1) runs on real PG and completes.
    captured[0]["resume_callback"]()

    resumed = _read_run(run_id)
    assert resumed["status"] == "completed", resumed
    assert resumed["wait_state"] is None
    assert _read_steps(run_id) == [
        (0, "memory.recall", "success"),  # NOT re-run
        (1, "memory.recall", "success"),
    ]


# --------------------------------------------------------------------------- #
# Real tool FAILURE — retry + halt end-to-end on real PG (via runtime.selftest)
# --------------------------------------------------------------------------- #

def _step_error(run_id, step_index):
    from AINDY.db.database import SessionLocal
    from AINDY.db.models import AgentStep

    s = SessionLocal()
    try:
        row = (
            s.query(AgentStep)
            .filter(AgentStep.run_id == uuid.UUID(run_id), AgentStep.step_index == step_index)
            .first()
        )
        return None if row is None else (row.error_message or "")
    finally:
        s.close()


def test_tool_failure_parity(monkeypatch):
    """A real tool failure (raised in the handler) fails the run identically on
    both backends — the failed-step shape the mocked tests could not exercise."""
    plan = {"steps": [
        {"tool": "runtime.selftest", "args": {"outcome": "fail", "error": "boom"}, "risk_level": "high", "description": "fail"},
    ]}
    results = {}
    for backend in ("agent_flow", "nodus_vm"):
        run_id, token = _create_executing_run(_committed_user(), plan)
        _execute(backend, run_id=run_id, plan=plan, token=token, user_id=token["user_id"], monkeypatch=monkeypatch)
        results[backend] = (_read_run(run_id), _read_steps(run_id), _step_error(run_id, 0))

    for backend, (run, steps, err) in results.items():
        assert run["status"] == "failed", (backend, run)
        assert steps == [(0, "runtime.selftest", "failed")], (backend, steps)
        assert "boom" in (err or ""), (backend, err)


def test_halt_on_first_failure_parity(monkeypatch):
    """A failed step halts the plan on both backends — the downstream step never runs."""
    plan = {"steps": [
        {"tool": "runtime.selftest", "args": {"outcome": "fail", "error": "boom"}, "risk_level": "high", "description": "fail"},
        {"tool": "memory.recall", "args": {"query": "should-not-run"}, "risk_level": "low", "description": "downstream"},
    ]}
    for backend in ("agent_flow", "nodus_vm"):
        run_id, token = _create_executing_run(_committed_user(), plan)
        _execute(backend, run_id=run_id, plan=plan, token=token, user_id=token["user_id"], monkeypatch=monkeypatch)
        run, steps = _read_run(run_id), _read_steps(run_id)
        assert run["status"] == "failed", (backend, run)
        # Only the failing step ran; the downstream memory.recall was halted.
        assert steps == [(0, "runtime.selftest", "failed")], (backend, steps)


def _selftest_fail_plan(error, risk):
    return {"steps": [
        {"tool": "runtime.selftest",
         "args": {"outcome": "fail", "error": error, "attempt_key": uuid.uuid4().hex},
         "risk_level": risk, "description": "fail probe"},
    ]}


def test_nodus_vm_retryable_failure_retries_on_postgres(monkeypatch):
    """A transient (retryable) failure on a low-risk step is retried to exhaustion
    (3 attempts) inside the real subprocess — proven by the attempt count carried in
    the recorded error."""
    plan = _selftest_fail_plan("transient timeout", "low")
    run_id, token = _create_executing_run(_committed_user(), plan)
    _execute("nodus_vm", run_id=run_id, plan=plan, token=token, user_id=token["user_id"], monkeypatch=monkeypatch)
    assert _read_run(run_id)["status"] == "failed"
    assert "attempt 3" in (_step_error(run_id, 0) or ""), _step_error(run_id, 0)


def test_nodus_vm_nonretryable_failure_short_circuits_on_postgres(monkeypatch):
    """A non-transient error ('permission') is NOT retried, even on a low-risk step."""
    plan = _selftest_fail_plan("permission denied", "low")
    run_id, token = _create_executing_run(_committed_user(), plan)
    _execute("nodus_vm", run_id=run_id, plan=plan, token=token, user_id=token["user_id"], monkeypatch=monkeypatch)
    assert _read_run(run_id)["status"] == "failed"
    assert "attempt 1" in (_step_error(run_id, 0) or ""), _step_error(run_id, 0)


def test_nodus_vm_high_risk_single_attempt_on_postgres(monkeypatch):
    """A high-risk step is never retried (max_attempts=1), even on a retryable error."""
    plan = _selftest_fail_plan("transient timeout", "high")
    run_id, token = _create_executing_run(_committed_user(), plan)
    _execute("nodus_vm", run_id=run_id, plan=plan, token=token, user_id=token["user_id"], monkeypatch=monkeypatch)
    assert _read_run(run_id)["status"] == "failed"
    assert "attempt 1" in (_step_error(run_id, 0) or ""), _step_error(run_id, 0)
