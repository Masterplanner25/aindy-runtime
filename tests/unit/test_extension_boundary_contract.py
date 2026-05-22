from __future__ import annotations

from collections import defaultdict

import pytest

from AINDY.agents.tool_registry import suggest_tools
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
def clean_extension_boundary_registry():
    snapshot = {
        name: _copy_registry_value(getattr(registry, name))
        for name in _REGISTRY_STATE_EMPTY
    }
    try:
        for name, value in _REGISTRY_STATE_EMPTY.items():
            setattr(registry, name, _copy_registry_value(value))
        yield
    finally:
        for name, value in snapshot.items():
            setattr(registry, name, value)


def test_planner_context_provider_receives_sanitized_structured_context(
    clean_extension_boundary_registry,
):
    captured = {}

    class FakeRow:
        pass

    def provider(context):
        captured["context"] = context
        return {"system_prompt": "ok"}

    registry.register_planner_context_provider("default", provider)

    context = registry.get_planner_context(
        "default",
        {
            "user_id": "user-1",
            "db": object(),
            "_db": object(),
            "row": FakeRow(),
            "nested": {"session": object(), "value": "safe"},
        },
    )

    assert context == {"system_prompt": "ok"}
    assert captured["context"]["user_id"] == "user-1"
    assert "db" not in captured["context"]
    assert "_db" not in captured["context"]
    assert captured["context"]["row"] == {"_redacted_type": "FakeRow"}
    assert captured["context"]["nested"]["session"] == {"_redacted_type": "object"}
    assert captured["context"]["nested"]["value"] == "safe"


def test_internal_event_handlers_do_not_receive_db_or_raw_internal_objects():
    from AINDY.platform_layer.event_service import (
        _INTERNAL_HANDLERS,
        _webhook_lock,
        dispatch_internal_event_handlers,
    )

    captured = {}

    class FakeModel:
        pass

    def handler(event):
        captured["event"] = event

    with _webhook_lock:
        _INTERNAL_HANDLERS.clear()
        _INTERNAL_HANDLERS["execution.completed"] = [handler]
    try:
        count = dispatch_internal_event_handlers(
            db=object(),
            event_type="execution.completed",
            event_id="evt-1",
            payload={"row": FakeModel(), "nested": {"db": object(), "safe": "ok"}},
            user_id="user-1",
            trace_id="trace-1",
            source="test",
        )
    finally:
        with _webhook_lock:
            _INTERNAL_HANDLERS.clear()

    assert count == 1
    assert "db" not in captured["event"]
    assert captured["event"]["payload"]["row"] == {"_redacted_type": "FakeModel"}
    assert captured["event"]["payload"]["nested"]["db"] == {"_redacted_type": "object"}
    assert captured["event"]["payload"]["nested"]["safe"] == "ok"


def test_tool_suggestion_providers_do_not_receive_db_handles():
    captured = {}

    def provider(*, suggestion_context, user_id):
        captured["suggestion_context"] = suggestion_context
        captured["user_id"] = user_id
        return [{"name": "memory.recall"}]

    from AINDY.agents import tool_registry

    snapshot = list(tool_registry._SUGGESTION_PROVIDERS)
    try:
        tool_registry._SUGGESTION_PROVIDERS.clear()
        tool_registry.register_tool_suggestion_provider(provider)
        suggestions = suggest_tools(
            suggestion_context={
                "goal": "recall context",
                "db": object(),
                "model": object(),
            },
            user_id="user-1",
            db=object(),
        )
    finally:
        tool_registry._SUGGESTION_PROVIDERS.clear()
        tool_registry._SUGGESTION_PROVIDERS.extend(snapshot)

    assert suggestions == [{"name": "memory.recall"}]
    assert captured["user_id"] == "user-1"
    assert "db" not in captured["suggestion_context"]
    assert captured["suggestion_context"]["model"] == {"_redacted_type": "object"}


def test_extension_runtime_api_rejects_direct_channel_bootstrap():
    from AINDY.platform_layer import extension_runtime_api

    with pytest.raises(PermissionError, match="restricted to the extension worker"):
        extension_runtime_api._install_runtime_api_channel(bridge=lambda *_args, **_kwargs: {})


def test_extension_runtime_api_denies_unauthenticated_runtime_calls():
    from AINDY.platform_layer import extension_runtime_api

    with pytest.raises(PermissionError, match="UNAUTHENTICATED_EXTENSION_CHANNEL"):
        extension_runtime_api.memory_read(query="alpha")


def test_extension_runtime_api_exposes_no_ambient_runtime_handles():
    from AINDY.platform_layer import extension_runtime_api

    assert hasattr(extension_runtime_api, "dispatch_syscall") is False
    assert hasattr(extension_runtime_api, "SessionLocal") is False
    assert hasattr(extension_runtime_api, "execute_tool") is False
