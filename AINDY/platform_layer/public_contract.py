from __future__ import annotations

from AINDY.config import settings
from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY
from AINDY.platform_layer.deployment_contract import (
    DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY,
    hostile_third_party_attestation_requirements,
    runtime_only_deployment_contract,
)
from AINDY.platform_layer.extension_abi import extension_abi_policy
from AINDY.platform_layer.extension_capabilities import extension_capability_policy
from AINDY.platform_layer.extension_execution_model import (
    extension_execution_model_contract,
)
from AINDY.platform_layer.extension_policy import external_python_override_state
from AINDY.platform_layer.extension_provenance import extension_provenance_policy
from AINDY.platform_layer.sandbox_certification import (
    sandbox_certification_contract,
    sandbox_certification_profile,
)
from AINDY.platform_layer.sandbox_runner import (
    RUNNER_SELECTION_AUTO,
    RUNNER_INSECURE_DEV_SUBPROCESS,
    resolve_sandbox_runner_type,
    SANDBOX_RUNNER_INTERFACE_VERSION,
    list_supported_sandbox_runners,
    sandbox_platform_capability_matrix,
)


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
    override_state = external_python_override_state()
    abi_policy = extension_abi_policy()
    capability_policy = extension_capability_policy()
    return {
        "stable": [
            {
                "surface": "extension manifest",
                "entrypoint": "AINDY.platform_layer.registry.load_plugins()",
                "abi_versions": abi_policy["surfaces"]["manifest"]["supported_versions"],
                "notes": (
                    "The manifest document shape is a stable ABI. It supports "
                    "trusted bootstrap entries for internal code and declarative "
                    "registration entries for external onboarding. It does not make "
                    "every extension execution surface stable or safe by itself."
                ),
            }
        ],
        "abi": abi_policy,
        "execution_models": extension_execution_model_contract(),
        "capability_model": capability_policy,
        "provenance_policy": extension_provenance_policy(),
        "sandbox_runners": {
            "interface_version": SANDBOX_RUNNER_INTERFACE_VERSION,
            "configured_selection": RUNNER_SELECTION_AUTO,
            "default_external_runner": resolve_sandbox_runner_type(),
            "available_runners": list_supported_sandbox_runners(),
            "certification_contract": sandbox_certification_contract(),
            "active_certification_profile": sandbox_certification_profile(
                runner_type=resolve_sandbox_runner_type()
            ),
            "platform_matrix": sandbox_platform_capability_matrix(),
            "operator_reporting": {
                "version_surface": "runtime.plugin_sandbox_attestation",
                "health_surface": "plugin_sandbox_attestation",
                "readiness_surface": "checks.plugin_sandbox_attestation",
                "assurance_posture_surface": "runtime.plugin_sandbox_posture",
                "platform_matrix_surface": "runtime.plugin_sandbox_platform",
                "attestation_fields": [
                    "runner_type",
                    "execution_model_class",
                    "assurance_class",
                    "isolation_class",
                    "certification",
                    "requested_hardening_controls",
                    "active_hardening_controls",
                    "verified_hardening_controls",
                    "effective_resource_limits",
                    "launch_attestation",
                    "mount_isolation",
                    "runtime_identity",
                    "runtime_identity.trust_chain",
                    "assurance_properties",
                    "post_launch_verification",
                    "post_launch_verification.verification_method",
                    "network_isolation",
                    "mount_isolation.live_verification",
                    "network_isolation.live_verification",
                    "network_policy",
                    "filesystem_policy",
                    "provenance_status",
                ],
                "attestation_model": {
                    "assurance_class": "the current runner category reported by the runtime",
                    "required_assurance_class": "the minimum class required by the active deployment profile",
                    "coverage": "plugin sandbox attestation and certification cover isolated plugin-host execution only",
                    "requested": "operator-configured or runner-requested policy",
                    "active": "runner metadata for controls the runtime expects to be active",
                    "verified": "launch-observed backend identity and command evidence only",
                    "verification_method": "how post-launch verification evidence was obtained",
                    "post_launch_verified": "live worker continuity and guard-state checks over a runtime-owned authenticated probe",
                    "live_mount_and_network": "partial live verification that the worker still experiences read-only artifact behavior, writable temp scope, host-path denial, and deny-by-default socket policy where those probes are applicable",
                    "runtime_trust_chain": "runtime-owned trust policy result combining digest pinning with source, issuer, signing-status, and base-compatibility metadata",
                    "assurance_properties": "machine-readable properties that distinguish process containment, container-grade sandboxing, and strong sandbox behavior",
                    "certification_tier": "derived only from runner-specific verified evidence and shared worker-policy eligibility",
                    "mount_and_network": "mount/network isolation claims are verified only when the runtime observes launch arguments or resolved backend identity proving them",
                },
            },
            "selection_policy": {
                "explicit_setting_env_var": "AINDY_PLUGIN_SANDBOX_RUNNER",
                "auto_single_instance": RUNNER_INSECURE_DEV_SUBPROCESS,
                "auto_distributed": "containerized_oci",
                "strong_runner_requires_explicit_selection": "strong_sandbox_vm",
                "pinned_runtime_identity_required_for_production_safe_profiles": True,
                "trusted_runtime_identity_chain_required_for_production_safe_profiles": True,
                "hostile_third_party_profile": hostile_third_party_attestation_requirements(),
            },
            "support_contract": {
                "strong_sandbox_supported_host_platforms": sandbox_platform_capability_matrix()[
                    "support_contract"
                ]["strong_sandbox_supported_host_platforms"],
                "hostile_third_party_supported_host_platforms": sandbox_platform_capability_matrix()[
                    "support_contract"
                ]["hostile_third_party_supported_host_platforms"],
                "notes": (
                    "Windows and macOS may report contained-process or container-grade runner availability, "
                    "but they are not part of the declared strong-sandbox or hostile-workload support set."
                ),
            },
            "assurance_classes": [
                "insecure-dev",
                "container-grade-sandbox",
                "strong-sandbox-tier",
            ],
            "notes": (
                "Third-party plugin-host execution targets a runtime-owned sandbox runner "
                "interface. The current subprocess-backed runner is a containment boundary, "
                "not a sandbox guarantee. When the container runner or strong sandbox VM runner "
                "is selected, the runtime fails closed if the required runtime, launcher, image, "
                "or trusted pinned runtime identity chain is unavailable. Verified attestation reflects only "
                "what the runtime directly observes at launch time, and post-launch verification reflects "
                "only live worker continuity and guard-state probes, not a blanket proof of ongoing "
                "kernel enforcement. "
                f"{DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY!r} is the explicit fail-closed "
                "profile for hostile or semi-trusted third-party workloads and requires "
                "strong_sandbox_vm plus live verified host attestation. Operators must "
                "distinguish assurance class, attestation, and certification tier; they are "
                "related but not interchangeable. Linux is the only declared fully supported "
                "host platform for strong_sandbox_vm and hostile third-party plugin execution."
            ),
        },
        "ownership_classes": [
            "runtime-built-in",
            "first-party-app",
            "external-third-party",
        ],
        "trusted_in_process_python": {
            "owner_classes": [
                "runtime-built-in",
                "first-party-app",
            ],
            "execution_model": "trusted in-process Python execution",
            "sandboxing": "none",
            "explicit_exceptions": [
                "manifest bootstrap modules",
            ],
            "capability_boundary": {
                "mode": "explicit-runtime-owned-mediation",
                "first_party_bootstrap_default": "full-runtime-owned-allowlist",
                "runtime_built_in_bootstrap_default": "full-runtime-owned-allowlist",
                "not_claimed": [
                    "sandboxing",
                    "import-level isolation",
                    "kernel-enforced privilege separation",
                ],
            },
            "operator_visibility": [
                "GET /api/version",
                "GET /health",
                "GET /ready",
            ],
            "notes": (
                "Manifest bootstrap remains the explicit privileged exception for "
                "runtime-built-in and first-party app Python. The runtime reports "
                "that in-process inventory for audit visibility rather than claiming "
                "isolation or explicit capability confinement. First-party plugin "
                "nodes and module-style callback providers are isolated where the "
                "runtime can externalize them through runtime-owned worker boundaries. "
                "Residual in-process bootstrap code is confined only through explicit "
                "runtime-owned registration capabilities on official kernel APIs."
            ),
        },
        "external_python_override": {
            "env_var": override_state["env_var"],
            "production_ack_env_var": override_state["production_ack_env_var"],
            "default": "no direct in-process effect",
            "effect_when_enabled": "legacy configuration marker; third-party plugin nodes still require the isolated plugin-host boundary",
            "sandboxing": "subprocess-boundary",
            "notes": (
                "Third-party manifest bootstrap modules remain unsupported. "
                "Third-party plugin nodes execute over a runtime-owned plugin-host subprocess boundary "
                "instead of being imported into the runtime process."
            ),
        },
        "experimental": [
            {
                "surface": "manifest bootstrap modules",
                "entrypoint": "AINDY.platform_layer.registry.load_plugins()",
                "abi_versions": abi_policy["surfaces"]["manifest"]["supported_versions"],
                "notes": (
                    "Manifest parsing is versioned. Manifest v1 is the stable manifest "
                    "ABI, while bootstrap module naming and registration composition "
                    "remain operationally constrained by the trust model."
                ),
            },
            {
                "surface": "manifest declarative extension entries",
                "entrypoint": "AINDY.platform_layer.registry.load_plugins()",
                "abi_versions": abi_policy["surfaces"]["manifest"]["supported_versions"],
                "notes": (
                    "External onboarding may use declarative manifest entries for "
                    "dynamic nodes, webhook subscriptions, and dynamic flows without "
                    "executing bootstrap code in the runtime process. The manifest "
                    "container is stable, but the underlying registration surfaces "
                    "remain experimental."
                ),
            },
            {
                "surface": "registry registration helpers",
                "entrypoint": "AINDY.platform_layer.registry.register_*",
                "notes": "Registration helper shapes are public for current integration but not yet declared stable.",
            },
            {
                "surface": "agent tool registration",
                "entrypoint": "AINDY.agents.tool_registry.register_tool",
                "abi_versions": abi_policy["surfaces"]["agent-tool-registration"]["supported_versions"],
                "notes": "Tool metadata keys are validated but the registration surface may still evolve.",
            },
            {
                "surface": "dynamic plugin nodes",
                "entrypoint": "AINDY.platform_layer.node_registry.register_external_node(type='plugin')",
                "abi_versions": abi_policy["surfaces"]["dynamic-node-registration"]["supported_versions"],
                "notes": "Runtime-built-in plugin nodes remain trusted in-process code. First-party app plugin nodes and third-party plugin nodes use the isolated plugin-host boundary; third-party nodes must also be admitted as verified plugin artifacts.",
            },
            {
                "surface": "webhook nodes",
                "entrypoint": "AINDY.platform_layer.node_registry.register_external_node(type='webhook')",
                "abi_versions": abi_policy["surfaces"]["dynamic-node-registration"]["supported_versions"],
                "notes": "Outbound node contract is supported but the registration surface is not frozen.",
            },
            {
                "surface": "dynamic flows",
                "entrypoint": "AINDY.runtime.flow_registry.register_dynamic_flow",
                "abi_versions": abi_policy["surfaces"]["flow-registration"]["supported_versions"],
                "notes": "Data-only flow registration is runtime-owned but still experimental.",
            },
            {
                "surface": "webhook subscriptions",
                "entrypoint": "AINDY.platform_layer.event_service.subscribe_webhook",
                "abi_versions": abi_policy["surfaces"]["webhook-registration"]["supported_versions"],
                "notes": "Webhook subscription payloads are versioned but still experimental.",
            },
            {
                "surface": "planner backend registration",
                "entrypoint": "AINDY.platform_layer.registry.register_agent_planner_backend",
                "abi_versions": abi_policy["surfaces"]["planner-backend-registration"]["supported_versions"],
                "notes": "Planner backend registration remains a code-level experimental contract.",
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
