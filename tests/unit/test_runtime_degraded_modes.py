from __future__ import annotations

import asyncio

import pytest

from AINDY.core import distributed_queue
from AINDY.platform_layer import health_service
from AINDY.platform_layer.deployment_contract import (
    get_api_runtime_conditions,
    publish_api_runtime_state,
    reset_runtime_state,
    set_api_runtime_condition,
)


class _DummyDb:
    def close(self) -> None:
        return None


def _ok_dependency(name: str, *, critical: bool = False) -> health_service.DependencyStatus:
    return health_service.DependencyStatus(name=name, status="ok", critical=critical)


@pytest.fixture(autouse=True)
def _reset_runtime_state():
    reset_runtime_state()
    yield
    reset_runtime_state()


def test_queue_fallback_records_unsafe_runtime_condition(monkeypatch):
    monkeypatch.setattr(distributed_queue.settings, "AINDY_REQUIRE_REDIS", False)
    monkeypatch.setattr(distributed_queue.settings, "EXECUTION_MODE", "distributed")

    backend = distributed_queue._fallback_to_memory_backend(RuntimeError("redis down"))
    conditions = get_api_runtime_conditions()

    assert backend.degraded is True
    assert len(conditions) == 1
    assert conditions[0]["code"] == "queue_backend_fallback"
    assert conditions[0]["classification"] == "unsafe_degraded"
    assert conditions[0]["detail"] == "redis down"


def test_health_and_readiness_expose_unsafe_runtime_conditions(monkeypatch):
    monkeypatch.setattr(health_service.settings, "TESTING", False)
    monkeypatch.setattr(health_service.settings, "TEST_MODE", False)
    monkeypatch.setattr(health_service.settings, "ENV", "development")
    publish_api_runtime_state(startup_complete=True, background_enabled=False, scheduler_role="disabled")
    set_api_runtime_condition(
        code="dynamic_registry_restore_incomplete",
        component="plugin_restore",
        classification="unsafe_degraded",
        detail="flows=0/1",
        production_behavior="startup-fatal",
    )

    monkeypatch.setattr(health_service, "check_postgres", lambda: _ok_dependency("postgres", critical=True))
    monkeypatch.setattr(health_service, "check_redis", lambda: _ok_dependency("redis"))
    monkeypatch.setattr(health_service, "check_queue", lambda: _ok_dependency("queue"))
    monkeypatch.setattr(health_service, "check_event_bus", lambda: _ok_dependency("event_bus"))
    monkeypatch.setattr(health_service, "check_mongo", lambda: _ok_dependency("mongo"))
    monkeypatch.setattr(health_service, "check_schema", lambda: _ok_dependency("schema", critical=True))
    monkeypatch.setattr(health_service, "check_ai_providers", lambda: _ok_dependency("ai_providers"))
    monkeypatch.setattr(health_service, "get_degraded_domains", lambda: [])

    health = health_service.get_system_health(force=True)
    status_code, payload = health_service.get_readiness_report()

    assert health.tier == "critical"
    assert health.to_dict()["runtime_conditions"][0]["code"] == "dynamic_registry_restore_incomplete"
    assert status_code == 503
    assert "dynamic_registry_restore_incomplete" in payload["required_failures"]


def test_safe_runtime_degradation_keeps_readiness_green(monkeypatch):
    monkeypatch.setattr(health_service.settings, "TESTING", False)
    monkeypatch.setattr(health_service.settings, "TEST_MODE", False)
    monkeypatch.setattr(health_service.settings, "ENV", "development")
    publish_api_runtime_state(startup_complete=True, background_enabled=False, scheduler_role="disabled")
    set_api_runtime_condition(
        code="mongo_optional_unavailable",
        component="mongo",
        classification="safe_degraded",
        detail="mongo not configured",
        production_behavior="explicitly degraded",
    )

    monkeypatch.setattr(health_service, "check_postgres", lambda: _ok_dependency("postgres", critical=True))
    monkeypatch.setattr(health_service, "check_redis", lambda: _ok_dependency("redis"))
    monkeypatch.setattr(health_service, "check_queue", lambda: _ok_dependency("queue"))
    monkeypatch.setattr(health_service, "check_event_bus", lambda: _ok_dependency("event_bus"))
    monkeypatch.setattr(health_service, "check_mongo", lambda: _ok_dependency("mongo"))
    monkeypatch.setattr(health_service, "check_schema", lambda: _ok_dependency("schema", critical=True))
    monkeypatch.setattr(health_service, "check_ai_providers", lambda: _ok_dependency("ai_providers"))
    monkeypatch.setattr(health_service, "get_degraded_domains", lambda: [])

    health = health_service.get_system_health(force=True)
    status_code, payload = health_service.get_readiness_report()

    assert health.tier == "degraded"
    assert status_code == 200
    assert payload["required_failures"] == []
    assert payload["checks"]["trusted_python_execution"]["sandboxing"] == "none"


