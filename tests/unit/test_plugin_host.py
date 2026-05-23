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
    assert snapshot["runner_type"] == "insecure_dev_subprocess"
    assert snapshot["runner"]["execution_boundary"] == "subprocess-json-rpc"
    assert snapshot["runner"]["isolation_claim"] == "none"
    assert snapshot["resource_limits"]["enforcement"] == "none"
    assert snapshot["resource_limits"]["memory_limit"] is None
    assert snapshot["sandbox_attestation"]["runner_type"] == "insecure_dev_subprocess"
    assert snapshot["sandbox_attestation"]["execution_model_class"] == "isolated-externalized"
    assert snapshot["sandbox_attestation"]["assurance_class"] == "insecure-dev"
    assert snapshot["sandbox_attestation"]["isolation_class"] == "insecure-dev-subprocess"
    assert snapshot["sandbox_attestation"]["certification"]["certification_tier"] == "contained-process-certified"
    assert snapshot["sandbox_attestation"]["requested_hardening_controls"] == []
    assert snapshot["sandbox_attestation"]["active_hardening_controls"] == []
    assert snapshot["sandbox_attestation"]["verified_hardening_controls"] == []
    assert snapshot["sandbox_attestation"]["effective_resource_limits"]["enforcement"] == "none"
    assert snapshot["sandbox_attestation"]["launch_attestation"]["status"] == "not-applicable"
    assert snapshot["sandbox_attestation"]["post_launch_verification"]["status"] == "not_applicable"
    assert snapshot["sandbox_attestation"]["mount_isolation"]["artifact_mount"]["active"] == "host-process"
    assert snapshot["sandbox_attestation"]["network_isolation"]["boundary"]["active"] == "host-process"
    assert snapshot["sandbox_attestation"]["runtime_identity"]["verification"] == "missing-reference"
    assert snapshot["sandbox_attestation"]["runtime_identity"]["trust_chain"]["verification_status"] == "missing-reference"
    assert snapshot["provenance"]["module_name"] == "safe_node"

    assert shutdown_plugin_host("hosted-plugin") is True
    stopped = get_plugin_host("hosted-plugin")
    assert stopped is not None
    assert stopped["lifecycle_state"] == "stopped"
    assert stopped["healthy"] is False
    assert stopped["pid"] is None
    assert stopped["runner_type"] == "insecure_dev_subprocess"


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
        resource_access={
            "environment": {"secret_injection": "none"},
            "network": {"default": "deny", "capability_required": "outbound.http"},
            "filesystem": {"default": "read-only-approved-roots", "writes": "deny"},
        },
        provenance={
            "extension_id": "vendor.inventory-plugin",
            "version": "1.0.0",
            "source_type": "external-plugin-artifact",
            "verification": "declared-and-verified",
            "integrity": {"algorithm": "sha256", "value": "a" * 64},
        },
    )

    status_code, payload = get_readiness_report()
    hosts = payload["checks"]["plugin_hosts"]

    assert status_code == 200
    assert hosts["present"] is True
    assert hosts["overall_status"] == "ok"
    assert hosts["runner_types_present"] == ["insecure_dev_subprocess"]
    assert hosts["sandbox_attestation"]["covered_execution_model_class"] == "isolated-externalized"
    assert hosts["sandbox_attestation"]["covered_surface_ids"] == [
        "dynamic-plugin-node:first-party-app",
        "dynamic-plugin-node:external-third-party",
    ]
    assert hosts["sandbox_attestation"]["assurance_classes_present"] == ["insecure-dev"]
    assert hosts["sandbox_attestation"]["certification_tiers_present"] == ["contained-process-certified"]
    assert hosts["sandbox_attestation"]["post_launch_verification_statuses_present"] == ["not_applicable"]
    assert hosts["hosts"][0]["name"] == "inventory-plugin"
    assert hosts["hosts"][0]["runner_type"] == "insecure_dev_subprocess"
    assert hosts["hosts"][0]["granted_capabilities"] == ["memory.read"]
    assert hosts["hosts"][0]["resource_access"]["environment"]["secret_injection"] == "none"
    assert hosts["hosts"][0]["resource_limits"]["enforcement"] == "none"
    assert hosts["hosts"][0]["sandbox_attestation"]["isolation_class"] == "insecure-dev-subprocess"
    assert hosts["hosts"][0]["sandbox_attestation"]["provenance_status"]["verification"] == "declared-and-verified"
    assert hosts["hosts"][0]["sandbox_attestation"]["network_policy"]["capability_required"] == "outbound.http"
    assert hosts["hosts"][0]["sandbox_attestation"]["filesystem_policy"]["writes"] == "deny"
    assert hosts["sandbox_attestation"]["isolation_classes_present"] == ["insecure-dev-subprocess"]
    assert hosts["sandbox_attestation"]["hosts"][0]["runner_type"] == "insecure_dev_subprocess"
    assert hosts["sandbox_attestation"]["hosts"][0]["execution_model_class"] == "isolated-externalized"
    assert hosts["sandbox_attestation"]["hosts"][0]["assurance_class"] == "insecure-dev"
    assert hosts["sandbox_attestation"]["hosts"][0]["certification"]["certification_tier"] == "contained-process-certified"
    assert hosts["sandbox_attestation"]["hosts"][0]["requested_hardening_controls"] == []
    assert hosts["sandbox_attestation"]["hosts"][0]["verified_hardening_controls"] == []
    assert hosts["sandbox_attestation"]["hosts"][0]["launch_attestation"]["status"] == "not-applicable"
    assert hosts["sandbox_attestation"]["hosts"][0]["post_launch_verification"]["status"] == "not_applicable"
    assert hosts["sandbox_attestation"]["hosts"][0]["mount_isolation"]["artifact_mount"]["active"] == "host-process"
    assert hosts["sandbox_attestation"]["hosts"][0]["network_isolation"]["deny_by_default"] is True
    assert hosts["sandbox_attestation"]["hosts"][0]["runtime_identity"]["verification"] == "missing-reference"
    assert hosts["sandbox_attestation"]["hosts"][0]["runtime_identity"]["trust_chain"]["verification_status"] == "missing-reference"
    assert hosts["sandbox_attestation"]["hosts"][0]["network_policy"]["default"] == "deny"
    assert hosts["sandbox_attestation"]["hosts"][0]["filesystem_policy"]["default"] == "read-only-approved-roots"
    assert "plugin host boundary" in hosts["operator_note"]
    assert "not a sandbox" in hosts["operator_note"]


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


