from __future__ import annotations

import pytest

from AINDY.platform_layer.deployment_contract import runtime_only_deployment_contract
from AINDY.platform_layer.public_contract import runtime_public_contract_metadata


pytestmark = pytest.mark.runtime_only


def test_runtime_public_contract_marks_only_selected_http_surfaces_stable():
    metadata = runtime_public_contract_metadata()

    stable_routes = {entry["route"] for entry in metadata["http"]["stable"]}
    experimental_prefixes = {entry["route_prefix"] for entry in metadata["http"]["experimental"]}

    assert "GET /api/version" in stable_routes
    assert "GET /platform/syscalls" in stable_routes
    assert "POST /platform/syscall" in stable_routes
    assert "/apps/agent/" in experimental_prefixes
    assert "/platform/nodes" in experimental_prefixes


def test_runtime_public_contract_publishes_trusted_internal_release_posture():
    metadata = runtime_public_contract_metadata()

    posture = metadata["release_posture"]
    assert posture["support_tier"] == "trusted-internal"
    assert "third-party extension isolation" in posture["not_claimed"]
    assert "runtime-only internal deployments" in posture["suitable_for"]
    assert "do not certify" in posture["operator_scope"]


def test_runtime_public_contract_reports_syscall_stability_inventory():
    metadata = runtime_public_contract_metadata()

    assert metadata["syscalls"]["stable_versions"] == ["v1"]
    assert metadata["syscalls"]["experimental_versions"] == ["v2"]
    assert "sys.v1.memory.list" in metadata["syscalls"]["experimental_entries"]
    assert "sys.v2.memory.read" in metadata["syscalls"]["experimental_entries"]


def test_runtime_only_boot_contract_is_marked_stable():
    metadata = runtime_public_contract_metadata()
    boot_contract = runtime_only_deployment_contract()

    assert boot_contract["stability"] == "stable"
    assert metadata["runtime_only_boot"]["stability"] == "stable"
    assert metadata["runtime_only_boot"]["boot_mode"] == "runtime-only"
    assert "/platform/syscalls" in metadata["runtime_only_boot"]["required_routes"]
    assert "does not imply third-party extension isolation" in metadata["runtime_only_boot"]["notes"]


def test_runtime_public_contract_marks_extension_registration_surfaces_experimental():
    metadata = runtime_public_contract_metadata()
    stable_surfaces = {entry["surface"] for entry in metadata["extensions"]["stable"]}
    extension_surfaces = {entry["surface"] for entry in metadata["extensions"]["experimental"]}

    assert "extension manifest" in stable_surfaces
    assert "manifest bootstrap modules" in extension_surfaces
    assert "dynamic plugin nodes" in extension_surfaces
    assert "dynamic flows" in extension_surfaces


def test_runtime_public_contract_publishes_extension_abi_policy():
    metadata = runtime_public_contract_metadata()

    abi = metadata["extensions"]["abi"]
    assert abi["schema_version"] == "2026-05-20"
    assert abi["surfaces"]["manifest"]["stability"] == "stable"
    assert abi["surfaces"]["manifest"]["supported_versions"] == [
        "aindy.extension.manifest/v1"
    ]
    assert abi["surfaces"]["manifest"]["legacy_accepted"] is True
    assert abi["surfaces"]["dynamic-node-registration"]["stability"] == "experimental"


def test_runtime_public_contract_publishes_extension_capability_model():
    metadata = runtime_public_contract_metadata()

    capability_model = metadata["extensions"]["capability_model"]
    assert capability_model["policy_version"] == "2026-05-20"
    assert "memory.read" in capability_model["capabilities"]
    assert capability_model["surfaces"]["dynamic-plugin-node"]["authority_model"] == (
        "isolated-explicit-capabilities"
    )
    assert capability_model["surfaces"]["dynamic-plugin-node"]["default_runtime_capabilities"] == []
    assert capability_model["surfaces"]["dynamic-plugin-node"]["network_policy"]["capability_required"] == "outbound.http"
    assert capability_model["surfaces"]["dynamic-plugin-node"]["filesystem_policy"]["default"] == "read-only-approved-roots"
    assert capability_model["surfaces"]["dynamic-plugin-node"]["environment_policy"]["secret_injection"] == "none"
    assert "secret.read" in capability_model["not_exposed"]


