from __future__ import annotations

from typing import Any

from AINDY.platform_layer.sandbox_runner import (
    RUNNER_CONTAINERIZED_OCI,
    RUNNER_INSECURE_DEV_SUBPROCESS,
    RUNNER_STRONG_SANDBOX_VM,
    SANDBOX_RUNNER_INTERFACE_VERSION,
    STRONG_SANDBOX_REQUIRED_ASSURANCE_PROPERTIES,
    create_sandbox_runner,
    sandbox_platform_capability_matrix,
)
from AINDY.platform_layer.extension_execution_model import (
    EXECUTION_MODEL_ISOLATED_EXTERNALIZED,
)

SANDBOX_CERTIFICATION_SCHEMA_VERSION = "2026-05-21"
CERTIFICATION_TIER_CONTAINED_PROCESS = "contained-process-certified"
CERTIFICATION_TIER_CONTAINER_SANDBOX = "container-sandbox-certified"
CERTIFICATION_TIER_STRONG_SANDBOX = "strong-sandbox-certified"


def _strong_sandbox_live_evidence_fields() -> list[str]:
    return [
        "session_continuity.worker_instance_id",
        "session_continuity.sandbox_instance_id",
        "isolation_state.import_guard_active",
        "isolation_state.filesystem_guard_active",
        "isolation_state.network_guard_active",
        "boundary_metadata.runtime_api_channel_hidden",
        "mount_network_state.artifact_read_access",
        "mount_network_state.artifact_write_blocked",
        "mount_network_state.writable_temp_scope",
        "mount_network_state.host_path_access_blocked",
        "mount_network_state.network_policy.socket_guard_active",
        "mount_network_state.network_policy.deny_by_default_outbound",
        "mount_network_state.network_policy.private_target_blocking",
        "mount_network_state.network_policy.expected_boundary_mode",
    ]


def _uncertified_reason_category(requirement: str) -> str:
    if requirement.startswith("platform_support."):
        return "platform_support"
    if requirement in {"assurance_class", "isolation_claim", "execution_boundary"}:
        return "runner_identity"
    if requirement.startswith("runtime_identity.trust_chain"):
        return "runtime_trust"
    if requirement.startswith("assurance_properties.") or requirement.startswith("launch_attestation.assurance_properties."):
        return "assurance_properties"
    if requirement.startswith("hardening_controls.") or requirement.startswith("verified.read_only_plugin_mount"):
        return "hardening_state"
    if requirement.startswith("resource_limits."):
        return "resource_limits"
    if requirement.startswith("launch_attestation.") or requirement.startswith("verified."):
        return "launch_evidence"
    if requirement.startswith("post_launch_verification.") or requirement.startswith("post_launch_verified."):
        return "live_evidence"
    if requirement.startswith("shared_worker_policy_status"):
        return "shared_worker_policy"
    return "other"


def _uncertified_reasons(missing_requirements: list[str]) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {}
    for requirement in missing_requirements:
        grouped.setdefault(_uncertified_reason_category(requirement), []).append(requirement)
    ordered_categories = [
        "platform_support",
        "runner_identity",
        "runtime_trust",
        "launch_evidence",
        "live_evidence",
        "hardening_state",
        "resource_limits",
        "assurance_properties",
        "shared_worker_policy",
        "other",
    ]
    return [
        {
            "category": category,
            "missing_requirements": sorted(grouped[category]),
        }
        for category in ordered_categories
        if grouped.get(category)
    ]


def _check_result(*, check_id: str, status: str, detail: str) -> dict[str, str]:
    return {
        "check_id": check_id,
        "status": status,
        "detail": detail,
    }


