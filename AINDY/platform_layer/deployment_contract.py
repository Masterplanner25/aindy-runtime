from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from AINDY.config import settings
from AINDY.platform_layer.extension_execution_model import (
    EXECUTION_MODEL_ISOLATED_EXTERNALIZED,
    extension_execution_model_contract,
)
from AINDY.platform_layer.extension_policy import external_python_override_state
from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile
from AINDY.platform_layer.sandbox_runner import (
    RUNNER_CONTAINERIZED_OCI,
    RUNNER_INSECURE_DEV_SUBPROCESS,
    RUNNER_STRONG_SANDBOX_VM,
    RUNNER_SELECTION_AUTO,
    create_sandbox_runner,
    resolve_sandbox_runner_type,
    sandbox_platform_capability_matrix,
)

BOOT_MODE_ENV_VAR = "AINDY_BOOT_MODE"
DEPLOYMENT_PROFILE_ENV_VAR = "AINDY_DEPLOYMENT_PROFILE"
RUNTIME_ONLY_BOOT_MODE = "runtime-only"
APP_PROFILE_BOOT_MODE = "app-profile"
RUNTIME_ONLY_BOOT_PROFILE = "platform-only"
PROCESS_ROLE_API = "api"
PROCESS_ROLE_WORKER = "worker"
DEPLOYMENT_PROFILE_SINGLE_INSTANCE = "single-instance"
DEPLOYMENT_PROFILE_DISTRIBUTED_API = "distributed-api"
DEPLOYMENT_PROFILE_DISTRIBUTED_WORKER = "distributed-worker"
DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY = "hostile-third-party"
SUPPORTED_DEPLOYMENT_PROFILES = (
    DEPLOYMENT_PROFILE_SINGLE_INSTANCE,
    DEPLOYMENT_PROFILE_DISTRIBUTED_API,
    DEPLOYMENT_PROFILE_DISTRIBUTED_WORKER,
    DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY,
)
RUNTIME_ONLY_REQUIRED_ROUTES = (
    "/health",
    "/ready",
    "/apps/memory/recall",
    "/apps/memory/nodes",
    "/platform/syscalls",
)
RUNTIME_ONLY_REQUIRED_ROUTE_PREFIXES = (
    "/platform/",
    "/apps/memory/",
)
RUNTIME_ONLY_BASELINE_AGENT_TOOLS = (
    "memory.recall",
    "memory.write",
)
RUNTIME_ONLY_BASELINE_AGENT_CAPABILITIES = (
    "execute_flow",
    "read_memory",
    "write_memory",
)
RUNTIME_BASELINE_AGENT_ENRICHMENTS = (
    {
        "type": "planner_context",
        "behavior": "generic runtime-owned planner prompt with empty context block",
    },
    {
        "type": "tool_catalog",
        "behavior": "runtime-owned memory.recall and memory.write only",
    },
    {
        "type": "trigger_evaluator",
        "behavior": "runtime default evaluator with no domain-specific scoring assumptions",
    },
    {
        "type": "suggestions",
        "behavior": "empty unless a plugin registers a provider",
    },
    {
        "type": "completion_hook",
        "behavior": "runtime no-op completion hook",
    },
)
OPTIONAL_PLUGIN_AGENT_ENRICHMENTS = (
    {
        "type": "planner_context",
        "behavior": "KPI-aware prompt enrichment and app-selected memory/planning guidance",
    },
    {
        "type": "suggestions",
        "behavior": "KPI-driven or persisted-loop tool suggestions",
    },
    {
        "type": "completion_hook",
        "behavior": "post-run Infinity orchestration and next_action enrichment",
    },
    {
        "type": "tool_catalog",
        "behavior": "additional app-owned tools such as task, ARM, search, or masterplan actions",
    },
)
AMBIGUOUS_AGENT_ENRICHMENTS = (
    {
        "type": "planner_context",
        "behavior": "memory-context prompt enrichment is domain-agnostic but is currently bundled with KPI-aware app enrichment",
        "refactor_goal": "split runtime-owned memory-context augmentation from app-owned KPI planning context",
    },
    {
        "type": "suggestions",
        "behavior": "KPI suggestion heuristics and persisted-loop replay are duplicated across app provider and owner syscall paths",
        "refactor_goal": "keep the feature plugin-owned but consolidate the implementation behind one owner boundary",
    },
    {
        "type": "completion_hook",
        "behavior": "analytics orchestration currently mutates generic run.result through a completion hook",
        "refactor_goal": "keep orchestration plugin-owned but consider a dedicated post-run enrichment contract instead of an overloaded generic hook",
    },
)
RUNTIME_ONLY_INTENTIONALLY_UNAVAILABLE = (
    "app-domain routers from apps/*",
    "app-owned agent tools beyond runtime defaults",
    "app-owned planner enrichment and suggestion providers",
    "app-owned completion hooks and Infinity orchestration",
    "app-owned syscalls and startup hooks",
)

_api_runtime_state: dict[str, Any] = {
    "process_role": PROCESS_ROLE_API,
    "startup_complete": False,
    "background_enabled": False,
    "scheduler_role": "disabled",
    "background_leadership_mode": "unknown",
    "event_bus_ready": False,
    "boot_mode": "unknown",
    "boot_profile": "unknown",
    "boot_profile_source": "unknown",
    "deployment_profile": "unknown",
    "deployment_profile_source": "unknown",
    "app_plugins_loaded": False,
    "app_plugin_count": 0,
    "external_python_override_active": False,
    "external_python_override_execution_model": "external-python-blocked",
    "runtime_conditions": {},
}

_worker_runtime_state: dict[str, Any] = {
    "process_role": PROCESS_ROLE_WORKER,
    "startup_complete": False,
    "queue_ready": False,
    "schema_ready": False,
    "scheduler_role": "disabled",
    "background_leadership_mode": "unknown",
    "deployment_profile": "unknown",
    "deployment_profile_source": "unknown",
}


