from __future__ import annotations

import json
import queue
from pathlib import Path

import pytest

from tests.helpers_plugin_artifacts import build_plugin_artifact


pytestmark = pytest.mark.runtime_only


@pytest.fixture
def clean_dynamic_runtime_state():
    from AINDY.platform_layer import event_service, node_registry
    from AINDY.platform_layer.plugin_host import reset_plugin_hosts
    from AINDY.runtime import flow_registry
    from AINDY.runtime.flow_engine import FLOW_REGISTRY, NODE_REGISTRY

    original_node_registry = dict(NODE_REGISTRY)
    original_flow_registry = dict(FLOW_REGISTRY)
    original_dynamic_nodes = dict(node_registry._DYNAMIC_NODE_META)
    original_dynamic_flows = dict(flow_registry._DYNAMIC_META)
    original_subscriptions = dict(event_service._SUBSCRIPTIONS)
    try:
        reset_plugin_hosts()
        node_registry._DYNAMIC_NODE_META.clear()
        flow_registry._DYNAMIC_META.clear()
        event_service._SUBSCRIPTIONS.clear()
        yield
    finally:
        reset_plugin_hosts()
        NODE_REGISTRY.clear()
        NODE_REGISTRY.update(original_node_registry)
        FLOW_REGISTRY.clear()
        FLOW_REGISTRY.update(original_flow_registry)
        node_registry._DYNAMIC_NODE_META.clear()
        node_registry._DYNAMIC_NODE_META.update(original_dynamic_nodes)
        flow_registry._DYNAMIC_META.clear()
        flow_registry._DYNAMIC_META.update(original_dynamic_flows)
        event_service._SUBSCRIPTIONS.clear()
        event_service._SUBSCRIPTIONS.update(original_subscriptions)


def _artifact(
    tmp_path: Path,
    *,
    module_name: str,
    extension_id: str,
    source: str,
) -> dict[str, object]:
    return build_plugin_artifact(
        tmp_path,
        module_name=module_name,
        extension_id=extension_id,
        source=source,
    )


def _certification_result(
    check_id: str,
    *,
    status: str,
    detail: str,
) -> dict[str, str]:
    return {
        "check_id": check_id,
        "status": status,
        "detail": detail,
    }


class _FakeReadablePipe:
    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()

    def put_line(self, line: str) -> None:
        self._queue.put(line)

    def readline(self) -> str:
        return self._queue.get()


class _FakeWritablePipe:
    def __init__(self, on_line) -> None:
        self._on_line = on_line
        self._buffer = ""

    def write(self, chunk: str) -> int:
        self._buffer += chunk
        return len(chunk)

    def flush(self) -> None:
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line:
                self._on_line(line)


