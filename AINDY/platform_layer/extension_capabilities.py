from __future__ import annotations

from typing import Any

from AINDY.platform_layer.extension_policy import (
    OWNER_EXTERNAL_THIRD_PARTY,
    OWNER_FIRST_PARTY_APP,
    OWNER_RUNTIME_BUILTIN,
    validate_extension_owner_class,
)

CAP_MEMORY_READ = "memory.read"
CAP_MEMORY_WRITE = "memory.write"
CAP_MEMORY_DELETE = "memory.delete"
CAP_FLOW_RUN = "flow.run"
CAP_EVENT_EMIT = "event.emit"
CAP_TOOL_INVOKE = "tool.invoke"
CAP_OUTBOUND_HTTP = "outbound.http"
# Outbound MCP tool egress (ECOGAP-4 / G4b) — distinct from outbound.http so G4a can
# gate MCP tool calls specifically once activated.
CAP_OUTBOUND_MCP = "outbound.mcp"
PLUGIN_HOST_ENV_ALLOWLIST = [
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "WINDIR",
]

EXTENSION_RUNTIME_CAPABILITIES = {
    CAP_MEMORY_READ,
    CAP_MEMORY_WRITE,
    CAP_MEMORY_DELETE,
    CAP_FLOW_RUN,
    CAP_EVENT_EMIT,
    CAP_TOOL_INVOKE,
    CAP_OUTBOUND_HTTP,
    CAP_OUTBOUND_MCP,
}

TRUSTED_INTERNAL_AUTHORITY = "trusted-internal-ambient-authority"
ISOLATED_CAPABILITY_AUTHORITY = "isolated-explicit-capabilities"
CONTRACT_DRIVEN_AUTHORITY = "contract-driven-surface"


def _first_party_isolated_surface(*, owner_class: str, surface: str) -> bool:
    resolved_owner = validate_extension_owner_class(owner_class)
    return resolved_owner == OWNER_FIRST_PARTY_APP and surface == "dynamic-plugin-node"


def extension_capability_policy() -> dict[str, Any]:
    return {
        "policy_version": "2026-05-20",
        "capabilities": sorted(EXTENSION_RUNTIME_CAPABILITIES),
        "surfaces": {
            "manifest-bootstrap": {
                "authority_model": TRUSTED_INTERNAL_AUTHORITY,
                "supported_runtime_capabilities": [],
                "notes": (
                    "Manifest bootstrap modules remain trusted internal Python code and are "
                    "not capability-confined third-party extension surfaces."
                ),
            },
            "dynamic-plugin-node": {
                "authority_model": ISOLATED_CAPABILITY_AUTHORITY,
                "supported_runtime_capabilities": [
                    CAP_MEMORY_READ,
                    CAP_MEMORY_WRITE,
                    CAP_FLOW_RUN,
                    CAP_EVENT_EMIT,
                    CAP_TOOL_INVOKE,
                    CAP_OUTBOUND_HTTP,
                ],
                "default_runtime_capabilities": [],
                "network_policy": {
                    "default": "deny",
                    "capability_required": CAP_OUTBOUND_HTTP,
                    "private_target_policy": "deny",
                    "private_target_override_env_var": "AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS",
                    "enforcement_scope": "plugin-host socket connect APIs",
                    "notes": (
                        "Network access is denied unless outbound.http is granted. "
                        "When granted, loopback and literal private/non-routable targets remain denied "
                        "unless the explicit operator override is enabled. Hostname-based filtering "
                        "does not inspect every downstream resolved IP."
                    ),
                },
                "filesystem_policy": {
                    "default": "read-only-approved-roots",
                    "allowed_read_scope": ["plugin_root", "python_runtime"],
                    "writes": "deny",
                    "enforcement_scope": "plugin-host standard Python file APIs",
                    "notes": (
                        "The plugin host blocks standard Python file opens and directory listing "
                        "outside approved read-only roots needed for plugin execution, and denies writes. "
                        "This is not a full OS sandbox."
                    ),
                },
                "environment_policy": {
                    "default": "allowlist",
                    "allowed_keys": list(PLUGIN_HOST_ENV_ALLOWLIST),
                    "secret_injection": "none",
                },
                "secret_policy": {
                    "default": "deny",
                    "exposed_capabilities": [],
                },
                "notes": (
                    "External third-party plugin nodes run in an isolated subprocess with "
                    "default-deny runtime capabilities."
                ),
            },
            "webhook-node": {
                "authority_model": CONTRACT_DRIVEN_AUTHORITY,
                "supported_runtime_capabilities": [CAP_OUTBOUND_HTTP],
                "default_runtime_capabilities": [CAP_OUTBOUND_HTTP],
                "notes": "Webhook nodes are outbound HTTP integrations only.",
            },
            "webhook-subscription": {
                "authority_model": CONTRACT_DRIVEN_AUTHORITY,
                "supported_runtime_capabilities": [CAP_OUTBOUND_HTTP],
                "default_runtime_capabilities": [CAP_OUTBOUND_HTTP],
                "notes": "Webhook subscriptions are outbound HTTP integrations only.",
            },
            "dynamic-flow": {
                "authority_model": CONTRACT_DRIVEN_AUTHORITY,
                "supported_runtime_capabilities": [],
                "default_runtime_capabilities": [],
                "notes": "Dynamic flows are data-only registrations.",
            },
        },
        "not_exposed": [
            "secret.read",
            "config.read",
        ],
        "notes": (
            "No extension receives live DB sessions or internal runtime objects over the "
            "isolated third-party boundary. Secret/config reads are not exposed as "
            "extension capabilities."
        ),
    }