def runtime_ui_surface_state() -> dict[str, Any]:
    api_state = get_api_runtime_state()
    boot_mode = api_state.get("boot_mode", "unknown")
    runtime_only = boot_mode == RUNTIME_ONLY_BOOT_MODE
    from AINDY.platform_layer.extension_runtime_inventory import (
        trusted_python_execution_summary,
    )
    from AINDY.platform_layer.extension_provenance_inventory import (
        extension_provenance_inventory,
    )
    from AINDY.platform_layer.plugin_host import plugin_host_inventory
    from AINDY.platform_layer.sandbox_runner import sandbox_platform_capability_matrix

    plugin_hosts = plugin_host_inventory(probe=False)
    return {
        "process_role": api_state.get("process_role", PROCESS_ROLE_API),
        "boot_mode": boot_mode,
        "boot_profile": api_state.get("boot_profile", "unknown"),
        "boot_profile_source": api_state.get("boot_profile_source", "unknown"),
        "deployment_profile": api_state.get("deployment_profile", "unknown"),
        "deployment_profile_source": api_state.get("deployment_profile_source", "unknown"),
        "background_leadership_mode": api_state.get(
            "background_leadership_mode",
            "unknown",
        ),
        "app_plugins_loaded": bool(api_state.get("app_plugins_loaded", False)),
        "app_plugin_count": int(api_state.get("app_plugin_count", 0) or 0),
        "external_python_override_active": bool(
            api_state.get("external_python_override_active", False)
        ),
        "external_python_override_execution_model": str(
            api_state.get(
                "external_python_override_execution_model",
                "external-python-blocked",
            )
        ),
        "trusted_python_execution": trusted_python_execution_summary(),
        "extension_execution_posture": extension_execution_model_contract(),
        "extension_provenance": extension_provenance_inventory(),
        "plugin_hosts": plugin_hosts,
        "plugin_sandbox_attestation": dict(plugin_hosts.get("sandbox_attestation") or {}),
        "plugin_sandbox_posture": plugin_sandbox_assurance_posture(
            api_state.get("deployment_profile")
        ),
        "plugin_sandbox_platform": sandbox_platform_capability_matrix(),
        "ui_mode": RUNTIME_ONLY_BOOT_MODE if runtime_only else APP_PROFILE_BOOT_MODE,
        "default_route": "/memory" if runtime_only else "/dashboard",
        "platform_home": "/platform/agent",
    }


def background_tasks_enabled() -> bool:
    if settings.is_testing or os.getenv("PYTEST_CURRENT_TEST"):
        return False
    return os.getenv("AINDY_ENABLE_BACKGROUND_TASKS", "true").lower() in {
        "1",
        "true",
        "yes",
    }


def _deployment_profile_contracts() -> dict[str, dict[str, Any]]:
    return {
        DEPLOYMENT_PROFILE_SINGLE_INSTANCE: {
            "name": DEPLOYMENT_PROFILE_SINGLE_INSTANCE,
            "process_role": PROCESS_ROLE_API,
            "stability": "stable",
            "summary": (
                "Single-process API runtime. Thread-mode execution, optional Redis, "
                "no separate worker requirement."
            ),
            "execution_mode": "thread",
            "required_dependencies": {
                "postgres": True,
                "schema_enforcement": True,
                "redis": False,
                "event_bus": False,
                "queue_backend": False,
                "worker_process": False,
            },
            "background_leadership_mode": "in-process",
            "supports_runtime_only_boot": True,
        },
        DEPLOYMENT_PROFILE_DISTRIBUTED_API: {
            "name": DEPLOYMENT_PROFILE_DISTRIBUTED_API,
            "process_role": PROCESS_ROLE_API,
            "stability": "stable",
            "summary": (
                "API process in a distributed topology. Requires Redis-backed queue, "
                "cross-instance event bus, and at least one worker process."
            ),
            "execution_mode": "distributed",
            "required_dependencies": {
                "postgres": True,
                "schema_enforcement": True,
                "redis": True,
                "event_bus": True,
                "queue_backend": True,
                "worker_process": True,
            },
            "background_leadership_mode": "lease-elected",
            "supports_runtime_only_boot": True,
        },
        DEPLOYMENT_PROFILE_DISTRIBUTED_WORKER: {
            "name": DEPLOYMENT_PROFILE_DISTRIBUTED_WORKER,
            "process_role": PROCESS_ROLE_WORKER,
            "stability": "stable",
            "summary": (
                "Distributed async worker. Requires Redis-backed queue and the "
                "runtime-owned schema; participates in lease-based background leadership."
            ),
            "execution_mode": "distributed",
            "required_dependencies": {
                "postgres": True,
                "schema_enforcement": True,
                "redis": True,
                "event_bus": False,
                "queue_backend": True,
                "worker_process": False,
            },
            "background_leadership_mode": "lease-elected",
            "supports_runtime_only_boot": False,
        },
        DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY: {
            "name": DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY,
            "process_role": PROCESS_ROLE_API,
            "stability": "experimental",
            "summary": (
                "Distributed API profile for hostile or semi-trusted third-party "
                "extension workloads. Requires strong sandbox VM execution, pinned "
                "sandbox runtime identity, and fail-closed verified sandbox host "
                "attestation for external third-party plugin admission."
            ),
            "execution_mode": "distributed",
            "required_dependencies": {
                "postgres": True,
                "schema_enforcement": True,
                "redis": True,
                "event_bus": True,
                "queue_backend": True,
                "worker_process": True,
                "strong_sandbox_runner": True,
                "pinned_sandbox_runtime_identity": True,
                "verified_plugin_sandbox_attestation": True,
            },
            "background_leadership_mode": "lease-elected",
            "supports_runtime_only_boot": True,
        },
    }


def get_deployment_profile_contract(profile_name: str) -> dict[str, Any]:
    contracts = _deployment_profile_contracts()
    if profile_name not in contracts:
        raise ValueError(
            f"Unsupported deployment profile {profile_name!r}. "
            f"Supported values: {', '.join(SUPPORTED_DEPLOYMENT_PROFILES)}."
        )
    return dict(contracts[profile_name])


def list_supported_deployment_profiles(*, process_role: str | None = None) -> list[dict[str, Any]]:
    profiles = list(_deployment_profile_contracts().values())
    if process_role is not None:
        profiles = [profile for profile in profiles if profile["process_role"] == process_role]
    return [dict(profile) for profile in profiles]