def test_runtime_public_contract_publishes_extension_execution_model_matrix():
    metadata = runtime_public_contract_metadata()

    execution_models = metadata["extensions"]["execution_models"]
    assert execution_models["schema_version"] == "2026-05-22"
    execution_model_ids = {entry["id"] for entry in execution_models["execution_model_classes"]}
    assert execution_model_ids == {"kernel-resident", "isolated-externalized"}
    assert "capability-confined-in-process-exception" not in execution_model_ids
    surface_ids = {
        entry["surface_id"] for entry in execution_models["surface_matrix"]
    }
    assert {
        "manifest-bootstrap:runtime-built-in",
        "manifest-bootstrap:first-party-app",
        "manifest-bootstrap:external-third-party",
        "registry-kernel-callable:runtime-built-in",
        "registry-kernel-callable:first-party-app",
        "runtime-callback-worker:runtime-built-in",
        "runtime-callback-worker:first-party-app",
        "dynamic-plugin-node:runtime-built-in",
        "dynamic-plugin-node:first-party-app",
        "dynamic-plugin-node:external-third-party",
        "webhook-node:any-owner",
        "webhook-subscription:any-owner",
        "dynamic-flow:any-owner",
    }.issubset(surface_ids)
    first_party_kernel_callable = next(
        entry
        for entry in execution_models["surface_matrix"]
        if entry["surface_id"] == "registry-kernel-callable:first-party-app"
    )
    assert first_party_kernel_callable["execution_model_class"] == "kernel-resident"
    assert first_party_kernel_callable["registration_boundary"] == "registration-capability-gate"
    runtime_builtin_bootstrap = next(
        entry
        for entry in execution_models["surface_matrix"]
        if entry["surface_id"] == "manifest-bootstrap:runtime-built-in"
    )
    assert runtime_builtin_bootstrap["execution_model_class"] == "kernel-resident"
    first_party_bootstrap = next(
        entry
        for entry in execution_models["surface_matrix"]
        if entry["surface_id"] == "manifest-bootstrap:first-party-app"
    )
    assert first_party_bootstrap["execution_model_class"] == "kernel-resident"
    external_plugin = next(
        entry
        for entry in execution_models["surface_matrix"]
        if entry["surface_id"] == "dynamic-plugin-node:external-third-party"
    )
    assert external_plugin["execution_model_class"] == "isolated-externalized"
    assert external_plugin["platform_support"]["hostile_third_party_supported_host_platforms"] == [
        "linux"
    ]
    assert execution_models["attestation_scope"]["plugin_sandbox_attestation"] == {
        "covered_execution_model_class": "isolated-externalized",
        "covered_surface_ids": [
            "dynamic-plugin-node:first-party-app",
            "dynamic-plugin-node:external-third-party",
        ],
        "excluded_surface_ids": [
            "manifest-bootstrap:runtime-built-in",
            "manifest-bootstrap:first-party-app",
            "registry-kernel-callable:runtime-built-in",
            "registry-kernel-callable:first-party-app",
            "dynamic-plugin-node:runtime-built-in",
            "dynamic-flow:any-owner",
        ],
        "notes": (
            "Plugin sandbox attestation and certification describe Tier 2 isolated plugin-host "
            "execution only. Tier 1 trusted-operator surfaces — manifest bootstrap, "
            "kernel-resident callables, and runtime-built-in plugin nodes — are excluded; "
            "they are kernel code and do not require a process isolation boundary."
        ),
    }
    assert execution_models["attestation_scope"]["deployment_profile_enforcement"] == {
        "covered_surface_ids": [
            "dynamic-plugin-node:external-third-party",
        ],
        "notes": (
            "Deployment-profile sandbox gating currently applies only to external third-party dynamic plugin nodes."
        ),
    }


