import pytest

from AINDY._version import __version__ as RUNTIME_PACKAGE_VERSION
from AINDY.platform_layer.deployment_contract import publish_api_runtime_state


pytestmark = pytest.mark.runtime_only


def test_version_route_includes_runtime_surface(runtime_only_client):
    publish_api_runtime_state(
        process_role="api",
        boot_mode="runtime-only",
        boot_profile="platform-only",
        boot_profile_source="AINDY_BOOT_MODE",
        deployment_profile="single-instance",
        deployment_profile_source="AINDY_DEPLOYMENT_PROFILE",
        background_leadership_mode="in-process",
        app_plugins_loaded=False,
        app_plugin_count=0,
        external_python_override_active=False,
        external_python_override_execution_model="external-python-blocked",
    )

    response = runtime_only_client.get("/api/version")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime"]["process_role"] == "api"
    assert payload["runtime"]["boot_mode"] == "runtime-only"
    assert payload["runtime"]["boot_profile"] == "platform-only"
    assert payload["runtime"]["boot_profile_source"] == "AINDY_BOOT_MODE"
    assert payload["runtime"]["deployment_profile"] == "single-instance"
    assert payload["runtime"]["deployment_profile_source"] == "AINDY_DEPLOYMENT_PROFILE"
    assert payload["runtime"]["background_leadership_mode"] == "in-process"
    assert payload["runtime"]["app_plugins_loaded"] is False
    assert payload["runtime"]["app_plugin_count"] == 0
    assert payload["runtime"]["external_python_override_active"] is False
    assert (
        payload["runtime"]["external_python_override_execution_model"]
        == "external-python-blocked"
    )
    assert payload["runtime"]["ui_mode"] == "runtime-only"
    assert payload["runtime"]["default_route"] == "/memory"
    assert payload["runtime"]["platform_home"] == "/platform/agent"
    assert payload["runtime"]["trusted_python_execution"] == {
        "present": False,
        "execution_model": "trusted-in-process-python",
        "sandboxing": "none",
        "capability_boundary_mode": "explicit-runtime-owned-mediation",
        "total_count": 0,
        "manifest_module_count": 0,
        "bootstrap_registration_count": 0,
        "plugin_node_count": 0,
        "owner_classes_present": [],
        "owner_class_counts": {
            "runtime-built-in": 0,
            "first-party-app": 0,
            "external-third-party": 0,
        },
        "operator_note": (
            "Trusted Python extensions execute in-process with full interpreter "
            "privileges. This inventory is an audit surface, not a sandbox boundary. "
            "Residual in-process execution is confined only by explicit runtime-owned "
            "capability mediation on official registration surfaces."
        ),
    }
    assert payload["runtime"]["extension_execution_posture"]["schema_version"] == "2026-05-22"
    assert payload["runtime"]["extension_execution_posture"]["attestation_scope"]["plugin_sandbox_attestation"]["covered_execution_model_class"] == (
        "isolated-externalized"
    )
    version_surface_ids = {
        entry["surface_id"]
        for entry in payload["runtime"]["extension_execution_posture"]["surface_matrix"]
    }
    assert {
        "manifest-bootstrap:runtime-built-in",
        "manifest-bootstrap:first-party-app",
        "registry-kernel-callable:first-party-app",
        "runtime-callback-worker:first-party-app",
        "dynamic-plugin-node:runtime-built-in",
        "dynamic-plugin-node:first-party-app",
        "dynamic-plugin-node:external-third-party",
        "webhook-node:any-owner",
        "webhook-subscription:any-owner",
        "dynamic-flow:any-owner",
    }.issubset(version_surface_ids)
    assert payload["runtime"]["extension_provenance"]["present"] is False
    assert payload["runtime"]["extension_provenance"]["total_count"] == 0
    assert payload["runtime"]["plugin_hosts"]["present"] is False
    assert payload["runtime"]["plugin_hosts"]["sandbox_runner_interface_version"] == "2026-05-21"
    assert payload["runtime"]["plugin_hosts"]["default_runner_type"] == "insecure_dev_subprocess"
    assert payload["runtime"]["plugin_hosts"]["runner_types_present"] == []
    assert payload["runtime"]["plugin_sandbox_attestation"] == {
        "present": False,
        "host_count": 0,
        "covered_execution_model_class": "isolated-externalized",
        "covered_surface_ids": [
            "dynamic-plugin-node:first-party-app",
            "dynamic-plugin-node:external-third-party",
        ],
        "runner_types_present": [],
        "assurance_classes_present": [],
        "isolation_classes_present": [],
        "certification_tiers_present": [],
        "post_launch_verification_statuses_present": [],
        "active_hardening_controls_present": [],
        "hosts": [],
        "operator_note": (
            "Sandbox attestation summarizes requested policy, active runner metadata, launch-verified "
            "state, pinned runtime identity, resource limits, and provenance for isolated plugin hosts."
        ),
    }
    assert payload["runtime"]["plugin_sandbox_posture"] == {
        "deployment_profile": "single-instance",
        "current": {
            "runner_type": "insecure_dev_subprocess",
            "assurance_class": "insecure-dev",
            "runtime_trust_status": "missing-reference",
            "certification_tier": "contained-process-certified",
            "certification_status": "certified",
        },
        "covered_execution_model_class": "isolated-externalized",
        "covered_surface_ids": [
            "dynamic-plugin-node:first-party-app",
            "dynamic-plugin-node:external-third-party",
        ],
        "required": {
            "assurance_class": None,
            "runner_type": None,
            "certification_tier": None,
        },
        "requirement_status": {
            "assurance_class_satisfied": True,
            "certification_tier_satisfied": True,
        },
        "platform_support": {
            "current_platform": payload["runtime"]["plugin_sandbox_platform"]["current_platform"],
            "current_equivalence_status": payload["runtime"]["plugin_sandbox_platform"]["current_environment"]["equivalence_status"],
            "strong_sandbox_supported_host_platforms": ["linux"],
            "hostile_third_party_supported_host_platforms": ["linux"],
        },
        "unsupported_claims": [
            "general third-party sandboxing",
            "hard resource-limit enforcement",
            "kernel-level isolation guarantees",
        ],
        "distinction_note": (
            "Assurance class describes the runner category, attestation describes what the runtime "
            "observed, and certification describes what the runtime can justify from verified evidence."
        ),
        "notes": "This profile does not require a third-party sandbox assurance class.",
    }
    assert payload["runtime"]["plugin_sandbox_platform"]["schema_version"] == "2026-05-21"
    assert payload["runtime"]["plugin_sandbox_platform"]["current_platform"] in {"linux", "windows", "darwin", "other"}
    assert payload["runtime"]["plugin_sandbox_platform"]["support_contract"]["strong_sandbox_supported_host_platforms"] == [
        "linux"
    ]
    assert payload["runtime"]["plugin_sandbox_platform"]["support_contract"]["hostile_third_party_supported_host_platforms"] == [
        "linux"
    ]
    assert "supported_platforms" in payload["runtime"]["plugin_sandbox_platform"]
    runtime_runner_types = {entry["runner_type"] for entry in payload["runtime"]["plugin_hosts"]["available_runners"]}
    assert runtime_runner_types == {"insecure_dev_subprocess", "containerized_oci", "strong_sandbox_vm"}
    assert payload["compatibility"] == {
        "runtime_package": {
            "name": "aindy-runtime",
            "version": RUNTIME_PACKAGE_VERSION,
        },
        "apps_repo_contract": {
            "declaration_format": "pep440",
            "recommended_runtime_requirement": ">=1.0,<2.0",
            "compatible_runtime_major": "1",
            "compatible_api_major": "1",
            "policy": (
                "The apps repo must declare a normal Python dependency range on "
                "aindy-runtime with an explicit upper bound before the next MAJOR "
                "runtime release. Runtime package MAJOR and API MAJOR indicate "
                "repo-split compatibility boundaries."
            ),
        },
    }
    assert payload["public_contract"]["schema_version"] == "2026-05-20"
    assert payload["public_contract"]["api_major"] == "1"
    assert payload["public_contract"]["release_posture"]["support_tier"] == "trusted-internal"
    assert "third-party extension isolation" in payload["public_contract"]["release_posture"]["not_claimed"]
    stable_routes = {entry["route"] for entry in payload["public_contract"]["http"]["stable"]}
    assert "GET /api/version" in stable_routes
    assert "GET /platform/syscalls" in stable_routes
    experimental_prefixes = {entry["route_prefix"] for entry in payload["public_contract"]["http"]["experimental"]}
    assert "/platform/nodes" in experimental_prefixes
    assert payload["public_contract"]["extensions"]["external_python_override"]["env_var"] == "AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS"
    assert payload["public_contract"]["extensions"]["external_python_override"]["sandboxing"] == "subprocess-boundary"
    assert "plugin-host" in payload["public_contract"]["extensions"]["external_python_override"]["effect_when_enabled"]
    assert payload["public_contract"]["extensions"]["trusted_in_process_python"]["sandboxing"] == "none"
    assert payload["public_contract"]["extensions"]["abi"]["surfaces"]["manifest"]["supported_versions"] == [
        "aindy.extension.manifest/v1"
    ]
    assert payload["public_contract"]["extensions"]["provenance_policy"]["policy_version"] == "2026-05-20"
    assert payload["public_contract"]["extensions"]["provenance_policy"]["signing"]["status"] == "supported"
    assert payload["public_contract"]["extensions"]["execution_models"]["schema_version"] == "2026-05-22"
    assert payload["public_contract"]["extensions"]["execution_models"]["attestation_scope"]["plugin_sandbox_attestation"]["covered_surface_ids"] == [
        "dynamic-plugin-node:first-party-app",
        "dynamic-plugin-node:external-third-party",
    ]
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["default_external_runner"] == "insecure_dev_subprocess"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["configured_selection"] == "auto"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["certification_contract"]["schema_version"] == "2026-05-21"
    assert {
        entry["tier"]
        for entry in payload["public_contract"]["extensions"]["sandbox_runners"]["certification_contract"]["certification_tiers"]
    } == {
        "contained-process-certified",
        "container-sandbox-certified",
        "strong-sandbox-certified",
    }
    assert {
        entry["id"]
        for entry in payload["public_contract"]["extensions"]["sandbox_runners"]["certification_contract"]["assurance_validation_checks"]["strong-sandbox-certified"]
    } == {
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
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["active_certification_profile"]["runner_type"] == "insecure_dev_subprocess"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["active_certification_profile"]["certification_tier"] == "contained-process-certified"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["active_certification_profile"]["tier_status"] == "certified"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["active_certification_profile"]["uncertified_reasons"] == []
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["active_certification_profile"]["validation_layers"]["runner_assurance"]["layer"] == "contained-process-certified"
    strong_tier_contract = next(
        entry
        for entry in payload["public_contract"]["extensions"]["sandbox_runners"]["certification_contract"]["certification_tiers"]
        if entry["tier"] == "strong-sandbox-certified"
    )
    assert strong_tier_contract["requirements"]["post_launch_verification_scope"] == "live-worker-self-report-over-authenticated-rpc"
    assert strong_tier_contract["requirements"]["post_launch_required_fields"] == [
        "checked_at",
        "worker_instance_id",
    ]
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["selection_policy"]["strong_runner_requires_explicit_selection"] == "strong_sandbox_vm"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["selection_policy"]["pinned_runtime_identity_required_for_production_safe_profiles"] is True
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["selection_policy"]["trusted_runtime_identity_chain_required_for_production_safe_profiles"] is True
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["selection_policy"]["hostile_third_party_profile"]["profile"] == "hostile-third-party"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["selection_policy"]["hostile_third_party_profile"]["required_runner_type"] == "strong_sandbox_vm"
    assert "launch_attestation.runtime_identity" in payload["public_contract"]["extensions"]["sandbox_runners"]["selection_policy"]["hostile_third_party_profile"]["required_verified_fields"]
    assert "post_launch_verification.session_continuity" in payload["public_contract"]["extensions"]["sandbox_runners"]["selection_policy"]["hostile_third_party_profile"]["required_verified_fields"]
    assert "post_launch_verification.isolation_state" in payload["public_contract"]["extensions"]["sandbox_runners"]["selection_policy"]["hostile_third_party_profile"]["required_verified_fields"]
    assert "post_launch_verification.mount_network_state.artifact_write_blocked" in payload["public_contract"]["extensions"]["sandbox_runners"]["selection_policy"]["hostile_third_party_profile"]["required_verified_fields"]
    assert "post_launch_verification.mount_network_state.network_policy.deny_by_default_outbound" in payload["public_contract"]["extensions"]["sandbox_runners"]["selection_policy"]["hostile_third_party_profile"]["required_verified_fields"]
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["selection_policy"]["hostile_third_party_profile"]["required_active_policies"]["runtime_trust_chain"] == "trusted-signed-pinned-compatible"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["version_surface"] == "runtime.plugin_sandbox_attestation"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["assurance_posture_surface"] == "runtime.plugin_sandbox_posture"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["platform_matrix_surface"] == "runtime.plugin_sandbox_platform"
    assert "execution_model_class" in payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_fields"]
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_model"]["verified"] == "launch-observed backend identity and command evidence only"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_model"]["post_launch_verified"] == "live worker continuity and guard-state checks over a runtime-owned authenticated probe"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_model"]["coverage"] == "plugin sandbox attestation and certification cover isolated plugin-host execution only"
    assert "partial live verification" in payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_model"]["live_mount_and_network"]
    assert "source, issuer, signing-status, and base-compatibility" in payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_model"]["runtime_trust_chain"]
    assert "process containment, container-grade sandboxing, and strong sandbox behavior" in payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_model"]["assurance_properties"]
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_model"]["assurance_class"] == "the current runner category reported by the runtime"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_model"]["required_assurance_class"] == "the minimum class required by the active deployment profile"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_model"]["certification_tier"] == "derived only from runner-specific verified evidence and shared worker-policy eligibility"
    assert "certification" in payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_fields"]
    assert "runtime_identity" in payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_fields"]
    assert "runtime_identity.trust_chain" in payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_fields"]
    assert "assurance_properties" in payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_fields"]
    assert "launch_attestation" in payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_fields"]
    assert "post_launch_verification" in payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_fields"]
    assert "mount_isolation" in payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_fields"]
    assert "network_isolation" in payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_fields"]
    assert "mount_isolation.live_verification" in payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_fields"]
    assert "network_isolation.live_verification" in payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["attestation_fields"]
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["platform_matrix"]["schema_version"] == "2026-05-21"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["platform_matrix"]["support_contract"]["strong_sandbox_supported_host_platforms"] == [
        "linux"
    ]
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["platform_matrix"]["supported_platforms"]["linux"]["equivalence_status"] == "full-strong-sandbox-support"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["platform_matrix"]["supported_platforms"]["windows"]["equivalence_status"] == "non-equivalent-container-grade-only"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["support_contract"]["hostile_third_party_supported_host_platforms"] == [
        "linux"
    ]
    public_runner_claims = {
        entry["runner_type"]: entry["isolation_claim"]
        for entry in payload["public_contract"]["extensions"]["sandbox_runners"]["available_runners"]
    }
    assert public_runner_claims["insecure_dev_subprocess"] == "none"
    assert public_runner_claims["containerized_oci"] == "container-boundary"
    assert public_runner_claims["strong_sandbox_vm"] == "vm-boundary"
    assert "hostile-third-party" in payload["public_contract"]["extensions"]["sandbox_runners"]["notes"]
    assert "post-launch verification reflects only live worker continuity and guard-state probes" in payload["public_contract"]["extensions"]["sandbox_runners"]["notes"]
    assert "not interchangeable" in payload["public_contract"]["extensions"]["sandbox_runners"]["notes"]
    assert "Linux is the only declared fully supported host platform" in payload["public_contract"]["extensions"]["sandbox_runners"]["notes"]
    assert payload["public_contract"]["extensions"]["capability_model"]["surfaces"]["dynamic-plugin-node"]["authority_model"] == "isolated-explicit-capabilities"
    assert payload["public_contract"]["extensions"]["capability_model"]["surfaces"]["dynamic-plugin-node"]["filesystem_policy"]["default"] == "read-only-approved-roots"
    assert payload["public_contract"]["extensions"]["capability_model"]["surfaces"]["dynamic-plugin-node"]["filesystem_policy"]["writes"] == "deny"
    assert "GET /api/version" in payload["public_contract"]["extensions"]["trusted_in_process_python"]["operator_visibility"]
    assert response.headers["X-API-Version"] == payload["api_version"]