def get_requested_deployment_profile() -> str | None:
    value = os.getenv(DEPLOYMENT_PROFILE_ENV_VAR, "").strip()
    if not value:
        return None
    if value not in SUPPORTED_DEPLOYMENT_PROFILES:
        raise ValueError(
            f"Unsupported {DEPLOYMENT_PROFILE_ENV_VAR} value {value!r}. "
            f"Supported values: {', '.join(SUPPORTED_DEPLOYMENT_PROFILES)}."
        )
    return value


def infer_api_deployment_profile() -> str:
    if settings.EXECUTION_MODE == "distributed":
        return DEPLOYMENT_PROFILE_DISTRIBUTED_API
    return DEPLOYMENT_PROFILE_SINGLE_INSTANCE


def infer_worker_deployment_profile() -> str:
    return DEPLOYMENT_PROFILE_DISTRIBUTED_WORKER


def resolve_api_deployment_profile() -> tuple[str, str]:
    requested = get_requested_deployment_profile()
    if requested is not None:
        if requested not in {
            DEPLOYMENT_PROFILE_SINGLE_INSTANCE,
            DEPLOYMENT_PROFILE_DISTRIBUTED_API,
            DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY,
        }:
            raise ValueError(
                f"{DEPLOYMENT_PROFILE_ENV_VAR}={requested!r} is not valid for API startup. "
                f"Supported API values: {DEPLOYMENT_PROFILE_SINGLE_INSTANCE!r}, "
                f"{DEPLOYMENT_PROFILE_DISTRIBUTED_API!r}, "
                f"{DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY!r}."
            )
        return requested, DEPLOYMENT_PROFILE_ENV_VAR
    return infer_api_deployment_profile(), "derived:EXECUTION_MODE"


def resolve_worker_deployment_profile() -> tuple[str, str]:
    requested = get_requested_deployment_profile()
    if requested is not None:
        if requested != DEPLOYMENT_PROFILE_DISTRIBUTED_WORKER:
            raise ValueError(
                f"{DEPLOYMENT_PROFILE_ENV_VAR}={requested!r} is not valid for worker startup. "
                f"Workers support only {DEPLOYMENT_PROFILE_DISTRIBUTED_WORKER!r}."
            )
        return requested, DEPLOYMENT_PROFILE_ENV_VAR
    return infer_worker_deployment_profile(), "worker-default"


def background_leadership_mode_for_profile(profile_name: str) -> str:
    return str(get_deployment_profile_contract(profile_name)["background_leadership_mode"])


def production_safe_plugin_sandbox_required(profile_name: str) -> bool:
    return profile_name in {
        DEPLOYMENT_PROFILE_DISTRIBUTED_API,
        DEPLOYMENT_PROFILE_DISTRIBUTED_WORKER,
        DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY,
    }


def hostile_third_party_profile_required(profile_name: str) -> bool:
    return profile_name == DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY


def hostile_third_party_attestation_requirements() -> dict[str, Any]:
    return {
        "profile": DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY,
        "required_runner_type": RUNNER_STRONG_SANDBOX_VM,
        "required_assurance_class": "strong-sandbox-tier",
        "required_active_policies": {
            "pinned_runtime_identity": True,
            "runtime_trust_chain": "trusted-signed-pinned-compatible",
            "resource_limit_enforcement": "sandbox-runtime-hard-limits",
        },
        "required_verified_fields": [
            "launch_attestation.backend_identity",
            "launch_attestation.runtime_identity",
            "mount_isolation.artifact_mount",
            "launch_attestation.resource_limit_mode",
            "post_launch_verification.session_continuity",
            "post_launch_verification.isolation_state",
            "post_launch_verification.mount_network_state.artifact_write_blocked",
            "post_launch_verification.mount_network_state.host_path_access_blocked",
            "post_launch_verification.mount_network_state.network_policy.deny_by_default_outbound",
        ],
        "operator_note": (
            "Hostile third-party mode requires strong_sandbox_vm plus live host "
            "attestation that the runtime actually observed a launched sandbox "
            "backend, a signed and trusted pinned runtime identity, a verified read-only artifact "
            "mount, verified hard resource-limit launch flags, and a successful "
            "post-launch continuity, guard-state, and live mount/network policy probe."
        ),
    }


def plugin_sandbox_profile_requirements(profile_name: str | None) -> dict[str, Any]:
    normalized = str(profile_name or "").strip() or "unknown"
    if normalized == DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY:
        return {
            "deployment_profile": normalized,
            "required_assurance_class": "strong-sandbox-tier",
            "required_runner_type": RUNNER_STRONG_SANDBOX_VM,
            "required_certification_tier": "strong-sandbox-certified",
            "notes": (
                "Hostile third-party mode requires the strong sandbox runner plus "
                "verified host attestation and strong certification."
            ),
        }
    if normalized in {
        DEPLOYMENT_PROFILE_DISTRIBUTED_API,
        DEPLOYMENT_PROFILE_DISTRIBUTED_WORKER,
    }:
        return {
            "deployment_profile": normalized,
            "required_assurance_class": "container-grade-sandbox",
            "required_runner_type": RUNNER_CONTAINERIZED_OCI,
            "required_certification_tier": None,
            "notes": (
                "Distributed profiles require at least container-grade third-party "
                "sandboxing for production-safe execution, but they do not require "
                "a certified live sandbox tier at startup."
            ),
        }
    return {
        "deployment_profile": normalized,
        "required_assurance_class": None,
        "required_runner_type": None,
        "required_certification_tier": None,
        "notes": (
            "This profile does not require a third-party sandbox assurance class."
        ),
    }


def _assurance_unsupported_claims(assurance_class: str | None) -> list[str]:
    current = str(assurance_class or "").strip()
    if current == "insecure-dev":
        return [
            "general third-party sandboxing",
            "hard resource-limit enforcement",
            "kernel-level isolation guarantees",
        ]
    if current == "container-grade-sandbox":
        return [
            "vm-grade isolation guarantees",
            "ongoing kernel-state verification beyond launch evidence",
            "uniform Linux security-control parity across all host platforms",
        ]
    if current == "strong-sandbox-tier":
        return [
            "ongoing kernel-state verification beyond launch evidence",
            "cross-platform strong-sandbox parity outside supported Linux hosts",
            "blanket proof of sandbox integrity beyond runtime-observed launcher evidence",
        ]
    return [
        "declared sandbox assurance for third-party plugin execution",
    ]