class _FakeContainerHostProcess:
    def __init__(self, args, **kwargs) -> None:
        self.args = list(args)
        self.kwargs = kwargs
        self.pid = 9001
        self.returncode = None
        self.stdout = _FakeReadablePipe()
        self.stderr = _FakeReadablePipe()
        self.stdin = _FakeWritablePipe(self._handle_line)

    def _handle_line(self, raw: str) -> None:
        payload = json.loads(raw)
        command = payload["command"]
        if command == "start":
            response = {
                "ok": True,
                "provenance": {
                    "module_name": "container_plugin",
                    "source_path": payload.get("plugin_root"),
                },
            }
        elif command == "heartbeat":
            response = {"ok": True}
        elif command == "execute":
            response = {"ok": True, "result": {"status": "SUCCESS", "output_patch": {"runner": "container"}}}
        elif command == "shutdown":
            response = {"ok": True}
            self.returncode = 0
        else:
            response = {"ok": False, "error": f"unsupported command {command!r}"}
        self.stdout.put_line(json.dumps(response) + "\n")

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = 0

    def kill(self) -> None:
        self.returncode = 1

    def wait(self, timeout=None) -> int:
        _ = timeout
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def test_insecure_dev_runner_certification_suite(tmp_path, clean_dynamic_runtime_state):
    from AINDY.platform_layer.node_registry import get_dynamic_node, register_external_node
    from AINDY.platform_layer.plugin_host import plugin_host_inventory
    from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile
    from AINDY.runtime.flow_engine import NODE_REGISTRY

    report: dict[str, object] = {
        "runner_type": "insecure_dev_subprocess",
        "results": [],
    }

    blocked_import = _artifact(
        tmp_path,
        module_name="blocked_import_node",
        extension_id="vendor.cert.blocked-import",
        source="""
from AINDY.config import settings

def handler(state, context):
    return {"status": "SUCCESS", "output_patch": {"env": settings.ENV}}
""",
    )
    with pytest.raises(ValueError, match="plugin runtime import blocked"):
        register_external_node(
            "cert-blocked-import",
            "plugin",
            blocked_import["handler"],
            artifact_path=str(blocked_import["artifact_root"]),
            provenance=blocked_import["provenance"],
            overwrite=True,
        )
    report["results"].append(
        _certification_result(
            "blocked_internal_imports",
            status="passed",
            detail="registration rejected internal runtime import",
        )
    )

    blocked_write = _artifact(
        tmp_path,
        module_name="blocked_write_node",
        extension_id="vendor.cert.blocked-write",
        source="""
from pathlib import Path

def handler(state, context):
    Path(__file__).with_name("forbidden-write.txt").write_text("overwrite", encoding="utf-8")
    return {"status": "SUCCESS"}
""",
    )
    register_external_node(
        "cert-blocked-write",
        "plugin",
        blocked_write["handler"],
        artifact_path=str(blocked_write["artifact_root"]),
        provenance=blocked_write["provenance"],
        overwrite=True,
    )
    blocked_write_result = NODE_REGISTRY["cert-blocked-write"]({}, {"user_id": "u-1", "run_id": "r-1"})
    assert blocked_write_result["status"] == "FAILURE"
    assert "Filesystem write blocked" in blocked_write_result["error"]
    report["results"].append(
        _certification_result(
            "blocked_filesystem_writes",
            status="passed",
            detail=blocked_write_result["error"],
        )
    )

    blocked_network = _artifact(
        tmp_path,
        module_name="blocked_network_node",
        extension_id="vendor.cert.blocked-network",
        source="""
import socket

def handler(state, context):
    socket.create_connection(("127.0.0.1", 9), timeout=0.1)
    return {"status": "SUCCESS"}
""",
    )
    register_external_node(
        "cert-blocked-network",
        "plugin",
        blocked_network["handler"],
        artifact_path=str(blocked_network["artifact_root"]),
        provenance=blocked_network["provenance"],
        overwrite=True,
    )
    blocked_network_result = NODE_REGISTRY["cert-blocked-network"]({}, {"user_id": "u-1", "run_id": "r-1"})
    assert blocked_network_result["status"] == "FAILURE"
    assert "outbound.http" in blocked_network_result["error"]
    report["results"].append(
        _certification_result(
            "blocked_out_of_policy_network_access",
            status="passed",
            detail=blocked_network_result["error"],
        )
    )

    denied_capability = _artifact(
        tmp_path,
        module_name="denied_capability_node",
        extension_id="vendor.cert.denied-capability",
        source="""
from AINDY.platform_layer.extension_runtime_api import memory_read

def handler(state, context):
    memory_read(query="forbidden")
    return {"status": "SUCCESS"}
""",
    )
    register_external_node(
        "cert-denied-capability",
        "plugin",
        denied_capability["handler"],
        artifact_path=str(denied_capability["artifact_root"]),
        provenance=denied_capability["provenance"],
        overwrite=True,
    )
    denied_capability_result = NODE_REGISTRY["cert-denied-capability"]({}, {"user_id": "u-1", "run_id": "r-1"})
    assert denied_capability_result["status"] == "FAILURE"
    assert "not granted" in denied_capability_result["error"]
    report["results"].append(
        _certification_result(
            "denied_capabilities",
            status="passed",
            detail=denied_capability_result["error"],
        )
    )

    quarantined = _artifact(
        tmp_path,
        module_name="quarantine_node",
        extension_id="vendor.cert.quarantine",
        source="""
def handler(state, context):
    return {"status": "BOGUS"}
""",
    )
    register_external_node(
        "cert-quarantine",
        "plugin",
        quarantined["handler"],
        artifact_path=str(quarantined["artifact_root"]),
        provenance=quarantined["provenance"],
        overwrite=True,
    )
    quarantined_result = NODE_REGISTRY["cert-quarantine"]({}, {"user_id": "u-1", "run_id": "r-1"})
    quarantined_host = get_dynamic_node("cert-quarantine")["plugin_host"]
    assert quarantined_result["status"] == "FAILURE"
    assert quarantined_host["lifecycle_state"] == "quarantined"
    report["results"].append(
        _certification_result(
            "quarantine_behavior",
            status="passed",
            detail=f"host entered {quarantined_host['lifecycle_state']}",
        )
    )

    unverifiable = _artifact(
        tmp_path,
        module_name="bad_provenance_node",
        extension_id="vendor.cert.bad-provenance",
        source="""
def handler(state, context):
    return {"status": "SUCCESS"}
""",
    )
    with pytest.raises(ValueError, match="failed integrity verification"):
        register_external_node(
            "cert-bad-provenance",
            "plugin",
            unverifiable["handler"],
            artifact_path=str(unverifiable["artifact_root"]),
            provenance={
                "extension_id": "vendor.cert.bad-provenance",
                "version": "1.0.0",
                "source_type": "external-plugin-artifact",
                "source_ref": str(unverifiable["artifact_root"]),
                "integrity": {"algorithm": "sha256", "value": "0" * 64},
            },
            overwrite=True,
        )
    report["results"].append(
        _certification_result(
            "provenance_rejection",
            status="passed",
            detail="registration rejected unverifiable provenance",
        )
    )

    inventory = plugin_host_inventory(probe=False)
    live_runner_metadata = get_dynamic_node("cert-blocked-write")["plugin_host"]["runner"]
    profile = sandbox_certification_profile(
        runner_type="insecure_dev_subprocess",
        runner_metadata=live_runner_metadata,
    )
    assert inventory["default_runner_type"] == "insecure_dev_subprocess"
    assert profile["resource_limit_enforcement"] == "none"
    assert profile["certification_tier"] == "contained-process-certified"
    assert profile["tier_status"] == "certified"
    assert profile["validation_layers"]["shared_worker_policy"]["status"] == "certifiable-shared-worker-policy"
    assert profile["validation_layers"]["runner_assurance"]["layer"] == "contained-process-certified"
    assert profile["validation_layers"]["runner_assurance"]["status"] == "not_applicable_for_stronger_assurance"
    report["results"].append(
        _certification_result(
            "hard_resource_limits",
            status="not_certified_for_runner",
            detail="insecure_dev_subprocess reports no hard resource limit enforcement",
        )
    )

    assert [entry["check_id"] for entry in report["results"]] == [
        "blocked_internal_imports",
        "blocked_filesystem_writes",
        "blocked_out_of_policy_network_access",
        "denied_capabilities",
        "quarantine_behavior",
        "provenance_rejection",
        "hard_resource_limits",
    ]
    assert [entry["status"] for entry in report["results"]] == [
        "passed",
        "passed",
        "passed",
        "passed",
        "passed",
        "passed",
        "not_certified_for_runner",
    ]
    assert any(
        check["id"] == "hard_resource_limits"
        and check["runner_status"] == "not_certifiable_for_runner"
        for check in profile["checks"]
    )


