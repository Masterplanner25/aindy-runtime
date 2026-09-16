"""COST-GOVERNOR-1 phase 4 — the governor: reserve → call → reconcile, refusing on breach.

Phase 2 proved the meter moves on a real deployment (one planner call, 2 244 tokens); phase 3
gave every call a subject and accrued the actual into `ResourceManager`. This is the half that
refuses, and these tests pin the four properties that make it a governor rather than a check:

* **admission and accounting are one operation** — a reservation pre-fills the counter it is
  checked against, so N concurrent callers cannot all pass (the thread test drives real
  threads against one in-memory store and counts admissions);
* **the estimate never becomes the record** — after the call the counter holds the ACTUAL the
  meter recorded, not the estimate; a call that raises leaves nothing behind;
* **a refusal is not a provider failure** — it is raised outside the circuit breaker and the
  breaker's failure count does not move;
* **it is opt-in and fails the way the other quotas fail** — both ceilings default to 0
  (unlimited); a store failure admits in dev/test and refuses in prod.

And the constraint from phase 3: planning has no run id, so the tenant window is what catches
a runaway planner — the last test drives the real `generate_plan` into a refusal.

Mutation-checked: make `reserve_tokens` always return True and three tests fail; drop the
`release_*` calls from the reservation's `finally` and the reconcile test fails; move the
reservation inside the breaker call and the breaker test fails; invert `_may_fail_open` and
the fail-closed test fails.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

import pytest

from AINDY.kernel import resource_manager as rm_mod
from AINDY.kernel.resource_manager import ResourceManager
from AINDY.platform_layer.llm_budget import llm_budget_reservation
from AINDY.platform_layer.llm_client import (
    CircuitBreakerLLMClient,
    LLMBudgetExceededError,
    LLMCallError,
)
from AINDY.platform_layer.token_meter import llm_attribution_scope, observe_llm_usage

pytestmark = pytest.mark.runtime_only


def _response(prompt: int, completion: int):
    return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion))


def _outcome(scope: str, outcome: str) -> float:
    from AINDY.platform_layer.metrics import REGISTRY

    value = REGISTRY.get_sample_value(
        "aindy_llm_budget_outcomes_total", {"scope": scope, "outcome": outcome}
    )
    return float(value or 0.0)


@pytest.fixture
def rm(monkeypatch):
    fresh = ResourceManager()
    monkeypatch.setattr(rm_mod, "_RESOURCE_MANAGER", fresh)
    return fresh


@pytest.fixture
def caps(monkeypatch):
    """Set the two ceilings for a test. Read per call, so a monkeypatch is enough."""

    def _set(execution: int = 0, tenant: int = 0):
        monkeypatch.setattr(rm_mod, "MAX_TOKENS_PER_EXECUTION", execution)
        monkeypatch.setattr(rm_mod, "MAX_TOKENS_PER_TENANT_WINDOW", tenant)

    return _set


class _FakeClient:
    """Satisfies LLMClient; `chat` meters like a real raw path would."""

    def __init__(self, prompt: int = 100, completion: int = 50, fail: Exception | None = None):
        self.calls = 0
        self._usage = (prompt, completion)
        self._fail = fail

    def chat(self, messages, model=None, temperature=0.7, max_tokens=None) -> str:
        self.calls += 1
        if self._fail is not None:
            raise self._fail
        observe_llm_usage(provider="probe", model="m", response=_response(*self._usage))
        return "ok"

    def is_available(self) -> bool:
        return True


# ── the reservation primitive ─────────────────────────────────────────────────


def test_reserve_admits_within_cap_and_refuses_over_it(rm):
    assert rm.reserve_tokens("eu-1", 600, 1000, tenant_id="t") is True
    assert rm.reserve_tokens("eu-1", 500, 1000) is False, "600 + 500 > 1000"
    assert rm.get_usage("eu-1")["tokens"] == 600, "a refused reservation leaves nothing behind"
    rm.release_tokens("eu-1", 600)
    assert rm.get_usage("eu-1")["tokens"] == 0
    assert rm.reserve_tenant_tokens("t", 900, 1000) and not rm.reserve_tenant_tokens("t", 200, 1000)
    assert rm.get_tenant_tokens("t") == 900


def test_concurrent_reservations_cannot_all_pass(rm):
    """The property a read-then-compare lacks: 16 threads, cap for exactly 4 admissions."""
    admitted = []
    barrier = threading.Barrier(16)

    def _worker():
        barrier.wait()
        admitted.append(rm.reserve_tokens("eu-c", 250, 1000))

    threads = [threading.Thread(target=_worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(admitted) == 4, f"exactly cap/reserve admissions expected, got {sum(admitted)}"
    assert rm.get_usage("eu-c")["tokens"] == 1000


# ── the governor at the seam ──────────────────────────────────────────────────


def test_unlimited_by_default_reserves_nothing(rm, caps, monkeypatch):
    caps(0, 0)
    spy = {"reserve": 0}
    monkeypatch.setattr(rm, "reserve_tokens", lambda *a, **k: spy.__setitem__("reserve", spy["reserve"] + 1) or True)
    with llm_attribution_scope(tenant_id="t", run_id="r"):
        with llm_budget_reservation(provider="probe", kwargs={"max_tokens": 10}):
            pass
    assert spy["reserve"] == 0, "opt-in: no ceiling configured means the governor is inert"


def test_unattributed_call_is_admitted_without_reservation(rm, caps):
    caps(100, 100)
    with llm_budget_reservation(provider="probe", kwargs={"max_tokens": 10_000}):
        pass  # no subject → nothing to charge → admitted (INITIATOR-IDENTITY-1)
    assert rm._usage == {} and rm._tenant_tokens == {}


def test_execution_ceiling_refuses_before_the_call(rm, caps):
    caps(execution=500)
    before = _outcome("execution", "refused")
    with llm_attribution_scope(tenant_id="t", run_id="run-x"):
        rm.record_tokens("run-x", 400, tenant_id="t")  # already spent this run
        with pytest.raises(LLMBudgetExceededError) as exc_info:
            with llm_budget_reservation(provider="probe", kwargs={"max_tokens": 200}):
                pytest.fail("the call must not run")
    exc = exc_info.value
    assert isinstance(exc, LLMCallError), "one error contract for every consumer"
    assert str(exc).startswith("RESOURCE_LIMIT_EXCEEDED") and exc.scope == "execution"
    assert exc.used == 400 and exc.cap == 500 and exc.reserved >= 200
    assert rm.get_usage("run-x")["tokens"] == 400, "a refusal leaves the counter untouched"
    assert _outcome("execution", "refused") == before + 1


def test_tenant_window_refuses_a_call_with_no_run(rm, caps):
    """Planning's shape: tenant known, no run — the run ceiling cannot help, the window can."""
    caps(execution=10, tenant=1000)
    rm.record_tenant_tokens("tenant-p", 950)
    with llm_attribution_scope(tenant_id="tenant-p"):
        with pytest.raises(LLMBudgetExceededError) as exc_info:
            with llm_budget_reservation(provider="probe", kwargs={"max_tokens": 100}):
                pytest.fail("the call must not run")
    assert exc_info.value.scope == "tenant" and exc_info.value.subject == "tenant-p"
    assert rm.get_tenant_tokens("tenant-p") == 950