def plugin_sandbox_assurance_posture(profile_name: str | None = None) -> dict[str, Any]:
    active_profile = str(profile_name or "").strip()
    if not active_profile or active_profile == "unknown":
        try:
            active_profile, _ = resolve_api_deployment_profile()
        except Exception:
            active_profile = "unknown"
    policy = selected_plugin_sandbox_policy()
    runner_metadata = create_sandbox_runner(str(policy["resolved_runner"])).metadata()
    certification = sandbox_certification_profile(
        runner_type=str(policy["resolved_runner"]),
        runner_metadata=runner_metadata,
        platform_matrix=dict(policy.get("platform_matrix") or {}),
    )
    requirements = plugin_sandbox_profile_requirements(active_profile)
    required_assurance_class = requirements.get("required_assurance_class")
    required_certification_tier = requirements.get("required_certification_tier")
    current_assurance_class = str(policy.get("assurance_class") or "unknown")
    current_certification_tier = certification.get("certification_tier")
    platform_matrix = dict(policy.get("platform_matrix") or {})
    current_environment = dict(platform_matrix.get("current_environment") or {})
    support_contract = dict(platform_matrix.get("support_contract") or {})
    return {
        "deployment_profile": active_profile,
        "current": {
            "runner_type": str(policy["resolved_runner"]),
            "assurance_class": current_assurance_class,
            "runtime_trust_status": str(policy.get("runtime_trust_status") or "unknown"),
            "certification_tier": current_certification_tier,
            "certification_status": certification.get("tier_status"),
        },
        "covered_execution_model_class": EXECUTION_MODEL_ISOLATED_EXTERNALIZED,
        "covered_surface_ids": [
            "dynamic-plugin-node:first-party-app",
            "dynamic-plugin-node:external-third-party",
        ],
        "required": {
            "assurance_class": required_assurance_class,
            "runner_type": requirements.get("required_runner_type"),
            "certification_tier": required_certification_tier,
        },
        "requirement_status": {
            "assurance_class_satisfied": (
                required_assurance_class is None
                or current_assurance_class == required_assurance_class
            ),
            "certification_tier_satisfied": (
                required_certification_tier is None
                or current_certification_tier == required_certification_tier
            ),
        },
        "platform_support": {
            "current_platform": platform_matrix.get("current_platform"),
            "current_equivalence_status": current_environment.get("equivalence_status"),
            "strong_sandbox_supported_host_platforms": list(
                support_contract.get("strong_sandbox_supported_host_platforms") or []
            ),
            "hostile_third_party_supported_host_platforms": list(
                support_contract.get("hostile_third_party_supported_host_platforms")
                or []
            ),
        },
        "unsupported_claims": _assurance_unsupported_claims(current_assurance_class),
        "distinction_note": (
            "Assurance class describes the runner category, attestation describes "
            "what the runtime observed, and certification describes what the runtime "
            "can justify from verified evidence."
        ),
        "notes": requirements.get("notes"),
    }


def hostile_third_party_attestation_violations(
    sandbox_attestation: dict[str, Any] | None,
) -> list[str]:
    attestation = dict(sandbox_attestation or {})
    launch_attestation = dict(attestation.get("launch_attestation") or {})
    runtime_identity = dict(attestation.get("runtime_identity") or {})
    mount_isolation = dict(attestation.get("mount_isolation") or {})
    artifact_mount = dict(mount_isolation.get("artifact_mount") or {})
    effective_resource_limits = dict(attestation.get("effective_resource_limits") or {})
    resource_limit_mode = dict(launch_attestation.get("resource_limit_mode") or {})
    post_launch_verification = dict(attestation.get("post_launch_verification") or {})
    post_launch_isolation_state = dict(post_launch_verification.get("isolation_state") or {})

    violations: list[str] = []
    if str(attestation.get("runner_type") or "") != RUNNER_STRONG_SANDBOX_VM:
        violations.append("runner_type")
    if str(attestation.get("assurance_class") or "") != "strong-sandbox-tier":
        violations.append("assurance_class")
    if not bool(runtime_identity.get("pinned")):
        violations.append("runtime_identity.pinned")
    if not bool((runtime_identity.get("trust_chain") or {}).get("accepted_for_hostile_profiles")):
        violations.append("runtime_identity.trust_chain")
    if str(launch_attestation.get("status") or "") != "launch-observed":
        violations.append("launch_attestation.status")
    if not bool((launch_attestation.get("backend_identity") or {}).get("verified")):
        violations.append("launch_attestation.backend_identity")
    if not bool((launch_attestation.get("runtime_identity") or {}).get("verified")):
        violations.append("launch_attestation.runtime_identity")
    if not bool(artifact_mount.get("verified")):
        violations.append("mount_isolation.artifact_mount")
    if (
        str(effective_resource_limits.get("enforcement") or "")
        != "sandbox-runtime-hard-limits"
    ):
        violations.append("effective_resource_limits.enforcement")
    if not bool(resource_limit_mode.get("verified")):
        violations.append("launch_attestation.resource_limit_mode")
    if str(post_launch_verification.get("status") or "") != "passed":
        violations.append("post_launch_verification.status")
    required_live_fields = {
        "session_continuity.worker_instance_id": bool(
            str(post_launch_verification.get("worker_instance_id") or "").strip()
        ),
        "session_continuity.sandbox_instance_id": bool(
            str((post_launch_verification.get("session_continuity") or {}).get("sandbox_instance_id") or "").strip()
        ),
        "isolation_state.import_guard_active": bool(post_launch_isolation_state.get("import_guard_active")),
        "isolation_state.filesystem_guard_active": bool(post_launch_isolation_state.get("filesystem_guard_active")),
        "isolation_state.network_guard_active": bool(post_launch_isolation_state.get("network_guard_active")),
        "boundary_metadata.runtime_api_channel_hidden": bool(
            (post_launch_verification.get("boundary_metadata") or {}).get("runtime_api_channel_hidden")
        ),
    }
    for field_name, satisfied in required_live_fields.items():
        if not satisfied:
            violations.append(f"post_launch_verification.{field_name}")
    return violations


