from __future__ import annotations

import hashlib

import pytest
from tests.helpers_plugin_artifacts import build_plugin_artifact


pytestmark = pytest.mark.runtime_only


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


@pytest.fixture
def clean_registry_state():
    from AINDY.platform_layer import registry

    snapshot = {
        "_loaded_plugins": set(registry._loaded_plugins),
        "_registered_apps": list(registry._registered_apps),
        "_bootstrap_dependencies": dict(registry._bootstrap_dependencies),
        "_loaded_extension_records": dict(registry._loaded_extension_records),
        "_bootstrap_registrations": dict(registry._bootstrap_registrations),
        "_active_plugin_profile": registry._active_plugin_profile,
        "_active_plugin_profile_source": registry._active_plugin_profile_source,
        "_runtime_agent_defaults_loaded": registry._runtime_agent_defaults_loaded,
        "_runtime_callback_invocations": dict(registry._runtime_callback_invocations),
        "_in_process_extension_capability_audit": dict(registry._in_process_extension_capability_audit),
    }
    try:
        registry._loaded_plugins.clear()
        registry._registered_apps.clear()
        registry._bootstrap_dependencies.clear()
        registry._loaded_extension_records.clear()
        registry._bootstrap_registrations.clear()
        registry._active_plugin_profile = None
        registry._active_plugin_profile_source = None
        registry._runtime_agent_defaults_loaded = False
        registry._runtime_callback_invocations.clear()
        registry._in_process_extension_capability_audit.clear()
        yield
    finally:
        registry._loaded_plugins = snapshot["_loaded_plugins"]
        registry._registered_apps = snapshot["_registered_apps"]
        registry._bootstrap_dependencies = snapshot["_bootstrap_dependencies"]
        registry._loaded_extension_records = snapshot["_loaded_extension_records"]
        registry._bootstrap_registrations = snapshot["_bootstrap_registrations"]
        registry._active_plugin_profile = snapshot["_active_plugin_profile"]
        registry._active_plugin_profile_source = snapshot["_active_plugin_profile_source"]
        registry._runtime_agent_defaults_loaded = snapshot["_runtime_agent_defaults_loaded"]
        registry._runtime_callback_invocations = snapshot["_runtime_callback_invocations"]
        registry._in_process_extension_capability_audit = snapshot["_in_process_extension_capability_audit"]


def test_manifest_runtime_builtin_records_runtime_derived_provenance(monkeypatch, tmp_path, clean_registry_state):
    from AINDY._version import __version__ as runtime_version
    from AINDY.platform_layer import registry

    manifest = tmp_path / "runtime_plugins.json"
    manifest.write_text(
        """
{
  "kind": "aindy-extension-manifest",
  "abi_version": "aindy.extension.manifest/v1",
  "default_profile": "platform-only",
  "profiles": {
    "platform-only": {
      "plugins": [
        {"module": "AINDY.platform_layer.runtime_agent_defaults", "owner_class": "runtime-built-in"}
      ]
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    registry.load_plugins(manifest_path=manifest, profile="platform-only")
    record = registry.get_loaded_extensions()[0]

    assert record["provenance"]["extension_id"] == "AINDY.platform_layer.runtime_agent_defaults"
    assert record["provenance"]["version"] == runtime_version
    assert record["provenance"]["source_type"] == "runtime-package"
    assert record["provenance"]["verification"] == "runtime-derived"
    assert record["provenance"]["integrity"]["observed"]


def test_external_plugin_registration_rejects_missing_provenance(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.node_registry import register_external_node

    artifact = build_plugin_artifact(
        tmp_path,
        module_name="safe_node",
        extension_id="vendor.safe-node",
        source="""
def handler(state, context):
    return {"status": "SUCCESS"}
""",
    )

    with pytest.raises(ValueError, match="requires declared provenance"):
        register_external_node(
            "missing-provenance-plugin",
            "plugin",
            artifact["handler"],
            artifact_path=str(artifact["artifact_root"]),
        )


def test_external_plugin_registration_rejects_integrity_mismatch(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.node_registry import register_external_node

    artifact = build_plugin_artifact(
        tmp_path,
        module_name="safe_node",
        extension_id="vendor.safe-node",
        source="""
def handler(state, context):
    return {"status": "SUCCESS"}
""",
    )

    with pytest.raises(ValueError, match="failed integrity verification"):
        register_external_node(
            "bad-integrity-plugin",
            "plugin",
            artifact["handler"],
            artifact_path=str(artifact["artifact_root"]),
            provenance={
                "extension_id": "vendor.safe-node",
                "version": "1.2.3",
                "source_type": "external-plugin-artifact",
                "source_ref": str(artifact["artifact_root"]),
                "integrity": {"algorithm": "sha256", "value": "0" * 64},
            },
        )


def test_external_webhook_subscription_requires_verifiable_provenance():
    from AINDY.platform_layer.event_service import subscribe_webhook, unsubscribe_webhook

    with pytest.raises(ValueError, match="requires declared provenance"):
        subscribe_webhook(
            "execution.completed",
            "https://example.com/hook",
        )

    meta = subscribe_webhook(
        "execution.completed",
        "https://example.com/hook",
        provenance={
            "extension_id": "vendor.hook",
            "version": "2026.05",
            "source_type": "webhook-integration",
            "source_ref": "https://example.com/hook",
            "integrity": {
                "algorithm": "sha256",
                "value": _sha256_text(
                    '{"abi_version":"aindy.extension.webhook-registration/v1alpha1","callback_url":"https://example.com/hook","event_type":"execution.completed","owner_class":"external-third-party"}'
                ),
            },
        },
    )
    assert meta["provenance"]["verification"] == "declared-and-verified"
    unsubscribe_webhook(meta["id"])


def test_readiness_reports_loaded_extension_provenance(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.health_service import get_readiness_report
    from AINDY.platform_layer.node_registry import register_external_node

    artifact = build_plugin_artifact(
        tmp_path,
        module_name="safe_node",
        extension_id="vendor.reported-plugin",
        version="2.0.0",
        source="""
def handler(state, context):
    return {"status": "SUCCESS"}
""",
    )

    register_external_node(
        "reported-plugin",
        "plugin",
        artifact["handler"],
        artifact_path=str(artifact["artifact_root"]),
        provenance={
            "extension_id": "vendor.reported-plugin",
            "version": "2.0.0",
            "source_type": "external-plugin-artifact",
            "source_ref": str(artifact["artifact_root"]),
            "integrity": {"algorithm": "sha256", "value": artifact["integrity"]},
        },
    )

    _status, payload = get_readiness_report()

    assert payload["checks"]["extension_provenance"]["present"] is True
    assert payload["checks"]["extension_provenance"]["verified_count"] >= 1
    assert any(
        entry["extension_id"] == "vendor.reported-plugin"
        for entry in payload["checks"]["extension_provenance"]["entries"]
    )


def test_plugin_artifact_admission_rejects_malformed_manifest(tmp_path):
    from AINDY.platform_layer.plugin_artifacts import admit_plugin_artifact

    artifact_root = tmp_path / "broken_artifact"
    artifact_root.mkdir(parents=True)
    (artifact_root / "plugin-artifact.json").write_text(
        '{"kind":"aindy-plugin-artifact","schema_version":"2026-05-21"}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="plugin artifact manifest is invalid"):
        admit_plugin_artifact(
            artifact_path=artifact_root,
            expected_owner_class="external-third-party",
            expected_handler="broken:handler",
        )