def test_reconcile_replaces_the_estimate_with_the_actual(rm, caps):
    caps(execution=10_000, tenant=10_000)
    with llm_attribution_scope(tenant_id="t", run_id="run-r"):
        with llm_budget_reservation(provider="probe", kwargs={"max_tokens": 4000}):
            during = rm.get_usage("run-r")["tokens"]
            observe_llm_usage(provider="probe", model="m", response=_response(120, 30))
    assert during >= 4000, "inside the call the estimate is held against the budget"
    assert rm.get_usage("run-r")["tokens"] == 150, "after it, only the actual remains"
    assert rm.get_tenant_tokens("t") == 150


def test_a_failed_call_releases_its_reservation(rm, caps):
    caps(execution=10_000)
    with llm_attribution_scope(tenant_id="t", run_id="run-f"):
        with pytest.raises(RuntimeError, match="provider down"):
            with llm_budget_reservation(provider="probe", kwargs={"max_tokens": 4000}):
                raise RuntimeError("provider down")
    assert rm.get_usage("run-f")["tokens"] == 0, "the call never happened; nothing is charged"


def test_tenant_refusal_releases_an_already_held_execution_reservation(rm, caps):
    caps(execution=10_000, tenant=100)
    with llm_attribution_scope(tenant_id="t-small", run_id="run-h"):
        with pytest.raises(LLMBudgetExceededError):
            with llm_budget_reservation(provider="probe", kwargs={"max_tokens": 500}):
                pytest.fail("must not run")
    assert rm.get_usage("run-h")["tokens"] == 0, "the execution reservation was released on the tenant refusal"