def selected_plugin_sandbox_policy() -> dict[str, Any]:
    configured = str(settings.AINDY_PLUGIN_SANDBOX_RUNNER or RUNNER_SELECTION_AUTO).strip() or RUNNER_SELECTION_AUTO
    resolved = resolve_sandbox_runner_type(configured)
    platform_matrix = sandbox_platform_capability_matrix()
    runner_metadata = create_sandbox_runner(resolved).metadata()
    return {
        "configured_runner": configured,
        "resolved_runner": resolved,
        "container_image_configured": bool(str(settings.AINDY_PLUGIN_CONTAINER_IMAGE or "").strip()),
        "strong_sandbox_image_configured": bool(str(settings.AINDY_PLUGIN_STRONG_SANDBOX_IMAGE or "").strip()),
        "assurance_class": str(runner_metadata.get("assurance_class") or "unknown"),
        "runtime_identity": dict(runner_metadata.get("runtime_identity") or {}),
        "runtime_trust_status": str(
            (
                (runner_metadata.get("runtime_identity") or {}).get("trust_chain") or {}
            ).get("verification_status")
            or "unknown"
        ),
        "launch_attestation": dict(runner_metadata.get("launch_attestation") or {}),
        "resource_limits": dict(runner_metadata.get("resource_limits") or {}),
        "platform_matrix": platform_matrix,
        "hostile_third_party_attestation_requirements": hostile_third_party_attestation_requirements(),
    }


def _pinned_runtime_identity_required_for_runner(resolved_runner: str) -> bool:
    return resolved_runner in {
        RUNNER_CONTAINERIZED_OCI,
        RUNNER_STRONG_SANDBOX_VM,
    }


def validate_plugin_sandbox_profile_policy(*, profile_name: str, process_role: str) -> dict[str, Any]:
    policy = selected_plugin_sandbox_policy()
    if not production_safe_plugin_sandbox_required(profile_name):
        return policy

    configured = str(policy["configured_runner"])
    resolved = str(policy["resolved_runner"])
    if configured == RUNNER_SELECTION_AUTO:
        raise RuntimeError(
            f"{process_role} deployment profile {profile_name!r} requires an explicit "
            "third-party plugin sandbox runner. Set AINDY_PLUGIN_SANDBOX_RUNNER=containerized_oci; "
            "AINDY_PLUGIN_SANDBOX_RUNNER=auto is not allowed for production-safe profiles."
        )
    if resolved == RUNNER_INSECURE_DEV_SUBPROCESS:
        raise RuntimeError(
            f"{process_role} deployment profile {profile_name!r} does not permit "
            "AINDY_PLUGIN_SANDBOX_RUNNER=insecure_dev_subprocess. "
            "Use AINDY_PLUGIN_SANDBOX_RUNNER=containerized_oci or strong_sandbox_vm for production-safe third-party plugin execution."
        )
    if hostile_third_party_profile_required(profile_name) and resolved != RUNNER_STRONG_SANDBOX_VM:
        raise RuntimeError(
            f"{process_role} deployment profile {profile_name!r} requires "
            "AINDY_PLUGIN_SANDBOX_RUNNER=strong_sandbox_vm. Container-only or "
            "development runners do not meet the hostile third-party sandbox policy."
        )
    if resolved == RUNNER_CONTAINERIZED_OCI and not policy["container_image_configured"]:
        raise RuntimeError(
            f"{process_role} deployment profile {profile_name!r} requires "
            "AINDY_PLUGIN_CONTAINER_IMAGE when third-party plugin sandboxing is enabled."
        )
    if resolved == RUNNER_STRONG_SANDBOX_VM and not policy["strong_sandbox_image_configured"]:
        raise RuntimeError(
            f"{process_role} deployment profile {profile_name!r} requires "
            "AINDY_PLUGIN_STRONG_SANDBOX_IMAGE when strong_sandbox_vm is selected."
        )
    runtime_identity = dict(policy.get("runtime_identity") or {})
    runtime_trust_chain = dict(runtime_identity.get("trust_chain") or {})
    if _pinned_runtime_identity_required_for_runner(resolved) and not bool(
        runtime_identity.get("pinned")
    ):
        launch_reference = runtime_identity.get("launch_reference") or runtime_identity.get("configured_reference") or "<unset>"
        raise RuntimeError(
            f"{process_role} deployment profile {profile_name!r} requires a pinned sandbox runtime identity for runner "
            f"{resolved}. Mutable or unverified runtime reference {launch_reference!r} is not allowed."
        )
    if not bool(runtime_trust_chain.get("accepted_for_production_safe_profiles")):
        raise RuntimeError(
            f"{process_role} deployment profile {profile_name!r} requires a trusted sandbox runtime identity chain for "
            f"runner {resolved}; current trust status is {runtime_trust_chain.get('verification_status')!r}."
        )
    if resolved == RUNNER_CONTAINERIZED_OCI and not bool(
        ((policy.get("platform_matrix") or {}).get("current_environment") or {}).get(
            "production_safe_third_party_plugin_execution",
            False,
        )
    ):
        current_platform = str(
            ((policy.get("platform_matrix") or {}).get("current_platform") or "unknown")
        )
        raise RuntimeError(
            f"{process_role} deployment profile {profile_name!r} requires a Linux host with "
            f"compatible container sandbox support for third-party plugins. Current platform={current_platform!r}."
        )
    if resolved == RUNNER_STRONG_SANDBOX_VM and not bool(
        (((
            ((policy.get("platform_matrix") or {}).get("current_environment") or {}).get(
                "support_levels",
                {},
            )
        ).get("strong_sandbox") or {}).get("support") == "supported")
    ):
        current_platform = str(
            ((policy.get("platform_matrix") or {}).get("current_platform") or "unknown")
        )
        raise RuntimeError(
            f"{process_role} deployment profile {profile_name!r} requires a Linux host with "
            f"compatible strong sandbox VM support for third-party plugins. Current platform={current_platform!r}."
        )
    if hostile_third_party_profile_required(profile_name):
        if not bool(
            ((policy.get("platform_matrix") or {}).get("current_environment") or {}).get(
                "high_assurance_hostile_workload_support",
                False,
            )
        ):
            current_platform = str(
                ((policy.get("platform_matrix") or {}).get("current_platform") or "unknown")
            )
            raise RuntimeError(
                f"{process_role} deployment profile {profile_name!r} requires host platforms with "
                "documented strong-sandbox support for hostile third-party workloads. "
                f"Current platform={current_platform!r} does not provide that support."
            )
        if str(policy.get("assurance_class") or "") != "strong-sandbox-tier":
            raise RuntimeError(
                f"{process_role} deployment profile {profile_name!r} requires assurance_class="
                "'strong-sandbox-tier' for third-party sandbox execution."
            )
        if not bool(runtime_trust_chain.get("accepted_for_hostile_profiles")):
            raise RuntimeError(
                f"{process_role} deployment profile {profile_name!r} requires a signed, trusted, compatible sandbox runtime identity "
                f"for runner {resolved}; current trust status is {runtime_trust_chain.get('verification_status')!r}."
            )
        if str((policy.get("resource_limits") or {}).get("enforcement") or "") != "sandbox-runtime-hard-limits":
            raise RuntimeError(
                f"{process_role} deployment profile {profile_name!r} requires "
                "strong_sandbox_vm resource_limit enforcement sandbox-runtime-hard-limits."
            )
    return policy