def test_container_runner_certification_suite_reports_attested_hardening(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.config import settings
    from AINDY.platform_layer import sandbox_runner
    from AINDY.platform_layer.plugin_host import execute_plugin_host, shutdown_plugin_host, start_plugin_host
    from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile

    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_IMAGE", "ghcr.io/example/aindy-runtime:test")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_IMAGE_DIGEST", "sha256:" + ("d" * 64))
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_RUNTIME", "docker")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_WRITABLE_TMP", True)
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_NO_NEW_PRIVILEGES", True)
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_DROP_ALL_CAPABILITIES", True)
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_DISABLE_NETWORK", True)
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_READ_ONLY_ROOTFS", True)
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_PIDS_LIMIT", 64)
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_MEMORY_LIMIT", "512m")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_CPU_LIMIT", 1.0)
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_CPU_SHARES", 256)
    monkeypatch.setattr(sandbox_runner.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(sandbox_runner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sandbox_runner.subprocess, "Popen", _FakeContainerHostProcess)

    plugin_dir = tmp_path / "plugins" / "nodes"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    snapshot = start_plugin_host(
        name="cert-container-runner",
        handler="container_plugin:handler",
        plugin_root=plugin_dir,
        owner_class="external-third-party",
        granted_capabilities=[],
        runner_type="containerized_oci",
    )
    result = execute_plugin_host(
        name="cert-container-runner",
        state={"value": 1},
        runtime_context={"user_id": "u-1"},
    )
    profile = sandbox_certification_profile(
        runner_type="containerized_oci",
        runner_metadata=snapshot["runner"],
    )

    report = {
        "runner_type": snapshot["runner_type"],
        "results": [
            _certification_result(
                "runner_identity_reporting",
                status="passed",
                detail=snapshot["sandbox_attestation"]["isolation_class"],
            ),
            _certification_result(
                "hard_resource_limits",
                status="passed",
                detail=snapshot["resource_limits"]["enforcement"],
            ),
        ],
    }

    assert result["status"] == "SUCCESS"
    assert result["output_patch"]["runner"] == "container"
    assert snapshot["sandbox_attestation"]["isolation_class"] == "containerized-hardened-sandbox"
    assert snapshot["sandbox_attestation"]["certification"]["certification_tier"] == "container-sandbox-certified"
    assert snapshot["resource_limits"]["enforcement"] == "container-runtime-hard-limits"
    assert "no_new_privileges" in snapshot["sandbox_attestation"]["active_hardening_controls"]
    assert profile["certification_tier"] == "container-sandbox-certified"
    assert profile["tier_status"] == "certified"
    assert profile["validation_layers"]["runner_assurance"]["layer"] == "container-sandbox-certified"
    assert profile["validation_layers"]["runner_assurance"]["status"] == "passed"
    assert {
        check["check_id"]: check["status"]
        for check in profile["validation_layers"]["runner_assurance"]["checks"]
    } == {
        "runner_class_verification": "passed",
        "verified_runtime_identity": "passed",
        "verified_runtime_trust_chain": "passed",
        "verified_resource_limit_mode": "passed",
        "verified_isolation_reporting": "passed",
    }
    assert any(
        check["id"] == "hard_resource_limits"
        and check["runner_status"] == "certifiable"
        and check["effective_enforcement"] == "container-runtime-hard-limits"
        for check in profile["checks"]
    )
    assert [entry["status"] for entry in report["results"]] == ["passed", "passed"]

    assert shutdown_plugin_host("cert-container-runner") is True


def test_container_runner_certification_unavailability_is_explicit(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.config import settings
    from AINDY.platform_layer import sandbox_runner
    from AINDY.platform_layer.plugin_host import start_plugin_host
    from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile
    from AINDY.platform_layer.sandbox_runner import ContainerizedOciSandboxRunner

    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_IMAGE", "")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_RUNTIME", "docker")
    monkeypatch.setattr(sandbox_runner.shutil, "which", lambda _: None)

    profile = sandbox_certification_profile(
        runner_type="containerized_oci",
        runner_metadata=ContainerizedOciSandboxRunner().metadata(),
    )

    plugin_dir = tmp_path / "plugins" / "nodes"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match="AINDY_PLUGIN_CONTAINER_IMAGE"):
        start_plugin_host(
            name="cert-container-unavailable",
            handler="missing:handler",
            plugin_root=plugin_dir,
            owner_class="external-third-party",
            granted_capabilities=[],
            runner_type="containerized_oci",
        )

    assert any(
        check["id"] == "hard_resource_limits"
        and check["runner_status"] == "not_certifiable_for_runner"
        for check in profile["checks"]
    )
    assert profile["certification_tier"] is None
    assert "launch_attestation.status" in profile["missing_tier_requirements"]
    assert profile["validation_layers"]["runner_assurance"]["layer"] == "container-sandbox-certified"
    assert profile["validation_layers"]["runner_assurance"]["status"] == "not_certified"


def test_strong_runner_certification_tier_requires_verified_launch_attestation():
    from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile

    uncertified = sandbox_certification_profile(
        runner_type="strong_sandbox_vm",
        runner_metadata={
            "runner_type": "strong_sandbox_vm",
            "assurance_class": "strong-sandbox-tier",
            "execution_boundary": "vm-stdio-json-rpc",
            "isolation_claim": "vm-boundary",
            "assurance_properties": {
                "boundary_type": "dedicated-vm-sandbox",
                "process_separation_model": "vm-kernel-boundary",
                "mount_mediation_model": "sandbox-launcher-mediated-read-only",
                "network_mediation_model": "sandbox-launcher-deny-default",
                "runtime_identity_model": "pinned-sandbox-runtime",
                "session_verification_model": "launch-plus-post-launch-runtime-probe",
            },
            "resource_limits": {
                "enforcement": "sandbox-runtime-hard-limits",
            },
            "hardening_controls": {
                "active_controls": [],
            },
            "launch_attestation": {
                "status": "not-started",
                "backend_identity": {"verified": False},
                "runtime_identity": {"verified": False},
                "mount_mode": {"verified": False},
                "resource_limit_mode": {"verified": False},
                "hardening_profiles": {"verified_controls": []},
            },
        },
        platform_matrix={
            "current_environment": {
                "support_levels": {
                    "strong_sandbox": {"support": "supported"},
                }
            }
        },
        post_launch_verification={},
    )

    assert uncertified["certification_tier"] is None
    assert uncertified["tier_status"] == "not_certified_for_runner"
    assert "launch_attestation.status" in uncertified["missing_tier_requirements"]
    assert "post_launch_verification.verification_scope" in uncertified["missing_tier_requirements"]
    assert "post_launch_verification.checked_at" in uncertified["missing_tier_requirements"]
    assert "post_launch_verification.worker_instance_id" in uncertified["missing_tier_requirements"]
    assert "verified.backend_identity" in uncertified["missing_tier_requirements"]
    assert "hardening_controls.active_controls" in uncertified["missing_tier_requirements"]
    assert "verified.read_only_plugin_mount" in uncertified["missing_tier_requirements"]
    assert "post_launch_verification.status" in uncertified["missing_tier_requirements"]
    assert "post_launch_verified.mount_network_state.artifact_write_blocked" in uncertified["missing_tier_requirements"]
    assert {
        reason["category"]
        for reason in uncertified["uncertified_reasons"]
    } >= {"launch_evidence", "live_evidence", "hardening_state", "runtime_trust"}
    assert uncertified["validation_layers"]["runner_assurance"]["layer"] == "strong-sandbox-certified"
    assert uncertified["validation_layers"]["runner_assurance"]["status"] == "not_certified"


