"""
INV-READY-001 regression tests: readiness under partial infrastructure conditions.

Covers:
- Critical dependency (postgres) unavailable → 503 with "postgres" in required_failures
- Critical dependency (schema) unavailable → 503 with "schema" in required_failures
- Non-critical dependency down (redis, not required) → readiness remains 200
- All critical dependencies healthy → readiness 200
- Multiple partial failures accumulate in required_failures
"""
from __future__ import annotations

import pytest

from AINDY.platform_layer import health_service
from AINDY.platform_layer.deployment_contract import (
    publish_api_runtime_state,
    reset_runtime_state,
)

pytestmark = pytest.mark.runtime_only


@pytest.fixture(autouse=True)
def _reset():
    reset_runtime_state()
    yield
    reset_runtime_state()


def _ok(name: str, *, critical: bool = False) -> health_service.DependencyStatus:
    return health_service.DependencyStatus(name=name, status="ok", critical=critical)


def _down(name: str, *, critical: bool = False) -> health_service.DependencyStatus:
    return health_service.DependencyStatus(name=name, status="unavailable", critical=critical)


def _patch_all_ok(monkeypatch) -> None:
    """Patch all check functions to return healthy for clean baseline."""
    monkeypatch.setattr(health_service, "check_postgres", lambda: _ok("postgres", critical=True))
    monkeypatch.setattr(health_service, "check_redis", lambda: _ok("redis"))
    monkeypatch.setattr(health_service, "check_queue", lambda: _ok("queue"))
    monkeypatch.setattr(health_service, "check_event_bus", lambda: _ok("event_bus"))
    monkeypatch.setattr(health_service, "check_mongo", lambda: _ok("mongo"))
    monkeypatch.setattr(health_service, "check_schema", lambda: _ok("schema", critical=True))
    monkeypatch.setattr(health_service, "check_ai_providers", lambda: _ok("ai_providers"))
    monkeypatch.setattr(health_service, "get_degraded_domains", lambda: [])


def _non_testing_mode(monkeypatch) -> None:
    monkeypatch.setattr(health_service.settings, "TESTING", False)
    monkeypatch.setattr(health_service.settings, "TEST_MODE", False)
    monkeypatch.setattr(health_service.settings, "ENV", "development")


# ── postgres down ─────────────────────────────────────────────────────────────

def test_postgres_down_blocks_readiness(monkeypatch) -> None:
    _non_testing_mode(monkeypatch)
    publish_api_runtime_state(startup_complete=True, background_enabled=False,
                              scheduler_role="disabled")
    _patch_all_ok(monkeypatch)
    monkeypatch.setattr(health_service, "check_postgres",
                        lambda: _down("postgres", critical=True))

    status_code, payload = health_service.get_readiness_report()

    assert status_code == 503
    assert "postgres" in payload["required_failures"]


def test_postgres_down_includes_check_value_in_payload(monkeypatch) -> None:
    _non_testing_mode(monkeypatch)
    publish_api_runtime_state(startup_complete=True, background_enabled=False,
                              scheduler_role="disabled")
    _patch_all_ok(monkeypatch)
    monkeypatch.setattr(health_service, "check_postgres",
                        lambda: _down("postgres", critical=True))

    _, payload = health_service.get_readiness_report()

    assert payload["checks"]["postgres"] == "unavailable"


# ── schema down ───────────────────────────────────────────────────────────────

def test_critical_schema_down_blocks_readiness(monkeypatch) -> None:
    _non_testing_mode(monkeypatch)
    publish_api_runtime_state(startup_complete=True, background_enabled=False,
                              scheduler_role="disabled")
    _patch_all_ok(monkeypatch)
    monkeypatch.setattr(health_service, "check_schema",
                        lambda: _down("schema", critical=True))

    status_code, payload = health_service.get_readiness_report()

    assert status_code == 503
    assert "schema" in payload["required_failures"]


# ── non-critical dependency down ──────────────────────────────────────────────

def test_redis_down_does_not_block_readiness_when_not_required(monkeypatch) -> None:
    """In the default single-instance profile, Redis is not required.
    A degraded Redis must not block readiness."""
    _non_testing_mode(monkeypatch)
    publish_api_runtime_state(startup_complete=True, background_enabled=False,
                              scheduler_role="disabled")
    _patch_all_ok(monkeypatch)
    monkeypatch.setattr(health_service, "check_redis",
                        lambda: _down("redis", critical=False))
    # Ensure redis is not treated as required
    from AINDY.platform_layer import deployment_contract
    monkeypatch.setattr(deployment_contract, "redis_required", lambda: False)

    status_code, payload = health_service.get_readiness_report()

    assert status_code == 200
    assert "redis" not in payload["required_failures"]


# ── multiple failures accumulate ─────────────────────────────────────────────

def test_multiple_critical_failures_all_reported(monkeypatch) -> None:
    _non_testing_mode(monkeypatch)
    publish_api_runtime_state(startup_complete=True, background_enabled=False,
                              scheduler_role="disabled")
    _patch_all_ok(monkeypatch)
    monkeypatch.setattr(health_service, "check_postgres",
                        lambda: _down("postgres", critical=True))
    monkeypatch.setattr(health_service, "check_schema",
                        lambda: _down("schema", critical=True))

    status_code, payload = health_service.get_readiness_report()

    assert status_code == 503
    assert "postgres" in payload["required_failures"]
    assert "schema" in payload["required_failures"]


# ── all critical healthy ──────────────────────────────────────────────────────

def test_all_critical_deps_healthy_returns_ready(monkeypatch) -> None:
    _non_testing_mode(monkeypatch)
    publish_api_runtime_state(startup_complete=True, background_enabled=False,
                              scheduler_role="disabled")
    _patch_all_ok(monkeypatch)

    status_code, payload = health_service.get_readiness_report()

    assert status_code == 200
    assert payload["required_failures"] == []
