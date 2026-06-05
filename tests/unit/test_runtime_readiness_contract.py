"""
Phase 3 — runtime readiness contract tests.

Covers:
- IDEM-7: syscall registry completeness floor (SYSCALL_REGISTRY_MIN_COUNT)
- _check_syscall_registry_status() returns ok / incomplete correctly
- /health/deep includes syscall_registry in its checks payload
- Scheduler status endpoint (SCHED-001/002/003): works in platform-only profile
  without tasks domain — no 500, graceful not-available response
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only


# ---------------------------------------------------------------------------
# IDEM-7: syscall registry floor constant and check function
# ---------------------------------------------------------------------------

def test_syscall_registry_min_count_is_exported():
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY_MIN_COUNT

    assert isinstance(SYSCALL_REGISTRY_MIN_COUNT, int)
    assert SYSCALL_REGISTRY_MIN_COUNT > 0


def test_syscall_registry_meets_minimum_floor():
    """After module load the static built-in entries must reach the declared floor."""
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY, SYSCALL_REGISTRY_MIN_COUNT

    assert len(SYSCALL_REGISTRY) >= SYSCALL_REGISTRY_MIN_COUNT


def test_check_syscall_registry_status_ok_when_count_meets_floor():
    from AINDY.routes.health_router import _check_syscall_registry_status

    result = _check_syscall_registry_status()

    assert result["status"] == "ok"
    assert result["count"] >= result["minimum_expected"]
    assert result["minimum_expected"] > 0


def test_check_syscall_registry_status_incomplete_when_below_floor(monkeypatch):
    """Simulate a future bug where registrations are lost."""
    import sys
    from AINDY.kernel import syscall_registry as _reg_mod

    # Import must go via sys.modules — __init__.py name-shadows the submodule attribute.
    _hr = sys.modules.get("AINDY.routes.health_router")
    if _hr is None:
        import importlib
        importlib.import_module("AINDY.routes.health_router")
        _hr = sys.modules["AINDY.routes.health_router"]

    monkeypatch.setattr(_reg_mod, "SYSCALL_REGISTRY_MIN_COUNT", 9999)

    result = _hr._check_syscall_registry_status()

    assert result["status"] == "incomplete"
    assert "minimum_expected" in result
    assert "detail" in result
    assert "Phase 8" in result["detail"]


# ---------------------------------------------------------------------------
# Deep health payload includes syscall_registry
# ---------------------------------------------------------------------------

def test_deep_health_payload_includes_syscall_registry_check():
    """_build_deep_health_payload must include syscall_registry in its checks dict."""
    import asyncio
    from AINDY.routes.health_router import _build_deep_health_payload

    payload = asyncio.run(_build_deep_health_payload())

    assert "checks" in payload
    assert "syscall_registry" in payload["checks"]
    sr = payload["checks"]["syscall_registry"]
    assert "status" in sr
    assert "count" in sr
    assert "minimum_expected" in sr


# ---------------------------------------------------------------------------
# SCHED-001/002/003: scheduler status in platform-only profile (no tasks domain)
# ---------------------------------------------------------------------------

def test_scheduler_status_payload_graceful_when_tasks_domain_absent(monkeypatch):
    """SCHED-001/002/003: _build_scheduler_status_payload must not raise when
    task_is_background_leader is not registered in the plugin registry."""
    from unittest.mock import MagicMock, patch

    mock_db = MagicMock()

    with (
        patch("AINDY.platform_layer.registry.get_symbol", return_value=None),
        patch(
            "AINDY.platform_layer.scheduler_service.get_scheduler",
            side_effect=RuntimeError("scheduler not started"),
        ),
        patch(
            "AINDY.agents.stuck_run_watchdog.get_last_scan_result",
            return_value={
                "last_run_at": None,
                "recovered": 0,
                "dead_lettered": 0,
                "had_error": False,
                "error_message": None,
            },
        ),
    ):
        from AINDY.routes.observability_router import _build_scheduler_status_payload

        result = _build_scheduler_status_payload(mock_db)

    assert "observability_scheduler_status_result" in result
    sr = result["observability_scheduler_status_result"]
    assert sr["tasks_domain_available"] is False
    assert sr["is_leader"] is None
    assert sr["lease"] is None
    assert sr["scheduler_running"] is False


def test_scheduler_status_payload_includes_stuck_run_watchdog(monkeypatch):
    """stuck_run_watchdog fields must always appear in the scheduler status payload."""
    from unittest.mock import MagicMock, patch

    mock_db = MagicMock()

    with (
        patch("AINDY.platform_layer.registry.get_symbol", return_value=None),
        patch(
            "AINDY.platform_layer.scheduler_service.get_scheduler",
            side_effect=RuntimeError("scheduler not started"),
        ),
        patch(
            "AINDY.agents.stuck_run_watchdog.get_last_scan_result",
            return_value={
                "last_run_at": "2026-06-04T12:00:00",
                "recovered": 2,
                "dead_lettered": 0,
                "had_error": False,
                "error_message": None,
            },
        ),
    ):
        from AINDY.routes.observability_router import _build_scheduler_status_payload

        result = _build_scheduler_status_payload(mock_db)

    assert "stuck_run_watchdog" in result
    wdog = result["stuck_run_watchdog"]
    assert "registered" in wdog
    assert "last_run_at" in wdog
    assert "recovery_sla_minutes" in wdog
    assert "stuck_threshold_minutes" in wdog