def test_health_route_reports_trusted_python_inventory(runtime_only_client):
    response = runtime_only_client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["trusted_python_execution"]["execution_model"] == "trusted-in-process-python"
    assert payload["trusted_python_execution"]["sandboxing"] == "none"
    assert payload["trusted_python_execution"]["present"] is False
    assert payload["extension_provenance"]["present"] is False
    assert payload["plugin_hosts"]["present"] is False
    assert payload["plugin_sandbox_attestation"]["present"] is False
    assert payload["extension_execution_posture"]["schema_version"] == "2026-05-22"
    assert payload["extension_execution_posture"]["attestation_scope"]["plugin_sandbox_attestation"]["covered_execution_model_class"] == (
        "isolated-externalized"
    )
    assert payload["plugin_sandbox_posture"] == {
        "deployment_profile": "single-instance",
        "current": {
            "runner_type": "insecure_dev_subprocess",
            "assurance_class": "insecure-dev",
            "runtime_trust_status": "missing-reference",
            "certification_tier": "contained-process-certified",
            "certification_status": "certified",
        },
        "covered_execution_model_class": "isolated-externalized",
        "covered_surface_ids": [
            "dynamic-plugin-node:first-party-app",
            "dynamic-plugin-node:external-third-party",
        ],
        "required": {
            "assurance_class": None,
            "runner_type": None,
            "certification_tier": None,
        },
        "requirement_status": {
            "assurance_class_satisfied": True,
            "certification_tier_satisfied": True,
        },
        "platform_support": {
            "current_platform": payload["plugin_sandbox_platform"]["current_platform"],
            "current_equivalence_status": payload["plugin_sandbox_platform"]["current_environment"]["equivalence_status"],
            "strong_sandbox_supported_host_platforms": ["linux"],
            "hostile_third_party_supported_host_platforms": ["linux"],
        },
        "unsupported_claims": [
            "general third-party sandboxing",
            "hard resource-limit enforcement",
            "kernel-level isolation guarantees",
        ],
        "distinction_note": (
            "Assurance class describes the runner category, attestation describes what the runtime "
            "observed, and certification describes what the runtime can justify from verified evidence."
        ),
        "notes": "This profile does not require a third-party sandbox assurance class.",
    }
    assert payload["plugin_sandbox_platform"]["schema_version"] == "2026-05-21"


