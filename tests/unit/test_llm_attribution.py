"""COST-GOVERNOR-1 phase 3 — tokens become a resource dimension with a subject.

The meter (#563/#564) recorded tokens on a Prometheus counter labelled by provider and model,
deliberately NOT by tenant (cardinality), and said the per-tenant accounting "belongs in the
counter the governor will check — a cache, keyed and expiring". That store is
`kernel.resource_manager`, and since `QUOTA-ACCRUAL-ORPHAN-1` (#632) its units are reaped and
the pipeline binds one per request. This suite pins the wiring from meter to store:

* `observe_llm_usage` accrues tokens onto the attributed run, else the bound execution unit,
  and onto the tenant's rolling window whenever a tenant is known;
* every call is counted by what it could be attributed to — `run | unit | tenant | none` —
  because a budget can only refuse what it can attribute, and the `none` fraction is the number
  that says whether a cap would mean anything (`INITIATOR-IDENTITY-1`: allow, count separately);
* `generate_plan` declares the TENANT (planning runs before the `AgentRun` row exists — the
  finding that shaped the design), and `execute_run` declares (tenant, run) and lands the run's
  tokens on its SCORE_COMPUTED record.

Nothing here is enforced. Phase 4 — the ceiling — is gated on evidence the meter moves in a
real deployment, and this suite is not that evidence: it is the plumbing that evidence will
flow through.

Mutation-checked: drop `_attribute_usage(...)` from `observe_llm_usage` and six tests fail;
drop the `llm_attribution_scope` from `generate_plan` and the planning test fails; drop it from
`execute_run` and the run test fails; make `observed_unit` call `mark_started` and its control
fails.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

import pytest

from AINDY.kernel import resource_manager as rm_mod
from AINDY.kernel import syscall_dispatcher as sd
from AINDY.kernel.resource_manager import ResourceManager
from AINDY.platform_layer.token_meter import (
    current_llm_attribution,
    llm_attribution_scope,
    observe_llm_usage,
)

pytestmark = pytest.mark.runtime_only


def _response(prompt: int, completion: int):
    return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion))


def _calls(attributed: str, provider: str = "probe") -> float:
    from AINDY.platform_layer.metrics import REGISTRY

    value = REGISTRY.get_sample_value(
        "aindy_llm_calls_total", {"provider": provider, "attributed": attributed}
    )
    return float(value or 0.0)


@pytest.fixture
def rm(monkeypatch):
    fresh = ResourceManager()
    monkeypatch.setattr(rm_mod, "_RESOURCE_MANAGER", fresh)
    return fresh


# ── the scope itself ──────────────────────────────────────────────────────────


def test_attribution_scope_inherits_and_releases():
    assert current_llm_attribution() == (None, None)
    with llm_attribution_scope(tenant_id="t-1") as outer:
        assert outer == ("t-1", None)
        with llm_attribution_scope(run_id="r-9") as inner:
            assert inner == ("t-1", "r-9"), "an inner span adds a run without restating the tenant"
        assert current_llm_attribution() == ("t-1", None)
    assert current_llm_attribution() == (None, None)


# ── the meter → store wiring ──────────────────────────────────────────────────


def test_unattributed_call_is_allowed_and_counted_as_none(rm):
    before = _calls("none")
    observe_llm_usage(provider="probe", model="m", response=_response(10, 5))
    assert _calls("none") == before + 1
    assert rm._usage == {} and rm._tenant_tokens == {}, "nothing to accrue onto — and nothing invented"


def test_tenant_only_attribution_accrues_the_tenant_window(rm):
    before = _calls("tenant")
    with llm_attribution_scope(tenant_id="tenant-a"):
        observe_llm_usage(provider="probe", model="m", response=_response(100, 20))
        observe_llm_usage(provider="probe", model="m", response=_response(1, 1))
    assert rm.get_tenant_tokens("tenant-a") == 122
    assert rm._usage == {}, "no run and no bound unit: nothing per-unit to accrue onto"
    assert _calls("tenant") == before + 2


def test_run_attribution_accrues_on_the_run_and_the_tenant(rm):
    before = _calls("run")
    with llm_attribution_scope(tenant_id="tenant-a", run_id="run-1"):
        observe_llm_usage(provider="probe", model="m", response=_response(30, 12))
    usage = rm.get_usage("run-1")
    assert usage["tokens"] == 42 and usage["tenant_id"] == "tenant-a"
    assert rm.get_tenant_tokens("tenant-a") == 42
    assert _calls("run") == before + 1


def test_bound_unit_attribution_uses_the_units_tenant(rm):
    """A pipeline-bound request: the unit was mark_started under its tenant, no scope declared."""
    unit = str(uuid.uuid4())
    rm.mark_started("tenant-b", unit)
    before = _calls("unit")
    tok_t = sd._TRACE_ID_CTX.set(unit)
    tok_e = sd._EU_ID_CTX.set(unit)
    try:
        observe_llm_usage(provider="probe", model="m", response=_response(7, 3))
    finally:
        sd._EU_ID_CTX.reset(tok_e)
        sd._TRACE_ID_CTX.reset(tok_t)
    assert rm.get_usage(unit)["tokens"] == 10
    assert rm.get_tenant_tokens("tenant-b") == 10, "tenant derived from the unit's snapshot"
    assert _calls("unit") == before + 1


def test_run_takes_precedence_over_a_bound_unit(rm):
    unit = str(uuid.uuid4())
    tok_t = sd._TRACE_ID_CTX.set(unit)
    tok_e = sd._EU_ID_CTX.set(unit)
    try:
        with llm_attribution_scope(tenant_id="tenant-c", run_id="run-2"):
            observe_llm_usage(provider="probe", model="m", response=_response(5, 5))
    finally:
        sd._EU_ID_CTX.reset(tok_e)
        sd._TRACE_ID_CTX.reset(tok_t)
    assert rm.get_usage("run-2")["tokens"] == 10
    assert unit not in rm._usage, "the run is the subject; the enclosing unit is not double-charged"


def test_unreadable_usage_accrues_nothing(rm):
    with llm_attribution_scope(tenant_id="tenant-d", run_id="run-3"):
        observe_llm_usage(provider="probe", model="m", response=SimpleNamespace())
    assert "run-3" not in rm._usage and rm.get_tenant_tokens("tenant-d") == 0


def test_accounting_failure_never_fails_the_call(rm, monkeypatch):
    monkeypatch.setattr(rm, "record_tokens", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("store down")))
    with llm_attribution_scope(tenant_id="tenant-e", run_id="run-4"):
        observe_llm_usage(provider="probe", model="m", response=_response(1, 1))  # must not raise


# ── the store's token dimension ───────────────────────────────────────────────


def test_tokens_are_a_snapshot_dimension_reaped_with_the_unit(rm):
    rm.record_usage("eu-t", {"syscall_count": 1, "tokens": 40})
    rm.record_tokens("eu-t", 2)
    assert rm.get_usage("eu-t")["tokens"] == 42
    rm.purge_eu("eu-t")
    assert rm.get_usage("eu-t")["tokens"] == 0


def test_tenant_window_is_summarised_and_expires_like_the_redis_key(rm):
    from AINDY.config import Settings

    rm.record_tenant_tokens("tenant-w", 500)
    assert rm.get_tenant_summary("tenant-w")["window_tokens"] == 500
    # Age the window past TENANT_KEY_TTL_SECONDS and force the sweep clock.
    count, _ = rm._tenant_tokens["tenant-w"]
    stale = rm._last_eviction_sweep - rm_mod.TENANT_KEY_TTL_SECONDS - 1
    rm._tenant_tokens["tenant-w"] = (count, stale)
    rm._last_eviction_sweep = stale
    with patch.object(Settings, "is_testing", new_callable=PropertyMock, return_value=False):
        rm.can_execute("anyone")
    assert rm.get_tenant_tokens("tenant-w") == 0, "the window expired silently, by design"


def test_observed_unit_gives_tokens_a_subject_without_admitting_it(rm):
    """Control against `owned_execution`: the same block must NOT touch the tenant's active count."""
    with rm.observed_unit("tenant-o", "run-o") as eu:
        assert rm.get_tenant_active("tenant-o") == 0, "observed, not admitted"
        rm.record_tokens(eu, 9)
        assert rm.get_usage(eu) == {
            "eu_id": "run-o", "tenant_id": "tenant-o", "wall_time_ms": 0,
            "memory_bytes": 0, "syscall_count": 0, "tokens": 9,
        }
    assert "run-o" not in rm._usage, "purged on exit"
    with rm.owned_execution("tenant-o") as eu:
        assert rm.get_tenant_active("tenant-o") == 1, "liveness: the admitting scope does count"