def test_external_third_party_plugin_host_rejects_insecure_runner_in_distributed_profile(
    tmp_path, clean_plugin_hosts, monkeypatch
):
    from AINDY.config import settings
    from AINDY.platform_layer.deployment_contract import (
        get_api_runtime_state,
        publish_api_runtime_state,
    )
    from AINDY.platform_layer.plugin_host import start_plugin_host

    plugin_dir = _write_plugin(
        tmp_path,
        "safe_node",
        """
def handler(state, context):
    return {"status": "SUCCESS"}
""",
    )
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", "distributed-api")
    monkeypatch.setattr(settings, "EXECUTION_MODE", "distributed")
    monkeypatch.setattr(settings, "REDIS_URL", "redis://example")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_SANDBOX_RUNNER", "insecure_dev_subprocess")
    original_state = get_api_runtime_state()
    try:
        publish_api_runtime_state(
            process_role="api",
            deployment_profile="distributed-api",
            deployment_profile_source="AINDY_DEPLOYMENT_PROFILE",
        )

        with pytest.raises(RuntimeError, match="not allowed under deployment profile 'distributed-api' with runner insecure_dev_subprocess"):
            start_plugin_host(
                name="unsafe-third-party-plugin",
                handler="safe_node:handler",
                plugin_root=plugin_dir,
                owner_class="external-third-party",
                granted_capabilities=[],
            )
    finally:
        publish_api_runtime_state(**original_state)


def test_external_third_party_plugin_host_rejects_container_runner_in_hostile_profile(
    tmp_path, clean_plugin_hosts, monkeypatch
):
    from AINDY.config import settings
    from AINDY.platform_layer.deployment_contract import (
        get_api_runtime_state,
        publish_api_runtime_state,
    )
    from AINDY.platform_layer.plugin_host import start_plugin_host

    plugin_dir = _write_plugin(
        tmp_path,
        "safe_node",
        """
def handler(state, context):
    return {"status": "SUCCESS"}
""",
    )
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", "hostile-third-party")
    monkeypatch.setattr(settings, "EXECUTION_MODE", "distributed")
    monkeypatch.setattr(settings, "REDIS_URL", "redis://example")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_SANDBOX_RUNNER", "containerized_oci")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_IMAGE", "ghcr.io/example/aindy-runtime:test")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_IMAGE_DIGEST", "sha256:" + ("d" * 64))
    original_state = get_api_runtime_state()
    try:
        publish_api_runtime_state(
            process_role="api",
            deployment_profile="hostile-third-party",
            deployment_profile_source="AINDY_DEPLOYMENT_PROFILE",
        )

        with pytest.raises(RuntimeError, match="Hostile third-party mode rejects container-only and development runners|requires AINDY_PLUGIN_SANDBOX_RUNNER=strong_sandbox_vm"):
            start_plugin_host(
                name="hostile-container-plugin",
                handler="safe_node:handler",
                plugin_root=plugin_dir,
                owner_class="external-third-party",
                granted_capabilities=[],
            )
    finally:
        publish_api_runtime_state(**original_state)
