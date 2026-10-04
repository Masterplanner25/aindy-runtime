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


def _run_guest(monkeypatch, execution_unit_id: str, *, quota: dict | None = None, calls: int = 1, rm=None):
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.runtime import nodus_worker

    seen: dict = {"units": []}

    def _dispatch(name, payload, *, user_id):
        unit = sd._EU_ID_CTX.get()
        seen["unit_during_sys"] = unit
        seen["units"].append(unit)
        seen["syscalls_only"] = sd._SYSCALLS_ONLY_CTX.get()
        if rm is not None and unit:
            rm.record_syscall(unit)  # what the real dispatcher accrues per call
        return {"status": "success", "data": {}}

    monkeypatch.setattr(nodus_worker, "dispatch_worker_syscall", _dispatch)
    context = {"user_id": "guest-u", "execution_unit_id": execution_unit_id, "trace_id": "trace-g"}
    if quota is not None:
        context["quota"] = quota
    seen["reply"] = nodus_worker.run_one({
        "script": 'sys("sys.v1.probe.noop", {})\n' * calls + 'set_state("x", 1)\n',
        "state": {},
        "context": context,
    })
    return seen


def test_guest_sys_calls_bind_the_run_unit_by_default(monkeypatch):
    """SYSMAX-4 (DEC-095): the run is the quota subject unless an operator says otherwise."""
    monkeypatch.delenv("AINDY_RUN_SCOPED_QUOTA", raising=False)
    seen = _run_guest(monkeypatch, "run-guest-on")
    assert seen.get("unit_during_sys") == "run-guest-on"


@pytest.mark.parametrize("off", ["0", "false", "no", "off"])
def test_guest_sys_calls_do_not_bind_the_run_unit_when_disabled(monkeypatch, off):
    monkeypatch.setenv("AINDY_RUN_SCOPED_QUOTA", off)
    seen = _run_guest(monkeypatch, "run-guest-off")
    assert seen.get("unit_during_sys") == "", "disabled: each sys() call is its own (reaped) unit"


# ── SYSMAX-4: the worker charges the PARENT's unit and reports what it added ──────────────────


def test_the_worker_charges_the_unit_the_parent_names_from_its_running_total(monkeypatch, rm):
    """★ The worker is another process. Before, it bound the Nodus execution's own unit and
    counted in its own memory, so a `nodus_vm` run's tool syscalls never reached the run."""
    monkeypatch.delenv("AINDY_RUN_SCOPED_QUOTA", raising=False)
    quota = {"unit": "agent-run-1", "shared": False, "syscalls": 40, "limits": {}}
    seen = _run_guest(monkeypatch, "nodus-exec-eu", quota=quota, calls=3, rm=rm)
    assert seen["units"] == ["agent-run-1"] * 3, seen["units"]
    assert seen["syscalls_only"] is True, "the worker charged the run's wall budget (DEC-096)"
    assert seen["reply"]["quota_usage"] == {"unit": "agent-run-1", "syscalls": 3}, seen["reply"].get("quota_usage")
    assert rm.get_usage("agent-run-1").get("syscall_count", 0) == 0, (
        "the seeded snapshot stayed in the worker; a warm worker would carry one per run"
    )


def test_the_worker_refuses_at_the_runs_total_not_a_fresh_cap(monkeypatch, rm, enforcing):
    """Seeded at 99 of 100, the worker's check refuses after two more calls, not after 101."""
    monkeypatch.delenv("AINDY_RUN_SCOPED_QUOTA", raising=False)
    monkeypatch.setattr(rm_mod, "MAX_SYSCALLS_PER_EXECUTION", 100)
    rm.seed_usage("agent-run-2", syscalls=99)
    assert rm.check_quota("agent-run-2") == (True, None), "control: 99 of 100 is within the cap"
    rm.seed_usage("agent-run-2", syscalls=101)
    ok, reason = rm.check_quota("agent-run-2")
    assert not ok and "RESOURCE_LIMIT_EXCEEDED" in reason
    # a warm worker seeds the same unit again on its next execution: SET, never add
    rm.seed_usage("agent-run-2", syscalls=10)
    assert rm.check_quota("agent-run-2") == (True, None), "a re-seed added to the old count"


