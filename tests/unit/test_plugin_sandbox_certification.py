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
    Path(__file__).write_text("overwrite", encoding="utf-8")
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
    assert snapshot["resource_limits"]["enforcement"] == "container-runtime-hard-limits"
    assert "no_new_privileges" in snapshot["sandbox_attestation"]["active_hardening_controls"]
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