# ── the two runtime call sites ────────────────────────────────────────────────


def test_generate_plan_declares_the_tenant_around_the_backend(monkeypatch):
    from AINDY.agents.agent_runtime import planning

    seen: dict = {}

    def _backend(**kwargs):
        seen["attribution"] = current_llm_attribution()
        return {"steps": [], "overall_risk": "low", "executive_summary": "x"}

    compat = SimpleNamespace(
        _resolve_objective=lambda objective, values: objective,
        _get_planner_context=lambda run_type, user_id, db: {"system_prompt": "plan"},
        _get_tools_for_run=lambda run_type, user_id, db: [],
        _plan_failure=SimpleNamespace(reason=None, error=None),
    )
    monkeypatch.setattr(planning, "get_runtime_compat_module", lambda: compat)
    monkeypatch.setattr(planning, "_resolve_planner_backend_name", lambda ctx: ("probe", "test"))
    monkeypatch.setattr(planning, "_recall_planner_memory", lambda *a, **k: ("", []))
    monkeypatch.setattr(planning, "_build_planner_prompt", lambda **k: "plan")
    monkeypatch.setattr(planning, "_invoke_planner_backend", _backend)

    plan = planning.generate_plan(objective="probe", user_id="tenant-p", db=object())

    assert plan is not None
    assert seen["attribution"] == ("tenant-p", None), (
        "planning runs before the AgentRun row exists — the tenant is the only identity it can carry"
    )
    assert current_llm_attribution() == (None, None), "released after the backend returned"