def test_health_sandbox_route_returns_posture(runtime_only_client):
    response = runtime_only_client.get("/health/sandbox")

    assert response.status_code == 200
    payload = response.json()

    # Top-level keys present
    assert "plugin_sandbox_posture" in payload
    assert "plugin_sandbox_platform" in payload
    assert "sandbox_verification_posture" in payload
    assert "trusted_python_execution" in payload
    assert "plugin_hosts" in payload
    assert "plugin_sandbox_attestation" in payload
    assert "runtime_conditions" in payload

    # Posture shape matches what /health returns
    posture = payload["plugin_sandbox_posture"]
    assert posture["current"]["runner_type"] == "insecure_dev_subprocess"
    assert posture["current"]["assurance_class"] == "insecure-dev"
    assert posture["requirement_status"]["assurance_class_satisfied"] is True
    assert posture["requirement_status"]["certification_tier_satisfied"] is True

    # Verification posture present
    verification = payload["sandbox_verification_posture"]
    assert "verification_method" in verification
    assert "kernel_observable" in verification
    assert "assurance_ceiling" in verification

    # Trusted python fields present and accurate for test env
    trusted_py = payload["trusted_python_execution"]
    assert trusted_py["present"] is False
    assert trusted_py["execution_model"] == "trusted-in-process-python"
    assert trusted_py["sandboxing"] == "none"

    # runtime_conditions is a list (empty in baseline test env)
    assert isinstance(payload["runtime_conditions"], list)