def validate_external_third_party_plugin_runtime_policy(*, identifier: str) -> dict[str, Any]:
    profile_name = str(get_api_runtime_state().get("deployment_profile") or "").strip()
    if not profile_name or profile_name == "unknown":
        try:
            profile_name, _ = resolve_api_deployment_profile()
        except Exception:
            profile_name = DEPLOYMENT_PROFILE_SINGLE_INSTANCE

    policy = selected_plugin_sandbox_policy()
    configured = str(policy["configured_runner"])
    resolved = str(policy["resolved_runner"])
    if production_safe_plugin_sandbox_required(profile_name) and configured == RUNNER_SELECTION_AUTO:
        raise RuntimeError(
            f"external-third-party plugin {identifier!r} is not allowed under deployment profile "
            f"{profile_name!r} while AINDY_PLUGIN_SANDBOX_RUNNER=auto. "
            "Set AINDY_PLUGIN_SANDBOX_RUNNER explicitly."
        )
    if production_safe_plugin_sandbox_required(profile_name) and resolved == RUNNER_INSECURE_DEV_SUBPROCESS:
        raise RuntimeError(
            f"external-third-party plugin {identifier!r} is not allowed under deployment profile "
            f"{profile_name!r} with runner insecure_dev_subprocess. "
            "Use AINDY_PLUGIN_SANDBOX_RUNNER=containerized_oci or strong_sandbox_vm."
        )
    if hostile_third_party_profile_required(profile_name) and resolved != RUNNER_STRONG_SANDBOX_VM:
        raise RuntimeError(
            f"external-third-party plugin {identifier!r} is not allowed under deployment profile "
            f"{profile_name!r} unless AINDY_PLUGIN_SANDBOX_RUNNER=strong_sandbox_vm. "
            "Hostile third-party mode rejects container-only and development runners."
        )
    if resolved == RUNNER_CONTAINERIZED_OCI and not policy["container_image_configured"]:
        raise RuntimeError(
            f"external-third-party plugin {identifier!r} requires AINDY_PLUGIN_CONTAINER_IMAGE "
            "for containerized sandbox execution."
        )
    if resolved == RUNNER_STRONG_SANDBOX_VM and not policy["strong_sandbox_image_configured"]:
        raise RuntimeError(
            f"external-third-party plugin {identifier!r} requires AINDY_PLUGIN_STRONG_SANDBOX_IMAGE "
            "for strong sandbox VM execution."
        )
    runtime_identity = dict(policy.get("runtime_identity") or {})
    runtime_trust_chain = dict(runtime_identity.get("trust_chain") or {})
    if _pinned_runtime_identity_required_for_runner(resolved) and production_safe_plugin_sandbox_required(profile_name) and not bool(
        runtime_identity.get("pinned")
    ):
        launch_reference = runtime_identity.get("launch_reference") or runtime_identity.get("configured_reference") or "<unset>"
        raise RuntimeError(
            f"external-third-party plugin {identifier!r} is not allowed under deployment profile "
            f"{profile_name!r} because runner {resolved} does not have a pinned sandbox runtime identity; "
            f"configured runtime reference {launch_reference!r} is mutable or unverifiable."
        )
    if production_safe_plugin_sandbox_required(profile_name) and not bool(
        runtime_trust_chain.get("accepted_for_production_safe_profiles")
    ):
        raise RuntimeError(
            f"external-third-party plugin {identifier!r} is not allowed under deployment profile "
            f"{profile_name!r} because runner {resolved} does not satisfy the trusted sandbox runtime identity chain; "
            f"current trust status is {runtime_trust_chain.get('verification_status')!r}."
        )
    if resolved == RUNNER_CONTAINERIZED_OCI and not bool(
        ((policy.get("platform_matrix") or {}).get("current_environment") or {}).get(
            "production_safe_third_party_plugin_execution",
            False,
        )
    ) and production_safe_plugin_sandbox_required(profile_name):
        current_platform = str(
            ((policy.get("platform_matrix") or {}).get("current_platform") or "unknown")
        )
        raise RuntimeError(
            f"external-third-party plugin {identifier!r} is not allowed under deployment profile "
            f"{profile_name!r} because platform {current_platform!r} does not provide the "
            "documented production-safe third-party sandbox guarantees."
        )
    if resolved == RUNNER_STRONG_SANDBOX_VM and production_safe_plugin_sandbox_required(profile_name) and not bool(
        (((
            ((policy.get("platform_matrix") or {}).get("current_environment") or {}).get(
                "support_levels",
                {},
            )
        ).get("strong_sandbox") or {}).get("support") == "supported")
    ):
        current_platform = str(
            ((policy.get("platform_matrix") or {}).get("current_platform") or "unknown")
        )
        raise RuntimeError(
            f"external-third-party plugin {identifier!r} is not allowed under deployment profile "
            f"{profile_name!r} because platform {current_platform!r} does not provide the "
            "documented strong sandbox VM guarantees."
        )
    if hostile_third_party_profile_required(profile_name) and not bool(
        ((policy.get("platform_matrix") or {}).get("current_environment") or {}).get(
            "high_assurance_hostile_workload_support",
            False,
        )
    ):
        current_platform = str(
            ((policy.get("platform_matrix") or {}).get("current_platform") or "unknown")
        )
        raise RuntimeError(
            f"external-third-party plugin {identifier!r} is not allowed under deployment profile "
            f"{profile_name!r} because platform {current_platform!r} does not provide the "
            "documented high-assurance strong sandbox support required for hostile workloads."
        )
    if hostile_third_party_profile_required(profile_name):
        if str(policy.get("assurance_class") or "") != "strong-sandbox-tier":
            raise RuntimeError(
                f"external-third-party plugin {identifier!r} is not allowed under deployment profile "
                f"{profile_name!r} because the selected runner does not report assurance_class "
                "'strong-sandbox-tier'."
            )
        if not bool(runtime_trust_chain.get("accepted_for_hostile_profiles")):
            raise RuntimeError(
                f"external-third-party plugin {identifier!r} is not allowed under deployment profile "
                f"{profile_name!r} because the selected runner does not have a signed, trusted, compatible sandbox runtime identity chain; "
                f"current trust status is {runtime_trust_chain.get('verification_status')!r}."
            )
        if str((policy.get("resource_limits") or {}).get("enforcement") or "") != "sandbox-runtime-hard-limits":
            raise RuntimeError(
                f"external-third-party plugin {identifier!r} is not allowed under deployment profile "
                f"{profile_name!r} because strong_sandbox_vm hard resource-limit enforcement is not active."
            )
    return policy


