"""EXEC-ENV-BIND-1 phase 4 — the resources axis becomes ENFORCING, not just declared.

Phases 1–3 could declare a ceiling, refuse a host that could not meet an assurance class, and
record required-vs-applied on the row — and enforce nothing on the resources axis, because the
only ceilings that bound anything were `resource_manager`'s globals, at a different seam, in a
different vocabulary. Phase 4 joins them:

* `resources.tokens` is the descriptor's fourth dimension — the row `COST-GOVERNOR-1` said it
  would want; clamped narrow-only like the others;
* `require_execution_unit` hands the EFFECTIVE (floor-clamped) resources to the resource
  manager, and `env_applied.resources_enforced` says which of them will actually bind — memory
  is declared and NOT enforced (`SYSMAX-3`), and a row must be able to say so;
* `check_quota` and the token governor enforce `min(global, declared)`: a declaration narrows
  a global ceiling and can never widen it;
* under `AINDY_RUN_SCOPED_QUOTA` (default OFF) the guest's `sys()` calls and an agent run's
  execution span bind the RUN's unit, so they are checked against ITS ceilings — accounting
  only; the idempotency gate keys on the caller's own id and is untouched.

Mutation-checked: drop `_declare_resource_limits(...)` from the gate and the gate test fails;
make `effective_limit` return the global only and both narrowing tests fail; drop
`resources_enforced` and its test fails; bind the unit regardless of the flag and the flag-off
control fails; drop the tokens clamp and the widen test fails.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from AINDY.core.execution_environment import (
    ExecutionEnvironmentSpec,
    Resources,
    clamp_to_floor,
    enforced_resources,
)
from AINDY.kernel import resource_manager as rm_mod
from AINDY.kernel import syscall_dispatcher as sd
from AINDY.kernel.resource_manager import ResourceManager

pytestmark = pytest.mark.runtime_only


@pytest.fixture
def rm(monkeypatch):
    fresh = ResourceManager()
    monkeypatch.setattr(rm_mod, "_RESOURCE_MANAGER", fresh)
    monkeypatch.setattr(sd, "_get_rm", lambda: fresh)
    return fresh


@pytest.fixture
def enforcing():
    from AINDY.config import Settings

    with patch.object(Settings, "is_testing", new_callable=PropertyMock, return_value=False):
        yield


# ── the descriptor ────────────────────────────────────────────────────────────


def test_tokens_is_a_resources_dimension_and_round_trips():
    spec = ExecutionEnvironmentSpec.from_dict({"resources": {"tokens": 5000, "syscalls": 40}})
    assert spec.resources.tokens == 5000
    assert ExecutionEnvironmentSpec.from_dict(spec.to_dict()).resources.tokens == 5000


def test_tokens_clamps_narrow_only_like_the_other_ceilings():
    floor = ExecutionEnvironmentSpec(resources=Resources(tokens=1000))
    wider = ExecutionEnvironmentSpec(resources=Resources(tokens=50_000))
    effective, widened = clamp_to_floor(wider, floor)
    assert effective.resources.tokens == 1000 and "resources.tokens" in widened
    narrower = ExecutionEnvironmentSpec(resources=Resources(tokens=200))
    effective, widened = clamp_to_floor(narrower, floor)
    assert effective.resources.tokens == 200 and not widened


def test_enforced_resources_names_only_what_the_runtime_binds():
    spec = ExecutionEnvironmentSpec(resources=Resources(wall_time_ms=1, memory_bytes=1, syscalls=1, tokens=1))
    assert enforced_resources(spec) == ["wall_time_ms", "syscalls", "tokens"], "memory is declared, not enforced (SYSMAX-3)"
    assert enforced_resources(ExecutionEnvironmentSpec()) == []


# ── the gate hands ceilings to the resource manager ───────────────────────────


class _FakeService:
    def __init__(self, db):
        self.created: list[dict] = []

    def get_by_source(self, *_a, **_k):
        return None

    def create(self, **kwargs):
        self.created.append(kwargs)
        return MagicMock(id="eu-declared", extra=kwargs.get("extra"))

    def update_status(self, *_a, **_k):
        return True


def test_require_execution_unit_declares_effective_limits_and_records_what_is_enforced(rm, monkeypatch):
    from AINDY.core.execution_gate import require_execution_unit

    monkeypatch.setattr("AINDY.core.execution_unit_service.ExecutionUnitService", _FakeService)
    monkeypatch.setattr(
        "AINDY.core.execution_environment._host_assurance",
        lambda: ("insecure-dev", "insecure-dev/no-isolation-guarantee"),
    )
    require_execution_unit(
        db=MagicMock(), eu_type="flow", user_id="tenant-g", source_type="flow_run", source_id="src-g",
        env_spec={"resources": {"syscalls": 7, "tokens": 900, "memory_bytes": 4096}},
    )
    limits = rm.declared_limits("eu-declared")
    assert limits == {"syscalls": 7, "tokens": 900, "memory_bytes": 4096}
    assert rm.get_usage("eu-declared")["tenant_id"] == "tenant-g"
    # The row says which of those will bind — and memory is not among them.
    # (The fake service's create kwargs carry the columns the gate computed.)
    import AINDY.core.execution_gate as gate

    columns = gate._resolve_env_columns(
        db=MagicMock(), eu_type="flow", user_id="tenant-g", source_type="flow_run", source_id="src-g",
        correlation_id=None, merged_extra={}, env_spec={"resources": {"syscalls": 7, "tokens": 900, "memory_bytes": 4096}},
    )
    assert columns["env_applied"]["resources_enforced"] == ["syscalls", "tokens"]


# ── enforcement: min(global, declared) ────────────────────────────────────────


def test_a_declared_syscall_ceiling_narrows_the_global_one(rm, enforcing, monkeypatch):
    monkeypatch.setattr(rm_mod, "MAX_SYSCALLS_PER_EXECUTION", 100)
    rm.declare_limits("eu-n", syscalls=5, tenant_id="t")
    rm.record_syscall("eu-n", 5)
    assert rm.check_quota("eu-n") == (True, None)
    rm.record_syscall("eu-n", 1)
    ok, reason = rm.check_quota("eu-n")
    assert not ok and "(6 > 5)" in reason, reason
    # Control: an undeclared unit is still bound by the global.
    rm.record_syscall("eu-global", 6)
    assert rm.check_quota("eu-global") == (True, None)


def test_a_declared_ceiling_cannot_widen_the_global_one(rm, enforcing, monkeypatch):
    monkeypatch.setattr(rm_mod, "MAX_SYSCALLS_PER_EXECUTION", 3)
    rm.declare_limits("eu-w", syscalls=500)
    assert rm.effective_limit("eu-w", "syscalls") == 3
    rm.record_syscall("eu-w", 4)
    assert rm.check_quota("eu-w")[0] is False


def test_a_declared_token_ceiling_is_what_the_governor_enforces(rm, monkeypatch):
    from AINDY.platform_layer.llm_budget import llm_budget_reservation
    from AINDY.platform_layer.llm_client import LLMBudgetExceededError
    from AINDY.platform_layer.token_meter import llm_attribution_scope

    monkeypatch.setattr(rm_mod, "MAX_TOKENS_PER_EXECUTION", 0)  # no global cap at all
    monkeypatch.setattr(rm_mod, "MAX_TOKENS_PER_TENANT_WINDOW", 0)
    rm.declare_limits("run-t", tokens=300, tenant_id="t")
    with llm_attribution_scope(tenant_id="t", run_id="run-t"):
        with pytest.raises(LLMBudgetExceededError) as exc_info:
            with llm_budget_reservation(provider="probe", kwargs={"max_tokens": 500}):
                pytest.fail("must not run")
    assert exc_info.value.cap == 300 and exc_info.value.scope == "execution"


def test_declared_limits_are_visible_from_the_shared_store(rm, monkeypatch):
    """A worker in another process binds the unit and must see the same ceilings."""
    store: dict = {}
    backend = SimpleNamespace(
        set_limits=lambda eu, limits: store.update({eu: dict(limits)}),
        get_limits=lambda eu: dict(store.get(eu, {})),
    )
    rm._backend = backend
    rm.declare_limits("eu-shared", syscalls=9)
    other = ResourceManager()
    other._backend = backend
    assert other.declared_limits("eu-shared") == {"syscalls": 9}
    assert other.effective_limit("eu-shared", "syscalls") == 9


# ── the flag: the run is the subject ──────────────────────────────────────────


def test_bind_execution_unit_nests_dispatches_and_releases(rm):
    from AINDY.kernel.syscall_dispatcher import bind_execution_unit

    with bind_execution_unit("unit-b", "trace-b"):
        assert sd._EU_ID_CTX.get() == "unit-b" and sd._TRACE_ID_CTX.get() == "trace-b"
    assert sd._EU_ID_CTX.get() == "" and sd._TRACE_ID_CTX.get() == ""
    with bind_execution_unit(""):
        assert sd._EU_ID_CTX.get() == "", "an empty id binds nothing (never a '' unit)"


def _run_guest(monkeypatch, execution_unit_id: str):
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.runtime import nodus_worker

    seen: dict = {}

    def _dispatch(name, payload, *, user_id):
        seen["unit_during_sys"] = sd._EU_ID_CTX.get()
        return {"status": "success", "data": {}}

    monkeypatch.setattr(nodus_worker, "dispatch_worker_syscall", _dispatch)
    nodus_worker.run_one({
        "script": 'sys("sys.v1.probe.noop", {})\nset_state("x", 1)\n',
        "state": {},
        "context": {"user_id": "guest-u", "execution_unit_id": execution_unit_id, "trace_id": "trace-g"},
    })
    return seen


def test_guest_sys_calls_do_not_bind_the_run_unit_by_default(monkeypatch):
    monkeypatch.delenv("AINDY_RUN_SCOPED_QUOTA", raising=False)
    seen = _run_guest(monkeypatch, "run-guest-off")
    assert seen.get("unit_during_sys") == "", "flag off: each sys() call is its own (reaped) unit"


def test_guest_sys_calls_bind_the_run_unit_when_flagged(monkeypatch):
    monkeypatch.setenv("AINDY_RUN_SCOPED_QUOTA", "1")
    seen = _run_guest(monkeypatch, "run-guest-on")
    assert seen.get("unit_during_sys") == "run-guest-on"


def test_execute_run_binds_the_run_and_declares_its_ceilings_when_flagged(db_session, monkeypatch, rm):
    from AINDY.agents.agent_runtime import execution
    from AINDY.db.models import AgentRun

    monkeypatch.setenv("AINDY_RUN_SCOPED_QUOTA", "1")
    run = AgentRun(
        user_id=uuid.uuid4(), goal="g", plan={"steps": []}, executive_summary="x", overall_risk="low",
        status="approved", steps_total=0, correlation_id=f"run_{uuid.uuid4()}", trace_id="trace-r",
        capability_token={"allowed_capabilities": []},
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    from AINDY.core.execution_unit_service import ExecutionUnitService

    fake_eu = SimpleNamespace(id="eu-row", env_applied={"resources": {"syscalls": 11, "tokens": 777}})
    monkeypatch.setattr(ExecutionUnitService, "get_by_source", lambda self, *a, **k: fake_eu)
    monkeypatch.setattr(ExecutionUnitService, "update_status", lambda self, *a, **k: True)

    seen: dict = {}

    def _fake_execution(**kwargs):
        seen["unit"] = sd._EU_ID_CTX.get()
        seen["limits"] = rm.declared_limits(str(run.id))
        r = db_session.query(AgentRun).filter(AgentRun.id == run.id).first()
        r.status = "completed"
        db_session.commit()

    monkeypatch.setattr(execution, "register_or_update_agent", lambda *a, **k: {})
    monkeypatch.setattr(execution, "decide_execution_mode", lambda *a, **k: {"mode": "local", "selected_agent": None, "candidates": []})
    monkeypatch.setattr(execution, "record_agent_event", lambda *a, **k: "event-1")
    monkeypatch.setattr("AINDY.runtime.nodus_execution_service.execute_agent_run_via_nodus", _fake_execution)
    monkeypatch.setattr(execution, "_emit_agent_next_action", lambda *a, **k: None)
    monkeypatch.setattr("AINDY.core.execution_score.emit_execution_score", lambda **k: None)

    result = execution.execute_run(run_id=str(run.id), user_id=str(run.user_id), db=db_session)

    assert result is not None and result["status"] == "completed"
    assert seen["unit"] == str(run.id), "the run is the quota subject for its execution span"
    assert seen["limits"] == {"syscalls": 11, "tokens": 777}, "the row's ceilings moved onto the accounting key"
    assert sd._EU_ID_CTX.get() == "", "released with the span"