def test_runtime_public_contract_publishes_extension_provenance_policy():
    metadata = runtime_public_contract_metadata()

    provenance = metadata["extensions"]["provenance_policy"]
    assert provenance["policy_version"] == "2026-05-20"
    assert provenance["signing"]["status"] == "unsupported"
    assert provenance["trust_policies"]["runtime-built-in"] == "runtime-owned-derived"
    assert "dynamic-plugin-node" in provenance["required_when"]["external-third-party"]


def test_runtime_public_contract_publishes_sandbox_runner_inventory():
    metadata = runtime_public_contract_metadata()

    runners = metadata["extensions"]["sandbox_runners"]
    assert runners["interface_version"] == "2026-05-21"
    assert runners["configured_selection"] == "auto"
    assert runners["default_external_runner"] == "insecure_dev_subprocess"
    assert runners["selection_policy"]["auto_single_instance"] == "insecure_dev_subprocess"
    assert runners["selection_policy"]["auto_distributed"] == "containerized_oci"
    assert runners["selection_policy"]["strong_runner_requires_explicit_selection"] == "strong_sandbox_vm"
    runner_types = {entry["runner_type"] for entry in runners["available_runners"]}
    assert runner_types == {"insecure_dev_subprocess", "containerized_oci", "strong_sandbox_vm"}
    claims = {entry["runner_type"]: entry["isolation_claim"] for entry in runners["available_runners"]}
    assert claims["insecure_dev_subprocess"] == "none"
    assert claims["containerized_oci"] == "container-boundary"
    assert claims["strong_sandbox_vm"] == "vm-boundary"
    container_runner = next(
        entry for entry in runners["available_runners"]
        if entry["runner_type"] == "containerized_oci"
    )
    strong_runner = next(
        entry for entry in runners["available_runners"]
        if entry["runner_type"] == "strong_sandbox_vm"
    )
    assert container_runner["kernel_control_reporting"] == "explicit"
    assert strong_runner["assurance_class"] == "strong-sandbox-tier"
    assert runners["operator_reporting"]["version_surface"] == "runtime.plugin_sandbox_attestation"
    assert runners["operator_reporting"]["health_surface"] == "plugin_sandbox_attestation"
    assert runners["operator_reporting"]["assurance_posture_surface"] == "runtime.plugin_sandbox_posture"
    assert runners["operator_reporting"]["platform_matrix_surface"] == "runtime.plugin_sandbox_platform"
    assert "execution_model_class" in runners["operator_reporting"]["attestation_fields"]
    assert "assurance_class" in runners["operator_reporting"]["attestation_fields"]
    assert "isolation_class" in runners["operator_reporting"]["attestation_fields"]
    assert "certification" in runners["operator_reporting"]["attestation_fields"]
    assert "runtime_identity" in runners["operator_reporting"]["attestation_fields"]
    assert "runtime_identity.trust_chain" in runners["operator_reporting"]["attestation_fields"]
    assert "assurance_properties" in runners["operator_reporting"]["attestation_fields"]
    assert "launch_attestation" in runners["operator_reporting"]["attestation_fields"]
    assert "post_launch_verification" in runners["operator_reporting"]["attestation_fields"]
    assert "verified_hardening_controls" in runners["operator_reporting"]["attestation_fields"]
    assert "mount_isolation" in runners["operator_reporting"]["attestation_fields"]
    assert "network_isolation" in runners["operator_reporting"]["attestation_fields"]
    assert "mount_isolation.live_verification" in runners["operator_reporting"]["attestation_fields"]
    assert "network_isolation.live_verification" in runners["operator_reporting"]["attestation_fields"]
    assert runners["operator_reporting"]["attestation_model"]["requested"] == "operator-configured or runner-requested policy"
    assert runners["operator_reporting"]["attestation_model"]["active"] == "runner metadata for controls the runtime expects to be active"
    assert runners["operator_reporting"]["attestation_model"]["assurance_class"] == "the current runner category reported by the runtime"
    assert runners["operator_reporting"]["attestation_model"]["required_assurance_class"] == "the minimum class required by the active deployment profile"
    assert runners["operator_reporting"]["attestation_model"]["coverage"] == "plugin sandbox attestation and certification cover isolated plugin-host execution only"
    assert "source, issuer, signing-status, and base-compatibility" in runners["operator_reporting"]["attestation_model"]["runtime_trust_chain"]
    assert "distinguish process containment, container-grade sandboxing, and strong sandbox behavior" in runners["operator_reporting"]["attestation_model"]["assurance_properties"]
    assert runners["operator_reporting"]["attestation_model"]["post_launch_verified"] == "live worker continuity and guard-state checks over a runtime-owned authenticated probe"
    assert "partial live verification" in runners["operator_reporting"]["attestation_model"]["live_mount_and_network"]
    assert "mount/network isolation claims" in runners["operator_reporting"]["attestation_model"]["mount_and_network"]
    assert runners["selection_policy"]["pinned_runtime_identity_required_for_production_safe_profiles"] is True
    assert runners["selection_policy"]["trusted_runtime_identity_chain_required_for_production_safe_profiles"] is True
    assert runners["selection_policy"]["hostile_third_party_profile"]["profile"] == "hostile-third-party"
    assert runners["selection_policy"]["hostile_third_party_profile"]["required_runner_type"] == "strong_sandbox_vm"
    assert (
        runners["selection_policy"]["hostile_third_party_profile"]["required_assurance_class"]
        == "strong-sandbox-tier"
    )
    assert "launch_attestation.runtime_identity" in runners["selection_policy"]["hostile_third_party_profile"]["required_verified_fields"]
    assert "post_launch_verification.session_continuity" in runners["selection_policy"]["hostile_third_party_profile"]["required_verified_fields"]
    assert "post_launch_verification.isolation_state" in runners["selection_policy"]["hostile_third_party_profile"]["required_verified_fields"]
    assert "post_launch_verification.mount_network_state.artifact_write_blocked" in runners["selection_policy"]["hostile_third_party_profile"]["required_verified_fields"]
    assert "post_launch_verification.mount_network_state.network_policy.deny_by_default_outbound" in runners["selection_policy"]["hostile_third_party_profile"]["required_verified_fields"]
    assert runners["selection_policy"]["hostile_third_party_profile"]["required_active_policies"]["runtime_trust_chain"] == "trusted-signed-pinned-compatible"
    assert "strong-sandbox-tier" in runners["assurance_classes"]
    assert runners["certification_contract"]["schema_version"] == "2026-05-21"
    certification_tiers = {
        entry["tier"] for entry in runners["certification_contract"]["certification_tiers"]
    }
    assert certification_tiers == {
        "contained-process-certified",
        "container-sandbox-certified",
        "strong-sandbox-certified",
    }
    assert runners["active_certification_profile"]["runner_type"] == "insecure_dev_subprocess"
    assert runners["active_certification_profile"]["shared_worker_policy_status"] == "certifiable-shared-worker-policy"
    assert runners["active_certification_profile"]["certification_tier"] == "contained-process-certified"
    assert runners["active_certification_profile"]["tier_status"] == "certified"
    assert runners["active_certification_profile"]["uncertified_reasons"] == []
    assert "assurance_validation_checks" in runners["certification_contract"]
    assert "strong-sandbox-certified" in runners["certification_contract"]["assurance_validation_checks"]
    strong_tier_contract = next(
        entry
        for entry in runners["certification_contract"]["certification_tiers"]
        if entry["tier"] == "strong-sandbox-certified"
    )
    assert strong_tier_contract["requirements"]["post_launch_verification_scope"] == "live-worker-self-report-over-authenticated-rpc"
    assert strong_tier_contract["requirements"]["post_launch_required_fields"] == [
        "checked_at",
        "worker_instance_id",
    ]
    strong_validation_ids = {
        entry["id"]
        for entry in runners["certification_contract"]["assurance_validation_checks"]["strong-sandbox-certified"]
    }
    assert strong_validation_ids == {
        "runner_class_verification",
        "verified_runtime_identity",
        "verified_runtime_trust_chain",
        "verified_hardening_profile_state",
        "verified_stronger_isolation_reporting",
        "verified_resource_limit_mode",
        "verified_strong_boundary_properties",
        "live_session_continuity",
        "live_isolation_state_probe",
        "live_mount_network_policy_probe",
        "fail_closed_unavailability",
    }
    container_validation_ids = {
        entry["id"]
        for entry in runners["certification_contract"]["assurance_validation_checks"]["container-sandbox-certified"]
    }
    assert "verified_runtime_trust_chain" in container_validation_ids
    assert runners["active_certification_profile"]["validation_layers"]["runner_assurance"]["layer"] == "contained-process-certified"
    assert runners["platform_matrix"]["schema_version"] == "2026-05-21"
    assert runners["platform_matrix"]["current_platform"] in {"linux", "windows", "darwin", "other"}
    assert runners["platform_matrix"]["support_contract"]["strong_sandbox_supported_host_platforms"] == [
        "linux"
    ]
    assert runners["platform_matrix"]["support_contract"]["hostile_third_party_supported_host_platforms"] == [
        "linux"
    ]
    assert "linux" in runners["platform_matrix"]["supported_platforms"]
    assert "windows" in runners["platform_matrix"]["supported_platforms"]
    assert runners["platform_matrix"]["supported_platforms"]["linux"]["support_levels"]["strong_sandbox"]["support"] == "supported"
    assert runners["platform_matrix"]["supported_platforms"]["linux"]["equivalence_status"] == "full-strong-sandbox-support"
    assert runners["platform_matrix"]["supported_platforms"]["windows"]["support_levels"]["strong_sandbox"]["support"] == "unsupported"
    assert runners["platform_matrix"]["supported_platforms"]["windows"]["equivalence_status"] == "non-equivalent-container-grade-only"
    assert runners["support_contract"]["strong_sandbox_supported_host_platforms"] == ["linux"]
    assert runners["support_contract"]["hostile_third_party_supported_host_platforms"] == ["linux"]
    assert "not part of the declared strong-sandbox" in runners["support_contract"]["notes"]
    assert "fails closed" in runners["notes"]
    assert "directly observes at launch time" in runners["notes"]
    assert "post-launch verification reflects only live worker continuity and guard-state probes" in runners["notes"]
    assert "hostile-third-party" in runners["notes"]
    assert "not interchangeable" in runners["notes"]
    assert "Linux is the only declared fully supported host platform" in runners["notes"]


