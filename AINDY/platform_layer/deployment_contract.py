from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from AINDY.config import settings
from AINDY.platform_layer.extension_policy import external_python_override_state

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
SUPPORTED_DEPLOYMENT_PROFILES = (
    DEPLOYMENT_PROFILE_SINGLE_INSTANCE,
    DEPLOYMENT_PROFILE_DISTRIBUTED_API,
    DEPLOYMENT_PROFILE_DISTRIBUTED_WORKER,
)
RUNTIME_ONLY_REQUIRED_ROUTES = (
    "/health",
    "/ready",
    "/apps/agent/run",
    "/apps/agent/tools",
    "/apps/memory/recall",
    "/apps/memory/nodes",
    "/platform/syscalls",
)
RUNTIME_ONLY_REQUIRED_ROUTE_PREFIXES = (
    "/platform/",
    "/apps/agent/",
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
        "extension_provenance": extension_provenance_inventory(),
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
        }:
            raise ValueError(
                f"{DEPLOYMENT_PROFILE_ENV_VAR}={requested!r} is not valid for API startup. "
                f"Supported API values: {DEPLOYMENT_PROFILE_SINGLE_INSTANCE!r}, "
                f"{DEPLOYMENT_PROFILE_DISTRIBUTED_API!r}."
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


def validate_api_deployment_profile() -> dict[str, Any]:
    profile_name, source = resolve_api_deployment_profile()
    errors: list[str] = []
    if profile_name == DEPLOYMENT_PROFILE_SINGLE_INSTANCE:
        if settings.EXECUTION_MODE != "thread":
            errors.append(
                "single-instance profile requires EXECUTION_MODE=thread"
            )
    elif profile_name == DEPLOYMENT_PROFILE_DISTRIBUTED_API:
        if settings.EXECUTION_MODE != "distributed":
            errors.append(
                "distributed-api profile requires EXECUTION_MODE=distributed"
            )
        if not settings.REDIS_URL:
            errors.append(
                "distributed-api profile requires REDIS_URL for queue and event-bus coordination"
            )
    if profile_name == DEPLOYMENT_PROFILE_DISTRIBUTED_API:
        if os.getenv("AINDY_EVENT_BUS_ENABLED", "true").lower() in {"0", "false", "no", "off"}:
            errors.append(
                "distributed-api profile requires AINDY_EVENT_BUS_ENABLED=true"
            )
        if str(settings.AINDY_CACHE_BACKEND).lower() == "memory":
            errors.append(
                "distributed-api profile does not permit AINDY_CACHE_BACKEND=memory"
            )
    if errors:
        raise RuntimeError(
            f"Invalid deployment profile {profile_name!r}: " + "; ".join(errors)
        )
    contract = get_deployment_profile_contract(profile_name)
    contract["source"] = source
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
    contract = get_deployment_profile_contract(profile_name)
    contract["source"] = source
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
        },
        "requires": {
            "redis": redis_required(),
            "worker": worker_required(),
            "event_bus": event_bus_required(),
            "queue_backend": queue_backend_required(),
            "schema_enforcement": schema_enforcement_required(),
        },
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
    if value == RUNTIME_ONLY_BOOT_MODE:
        return value
    raise ValueError(
        f"Unsupported {BOOT_MODE_ENV_VAR} value {value!r}. "
        f"Supported values: {RUNTIME_ONLY_BOOT_MODE!r}."
    )


def resolve_profile_for_boot_mode(boot_mode: str | None) -> str | None:
    if boot_mode is None:
        return None
    if boot_mode == RUNTIME_ONLY_BOOT_MODE:
        return RUNTIME_ONLY_BOOT_PROFILE
    raise ValueError(f"Unsupported boot mode {boot_mode!r}")
