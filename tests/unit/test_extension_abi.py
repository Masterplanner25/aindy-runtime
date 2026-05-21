from __future__ import annotations

from collections import defaultdict

import pytest
from pydantic import ValidationError

from AINDY.platform_layer import registry
from AINDY.platform_layer.extension_abi import (
    FLOW_REGISTRATION_ABI_V1ALPHA1,
    LEGACY_UNVERSIONED_MANIFEST,
    MANIFEST_ABI_V1,
    NODE_REGISTRATION_ABI_V1ALPHA1,
    WEBHOOK_REGISTRATION_ABI_V1ALPHA1,
    WEBHOOK_REGISTRATION_ABI_V1ALPHA1,
    extension_abi_policy,
)
from AINDY.platform_layer.extension_policy import OWNER_EXTERNAL_THIRD_PARTY
from AINDY.platform_layer.extension_provenance import sha256_json_document
from AINDY.platform_layer.event_service import _SUBSCRIPTIONS, _webhook_lock
from AINDY.routes.platform.schemas import (
    FlowDefinition,
    NodeRegistration,
    WebhookSubscription,
)


pytestmark = pytest.mark.runtime_only


_REGISTRY_STATE_EMPTY = {
    "_loaded_plugins": set(),
    "_registered_apps": [],
    "_bootstrap_dependencies": {},
    "_loaded_extension_records": {},
    "_bootstrap_registrations": {},
    "_active_plugin_profile": None,
    "_active_plugin_profile_source": None,
    "_runtime_agent_defaults_loaded": False,
    "_agent_planner_backends": {},
    "_agent_planner_contexts": {},
    "_agent_run_tools": {},
    "_agent_completion_hooks": defaultdict(list),
    "_agent_event_emitters": defaultdict(list),
    "_capability_definitions": {},
    "_capability_definition_providers": [],
    "_tool_capabilities": {},
    "_agent_capabilities": {},
    "_restricted_tools": set(),
}


def _copy_registry_value(value):
    if isinstance(value, defaultdict):
        copied = defaultdict(value.default_factory)
        for key, item in value.items():
            copied[key] = list(item) if isinstance(item, list) else item
        return copied
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    if isinstance(value, set):
        return set(value)
    return value


@pytest.fixture
def clean_registry_state():
    snapshot = {
        name: _copy_registry_value(getattr(registry, name))
        for name in _REGISTRY_STATE_EMPTY
    }
    try:
        for name, value in _REGISTRY_STATE_EMPTY.items():
            setattr(registry, name, _copy_registry_value(value))
        yield
    finally:
        for name, value in snapshot.items():
            setattr(registry, name, value)


@pytest.fixture
def clean_webhook_state():
    with _webhook_lock:
        _SUBSCRIPTIONS.clear()
    try:
        yield
    finally:
        with _webhook_lock:
            _SUBSCRIPTIONS.clear()


def test_extension_abi_policy_marks_only_manifest_stable():
    policy = extension_abi_policy()

    assert policy["surfaces"]["manifest"]["stability"] == "stable"
    assert policy["surfaces"]["dynamic-node-registration"]["stability"] == "experimental"
    assert policy["surfaces"]["webhook-registration"]["supported_versions"] == [
        WEBHOOK_REGISTRATION_ABI_V1ALPHA1
    ]


def test_versioned_manifest_v1_loads_with_abi_metadata(monkeypatch, tmp_path, clean_registry_state):
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

    loaded = registry.load_plugins(manifest_path=manifest, profile="platform-only")

    assert loaded == ["AINDY.platform_layer.runtime_agent_defaults"]
    record = registry.get_loaded_extensions()[0]
    assert record["abi_surface"] == "manifest"
    assert record["abi_version"] == MANIFEST_ABI_V1
    assert record["abi_stability"] == "stable"