def validate_api_deployment_profile() -> dict[str, Any]:
    profile_name, source = resolve_api_deployment_profile()
    errors: list[str] = []
    if profile_name == DEPLOYMENT_PROFILE_SINGLE_INSTANCE:
        if settings.EXECUTION_MODE != "thread":
            errors.append(
                "single-instance profile requires EXECUTION_MODE=thread"
            )
    elif profile_name in {
        DEPLOYMENT_PROFILE_DISTRIBUTED_API,
        DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY,
    }:
        if settings.EXECUTION_MODE != "distributed":
            errors.append(
                f"{profile_name} profile requires EXECUTION_MODE=distributed"
            )
        if not settings.REDIS_URL:
            errors.append(
                f"{profile_name} profile requires REDIS_URL for queue and event-bus coordination"
            )
    if profile_name in {
        DEPLOYMENT_PROFILE_DISTRIBUTED_API,
        DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY,
    }:
        if os.getenv("AINDY_EVENT_BUS_ENABLED", "true").lower() in {"0", "false", "no", "off"}:
            errors.append(
                f"{profile_name} profile requires AINDY_EVENT_BUS_ENABLED=true"
            )
        if str(settings.AINDY_CACHE_BACKEND).lower() == "memory":
            errors.append(
                f"{profile_name} profile does not permit AINDY_CACHE_BACKEND=memory"
            )
    if errors:
        raise RuntimeError(
            f"Invalid deployment profile {profile_name!r}: " + "; ".join(errors)
        )
    sandbox_policy = validate_plugin_sandbox_profile_policy(
        profile_name=profile_name,
        process_role=PROCESS_ROLE_API,
    )
    contract = get_deployment_profile_contract(profile_name)
    contract["source"] = source
    contract["plugin_sandbox_policy"] = sandbox_policy
    return contract


def validate_worker_deployment_profile() -> dict[str, Any]:
    profile_name, source = resolve_worker_deployment_profile()
    errors: list[str] = []
    if settings.EXECUTION_MODE != "distributed":
        errors.append(
            "distributed-worker profile requires EXECUTION_MODE=distributed"
        )
    if not settings.REDIS_URL:
        errors.append(
            "distributed-worker profile requires REDIS_URL for the durable queue backend"
        )
    if errors:
        raise RuntimeError(
            f"Invalid deployment profile {profile_name!r}: " + "; ".join(errors)
        )
    sandbox_policy = validate_plugin_sandbox_profile_policy(
        profile_name=profile_name,
        process_role=PROCESS_ROLE_WORKER,
    )
    contract = get_deployment_profile_contract(profile_name)
    contract["source"] = source
    contract["plugin_sandbox_policy"] = sandbox_policy
    return contract


def redis_required() -> bool:
    try:
        profile_name, _ = resolve_api_deployment_profile()
    except Exception:
        return settings.requires_redis
    return profile_name == DEPLOYMENT_PROFILE_DISTRIBUTED_API


def worker_required() -> bool:
    return (
        not settings.is_testing
        and redis_required()
    )


def event_bus_required() -> bool:
    return redis_required()


def queue_backend_required() -> bool:
    return redis_required()


def schema_enforcement_required() -> bool:
    return not settings.is_testing


def publish_api_runtime_state(**updates: Any) -> dict[str, Any]:
    _api_runtime_state.update(updates)
    return dict(_api_runtime_state)


def get_api_runtime_state() -> dict[str, Any]:
    return dict(_api_runtime_state)


