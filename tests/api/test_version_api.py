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
            "privileges. This inventory is an audit surface, not a sandbox boundary."
        ),
    }
    assert payload["runtime"]["extension_provenance"]["present"] is False
    assert payload["runtime"]["extension_provenance"]["total_count"] == 0
    assert payload["runtime"]["plugin_hosts"]["present"] is False
    assert payload["runtime"]["plugin_hosts"]["sandbox_runner_interface_version"] == "2026-05-21"
    assert payload["runtime"]["plugin_hosts"]["default_runner_type"] == "insecure_dev_subprocess"
    assert payload["runtime"]["plugin_hosts"]["runner_types_present"] == []
    assert payload["runtime"]["plugin_sandbox_attestation"] == {
        "present": False,
        "host_count": 0,
        "runner_types_present": [],
        "isolation_classes_present": [],
        "active_hardening_controls_present": [],
        "hosts": [],
        "operator_note": (
            "Sandbox attestation summarizes the effective isolation mode, hardening controls, "
            "resource limits, and provenance state for third-party plugin hosts."
        ),
    }
    assert payload["runtime"]["plugin_sandbox_platform"]["schema_version"] == "2026-05-21"
    assert payload["runtime"]["plugin_sandbox_platform"]["current_platform"] in {"linux", "windows", "darwin", "other"}
    assert "supported_platforms" in payload["runtime"]["plugin_sandbox_platform"]
    runtime_runner_types = {entry["runner_type"] for entry in payload["runtime"]["plugin_hosts"]["available_runners"]}
    assert runtime_runner_types == {"insecure_dev_subprocess", "containerized_oci"}
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
    assert "/apps/agent/" in experimental_prefixes
    assert "/platform/nodes" in experimental_prefixes
    assert payload["public_contract"]["extensions"]["external_python_override"]["env_var"] == "AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS"
    assert payload["public_contract"]["extensions"]["external_python_override"]["sandboxing"] == "subprocess-boundary"
    assert "plugin-host" in payload["public_contract"]["extensions"]["external_python_override"]["effect_when_enabled"]
    assert payload["public_contract"]["extensions"]["trusted_in_process_python"]["sandboxing"] == "none"
    assert payload["public_contract"]["extensions"]["abi"]["surfaces"]["manifest"]["supported_versions"] == [
        "aindy.extension.manifest/v1"
    ]
    assert payload["public_contract"]["extensions"]["provenance_policy"]["policy_version"] == "2026-05-20"
    assert payload["public_contract"]["extensions"]["provenance_policy"]["signing"]["status"] == "unsupported"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["default_external_runner"] == "insecure_dev_subprocess"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["configured_selection"] == "auto"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["certification_contract"]["schema_version"] == "2026-05-21"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["active_certification_profile"]["runner_type"] == "insecure_dev_subprocess"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["version_surface"] == "runtime.plugin_sandbox_attestation"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["operator_reporting"]["platform_matrix_surface"] == "runtime.plugin_sandbox_platform"
    assert payload["public_contract"]["extensions"]["sandbox_runners"]["platform_matrix"]["schema_version"] == "2026-05-21"
    public_runner_claims = {
        entry["runner_type"]: entry["isolation_claim"]
        for entry in payload["public_contract"]["extensions"]["sandbox_runners"]["available_runners"]
    }
    assert public_runner_claims["insecure_dev_subprocess"] == "none"
    assert public_runner_claims["containerized_oci"] == "container-boundary"
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
    assert payload["plugin_sandbox_platform"]["schema_version"] == "2026-05-21"
