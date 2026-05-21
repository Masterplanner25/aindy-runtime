from __future__ import annotations

import hashlib

import pytest


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

    plugin_dir = tmp_path / "plugins" / "nodes"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / "safe_node.py").write_text(
        """
def handler(state, context):
    return {"status": "SUCCESS"}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr("AINDY.platform_layer.node_registry._PLUGINS_DIR", plugin_dir)

    with pytest.raises(ValueError, match="requires declared provenance"):
        register_external_node(
            "missing-provenance-plugin",
            "plugin",
            "safe_node:handler",
        )


def test_external_plugin_registration_rejects_integrity_mismatch(
    monkeypatch, tmp_path, clean_dynamic_runtime_state
):
    from AINDY.platform_layer.node_registry import register_external_node

    plugin_dir = tmp_path / "plugins" / "nodes"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / "safe_node.py").write_text(
        """
def handler(state, context):
    return {"status": "SUCCESS"}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr("AINDY.platform_layer.node_registry._PLUGINS_DIR", plugin_dir)

    with pytest.raises(ValueError, match="failed integrity verification"):
        register_external_node(
            "bad-integrity-plugin",
            "plugin",
            "safe_node:handler",
            provenance={
                "extension_id": "vendor.safe-node",
                "version": "1.2.3",
                "source_type": "external-source-tree",
                "source_ref": "file://vendor/safe_node.py",
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

    plugin_dir = tmp_path / "plugins" / "nodes"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    plugin_file = plugin_dir / "safe_node.py"
    plugin_file.write_text(
        """
def handler(state, context):
    return {"status": "SUCCESS"}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr("AINDY.platform_layer.node_registry._PLUGINS_DIR", plugin_dir)

    register_external_node(
        "reported-plugin",
        "plugin",
        "safe_node:handler",
        provenance={
            "extension_id": "vendor.reported-plugin",
            "version": "2.0.0",
            "source_type": "external-source-tree",
            "source_ref": str(plugin_file),
            "integrity": {"algorithm": "sha256", "value": hashlib.sha256(plugin_file.read_bytes()).hexdigest()},
        },
    )

    _status, payload = get_readiness_report()

    assert payload["checks"]["extension_provenance"]["present"] is True
    assert payload["checks"]["extension_provenance"]["verified_count"] >= 1
    assert any(
        entry["extension_id"] == "vendor.reported-plugin"
        for entry in payload["checks"]["extension_provenance"]["entries"]
    )
