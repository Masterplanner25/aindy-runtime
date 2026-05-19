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
    )

    response = runtime_only_client.get("/api/version")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime"] == {
        "process_role": "api",
        "boot_mode": "runtime-only",
        "boot_profile": "platform-only",
        "boot_profile_source": "AINDY_BOOT_MODE",
        "deployment_profile": "single-instance",
        "deployment_profile_source": "AINDY_DEPLOYMENT_PROFILE",
        "background_leadership_mode": "in-process",
        "app_plugins_loaded": False,
        "app_plugin_count": 0,
        "ui_mode": "runtime-only",
        "default_route": "/memory",
        "platform_home": "/platform/agent",
    }
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
    assert payload["public_contract"]["schema_version"] == "2026-05-18"
    assert payload["public_contract"]["api_major"] == "1"
    stable_routes = {entry["route"] for entry in payload["public_contract"]["http"]["stable"]}
    assert "GET /api/version" in stable_routes
    assert "GET /platform/syscalls" in stable_routes
    experimental_prefixes = {entry["route_prefix"] for entry in payload["public_contract"]["http"]["experimental"]}
    assert "/apps/agent/" in experimental_prefixes
    assert "/platform/nodes" in experimental_prefixes
    assert response.headers["X-API-Version"] == payload["api_version"]
