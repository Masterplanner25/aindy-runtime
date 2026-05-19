from __future__ import annotations

import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.runtime_only


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


def test_webhook_extensions_can_allow_private_targets_explicitly(monkeypatch):
    from AINDY.platform_layer.event_service import subscribe_webhook, unsubscribe_webhook

    monkeypatch.setenv("AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS", "true")

    meta = subscribe_webhook("execution.completed", "http://127.0.0.1:9999/hook")
    assert meta["callback_url"] == "http://127.0.0.1:9999/hook"
    assert unsubscribe_webhook(meta["id"]) is True


def test_dynamic_flow_validation_rejects_duplicate_nodes(monkeypatch):
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
