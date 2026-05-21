from __future__ import annotations

import uuid
from collections import defaultdict

import pytest

from AINDY.agents.agent_runtime.planning import generate_plan
from AINDY.config import settings
from AINDY.db.models import AgentRun, AgentTrustSettings
from AINDY.platform_layer import registry


pytestmark = pytest.mark.runtime_only


_REGISTRY_STATE_EMPTY = {
    "_loaded_plugins": set(),
    "_registered_apps": [],
    "_bootstrap_dependencies": {},
    "_loaded_extension_records": {},
    "_bootstrap_registrations": {},
    "_active_plugin_profile": None,
    "_active_plugin_profile_source": None,
    "_routers": [],
    "_root_routers": [],
    "_legacy_root_routers": [],
    "_syscalls": {},
    "_jobs": {},
    "_flows": [],
    "_flow_result_keys": {},
    "_flow_result_extractors": {},
    "_flow_completion_events": {},
    "_flow_plans": {},
    "_event_handlers": defaultdict(list),
    "_event_types": set(),
    "_capture_rules": {},
    "_memory_policies": {},
    "_scheduled_jobs": {},
    "_response_adapters": {},
    "_route_guards": {},
    "_execution_adapters": {},
    "_startup_hooks": [],
    "_agent_tools": {},
    "_agent_planner_contexts": {},
    "_agent_planner_backends": {},
    "_agent_run_tools": {},
    "_agent_completion_hooks": defaultdict(list),
    "_agent_event_emitters": defaultdict(list),
    "_agent_ranking_strategy": None,
    "_trigger_evaluators": {},
    "_flow_strategies": {},
    "_capability_definitions": {},
    "_capability_definition_providers": [],
    "_tool_capabilities": {},
    "_agent_capabilities": {},
    "_restricted_tools": set(),
    "_symbols": {},
    "_core_domains": [],
    "_degraded_domains": [],
    "_health_checks": {},
    "_runtime_agent_defaults_loaded": False,
}


def _copy_registry_value(value):
    if isinstance(value, defaultdict):
        copied = defaultdict(value.default_factory)
        for key, item in value.items():
            copied[key] = list(item) if isinstance(item, list) else item
        return copied
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    if isinstance(value, set):
        return set(value)
    return value


@pytest.fixture
def clean_agent_planner_registry(monkeypatch):
    snapshot = {
        name: _copy_registry_value(getattr(registry, name))
        for name in _REGISTRY_STATE_EMPTY
    }
    original_backend = settings.AINDY_AGENT_PLANNER_BACKEND
    original_model = settings.AINDY_AGENT_PLANNER_MODEL
    original_temp = settings.AINDY_AGENT_PLANNER_TEMPERATURE
    try:
        for name, value in _REGISTRY_STATE_EMPTY.items():
            setattr(registry, name, _copy_registry_value(value))
        monkeypatch.setattr(settings, "AINDY_AGENT_PLANNER_BACKEND", "runtime_local")
        monkeypatch.setattr(settings, "AINDY_AGENT_PLANNER_MODEL", "gpt-4o")
        monkeypatch.setattr(settings, "AINDY_AGENT_PLANNER_TEMPERATURE", 0.3)
        yield
    finally:
        for name, value in snapshot.items():
            setattr(registry, name, value)
        settings.AINDY_AGENT_PLANNER_BACKEND = original_backend
        settings.AINDY_AGENT_PLANNER_MODEL = original_model
        settings.AINDY_AGENT_PLANNER_TEMPERATURE = original_temp


def _sample_plan(*, tool_name: str = "memory.recall") -> dict:
    return {
        "executive_summary": "Retrieve context and respond.",
        "steps": [
            {
                "tool": tool_name,
                "args": {"query": "alpha"},
                "risk_level": "low",
                "description": "Recall relevant memory.",
            }
        ],
        "overall_risk": "low",
    }


def test_generate_plan_uses_backend_selected_by_runtime_configuration(
    clean_agent_planner_registry,
    monkeypatch,
):
    captured = {}

    def backend(request):
        captured["backend"] = request
        return _sample_plan()

    registry.register_agent_planner_backend("test_backend", backend)
    monkeypatch.setattr(settings, "AINDY_AGENT_PLANNER_BACKEND", "test_backend")

    plan = generate_plan(objective="Find the relevant memory", user_id="user-1", db=object())

    assert plan is not None
    assert plan["overall_risk"] == "low"
    assert captured["backend"].objective == "Find the relevant memory"
    assert "Available tools:" in captured["backend"].system_prompt


def test_generate_plan_can_follow_provider_selected_backend_hint(
    clean_agent_planner_registry,
    monkeypatch,
):
    captured = {}

    def hinted_context(_context):
        return {
            "system_prompt": "Planner prompt.",
            "context_block": "",
            "planner_backend": "provider_backend",
        }

    def provider_backend(request):
        captured["backend"] = request
        return _sample_plan(tool_name="memory.write")

    registry.register_planner_context_provider("default", hinted_context)
    registry.register_agent_planner_backend("provider_backend", provider_backend)
    monkeypatch.setattr(settings, "AINDY_AGENT_PLANNER_BACKEND", "")

    plan = generate_plan(objective="Write a note", user_id="user-1", db=object())

    assert plan is not None
    assert plan["steps"][0]["tool"] == "memory.write"
    assert captured["backend"].metadata["planner_backend"] == "provider_backend"