def sandbox_certification_tiers() -> list[dict[str, Any]]:
    return [
        {
            "tier": CERTIFICATION_TIER_CONTAINED_PROCESS,
            "runner_types": [RUNNER_INSECURE_DEV_SUBPROCESS],
            "requirements": {
                "shared_worker_policy_status": "certifiable-shared-worker-policy",
            },
            "notes": (
                "Certifies the isolated plugin-host worker policy checks for a contained "
                "subprocess boundary. It does not certify hard kernel or container sandbox guarantees."
            ),
        },
        {
            "tier": CERTIFICATION_TIER_CONTAINER_SANDBOX,
            "runner_types": [RUNNER_CONTAINERIZED_OCI],
            "requirements": {
                "shared_worker_policy_status": "certifiable-shared-worker-policy",
                "assurance_class": "container-grade-sandbox",
                "launch_attestation_status": "launch-observed",
                "verified_fields": [
                    "backend_identity",
                    "runtime_identity",
                    "mount_mode",
                    "resource_limit_mode",
                ],
                "required_runtime_trust_status": "trusted-pinned-compatible",
                "resource_limit_enforcement": "container-runtime-hard-limits",
            },
            "notes": (
                "Requires the container runner plus launch-observed backend identity, "
                "verified pinned runtime identity, an accepted runtime trust chain, verified read-only artifact mount, "
                "and verified container runtime hard resource-limit flags."
            ),
        },
        {
            "tier": CERTIFICATION_TIER_STRONG_SANDBOX,
            "runner_types": [RUNNER_STRONG_SANDBOX_VM],
            "requirements": {
                "shared_worker_policy_status": "certifiable-shared-worker-policy",
                "assurance_class": "strong-sandbox-tier",
                "launch_attestation_status": "launch-observed",
                "verified_fields": [
                    "backend_identity",
                    "runtime_identity",
                    "mount_mode",
                    "resource_limit_mode",
                ],
                "post_launch_verification_status": "passed",
                "post_launch_verification_scope": "live-worker-self-report-over-authenticated-rpc",
                "post_launch_required_fields": [
                    "checked_at",
                    "worker_instance_id",
                ],
                "post_launch_verified_fields": _strong_sandbox_live_evidence_fields(),
                "required_assurance_properties": dict(STRONG_SANDBOX_REQUIRED_ASSURANCE_PROPERTIES),
                "required_runtime_trust_status": "trusted-signed-pinned-compatible",
                "resource_limit_enforcement": "sandbox-runtime-hard-limits",
            },
            "notes": (
                "Requires the higher-assurance strong_sandbox_vm runner and live verified "
                "attestation for launched backend identity, signed and trusted pinned runtime identity, "
                "read-only artifact mount, hard resource-limit launch mode, and a "
                "successful post-launch continuity and guard-state probe."
            ),
        },
    ]


def _verified_attestation_fields(launch_attestation: dict[str, Any]) -> set[str]:
    verified: set[str] = set()
    if bool((launch_attestation.get("backend_identity") or {}).get("verified")):
        verified.add("backend_identity")
    if bool((launch_attestation.get("runtime_identity") or {}).get("verified")):
        verified.add("runtime_identity")
    if bool((launch_attestation.get("mount_mode") or {}).get("verified")):
        verified.add("mount_mode")
    if bool((launch_attestation.get("resource_limit_mode") or {}).get("verified")):
        verified.add("resource_limit_mode")
    return verified


