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


def test_runtime_public_contract_marks_extension_registration_surfaces_experimental():
    metadata = runtime_public_contract_metadata()
    extension_surfaces = {entry["surface"] for entry in metadata["extensions"]["experimental"]}

    assert "manifest bootstrap modules" in extension_surfaces
    assert "dynamic plugin nodes" in extension_surfaces
    assert "dynamic flows" in extension_surfaces
