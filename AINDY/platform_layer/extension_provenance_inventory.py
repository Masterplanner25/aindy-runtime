from __future__ import annotations

from typing import Any

from AINDY.platform_layer.extension_provenance import summarize_extension_provenance


def extension_provenance_inventory() -> dict[str, Any]:
    from AINDY.platform_layer.event_service import list_webhooks
    from AINDY.platform_layer.node_registry import list_dynamic_nodes
    from AINDY.platform_layer.registry import (
        get_bootstrap_registrations,
        get_loaded_extensions,
    )
    from AINDY.runtime.flow_registry import list_dynamic_flows

    entries: list[dict[str, Any]] = []

    for record in get_loaded_extensions():
        provenance = record.get("provenance")
        if isinstance(provenance, dict):
            entries.append(
                {
                    "name": record.get("module_name"),
                    "surface": "manifest-bootstrap",
                    "owner_class": record.get("owner_class"),
                    "verification": provenance.get("verification"),
                    "extension_id": provenance.get("extension_id"),
                    "version": provenance.get("version"),
                    "source_type": provenance.get("source_type"),
                    "source_ref": provenance.get("source_ref"),
                }
            )

    for name, record in get_bootstrap_registrations().items():
        provenance = record.get("provenance")
        if isinstance(provenance, dict):
            entries.append(
                {
                    "name": name,
                    "surface": "bootstrap-registration",
                    "owner_class": record.get("owner_class"),
                    "verification": provenance.get("verification"),
                    "extension_id": provenance.get("extension_id"),
                    "version": provenance.get("version"),
                    "source_type": provenance.get("source_type"),
                    "source_ref": provenance.get("source_ref"),
                }
            )

    for record in list_dynamic_nodes():
        provenance = record.get("provenance")
        if isinstance(provenance, dict):
            entries.append(
                {
                    "name": record.get("name"),
                    "surface": record.get("execution_surface") or "dynamic-node",
                    "owner_class": record.get("owner_class"),
                    "verification": provenance.get("verification"),
                    "extension_id": provenance.get("extension_id"),
                    "version": provenance.get("version"),
                    "source_type": provenance.get("source_type"),
                    "source_ref": provenance.get("source_ref"),
                }
            )

    for record in list_dynamic_flows():
        provenance = record.get("provenance")
        if isinstance(provenance, dict):
            entries.append(
                {
                    "name": record.get("name"),
                    "surface": "dynamic-flow",
                    "owner_class": record.get("owner_class"),
                    "verification": provenance.get("verification"),
                    "extension_id": provenance.get("extension_id"),
                    "version": provenance.get("version"),
                    "source_type": provenance.get("source_type"),
                    "source_ref": provenance.get("source_ref"),
                }
            )

    for record in list_webhooks():
        provenance = record.get("provenance")
        if isinstance(provenance, dict):
            entries.append(
                {
                    "name": record.get("id"),
                    "surface": "webhook-subscription",
                    "owner_class": record.get("owner_class"),
                    "verification": provenance.get("verification"),
                    "extension_id": provenance.get("extension_id"),
                    "version": provenance.get("version"),
                    "source_type": provenance.get("source_type"),
                    "source_ref": provenance.get("source_ref"),
                }
            )

    return summarize_extension_provenance(entries)