def _runner_certification_tier(
    *,
    runner_type: str,
    metadata: dict[str, Any],
    shared_worker_status: str,
    platform_matrix: dict[str, Any],
    post_launch_verification: dict[str, Any],
) -> tuple[str | None, list[str]]:
    resource_limits = dict(metadata.get("resource_limits") or {})
    launch_attestation = dict(metadata.get("launch_attestation") or {})
    assurance_class = str(metadata.get("assurance_class") or "")
    runtime_identity = dict(metadata.get("runtime_identity") or {})
    runtime_trust_chain = dict(runtime_identity.get("trust_chain") or {})
    verified_fields = _verified_attestation_fields(launch_attestation)
    current_environment = dict(platform_matrix.get("current_environment") or {})
    support_levels = dict(current_environment.get("support_levels") or {})

    if shared_worker_status != "certifiable-shared-worker-policy":
        return None, ["shared_worker_policy_status"]

    if runner_type == RUNNER_INSECURE_DEV_SUBPROCESS:
        return CERTIFICATION_TIER_CONTAINED_PROCESS, []

    if runner_type == RUNNER_CONTAINERIZED_OCI:
        missing: list[str] = []
        if str((support_levels.get("container_sandbox") or {}).get("support") or "") != "supported":
            missing.append("platform_support.container_sandbox")
        if assurance_class != "container-grade-sandbox":
            missing.append("assurance_class")
        if str(launch_attestation.get("status") or "") != "launch-observed":
            missing.append("launch_attestation.status")
        if str(resource_limits.get("enforcement") or "") != "container-runtime-hard-limits":
            missing.append("resource_limits.enforcement")
        if not bool(runtime_trust_chain.get("accepted_for_production_safe_profiles")):
            missing.append("runtime_identity.trust_chain")
        for field_name in ("backend_identity", "runtime_identity", "mount_mode", "resource_limit_mode"):
            if field_name not in verified_fields:
                missing.append(f"verified.{field_name}")
        if not missing:
            return CERTIFICATION_TIER_CONTAINER_SANDBOX, []
        return None, missing

    if runner_type == RUNNER_STRONG_SANDBOX_VM:
        missing = []
        live_verified_fields = set(post_launch_verification.get("verified_fields") or [])
        active_controls = set((metadata.get("hardening_controls") or {}).get("active_controls") or [])
        verified_controls = set(
            ((launch_attestation.get("hardening_profiles") or {}).get("verified_controls") or [])
        )
        assurance_properties = dict(metadata.get("assurance_properties") or {})
        active_assurance_properties = dict((launch_attestation.get("assurance_properties") or {}).get("active") or {})
        verified_assurance_properties = dict((launch_attestation.get("assurance_properties") or {}).get("verified") or {})
        if str((support_levels.get("strong_sandbox") or {}).get("support") or "") != "supported":
            missing.append("platform_support.strong_sandbox")
        if assurance_class != "strong-sandbox-tier":
            missing.append("assurance_class")
        if metadata.get("isolation_claim") != "vm-boundary":
            missing.append("isolation_claim")
        if metadata.get("execution_boundary") != "vm-stdio-json-rpc":
            missing.append("execution_boundary")
        if str(launch_attestation.get("status") or "") != "launch-observed":
            missing.append("launch_attestation.status")
        if str(resource_limits.get("enforcement") or "") != "sandbox-runtime-hard-limits":
            missing.append("resource_limits.enforcement")
        if not bool(runtime_trust_chain.get("accepted_for_hostile_profiles")):
            missing.append("runtime_identity.trust_chain")
        for key, expected in STRONG_SANDBOX_REQUIRED_ASSURANCE_PROPERTIES.items():
            if str(assurance_properties.get(key) or "") != expected:
                missing.append(f"assurance_properties.{key}")
            if str(active_assurance_properties.get(key) or "") != expected:
                missing.append(f"launch_attestation.assurance_properties.active.{key}")
            if not bool(verified_assurance_properties.get(key)):
                missing.append(f"launch_attestation.assurance_properties.verified.{key}")
        if str(post_launch_verification.get("status") or "") != "passed":
            missing.append("post_launch_verification.status")
        if str(post_launch_verification.get("verification_scope") or "") != "live-worker-self-report-over-authenticated-rpc":
            missing.append("post_launch_verification.verification_scope")
        if not str(post_launch_verification.get("checked_at") or "").strip():
            missing.append("post_launch_verification.checked_at")
        if not str(post_launch_verification.get("worker_instance_id") or "").strip():
            missing.append("post_launch_verification.worker_instance_id")
        for field_name in _strong_sandbox_live_evidence_fields():
            if field_name not in live_verified_fields:
                missing.append(f"post_launch_verified.{field_name}")
        if not {
            "dedicated_vm_boundary",
            "read_only_plugin_mount",
            "minimal_environment",
            "sandbox_runtime_limits",
        }.issubset(active_controls):
            missing.append("hardening_controls.active_controls")
        if "read_only_plugin_mount" not in verified_controls:
            missing.append("verified.read_only_plugin_mount")
        for field_name in ("backend_identity", "runtime_identity", "mount_mode", "resource_limit_mode"):
            if field_name not in verified_fields:
                missing.append(f"verified.{field_name}")
        if not missing:
            return CERTIFICATION_TIER_STRONG_SANDBOX, []
        return None, missing

    return None, ["unsupported_runner"]


