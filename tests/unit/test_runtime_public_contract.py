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
    runner_types = {entry["runner_type"] for entry in runners["available_runners"]}
    assert runner_types == {"insecure_dev_subprocess", "containerized_oci"}
    claims = {entry["runner_type"]: entry["isolation_claim"] for entry in runners["available_runners"]}
    assert claims["insecure_dev_subprocess"] == "none"
    assert claims["containerized_oci"] == "container-boundary"
    container_runner = next(
        entry for entry in runners["available_runners"]
        if entry["runner_type"] == "containerized_oci"
    )
    assert container_runner["kernel_control_reporting"] == "explicit"
    assert runners["operator_reporting"]["version_surface"] == "runtime.plugin_sandbox_attestation"
    assert runners["operator_reporting"]["health_surface"] == "plugin_sandbox_attestation"
    assert runners["operator_reporting"]["platform_matrix_surface"] == "runtime.plugin_sandbox_platform"
    assert "isolation_class" in runners["operator_reporting"]["attestation_fields"]
    assert runners["certification_contract"]["schema_version"] == "2026-05-21"
    assert runners["active_certification_profile"]["runner_type"] == "insecure_dev_subprocess"
    assert runners["active_certification_profile"]["shared_worker_policy_status"] == "certifiable-shared-worker-policy"
    assert runners["platform_matrix"]["schema_version"] == "2026-05-21"
    assert runners["platform_matrix"]["current_platform"] in {"linux", "windows", "darwin", "other"}
    assert "linux" in runners["platform_matrix"]["supported_platforms"]
    assert "windows" in runners["platform_matrix"]["supported_platforms"]
    assert "fails closed" in runners["notes"]


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
    assert trusted["sandboxing"] == "none"
    assert "GET /health" in trusted["operator_visibility"]
    assert "full interpreter privileges" in trusted["notes"]
