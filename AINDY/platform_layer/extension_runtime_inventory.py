from __future__ import annotations

from typing import Any

from AINDY.platform_layer.extension_policy import (
    OWNER_EXTERNAL_THIRD_PARTY,
    OWNER_FIRST_PARTY_APP,
    OWNER_RUNTIME_BUILTIN,
)
from AINDY.platform_layer.node_registry import list_dynamic_nodes
from AINDY.platform_layer.registry import (
    get_bootstrap_registrations,
    get_loaded_extensions,
)

_OWNER_CLASS_ORDER = (
    OWNER_RUNTIME_BUILTIN,
    OWNER_FIRST_PARTY_APP,
    OWNER_EXTERNAL_THIRD_PARTY,
)


def trusted_python_execution_inventory() -> dict[str, Any]:
    manifest_modules = _trusted_manifest_modules()
    bootstrap_registrations = _trusted_bootstrap_registrations()
    plugin_nodes = _trusted_plugin_nodes()
    owner_class_counts = _owner_class_counts(manifest_modules, plugin_nodes)

    return {
        "present": bool(manifest_modules or plugin_nodes),
        "execution_model": "trusted-in-process-python",
        "sandboxing": "none",
        "total_count": len(manifest_modules) + len(plugin_nodes),
        "manifest_module_count": len(manifest_modules),
        "bootstrap_registration_count": len(bootstrap_registrations),
        "plugin_node_count": len(plugin_nodes),
        "owner_classes_present": [
            owner_class
            for owner_class in _OWNER_CLASS_ORDER
            if owner_class_counts[owner_class] > 0
        ],
        "owner_class_counts": owner_class_counts,
        "manifest_modules": manifest_modules,
        "bootstrap_registrations": bootstrap_registrations,
        "plugin_nodes": plugin_nodes,
        "operator_note": (
            "Trusted Python extensions execute in-process with full interpreter "
            "privileges. This inventory is an audit surface, not a sandbox boundary."
        ),
    }


def trusted_python_execution_summary() -> dict[str, Any]:
    inventory = trusted_python_execution_inventory()
    return {
        "present": inventory["present"],
        "execution_model": inventory["execution_model"],
        "sandboxing": inventory["sandboxing"],
        "total_count": inventory["total_count"],
        "manifest_module_count": inventory["manifest_module_count"],
        "bootstrap_registration_count": inventory["bootstrap_registration_count"],
        "plugin_node_count": inventory["plugin_node_count"],
        "owner_classes_present": list(inventory["owner_classes_present"]),
        "owner_class_counts": dict(inventory["owner_class_counts"]),
        "operator_note": inventory["operator_note"],
    }


def _trusted_manifest_modules() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for entry in get_loaded_extensions():
        if entry.get("execution_model") != "trusted-in-process-python":
            continue
        records.append(
            {
                "module_name": entry.get("module_name"),
                "owner_class": entry.get("owner_class"),
                "trust_class": entry.get("trust_class"),
                "execution_surface": entry.get("execution_surface"),
                "module_origin": entry.get("module_origin"),
                "manifest_owner": entry.get("manifest_owner"),
                "profile_name": entry.get("profile_name"),
                "bootstrap_callable_present": bool(
                    entry.get("bootstrap_callable_present", False)
                ),
                "bootstrap_executed": bool(entry.get("bootstrap_executed", False)),
                "trusted_override_active": bool(
                    entry.get("trusted_override_active", False)
                ),
                "loaded_at": entry.get("loaded_at"),
            }
        )
    return records


def _trusted_bootstrap_registrations() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for name, entry in sorted(get_bootstrap_registrations().items()):
        if entry.get("execution_model") != "trusted-in-process-python":
            continue
        records.append(
            {
                "name": name,
                "module_name": entry.get("module_name"),
                "module_origin": entry.get("module_origin"),
                "owner_class": entry.get("owner_class"),
                "trust_class": entry.get("trust_class"),
                "execution_surface": entry.get("execution_surface"),
                "manifest_owner": entry.get("manifest_owner"),
                "profile_name": entry.get("profile_name"),
                "trusted_override_active": bool(
                    entry.get("trusted_override_active", False)
                ),
                "dependencies": list(entry.get("dependencies") or []),
            }
        )
    return records


def _trusted_plugin_nodes() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for entry in list_dynamic_nodes():
        if entry.get("type") != "plugin":
            continue
        if entry.get("execution_model") != "trusted-in-process-python":
            continue
        records.append(
            {
                "name": entry.get("name"),
                "owner_class": entry.get("owner_class"),
                "trust_class": entry.get("trust_class"),
                "execution_surface": entry.get("execution_surface"),
                "module_name": entry.get("module_name"),
                "function_name": entry.get("function_name"),
                "source_path": entry.get("source_path"),
                "trusted_override_active": bool(
                    entry.get("trusted_override_active", False)
                ),
                "created_at": entry.get("created_at"),
                "created_by": entry.get("created_by"),
            }
        )
    records.sort(key=lambda record: str(record.get("name") or ""))
    return records


def _owner_class_counts(
    manifest_modules: list[dict[str, Any]],
    plugin_nodes: list[dict[str, Any]],
) -> dict[str, int]:
    counts = {owner_class: 0 for owner_class in _OWNER_CLASS_ORDER}
    for entry in [*manifest_modules, *plugin_nodes]:
        owner_class = str(entry.get("owner_class") or "").strip()
        if owner_class in counts:
            counts[owner_class] += 1
    return counts