def test_strong_runner_certification_tier_granted_only_with_verified_strong_evidence():
    from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile

    profile = sandbox_certification_profile(
        runner_type="strong_sandbox_vm",
        runner_metadata={
            "runner_type": "strong_sandbox_vm",
            "assurance_class": "strong-sandbox-tier",
            "execution_boundary": "vm-stdio-json-rpc",
            "isolation_claim": "vm-boundary",
            "assurance_properties": {
                "boundary_type": "dedicated-vm-sandbox",
                "process_separation_model": "vm-kernel-boundary",
                "mount_mediation_model": "sandbox-launcher-mediated-read-only",
                "network_mediation_model": "sandbox-launcher-deny-default",
                "runtime_identity_model": "pinned-sandbox-runtime",
                "session_verification_model": "launch-plus-post-launch-runtime-probe",
            },
            "resource_limits": {
                "enforcement": "sandbox-runtime-hard-limits",
            },
            "runtime_identity": {
                "pinned": True,
                "trust_chain": {
                    "accepted_for_hostile_profiles": True,
                    "verification_status": "trusted-signed-pinned-compatible",
                },
            },
            "hardening_controls": {
                "active_controls": [
                    "dedicated_vm_boundary",
                    "read_only_plugin_mount",
                    "launcher_network_deny_default",
                    "launcher_host_path_denial",
                    "minimal_environment",
                    "sandbox_runtime_limits",
                ]
            },
            "launch_attestation": {
                "status": "launch-observed",
                "backend_identity": {"verified": True},
                "runtime_identity": {"verified": True},
                "mount_mode": {"verified": True},
                "resource_limit_mode": {"verified": True},
                "hardening_profiles": {
                    "verified_controls": [
                        "read_only_plugin_mount",
                        "launcher_network_deny_default",
                        "launcher_host_path_denial",
                    ],
                },
                "assurance_properties": {
                    "active": {
                        "boundary_type": "dedicated-vm-sandbox",
                        "process_separation_model": "vm-kernel-boundary",
                        "mount_mediation_model": "sandbox-launcher-mediated-read-only",
                        "network_mediation_model": "sandbox-launcher-deny-default",
                        "runtime_identity_model": "pinned-sandbox-runtime",
                        "session_verification_model": "launch-plus-post-launch-runtime-probe",
                    },
                    "verified": {
                        "boundary_type": True,
                        "process_separation_model": True,
                        "mount_mediation_model": True,
                        "network_mediation_model": True,
                        "runtime_identity_model": True,
                        "session_verification_model": True,
                    },
                },
            },
        },
        platform_matrix={
            "current_environment": {
                "support_levels": {
                    "strong_sandbox": {"support": "supported"},
                }
            }
        },
        post_launch_verification={
            "status": "passed",
            "verification_scope": "live-worker-self-report-over-authenticated-rpc",
            "checked_at": "2026-05-22T00:00:00+00:00",
            "worker_instance_id": "worker-strong-1",
            "verified_fields": [
                "session_continuity.worker_instance_id",
                "session_continuity.sandbox_instance_id",
                "isolation_state.import_guard_active",
                "isolation_state.filesystem_guard_active",
                "isolation_state.network_guard_active",
                "boundary_metadata.runtime_api_channel_hidden",
                "mount_network_state.artifact_read_access",
                "mount_network_state.artifact_write_blocked",
                "mount_network_state.writable_temp_scope",
                "mount_network_state.host_path_access_blocked",
                "mount_network_state.network_policy.socket_guard_active",
                "mount_network_state.network_policy.deny_by_default_outbound",
                "mount_network_state.network_policy.private_target_blocking",
                "mount_network_state.network_policy.expected_boundary_mode",
            ],
        },
    )

    assert profile["certification_tier"] == "strong-sandbox-certified"
    assert profile["tier_status"] == "certified"
    assert profile["missing_tier_requirements"] == []
    assert profile["uncertified_reasons"] == []
    assert profile["validation_layers"]["runner_assurance"]["layer"] == "strong-sandbox-certified"
    assert profile["validation_layers"]["runner_assurance"]["status"] == "passed"
    assert {
        check["check_id"]: check["status"]
        for check in profile["validation_layers"]["runner_assurance"]["checks"]
    } == {
        "runner_class_verification": "passed",
        "verified_runtime_identity": "passed",
        "verified_runtime_trust_chain": "passed",
        "verified_hardening_profile_state": "passed",
        "verified_stronger_isolation_reporting": "passed",
        "verified_resource_limit_mode": "passed",
        "verified_strong_boundary_properties": "passed",
        "live_session_continuity": "passed",
        "live_isolation_state_probe": "passed",
        "live_mount_network_policy_probe": "passed",
        "fail_closed_unavailability": "not_applicable",
    }


