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
    """_build_deep_health_payload must include syscall_registry in its checks dict.

    Only the *wiring* is asserted here. `_build_deep_health_payload` runs each check
    through `_run_deep_check(..., timeout=0.5)`, which is correct for a health endpoint
    — it must never hang — but it means the value under this key is whatever the check
    produced **or** `{"status": "error", "detail": "Timed out after 0.5s"}` when the
    worker thread did not get scheduled in time. Asserting the success-shaped keys here
    made this test assert a timing property it never intended to, and it failed in a
    full-suite run under load with exactly that timeout payload.

    The check's own contract is asserted directly below, where no timeout is involved.
    """
    import asyncio
    from AINDY.routes.health_router import _build_deep_health_payload

    payload = asyncio.run(_build_deep_health_payload())

    assert "checks" in payload
    assert "syscall_registry" in payload["checks"]
    assert "status" in payload["checks"]["syscall_registry"]


def test_syscall_registry_check_reports_count_and_minimum():
    """The payload shape `/health/deep` publishes for syscall_registry.

    Called directly rather than through `_build_deep_health_payload`, so it measures the
    check and not the scheduler. This is stricter than what it replaces: it pins the
    `ok`/`incomplete` branch against the real registry, which the timing-dependent
    version could not do reliably.
    """
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY, SYSCALL_REGISTRY_MIN_COUNT
    from AINDY.routes.health_router import _check_syscall_registry_status

    sr = _check_syscall_registry_status()

    assert sr["count"] == len(SYSCALL_REGISTRY)
    assert sr["minimum_expected"] == SYSCALL_REGISTRY_MIN_COUNT
    assert sr["status"] == ("ok" if sr["count"] >= sr["minimum_expected"] else "incomplete")


def test_deep_health_reports_a_slow_check_instead_of_hanging(monkeypatch):
    """A check that overruns its budget degrades to an error entry, it does not hang.

    This is the behaviour that made the previous version of the wiring test flaky, so
    it is now asserted on purpose rather than tripped over. Forcing the timeout is also
    the liveness control for the relaxed assertion above: if `_build_deep_health_payload`
    ever propagated the failure instead of degrading, both tests would still pass without
    this one.
    """
    import asyncio
    import importlib
    import sys
    import time

    # `AINDY/routes/__init__.py` re-exports sub-router *objects* under their module
    # names, so `from AINDY.routes import health_router` yields an APIRouter and
    # monkeypatching an attribute on it raises AttributeError. Reach the real module.
    health_router = sys.modules.get("AINDY.routes.health_router") or importlib.import_module(
        "AINDY.routes.health_router"
    )

    monkeypatch.setattr(
        health_router, "_check_syscall_registry_status", lambda: time.sleep(5)
    )

    payload = asyncio.run(health_router._build_deep_health_payload())

    sr = payload["checks"]["syscall_registry"]
    assert sr["status"] == "error"
    assert "Timed out" in sr["detail"]


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