def sandbox_certification_contract() -> dict[str, Any]:
    return {
        "schema_version": SANDBOX_CERTIFICATION_SCHEMA_VERSION,
        "interface_version": SANDBOX_RUNNER_INTERFACE_VERSION,
        "covered_execution_model_class": EXECUTION_MODEL_ISOLATED_EXTERNALIZED,
        "covered_surface_ids": [
            "dynamic-plugin-node:first-party-app",
            "dynamic-plugin-node:external-third-party",
        ],
        "certification_tiers": sandbox_certification_tiers(),
        "shared_worker_policy_checks": [
            {
                "id": "blocked_internal_imports",
                "verification_mode": "end_to_end_behavior",
                "expected_outcome": "fail_closed",
                "notes": (
                    "Third-party plugin admission must block direct imports of internal "
                    "runtime modules outside the supported extension API."
                ),
            },
            {
                "id": "blocked_filesystem_writes",
                "verification_mode": "end_to_end_behavior",
                "expected_outcome": "fail_closed",
                "notes": (
                    "Third-party plugin execution must deny filesystem writes from the "
                    "isolated worker path."
                ),
            },
            {
                "id": "blocked_out_of_policy_network_access",
                "verification_mode": "end_to_end_behavior",
                "expected_outcome": "fail_closed",
                "notes": (
                    "Third-party plugin execution must deny outbound access without the "
                    "required capability and private/loopback targets remain blocked by policy."
                ),
            },
            {
                "id": "denied_capabilities",
                "verification_mode": "end_to_end_behavior",
                "expected_outcome": "fail_closed",
                "notes": (
                    "Runtime capability checks must deny ungranted runtime operations for "
                    "third-party plugins."
                ),
            },
            {
                "id": "quarantine_behavior",
                "verification_mode": "end_to_end_behavior",
                "expected_outcome": "quarantine_after_repeated_failure",
                "notes": (
                    "Repeated plugin-host failures must move the extension into explicit "
                    "backoff/quarantine instead of retrying forever."
                ),
            },
            {
                "id": "provenance_rejection",
                "verification_mode": "end_to_end_behavior",
                "expected_outcome": "fail_closed",
                "notes": (
                    "Third-party plugin admission must reject missing or unverifiable "
                    "provenance/integrity declarations."
                ),
            },
        ],
        "runner_specific_checks": [
            {
                "id": "runner_identity_reporting",
                "verification_mode": "attestation_and_runtime_state",
                "expected_outcome": "reported",
                "notes": (
                    "Operator surfaces must expose the actual runner type and isolation class."
                ),
            },
            {
                "id": "hard_resource_limits",
                "verification_mode": "attestation_and_fail_closed",
                "expected_outcome": (
                    "enforced_when_supported_and_configured"
                ),
                "notes": (
                    "Hard resource limits are only certifiable when the active runner "
                    "reports container-runtime enforcement. Timeout-only containment "
                    "must not be treated as equivalent."
                ),
            },
        ],
        "assurance_validation_checks": {
            CERTIFICATION_TIER_CONTAINER_SANDBOX: [
                {
                    "id": "runner_class_verification",
                    "verification_mode": "attestation_and_runtime_state",
                    "expected_outcome": "container-grade runner identity matches reported assurance class",
                },
                {
                    "id": "verified_runtime_identity",
                    "verification_mode": "launch_verified_attestation",
                    "expected_outcome": "pinned runtime identity is active and launch-verified",
                },
                {
                    "id": "verified_runtime_trust_chain",
                    "verification_mode": "runtime_identity_policy_and_attestation",
                    "expected_outcome": "runtime trust chain satisfies the production-safe trust policy",
                },
                {
                    "id": "verified_resource_limit_mode",
                    "verification_mode": "launch_verified_attestation",
                    "expected_outcome": "container runtime hard limits are active and launch-verified",
                },
                {
                    "id": "verified_isolation_reporting",
                    "verification_mode": "runner_metadata_and_attestation",
                    "expected_outcome": "container execution boundary and isolation claim are reported explicitly",
                },
            ],
            CERTIFICATION_TIER_STRONG_SANDBOX: [
                {
                    "id": "runner_class_verification",
                    "verification_mode": "attestation_and_platform_matrix",
                    "expected_outcome": "strong sandbox runner identity matches reported assurance class on a supported platform",
                },
                {
                    "id": "verified_runtime_identity",
                    "verification_mode": "launch_verified_attestation",
                    "expected_outcome": "pinned strong sandbox runtime identity is active and launch-verified",
                },
                {
                    "id": "verified_runtime_trust_chain",
                    "verification_mode": "runtime_identity_policy_and_attestation",
                    "expected_outcome": "strong sandbox runtime trust chain satisfies the hostile-workload trust policy",
                },
                {
                    "id": "verified_hardening_profile_state",
                    "verification_mode": "active_controls_and_launch_verified_attestation",
                    "expected_outcome": "strong sandbox hardening controls are active and at least the read-only plugin mount is launch-verified",
                },
                {
                    "id": "verified_stronger_isolation_reporting",
                    "verification_mode": "runner_metadata_and_attestation",
                    "expected_outcome": "vm-grade execution boundary and isolation claim are reported explicitly",
                },
                {
                    "id": "verified_resource_limit_mode",
                    "verification_mode": "launch_verified_attestation",
                    "expected_outcome": "strong sandbox hard limits are active and launch-verified",
                },
                {
                    "id": "verified_strong_boundary_properties",
                    "verification_mode": "runner_contract_and_launch_verified_attestation",
                    "expected_outcome": "the runner proves dedicated-vm, launcher-mediated mount/network, pinned sandbox runtime, and post-launch session verification properties that container-grade evidence cannot satisfy",
                },
                {
                    "id": "live_session_continuity",
                    "verification_mode": "post_launch_runtime_probe",
                    "expected_outcome": "the runtime can confirm the active worker instance and sandbox instance continuity after launch",
                },
                {
                    "id": "live_isolation_state_probe",
                    "verification_mode": "post_launch_runtime_probe",
                    "expected_outcome": "the runtime can confirm expected live guard state and hidden runtime channel posture after launch",
                },
                {
                    "id": "live_mount_network_policy_probe",
                    "verification_mode": "post_launch_runtime_probe",
                    "expected_outcome": "the runtime can partially verify live read-only artifact behavior, writable temp scope, host-path denial, and deny-by-default socket policy where applicable",
                },
                {
                    "id": "fail_closed_unavailability",
                    "verification_mode": "admission_fail_closed",
                    "expected_outcome": "unavailable strong sandbox support remains visibly uncertified and must fail closed at admission",
                },
            ],
        },
        "operator_note": (
            "This contract describes what the certification suite verifies. Shared worker-policy "
            "checks are exercised end to end against isolated plugin execution. Runner-specific "
            "checks certify only the guarantees that the active runner can actually expose "
            "or enforce."
        ),
    }