def test_strong_runner_certification_tier_rejected_on_unsupported_platform():
    from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile

    profile = sandbox_certification_profile(
        runner_type="strong_sandbox_vm",
        runner_metadata={
            "runner_type": "strong_sandbox_vm",
            "assurance_class": "strong-sandbox-tier",
            "execution_boundary": "vm-stdio-json-rpc",
            "isolation_claim": "vm-boundary",
            "assurance_properties": {
                "boundary_type": "dedicated-vm-sandbox",
                "process_separation_model": "vm-kernel-boundary",
                "mount_mediation_model": "sandbox-launcher-mediated-read-only",
                "network_mediation_model": "sandbox-launcher-deny-default",
                "runtime_identity_model": "pinned-sandbox-runtime",
                "session_verification_model": "launch-plus-post-launch-runtime-probe",
            },
            "resource_limits": {
                "enforcement": "sandbox-runtime-hard-limits",
            },
            "runtime_identity": {
                "pinned": True,
                "trust_chain": {
                    "accepted_for_hostile_profiles": True,
                    "verification_status": "trusted-signed-pinned-compatible",
                },
            },
            "hardening_controls": {
                "active_controls": [
                    "dedicated_vm_boundary",
                    "read_only_plugin_mount",
                    "launcher_network_deny_default",
                    "launcher_host_path_denial",
                    "minimal_environment",
                    "sandbox_runtime_limits",
                ]
            },
            "launch_attestation": {
                "status": "launch-observed",
                "backend_identity": {"verified": True},
                "runtime_identity": {"verified": True},
                "mount_mode": {"verified": True},
                "resource_limit_mode": {"verified": True},
                "hardening_profiles": {
                    "verified_controls": [
                        "read_only_plugin_mount",
                        "launcher_network_deny_default",
                        "launcher_host_path_denial",
                    ],
                },
                "assurance_properties": {
                    "active": {
                        "boundary_type": "dedicated-vm-sandbox",
                        "process_separation_model": "vm-kernel-boundary",
                        "mount_mediation_model": "sandbox-launcher-mediated-read-only",
                        "network_mediation_model": "sandbox-launcher-deny-default",
                        "runtime_identity_model": "pinned-sandbox-runtime",
                        "session_verification_model": "launch-plus-post-launch-runtime-probe",
                    },
                    "verified": {
                        "boundary_type": True,
                        "process_separation_model": True,
                        "mount_mediation_model": True,
                        "network_mediation_model": True,
                        "runtime_identity_model": True,
                        "session_verification_model": True,
                    },
                },
            },
        },
        platform_matrix={
            "current_environment": {
                "support_levels": {
                    "strong_sandbox": {"support": "unsupported"},
                }
            }
        },
        post_launch_verification={
            "status": "passed",
            "verification_scope": "live-worker-self-report-over-authenticated-rpc",
            "checked_at": "2026-05-22T00:00:00+00:00",
            "worker_instance_id": "worker-strong-1",
            "verified_fields": [
                "session_continuity.worker_instance_id",
                "session_continuity.sandbox_instance_id",
                "isolation_state.import_guard_active",
                "isolation_state.filesystem_guard_active",
                "isolation_state.network_guard_active",
                "boundary_metadata.runtime_api_channel_hidden",
                "mount_network_state.artifact_read_access",
                "mount_network_state.artifact_write_blocked",
                "mount_network_state.writable_temp_scope",
                "mount_network_state.host_path_access_blocked",
                "mount_network_state.network_policy.socket_guard_active",
                "mount_network_state.network_policy.deny_by_default_outbound",
                "mount_network_state.network_policy.private_target_blocking",
                "mount_network_state.network_policy.expected_boundary_mode",
            ],
        },
    )

    assert profile["certification_tier"] is None
    assert profile["tier_status"] == "not_certified_for_runner"
    assert "platform_support.strong_sandbox" in profile["missing_tier_requirements"]
    assert profile["validation_layers"]["runner_assurance"]["layer"] == "strong-sandbox-certified"
    assert profile["validation_layers"]["runner_assurance"]["status"] == "not_certified"


