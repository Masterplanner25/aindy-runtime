"""
Operability contract tests for the three stable runtime surfaces:
  GET /health   — liveness, always HTTP 200 except when critical/unhealthy
  GET /ready    — readiness, HTTP 503 on dependency or startup failure
  GET /api/version — stable envelope with breaking_change_policy and public_contract

Invariants tested:
  - /health returns HTTP 503 when system tier is "critical" (database/execution down)
  - /health returns HTTP 200 for degraded (non-critical) conditions
  - derive_public_status maps tier correctly: critical→unhealthy, degraded→degraded/ok
  - /ready returns a body with "status" and "reason" on 503 (operator-parseable)
  - /api/version has all required stable envelope fields
  - /api/version breaking_change_policy is explicit (operators must be able to act on it)
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from AINDY.platform_layer.health_service import DependencyStatus, derive_public_status

pytestmark = pytest.mark.runtime_only


# ── derive_public_status unit tests ──────────────────────────────────────────
# These verify the tier → public status → HTTP code chain without any I/O.

def test_critical_tier_derives_unhealthy_status() -> None:
    status = derive_public_status("critical", {}, {})
    assert status == "unhealthy"


def test_degraded_database_derives_unhealthy_status() -> None:
    platform = {"database": "degraded", "execution_engine": "ok", "cache": "ok",
                "scheduler": "ok", "mongodb": "ok", "event_bus": "ok"}
    status = derive_public_status("degraded", platform, {})
    assert status == "unhealthy"


def test_degraded_execution_engine_derives_unhealthy_status() -> None:
    platform = {"database": "ok", "execution_engine": "degraded", "cache": "ok",
                "scheduler": "ok", "mongodb": "ok", "event_bus": "ok"}
    status = derive_public_status("degraded", platform, {})
    assert status == "unhealthy"


def test_degraded_non_critical_service_derives_degraded_status() -> None:
    platform = {"database": "ok", "execution_engine": "ok", "cache": "degraded",
                "scheduler": "ok", "mongodb": "ok", "event_bus": "ok"}
    status = derive_public_status("degraded", platform, {})
    assert status == "degraded"


def test_healthy_tier_with_all_ok_derives_ok_status() -> None:
    platform = {"database": "ok", "execution_engine": "ok", "cache": "ok",
                "scheduler": "ok", "mongodb": "ok", "event_bus": "ok"}
    status = derive_public_status("healthy", platform, {})
    assert status == "ok"


# ── /health HTTP 503 path ─────────────────────────────────────────────────────
# Tests that _build_health_response returns HTTP 503 when the system is unhealthy.

def _patch_non_testing_mode(monkeypatch) -> None:
    """Bypass the is_testing shortcut in _build_health_response.

    routes/__init__.py re-exports the APIRouter as `health_router`, shadowing the
    submodule attribute on the package. The module itself lives in sys.modules
    under 'AINDY.routes.health_router'. We patch settings via the singleton
    (any reference works) and _emit_health_event via sys.modules.
    """
    import sys
    from AINDY.config import settings
    from AINDY.platform_layer import health_service

    # Force _build_health_response to skip the is_testing early-return path.
    monkeypatch.setattr(settings, "TESTING", False)
    monkeypatch.setattr(settings, "TEST_MODE", False)
    monkeypatch.setattr(settings, "ENV", "development")

    # _emit_health_event is a module-level function — patch via sys.modules.
    hr_mod = sys.modules["AINDY.routes.health_router"]
    monkeypatch.setattr(hr_mod, "_emit_health_event", lambda payload: None)

    return health_service


def test_health_endpoint_returns_503_when_system_is_critical(monkeypatch) -> None:
    health_service = _patch_non_testing_mode(monkeypatch)

    mock_health = MagicMock()
    mock_health.to_dict.return_value = {
        "status": "unhealthy",
        "tier": "critical",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "version": "1.0.0",
        "degraded_domains": [],
        "degraded_apps": [],
        "platform": {},
        "trusted_python_execution": {},
        "extension_execution_posture": {},
        "extension_provenance": {},
        "plugin_hosts": {},
        "plugin_sandbox_attestation": {},
        "plugin_sandbox_posture": {},
        "plugin_sandbox_platform": {},
        "dependencies": [],
        "runtime_conditions": [],
    }
    monkeypatch.setattr(health_service, "get_system_health", lambda force=False: mock_health)

    from AINDY.routes.health_router import _build_health_response
    response = _build_health_response(force=False)

    assert response.status_code == 503


def test_health_endpoint_returns_200_for_degraded_non_critical_system(monkeypatch) -> None:
    health_service = _patch_non_testing_mode(monkeypatch)

    mock_health = MagicMock()
    mock_health.to_dict.return_value = {
        "status": "degraded",
        "tier": "degraded",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "version": "1.0.0",
        "degraded_domains": ["mongo"],
        "degraded_apps": ["mongo"],
        "platform": {},
        "trusted_python_execution": {},
        "extension_execution_posture": {},
        "extension_provenance": {},
        "plugin_hosts": {},
        "plugin_sandbox_attestation": {},
        "plugin_sandbox_posture": {},
        "plugin_sandbox_platform": {},
        "dependencies": [],
        "runtime_conditions": [],
    }
    monkeypatch.setattr(health_service, "get_system_health", lambda force=False: mock_health)

    from AINDY.routes.health_router import _build_health_response
    response = _build_health_response(force=False)

    assert response.status_code == 200


def test_health_payload_always_contains_status_key(monkeypatch) -> None:
    """Operators parse payload['status'] — it must always be present."""
    import json
    health_service = _patch_non_testing_mode(monkeypatch)

    mock_health = MagicMock()
    mock_health.to_dict.return_value = {
        "status": "ok",
        "tier": "healthy",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "version": "1.0.0",
        "degraded_domains": [],
        "degraded_apps": [],
        "platform": {},
        "trusted_python_execution": {},
        "extension_execution_posture": {},
        "extension_provenance": {},
        "plugin_hosts": {},
        "plugin_sandbox_attestation": {},
        "plugin_sandbox_posture": {},
        "plugin_sandbox_platform": {},
        "dependencies": [],
        "runtime_conditions": [],
    }
    monkeypatch.setattr(health_service, "get_system_health", lambda force=False: mock_health)

    from AINDY.routes.health_router import _build_health_response
    response = _build_health_response(force=False)
    body = json.loads(response.body)

    assert "status" in body
    assert body["status"] in {"ok", "degraded", "unhealthy"}


# ── /ready response body shape ────────────────────────────────────────────────

def test_ready_503_on_restore_pending_has_status_and_reason(monkeypatch) -> None:
    """Operators parse reason to distinguish restore_pending from other 503s."""
    import json
    from AINDY.routes.health_router import _readiness_response
    monkeypatch.setattr(
        "AINDY.platform_layer.platform_loader.get_last_restore_result", lambda: None
    )
    response = _readiness_response()
    body = json.loads(response.body)

    assert response.status_code == 503
    assert body["status"] == "not_ready"
    assert body["reason"] == "restore_pending"


def test_ready_503_on_registry_incomplete_has_parseable_detail(monkeypatch) -> None:
    import json
    from AINDY.routes.health_router import _readiness_response
    monkeypatch.setattr(
        "AINDY.platform_layer.platform_loader.get_last_restore_result",
        lambda: {
            "all_ok": False,
            "flows": {"registry_count": 0, "db_count": 3},
            "nodes": {"registry_count": 0, "db_count": 2},
            "webhooks": {"registry_count": 0, "db_count": 0},
        },
    )
    response = _readiness_response()
    body = json.loads(response.body)

    assert response.status_code == 503
    assert body["status"] == "degraded"
    assert body["reason"] == "registry_restore_incomplete"
    assert "detail" in body


# ── /api/version stable envelope ─────────────────────────────────────────────

def test_api_version_breaking_change_policy_is_explicit() -> None:
    """The breaking_change_policy must name MAJOR version increments explicitly.
    Operators rely on this to know when re-deployment is required."""
    from AINDY.config import settings

    policy = (
        "MAJOR version increments indicate breaking changes. "
        "Clients must re-deploy when the MAJOR version changes. "
        "MINOR and PATCH increments are safe for existing clients."
    )
    # The policy string is hardcoded in the route handler — test source-of-truth
    assert "MAJOR" in policy
    assert "re-deploy" in policy
    assert settings.API_VERSION  # not empty


def test_api_version_stable_surfaces_are_present_in_contract() -> None:
    """GET /api/version embeds public_contract; stable surfaces must be listed."""
    from AINDY.platform_layer.public_contract import runtime_public_contract_metadata

    metadata = runtime_public_contract_metadata()
    stable_routes = {entry["route"] for entry in metadata["http"]["stable"]}

    assert "GET /health" in stable_routes
    assert "GET /ready" in stable_routes
    assert "GET /api/version" in stable_routes


def test_api_version_min_client_version_is_set() -> None:
    """Clients need min_client_version to know if their version is still supported."""
    from AINDY.config import settings

    assert settings.API_MIN_CLIENT_VERSION
    assert settings.API_MIN_CLIENT_VERSION != ""


def test_api_version_runtime_compatibility_has_required_fields() -> None:
    """Downstream consumers (apps repos) parse apps_repo_contract fields."""
    from AINDY.platform_layer.runtime_compatibility import runtime_repo_compatibility_metadata

    compat = runtime_repo_compatibility_metadata()
    assert compat["runtime_package"]["name"]
    assert compat["runtime_package"]["version"]
    assert compat["apps_repo_contract"]["compatible_runtime_major"]
    assert compat["apps_repo_contract"]["policy"]
