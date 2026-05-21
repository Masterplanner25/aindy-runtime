from __future__ import annotations

import importlib
from collections import defaultdict

import pytest

from AINDY.platform_layer import registry
from AINDY.platform_layer.extension_policy import (
    OWNER_EXTERNAL_THIRD_PARTY,
    OWNER_FIRST_PARTY_APP,
    OWNER_RUNTIME_BUILTIN,
    validate_bootstrap_module_name,
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
    "_routers": [],
    "_root_routers": [],
    "_legacy_root_routers": [],
    "_syscalls": {},
    "_jobs": {},
    "_flows": [],
    "_flow_result_keys": {},
    "_flow_result_extractors": {},
    "_flow_completion_events": {},
    "_flow_plans": {},
    "_event_handlers": defaultdict(list),
    "_event_types": set(),
    "_capture_rules": {},
    "_memory_policies": {},
    "_scheduled_jobs": {},
    "_response_adapters": {},
    "_route_guards": {},
    "_execution_adapters": {},
    "_startup_hooks": [],
    "_agent_tools": {},
    "_agent_planner_contexts": {},
    "_agent_planner_backends": {},
    "_agent_run_tools": {},
    "_agent_completion_hooks": defaultdict(list),
    "_agent_event_emitters": defaultdict(list),
    "_agent_ranking_strategy": None,
    "_trigger_evaluators": {},
    "_flow_strategies": {},
    "_capability_definitions": {},
    "_capability_definition_providers": [],
    "_tool_capabilities": {},
    "_agent_capabilities": {},
    "_restricted_tools": set(),
    "_symbols": {},
    "_core_domains": [],
    "_degraded_domains": [],
    "_health_checks": {},
    "_runtime_agent_defaults_loaded": False,
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


def test_validate_bootstrap_module_name_rejects_non_runtime_entry_in_runtime_manifest():
    with pytest.raises(ValueError, match="runtime-owned manifests may only declare runtime-built-in extensions"):
        validate_bootstrap_module_name(
            "apps.bootstrap",
            owner_class=OWNER_FIRST_PARTY_APP,
            manifest_owner="runtime",
        )


def test_resolve_plugin_profile_entries_preserves_declared_extension_ownership(monkeypatch, tmp_path, clean_registry_state):
    manifest = tmp_path / "aindy_plugins.json"
    manifest.write_text(
        """
{
  "default_profile": "default-apps",
  "profiles": {
    "default-apps": {
      "plugins": [
        "apps.bootstrap",
        {"module": "vendor.demo.bootstrap", "owner_class": "external-third-party"}
      ]
    }
  }
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("AINDY_EXTERNAL_BOOTSTRAP_PREFIXES", "vendor.")

    profile, entries = registry.resolve_plugin_profile_entries(manifest_path=manifest, profile="default-apps")

    assert profile == "default-apps"
    assert entries == [
        {"module_name": "apps.bootstrap", "owner_class": OWNER_FIRST_PARTY_APP},
        {"module_name": "vendor.demo.bootstrap", "owner_class": OWNER_EXTERNAL_THIRD_PARTY},
    ]


def test_runtime_owned_manifest_rejects_non_runtime_extension_entries(monkeypatch, tmp_path, clean_registry_state):
    manifest = tmp_path / "runtime_plugins.json"
    manifest.write_text(
        """
{
  "default_profile": "platform-only",
  "profiles": {
    "platform-only": {
      "plugins": [
        {"module": "apps.bootstrap", "owner_class": "first-party-app"}
      ]
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("AINDY_BOOT_MODE", "runtime-only")
    monkeypatch.setattr(registry, "_default_runtime_manifest_path", lambda: manifest)

    with pytest.raises(ValueError, match="runtime-owned manifests may only declare runtime-built-in extensions"):
        registry.resolve_plugin_profile_entries(profile="platform-only")


def test_load_plugins_allows_runtime_built_in_manifest_module(monkeypatch, tmp_path, clean_registry_state):
    manifest = tmp_path / "runtime_plugins.json"
    manifest.write_text(
        """
{
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
    assert registry.get_loaded_extensions() == [
        {
            "module_name": "AINDY.platform_layer.runtime_agent_defaults",
            "owner_class": OWNER_RUNTIME_BUILTIN,
            "trust_class": "trusted-runtime-python",
            "execution_model": "trusted-in-process-python",
            "sandboxing": "none",
            "trusted_override_active": False,
            "manifest_owner": "explicit",
            "profile_name": "platform-only",
        }
    ]


def test_load_plugins_allows_first_party_trusted_integrations(monkeypatch, tmp_path, clean_registry_state):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    (apps_dir / "__init__.py").write_text("", encoding="utf-8")
    (apps_dir / "test_bootstrap.py").write_text(
        """
from AINDY.platform_layer.registry import publish_bootstrap_registration

def bootstrap():
    publish_bootstrap_registration("demo-app")
""".strip(),
        encoding="utf-8",
    )

    manifest = tmp_path / "aindy_plugins.json"
    manifest.write_text(
        """
{
  "default_profile": "default-apps",
  "profiles": {
    "default-apps": {
      "plugins": [
        {"module": "apps.test_bootstrap", "owner_class": "first-party-app"}
      ]
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.syspath_prepend(str(tmp_path))

    loaded = registry.load_plugins(manifest_path=manifest, profile="default-apps")

    assert loaded == ["apps.test_bootstrap"]
    assert registry.get_registered_apps() == ["demo-app"]
    assert registry.get_loaded_extensions() == [
        {
            "module_name": "apps.test_bootstrap",
            "owner_class": OWNER_FIRST_PARTY_APP,
            "trust_class": "trusted-first-party-python",
            "execution_model": "trusted-in-process-python",
            "sandboxing": "none",
            "trusted_override_active": False,
            "manifest_owner": "explicit",
            "profile_name": "default-apps",
        }
    ]
    assert registry.get_bootstrap_registrations() == {
        "demo-app": {
            "name": "demo-app",
            "owner_class": OWNER_FIRST_PARTY_APP,
            "trust_class": "trusted-first-party-python",
            "execution_model": "trusted-in-process-python",
            "sandboxing": "none",
            "trusted_override_active": False,
            "module_name": "apps.test_bootstrap",
            "dependencies": [],
        }
    }


def test_load_plugins_blocks_external_python_bootstrap_by_default(monkeypatch, tmp_path, clean_registry_state):
    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()
    (vendor_dir / "__init__.py").write_text("", encoding="utf-8")
    (vendor_dir / "ext_bootstrap.py").write_text(
        """
from AINDY.platform_layer.registry import publish_bootstrap_registration

def bootstrap():
    publish_bootstrap_registration("vendor-demo")
""".strip(),
        encoding="utf-8",
    )

    manifest = tmp_path / "aindy_plugins.json"
    manifest.write_text(
        """
{
  "default_profile": "default-apps",
  "profiles": {
    "default-apps": {
      "plugins": [
        {"module": "vendor.ext_bootstrap", "owner_class": "external-third-party"}
      ]
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("AINDY_EXTERNAL_BOOTSTRAP_PREFIXES", "vendor.")
    monkeypatch.delenv("AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS", raising=False)

    with pytest.raises(ValueError, match="blocked by default"):
        registry.load_plugins(manifest_path=manifest, profile="default-apps")


def test_load_plugins_allows_external_python_only_with_explicit_override(
    monkeypatch, tmp_path, clean_registry_state
):
    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()
    (vendor_dir / "__init__.py").write_text("", encoding="utf-8")
    (vendor_dir / "ext_bootstrap.py").write_text(
        """
from AINDY.platform_layer.registry import publish_bootstrap_registration

def bootstrap():
    publish_bootstrap_registration("vendor-demo")
""".strip(),
        encoding="utf-8",
    )

    manifest = tmp_path / "aindy_plugins.json"
    manifest.write_text(
        """
{
  "default_profile": "default-apps",
  "profiles": {
    "default-apps": {
      "plugins": [
        {"module": "vendor.ext_bootstrap", "owner_class": "external-third-party"}
      ]
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("AINDY_EXTERNAL_BOOTSTRAP_PREFIXES", "vendor.")
    monkeypatch.setenv("AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS", "true")

    loaded = registry.load_plugins(manifest_path=manifest, profile="default-apps")

    assert loaded == ["vendor.ext_bootstrap"]
    assert registry.get_loaded_extensions() == [
        {
            "module_name": "vendor.ext_bootstrap",
            "owner_class": OWNER_EXTERNAL_THIRD_PARTY,
            "trust_class": "trusted-external-python-override",
            "execution_model": "trusted-in-process-python",
            "sandboxing": "none",
            "trusted_override_active": True,
            "manifest_owner": "explicit",
            "profile_name": "default-apps",
        }
    ]
    assert registry.get_bootstrap_registrations() == {
        "vendor-demo": {
            "name": "vendor-demo",
            "owner_class": OWNER_EXTERNAL_THIRD_PARTY,
            "trust_class": "trusted-external-python-override",
            "execution_model": "trusted-in-process-python",
            "sandboxing": "none",
            "trusted_override_active": True,
            "module_name": "vendor.ext_bootstrap",
            "dependencies": [],
        }
    }


def test_runtime_only_profile_stays_free_of_app_and_external_bootstrap(clean_registry_state):
    import AINDY.startup as startup

    startup = importlib.reload(startup)

    assert registry.get_active_plugin_profile() == "platform-only"
    assert registry.get_registered_apps() == []
    assert registry.get_loaded_extensions() == []
