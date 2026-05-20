from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.runtime_only


@pytest.fixture
def clean_dynamic_runtime_state():
    from AINDY.platform_layer import event_service, node_registry
    from AINDY.runtime import flow_registry
    from AINDY.runtime.flow_engine import FLOW_REGISTRY, NODE_REGISTRY

    original_node_registry = dict(NODE_REGISTRY)
    original_flow_registry = dict(FLOW_REGISTRY)
    original_dynamic_nodes = dict(node_registry._DYNAMIC_NODE_META)
    original_dynamic_flows = dict(flow_registry._DYNAMIC_META)
    original_subscriptions = dict(event_service._SUBSCRIPTIONS)
    try:
        node_registry._DYNAMIC_NODE_META.clear()
        flow_registry._DYNAMIC_META.clear()
        event_service._SUBSCRIPTIONS.clear()
        yield
    finally:
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
    (plugin_dir / "safe_node.py").write_text(
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


def test_dynamic_plugin_node_blocks_external_python_by_default(monkeypatch, tmp_path, clean_dynamic_runtime_state):
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
    monkeypatch.delenv("AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS", raising=False)

    with pytest.raises(ValueError, match="blocked by default"):
        register_external_node(
            "third-party-plugin",
            "plugin",
            "safe_node:handler",
        )


def test_dynamic_plugin_node_allows_first_party_trusted_integration(monkeypatch, tmp_path, clean_dynamic_runtime_state):
    from AINDY.platform_layer.extension_policy import OWNER_FIRST_PARTY_APP
    from AINDY.platform_layer.node_registry import get_dynamic_node, register_external_node

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

    meta = register_external_node(
        "first-party-plugin",
        "plugin",
        "safe_node:handler",
        owner_class=OWNER_FIRST_PARTY_APP,
        overwrite=True,
    )

    assert meta["owner_class"] == OWNER_FIRST_PARTY_APP
    assert meta["trust_class"] == "trusted-first-party-python"
    assert get_dynamic_node("first-party-plugin")["trust_class"] == "trusted-first-party-python"


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
        )


def test_webhook_extensions_remain_supported_with_contract_constraints(monkeypatch, clean_dynamic_runtime_state):
    from AINDY.platform_layer.event_service import subscribe_webhook, unsubscribe_webhook
    from AINDY.platform_layer.node_registry import register_external_node

    monkeypatch.setenv("AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS", "true")

    node_meta = register_external_node(
        "external-webhook-node",
        "webhook",
        "http://127.0.0.1:9999/node",
    )
    webhook_meta = subscribe_webhook("execution.completed", "http://127.0.0.1:9999/hook")

    assert node_meta["owner_class"] == "external-third-party"
    assert node_meta["trust_class"] == "contract-driven-webhook"
    assert webhook_meta["owner_class"] == "external-third-party"
    assert webhook_meta["trust_class"] == "contract-driven-webhook"
    assert unsubscribe_webhook(webhook_meta["id"]) is True


def test_registry_restore_skips_blocked_external_plugin_nodes(monkeypatch):
    from AINDY.platform_layer import platform_loader

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

    stats = {"nodes_loaded": 0, "nodes_skipped": 0}
    row = SimpleNamespace(
        name="blocked-third-party-plugin",
        node_type="plugin",
        owner_class="external-third-party",
        handler_config={"handler": "safe_node:handler"},
        secret=None,
        created_by="user-1",
    )

    monkeypatch.delenv("AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS", raising=False)
    monkeypatch.setattr("AINDY.runtime.flow_engine.NODE_REGISTRY", {})

    platform_loader._load_nodes(FakeDb([row]), stats)

    assert stats == {"nodes_loaded": 0, "nodes_skipped": 1}


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