def test_strong_runner_validation_layer_reports_passed_assurance_checks():
    from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile

    profile = sandbox_certification_profile(
        runner_type="strong_sandbox_vm",
        runner_metadata={
            "runner_type": "strong_sandbox_vm",
            "assurance_class": "strong-sandbox-tier",
            "execution_boundary": "vm-stdio-json-rpc",
            "isolation_claim": "vm-boundary",
            "assurance_properties": {
                "boundary_type": "dedicated-vm-sandbox",
                "process_separation_model": "vm-kernel-boundary",
                "mount_mediation_model": "sandbox-launcher-mediated-read-only",
                "network_mediation_model": "sandbox-launcher-deny-default",
                "runtime_identity_model": "pinned-sandbox-runtime",
                "session_verification_model": "launch-plus-post-launch-runtime-probe",
            },
            "resource_limits": {
                "enforcement": "sandbox-runtime-hard-limits",
            },
            "runtime_identity": {
                "pinned": True,
                "trust_chain": {
                    "accepted_for_hostile_profiles": True,
                    "verification_status": "trusted-signed-pinned-compatible",
                },
            },
            "hardening_controls": {
                "active_controls": [
                    "dedicated_vm_boundary",
                    "read_only_plugin_mount",
                    "launcher_network_deny_default",
                    "launcher_host_path_denial",
                    "minimal_environment",
                    "sandbox_runtime_limits",
                ]
            },
            "launch_attestation": {
                "status": "launch-observed",
                "backend_identity": {"verified": True},
                "runtime_identity": {"verified": True},
                "mount_mode": {"verified": True},
                "resource_limit_mode": {"verified": True},
                "hardening_profiles": {
                    "verified_controls": [
                        "read_only_plugin_mount",
                        "launcher_network_deny_default",
                        "launcher_host_path_denial",
                    ],
                },
                "assurance_properties": {
                    "active": {
                        "boundary_type": "dedicated-vm-sandbox",
                        "process_separation_model": "vm-kernel-boundary",
                        "mount_mediation_model": "sandbox-launcher-mediated-read-only",
                        "network_mediation_model": "sandbox-launcher-deny-default",
                        "runtime_identity_model": "pinned-sandbox-runtime",
                        "session_verification_model": "launch-plus-post-launch-runtime-probe",
                    },
                    "verified": {
                        "boundary_type": True,
                        "process_separation_model": True,
                        "mount_mediation_model": True,
                        "network_mediation_model": True,
                        "runtime_identity_model": True,
                        "session_verification_model": True,
                    },
                },
            },
        },
        platform_matrix={
            "current_environment": {
                "support_levels": {
                    "strong_sandbox": {"support": "supported"},
                }
            }
        },
        post_launch_verification={
            "status": "passed",
            "verification_scope": "live-worker-self-report-over-authenticated-rpc",
            "checked_at": "2026-05-22T00:00:00+00:00",
            "worker_instance_id": "worker-strong-1",
            "verified_fields": [
                "session_continuity.worker_instance_id",
                "session_continuity.sandbox_instance_id",
                "isolation_state.import_guard_active",
                "isolation_state.filesystem_guard_active",
                "isolation_state.network_guard_active",
                "boundary_metadata.runtime_api_channel_hidden",
                "mount_network_state.artifact_read_access",
                "mount_network_state.artifact_write_blocked",
                "mount_network_state.writable_temp_scope",
                "mount_network_state.host_path_access_blocked",
                "mount_network_state.network_policy.socket_guard_active",
                "mount_network_state.network_policy.deny_by_default_outbound",
                "mount_network_state.network_policy.private_target_blocking",
                "mount_network_state.network_policy.expected_boundary_mode",
            ],
        },
    )

    assert profile["certification_tier"] == "strong-sandbox-certified"
    checks = {
        check["check_id"]: check["status"]
        for check in profile["validation_layers"]["runner_assurance"]["checks"]
    }
    assert checks == {
        "runner_class_verification": "passed",
        "verified_runtime_identity": "passed",
        "verified_runtime_trust_chain": "passed",
        "verified_hardening_profile_state": "passed",
        "verified_stronger_isolation_reporting": "passed",
        "verified_resource_limit_mode": "passed",
        "verified_strong_boundary_properties": "passed",
        "live_session_continuity": "passed",
        "live_isolation_state_probe": "passed",
        "live_mount_network_policy_probe": "passed",
        "fail_closed_unavailability": "not_applicable",
    }


def test_strong_runner_validation_layer_reports_unavailability_as_observable():
    from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile

    profile = sandbox_certification_profile(
        runner_type="strong_sandbox_vm",
        runner_metadata={
            "runner_type": "strong_sandbox_vm",
            "assurance_class": "strong-sandbox-tier",
            "execution_boundary": "vm-stdio-json-rpc",
            "isolation_claim": "vm-boundary",
            "resource_limits": {
                "enforcement": "unavailable",
            },
            "runtime_identity": {
                "pinned": False,
            },
            "hardening_controls": {
                "unsupported_controls": [{"control": "strong_sandbox_vm", "reason": "launcher missing"}],
            },
            "launch_attestation": {
                "status": "not-started",
                "backend_identity": {"verified": False},
                "runtime_identity": {"verified": False},
                "mount_mode": {"verified": False},
                "resource_limit_mode": {"verified": False},
            },
        },
        platform_matrix={
            "current_environment": {
                "support_levels": {
                    "strong_sandbox": {"support": "supported"},
                }
            }
        },
        post_launch_verification={},
    )

    checks = {
        check["check_id"]: check["status"]
        for check in profile["validation_layers"]["runner_assurance"]["checks"]
    }
    assert checks["fail_closed_unavailability"] == "observable"


def test_strong_runner_certification_rejects_container_grade_assurance_properties():
    from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile

    profile = sandbox_certification_profile(
        runner_type="strong_sandbox_vm",
        runner_metadata={
            "runner_type": "strong_sandbox_vm",
            "assurance_class": "strong-sandbox-tier",
            "execution_boundary": "vm-stdio-json-rpc",
            "isolation_claim": "vm-boundary",
            "assurance_properties": {
                "boundary_type": "shared-kernel-container-sandbox",
                "process_separation_model": "container-namespace-boundary",
                "mount_mediation_model": "container-runtime-bind-mount-read-only",
                "network_mediation_model": "container-runtime-network-policy",
                "runtime_identity_model": "pinned-oci-image",
                "session_verification_model": "launch-attestation-only",
            },
            "resource_limits": {
                "enforcement": "sandbox-runtime-hard-limits",
            },
            "runtime_identity": {
                "pinned": True,
                "trust_chain": {
                    "accepted_for_hostile_profiles": True,
                    "verification_status": "trusted-signed-pinned-compatible",
                },
            },
            "hardening_controls": {
                "active_controls": [
                    "dedicated_vm_boundary",
                    "read_only_plugin_mount",
                    "launcher_network_deny_default",
                    "launcher_host_path_denial",
                    "minimal_environment",
                    "sandbox_runtime_limits",
                ]
            },
            "launch_attestation": {
                "status": "launch-observed",
                "backend_identity": {"verified": True},
                "runtime_identity": {"verified": True},
                "mount_mode": {"verified": True},
                "resource_limit_mode": {"verified": True},
                "hardening_profiles": {
                    "verified_controls": [
                        "read_only_plugin_mount",
                        "launcher_network_deny_default",
                        "launcher_host_path_denial",
                    ],
                },
                "assurance_properties": {
                    "active": {
                        "boundary_type": "shared-kernel-container-sandbox",
                        "process_separation_model": "container-namespace-boundary",
                        "mount_mediation_model": "container-runtime-bind-mount-read-only",
                        "network_mediation_model": "container-runtime-network-policy",
                        "runtime_identity_model": "pinned-oci-image",
                        "session_verification_model": "launch-attestation-only",
                    },
                    "verified": {
                        "boundary_type": True,
                        "process_separation_model": True,
                        "mount_mediation_model": True,
                        "network_mediation_model": True,
                        "runtime_identity_model": True,
                        "session_verification_model": True,
                    },
                },
            },
        },
        platform_matrix={
            "current_environment": {
                "support_levels": {
                    "strong_sandbox": {"support": "supported"},
                }
            }
        },
        post_launch_verification={
            "status": "passed",
            "verification_scope": "live-worker-self-report-over-authenticated-rpc",
            "checked_at": "2026-05-22T00:00:00+00:00",
            "worker_instance_id": "worker-strong-1",
            "verified_fields": [
                "session_continuity.worker_instance_id",
                "session_continuity.sandbox_instance_id",
                "isolation_state.import_guard_active",
                "isolation_state.filesystem_guard_active",
                "isolation_state.network_guard_active",
                "boundary_metadata.runtime_api_channel_hidden",
            ],
        },
    )

    assert profile["certification_tier"] is None
    assert "assurance_properties.boundary_type" in profile["missing_tier_requirements"]
    assert "launch_attestation.assurance_properties.active.network_mediation_model" in profile["missing_tier_requirements"]
    checks = {
        check["check_id"]: check["status"]
        for check in profile["validation_layers"]["runner_assurance"]["checks"]
    }
    assert checks["verified_strong_boundary_properties"] == "failed"