def test_external_python_override_is_operator_visible_but_not_readiness_fatal(monkeypatch):
    import AINDY.startup as startup

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS", "true")
    monkeypatch.setattr(startup.settings, "ENV", "development")
    monkeypatch.setattr(startup.settings, "TESTING", False)
    monkeypatch.setattr(startup.settings, "TEST_MODE", False)
    startup._enforce_external_python_override_policy()

    monkeypatch.setattr(health_service.settings, "TESTING", False)
    monkeypatch.setattr(health_service.settings, "TEST_MODE", False)
    monkeypatch.setattr(health_service.settings, "ENV", "development")
    publish_api_runtime_state(startup_complete=True, background_enabled=False, scheduler_role="disabled")
    monkeypatch.setattr(health_service, "check_postgres", lambda: _ok_dependency("postgres", critical=True))
    monkeypatch.setattr(health_service, "check_redis", lambda: _ok_dependency("redis"))
    monkeypatch.setattr(health_service, "check_queue", lambda: _ok_dependency("queue"))
    monkeypatch.setattr(health_service, "check_event_bus", lambda: _ok_dependency("event_bus"))
    monkeypatch.setattr(health_service, "check_mongo", lambda: _ok_dependency("mongo"))
    monkeypatch.setattr(health_service, "check_schema", lambda: _ok_dependency("schema", critical=True))
    monkeypatch.setattr(health_service, "check_ai_providers", lambda: _ok_dependency("ai_providers"))
    monkeypatch.setattr(health_service, "get_degraded_domains", lambda: [])

    health = health_service.get_system_health(force=True)
    status_code, payload = health_service.get_readiness_report()

    assert health.tier == "degraded"
    assert any(
        condition["code"] == "external_python_override_enabled"
        for condition in health.to_dict()["runtime_conditions"]
    )
    assert status_code == 200
    assert payload["checks"]["external_python_override_active"] is True
    assert payload["checks"]["external_python_override_execution_model"] == "isolated-plugin-host-required"
    assert payload["required_failures"] == []


def test_external_python_override_no_longer_requires_prod_ack(monkeypatch):
    import AINDY.startup as startup

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS", "true")
    monkeypatch.delenv("AINDY_ACK_UNSANDBOXED_EXTERNAL_PYTHON", raising=False)
    monkeypatch.setattr(startup.settings, "ENV", "production")
    monkeypatch.setattr(startup.settings, "TESTING", False)
    monkeypatch.setattr(startup.settings, "TEST_MODE", False)

    startup._enforce_external_python_override_policy()

    assert any(
        condition["code"] == "external_python_override_enabled"
        for condition in get_api_runtime_conditions()
    )


def test_dynamic_registry_restore_is_runtime_condition_in_development(monkeypatch):
    import AINDY.startup as startup
    import AINDY.platform_layer.platform_loader as platform_loader

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(startup.settings, "ENV", "development")
    monkeypatch.setattr(startup.settings, "TESTING", False)
    monkeypatch.setattr(startup.settings, "TEST_MODE", False)

    monkeypatch.setattr(platform_loader, "load_dynamic_registry", lambda db: (_ for _ in ()).throw(RuntimeError("restore exploded")))

    async def _verify_restore(_db):
        return {
            "flows": {"registry_count": 0, "db_count": 1},
            "nodes": {"registry_count": 0, "db_count": 1},
            "webhooks": {"registry_count": 0, "db_count": 1},
            "all_ok": False,
        }

    monkeypatch.setattr(platform_loader, "verify_restore_completeness", _verify_restore)

    asyncio.run(startup._restore_dynamic_registry(_DummyDb))

    assert {condition["code"] for condition in get_api_runtime_conditions()} == {
        "dynamic_registry_restore_failed",
        "dynamic_registry_restore_incomplete",
    }