def test_store_failure_admits_in_test_mode_and_refuses_in_prod(rm, caps, monkeypatch):
    caps(execution=1000)
    monkeypatch.setattr(rm, "reserve_tokens", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("redis gone")))
    before = _outcome("execution", "degraded")
    with llm_attribution_scope(tenant_id="t", run_id="run-d"):
        with llm_budget_reservation(provider="probe"):
            pass  # test mode: fail-open, counted
    assert _outcome("execution", "degraded") == before + 1

    # Prod: drive the REAL policy (`_quota_backend_failure_may_fail_open`), not a stub of it —
    # `is_testing`/`is_dev` are pydantic properties, so they are patched on the class.
    from AINDY.config import Settings

    with patch.object(Settings, "is_testing", new_callable=PropertyMock, return_value=False),          patch.object(Settings, "is_dev", new_callable=PropertyMock, return_value=False):
        with llm_attribution_scope(tenant_id="t", run_id="run-d"):
            with pytest.raises(LLMBudgetExceededError, match="fail-closed"):
                with llm_budget_reservation(provider="probe"):
                    pytest.fail("prod: a governor that cannot read its store refuses")


# ── through the seam ──────────────────────────────────────────────────────────


def test_seam_refusal_does_not_count_against_the_circuit_breaker(rm, caps):
    caps(execution=100)
    client = _FakeClient()
    wrapped = CircuitBreakerLLMClient(client, provider="probe")
    failures_before = wrapped.breaker.failure_count
    with llm_attribution_scope(tenant_id="t", run_id="run-b"):
        with pytest.raises(LLMBudgetExceededError):
            wrapped.chat([{"role": "user", "content": "x"}], max_tokens=500)
    assert client.calls == 0, "refused BEFORE the provider was called"
    assert wrapped.breaker.failure_count == failures_before, "a budget refusal is not a provider failure"


def test_seam_admits_and_reconciles_a_real_call(rm, caps):
    caps(execution=10_000, tenant=10_000)
    client = _FakeClient(prompt=100, completion=50)
    wrapped = CircuitBreakerLLMClient(client, provider="probe")
    with llm_attribution_scope(tenant_id="t", run_id="run-ok"):
        assert wrapped.chat([{"role": "user", "content": "x"}], max_tokens=200) == "ok"
    assert client.calls == 1
    assert rm.get_usage("run-ok")["tokens"] == 150 and rm.get_tenant_tokens("t") == 150


def test_seam_call_method_is_reserved_exactly_once(rm, caps, monkeypatch):
    caps(execution=10_000)
    class _Anthropicish(_FakeClient):
        def messages_create(self, **kw):  # governed: its NAME is in METERED_METHODS
            observe_llm_usage(provider="probe", model="m", response=_response(10, 5))
            return {"ok": True}

    client = _Anthropicish()
    wrapped = CircuitBreakerLLMClient(client, provider="probe")
    reservations = []
    real = rm.reserve_tokens
    monkeypatch.setattr(rm, "reserve_tokens", lambda *a, **k: reservations.append(a) or real(*a, **k))
    with llm_attribution_scope(tenant_id="t", run_id="run-m"):
        wrapped.call_method("messages_create", max_tokens=100)
    assert len(reservations) == 1