def test_with_a_shared_backend_the_worker_neither_seeds_nor_reports(monkeypatch, rm):
    """Redis already holds the run's count for every process; a reported delta would double it."""
    monkeypatch.delenv("AINDY_RUN_SCOPED_QUOTA", raising=False)
    seen = _run_guest(monkeypatch, "nodus-exec-eu", quota={"unit": "agent-run-3", "shared": True}, calls=2)
    assert seen["units"] == ["agent-run-3"] * 2
    assert "quota_usage" not in seen["reply"]


def test_the_parent_records_the_reported_syscalls_on_the_unit(rm):
    from AINDY.runtime.nodus_runtime_adapter import _apply_deferred_quota

    assert _apply_deferred_quota({"unit": "agent-run-4", "syscalls": 3}) == 3
    assert rm.get_usage("agent-run-4")["syscall_count"] == 3
    assert _apply_deferred_quota(None) == 0 and _apply_deferred_quota({"unit": "", "syscalls": 2}) == 0


def test_the_parent_hands_off_the_unit_it_is_charging(monkeypatch, rm):
    from types import SimpleNamespace

    from AINDY.kernel.syscall_dispatcher import bind_execution_unit
    from AINDY.runtime.nodus_runtime_adapter import _quota_handoff

    monkeypatch.delenv("AINDY_RUN_SCOPED_QUOTA", raising=False)
    rm.record_syscall("agent-run-5", 7)
    ctx = SimpleNamespace(execution_unit_id="nodus-exec-eu")
    with bind_execution_unit("agent-run-5", "t"):
        handoff = _quota_handoff(ctx)
    assert handoff["unit"] == "agent-run-5" and handoff["syscalls"] == 7 and handoff["shared"] is False
    assert _quota_handoff(ctx)["unit"] == "nodus-exec-eu", "unbound: the execution's own unit"
    monkeypatch.setenv("AINDY_RUN_SCOPED_QUOTA", "0")
    assert _quota_handoff(ctx) is None


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
        seen["syscalls_only"] = sd._SYSCALLS_ONLY_CTX.get()
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
    assert seen["syscalls_only"] is True, "the run must count its syscalls, not sum their wall time (DEC-096)"
    assert seen["limits"] == {"syscalls": 11, "tokens": 777}, "the row's ceilings moved onto the accounting key"
    assert sd._EU_ID_CTX.get() == "", "released with the span"


@pytest.mark.parametrize("syscalls_only, wall_charged", [(True, False), (False, True)])
def test_a_run_scoped_binding_counts_the_call_not_its_duration(rm, syscalls_only, wall_charged):
    """SYSMAX-4 (DEC-096): a run's syscall time summed against the 300 s wall cap would refuse
    a long LLM-heavy run mid-way. The run counts calls; a request binding still charges time."""
    import time

    from AINDY.kernel import syscall_registry as R

    name = f"sys.v1.test.slow_{uuid.uuid4().hex[:6]}"
    R.SYSCALL_REGISTRY[name] = R.SyscallEntry(handler=lambda p, c: time.sleep(0.03) or {"ok": True},
                                              capability="test.slow")
    try:
        d = sd.SyscallDispatcher()
        d._emit_syscall_event = lambda *a, **k: None
        ctx = R.SyscallContext(execution_unit_id="", user_id="u", capabilities=["test.slow"], trace_id="")
        with sd.bind_execution_unit("run-wall", "t", syscalls_only=syscalls_only):
            assert d.dispatch(name, {}, ctx)["status"] == "success"
            assert d.dispatch(name, {}, ctx)["status"] == "success"
    finally:
        R.SYSCALL_REGISTRY.pop(name, None)
    usage = rm.get_usage("run-wall")
    assert usage["syscall_count"] == 2
    assert (usage["wall_time_ms"] > 0) is wall_charged, usage
