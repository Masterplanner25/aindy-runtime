"""SYSMAX-4 — an agent run's dispatches are charged to ONE budget, on both backends (real Postgres).

`AINDY_RUN_SCOPED_QUOTA` makes the RUN the quota subject: `execute_run` binds `run.id` around the
backend call (unit-tested in `tests/unit/test_exec_env_resources_enforcing.py`), so every dispatch
under it accrues on the run and `check_quota` enforces the run's cap. Before the flip, each such
dispatch minted a one-call unit, so `AINDY_QUOTA_MAX_SYSCALLS` never applied to them.

★ The flip's soak found a second hole: on `nodus_vm` the tool calls run in a WORKER process, which
bound the Nodus execution's own unit and counted in its own memory. A 3-step run read 1 syscall
on the run. Now the parent hands the worker the unit and its usage so far, the worker checks
against the running total, and the reply carries what it added (`quota_usage`).

The binding is applied here exactly as `execute_run` applies it (`observed_unit` +
`bind_execution_unit(run.id)`), around the real backend on a real committed run.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def engine(test_engine):
    if test_engine.dialect.name != "postgresql":
        pytest.skip("run-scoped accounting is soaked on Postgres")
    return test_engine


@pytest.fixture
def _restore_request_context():
    from AINDY.main import _request_id_ctx

    before = _request_id_ctx.get()
    try:
        yield
    finally:
        _request_id_ctx.set(before)


def _recall_plan(n: int) -> dict:
    return {"steps": [{"tool": "memory.recall", "args": {"query": f"q{i}"}, "risk_level": "low",
                       "description": f"s{i}"} for i in range(n)]}


def _run_as_execute_run_does(backend: str, n: int, monkeypatch):
    import contextlib

    from AINDY.kernel.resource_manager import get_resource_manager, run_scoped_quota_enabled
    from AINDY.kernel.syscall_dispatcher import bind_execution_unit
    from tests.integration.test_agent_vm_parity import _committed_user, _create_executing_run, _execute, _read_run

    user = _committed_user()
    plan = _recall_plan(n)
    run_id, token = _create_executing_run(user, plan)
    rm = get_resource_manager()
    # exactly execute_run's shape: the run is bound only when the flag says so
    bound = bind_execution_unit(run_id, run_id, syscalls_only=True) if run_scoped_quota_enabled() else contextlib.nullcontext()
    with rm.observed_unit(str(user), run_id), bound:
        _execute(backend, run_id=run_id, plan=plan, token=token, user_id=user, monkeypatch=monkeypatch)
        syscalls = int(rm.get_usage(run_id).get("syscall_count") or 0)
    return {**_read_run(run_id), "run_id": run_id}, syscalls


@pytest.fixture
def fresh_workers(monkeypatch):
    """A warm worker keeps the environment it was spawned with, so a control that changes the
    environment needs a fresh worker per execution. (Production fixes the environment at boot.)"""
    monkeypatch.setenv("AINDY_NODUS_WARM_POOL", "0")


@pytest.mark.parametrize("backend", ["agent_flow", "nodus_vm"])
def test_every_tool_dispatch_of_a_run_is_charged_to_the_run(engine, monkeypatch, _restore_request_context,
                                                         fresh_workers, backend):
    monkeypatch.setenv("AINDY_RUN_SCOPED_QUOTA", "0")
    run_off, off = _run_as_execute_run_does(backend, 3, monkeypatch)
    monkeypatch.delenv("AINDY_RUN_SCOPED_QUOTA")  # the default
    run_on, on = _run_as_execute_run_does(backend, 3, monkeypatch)
    print(f"\n[run-quota] {backend}: 3 recall steps charge the run {off} syscalls off, {on} on")
    assert run_off["status"] == run_on["status"] == "completed", (run_off, run_on)
    assert on - off == 3, (
        f"{backend}: 3 tool dispatches added {on - off} to the run's count. A dispatch that is not "
        f"charged to the run escapes its cap"
    )


@pytest.mark.parametrize("backend", ["agent_flow", "nodus_vm"])
def test_the_run_is_refused_at_its_cap_and_not_before(engine, monkeypatch, _restore_request_context, fresh_workers,
                                                    backend):
    """★ The check is real only outside test mode, and on `nodus_vm` in the worker too: the
    test-mode short-circuit sits above the decision (standing rule), so both are switched off."""
    from AINDY.config import Settings
    from AINDY.kernel import resource_manager as rmod

    monkeypatch.setattr(Settings, "is_testing", property(lambda self: False))
    for key, value in (("TESTING", "false"), ("TEST_MODE", "false"), ("ENV", "development")):
        monkeypatch.setenv(key, value)  # the worker process reads its own settings

    monkeypatch.setattr(rmod, "MAX_SYSCALLS_PER_EXECUTION", 100)
    monkeypatch.setenv("AINDY_QUOTA_MAX_SYSCALLS", "100")
    control, _ = _run_as_execute_run_does(backend, 5, monkeypatch)
    assert control["status"] == "completed" and control["steps_completed"] == 5, (backend, control)

    monkeypatch.setattr(rmod, "MAX_SYSCALLS_PER_EXECUTION", 2)
    monkeypatch.setenv("AINDY_QUOTA_MAX_SYSCALLS", "2")
    capped, used = _run_as_execute_run_does(backend, 5, monkeypatch)
    print(f"\n[run-quota] {backend}: cap 2 -> {capped['status']}, {capped['steps_completed']} steps, {used} syscalls")
    # `check_quota` refuses once the count EXCEEDS the cap, before the call: 3 run, the 4th is refused.
    assert capped["status"] == "failed" and capped["steps_completed"] == 3, (backend, capped)
    assert "RESOURCE_LIMIT_EXCEEDED" in _step_error(capped["run_id"], 3), _step_error(capped["run_id"], 3)


def _step_error(run_id: str, index: int) -> str:
    import uuid

    from AINDY.db.database import SessionLocal
    from AINDY.db.models import AgentRun

    s = SessionLocal()
    try:
        run = s.query(AgentRun).filter(AgentRun.id == uuid.UUID(run_id)).first()
        steps = (run.result or {}).get("steps") or []
        return str(steps[index]) if index < len(steps) else str(run.result)
    finally:
        s.close()


def test_a_warm_worker_charges_the_run_too(engine, monkeypatch, _restore_request_context):
    """The production shape: the default flag and the warm pool (on by default)."""
    monkeypatch.delenv("AINDY_RUN_SCOPED_QUOTA", raising=False)
    monkeypatch.delenv("AINDY_NODUS_WARM_POOL", raising=False)
    _run_as_execute_run_does("nodus_vm", 1, monkeypatch)  # warm the pool under this environment
    one, n1 = _run_as_execute_run_does("nodus_vm", 1, monkeypatch)
    three, n3 = _run_as_execute_run_does("nodus_vm", 3, monkeypatch)
    print(f"[run-quota] warm nodus_vm: 1 step {n1}, 3 steps {n3}")
    assert one["status"] == three["status"] == "completed"
    assert n3 - n1 == 2, f"two more tool steps added {n3 - n1} to the run's count on a warm worker"