def test_generate_plan_is_refused_by_the_tenant_window(rm, caps, monkeypatch):
    """The runaway-planner backstop, end to end: tenant over budget → the planner never runs."""
    from AINDY.agents.agent_runtime import planning

    caps(execution=0, tenant=1000)
    rm.record_tenant_tokens("tenant-over", 990)
    planner_ran = {"n": 0}

    def _backend(**kwargs):
        planner_ran["n"] += 1
        wrapped = CircuitBreakerLLMClient(_FakeClient(), provider="probe")
        wrapped.chat([{"role": "user", "content": kwargs["objective_text"]}], max_tokens=300)
        return {"steps": [], "overall_risk": "low", "executive_summary": "x"}

    compat = SimpleNamespace(
        _resolve_objective=lambda objective, values: objective,
        _get_planner_context=lambda run_type, user_id, db: {"system_prompt": "plan"},
        _get_tools_for_run=lambda run_type, user_id, db: [],
        _plan_failure=SimpleNamespace(reason=None),
    )
    monkeypatch.setattr(planning, "get_runtime_compat_module", lambda: compat)
    monkeypatch.setattr(planning, "_resolve_planner_backend_name", lambda ctx: ("probe", "test"))
    monkeypatch.setattr(planning, "_recall_planner_memory", lambda *a, **k: ("", []))
    monkeypatch.setattr(planning, "_build_planner_prompt", lambda **k: "plan")
    monkeypatch.setattr(planning, "_invoke_planner_backend", _backend)

    plan = planning.generate_plan(objective="loop forever", user_id="tenant-over", db=object())

    assert plan is None, "the planner call was refused, so no plan"
    assert planner_ran["n"] == 1, "the backend was entered; the SEAM refused inside it"
    assert rm.get_tenant_tokens("tenant-over") == 990, "and charged nothing"
    # create_run reads this into its agent_plan_generation error event: the operator sees WHY.
    assert "RESOURCE_LIMIT_EXCEEDED" in compat._plan_failure.reason
    assert "tenant-over" in compat._plan_failure.reason


# ── found live, 2026-09-13: the two things the unit tests could not see ──────


def test_governor_reserves_only_for_metered_methods(rm, caps):
    """An embedding goes through the same seam and is never metered; reserving for it held ~2k
    tokens against the budget during the call and counted `reserved` twice per planner call."""
    caps(execution=100)  # tiny: any reservation would refuse

    class _Embedder(_FakeClient):
        def create_embedding_response(self, **kw):
            return {"data": [[0.0]]}

    wrapped = CircuitBreakerLLMClient(_Embedder(), provider="probe")
    before = _outcome("execution", "reserved")
    with llm_attribution_scope(tenant_id="t", run_id="run-e"):
        assert wrapped.call_method("create_embedding_response", input="x") == {"data": [[0.0]]}
    assert _outcome("execution", "reserved") == before, "an embedding is not a token-spending call"
    assert rm.get_usage("run-e")["tokens"] == 0