def set_api_runtime_condition(
    *,
    code: str,
    component: str,
    classification: str,
    detail: str,
    production_behavior: str,
) -> dict[str, Any]:
    conditions = dict(_api_runtime_state.get("runtime_conditions") or {})
    conditions[code] = {
        "code": code,
        "component": component,
        "classification": classification,
        "detail": detail,
        "production_behavior": production_behavior,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    _api_runtime_state["runtime_conditions"] = conditions
    return dict(conditions[code])


def clear_api_runtime_condition(code: str) -> None:
    conditions = dict(_api_runtime_state.get("runtime_conditions") or {})
    if code in conditions:
        conditions.pop(code, None)
        _api_runtime_state["runtime_conditions"] = conditions


def get_api_runtime_conditions() -> list[dict[str, Any]]:
    conditions = _api_runtime_state.get("runtime_conditions") or {}
    return [conditions[key] for key in sorted(conditions)]


def publish_worker_runtime_state(**updates: Any) -> dict[str, Any]:
    _worker_runtime_state.update(updates)
    return dict(_worker_runtime_state)


def get_worker_runtime_state() -> dict[str, Any]:
    return dict(_worker_runtime_state)


def reset_runtime_state() -> None:
    from AINDY.platform_layer.plugin_host import reset_plugin_hosts

    reset_plugin_hosts()
    _api_runtime_state.clear()
    _api_runtime_state.update(
        {
            "process_role": PROCESS_ROLE_API,
            "startup_complete": False,
            "background_enabled": False,
            "scheduler_role": "disabled",
            "background_leadership_mode": "unknown",
            "event_bus_ready": False,
            "boot_mode": "unknown",
            "boot_profile": "unknown",
            "boot_profile_source": "unknown",
            "deployment_profile": "unknown",
            "deployment_profile_source": "unknown",
            "app_plugins_loaded": False,
            "app_plugin_count": 0,
            "external_python_override_active": False,
            "external_python_override_execution_model": "external-python-blocked",
            "runtime_conditions": {},
        }
    )
    _worker_runtime_state.clear()
    _worker_runtime_state.update(
        {
            "process_role": PROCESS_ROLE_WORKER,
            "startup_complete": False,
            "queue_ready": False,
            "schema_ready": False,
            "scheduler_role": "disabled",
            "background_leadership_mode": "unknown",
            "deployment_profile": "unknown",
            "deployment_profile_source": "unknown",
        }
    )


def runtime_only_deployment_contract() -> dict[str, Any]:
    return {
        "stability": "stable",
        "boot_mode": RUNTIME_ONLY_BOOT_MODE,
        "boot_profile": RUNTIME_ONLY_BOOT_PROFILE,
        "activation": {
            "preferred": f"{BOOT_MODE_ENV_VAR}={RUNTIME_ONLY_BOOT_MODE}",
            "entrypoint": "uvicorn AINDY.runtime_only:app",
            "packaged_entrypoints": {
                "console_script": "aindy-runtime",
                "module": "python -m AINDY.runtime_only",
            },
            "legacy_profile_override": f"AINDY_BOOT_PROFILE={RUNTIME_ONLY_BOOT_PROFILE}",
        },
        "mounted_routes": {
            "required_routes": list(RUNTIME_ONLY_REQUIRED_ROUTES),
            "required_prefixes": list(RUNTIME_ONLY_REQUIRED_ROUTE_PREFIXES),
        },
        "baseline_agent_capabilities": {
            "planner": "generic runtime prompt",
            "tools": list(RUNTIME_ONLY_BASELINE_AGENT_TOOLS),
            "capabilities": list(RUNTIME_ONLY_BASELINE_AGENT_CAPABILITIES),
            "suggestions": "empty unless a plugin registers a provider",
            "completion_hook": "runtime no-op",
        },
        "agent_enrichment_boundary": agent_runtime_enrichment_contract(),
        "health_and_readiness": {
            "liveness_route": "/health",
            "readiness_route": "/ready",
        },
        "intentionally_unavailable": list(RUNTIME_ONLY_INTENTIONALLY_UNAVAILABLE),
    }


def deployment_contract_summary() -> dict[str, Any]:
    active_profile_name, active_profile_source = resolve_api_deployment_profile()
    active_profile = get_deployment_profile_contract(active_profile_name)
    override_state = external_python_override_state()
    from AINDY.platform_layer.extension_runtime_inventory import (
        trusted_python_execution_summary,
    )

    return {
        "release_posture": {
            "support_tier": "trusted-internal",
            "readiness_scope": (
                "Readiness reflects required dependencies and unsafe runtime conditions "
                "for the active deployment profile. It does not certify extension "
                "isolation or third-party code trust."
            ),
        },
        "environment": settings.ENV,
        "execution_mode": settings.EXECUTION_MODE,
        "process_role": PROCESS_ROLE_API,
        "active_profile": {
            "name": active_profile_name,
            "source": active_profile_source,
            "background_leadership_mode": active_profile["background_leadership_mode"],
        },
        "supported_profiles": list_supported_deployment_profiles(
            process_role=PROCESS_ROLE_API
        ),
        "runtime_only_support": runtime_only_deployment_contract(),
        "extension_execution": {
            "external_python_override": override_state,
            "trusted_python_execution": trusted_python_execution_summary(),
            "extension_execution_posture": extension_execution_model_contract(),
        },
        "requires": {
            "redis": redis_required(),
            "worker": worker_required(),
            "event_bus": event_bus_required(),
            "queue_backend": queue_backend_required(),
            "schema_enforcement": schema_enforcement_required(),
        },
        "plugin_sandbox_policy": selected_plugin_sandbox_policy(),
        "plugin_sandbox_posture": plugin_sandbox_assurance_posture(
            active_profile_name
        ),
        "optional_in_dev": {
            "redis": settings.is_dev or settings.is_testing,
            "worker": settings.is_dev or settings.is_testing,
            "scheduler_leadership": True,
            "peripheral_domains": True,
        },
    }


def agent_runtime_enrichment_contract() -> dict[str, Any]:
    return {
        "baseline_runtime_contract": list(RUNTIME_BASELINE_AGENT_ENRICHMENTS),
        "optional_plugin_enrichment": list(OPTIONAL_PLUGIN_AGENT_ENRICHMENTS),
        "ambiguous_or_refactor": list(AMBIGUOUS_AGENT_ENRICHMENTS),
    }


def resolve_boot_mode_for_profile(profile_name: str | None) -> str:
    if profile_name == RUNTIME_ONLY_BOOT_PROFILE:
        return RUNTIME_ONLY_BOOT_MODE
    return APP_PROFILE_BOOT_MODE


def get_requested_boot_mode() -> str | None:
    value = os.getenv(BOOT_MODE_ENV_VAR, "").strip()
    if not value:
        return None
    if value in (RUNTIME_ONLY_BOOT_MODE, APP_PROFILE_BOOT_MODE):
        return value
    raise ValueError(
        f"Unsupported {BOOT_MODE_ENV_VAR} value {value!r}. "
        f"Supported values: {RUNTIME_ONLY_BOOT_MODE!r}, {APP_PROFILE_BOOT_MODE!r}."
    )


def resolve_profile_for_boot_mode(boot_mode: str | None) -> str | None:
    if boot_mode is None:
        return None
    if boot_mode == RUNTIME_ONLY_BOOT_MODE:
        return RUNTIME_ONLY_BOOT_PROFILE
    if boot_mode == APP_PROFILE_BOOT_MODE:
        return None
    raise ValueError(f"Unsupported boot mode {boot_mode!r}")