def test_generate_plan_fails_clearly_when_backend_is_not_registered(
    clean_agent_planner_registry,
    monkeypatch,
):
    import AINDY.agents.agent_runtime as compat

    monkeypatch.setattr(settings, "AINDY_AGENT_PLANNER_BACKEND", "missing_backend")

    plan = generate_plan(objective="Do something", user_id="user-1", db=object())

    assert plan is None
    assert "missing_backend" in getattr(compat._plan_failure, "reason", "")
    assert "not registered" in getattr(compat._plan_failure, "reason", "")


def test_generate_plan_disabled_backend_is_deterministic(
    clean_agent_planner_registry,
    monkeypatch,
):
    import AINDY.agents.agent_runtime as compat

    monkeypatch.setattr(settings, "AINDY_AGENT_PLANNER_BACKEND", "disabled")

    plan = generate_plan(objective="Do nothing", user_id="user-1", db=object())

    assert plan is None
    assert "disabled" in getattr(compat._plan_failure, "reason", "").lower()


def test_runtime_local_backend_generates_valid_plan_without_external_provider(
    clean_agent_planner_registry,
):
    plan = generate_plan(
        objective="Recall prior context for the release task",
        user_id="user-1",
        db=object(),
    )

    assert plan is not None
    assert isinstance(plan["executive_summary"], str)
    assert plan["steps"]
    assert plan["steps"][0]["tool"] == "memory.recall"
    assert plan["steps"][0]["args"]["query"] == "Recall prior context for the release task"
    assert plan["overall_risk"] == "low"


def test_runtime_local_backend_can_select_memory_write_when_objective_implies_persistence(
    clean_agent_planner_registry,
):
    plan = generate_plan(
        objective="Write a memory note about the customer follow-up",
        user_id="user-1",
        db=object(),
    )

    assert plan is not None
    assert plan["steps"][0]["tool"] == "memory.write"
    assert plan["steps"][0]["args"]["content"] == "Write a memory note about the customer follow-up"


def test_openai_compat_backend_remains_available_as_explicit_adapter(
    clean_agent_planner_registry,
    monkeypatch,
):
    captured = {}

    def fake_external_call(*, service_name, db, user_id, endpoint, model, method, extra, operation):
        captured.update(
            {
                "service_name": service_name,
                "endpoint": endpoint,
                "model": model,
                "method": method,
                "extra": extra,
            }
        )

        class _Message:
            content = '{"executive_summary":"Use OpenAI adapter.","steps":[{"tool":"memory.recall","args":{"query":"alpha"},"risk_level":"low","description":"Recall memory."}],"overall_risk":"low"}'

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]

        return _Response()

    monkeypatch.setattr(settings, "AINDY_AGENT_PLANNER_BACKEND", "openai_chat_compat")
    monkeypatch.setattr(
        "AINDY.agents.agent_runtime.planner_backends.perform_external_call",
        fake_external_call,
    )
    monkeypatch.setattr(
        "AINDY.agents.agent_runtime.planner_backends.get_openai_client",
        lambda: object(),
    )
    monkeypatch.setattr(
        "AINDY.agents.agent_runtime.planner_backends.chat_completion",
        lambda *args, **kwargs: object(),
    )

    plan = generate_plan(objective="Recall alpha", user_id="user-1", db=object())

    assert plan is not None
    assert plan["steps"][0]["tool"] == "memory.recall"
    assert captured["service_name"] == "openai"
    assert captured["extra"]["planner_backend"] == "openai_chat_compat"


def test_create_agent_run_runtime_still_uses_new_planning_boundary(
    clean_agent_planner_registry,
    db_session,
    monkeypatch,
):
    from AINDY.agents import runtime_api

    user_id = uuid.uuid4()

    def backend(request):
        return _sample_plan()

    registry.register_agent_planner_backend("test_backend", backend)
    monkeypatch.setattr(settings, "AINDY_AGENT_PLANNER_BACKEND", "test_backend")
    db_session.add(
        AgentTrustSettings(
            user_id=user_id,
            auto_execute_low=True,
            auto_execute_medium=False,
        )
    )
    db_session.commit()

    monkeypatch.setattr(runtime_api, "async_heavy_execution_enabled", lambda: False)
    monkeypatch.setattr(runtime_api, "_decision_or_defer_response", lambda **kwargs: None)
    monkeypatch.setattr(
        runtime_api,
        "execute_run",
        lambda *, run_id, user_id, db: {
            "run_id": run_id,
            "objective": "Ship release note",
            "status": "completed",
        },
    )
    monkeypatch.setattr(runtime_api, "to_execution_response", lambda run, db: run)

    result = runtime_api.create_agent_run_runtime(
        goal="Ship release note",
        db=db_session,
        user_id=user_id,
    )

    persisted = db_session.query(AgentRun).filter(AgentRun.user_id == user_id).one()
    assert result["status"] == "completed"
    assert persisted.plan["steps"][0]["tool"] == "memory.recall"
    assert persisted.status == "approved"