def _assurance_validation_profile(
    *,
    runner_type: str,
    metadata: dict[str, Any],
    platform_matrix: dict[str, Any],
    certification_tier: str | None,
    post_launch_verification: dict[str, Any],
) -> dict[str, Any]:
    launch_attestation = dict(metadata.get("launch_attestation") or {})
    runtime_identity = dict(metadata.get("runtime_identity") or {})
    runtime_trust_chain = dict(runtime_identity.get("trust_chain") or {})
    resource_limits = dict(metadata.get("resource_limits") or {})
    hardening_controls = dict(metadata.get("hardening_controls") or {})
    active_controls = set(hardening_controls.get("active_controls") or [])
    verified_controls = set(
        ((launch_attestation.get("hardening_profiles") or {}).get("verified_controls") or [])
    )
    live_verified_fields = set(post_launch_verification.get("verified_fields") or [])
    assurance_properties = dict(metadata.get("assurance_properties") or {})
    active_assurance_properties = dict((launch_attestation.get("assurance_properties") or {}).get("active") or {})
    verified_assurance_properties = dict((launch_attestation.get("assurance_properties") or {}).get("verified") or {})
    support_levels = dict(
        ((platform_matrix.get("current_environment") or {}).get("support_levels") or {})
    )

    if runner_type == RUNNER_INSECURE_DEV_SUBPROCESS:
        return {
            "layer": CERTIFICATION_TIER_CONTAINED_PROCESS,
            "status": "not_applicable_for_stronger_assurance",
            "checks": [],
        }

    if runner_type == RUNNER_CONTAINERIZED_OCI:
        checks = [
            _check_result(
                check_id="runner_class_verification",
                status=(
                    "passed"
                    if metadata.get("assurance_class") == "container-grade-sandbox"
                    else "failed"
                ),
                detail=(
                    "runner reports assurance_class=container-grade-sandbox"
                    if metadata.get("assurance_class") == "container-grade-sandbox"
                    else f"runner reported assurance_class={metadata.get('assurance_class')!r}"
                ),
            ),
            _check_result(
                check_id="verified_runtime_identity",
                status=(
                    "passed"
                    if runtime_identity.get("pinned")
                    and bool((launch_attestation.get("runtime_identity") or {}).get("verified"))
                    else "failed"
                ),
                detail=(
                    "runtime identity is pinned and launch-verified"
                    if runtime_identity.get("pinned")
                    and bool((launch_attestation.get("runtime_identity") or {}).get("verified"))
                    else "runtime identity is not both pinned and launch-verified"
                ),
            ),
            _check_result(
                check_id="verified_runtime_trust_chain",
                status=(
                    "passed"
                    if bool(runtime_trust_chain.get("accepted_for_production_safe_profiles"))
                    else "failed"
                ),
                detail=(
                    "runtime trust chain satisfies the production-safe policy"
                    if bool(runtime_trust_chain.get("accepted_for_production_safe_profiles"))
                    else f"runtime trust chain did not satisfy the production-safe policy: {runtime_trust_chain.get('verification_status')!r}"
                ),
            ),
            _check_result(
                check_id="verified_resource_limit_mode",
                status=(
                    "passed"
                    if resource_limits.get("enforcement") == "container-runtime-hard-limits"
                    and bool((launch_attestation.get("resource_limit_mode") or {}).get("verified"))
                    else "failed"
                ),
                detail=(
                    "container hard resource-limit mode is active and verified"
                    if resource_limits.get("enforcement") == "container-runtime-hard-limits"
                    and bool((launch_attestation.get("resource_limit_mode") or {}).get("verified"))
                    else "container hard resource-limit mode is not fully verified"
                ),
            ),
            _check_result(
                check_id="verified_isolation_reporting",
                status=(
                    "passed"
                    if metadata.get("isolation_claim") == "container-boundary"
                    and metadata.get("execution_boundary") == "container-stdio-json-rpc"
                    else "failed"
                ),
                detail=(
                    "container isolation reporting matches the runner contract"
                    if metadata.get("isolation_claim") == "container-boundary"
                    and metadata.get("execution_boundary") == "container-stdio-json-rpc"
                    else "container isolation reporting does not match the runner contract"
                ),
            ),
        ]
        return {
            "layer": CERTIFICATION_TIER_CONTAINER_SANDBOX,
            "status": "passed" if certification_tier == CERTIFICATION_TIER_CONTAINER_SANDBOX else "not_certified",
            "checks": checks,
        }

    if runner_type == RUNNER_STRONG_SANDBOX_VM:
        strong_supported = (
            str((support_levels.get("strong_sandbox") or {}).get("support") or "")
            == "supported"
        )
        read_only_mount_verified = "read_only_plugin_mount" in verified_controls
        required_active_controls = {
            "dedicated_vm_boundary",
            "read_only_plugin_mount",
            "minimal_environment",
            "sandbox_runtime_limits",
        }
        checks = [
            _check_result(
                check_id="runner_class_verification",
                status=(
                    "passed"
                    if strong_supported
                    and metadata.get("assurance_class") == "strong-sandbox-tier"
                    else "failed"
                ),
                detail=(
                    "strong sandbox runner class is supported on this platform and assurance_class matches"
                    if strong_supported
                    and metadata.get("assurance_class") == "strong-sandbox-tier"
                    else "strong sandbox runner class is unsupported on this platform or assurance_class mismatched"
                ),
            ),
            _check_result(
                check_id="verified_runtime_identity",
                status=(
                    "passed"
                    if runtime_identity.get("pinned")
                    and bool((launch_attestation.get("runtime_identity") or {}).get("verified"))
                    else "failed"
                ),
                detail=(
                    "strong sandbox runtime identity is pinned and launch-verified"
                    if runtime_identity.get("pinned")
                    and bool((launch_attestation.get("runtime_identity") or {}).get("verified"))
                    else "strong sandbox runtime identity is not both pinned and launch-verified"
                ),
            ),
            _check_result(
                check_id="verified_runtime_trust_chain",
                status=(
                    "passed"
                    if bool(runtime_trust_chain.get("accepted_for_hostile_profiles"))
                    else "failed"
                ),
                detail=(
                    "strong sandbox runtime trust chain satisfies the hostile-workload policy"
                    if bool(runtime_trust_chain.get("accepted_for_hostile_profiles"))
                    else f"strong sandbox runtime trust chain did not satisfy the hostile-workload policy: {runtime_trust_chain.get('verification_status')!r}"
                ),
            ),
            _check_result(
                check_id="verified_hardening_profile_state",
                status=(
                    "passed"
                    if required_active_controls.issubset(active_controls) and read_only_mount_verified
                    else "failed"
                ),
                detail=(
                    "strong sandbox hardening controls are active and read-only plugin mount is launch-verified"
                    if required_active_controls.issubset(active_controls) and read_only_mount_verified
                    else "strong sandbox hardening controls or read-only mount verification are incomplete"
                ),
            ),
            _check_result(
                check_id="verified_stronger_isolation_reporting",
                status=(
                    "passed"
                    if metadata.get("isolation_claim") == "vm-boundary"
                    and metadata.get("execution_boundary") == "vm-stdio-json-rpc"
                    else "failed"
                ),
                detail=(
                    "strong sandbox isolation reporting matches the VM-grade runner contract"
                    if metadata.get("isolation_claim") == "vm-boundary"
                    and metadata.get("execution_boundary") == "vm-stdio-json-rpc"
                    else "strong sandbox isolation reporting does not match the VM-grade runner contract"
                ),
            ),
            _check_result(
                check_id="verified_resource_limit_mode",
                status=(
                    "passed"
                    if resource_limits.get("enforcement") == "sandbox-runtime-hard-limits"
                    and bool((launch_attestation.get("resource_limit_mode") or {}).get("verified"))
                    else "failed"
                ),
                detail=(
                    "strong sandbox hard resource-limit mode is active and verified"
                    if resource_limits.get("enforcement") == "sandbox-runtime-hard-limits"
                    and bool((launch_attestation.get("resource_limit_mode") or {}).get("verified"))
                    else "strong sandbox hard resource-limit mode is not fully verified"
                ),
            ),
            _check_result(
                check_id="verified_strong_boundary_properties",
                status=(
                    "passed"
                    if all(
                        str(assurance_properties.get(key) or "") == expected
                        and str(active_assurance_properties.get(key) or "") == expected
                        and bool(verified_assurance_properties.get(key))
                        for key, expected in STRONG_SANDBOX_REQUIRED_ASSURANCE_PROPERTIES.items()
                    )
                    else "failed"
                ),
                detail=(
                    "strong sandbox runner reports and verifies its required VM-only assurance properties"
                    if all(
                        str(assurance_properties.get(key) or "") == expected
                        and str(active_assurance_properties.get(key) or "") == expected
                        and bool(verified_assurance_properties.get(key))
                        for key, expected in STRONG_SANDBOX_REQUIRED_ASSURANCE_PROPERTIES.items()
                    )
                    else "strong sandbox runner did not verify the VM-only assurance properties required to distinguish it from container-grade isolation"
                ),
            ),
            _check_result(
                check_id="live_session_continuity",
                status=(
                    "passed"
                    if str(post_launch_verification.get("status") or "") == "passed"
                    and {
                        "session_continuity.worker_instance_id",
                        "session_continuity.sandbox_instance_id",
                    }.issubset(live_verified_fields)
                    else "failed"
                ),
                detail=(
                    "post-launch probe verified worker instance continuity and sandbox instance binding"
                    if str(post_launch_verification.get("status") or "") == "passed"
                    and {
                        "session_continuity.worker_instance_id",
                        "session_continuity.sandbox_instance_id",
                    }.issubset(live_verified_fields)
                    else "post-launch probe did not verify worker instance continuity and sandbox binding"
                ),
            ),
            _check_result(
                check_id="live_isolation_state_probe",
                status=(
                    "passed"
                    if str(post_launch_verification.get("status") or "") == "passed"
                    and {
                        "isolation_state.import_guard_active",
                        "isolation_state.filesystem_guard_active",
                        "isolation_state.network_guard_active",
                        "boundary_metadata.runtime_api_channel_hidden",
                    }.issubset(live_verified_fields)
                    else "failed"
                ),
                detail=(
                    "post-launch probe verified expected live guard state and hidden runtime channel posture"
                    if str(post_launch_verification.get("status") or "") == "passed"
                    and {
                        "isolation_state.import_guard_active",
                        "isolation_state.filesystem_guard_active",
                        "isolation_state.network_guard_active",
                        "boundary_metadata.runtime_api_channel_hidden",
                    }.issubset(live_verified_fields)
                    else "post-launch probe did not verify expected live guard state and hidden runtime channel posture"
                ),
            ),
            _check_result(
                check_id="live_mount_network_policy_probe",
                status=(
                    "passed"
                    if str(post_launch_verification.get("status") or "") == "passed"
                    and {
                        "mount_network_state.artifact_read_access",
                        "mount_network_state.artifact_write_blocked",
                        "mount_network_state.writable_temp_scope",
                        "mount_network_state.host_path_access_blocked",
                        "mount_network_state.network_policy.socket_guard_active",
                        "mount_network_state.network_policy.deny_by_default_outbound",
                        "mount_network_state.network_policy.private_target_blocking",
                        "mount_network_state.network_policy.expected_boundary_mode",
                    }.issubset(live_verified_fields)
                    else "failed"
                ),
                detail=(
                    "post-launch probe partially verified live mount and network policy behavior"
                    if str(post_launch_verification.get("status") or "") == "passed"
                    and {
                        "mount_network_state.artifact_read_access",
                        "mount_network_state.artifact_write_blocked",
                        "mount_network_state.writable_temp_scope",
                        "mount_network_state.host_path_access_blocked",
                        "mount_network_state.network_policy.socket_guard_active",
                        "mount_network_state.network_policy.deny_by_default_outbound",
                        "mount_network_state.network_policy.private_target_blocking",
                        "mount_network_state.network_policy.expected_boundary_mode",
                    }.issubset(live_verified_fields)
                    else "post-launch probe did not verify the required live mount and network policy behavior"
                ),
            ),
            _check_result(
                check_id="fail_closed_unavailability",
                status=(
                    "observable"
                    if resource_limits.get("enforcement") == "unavailable"
                    or bool(hardening_controls.get("unsupported_controls"))
                    else "not_applicable"
                ),
                detail=(
                    "unavailable strong sandbox support is surfaced explicitly and must fail closed at admission"
                    if resource_limits.get("enforcement") == "unavailable"
                    or bool(hardening_controls.get("unsupported_controls"))
                    else "strong sandbox runner is currently available; fail-closed unavailability path not exercised in this profile"
                ),
            ),
        ]
        return {
            "layer": CERTIFICATION_TIER_STRONG_SANDBOX,
            "status": "passed" if certification_tier == CERTIFICATION_TIER_STRONG_SANDBOX else "not_certified",
            "checks": checks,
        }

    return {
        "layer": "unsupported",
        "status": "unsupported",
        "checks": [],
    }


