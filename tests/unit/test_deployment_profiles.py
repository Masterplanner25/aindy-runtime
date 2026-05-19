from __future__ import annotations

import pytest

from AINDY.platform_layer.deployment_contract import (
    DEPLOYMENT_PROFILE_DISTRIBUTED_API,
    DEPLOYMENT_PROFILE_DISTRIBUTED_WORKER,
    DEPLOYMENT_PROFILE_SINGLE_INSTANCE,
    deployment_contract_summary,
    publish_api_runtime_state,
    reset_runtime_state,
    resolve_api_deployment_profile,
    validate_api_deployment_profile,
    validate_worker_deployment_profile,
)
from AINDY.platform_layer import health_service


class _HealthyBackend:
    degraded = False
    fallback_reason = None


def _ok_dependency(name: str, *, critical: bool = False) -> health_service.DependencyStatus:
    return health_service.DependencyStatus(name=name, status="ok", critical=critical)


@pytest.fixture(autouse=True)
def _reset_runtime(monkeypatch):
    monkeypatch.delenv("AINDY_DEPLOYMENT_PROFILE", raising=False)
    monkeypatch.delenv("AINDY_EVENT_BUS_ENABLED", raising=False)
    reset_runtime_state()
    yield
    reset_runtime_state()


def test_single_instance_profile_is_inferred_from_thread_mode(monkeypatch):
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "thread")

    profile_name, source = resolve_api_deployment_profile()

    assert profile_name == DEPLOYMENT_PROFILE_SINGLE_INSTANCE
    assert source == "derived:EXECUTION_MODE"


def test_explicit_single_instance_profile_rejects_distributed_execution(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_SINGLE_INSTANCE)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "distributed")

    with pytest.raises(RuntimeError, match="single-instance profile requires EXECUTION_MODE=thread"):
        validate_api_deployment_profile()


def test_distributed_api_profile_requires_redis(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_DISTRIBUTED_API)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.REDIS_URL", "")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_CACHE_BACKEND", "redis")

    with pytest.raises(RuntimeError, match="requires REDIS_URL"):
        validate_api_deployment_profile()


def test_worker_profile_requires_distributed_mode(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_DISTRIBUTED_WORKER)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "thread")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.REDIS_URL", "redis://example")

    with pytest.raises(RuntimeError, match="requires EXECUTION_MODE=distributed"):
        validate_worker_deployment_profile()


def test_distributed_api_missing_worker_is_startup_fatal_in_production(monkeypatch):
    import AINDY.startup as startup

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_DISTRIBUTED_API)
    monkeypatch.setattr(startup.settings, "ENV", "production")
    monkeypatch.setattr(startup.settings, "TESTING", False)
    monkeypatch.setattr(startup.settings, "TEST_MODE", False)
    monkeypatch.setattr(startup.settings, "EXECUTION_MODE", "distributed")
    monkeypatch.setattr(startup.settings, "REDIS_URL", "redis://example")
    monkeypatch.setattr(startup.settings, "AINDY_CACHE_BACKEND", "redis")
    monkeypatch.setattr(startup, "validate_queue_backend", lambda: _HealthyBackend())
    monkeypatch.setattr(startup, "_check_worker_presence", lambda _log: False)

    with pytest.raises(RuntimeError, match="no worker heartbeat detected"):
        startup._validate_queue_and_workers()


def test_readiness_reports_active_deployment_profile(monkeypatch):
    monkeypatch.setattr(health_service.settings, "TESTING", False)
    monkeypatch.setattr(health_service.settings, "TEST_MODE", False)
    monkeypatch.setattr(health_service.settings, "ENV", "development")
    publish_api_runtime_state(
        process_role="api",
        startup_complete=True,
        background_enabled=False,
        scheduler_role="disabled",
        background_leadership_mode="in-process",
        deployment_profile=DEPLOYMENT_PROFILE_SINGLE_INSTANCE,
        deployment_profile_source="derived:EXECUTION_MODE",
    )
    monkeypatch.setattr(health_service, "check_postgres", lambda: _ok_dependency("postgres", critical=True))
    monkeypatch.setattr(health_service, "check_redis", lambda: _ok_dependency("redis"))
    monkeypatch.setattr(health_service, "check_queue", lambda: _ok_dependency("queue"))
    monkeypatch.setattr(health_service, "check_event_bus", lambda: _ok_dependency("event_bus"))
    monkeypatch.setattr(health_service, "check_mongo", lambda: _ok_dependency("mongo"))
    monkeypatch.setattr(health_service, "check_schema", lambda: _ok_dependency("schema", critical=True))
    monkeypatch.setattr(health_service, "check_ai_providers", lambda: _ok_dependency("ai_providers"))
    monkeypatch.setattr(health_service, "get_degraded_domains", lambda: [])

    status_code, payload = health_service.get_readiness_report()

    assert status_code == 200
    assert payload["checks"]["deployment_profile"] == DEPLOYMENT_PROFILE_SINGLE_INSTANCE
    assert payload["checks"]["background_leadership_mode"] == "in-process"


def test_deployment_contract_summary_reports_active_profile(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_SINGLE_INSTANCE)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "thread")

    summary = deployment_contract_summary()

    assert summary["active_profile"]["name"] == DEPLOYMENT_PROFILE_SINGLE_INSTANCE
    assert summary["active_profile"]["source"] == "AINDY_DEPLOYMENT_PROFILE"