def test_dynamic_registry_restore_is_startup_fatal_in_production(monkeypatch):
    import AINDY.startup as startup
    import AINDY.platform_layer.platform_loader as platform_loader

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(startup.settings, "ENV", "production")
    monkeypatch.setattr(startup.settings, "TESTING", False)
    monkeypatch.setattr(startup.settings, "TEST_MODE", False)
    monkeypatch.setattr(platform_loader, "load_dynamic_registry", lambda db: (_ for _ in ()).throw(RuntimeError("restore exploded")))

    with pytest.raises(RuntimeError, match="Dynamic registry restore failed"):
        asyncio.run(startup._restore_dynamic_registry(_DummyDb))


def test_rehydration_failure_is_startup_fatal_in_production(monkeypatch):
    import AINDY.startup as startup
    import AINDY.core.wait_rehydration as wait_rehydration

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(startup.settings, "ENV", "production")
    monkeypatch.setattr(startup.settings, "TESTING", False)
    monkeypatch.setattr(startup.settings, "TEST_MODE", False)
    monkeypatch.setattr(wait_rehydration, "rehydrate_waiting_eus", lambda db: (_ for _ in ()).throw(RuntimeError("rehydrate exploded")))

    with pytest.raises(RuntimeError, match="WAIT execution-unit rehydration failed"):
        startup._rehydrate_waiting_state(_DummyDb, False)


def test_event_bus_dependency_reports_local_only_degradation(monkeypatch):
    class _Bus:
        def get_status(self):
            return {
                "enabled": True,
                "subscriber_running": False,
                "redis_connected": False,
                "mode": "local-only",
            }

    monkeypatch.setattr(health_service.settings, "AINDY_REQUIRE_REDIS", False)
    monkeypatch.setattr("AINDY.kernel.event_bus.get_event_bus", lambda: _Bus())

    status = health_service.check_event_bus()

    assert status.status == "degraded"
    assert status.detail == "WAIT/RESUME propagation is local-only"


def test_testing_mode_readiness_reports_trusted_python_inventory(monkeypatch):
    monkeypatch.setattr(health_service.settings, "TESTING", True)
    monkeypatch.setattr(health_service.settings, "TEST_MODE", True)

    status_code, payload = health_service.get_readiness_report()

    assert status_code == 200
    assert payload["checks"]["testing_mode"] is True
    assert payload["checks"]["trusted_python_execution"]["execution_model"] == "trusted-in-process-python"
    assert payload["checks"]["trusted_python_execution"]["sandboxing"] == "none"
    assert payload["checks"]["plugin_sandbox_attestation"]["present"] is False
    assert payload["checks"]["plugin_sandbox_posture"] == {
        "deployment_profile": "single-instance",
        "current": {
            "runner_type": "insecure_dev_subprocess",
            "assurance_class": "insecure-dev",
            "certification_tier": "contained-process-certified",
            "certification_status": "certified",
        },
        "required": {
            "assurance_class": None,
            "runner_type": None,
            "certification_tier": None,
        },
        "requirement_status": {
            "assurance_class_satisfied": True,
            "certification_tier_satisfied": True,
        },
        "unsupported_claims": [
            "general third-party sandboxing",
            "hard resource-limit enforcement",
            "kernel-level isolation guarantees",
        ],
        "distinction_note": (
            "Assurance class describes the runner category, attestation describes what the runtime "
            "observed, and certification describes what the runtime can justify from verified evidence."
        ),
        "notes": "This profile does not require a third-party sandbox assurance class.",
    }
    assert payload["checks"]["plugin_sandbox_platform"]["schema_version"] == "2026-05-21"