def test_strong_runner_certification_reports_live_evidence_denial_reasons():
    from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile

    profile = sandbox_certification_profile(
        runner_type="strong_sandbox_vm",
        runner_metadata={
            "runner_type": "strong_sandbox_vm",
            "assurance_class": "strong-sandbox-tier",
            "execution_boundary": "vm-stdio-json-rpc",
            "isolation_claim": "vm-boundary",
            "assurance_properties": {
                "boundary_type": "dedicated-vm-sandbox",
                "process_separation_model": "vm-kernel-boundary",
                "mount_mediation_model": "sandbox-launcher-mediated-read-only",
                "network_mediation_model": "sandbox-launcher-deny-default",
                "runtime_identity_model": "pinned-sandbox-runtime",
                "session_verification_model": "launch-plus-post-launch-runtime-probe",
            },
            "resource_limits": {
                "enforcement": "sandbox-runtime-hard-limits",
            },
            "runtime_identity": {
                "pinned": True,
                "trust_chain": {
                    "accepted_for_hostile_profiles": True,
                    "verification_status": "trusted-signed-pinned-compatible",
                },
            },
            "hardening_controls": {
                "active_controls": [
                    "dedicated_vm_boundary",
                    "read_only_plugin_mount",
                    "launcher_network_deny_default",
                    "launcher_host_path_denial",
                    "minimal_environment",
                    "sandbox_runtime_limits",
                ]
            },
            "launch_attestation": {
                "status": "launch-observed",
                "backend_identity": {"verified": True},
                "runtime_identity": {"verified": True},
                "mount_mode": {"verified": True},
                "resource_limit_mode": {"verified": True},
                "hardening_profiles": {
                    "verified_controls": [
                        "read_only_plugin_mount",
                        "launcher_network_deny_default",
                        "launcher_host_path_denial",
                    ],
                },
                "assurance_properties": {
                    "active": {
                        "boundary_type": "dedicated-vm-sandbox",
                        "process_separation_model": "vm-kernel-boundary",
                        "mount_mediation_model": "sandbox-launcher-mediated-read-only",
                        "network_mediation_model": "sandbox-launcher-deny-default",
                        "runtime_identity_model": "pinned-sandbox-runtime",
                        "session_verification_model": "launch-plus-post-launch-runtime-probe",
                    },
                    "verified": {
                        "boundary_type": True,
                        "process_separation_model": True,
                        "mount_mediation_model": True,
                        "network_mediation_model": True,
                        "runtime_identity_model": True,
                        "session_verification_model": True,
                    },
                },
            },
        },
        platform_matrix={
            "current_environment": {
                "support_levels": {
                    "strong_sandbox": {"support": "supported"},
                }
            }
        },
        post_launch_verification={
            "status": "passed",
            "verification_scope": "live-worker-self-report-over-authenticated-rpc",
            "checked_at": "2026-05-22T00:00:00+00:00",
            "worker_instance_id": "worker-strong-1",
            "verified_fields": [
                "session_continuity.worker_instance_id",
                "session_continuity.sandbox_instance_id",
            ],
        },
    )

    assert profile["certification_tier"] is None
    reasons = {
        reason["category"]: set(reason["missing_requirements"])
        for reason in profile["uncertified_reasons"]
    }
    assert "live_evidence" in reasons
    assert "post_launch_verified.isolation_state.import_guard_active" in reasons["live_evidence"]
    assert "post_launch_verified.mount_network_state.artifact_write_blocked" in reasons["live_evidence"]