def sandbox_certification_profile(
    *,
    runner_type: str,
    runner_metadata: dict[str, Any] | None = None,
    platform_matrix: dict[str, Any] | None = None,
    post_launch_verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(runner_metadata or create_sandbox_runner(runner_type).metadata())
    effective_platform_matrix = dict(platform_matrix or sandbox_platform_capability_matrix())
    effective_post_launch_verification = dict(post_launch_verification or {})
    resource_limits = dict(metadata.get("resource_limits") or {})
    launch_attestation = dict(metadata.get("launch_attestation") or {})
    hard_limits_active = (
        runner_type == RUNNER_CONTAINERIZED_OCI
        and resource_limits.get("enforcement") == "container-runtime-hard-limits"
    )
    strong_limits_active = (
        runner_type == RUNNER_STRONG_SANDBOX_VM
        and resource_limits.get("enforcement") == "sandbox-runtime-hard-limits"
    )
    shared_worker_status = (
        "certifiable-shared-worker-policy"
        if runner_type in {RUNNER_INSECURE_DEV_SUBPROCESS, RUNNER_CONTAINERIZED_OCI, RUNNER_STRONG_SANDBOX_VM}
        else "unsupported"
    )
    runner_checks = []
    for check in sandbox_certification_contract()["runner_specific_checks"]:
        check_record = dict(check)
        if check["id"] == "hard_resource_limits":
            if hard_limits_active or strong_limits_active:
                check_record["runner_status"] = "certifiable"
                check_record["effective_enforcement"] = resource_limits.get("enforcement")
            else:
                check_record["runner_status"] = "not_certifiable_for_runner"
                check_record["effective_enforcement"] = resource_limits.get("enforcement")
                check_record["reason"] = (
                    "The active runner does not report a certifiable hard resource-limit enforcement mode."
                )
        else:
            check_record["runner_status"] = "certifiable"
        runner_checks.append(check_record)

    certification_tier, missing_requirements = _runner_certification_tier(
        runner_type=runner_type,
        metadata=metadata,
        shared_worker_status=shared_worker_status,
        platform_matrix=effective_platform_matrix,
        post_launch_verification=effective_post_launch_verification,
    )

    return {
        "schema_version": SANDBOX_CERTIFICATION_SCHEMA_VERSION,
        "runner_type": runner_type,
        "covered_execution_model_class": EXECUTION_MODEL_ISOLATED_EXTERNALIZED,
        "covered_surface_ids": [
            "dynamic-plugin-node:first-party-app",
            "dynamic-plugin-node:external-third-party",
        ],
        "assurance_class": metadata.get("assurance_class"),
        "platform_support": dict(
            ((effective_platform_matrix.get("current_environment") or {}).get("support_levels") or {})
        ),
        "shared_worker_policy_status": shared_worker_status,
        "resource_limit_enforcement": resource_limits.get("enforcement"),
        "launch_attestation_status": launch_attestation.get("status"),
        "certification_tier": certification_tier,
        "tier_status": "certified" if certification_tier else "not_certified_for_runner",
        "missing_tier_requirements": missing_requirements,
        "uncertified_reasons": _uncertified_reasons(missing_requirements),
        "validation_layers": {
            "shared_worker_policy": {
                "status": shared_worker_status,
                "checks": [
                    {
                        "check_id": check["id"],
                        "status": shared_worker_status,
                        "detail": check["expected_outcome"],
                    }
                    for check in sandbox_certification_contract()["shared_worker_policy_checks"]
                ],
            },
            "runner_assurance": _assurance_validation_profile(
                runner_type=runner_type,
                metadata=metadata,
                platform_matrix=effective_platform_matrix,
                certification_tier=certification_tier,
                post_launch_verification=effective_post_launch_verification,
            ),
        },
        "checks": [
            *[
                {
                    **dict(check),
                    "runner_status": shared_worker_status,
                }
                for check in sandbox_certification_contract()["shared_worker_policy_checks"]
            ],
            *runner_checks,
        ],
        "operator_note": (
            "This profile is not itself a certification result. It tells operators and CI "
            "which guarantees the current runner mode is expected to satisfy, which tier "
            "the runtime can currently justify from live evidence, and which claims must "
            "remain unsupported."
        ),
    }
