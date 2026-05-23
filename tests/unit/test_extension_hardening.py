from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.helpers_plugin_artifacts import build_plugin_artifact


pytestmark = pytest.mark.runtime_only


def _third_party_plugin_artifact(
    tmp_path: Path,
    *,
    module_name: str,
    source: str,
    extension_id: str,
) -> dict[str, object]:
    return build_plugin_artifact(
        tmp_path,
        module_name=module_name,
        source=source,
        extension_id=extension_id,
    )


def _json_sha256(payload: dict[str, object]) -> str:
    import json

    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _webhook_registration_provenance(*, extension_id: str, callback_url: str, event_type: str | None = None, timeout_seconds: int | None = None, capabilities: list[str] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "abi_version": (
            "aindy.extension.webhook-registration/v1alpha1"
            if event_type is not None
            else "aindy.extension.node-registration/v1alpha1"
        ),
        "owner_class": "external-third-party",
    }
    if event_type is not None:
        payload["event_type"] = event_type
        payload["callback_url"] = callback_url
        source_type = "webhook-integration"
        source_ref = callback_url
    else:
        payload["type"] = "webhook"
        payload["handler"] = callback_url
        payload["timeout_seconds"] = timeout_seconds or 10
        payload["capabilities"] = list(capabilities or ["outbound.http"])
        source_type = "webhook-integration"
        source_ref = callback_url
    return {
        "extension_id": extension_id,
        "version": "1.0.0",
        "source_type": source_type,
        "source_ref": source_ref,
        "integrity": {
            "algorithm": "sha256",
            "value": _json_sha256(payload),
        },
    }


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


def test_manifest_rejects_untrusted_plugin_module(monkeypatch, tmp_path):
    from AINDY.platform_layer import registry

    manifest = tmp_path / "plugins.json"
    manifest.write_text(
        """
{
  "default_profile": "default-apps",
  "profiles": {
    "default-apps": {"plugins": ["evil.module"]}
  }
}
""".strip(),
        encoding="utf-8",
    )

    with monkeypatch.context() as scoped:
        scoped.setenv("AINDY_PLUGIN_MANIFEST", str(manifest))
        scoped.delenv("AINDY_BOOT_MODE", raising=False)
        scoped.delenv("AINDY_BOOT_PROFILE", raising=False)
        scoped.delenv("AINDY_PLUGIN_PROFILE", raising=False)
        with pytest.raises(ValueError, match="outside (trusted bootstrap|allowed) prefixes"):
            registry.resolve_plugin_profile(profile="default-apps")


def test_manifest_allows_trusted_plugin_module_name():
    from AINDY.platform_layer.extension_policy import validate_bootstrap_module_name

    validate_bootstrap_module_name("AINDY.plugins.nodes.sample")
    validate_bootstrap_module_name("apps.bootstrap.runtime")


def test_plugin_node_loader_does_not_mutate_sys_path(monkeypatch, tmp_path):
    from AINDY.platform_layer import node_registry

    plugin_dir = tmp_path / "plugins" / "nodes"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    plugin_file = plugin_dir / "safe_node.py"
    plugin_file.write_text(
        """
def handler(state, context):
    return {"status": "SUCCESS", "output_patch": {"seen": True}}
""".strip(),
        encoding="utf-8",
    )

    before = list(sys.path)
    monkeypatch.setattr(node_registry, "_PLUGINS_DIR", plugin_dir)

    fn = node_registry._load_plugin_node("safe_node:handler")

    assert fn({"value": 1}, {})["status"] == "SUCCESS"
    assert sys.path == before