def test_manifest_rejects_unsupported_abi_version(tmp_path):
    manifest = tmp_path / "runtime_plugins.json"
    manifest.write_text(
        """
{
  "kind": "aindy-extension-manifest",
  "abi_version": "aindy.extension.manifest/v9",
  "default_profile": "platform-only",
  "profiles": {"platform-only": {"plugins": []}}
}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unsupported abi_version"):
        registry.resolve_plugin_profile_entries(manifest_path=manifest, profile="platform-only")


def test_legacy_unversioned_manifest_remains_supported(tmp_path):
    manifest = tmp_path / "aindy_plugins.json"
    manifest.write_text(
        """
{
  "default_profile": "default-apps",
  "profiles": {
    "default-apps": {
      "plugins": ["apps.bootstrap"]
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    profile, entries = registry.resolve_plugin_profile_entries(
        manifest_path=manifest,
        profile="default-apps",
    )

    assert profile == "default-apps"
    assert entries == [{"module_name": "apps.bootstrap", "owner_class": "first-party-app"}]
    path, data = registry._read_plugin_manifest(manifest)
    assert path == manifest
    assert data is not None
    from AINDY.platform_layer.extension_abi import manifest_effective_abi_version

    assert manifest_effective_abi_version(data) == LEGACY_UNVERSIONED_MANIFEST


def test_manifest_supports_declarative_external_onboarding_without_bootstrap(
    tmp_path, clean_registry_state, clean_webhook_state
):
    artifact_payload = {
        "abi_version": WEBHOOK_REGISTRATION_ABI_V1ALPHA1,
        "event_type": "execution.completed",
        "callback_url": "https://example.com/hook",
        "owner_class": OWNER_EXTERNAL_THIRD_PARTY,
    }
    integrity = sha256_json_document(artifact_payload)
    manifest = tmp_path / "aindy_plugins.json"
    manifest.write_text(
        f"""
{{
  "kind": "aindy-extension-manifest",
  "abi_version": "{MANIFEST_ABI_V1}",
  "default_profile": "default-apps",
  "profiles": {{
    "default-apps": {{
      "plugins": [],
      "extensions": [
        {{
          "kind": "webhook-subscription",
          "abi_version": "{WEBHOOK_REGISTRATION_ABI_V1ALPHA1}",
          "event_type": "execution.completed",
          "callback_url": "https://example.com/hook",
          "owner_class": "{OWNER_EXTERNAL_THIRD_PARTY}",
          "provenance": {{
            "extension_id": "vendor.hook",
            "version": "1.2.3",
            "source_type": "webhook-integration",
            "source_ref": "https://example.com/hook",
            "publisher": "vendor",
            "integrity": {{"algorithm": "sha256", "value": "{integrity}"}}
          }}
        }}
      ]
    }}
  }}
}}
""".strip(),
        encoding="utf-8",
    )

    loaded = registry.load_plugins(manifest_path=manifest, profile="default-apps")

    assert loaded == [loaded[0]]
    assert loaded[0].startswith("manifest-extension:webhook-subscription:execution.completed:")
    assert registry.get_registered_apps() == []
    assert registry.get_bootstrap_registrations() == {}
    records = registry.get_loaded_extensions()
    assert len(records) == 1
    assert records[0]["execution_surface"] == "manifest-declarative-registration"
    assert records[0]["declarative_kind"] == "webhook-subscription"
    assert records[0]["owner_class"] == OWNER_EXTERNAL_THIRD_PARTY

    from AINDY.platform_layer.event_service import list_webhooks

    webhooks = list_webhooks()
    assert len(webhooks) == 1
    assert webhooks[0]["event_type"] == "execution.completed"
    assert webhooks[0]["callback_url"] == "https://example.com/hook"


def test_manifest_declarative_external_registration_requires_provenance(
    tmp_path, clean_registry_state, clean_webhook_state
):
    manifest = tmp_path / "aindy_plugins.json"
    manifest.write_text(
        f"""
{{
  "kind": "aindy-extension-manifest",
  "abi_version": "{MANIFEST_ABI_V1}",
  "default_profile": "default-apps",
  "profiles": {{
    "default-apps": {{
      "plugins": [],
      "extensions": [
        {{
          "kind": "webhook-subscription",
          "abi_version": "{WEBHOOK_REGISTRATION_ABI_V1ALPHA1}",
          "event_type": "execution.completed",
          "callback_url": "https://example.com/hook",
          "owner_class": "{OWNER_EXTERNAL_THIRD_PARTY}"
        }}
      ]
    }}
  }}
}}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires declared provenance"):
        registry.load_plugins(manifest_path=manifest, profile="default-apps")


def test_manifest_rejects_invalid_declarative_extension_shape(tmp_path):
    manifest = tmp_path / "aindy_plugins.json"
    manifest.write_text(
        f"""
{{
  "kind": "aindy-extension-manifest",
  "abi_version": "{MANIFEST_ABI_V1}",
  "default_profile": "default-apps",
  "profiles": {{
    "default-apps": {{
      "plugins": [],
      "extensions": [
        {{
          "kind": "webhook-subscription",
          "abi_version": "{WEBHOOK_REGISTRATION_ABI_V1ALPHA1}",
          "event_type": "execution.completed",
          "owner_class": "{OWNER_EXTERNAL_THIRD_PARTY}"
        }}
      ]
    }}
  }}
}}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="callback_url"):
        registry.resolve_plugin_profile_declarative_extensions(
            manifest_path=manifest,
            profile="default-apps",
        )


def test_node_registration_accepts_current_abi_version():
    model = NodeRegistration(
        abi_version=NODE_REGISTRATION_ABI_V1ALPHA1,
        name="demo-node",
        type="webhook",
        handler="https://example.com/node",
    )

    assert model.abi_version == NODE_REGISTRATION_ABI_V1ALPHA1


def test_node_registration_rejects_unsupported_abi_version():
    with pytest.raises(ValidationError, match="Unsupported abi_version"):
        NodeRegistration(
            abi_version="aindy.extension.node-registration/v9",
            name="demo-node",
            type="webhook",
            handler="https://example.com/node",
        )


def test_webhook_registration_rejects_unsupported_abi_version():
    with pytest.raises(ValidationError, match="Unsupported abi_version"):
        WebhookSubscription(
            abi_version="aindy.extension.webhook-registration/v9",
            event_type="execution.completed",
            callback_url="https://example.com/hook",
        )


def test_flow_registration_rejects_unsupported_abi_version():
    with pytest.raises(ValidationError, match="Unsupported abi_version"):
        FlowDefinition(
            abi_version="aindy.extension.flow-registration/v9",
            name="demo-flow",
            nodes=["alpha"],
            edges={},
            start="alpha",
            end=["alpha"],
        )


def test_flow_registration_defaults_to_current_abi_version():
    model = FlowDefinition(
        name="demo-flow",
        nodes=["alpha"],
        edges={},
        start="alpha",
        end=["alpha"],
    )

    assert model.abi_version == FLOW_REGISTRATION_ABI_V1ALPHA1
