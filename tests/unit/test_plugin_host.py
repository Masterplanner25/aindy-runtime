from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


pytestmark = pytest.mark.runtime_only


@pytest.fixture
def clean_plugin_hosts():
    from AINDY.platform_layer.plugin_host import reset_plugin_hosts

    reset_plugin_hosts()
    try:
        yield
    finally:
        reset_plugin_hosts()


def _write_plugin(tmp_path, module_name: str, source: str):
    plugin_dir = tmp_path / "plugins" / "nodes"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / f"{module_name}.py").write_text(source.strip(), encoding="utf-8")
    return plugin_dir


def test_plugin_host_startup_and_shutdown(tmp_path, clean_plugin_hosts):
    from AINDY.platform_layer.plugin_host import (
        get_plugin_host,
        shutdown_plugin_host,
        start_plugin_host,
    )

    plugin_dir = _write_plugin(
        tmp_path,
        "safe_node",
        """
def handler(state, context):
    return {"status": "SUCCESS", "output_patch": {"seen": state.get("value")}}
""",
    )

    snapshot = start_plugin_host(
        name="hosted-plugin",
        handler="safe_node:handler",
        plugin_root=plugin_dir,
        owner_class="external-third-party",
        granted_capabilities=[],
    )

    assert snapshot["lifecycle_state"] == "running"
    assert snapshot["healthy"] is True
    assert snapshot["pid"] is not None
    assert snapshot["provenance"]["module_name"] == "safe_node"

    assert shutdown_plugin_host("hosted-plugin") is True
    stopped = get_plugin_host("hosted-plugin")
    assert stopped is not None
    assert stopped["lifecycle_state"] == "stopped"
    assert stopped["healthy"] is False
    assert stopped["pid"] is None


def test_plugin_host_heartbeat_loss_is_reported(tmp_path, clean_plugin_hosts):
    from AINDY.platform_layer import plugin_host

    plugin_dir = _write_plugin(
        tmp_path,
        "safe_node",
        """
def handler(state, context):
    return {"status": "SUCCESS"}
""",
    )

    plugin_host.start_plugin_host(
        name="heartbeat-plugin",
        handler="safe_node:handler",
        plugin_root=plugin_dir,
        owner_class="external-third-party",
        granted_capabilities=[],
    )
    plugin_host._HOSTS["heartbeat-plugin"].last_heartbeat_at = (
        datetime.now(timezone.utc) - timedelta(seconds=60)
    ).isoformat()

    inventory = plugin_host.plugin_host_inventory(probe=False)

    assert inventory["overall_status"] == "degraded"
    assert inventory["hosts"][0]["lifecycle_state"] == "heartbeat_lost"


def test_plugin_host_crash_and_restart_handling(tmp_path, clean_plugin_hosts):
    from AINDY.platform_layer.plugin_host import (
        execute_plugin_host,
        get_plugin_host,
        start_plugin_host,
    )

    plugin_dir = _write_plugin(
        tmp_path,
        "crash_node",
        """
import os

def handler(state, context):
    if state.get("crash"):
        os._exit(7)
    return {"status": "SUCCESS", "output_patch": {"recovered": True}}
""",
    )

    start_plugin_host(
        name="crash-plugin",
        handler="crash_node:handler",
        plugin_root=plugin_dir,
        owner_class="external-third-party",
        granted_capabilities=[],
    )

    with pytest.raises(RuntimeError, match="plugin host exited with code 7"):
        execute_plugin_host(
            name="crash-plugin",
            state={"crash": True},
            runtime_context={},
        )

    crashed = get_plugin_host("crash-plugin")
    assert crashed is not None
    assert crashed["lifecycle_state"] == "backoff"
    assert crashed["crash_failures"] >= 1

    from AINDY.platform_layer import plugin_host as plugin_host_module
    plugin_host_module._HOSTS["crash-plugin"].circuit_open_until = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    result = execute_plugin_host(
        name="crash-plugin",
        state={"crash": False},
        runtime_context={},
    )
    restarted = get_plugin_host("crash-plugin")

    assert result["status"] == "SUCCESS"
    assert restarted is not None
    assert restarted["lifecycle_state"] == "running"
    assert restarted["restart_count"] >= 1