def test_execute_run_declares_tenant_and_run_and_records_tokens_on_the_score(db_session, monkeypatch, rm):
    """Drives the real execute_run down to its execution span with the coordinator forced local."""
    from AINDY.agents.agent_runtime import execution
    from AINDY.db.models import AgentRun

    run = AgentRun(
        user_id=uuid.uuid4(), goal="spend tokens", plan={"steps": []}, executive_summary="x",
        overall_risk="low", status="approved", steps_total=0, correlation_id=f"run_{uuid.uuid4()}",
        trace_id="trace-x", capability_token={"allowed_capabilities": []},
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    seen: dict = {}

    def _fake_execution(**kwargs):
        seen["attribution"] = current_llm_attribution()
        seen["active_during"] = rm.get_tenant_active(str(run.user_id))
        # The LLM calls the run makes, as the meter would see them.
        observe_llm_usage(provider="probe", model="m", response=_response(200, 50))
        observe_llm_usage(provider="probe", model="m", response=_response(20, 5))
        r = db_session.query(AgentRun).filter(AgentRun.id == run.id).first()
        r.status = "completed"
        db_session.commit()

    def _fake_score(**kwargs):
        seen["score_dimensions"] = kwargs["dimensions"]

    monkeypatch.setattr(execution, "register_or_update_agent", lambda *a, **k: {})
    monkeypatch.setattr(execution, "decide_execution_mode", lambda *a, **k: {"mode": "local", "selected_agent": None, "candidates": []})
    monkeypatch.setattr(execution, "record_agent_event", lambda *a, **k: "event-1")
    monkeypatch.setattr("AINDY.runtime.nodus_execution_service.execute_agent_run_via_nodus", _fake_execution)
    monkeypatch.setattr(execution, "_emit_agent_next_action", lambda *a, **k: None)
    monkeypatch.setattr("AINDY.core.execution_score.emit_execution_score", _fake_score)

    result = execution.execute_run(run_id=str(run.id), user_id=str(run.user_id), db=db_session)

    assert result is not None and result["status"] == "completed", result
    assert seen["attribution"] == (str(run.user_id), str(run.id))
    assert seen["active_during"] == 0, "observed, not admitted — no concurrency side effect"
    assert seen["score_dimensions"]["llm_tokens"] == 275, "per-run spend is durable on the score record"
    assert str(run.id) not in rm._usage, "the run's snapshot is purged with the span"
    assert rm.get_tenant_tokens(str(run.user_id)) == 275
    assert current_llm_attribution() == (None, None)