def test_metered_methods_constant_matches_the_provider_clients():
    """★ Variant 12: the constant the governor reserves against is compared AGAINST a census
    derived from source — every provider method that meters (since OTEL-GENAI-SEMCONV-1: calls
    `op.record(...)` inside `with llm_operation(...)`, which IS `observe_llm_usage`) must be in
    it, and every raw entry in it must exist in some client — never used AS the census."""
    import ast
    from pathlib import Path

    from AINDY.platform_layer.token_meter import METERED_METHODS

    metering_methods: set[str] = set()
    all_methods: set[str] = set()
    for path in sorted(Path("AINDY/platform_layer").glob("*_client.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            all_methods.add(node.name)
            calls = {
                c.func.id for c in ast.walk(node)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            }
            records = any(
                isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "record"
                for c in ast.walk(node)
            )
            if "observe_llm_usage" in calls or records:
                metering_methods.add(node.name)

    assert metering_methods, "liveness: no provider client meters anything — the census is broken"
    assert metering_methods <= METERED_METHODS, (
        f"a provider meters {metering_methods - METERED_METHODS} but the governor would not "
        "reserve for it — the meter and the governor disagree about what spends tokens"
    )
    assert (METERED_METHODS - {"chat"}) <= all_methods, "a governed raw method exists in no client"


def test_route_answers_a_budget_refusal_with_429_not_500(runtime_only_app, monkeypatch, rm, caps):
    """ROUTE-GUARD-1: a route test must call the route. Found live as a 500 `http_error`."""
    import uuid

    from fastapi.testclient import TestClient

    from AINDY.agents import runtime_api
    from AINDY.agents.agent_runtime import planning
    from AINDY.services.auth_service import get_current_user

    uid = str(uuid.uuid4())
    caps(execution=0, tenant=1000)
    rm.record_tenant_tokens(uid, 990)

    class _AppPlannerError(RuntimeError):
        """The app's planner rewraps seam errors (`AnthropicPlannerError(detail) from exc`)."""

    def _backend(**kwargs):
        wrapped = CircuitBreakerLLMClient(_FakeClient(), provider="probe")
        try:
            wrapped.chat([{"role": "user", "content": kwargs["objective_text"]}], max_tokens=300)
        except LLMCallError as exc:
            raise _AppPlannerError(f"planner call failed: {exc}") from exc
        return {"steps": [], "overall_risk": "low", "executive_summary": "x"}

    monkeypatch.setattr(planning, "_resolve_planner_backend_name", lambda ctx: ("probe", "test"))
    monkeypatch.setattr(planning, "_recall_planner_memory", lambda *a, **k: ("", []))
    monkeypatch.setattr(planning, "_build_planner_prompt", lambda **k: "plan")
    monkeypatch.setattr(planning, "_invoke_planner_backend", _backend)
    monkeypatch.setattr(runtime_api, "async_heavy_execution_enabled", lambda: False)
    monkeypatch.setattr(runtime_api, "_decision_or_defer_response", lambda **kwargs: None)
    compat = planning.get_runtime_compat_module()
    monkeypatch.setattr(compat, "_get_planner_context", lambda run_type, user_id, db: {"system_prompt": "plan"})
    monkeypatch.setattr(compat, "_get_tools_for_run", lambda run_type, user_id, db: [])
    monkeypatch.setattr(compat, "emit_error_event", lambda **kwargs: None)

    from AINDY.routes.agent_router import router as _agent_router

    runtime_only_app.include_router(_agent_router, prefix="/apps")
    runtime_only_app.dependency_overrides[get_current_user] = lambda: {"sub": uid, "user_id": uid, "auth_type": "jwt"}
    with TestClient(runtime_only_app, raise_server_exceptions=False) as client:
        response = client.post("/apps/agent/run", json={"goal": "loop forever"})

    assert response.status_code == 429, response.text
    assert "RESOURCE_LIMIT_EXCEEDED" in response.text and uid in response.text


def test_find_budget_refusal_walks_the_cause_chain():
    from AINDY.platform_layer.llm_client import find_budget_refusal

    inner = LLMBudgetExceededError("RESOURCE_LIMIT_EXCEEDED: x", scope="tenant", subject="t", used=1, reserved=1, cap=1)
    try:
        try:
            raise inner
        except LLMBudgetExceededError as exc:
            raise ValueError("app rewrap") from exc
    except ValueError as outer:
        assert find_budget_refusal(outer) is inner
    assert find_budget_refusal(ValueError("unrelated")) is None
    assert find_budget_refusal(None) is None