def test_plugin_host_timeout_failures_trigger_quarantine(tmp_path, clean_plugin_hosts, monkeypatch):
    from AINDY.platform_layer import plugin_host as plugin_host_module
    from AINDY.platform_layer.plugin_host import execute_plugin_host, get_plugin_host, start_plugin_host

    monkeypatch.setattr(plugin_host_module, "DEFAULT_PLUGIN_HOST_EXECUTE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(plugin_host_module, "DEFAULT_PLUGIN_HOST_TIMEOUT_QUARANTINE_THRESHOLD", 2)
    monkeypatch.setattr(plugin_host_module, "DEFAULT_PLUGIN_HOST_FAILURE_BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr(plugin_host_module, "DEFAULT_PLUGIN_HOST_FAILURE_BACKOFF_MAX_SECONDS", 0.02)

    plugin_dir = _write_plugin(
        tmp_path,
        "slow_node",
        """
import time

def handler(state, context):
    time.sleep(0.2)
    return {"status": "SUCCESS"}
""",
    )

    start_plugin_host(
        name="slow-plugin",
        handler="slow_node:handler",
        plugin_root=plugin_dir,
        owner_class="external-third-party",
        granted_capabilities=[],
    )

    with pytest.raises(TimeoutError, match="timed out"):
        execute_plugin_host(name="slow-plugin", state={}, runtime_context={})
    plugin_host_module._HOSTS["slow-plugin"].circuit_open_until = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    with pytest.raises(RuntimeError, match="quarantined"):
        execute_plugin_host(name="slow-plugin", state={}, runtime_context={})

    snapshot = get_plugin_host("slow-plugin")
    assert snapshot is not None
    assert snapshot["lifecycle_state"] == "quarantined"
    assert snapshot["timeout_failures"] >= 2


def test_plugin_host_contract_violations_trigger_quarantine(tmp_path, clean_plugin_hosts, monkeypatch):
    from AINDY.platform_layer import plugin_host as plugin_host_module
    from AINDY.platform_layer.plugin_host import execute_plugin_host, get_plugin_host, start_plugin_host

    monkeypatch.setattr(plugin_host_module, "DEFAULT_PLUGIN_HOST_CONTRACT_VIOLATION_QUARANTINE_THRESHOLD", 2)
    monkeypatch.setattr(plugin_host_module, "DEFAULT_PLUGIN_HOST_FAILURE_BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr(plugin_host_module, "DEFAULT_PLUGIN_HOST_FAILURE_BACKOFF_MAX_SECONDS", 0.02)

    plugin_dir = _write_plugin(
        tmp_path,
        "bad_contract_node",
        """
def handler(state, context):
    return {"status": "BOGUS"}
""",
    )

    start_plugin_host(
        name="bad-contract-plugin",
        handler="bad_contract_node:handler",
        plugin_root=plugin_dir,
        owner_class="external-third-party",
        granted_capabilities=[],
    )

    with pytest.raises(RuntimeError, match="invalid status"):
        execute_plugin_host(name="bad-contract-plugin", state={}, runtime_context={})

    snapshot = get_plugin_host("bad-contract-plugin")
    assert snapshot is not None
    assert snapshot["lifecycle_state"] == "quarantined"
    assert snapshot["contract_violations"] >= 2
    with pytest.raises(RuntimeError, match="quarantined"):
        execute_plugin_host(name="bad-contract-plugin", state={}, runtime_context={})


def test_plugin_host_repeated_runtime_failures_trigger_quarantine(tmp_path, clean_plugin_hosts, monkeypatch):
    from AINDY.platform_layer import plugin_host as plugin_host_module
    from AINDY.platform_layer.plugin_host import execute_plugin_host, get_plugin_host, start_plugin_host

    monkeypatch.setattr(
        plugin_host_module,
        "DEFAULT_PLUGIN_HOST_CONSECUTIVE_FAILURE_QUARANTINE_THRESHOLD",
        3,
    )
    monkeypatch.setattr(plugin_host_module, "DEFAULT_PLUGIN_HOST_FAILURE_BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr(plugin_host_module, "DEFAULT_PLUGIN_HOST_FAILURE_BACKOFF_MAX_SECONDS", 0.02)

    plugin_dir = _write_plugin(
        tmp_path,
        "failing_node",
        """
def handler(state, context):
    raise RuntimeError("upstream dependency failure")
""",
    )

    start_plugin_host(
        name="failing-plugin",
        handler="failing_node:handler",
        plugin_root=plugin_dir,
        owner_class="external-third-party",
        granted_capabilities=[],
    )

    for _ in range(2):
        with pytest.raises(RuntimeError, match="upstream dependency failure|quarantined"):
            execute_plugin_host(name="failing-plugin", state={}, runtime_context={})
        plugin_host_module._HOSTS["failing-plugin"].circuit_open_until = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()

    snapshot = get_plugin_host("failing-plugin")
    assert snapshot is not None
    assert snapshot["lifecycle_state"] == "quarantined"
    assert snapshot["consecutive_failures"] >= 3
    assert snapshot["last_failure_kind"] == "runtime_failure"


def test_plugin_host_state_reporting_is_visible_to_runtime(tmp_path, clean_plugin_hosts):
    from AINDY.platform_layer.health_service import get_readiness_report
    from AINDY.platform_layer.plugin_host import start_plugin_host

    plugin_dir = _write_plugin(
        tmp_path,
        "safe_node",
        """
def handler(state, context):
    return {"status": "SUCCESS"}
""",
    )

    start_plugin_host(
        name="inventory-plugin",
        handler="safe_node:handler",
        plugin_root=plugin_dir,
        owner_class="external-third-party",
        granted_capabilities=["memory.read"],
        resource_access={"environment": {"secret_injection": "none"}},
    )

    status_code, payload = get_readiness_report()
    hosts = payload["checks"]["plugin_hosts"]

    assert status_code == 200
    assert hosts["present"] is True
    assert hosts["overall_status"] == "ok"
    assert hosts["hosts"][0]["name"] == "inventory-plugin"
    assert hosts["hosts"][0]["granted_capabilities"] == ["memory.read"]
    assert hosts["hosts"][0]["resource_access"]["environment"]["secret_injection"] == "none"
    assert "plugin host boundary" in hosts["operator_note"]


def test_readiness_fails_when_plugin_host_is_quarantined(tmp_path, clean_plugin_hosts, monkeypatch):
    from AINDY.platform_layer import plugin_host as plugin_host_module
    from AINDY.platform_layer.health_service import get_readiness_report
    from AINDY.platform_layer.plugin_host import start_plugin_host
    from AINDY.config import settings

    plugin_dir = _write_plugin(
        tmp_path,
        "safe_node",
        """
def handler(state, context):
    return {"status": "SUCCESS"}
""",
    )

    start_plugin_host(
        name="quarantined-plugin",
        handler="safe_node:handler",
        plugin_root=plugin_dir,
        owner_class="external-third-party",
        granted_capabilities=[],
    )
    host = plugin_host_module._HOSTS["quarantined-plugin"]
    host.state = "quarantined"
    host.quarantined_until = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    monkeypatch.setattr(settings, "TESTING", False)
    monkeypatch.setattr(settings, "TEST_MODE", False)
    monkeypatch.setattr(settings, "ENV", "development")

    status_code, payload = get_readiness_report()

    assert status_code == 503
    assert payload["checks"]["plugin_hosts_overall_status"] == "unavailable"
    assert "plugin_hosts" in payload["required_failures"]
