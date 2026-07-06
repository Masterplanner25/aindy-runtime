"""
Cross-repo compatibility smoke tests.

Verifies the invariants that aindy-sdk and aindy-ui-kit depend on.
Run the full suite before any release that touches stable surfaces:

    pytest tests/unit/test_cross_repo_compatibility.py -v

Run only SDK-specific or UI-specific assertions:

    pytest tests/unit/test_cross_repo_compatibility.py -v -k sdk
    pytest tests/unit/test_cross_repo_compatibility.py -v -k ui

Run only condition code assertions:

    pytest tests/unit/test_cross_repo_compatibility.py -v -k condition

See docs/runtime/CROSS_REPO_COMPATIBILITY.md for the policy.
See docs/runtime/SDK_CONTRACT.md, UI_CONTRACT.md, and CONDITION_CODES.md for surface definitions.
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
    # agent.execute is registered stable and documented in the apps
    # API_REFERENCE.md Syscall Reference; it drives the approve→execute path.
    # Rename/remove = MAJOR bump like the SDK-called entries above.
    "sys.v1.agent.execute",
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


def _collect_router_paths(router) -> set:
    """Collect all served paths from a router, recursing into sub-routers.

    Compatible with both FastAPI ≤ 0.135 (routes are eagerly flattened into
    the parent's route list with full prefixes) and FastAPI ≥ 0.137 (sub-routers
    are wrapped in lazy _IncludedRouter objects; the effective path is
    include_context.prefix + original_route.path).
    """
    from fastapi.routing import APIRoute

    try:
        from fastapi.routing import _IncludedRouter
    except ImportError:
        _IncludedRouter = None

    def _walk(route_list):
        for route in route_list:
            if isinstance(route, APIRoute):
                yield route.path
            if _IncludedRouter is not None and isinstance(route, _IncludedRouter):
                prefix = getattr(route.include_context, "prefix", "") or ""
                for sub_path in _walk(route.original_router.routes):
                    yield prefix + sub_path

    return set(_walk(router.routes))


def test_served_platform_routes_match_expected_prefixes_ui():
    """All operator prefixes referenced in UI_CONTRACT.md must be served.

    PLATFORM_ROUTERS child routers have prefixes like /flows, /observability,
    /db — they are registered with prefix="/platform" in routing.py, giving
    effective paths /platform/flows, /platform/observability, /platform/db.

    The /platform/syscalls route lives on platform_router (prefix=/platform),
    included via platform_ops_router.

    The platform SPA operator panel depends on all of these being served.
    """
    from AINDY.routes import PLATFORM_ROUTERS, platform_router

    # Effective prefixes from PLATFORM_ROUTERS: each child prefix is mounted
    # under /platform in routing.py (include_router(route, prefix="/platform")).
    effective_prefixes = {
        "/platform" + getattr(router, "prefix", "")
        for router in PLATFORM_ROUTERS
    }

    # All paths served by platform_router (handles nested sub-routers in 0.137+).
    platform_direct_paths = _collect_router_paths(platform_router)

    def _is_served(required: str) -> bool:
        # Matched by a PLATFORM_ROUTERS child with effective prefix
        if any(
            ep.startswith(required) or required.startswith(ep)
            for ep in effective_prefixes
        ):
            return True
        # Matched by a route on platform_router (or its included sub-routers)
        return any(p == required or p.startswith(required) for p in platform_direct_paths)

    missing = [p for p in _REQUIRED_PLATFORM_PREFIXES if not _is_served(p)]

    assert missing == [], (
        f"Expected platform prefixes not served: {missing}. "
        "The platform SPA operator panel depends on these routes being registered. "
        "See docs/runtime/UI_CONTRACT.md §Operator Endpoint Availability."
    )


# ---------------------------------------------------------------------------
# Exact documented runtime-owned routes — API_REFERENCE contract
# ---------------------------------------------------------------------------
#
# Exact (method, path) freeze for every runtime-owned surface documented in the
# apps repo `docs/api/API_REFERENCE.md`. The prefix test above guards the four
# operator prefixes the SPA panel needs; this list is stricter — it pins each
# individual path + verb so a rename/removal of any documented runtime route
# (not just the prefix) fails CI. App-owned `/apps/*` routes are intentionally
# excluded: they live in aindy-apps-monolith and are that repo's contract.
#
# Adding a runtime route is fine (this asserts a subset is served, not equality).
# Renaming or dropping one listed here is a breaking change to a documented
# surface and requires a coordinated apps-repo doc update + version policy.

_DOCUMENTED_RUNTIME_ROUTES = [
    # Flow Engine + Platform flows
    ("GET", "/platform/flows"),
    ("POST", "/platform/flows"),
    ("GET", "/platform/flows/registry"),
    ("GET", "/platform/flows/runs"),
    ("GET", "/platform/flows/runs/{run_id}"),
    ("GET", "/platform/flows/runs/{run_id}/history"),
    ("POST", "/platform/flows/runs/{run_id}/resume"),
    ("GET", "/platform/flows/{name}"),
    ("DELETE", "/platform/flows/{name}"),
    ("POST", "/platform/flows/{name}/run"),
    # Keys
    ("GET", "/platform/keys"),
    ("POST", "/platform/keys"),
    ("GET", "/platform/keys/{key_id}"),
    ("DELETE", "/platform/keys/{key_id}"),
    # Platform memory (MAS)
    ("GET", "/platform/memory"),
    ("GET", "/platform/memory/trace"),
    ("GET", "/platform/memory/tree"),
    # Nodes (extension registry)
    ("GET", "/platform/nodes"),
    ("POST", "/platform/nodes/register"),
    ("GET", "/platform/nodes/{name}"),
    ("DELETE", "/platform/nodes/{name}"),
    # Nodus
    ("POST", "/platform/nodus/flow"),
    ("POST", "/platform/nodus/run"),
    ("GET", "/platform/nodus/schedule"),
    ("POST", "/platform/nodus/schedule"),
    ("DELETE", "/platform/nodus/schedule/{job_id}"),
    ("GET", "/platform/nodus/scripts"),
    ("GET", "/platform/nodus/trace/{trace_id}"),
    ("POST", "/platform/nodus/upload"),
    # Syscall surface
    ("POST", "/platform/syscall"),
    ("GET", "/platform/syscalls"),
    # Tenant usage
    ("GET", "/platform/tenants/{tenant_id}/usage"),
    # Webhooks
    ("GET", "/platform/webhooks"),
    ("POST", "/platform/webhooks"),
    ("GET", "/platform/webhooks/{subscription_id}"),
    ("DELETE", "/platform/webhooks/{subscription_id}"),
    # Observability
    ("GET", "/platform/observability/dashboard"),
    ("GET", "/platform/observability/execution_graph/{trace_id}"),
    ("GET", "/platform/observability/llm/status"),
    ("POST", "/platform/observability/queue/dlq/drain"),
    ("GET", "/platform/observability/queue/metrics"),
    ("GET", "/platform/observability/requests"),
    ("GET", "/platform/observability/scheduler/status"),
    # Health + readiness
    ("GET", "/health"),
    ("GET", "/health/"),
    ("GET", "/health/deep"),
    ("GET", "/health/detail"),
    ("GET", "/health/details"),
    ("GET", "/ready"),
    # Auth
    ("POST", "/auth/login"),
    ("POST", "/auth/register"),
]


def _collect_served_methods(router, base: str = "") -> dict:
    """Map effective path -> set of HTTP methods, recursing into sub-routers.

    Method-aware sibling of `_collect_router_paths`. Compatible with both
    FastAPI ≤ 0.135 (eager flatten) and ≥ 0.137 (lazy `_IncludedRouter`).
    """
    from fastapi.routing import APIRoute

    try:
        from fastapi.routing import _IncludedRouter
    except ImportError:
        _IncludedRouter = None

    out: dict = {}
    for route in router.routes:
        if isinstance(route, APIRoute):
            methods = {m.upper() for m in (route.methods or set())}
            out.setdefault(base + route.path, set()).update(methods)
        if _IncludedRouter is not None and isinstance(route, _IncludedRouter):
            prefix = getattr(route.include_context, "prefix", "") or ""
            for path, methods in _collect_served_methods(
                route.original_router, base + prefix
            ).items():
                out.setdefault(path, set()).update(methods)
    return out


def test_documented_runtime_routes_served_exact_path():
    """Every runtime-owned route in the apps API_REFERENCE.md must be served.

    Exact (method, path) freeze — stricter than the prefix test above. Guards
    against a rename/removal of any individual documented runtime route, not
    just the operator prefix. See docs/runtime/PUBLIC_API_CONTRACT.md and the
    apps-repo docs/api/API_REFERENCE.md (runtime-owned surfaces only).
    """
    from AINDY.routes import PLATFORM_ROUTERS, ROOT_ROUTERS, platform_router

    # platform_router carries /platform on its own routes + included sub-routers.
    served = _collect_served_methods(platform_router)
    # PLATFORM_ROUTERS children are mounted under /platform in routing.py.
    for child in PLATFORM_ROUTERS:
        for path, methods in _collect_served_methods(child, "/platform").items():
            served.setdefault(path, set()).update(methods)
    # ROOT_ROUTERS carry their own prefixes (/health, /auth, ...) already.
    for child in ROOT_ROUTERS:
        for path, methods in _collect_served_methods(child).items():
            served.setdefault(path, set()).update(methods)

    missing = [
        f"{method} {path}"
        for method, path in _DOCUMENTED_RUNTIME_ROUTES
        if path not in served or method not in served[path]
    ]

    assert missing == [], (
        f"Documented runtime-owned route(s) no longer served: {missing}. "
        "These paths are published in aindy-apps-monolith docs/api/API_REFERENCE.md "
        "and consumed cross-repo. Renaming/removing one is a breaking change — "
        "update the apps API_REFERENCE.md and follow the version policy. "
        "See docs/runtime/PUBLIC_API_CONTRACT.md and CROSS_REPO_COMPATIBILITY.md."
    )


# ---------------------------------------------------------------------------
# Stable operator-facing condition codes — operator / automation contract
# ---------------------------------------------------------------------------

_STABLE_RUNTIME_CONDITION_CODES = [
    "external_python_override_enabled",
    "redis_single_instance_mode",
    "event_bus_local_only",
    "event_bus_rehydration_drain_failed",
    "event_bus_subscriber_unavailable",
    "distributed_worker_unavailable",
    "queue_backend_fallback",
    "mongo_required_unavailable",
    "mongo_optional_unavailable",
    "dynamic_registry_restore_incomplete",
    "dynamic_registry_restore_failed",
    "wait_eus_rehydration_failed",
    "flow_run_rehydration_failed",
]

_STABLE_READINESS_BLOCKER_CODES = [
    "startup_incomplete",
    "postgres",
    "schema",
    "redis",
    "queue",
    "event_bus",
    "worker",
    "scheduler",
    "plugin_hosts",
    "plugin_sandbox_attestation",
]

_STABLE_CONDITION_CLASSIFICATIONS = [
    "safe_degraded",
    "unsafe_degraded",
    "startup_fatal",
]

_STABLE_FLOW_RUN_STATUSES = ["running", "waiting", "completed", "failed"]

_STABLE_AGENT_RUN_STATUSES = [
    "pending_approval",
    "approved",
    "executing",
    "delegated",
    "completed",
    "failed",
    "cancelled",  # AGENT-HARDEN-1 — operator-driven terminal cancel state
    "verify_failed",  # AGENT-HARDEN-6 — post-condition verification failed (terminal)
]


def test_runtime_condition_codes_stable_operator():
    """All stable RuntimeConditionCode values must remain importable by string.

    Operators, automation tooling, and incident systems key on these string values
    from /ready and /health responses. Removing or renaming requires a MAJOR bump.
    See docs/runtime/CONDITION_CODES.md.
    """
    from AINDY.kernel.condition_codes import RuntimeConditionCode

    enum_values = {member.value for member in RuntimeConditionCode}
    missing = [code for code in _STABLE_RUNTIME_CONDITION_CODES if code not in enum_values]
    assert missing == [], (
        f"Stable RuntimeConditionCode value(s) removed: {missing}. "
        "These codes appear in /ready and /health responses. "
        "Removing them breaks operator automation. "
        "See docs/runtime/CONDITION_CODES.md and CROSS_REPO_COMPATIBILITY.md."
    )


def test_readiness_blocker_codes_stable_operator():
    """All stable ReadinessBlockerCode values must remain importable by string.

    These codes appear in the required_failures list of /ready (HTTP 503) responses.
    Operators and monitoring dashboards key on them for alerting.
    See docs/runtime/CONDITION_CODES.md.
    """
    from AINDY.kernel.condition_codes import ReadinessBlockerCode

    enum_values = {member.value for member in ReadinessBlockerCode}
    missing = [code for code in _STABLE_READINESS_BLOCKER_CODES if code not in enum_values]
    assert missing == [], (
        f"Stable ReadinessBlockerCode value(s) removed: {missing}. "
        "These codes appear in /ready required_failures. "
        "See docs/runtime/CONDITION_CODES.md."
    )


def test_condition_classifications_stable_operator():
    """ConditionClassification values must match the documented set."""
    from AINDY.kernel.condition_codes import ConditionClassification

    enum_values = {member.value for member in ConditionClassification}
    missing = [c for c in _STABLE_CONDITION_CLASSIFICATIONS if c not in enum_values]
    assert missing == [], (
        f"Stable ConditionClassification value(s) removed: {missing}. "
        "These classifications gate /ready readiness. "
        "See docs/runtime/CONDITION_CODES.md."
    )


def test_flow_run_statuses_stable_operator():
    """FlowRunStatus values must remain consistent with the DB schema."""
    from AINDY.kernel.condition_codes import FlowRunStatus

    enum_values = {member.value for member in FlowRunStatus}
    missing = [s for s in _STABLE_FLOW_RUN_STATUSES if s not in enum_values]
    assert missing == [], (
        f"Stable FlowRunStatus value(s) removed: {missing}. "
        "Flow status strings are stored in the database and returned in API responses. "
        "See docs/runtime/CONDITION_CODES.md."
    )


def test_agent_run_statuses_stable_operator():
    """AgentRunStatus values must remain consistent with the DB schema."""
    from AINDY.kernel.condition_codes import AgentRunStatus

    enum_values = {member.value for member in AgentRunStatus}
    missing = [s for s in _STABLE_AGENT_RUN_STATUSES if s not in enum_values]
    assert missing == [], (
        f"Stable AgentRunStatus value(s) removed: {missing}. "
        "Agent run status strings are stored in the database and returned in API responses. "
        "See docs/runtime/CONDITION_CODES.md."
    )


def test_condition_codes_module_importable_operator():
    """AINDY.kernel.condition_codes must be importable with all expected classes."""
    from AINDY.kernel.condition_codes import (  # noqa: F401
        AgentRunStatus,
        AutonomyDecision,
        ConditionClassification,
        DependencyStatus,
        FlowRunStatus,
        PublicHealthStatus,
        ReadinessBlockerCode,
        RuntimeConditionCode,
        SyscallResponseStatus,
    )
