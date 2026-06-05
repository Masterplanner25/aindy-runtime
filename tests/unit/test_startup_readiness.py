"""
INV-STARTUP-002 regression tests: runtime metadata surfaces (liveness) must remain
available even when deeper execution readiness is blocked.

Covers:
- Readiness gate: restore_result=None → 503 restore_pending
- Readiness gate: restore incomplete → 503 registry_restore_incomplete
- Liveness (/health) is independent of the restore gate
- startup_complete=False blocks /ready in production mode
"""
from __future__ import annotations

import pytest

from AINDY.platform_layer import health_service
from AINDY.platform_layer.deployment_contract import (
    get_api_runtime_state,
    publish_api_runtime_state,
    reset_runtime_state,
)

pytestmark = pytest.mark.runtime_only


@pytest.fixture(autouse=True)
def _reset_state():
    reset_runtime_state()
    yield
    reset_runtime_state()


def _ok_dep(name: str, *, critical: bool = False) -> health_service.DependencyStatus:
    return health_service.DependencyStatus(name=name, status="ok", critical=critical)


# ---------------------------------------------------------------------------
# INV-STARTUP-002: restore gate blocks readiness before dynamic registry load
# ---------------------------------------------------------------------------


def test_readiness_returns_503_when_restore_pending(monkeypatch):
    """INV-STARTUP-002: /ready returns 503 restore_pending before load_dynamic_registry runs."""
    from AINDY.routes.health_router import _readiness_response

    monkeypatch.setattr(
        "AINDY.platform_layer.platform_loader.get_last_restore_result",
        lambda: None,
    )

    response = _readiness_response()

    assert response.status_code == 503
    import json
    body = json.loads(response.body)
    assert body["status"] == "not_ready"
    assert body["reason"] == "restore_pending"


def test_readiness_returns_503_when_registry_restore_incomplete(monkeypatch):
    """INV-STARTUP-002: /ready returns 503 when dynamic registry restore did not finish cleanly."""
    from AINDY.routes.health_router import _readiness_response

    monkeypatch.setattr(
        "AINDY.platform_layer.platform_loader.get_last_restore_result",
        lambda: {
            "all_ok": False,
            "flows": {"registry_count": 0, "db_count": 2},
            "nodes": {"registry_count": 0, "db_count": 1},
            "webhooks": {"registry_count": 0, "db_count": 0},
        },
    )

    response = _readiness_response()

    assert response.status_code == 503
    import json
    body = json.loads(response.body)
    assert body["status"] == "degraded"
    assert body["reason"] == "registry_restore_incomplete"


def test_health_returns_200_independent_of_restore_gate(monkeypatch):
    """INV-STARTUP-002: GET /health (liveness) returns 200 even when GET /ready is blocked.

    This proves the liveness-vs-readiness distinction: operators can always reach
    the health endpoint to investigate, even when the runtime is not yet ready to
    accept execution work.
    """
    from AINDY.routes.health_router import _build_health_response, _readiness_response

    # Block readiness with a missing restore result (simulate pre-startup state).
    monkeypatch.setattr(
        "AINDY.platform_layer.platform_loader.get_last_restore_result",
        lambda: None,
    )

    health_response = _build_health_response(force=False)
    ready_response = _readiness_response()

    assert health_response.status_code == 200
    assert ready_response.status_code == 503


def test_startup_incomplete_blocks_readiness_outside_testing_mode(monkeypatch):
    """INV-STARTUP-002: startup_complete=False surfaces as startup_incomplete failure in /ready."""
    monkeypatch.setattr(health_service.settings, "TESTING", False)
    monkeypatch.setattr(health_service.settings, "TEST_MODE", False)
    monkeypatch.setattr(health_service.settings, "ENV", "development")

    # startup_complete defaults to False after reset_runtime_state(); leave it there.
    publish_api_runtime_state(background_enabled=False, scheduler_role="disabled")

    monkeypatch.setattr(health_service, "check_postgres", lambda: _ok_dep("postgres", critical=True))
    monkeypatch.setattr(health_service, "check_redis", lambda: _ok_dep("redis"))
    monkeypatch.setattr(health_service, "check_queue", lambda: _ok_dep("queue"))
    monkeypatch.setattr(health_service, "check_event_bus", lambda: _ok_dep("event_bus"))
    monkeypatch.setattr(health_service, "check_mongo", lambda: _ok_dep("mongo"))
    monkeypatch.setattr(health_service, "check_schema", lambda: _ok_dep("schema", critical=True))
    monkeypatch.setattr(health_service, "check_ai_providers", lambda: _ok_dep("ai_providers"))
    monkeypatch.setattr(health_service, "get_degraded_domains", lambda: [])

    status_code, payload = health_service.get_readiness_report()

    assert status_code == 503
    assert "startup_incomplete" in payload["required_failures"]
    assert payload["checks"]["startup_complete"] is False


def test_readiness_passes_once_startup_complete_and_restore_ok(monkeypatch):
    """INV-STARTUP-002: /ready passes when startup_complete=True and restore result is all_ok."""
    monkeypatch.setattr(health_service.settings, "TESTING", False)
    monkeypatch.setattr(health_service.settings, "TEST_MODE", False)
    monkeypatch.setattr(health_service.settings, "ENV", "development")

    publish_api_runtime_state(
        startup_complete=True,
        background_enabled=False,
        scheduler_role="disabled",
        event_bus_ready=False,
    )

    monkeypatch.setattr(health_service, "check_postgres", lambda: _ok_dep("postgres", critical=True))
    monkeypatch.setattr(health_service, "check_redis", lambda: _ok_dep("redis"))
    monkeypatch.setattr(health_service, "check_queue", lambda: _ok_dep("queue"))
    monkeypatch.setattr(health_service, "check_event_bus", lambda: _ok_dep("event_bus"))
    monkeypatch.setattr(health_service, "check_mongo", lambda: _ok_dep("mongo"))
    monkeypatch.setattr(health_service, "check_schema", lambda: _ok_dep("schema", critical=True))
    monkeypatch.setattr(health_service, "check_ai_providers", lambda: _ok_dep("ai_providers"))
    monkeypatch.setattr(health_service, "get_degraded_domains", lambda: [])

    status_code, payload = health_service.get_readiness_report()

    assert status_code == 200
    assert payload["status"] == "ready"
    assert "startup_incomplete" not in payload["required_failures"]