def test_plugin_node_loader_rejects_unsupported_signature(monkeypatch, tmp_path):
    from AINDY.platform_layer import node_registry

    plugin_dir = tmp_path / "plugins" / "nodes"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / "bad_node.py").write_text(
        """
def handler(state, context, extra):
    return {"status": "SUCCESS"}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(node_registry, "_PLUGINS_DIR", plugin_dir)

    with pytest.raises(ValueError, match="unsupported positional arguments"):
        node_registry._load_plugin_node("bad_node:handler")


def test_dynamic_plugin_node_uses_isolated_worker_by_default(monkeypatch, tmp_path, clean_dynamic_runtime_state):
    from AINDY.platform_layer.node_registry import get_dynamic_node, register_external_node
    from AINDY.runtime.flow_engine import NODE_REGISTRY

    artifact = _third_party_plugin_artifact(
        tmp_path,
        module_name="safe_node",
        extension_id="vendor.third-party-plugin",
        source="""
def handler(state, context):
    return {"status": "SUCCESS", "output_patch": {"seen": state.get("value")}}
""",
    )

    meta = register_external_node(
        "third-party-plugin",
        "plugin",
        artifact["handler"],
        artifact_path=str(artifact["artifact_root"]),
        provenance=artifact["provenance"],
    )

    assert meta["owner_class"] == "external-third-party"
    assert meta["abi_surface"] == "dynamic-node-registration"
    assert meta["abi_version"] == "aindy.extension.node-registration/v1alpha1"
    assert meta["abi_stability"] == "experimental"
    assert meta["authority_model"] == "isolated-explicit-capabilities"
    assert meta["granted_capabilities"] == []
    assert meta["trust_class"] == "isolated-third-party-python"
    assert meta["execution_model"] == "isolated-plugin-host"
    assert meta["sandboxing"] == "subprocess-boundary"
    assert meta["transport"] == "plugin-host-rpc"
    assert meta["plugin_host_name"] == "third-party-plugin"
    assert meta["provenance"]["verification"] == "declared-and-verified"
    assert meta["provenance"]["extension_id"] == "vendor.third-party-plugin"
    assert meta["resource_access"]["network"]["capability_required"] == "outbound.http"
    assert meta["resource_access"]["filesystem"]["default"] == "read-only-approved-roots"
    assert meta["resource_access"]["environment"]["secret_injection"] == "none"
    assert "AINDY.plugins.nodes.safe_node" not in sys.modules
    assert NODE_REGISTRY["third-party-plugin"]({"value": 7}, {}) == {
        "status": "SUCCESS",
        "output_patch": {"seen": 7},
    }
    assert "AINDY.plugins.nodes.safe_node" not in sys.modules
    plugin_host = get_dynamic_node("third-party-plugin")["plugin_host"]
    assert plugin_host["lifecycle_state"] == "running"
    assert plugin_host["provenance"]["extension_id"] == "vendor.third-party-plugin"
    assert get_dynamic_node("third-party-plugin")["transport"] == "plugin-host-rpc"


def test_dynamic_plugin_node_granted_capability_path_succeeds(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.node_registry import register_external_node
    from AINDY.runtime.flow_engine import NODE_REGISTRY

    artifact = _third_party_plugin_artifact(
        tmp_path,
        module_name="memory_cap_node",
        extension_id="vendor.memory-cap-plugin",
        source="""
from AINDY.platform_layer.extension_runtime_api import (
    get_execution_metadata,
    get_granted_capabilities,
    require_capability,
)

def handler(state, context):
    require_capability("memory.read")
    metadata = get_execution_metadata()
    return {
        "status": "SUCCESS",
        "output_patch": {
            "granted_capabilities": get_granted_capabilities(),
            "has_db": "db" in context,
            "has_runtime_channel_token": "runtime_channel_token" in context,
            "channel_type": context.get("runtime_api", {}).get("channel_type"),
            "metadata_channel_type": metadata.get("channel_type"),
            "metadata_channel_id_matches": metadata.get("runtime_channel_id") == context.get("runtime_api", {}).get("runtime_channel_id"),
            "metadata_sandbox_instance_matches": metadata.get("sandbox_instance_id") == context.get("runtime_api", {}).get("sandbox_instance_id"),
        },
    }
""",
    )

    meta = register_external_node(
        "memory-cap-plugin",
        "plugin",
        artifact["handler"],
        artifact_path=str(artifact["artifact_root"]),
        capabilities=["memory.read"],
        provenance=artifact["provenance"],
        overwrite=True,
    )

    result = NODE_REGISTRY["memory-cap-plugin"](
        {},
        {"user_id": "user-1", "run_id": "run-1", "trace_id": "trace-1", "db": object()},
    )

    assert meta["granted_capabilities"] == ["memory.read"]
    assert result["status"] == "SUCCESS"
    assert result["output_patch"]["granted_capabilities"] == ["memory.read"]
    assert result["output_patch"]["has_db"] is False
    assert result["output_patch"]["has_runtime_channel_token"] is False
    assert result["output_patch"]["channel_type"] == "worker-authenticated-rpc"
    assert result["output_patch"]["metadata_channel_type"] == "worker-authenticated-rpc"
    assert result["output_patch"]["metadata_channel_id_matches"] is True
    assert result["output_patch"]["metadata_sandbox_instance_matches"] is True


def test_dynamic_plugin_node_denies_ungranted_memory_read_capability(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.node_registry import register_external_node
    from AINDY.runtime.flow_engine import NODE_REGISTRY

    artifact = _third_party_plugin_artifact(
        tmp_path,
        module_name="denied_memory_node",
        extension_id="vendor.denied-memory-plugin",
        source="""
from AINDY.platform_layer.extension_runtime_api import memory_read

def handler(state, context):
    memory_read(query="forbidden")
    return {"status": "SUCCESS"}
""",
    )

    register_external_node(
        "denied-memory-plugin",
        "plugin",
        artifact["handler"],
        artifact_path=str(artifact["artifact_root"]),
        provenance=artifact["provenance"],
        overwrite=True,
    )

    result = NODE_REGISTRY["denied-memory-plugin"]({}, {"user_id": "user-1", "run_id": "run-1"})

    assert result["status"] == "FAILURE"
    assert "not granted" in result["error"]


def test_dynamic_plugin_node_denies_outbound_http_without_capability(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.node_registry import register_external_node
    from AINDY.runtime.flow_engine import NODE_REGISTRY

    artifact = _third_party_plugin_artifact(
        tmp_path,
        module_name="denied_network_node",
        extension_id="vendor.denied-network-plugin",
        source="""
import socket

def handler(state, context):
    socket.create_connection(("127.0.0.1", 9), timeout=0.1)
    return {"status": "SUCCESS"}
""",
    )

    register_external_node(
        "denied-network-plugin",
        "plugin",
        artifact["handler"],
        artifact_path=str(artifact["artifact_root"]),
        provenance=artifact["provenance"],
        overwrite=True,
    )

    result = NODE_REGISTRY["denied-network-plugin"]({}, {"user_id": "user-1", "run_id": "run-1"})

    assert result["status"] == "FAILURE"
    assert "outbound.http" in result["error"]


def test_dynamic_plugin_node_denies_private_targets_even_with_outbound_http(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.node_registry import register_external_node
    from AINDY.runtime.flow_engine import NODE_REGISTRY

    artifact = _third_party_plugin_artifact(
        tmp_path,
        module_name="private_network_node",
        extension_id="vendor.private-network-plugin",
        source="""
import socket

def handler(state, context):
    socket.create_connection(("127.0.0.1", 9), timeout=0.01)
    return {"status": "SUCCESS"}
""",
    )
    monkeypatch.delenv("AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS", raising=False)

    register_external_node(
        "private-network-plugin",
        "plugin",
        artifact["handler"],
        artifact_path=str(artifact["artifact_root"]),
        capabilities=["outbound.http"],
        provenance=artifact["provenance"],
        overwrite=True,
    )

    result = NODE_REGISTRY["private-network-plugin"]({}, {"user_id": "user-1", "run_id": "run-1"})

    assert result["status"] == "FAILURE"
    assert "private/loopback targets are denied" in result["error"]


def test_dynamic_plugin_node_private_target_override_allows_attempt(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.node_registry import register_external_node
    from AINDY.runtime.flow_engine import NODE_REGISTRY

    artifact = _third_party_plugin_artifact(
        tmp_path,
        module_name="override_network_node",
        extension_id="vendor.override-network-plugin",
        source="""
import socket

def handler(state, context):
    try:
        socket.create_connection(("127.0.0.1", 9), timeout=0.01)
    except Exception as exc:
        return {"status": "SUCCESS", "output_patch": {"error_type": type(exc).__name__}}
        return {"status": "SUCCESS", "output_patch": {"error_type": "none"}}
""",
    )
    monkeypatch.setenv("AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS", "true")

    register_external_node(
        "override-network-plugin",
        "plugin",
        artifact["handler"],
        artifact_path=str(artifact["artifact_root"]),
        capabilities=["outbound.http"],
        provenance=artifact["provenance"],
        overwrite=True,
    )

    result = NODE_REGISTRY["override-network-plugin"]({}, {"user_id": "user-1", "run_id": "run-1"})

    assert result["status"] == "SUCCESS"
    assert result["output_patch"]["error_type"] != "PermissionError"


def test_dynamic_plugin_node_allows_read_only_access_within_plugin_root(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.node_registry import register_external_node
    from AINDY.runtime.flow_engine import NODE_REGISTRY

    artifact = _third_party_plugin_artifact(
        tmp_path,
        module_name="self_read_node",
        extension_id="vendor.self-read-plugin",
        source="""
from pathlib import Path

def handler(state, context):
    text = Path(__file__).read_text(encoding="utf-8")
    return {"status": "SUCCESS", "output_patch": {"contains_handler": "def handler" in text}}
""",
    )

    register_external_node(
        "self-read-plugin",
        "plugin",
        artifact["handler"],
        artifact_path=str(artifact["artifact_root"]),
        provenance=artifact["provenance"],
        overwrite=True,
    )

    result = NODE_REGISTRY["self-read-plugin"]({}, {"user_id": "user-1", "run_id": "run-1"})

    assert result["status"] == "SUCCESS"
    assert result["output_patch"]["contains_handler"] is True


def test_dynamic_plugin_node_blocks_filesystem_access_outside_plugin_root(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.node_registry import register_external_node
    from AINDY.runtime.flow_engine import NODE_REGISTRY

    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("secret", encoding="utf-8")
    artifact = _third_party_plugin_artifact(
        tmp_path,
        module_name="blocked_fs_node",
        extension_id="vendor.blocked-fs-plugin",
        source=f"""
from pathlib import Path

def handler(state, context):
    Path(r\"{outside_file}\").read_text(encoding=\"utf-8\")
    return {{\"status\": \"SUCCESS\"}}
""",
    )

    register_external_node(
        "blocked-fs-plugin",
        "plugin",
        artifact["handler"],
        artifact_path=str(artifact["artifact_root"]),
        provenance=artifact["provenance"],
        overwrite=True,
    )

    result = NODE_REGISTRY["blocked-fs-plugin"]({}, {"user_id": "user-1", "run_id": "run-1"})

    assert result["status"] == "FAILURE"
    assert "Filesystem path blocked" in result["error"]


def test_dynamic_plugin_node_blocks_filesystem_writes(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.node_registry import register_external_node
    from AINDY.runtime.flow_engine import NODE_REGISTRY

    artifact = _third_party_plugin_artifact(
        tmp_path,
        module_name="blocked_write_node",
        extension_id="vendor.blocked-write-plugin",
        source="""
from pathlib import Path

def handler(state, context):
    Path(__file__).write_text("overwrite", encoding="utf-8")
    return {"status": "SUCCESS"}
""",
    )

    register_external_node(
        "blocked-write-plugin",
        "plugin",
        artifact["handler"],
        artifact_path=str(artifact["artifact_root"]),
        provenance=artifact["provenance"],
        overwrite=True,
    )

    result = NODE_REGISTRY["blocked-write-plugin"]({}, {"user_id": "user-1", "run_id": "run-1"})

    assert result["status"] == "FAILURE"
    assert "Filesystem write blocked" in result["error"]


def test_dynamic_plugin_node_does_not_receive_runtime_secret_environment(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.node_registry import register_external_node
    from AINDY.runtime.flow_engine import NODE_REGISTRY

    artifact = _third_party_plugin_artifact(
        tmp_path,
        module_name="env_node",
        extension_id="vendor.env-plugin",
        source="""
import os

def handler(state, context):
    return {
        "status": "SUCCESS",
        "output_patch": {
            "has_openai": "OPENAI_API_KEY" in os.environ,
            "has_database_url": "DATABASE_URL" in os.environ,
            "has_path": "PATH" in os.environ,
        },
    }
""",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret")

    meta = register_external_node(
        "env-plugin",
        "plugin",
        artifact["handler"],
        artifact_path=str(artifact["artifact_root"]),
        provenance=artifact["provenance"],
        overwrite=True,
    )

    result = NODE_REGISTRY["env-plugin"]({}, {"user_id": "user-1", "run_id": "run-1"})

    assert meta["resource_access"]["environment"]["secret_injection"] == "none"
    assert result["status"] == "SUCCESS"
    assert result["output_patch"]["has_openai"] is False
    assert result["output_patch"]["has_database_url"] is False
    assert result["output_patch"]["has_path"] is True


def test_dynamic_plugin_node_blocks_internal_runtime_import_bypass(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.node_registry import register_external_node

    artifact = _third_party_plugin_artifact(
        tmp_path,
        module_name="blocked_import_node",
        extension_id="vendor.blocked-import-plugin",
        source="""
from AINDY.config import settings

def handler(state, context):
    return {"status": "SUCCESS", "output_patch": {"env": settings.ENV}}
""",
    )

    with pytest.raises(ValueError, match="plugin runtime import blocked"):
        register_external_node(
            "blocked-import-plugin",
            "plugin",
            artifact["handler"],
            artifact_path=str(artifact["artifact_root"]),
            provenance=artifact["provenance"],
            overwrite=True,
        )


def test_dynamic_plugin_node_cannot_rebind_runtime_api_channel(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.node_registry import register_external_node
    from AINDY.runtime.flow_engine import NODE_REGISTRY

    artifact = _third_party_plugin_artifact(
        tmp_path,
        module_name="rebind_runtime_api_node",
        extension_id="vendor.rebind-runtime-api-plugin",
        source="""
from AINDY.platform_layer import extension_runtime_api

def handler(state, context):
    try:
        extension_runtime_api._install_runtime_api_channel(bridge=lambda *_args, **_kwargs: {})
    except Exception as exc:
        return {
            "status": "SUCCESS",
            "output_patch": {
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
        }
    return {"status": "FAILURE", "error": "runtime channel rebind unexpectedly succeeded"}
""",
    )

    register_external_node(
        "rebind-runtime-api-plugin",
        "plugin",
        artifact["handler"],
        artifact_path=str(artifact["artifact_root"]),
        provenance=artifact["provenance"],
        overwrite=True,
    )

    result = NODE_REGISTRY["rebind-runtime-api-plugin"]({}, {"user_id": "user-1", "run_id": "run-1"})

    assert result["status"] == "SUCCESS"
    assert result["output_patch"]["error_type"] == "PermissionError"
    assert "restricted to the extension worker" in result["output_patch"]["message"]


def test_dynamic_plugin_node_cannot_see_extension_worker_module(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.node_registry import register_external_node
    from AINDY.runtime.flow_engine import NODE_REGISTRY

    artifact = _third_party_plugin_artifact(
        tmp_path,
        module_name="sysmodules_probe_node",
        extension_id="vendor.sysmodules-probe-plugin",
        source="""
import sys

def handler(state, context):
    return {
        "status": "SUCCESS",
        "output_patch": {
            "has_extension_worker_module": "AINDY.platform_layer.extension_worker" in sys.modules,
        },
    }
""",
    )

    register_external_node(
        "sysmodules-probe-plugin",
        "plugin",
        artifact["handler"],
        artifact_path=str(artifact["artifact_root"]),
        provenance=artifact["provenance"],
        overwrite=True,
    )

    result = NODE_REGISTRY["sysmodules-probe-plugin"]({}, {"user_id": "user-1", "run_id": "run-1"})

    assert result["status"] == "SUCCESS"
    assert result["output_patch"]["has_extension_worker_module"] is False


def test_dynamic_plugin_node_allows_first_party_trusted_integration(monkeypatch, tmp_path, clean_dynamic_runtime_state):
    from AINDY.platform_layer.extension_policy import OWNER_FIRST_PARTY_APP
    from AINDY.platform_layer.node_registry import get_dynamic_node, register_external_node
    from AINDY.runtime.flow_engine import NODE_REGISTRY

    plugin_dir = tmp_path / "plugins" / "nodes"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / "safe_node.py").write_text(
        """
from AINDY.platform_layer.extension_runtime_api import (
    get_execution_metadata,
    get_granted_capabilities,
    require_capability,
)

def handler(state, context):
    require_capability("memory.read")
    metadata = get_execution_metadata()
    return {
        "status": "SUCCESS",
        "output_patch": {
            "granted_capabilities": get_granted_capabilities(),
            "has_db": "db" in context,
            "channel_type": context.get("runtime_api", {}).get("channel_type"),
            "metadata_channel_type": metadata.get("channel_type"),
        },
    }
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr("AINDY.platform_layer.node_registry._PLUGINS_DIR", plugin_dir)

    meta = register_external_node(
        "first-party-plugin",
        "plugin",
        "safe_node:handler",
        owner_class=OWNER_FIRST_PARTY_APP,
        capabilities=["memory.read"],
        overwrite=True,
    )
    result = NODE_REGISTRY["first-party-plugin"](
        {},
        {"user_id": "user-1", "run_id": "run-1", "trace_id": "trace-1", "db": object()},
    )

    assert meta["owner_class"] == OWNER_FIRST_PARTY_APP
    assert meta["abi_surface"] == "dynamic-node-registration"
    assert meta["authority_model"] == "isolated-explicit-capabilities"
    assert meta["granted_capabilities"] == ["memory.read"]
    assert meta["trust_class"] == "isolated-first-party-python"
    assert meta["execution_model"] == "isolated-plugin-host"
    assert meta["sandboxing"] == "subprocess-boundary"
    assert meta["trusted_override_active"] is False
    assert meta["execution_surface"] == "dynamic-plugin-node"
    assert meta["module_name"] == "safe_node"
    assert meta["function_name"] == "handler"
    assert meta["source_path"].endswith("safe_node.py")
    assert meta["transport"] == "plugin-host-rpc"
    assert meta["plugin_host_name"] == "first-party-plugin"
    assert meta["runner_type"] == "insecure_dev_subprocess"
    assert "AINDY.plugins.nodes.safe_node" not in sys.modules
    assert result["status"] == "SUCCESS"
    assert result["output_patch"]["granted_capabilities"] == ["memory.read"]
    assert result["output_patch"]["has_db"] is False
    assert result["output_patch"]["channel_type"] == "worker-authenticated-rpc"
    assert result["output_patch"]["metadata_channel_type"] == "worker-authenticated-rpc"
    assert "AINDY.plugins.nodes.safe_node" not in sys.modules
    dynamic = get_dynamic_node("first-party-plugin")
    assert dynamic["trust_class"] == "isolated-first-party-python"
    assert dynamic["plugin_host"]["lifecycle_state"] == "running"


def test_dynamic_plugin_node_crash_does_not_crash_runtime_process(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.node_registry import register_external_node
    from AINDY.runtime.flow_engine import NODE_REGISTRY

    artifact = _third_party_plugin_artifact(
        tmp_path,
        module_name="crash_node",
        extension_id="vendor.crashing-third-party-plugin",
        source="""
import os

def handler(state, context):
    os._exit(7)
""",
    )

    meta = register_external_node(
        "crashing-third-party-plugin",
        "plugin",
        artifact["handler"],
        artifact_path=str(artifact["artifact_root"]),
        provenance=artifact["provenance"],
        overwrite=True,
    )

    assert meta["owner_class"] == "external-third-party"
    assert meta["trust_class"] == "isolated-third-party-python"
    assert meta["execution_model"] == "isolated-plugin-host"
    assert meta["sandboxing"] == "subprocess-boundary"
    assert meta["execution_surface"] == "dynamic-plugin-node"
    assert meta["module_name"] == "crash_node"
    assert meta["function_name"] == "handler"
    assert meta["source_path"].endswith("crash_node.py")

    result = NODE_REGISTRY["crashing-third-party-plugin"]({}, {})

    assert result["status"] == "FAILURE"
    assert "plugin host error" in result["error"]


def test_dynamic_plugin_node_worker_failure_is_surfaced_cleanly(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.node_registry import register_external_node
    from AINDY.runtime.flow_engine import NODE_REGISTRY

    artifact = _third_party_plugin_artifact(
        tmp_path,
        module_name="boom_node",
        extension_id="vendor.failing-third-party-plugin",
        source="""
def handler(state, context):
    raise RuntimeError("boom")
""",
    )

    register_external_node(
        "failing-third-party-plugin",
        "plugin",
        artifact["handler"],
        artifact_path=str(artifact["artifact_root"]),
        provenance=artifact["provenance"],
        overwrite=True,
    )

    result = NODE_REGISTRY["failing-third-party-plugin"]({}, {})

    assert result["status"] == "FAILURE"
    assert "RuntimeError: boom" in result["error"]


def test_webhook_extensions_reject_private_targets_by_default(monkeypatch):
    from AINDY.platform_layer.event_service import subscribe_webhook
    from AINDY.platform_layer.node_registry import register_external_node

    monkeypatch.delenv("AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS", raising=False)

    with pytest.raises(ValueError, match="private/loopback host"):
        subscribe_webhook("execution.completed", "http://127.0.0.1:9999/hook")

    with pytest.raises(ValueError, match="private/loopback host"):
        register_external_node(
            "private-webhook",
            "webhook",
            "http://localhost:9999/node",
            provenance=_webhook_registration_provenance(
                extension_id="vendor.private-webhook",
                callback_url="http://localhost:9999/node",
            ),
        )


def test_webhook_extensions_remain_supported_with_contract_constraints(monkeypatch, clean_dynamic_runtime_state):
    from AINDY.platform_layer.event_service import subscribe_webhook, unsubscribe_webhook
    from AINDY.platform_layer.node_registry import register_external_node

    monkeypatch.setenv("AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS", "true")

    node_meta = register_external_node(
        "external-webhook-node",
        "webhook",
        "http://127.0.0.1:9999/node",
        provenance=_webhook_registration_provenance(
            extension_id="vendor.external-webhook-node",
            callback_url="http://127.0.0.1:9999/node",
        ),
    )
    webhook_meta = subscribe_webhook(
        "execution.completed",
        "http://127.0.0.1:9999/hook",
        provenance=_webhook_registration_provenance(
            extension_id="vendor.execution-hook",
            callback_url="http://127.0.0.1:9999/hook",
            event_type="execution.completed",
        ),
    )

    assert node_meta["owner_class"] == "external-third-party"
    assert node_meta["abi_surface"] == "dynamic-node-registration"
    assert node_meta["granted_capabilities"] == ["outbound.http"]
    assert node_meta["trust_class"] == "contract-driven-webhook"
    assert node_meta["provenance"]["verification"] == "declared-and-verified"
    assert webhook_meta["owner_class"] == "external-third-party"
    assert webhook_meta["abi_surface"] == "webhook-registration"
    assert webhook_meta["abi_version"] == "aindy.extension.webhook-registration/v1alpha1"
    assert webhook_meta["granted_capabilities"] == ["outbound.http"]
    assert webhook_meta["trust_class"] == "contract-driven-webhook"
    assert webhook_meta["provenance"]["verification"] == "declared-and-verified"
    assert unsubscribe_webhook(webhook_meta["id"]) is True


def test_registry_restore_loads_external_plugin_nodes_through_isolated_boundary(monkeypatch, tmp_path):
    from AINDY.platform_layer import platform_loader
    from AINDY.platform_layer.node_registry import get_dynamic_node

    class FakeQuery:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *_args, **_kwargs):
            return self

        def all(self):
            return list(self._rows)

    class FakeDb:
        def __init__(self, rows):
            self._rows = rows

        def query(self, _model):
            return FakeQuery(self._rows)

    artifact = _third_party_plugin_artifact(
        tmp_path,
        module_name="safe_node",
        extension_id="vendor.restored-third-party-plugin",
        source="""
def handler(state, context):
    return {"status": "SUCCESS"}
""",
    )
    stats = {"nodes_loaded": 0, "nodes_skipped": 0}
    row = SimpleNamespace(
        name="restored-third-party-plugin",
        node_type="plugin",
        owner_class="external-third-party",
        handler_config={"handler": artifact["handler"], "artifact_path": str(artifact["artifact_root"])},
        provenance=artifact["provenance"],
        secret=None,
        created_by="user-1",
    )

    monkeypatch.setattr("AINDY.runtime.flow_engine.NODE_REGISTRY", {})

    platform_loader._load_nodes(FakeDb([row]), stats)

    assert stats == {"nodes_loaded": 1, "nodes_skipped": 0}
    assert get_dynamic_node("restored-third-party-plugin")["transport"] == "plugin-host-rpc"


def test_dynamic_flow_validation_rejects_duplicate_nodes():
    from AINDY.runtime import flow_registry
    from AINDY.runtime.flow_engine import NODE_REGISTRY

    original_registry = dict(NODE_REGISTRY)
    try:
        NODE_REGISTRY.clear()
        NODE_REGISTRY.update({"alpha": object(), "beta": object()})

        with pytest.raises(ValueError, match="must not contain duplicates"):
            flow_registry.register_dynamic_flow(
                "dup-flow",
                nodes=["alpha", "alpha", "beta"],
                edges={"alpha": ["beta"]},
                start="alpha",
                end=["beta"],
            )
    finally:
        NODE_REGISTRY.clear()
        NODE_REGISTRY.update(original_registry)


def test_dynamic_flow_validation_rejects_excessive_size():
    from AINDY.runtime import flow_registry

    errors = flow_registry._validate(
        "big-flow",
        nodes=[f"n{i}" for i in range(129)],
        edges={},
        start="n0",
        end=["n1"],
    )

    assert any("exceeds limit" in error for error in errors)