def normalize_extension_capabilities(
    *,
    owner_class: str,
    surface: str,
    requested: list[str] | None = None,
) -> list[str]:
    resolved_owner = validate_extension_owner_class(owner_class)
    policy = extension_capability_policy()["surfaces"][surface]
    if resolved_owner == OWNER_RUNTIME_BUILTIN:
        return []
    if resolved_owner == OWNER_FIRST_PARTY_APP and not _first_party_isolated_surface(
        owner_class=resolved_owner,
        surface=surface,
    ):
        return []
    requested_caps = sorted(
        {
            str(value).strip()
            for value in (requested or [])
            if isinstance(value, str) and str(value).strip()
        }
    )
    supported = set(policy["supported_runtime_capabilities"])
    unsupported = [cap for cap in requested_caps if cap not in supported]
    if unsupported:
        raise ValueError(
            f"{surface} does not support requested capabilities {unsupported!r}; "
            f"supported capabilities: {sorted(supported)!r}"
        )
    default_caps = sorted(set(policy.get("default_runtime_capabilities") or []))
    if resolved_owner == OWNER_FIRST_PARTY_APP and _first_party_isolated_surface(
        owner_class=resolved_owner,
        surface=surface,
    ):
        return sorted(set(default_caps) | set(requested_caps))
    if resolved_owner != OWNER_EXTERNAL_THIRD_PARTY:
        return default_caps
    return sorted(set(default_caps) | set(requested_caps))


def extension_authority_model(*, owner_class: str, surface: str) -> str:
    resolved_owner = validate_extension_owner_class(owner_class)
    if resolved_owner == OWNER_RUNTIME_BUILTIN:
        return TRUSTED_INTERNAL_AUTHORITY
    if resolved_owner == OWNER_FIRST_PARTY_APP and not _first_party_isolated_surface(
        owner_class=resolved_owner,
        surface=surface,
    ):
        return TRUSTED_INTERNAL_AUTHORITY
    return str(extension_capability_policy()["surfaces"][surface]["authority_model"])


def extension_resource_access_summary(
    *,
    owner_class: str,
    surface: str,
    granted_capabilities: list[str] | None = None,
) -> dict[str, Any]:
    resolved_owner = validate_extension_owner_class(owner_class)
    if resolved_owner == OWNER_RUNTIME_BUILTIN:
        return {
            "authority_model": TRUSTED_INTERNAL_AUTHORITY,
            "network": {"policy": "trusted-internal"},
            "filesystem": {"policy": "trusted-internal"},
            "environment": {"policy": "trusted-internal"},
            "secret_access": {"policy": "trusted-internal"},
        }
    if resolved_owner == OWNER_FIRST_PARTY_APP and not _first_party_isolated_surface(
        owner_class=resolved_owner,
        surface=surface,
    ):
        return {
            "authority_model": TRUSTED_INTERNAL_AUTHORITY,
            "network": {"policy": "trusted-internal"},
            "filesystem": {"policy": "trusted-internal"},
            "environment": {"policy": "trusted-internal"},
            "secret_access": {"policy": "trusted-internal"},
        }
    surface_policy = extension_capability_policy()["surfaces"][surface]
    return {
        "authority_model": str(surface_policy["authority_model"]),
        "granted_capabilities": list(granted_capabilities or []),
        "network": dict(surface_policy.get("network_policy") or {"policy": "contract-driven"}),
        "filesystem": dict(surface_policy.get("filesystem_policy") or {"policy": "contract-driven"}),
        "environment": dict(surface_policy.get("environment_policy") or {"policy": "contract-driven"}),
        "secret_access": dict(surface_policy.get("secret_policy") or {"policy": "deny"}),
    }