class TestContainerSandboxCertificationCrossPlatform:
    """NF-5: proves sandbox_certification_profile reaches container-sandbox-certified
    on simulated Windows/macOS hosts and stays uncertified when conditions are not met.

    runner_metadata and platform_matrix are injected as synthetic dicts so no subprocess
    calls, no Docker, no platform.system() mocking needed — the certification function
    is platform-neutral by construction (zero platform.system() calls in
    sandbox_certification.py).
    """

    def _certified_metadata(self) -> dict:
        return {
            "assurance_class": "container-grade-sandbox",
            "isolation_claim": "container-boundary",
            "execution_boundary": "container-stdio-json-rpc",
            "resource_limits": {
                "enforcement": "container-runtime-hard-limits",
            },
            "runtime_identity": {
                "pinned": True,
                "trust_chain": {
                    "accepted_for_production_safe_profiles": True,
                },
            },
            "launch_attestation": {
                "status": "launch-observed",
                "backend_identity": {"verified": True},
                "runtime_identity": {"verified": True},
                "mount_mode": {"verified": True},
                "resource_limit_mode": {"verified": True},
            },
        }

    def _supported_platform_matrix(self) -> dict:
        return {
            "current_environment": {
                "support_levels": {
                    "container_sandbox": {"support": "supported"},
                }
            }
        }

    def _unsupported_platform_matrix(self) -> dict:
        return {
            "current_environment": {
                "support_levels": {
                    "container_sandbox": {"support": "unsupported"},
                }
            }
        }

    # --- Positive cases: certification is platform-neutral ---

    def test_linux_host_certified(self):
        from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile

        profile = sandbox_certification_profile(
            runner_type="containerized_oci",
            runner_metadata=self._certified_metadata(),
            platform_matrix=self._supported_platform_matrix(),
        )
        assert profile["tier_status"] == "certified"
        assert profile["certification_tier"] == "container-sandbox-certified"
        assert profile["missing_tier_requirements"] == []
        assert profile["validation_layers"]["runner_assurance"]["status"] == "passed"

    def test_windows_host_with_linux_backend_certified(self):
        from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile

        # platform_matrix reflects what _detect_linux_container_backend produces when
        # Docker Desktop on Windows is running in Linux-containers mode (OSType=linux).
        platform_matrix = {
            "current_environment": {
                "support_levels": {
                    "container_sandbox": {"support": "supported"},
                },
                "platform": "windows",
            }
        }
        profile = sandbox_certification_profile(
            runner_type="containerized_oci",
            runner_metadata=self._certified_metadata(),
            platform_matrix=platform_matrix,
        )
        assert profile["tier_status"] == "certified"
        assert profile["certification_tier"] == "container-sandbox-certified"
        assert profile["missing_tier_requirements"] == []
        assert profile["validation_layers"]["runner_assurance"]["status"] == "passed"

    def test_macos_host_with_linux_backend_certified(self):
        from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile

        # platform_matrix reflects what _detect_linux_container_backend produces when
        # Docker Desktop on macOS is running in Linux-containers mode (OSType=linux).
        platform_matrix = {
            "current_environment": {
                "support_levels": {
                    "container_sandbox": {"support": "supported"},
                },
                "platform": "darwin",
            }
        }
        profile = sandbox_certification_profile(
            runner_type="containerized_oci",
            runner_metadata=self._certified_metadata(),
            platform_matrix=platform_matrix,
        )
        assert profile["tier_status"] == "certified"
        assert profile["certification_tier"] == "container-sandbox-certified"
        assert profile["missing_tier_requirements"] == []
        assert profile["validation_layers"]["runner_assurance"]["status"] == "passed"

    # --- Negative cases: uncertified when conditions are not met ---

    def test_windows_containers_mode_not_certified(self):
        from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile

        # Docker Desktop on Windows in Windows-containers mode returns OSType=windows →
        # _detect_linux_container_backend sets linux_container_backend=False →
        # platform_matrix surfaces container_sandbox.support=unsupported.
        profile = sandbox_certification_profile(
            runner_type="containerized_oci",
            runner_metadata=self._certified_metadata(),
            platform_matrix=self._unsupported_platform_matrix(),
        )
        assert profile["tier_status"] == "not_certified_for_runner"
        assert profile["certification_tier"] is None
        assert "platform_support.container_sandbox" in profile["missing_tier_requirements"]
        assert profile["validation_layers"]["runner_assurance"]["status"] == "not_certified"

    def test_no_runtime_available_not_certified(self):
        from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile

        metadata = self._certified_metadata()
        metadata["launch_attestation"]["status"] = "not-started"
        for field in ("backend_identity", "runtime_identity", "mount_mode", "resource_limit_mode"):
            metadata["launch_attestation"][field] = {"verified": False}
        metadata["resource_limits"]["enforcement"] = "unavailable"
        metadata["runtime_identity"]["trust_chain"]["accepted_for_production_safe_profiles"] = False

        profile = sandbox_certification_profile(
            runner_type="containerized_oci",
            runner_metadata=metadata,
            platform_matrix=self._supported_platform_matrix(),
        )
        assert profile["tier_status"] == "not_certified_for_runner"
        assert "launch_attestation.status" in profile["missing_tier_requirements"]
        assert "resource_limits.enforcement" in profile["missing_tier_requirements"]

    def test_no_pinned_digest_not_certified(self):
        from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile

        metadata = self._certified_metadata()
        metadata["launch_attestation"]["backend_identity"] = {"verified": False}

        profile = sandbox_certification_profile(
            runner_type="containerized_oci",
            runner_metadata=metadata,
            platform_matrix=self._supported_platform_matrix(),
        )
        assert profile["tier_status"] == "not_certified_for_runner"
        assert "verified.backend_identity" in profile["missing_tier_requirements"]

    def test_wall_clock_only_limits_not_certified(self):
        from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile

        metadata = self._certified_metadata()
        metadata["resource_limits"]["enforcement"] = "wall-clock-timeout-only"

        profile = sandbox_certification_profile(
            runner_type="containerized_oci",
            runner_metadata=metadata,
            platform_matrix=self._supported_platform_matrix(),
        )
        assert profile["tier_status"] == "not_certified_for_runner"
        assert "resource_limits.enforcement" in profile["missing_tier_requirements"]

    # --- Diagnostic: each verified attestation field is independently required ---

    @pytest.mark.parametrize(
        "field_name",
        ["backend_identity", "runtime_identity", "mount_mode", "resource_limit_mode"],
    )
    def test_each_verified_attestation_field_independently_required(self, field_name: str):
        from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile

        metadata = self._certified_metadata()
        metadata["launch_attestation"][field_name] = {"verified": False}

        profile = sandbox_certification_profile(
            runner_type="containerized_oci",
            runner_metadata=metadata,
            platform_matrix=self._supported_platform_matrix(),
        )
        assert profile["tier_status"] == "not_certified_for_runner"
        assert f"verified.{field_name}" in profile["missing_tier_requirements"]
