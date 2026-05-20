from __future__ import annotations

from AINDY.config import settings
from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY
from AINDY.platform_layer.deployment_contract import runtime_only_deployment_contract


PUBLIC_CONTRACT_SCHEMA_VERSION = "2026-05-20"
STABILITY_STABLE = "stable"
STABILITY_EXPERIMENTAL = "experimental"


def runtime_public_contract_metadata() -> dict[str, object]:
    return {
        "schema_version": PUBLIC_CONTRACT_SCHEMA_VERSION,
        "release_posture": _release_posture_contract(),
        "policy": (
            "Stable surfaces are expected to remain compatible within the current "
            "runtime and API MAJOR series. Experimental surfaces may change in "
            "minor releases and must not be treated as long-term contracts."
        ),
        "api_major": settings.API_VERSION.split(".")[0],
        "http": _http_surface_contract(),
        "syscalls": _syscall_surface_contract(),
        "extensions": _extension_surface_contract(),
        "runtime_only_boot": _runtime_only_boot_surface_contract(),
    }


def _release_posture_contract() -> dict[str, object]:
    return {
        "support_tier": "trusted-internal",
        "not_claimed": [
            "third-party extension isolation",
            "sandboxed in-process plugin execution",
            "fully frozen external platform semantics outside declared stable surfaces",
        ],
        "suitable_for": [
            "runtime-only internal deployments",
            "first-party app integrations under the documented trust model",
            "operator-managed deployments that accept explicit experimental surfaces",
        ],
        "operator_scope": (
            "Health and readiness report dependency state for the active deployment "
            "profile. They do not certify plugin isolation, third-party code trust, "
            "or generalized multi-tenant platform hardening."
        ),
    }


def _http_surface_contract() -> dict[str, object]:
    return {
        "stable": [
            {
                "route": "GET /api/version",
                "notes": "Versioned runtime compatibility and public contract metadata.",
            },
            {
                "route": "GET /health",
                "notes": "Operator health surface with runtime condition reporting.",
            },
            {
                "route": "GET /ready",
                "notes": (
                    "Operator readiness surface with fail-fast dependency checks for the "
                    "active deployment profile. Ready does not imply sandboxing or "
                    "third-party extension trust."
                ),
            },
            {
                "route": "GET /platform/syscalls",
                "notes": "Versioned syscall catalog including per-entry stable and deprecated markers.",
            },
            {
                "route": "POST /platform/syscall",
                "notes": "Versioned syscall dispatch envelope for public runtime integration.",
            },
        ],
        "experimental": [
            {
                "route_prefix": "/apps/agent/",
                "notes": "Agent runtime HTTP semantics and orchestration behavior are still evolving.",
            },
            {
                "route_prefix": "/apps/memory/",
                "notes": "Runtime-owned memory APIs are shipped, but the external HTTP surface is not frozen.",
            },
            {
                "route_prefix": "/apps/coordination/",
                "notes": "Coordination routes are available but not yet a stable public contract.",
            },
            {
                "route_prefix": "/platform/flows",
                "notes": "Dynamic flow registration and management remain experimental.",
            },
            {
                "route_prefix": "/platform/nodes",
                "notes": "Dynamic external node registration remains experimental.",
            },
            {
                "route_prefix": "/platform/nodus",
                "notes": "Nodus upload and script management endpoints are still fluid.",
            },
            {
                "route_prefix": "/platform/webhooks",
                "notes": "Webhook subscription APIs are runtime-owned but not yet declared stable.",
            },
        ],
    }


def _syscall_surface_contract() -> dict[str, object]:
    stable_entries: list[str] = []
    experimental_entries: list[str] = []
    deprecated_entries: list[str] = []

    for full_name, entry in sorted(SYSCALL_REGISTRY.items()):
        if getattr(entry, "deprecated", False):
            deprecated_entries.append(full_name)
        if getattr(entry, "stable", True):
            stable_entries.append(full_name)
        else:
            experimental_entries.append(full_name)

    return {
        "stable_versions": ["v1"],
        "experimental_versions": ["v2"],
        "stable_entry_count": len(stable_entries),
        "experimental_entry_count": len(experimental_entries),
        "experimental_entries": experimental_entries,
        "deprecated_entries": deprecated_entries,
        "notes": (
            "The syscall ABI is versioned. Per-entry stability is authoritative: "
            "clients must inspect the stable marker from /platform/syscalls "
            "instead of assuming every syscall in a stable version is itself stable. "
            "Stable syscall status does not imply that every surrounding orchestration "
            "or extension surface is equally stable."
        ),
    }


def _extension_surface_contract() -> dict[str, object]:
    return {
        "stable": [],
        "experimental": [
            {
                "surface": "manifest bootstrap modules",
                "entrypoint": "AINDY.platform_layer.registry.load_plugins()",
                "notes": "Bootstrap module naming and registration composition remain fluid.",
            },
            {
                "surface": "registry registration helpers",
                "entrypoint": "AINDY.platform_layer.registry.register_*",
                "notes": "Registration helper shapes are public for current integration but not yet declared stable.",
            },
            {
                "surface": "agent tool registration",
                "entrypoint": "AINDY.agents.tool_registry.register_tool",
                "notes": "Tool metadata keys are validated but may still evolve.",
            },
            {
                "surface": "dynamic plugin nodes",
                "entrypoint": "AINDY.platform_layer.node_registry.register_external_node(type='plugin')",
                "notes": "Trusted in-process code loading remains experimental and intentionally unsandboxed.",
            },
            {
                "surface": "webhook nodes",
                "entrypoint": "AINDY.platform_layer.node_registry.register_external_node(type='webhook')",
                "notes": "Outbound node contract is supported but the registration surface is not frozen.",
            },
            {
                "surface": "dynamic flows",
                "entrypoint": "AINDY.runtime.flow_registry.register_dynamic_flow",
                "notes": "Data-only flow registration is runtime-owned but still experimental.",
            },
        ],
    }


def _runtime_only_boot_surface_contract() -> dict[str, object]:
    contract = runtime_only_deployment_contract()
    return {
        "stability": STABILITY_STABLE,
        "boot_mode": contract["boot_mode"],
        "boot_profile": contract["boot_profile"],
        "required_routes": contract["mounted_routes"]["required_routes"],
        "baseline_tools": contract["baseline_agent_capabilities"]["tools"],
        "notes": (
            "Runtime-only boot is a supported external boot contract for the runtime "
            "surface. It remains strict about schema, readiness, and dependency "
            "checks, but it does not imply third-party extension isolation or a "
            "fully frozen external platform beyond the declared stable surfaces."
        ),
    }