def test_runtime_public_contract_describes_external_python_override_precisely():
    metadata = runtime_public_contract_metadata()

    override = metadata["extensions"]["external_python_override"]
    assert override["env_var"] == "AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS"
    assert override["production_ack_env_var"] == "AINDY_ACK_UNSANDBOXED_EXTERNAL_PYTHON"
    assert override["default"] == "no direct in-process effect"
    assert override["sandboxing"] == "subprocess-boundary"
    assert "isolated plugin-host boundary" in override["effect_when_enabled"]


def test_runtime_public_contract_describes_trusted_in_process_python_precisely():
    metadata = runtime_public_contract_metadata()

    trusted = metadata["extensions"]["trusted_in_process_python"]
    assert trusted["owner_classes"] == ["runtime-built-in", "first-party-app"]
    assert trusted["explicit_exceptions"] == ["manifest bootstrap modules"]
    assert trusted["capability_boundary"]["mode"] == "explicit-runtime-owned-mediation"
    assert trusted["capability_boundary"]["first_party_bootstrap_default"] == "restricted-allowlist"
    assert trusted["sandboxing"] == "none"
    assert "GET /health" in trusted["operator_visibility"]
    assert "explicit privileged exception" in trusted["notes"]


def test_runtime_public_contract_describes_first_party_plugin_isolation_precisely():
    metadata = runtime_public_contract_metadata()

    dynamic_plugin_surface = next(
        entry
        for entry in metadata["extensions"]["experimental"]
        if entry["surface"] == "dynamic plugin nodes"
    )

    assert "First-party app plugin nodes and third-party plugin nodes use the isolated plugin-host boundary" in dynamic_plugin_surface["notes"]
