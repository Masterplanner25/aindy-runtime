"""
Cross-repo compatibility smoke tests.

Verifies the invariants that aindy-sdk and aindy-ui-kit depend on.
Run the full suite before any release that touches stable surfaces:

    pytest tests/unit/test_cross_repo_compatibility.py -v

Run only SDK-specific or UI-specific assertions:

    pytest tests/unit/test_cross_repo_compatibility.py -v -k sdk
    pytest tests/unit/test_cross_repo_compatibility.py -v -k ui

See docs/runtime/CROSS_REPO_COMPATIBILITY.md for the policy.
See docs/runtime/SDK_CONTRACT.md and UI_CONTRACT.md for the full surface definition.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only

# ---------------------------------------------------------------------------
# Stable syscall names — SDK contract (sys.v1 stable entries)
# ---------------------------------------------------------------------------

_STABLE_SYSCALLS = [
    "sys.v1.memory.read",
    "sys.v1.memory.write",
    "sys.v1.memory.search",
    "sys.v1.memory.tree",
    "sys.v1.memory.trace",
    "sys.v1.flow.run",
    "sys.v1.event.emit",
    "sys.v1.nodus.execute",
    "sys.v1.job.submit",
    "sys.v1.flow.execute_intent",
]


def test_stable_syscall_names_present_sdk():
    """SDK depends on these syscall names not being renamed or removed."""
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    registered = set(SYSCALL_REGISTRY.keys())
    missing = [name for name in _STABLE_SYSCALLS if name not in registered]
    assert missing == [], (
        f"Stable syscall(s) missing from registry: {missing}. "
        "Removing or renaming a stable syscall requires a MAJOR version bump. "
        "See docs/runtime/CROSS_REPO_COMPATIBILITY.md §2."
    )


# ---------------------------------------------------------------------------
# Version envelope shape — SDK contract
# ---------------------------------------------------------------------------

def test_api_version_envelope_shape_stable_sdk():
    """VersionResponse must expose all fields that aindy-sdk reads from /api/version."""
    from AINDY.routes.version_router import (
        RepoCompatibilityResponse,
        RuntimePackageResponse,
        VersionResponse,
    )

    # Pydantic model_fields available on v2; fall back to __fields__ for v1.
    def _fields(model):
        if hasattr(model, "model_fields"):
            return set(model.model_fields.keys())
        return set(model.__fields__.keys())

    version_fields = _fields(VersionResponse)
    assert "api_version" in version_fields
    assert "min_client_version" in version_fields
    assert "breaking_change_policy" in version_fields
    assert "compatibility" in version_fields
    assert "runtime" in version_fields

    compat_fields = _fields(RepoCompatibilityResponse)
    assert "runtime_package" in compat_fields
    assert "apps_repo_contract" in compat_fields

    pkg_fields = _fields(RuntimePackageResponse)
    assert "name" in pkg_fields
    assert "version" in pkg_fields


def test_api_version_compatibility_metadata_shape_sdk():
    """runtime_repo_compatibility_metadata() must return the declared keys."""
    from AINDY.platform_layer.runtime_compatibility import (
        RUNTIME_PACKAGE_NAME,
        runtime_repo_compatibility_metadata,
    )

    meta = runtime_repo_compatibility_metadata()

    assert "runtime_package" in meta
    pkg = meta["runtime_package"]
    assert pkg["name"] == RUNTIME_PACKAGE_NAME
    assert "version" in pkg

    assert "apps_repo_contract" in meta
    contract = meta["apps_repo_contract"]
    assert "recommended_runtime_requirement" in contract
    assert "compatible_runtime_major" in contract
    assert "compatible_api_major" in contract
    assert contract["declaration_format"] == "pep440"


# ---------------------------------------------------------------------------
# Watcher endpoint path — SDK contract
# ---------------------------------------------------------------------------

def test_watcher_endpoint_registered_sdk():
    """POST /watcher/signals and GET /watcher/signals must be accessible.

    aindy-sdk watcher client hardcodes these paths — they must remain
    registered in ROOT_ROUTERS under prefix /watcher.
    """
    from AINDY.routes import ROOT_ROUTERS

    watcher = None
    for router in ROOT_ROUTERS:
        prefix = getattr(router, "prefix", "")
        if prefix == "/watcher":
            watcher = router
            break

    assert watcher is not None, (
        "No router with prefix '/watcher' found in ROOT_ROUTERS. "
        "aindy-sdk watcher client hardcodes /watcher/signals — "
        "this router must remain in ROOT_ROUTERS."
    )

    # FastAPI stores the full path (prefix + route path) on router.routes objects.
    all_methods: dict[str, set[str]] = {}
    for route in watcher.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        all_methods.setdefault(path, set()).update(m.upper() for m in methods)

    signals_path = "/watcher/signals"
    assert signals_path in all_methods, (
        f"No route for {signals_path!r} found on the watcher router. "
        "POST /watcher/signals is required for aindy-sdk signal ingestion. "
        f"Found paths: {sorted(all_methods)}"
    )
    assert "POST" in all_methods[signals_path], (
        "POST /watcher/signals is missing — required for aindy-sdk signal ingestion."
    )
    assert "GET" in all_methods[signals_path], (
        "GET /watcher/signals is missing — required for aindy-sdk signal query."
    )


# ---------------------------------------------------------------------------
# boot_mode field in version metadata — UI contract
# ---------------------------------------------------------------------------

def test_boot_mode_field_in_version_metadata_ui():
    """RuntimeSurfaceResponse must include boot_mode.

    PlatformHomeRedirect (via bootIdentity in aindy-ui-kit/src/api/auth.js)
    reads data.system.runtime.boot_mode from /api/version to choose the
    post-login redirect destination (/agent vs /flows).
    """
    from AINDY.routes.version_router import RuntimeSurfaceResponse

    def _fields(model):
        if hasattr(model, "model_fields"):
            return set(model.model_fields.keys())
        return set(model.__fields__.keys())

    fields = _fields(RuntimeSurfaceResponse)
    assert "boot_mode" in fields, (
        "boot_mode is missing from RuntimeSurfaceResponse. "
        "Removing this field breaks PlatformHomeRedirect in the platform SPA. "
        "See docs/runtime/UI_CONTRACT.md §Boot Mode Detection."
    )


def test_boot_mode_present_in_deployment_contract_state_ui():
    """runtime_ui_surface_state() must include a non-empty boot_mode key."""
    from AINDY.platform_layer.deployment_contract import runtime_ui_surface_state

    state = runtime_ui_surface_state()
    assert "boot_mode" in state, "boot_mode key missing from runtime_ui_surface_state()"
    assert isinstance(state["boot_mode"], str)
    assert len(state["boot_mode"]) > 0


# ---------------------------------------------------------------------------
# Served platform routes match expected prefixes — UI contract
# ---------------------------------------------------------------------------

_REQUIRED_PLATFORM_PREFIXES = [
    "/platform/flows",
    "/platform/observability",
    "/platform/db",
    "/platform/syscalls",
]


def test_served_platform_routes_match_expected_prefixes_ui():
    """All operator prefixes referenced in UI_CONTRACT.md must be served.

    PLATFORM_ROUTERS child routers have prefixes like /flows, /observability,
    /db — they are registered with prefix="/platform" in routing.py, giving
    effective paths /platform/flows, /platform/observability, /platform/db.

    The /platform/syscalls route lives directly on platform_router (prefix=/platform).

    The platform SPA operator panel depends on all of these being served.
    """
    from AINDY.routes import PLATFORM_ROUTERS, platform_router

    # Effective prefixes from PLATFORM_ROUTERS: each child prefix is mounted
    # under /platform in routing.py (include_router(route, prefix="/platform")).
    effective_prefixes = {
        "/platform" + getattr(router, "prefix", "")
        for router in PLATFORM_ROUTERS
    }

    # Routes registered directly on platform_router (prefix=/platform already baked in).
    platform_direct_paths = {
        getattr(route, "path", "")
        for route in platform_router.routes
    }

    def _is_served(required: str) -> bool:
        # Matched by a PLATFORM_ROUTERS child with effective prefix
        if any(
            ep.startswith(required) or required.startswith(ep)
            for ep in effective_prefixes
        ):
            return True
        # Matched by a direct route on platform_router
        return any(p == required or p.startswith(required) for p in platform_direct_paths)

    missing = [p for p in _REQUIRED_PLATFORM_PREFIXES if not _is_served(p)]

    assert missing == [], (
        f"Expected platform prefixes not served: {missing}. "
        "The platform SPA operator panel depends on these routes being registered. "
        "See docs/runtime/UI_CONTRACT.md §Operator Endpoint Availability."
    )
