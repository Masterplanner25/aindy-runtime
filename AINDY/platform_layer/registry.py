"""Platform extension registry.

The platform owns registries, not application behavior. Applications register
routers, syscalls, jobs, flows, event handlers, capture rules, and agent tools
from their own bootstrap modules.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
from collections import defaultdict
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
import hashlib
from functools import wraps

from AINDY.platform_layer.agent_plugin_contracts import CapabilityProviderBundle
from AINDY.platform_layer.deployment_contract import (
    BOOT_MODE_ENV_VAR,
    get_requested_boot_mode,
    resolve_profile_for_boot_mode,
)
from AINDY.platform_layer.extension_abi import (
    LEGACY_UNVERSIONED_MANIFEST,
    SURFACE_MANIFEST,
    extension_surface_stability,
    manifest_effective_abi_version,
    validate_extension_manifest_document,
)
from AINDY.platform_layer.extension_boundary import (
    sanitize_extension_context,
)
from AINDY.platform_layer.registry_contracts import (
    validate_agent_event,
    validate_agent_planner_backend,
    validate_agent_planner_context,
    validate_agent_ranking_strategy,
    validate_agent_run_tools,
    validate_agent_tool,
    validate_capability_definition,
    validate_capability_names,
    validate_event_handler,
    validate_execution_adapter,
    validate_flow_plan,
    validate_flow_registration,
    validate_flow_result_registration,
    validate_flow_strategy,
    validate_job_handler,
    validate_memory_policy,
    validate_response_adapter,
    validate_restricted_tool,
    validate_route_guard,
    validate_route_prefix,
    validate_router,
    validate_scheduled_job_entry,
    validate_startup_hook,
    validate_symbol,
    validate_symbols,
    validate_syscall_handler,
    validate_trigger_evaluator,
)
from AINDY.platform_layer.extension_policy import (
    assert_python_extension_allowed,
    python_extension_execution_metadata,
    python_extension_trust_class,
    validate_bootstrap_module_name,
)
from AINDY.platform_layer.extension_policy import (
    OWNER_EXTERNAL_THIRD_PARTY,
    OWNER_FIRST_PARTY_APP,
    OWNER_RUNTIME_BUILTIN,
    infer_bootstrap_owner_class,
)
from AINDY.platform_layer.runtime_callback_host import (
    RUNTIME_CALLBACK_EXECUTION_MODE,
    build_runtime_callback_spec,
    invoke_runtime_callback,
)
from AINDY.platform_layer.extension_provenance import (
    derive_python_extension_provenance,
)

logger = logging.getLogger(__name__)

Handler = Callable[..., Any]

_routers: list[Any] = []
_root_routers: list[Any] = []
_legacy_root_routers: list[Any] = []
_syscalls: dict[str, Handler] = {}
_jobs: dict[str, Handler] = {}
_flows: list[Handler] = []
_flow_result_keys: dict[str, str] = {}
_flow_result_extractors: dict[str, Handler] = {}
_flow_completion_events: dict[str, str] = {}
_flow_plans: dict[str, dict[str, Any]] = {}
_event_handlers: dict[str, list[Handler]] = defaultdict(list)
_event_types: set[str] = set()
_capture_rules: dict[str, Any] = {}
_memory_policies: dict[str, Any] = {}
_scheduled_jobs: dict[str, dict[str, Any]] = {}
_response_adapters: dict[str, Handler] = {}
_route_guards: dict[str, Handler] = {}
_execution_adapters: dict[str, Handler] = {}
_startup_hooks: list[Handler] = []
_agent_tools: dict[str, Any] = {}
_agent_planner_contexts: dict[str, Handler] = {}
_agent_planner_backends: dict[str, Handler] = {}
_agent_run_tools: dict[str, Handler] = {}
_agent_completion_hooks: dict[str, list[Handler]] = defaultdict(list)
_agent_event_emitters: dict[str, list[Handler]] = defaultdict(list)
_agent_ranking_strategy: Handler | None = None
_trigger_evaluators: dict[str, Handler] = {}
_flow_strategies: dict[str, Handler] = {}
_capability_definitions: dict[str, dict[str, Any]] = {}
_capability_definition_providers: list[Handler] = []
_tool_capabilities: dict[str, list[str]] = {}
_agent_capabilities: dict[str, list[str]] = {}
_restricted_tools: set[str] = set()
_route_prefixes: dict[str, str] = {
    "flow": "flow",
    "memory": "flow",
    "nodus": "nodus",
    "platform": "job",
}
_required_flow_nodes: list[str] = []
_required_syscalls: list[str] = []
_symbols: dict[str, Any] = {}
_loaded_plugins: set[str] = set()
_registered_apps: list[str] = []
_bootstrap_dependencies: dict[str, list[str]] = {}
_loaded_extension_records: dict[str, dict[str, Any]] = {}
_bootstrap_registrations: dict[str, dict[str, Any]] = {}
_core_domains: list[str] = []
_degraded_domains: list[str] = []
_health_checks: dict[str, Callable[[], dict[str, Any]]] = {}
_PLUGIN_PROFILE_ENV_VARS: tuple[str, ...] = ("AINDY_BOOT_PROFILE", "AINDY_PLUGIN_PROFILE")
_PLUGIN_MANIFEST_ENV_VAR = "AINDY_PLUGIN_MANIFEST"
_RUNTIME_PLUGIN_MANIFEST_ENV_VAR = "AINDY_RUNTIME_PLUGIN_MANIFEST"
_APP_PLUGIN_MANIFEST_ENV_VAR = "AINDY_APP_PLUGIN_MANIFEST"
_active_plugin_profile: str | None = None
_active_plugin_profile_source: str | None = None
_runtime_agent_defaults_loaded = False
_runtime_callback_invocations: dict[str, dict[str, Any]] = {}
_in_process_extension_capability_audit: dict[str, dict[str, Any]] = {}
_bootstrap_extension_ctx: ContextVar[dict[str, Any] | None] = ContextVar(
    "_bootstrap_extension_ctx",
    default=None,
)

INPROC_CAP_PUBLISH_BOOTSTRAP_REGISTRATION = "bootstrap.publish_registration"
INPROC_CAP_REGISTER_ROUTER = "registry.register_router"
INPROC_CAP_REGISTER_ROOT_ROUTER = "registry.register_root_router"
INPROC_CAP_REGISTER_LEGACY_ROOT_ROUTER = "registry.register_legacy_root_router"
INPROC_CAP_REGISTER_JOB = "registry.register_job"
INPROC_CAP_REGISTER_FLOW = "registry.register_flow"
INPROC_CAP_REGISTER_FLOW_RESULT = "registry.register_flow_result"
INPROC_CAP_REGISTER_FLOW_PLAN = "registry.register_flow_plan"
INPROC_CAP_REGISTER_EVENT_HANDLER = "registry.register_event_handler"
INPROC_CAP_REGISTER_EVENT_TYPE = "registry.register_event_type"
INPROC_CAP_REGISTER_SCHEDULED_JOB = "registry.register_scheduled_job"
INPROC_CAP_REGISTER_RESPONSE_ADAPTER = "registry.register_response_adapter"
INPROC_CAP_REGISTER_ROUTE_GUARD = "registry.register_route_guard"
INPROC_CAP_REGISTER_STARTUP_HOOK = "registry.register_startup_hook"
INPROC_CAP_REGISTER_AGENT_TOOL = "registry.register_agent_tool"
INPROC_CAP_REGISTER_PLANNER_CONTEXT = "registry.register_planner_context"
INPROC_CAP_REGISTER_PLANNER_BACKEND = "registry.register_planner_backend"
INPROC_CAP_REGISTER_RUN_TOOL_PROVIDER = "registry.register_run_tool_provider"
INPROC_CAP_REGISTER_AGENT_COMPLETION_HOOK = "registry.register_agent_completion_hook"
INPROC_CAP_REGISTER_AGENT_EVENT = "registry.register_agent_event"
INPROC_CAP_REGISTER_TRIGGER_EVALUATOR = "registry.register_trigger_evaluator"
INPROC_CAP_REGISTER_CAPABILITY_DEFINITION = "registry.register_capability_definition"
INPROC_CAP_REGISTER_CAPABILITY_PROVIDER = "registry.register_capability_definition_provider"
INPROC_CAP_REGISTER_TOOL_CAPABILITIES = "registry.register_tool_capabilities"
INPROC_CAP_REGISTER_AGENT_CAPABILITIES = "registry.register_agent_capabilities"
INPROC_CAP_REGISTER_RESTRICTED_TOOL = "registry.register_restricted_tool"
INPROC_CAP_REGISTER_HEALTH_CHECK = "registry.register_health_check"
INPROC_CAP_PUBLISH_CORE_DOMAINS = "registry.publish_core_domains"
INPROC_CAP_REGISTER_SYSCALL = "registry.register_syscall"
INPROC_CAP_REGISTER_EXECUTION_ADAPTER = "registry.register_execution_adapter"
INPROC_CAP_REGISTER_MEMORY_POLICY = "registry.register_memory_policy"
INPROC_CAP_REGISTER_AGENT_RANKING_STRATEGY = "registry.register_agent_ranking_strategy"
INPROC_CAP_REGISTER_FLOW_STRATEGY = "registry.register_flow_strategy"
INPROC_CAP_REGISTER_SYMBOL = "registry.register_symbol"
INPROC_CAP_REGISTER_SYMBOLS = "registry.register_symbols"
INPROC_CAP_REGISTER_ROUTE_PREFIX = "registry.register_route_prefix"
INPROC_CAP_REGISTER_REQUIRED_FLOW_NODE = "registry.register_required_flow_node"
INPROC_CAP_REGISTER_REQUIRED_SYSCALL = "registry.register_required_syscall"

_ALL_INPROC_EXTENSION_CAPABILITIES = {
    INPROC_CAP_PUBLISH_BOOTSTRAP_REGISTRATION,
    INPROC_CAP_REGISTER_ROUTER,
    INPROC_CAP_REGISTER_ROOT_ROUTER,
    INPROC_CAP_REGISTER_LEGACY_ROOT_ROUTER,
    INPROC_CAP_REGISTER_JOB,
    INPROC_CAP_REGISTER_FLOW,
    INPROC_CAP_REGISTER_FLOW_RESULT,
    INPROC_CAP_REGISTER_FLOW_PLAN,
    INPROC_CAP_REGISTER_EVENT_HANDLER,
    INPROC_CAP_REGISTER_EVENT_TYPE,
    INPROC_CAP_REGISTER_SCHEDULED_JOB,
    INPROC_CAP_REGISTER_RESPONSE_ADAPTER,
    INPROC_CAP_REGISTER_ROUTE_GUARD,
    INPROC_CAP_REGISTER_STARTUP_HOOK,
    INPROC_CAP_REGISTER_AGENT_TOOL,
    INPROC_CAP_REGISTER_PLANNER_CONTEXT,
    INPROC_CAP_REGISTER_PLANNER_BACKEND,
    INPROC_CAP_REGISTER_RUN_TOOL_PROVIDER,
    INPROC_CAP_REGISTER_AGENT_COMPLETION_HOOK,
    INPROC_CAP_REGISTER_AGENT_EVENT,
    INPROC_CAP_REGISTER_TRIGGER_EVALUATOR,
    INPROC_CAP_REGISTER_CAPABILITY_DEFINITION,
    INPROC_CAP_REGISTER_CAPABILITY_PROVIDER,
    INPROC_CAP_REGISTER_TOOL_CAPABILITIES,
    INPROC_CAP_REGISTER_AGENT_CAPABILITIES,
    INPROC_CAP_REGISTER_RESTRICTED_TOOL,
    INPROC_CAP_REGISTER_HEALTH_CHECK,
    INPROC_CAP_PUBLISH_CORE_DOMAINS,
    INPROC_CAP_REGISTER_SYSCALL,
    INPROC_CAP_REGISTER_EXECUTION_ADAPTER,
    INPROC_CAP_REGISTER_MEMORY_POLICY,
    INPROC_CAP_REGISTER_AGENT_RANKING_STRATEGY,
    INPROC_CAP_REGISTER_FLOW_STRATEGY,
    INPROC_CAP_REGISTER_SYMBOL,
    INPROC_CAP_REGISTER_SYMBOLS,
    INPROC_CAP_REGISTER_ROUTE_PREFIX,
    INPROC_CAP_REGISTER_REQUIRED_FLOW_NODE,
    INPROC_CAP_REGISTER_REQUIRED_SYSCALL,
}
# First-party app bootstrap modules are Tier 1 trusted kernel code (EXTENSION_CAPABILITIES.md).
# They receive the full registration capability set — identical to runtime-built-in modules.
# The registration gate still runs for auditing; it just does not deny any capability.
_FIRST_PARTY_ALLOWED_INPROC_EXTENSION_CAPABILITIES = _ALL_INPROC_EXTENSION_CAPABILITIES


def _sanitized_extension_input(context: dict[str, Any] | None) -> dict[str, Any]:
    return sanitize_extension_context(context or {})


def _current_in_process_extension_capabilities(owner_class: str) -> list[str]:
    resolved_owner = str(owner_class or "").strip()
    if resolved_owner == OWNER_RUNTIME_BUILTIN:
        return sorted(_ALL_INPROC_EXTENSION_CAPABILITIES)
    if resolved_owner == OWNER_FIRST_PARTY_APP:
        return sorted(_FIRST_PARTY_ALLOWED_INPROC_EXTENSION_CAPABILITIES)
    return []


def _ensure_in_process_extension_audit_record() -> dict[str, Any] | None:
    current_extension = _bootstrap_extension_ctx.get() or {}
    module_name = str(current_extension.get("module_name") or "").strip()
    owner_class = str(current_extension.get("owner_class") or "").strip()
    if not module_name or owner_class not in {OWNER_RUNTIME_BUILTIN, OWNER_FIRST_PARTY_APP}:
        return None
    audit = _in_process_extension_capability_audit.get(module_name)
    if audit is None:
        audit = {
            "module_name": module_name,
            "owner_class": owner_class,
            "capability_boundary_mode": "in-process-bootstrap-capabilities",
            "allowed_capabilities": _current_in_process_extension_capabilities(owner_class),
            "used_capabilities": [],
            "denied_capabilities": [],
        }
        _in_process_extension_capability_audit[module_name] = audit
    return audit


def _require_in_process_extension_capability(capability: str) -> None:
    audit = _ensure_in_process_extension_audit_record()
    if audit is None:
        return
    used = set(audit.get("used_capabilities") or [])
    used.add(capability)
    audit["used_capabilities"] = sorted(used)
    allowed = set(audit.get("allowed_capabilities") or [])
    if capability in allowed:
        return
    denied = set(audit.get("denied_capabilities") or [])
    denied.add(capability)
    audit["denied_capabilities"] = sorted(denied)
    raise PermissionError(
        f"in-process bootstrap capability {capability!r} is not allowed for "
        f"{audit['owner_class']!r} module {audit['module_name']!r}"
    )


def get_in_process_extension_capability_audit() -> dict[str, dict[str, Any]]:
    return {key: dict(value) for key, value in _in_process_extension_capability_audit.items()}


def _runtime_callback_key(surface: str, identifier: str) -> str:
    return f"{surface}:{identifier}"


def _record_runtime_callback_invocation(spec: dict[str, Any], response: dict[str, Any]) -> None:
    _runtime_callback_invocations[_runtime_callback_key(spec["surface"], spec["identifier"])] = {
        "surface": spec["surface"],
        "identifier": spec["identifier"],
        "owner_class": spec["owner_class"],
        "module_name": spec["module_name"],
        "function_name": spec["function_name"],
        "execution_mode": response.get("execution_mode") or RUNTIME_CALLBACK_EXECUTION_MODE,
        "worker_pid": response.get("worker_pid"),
        "protocol_version": response.get("protocol_version"),
    }


def get_runtime_callback_invocations() -> dict[str, dict[str, Any]]:
    return {key: dict(value) for key, value in _runtime_callback_invocations.items()}


def _callback_owner_class(handler: Handler) -> str:
    current_extension = _bootstrap_extension_ctx.get() or {}
    owner_class = str(current_extension.get("owner_class") or "").strip()
    if owner_class:
        return owner_class
    module_name = str(getattr(handler, "__module__", "") or "").strip()
    if module_name:
        return infer_bootstrap_owner_class(module_name)
    return OWNER_EXTERNAL_THIRD_PARTY


def _callback_is_module_resolvable(handler: Handler) -> bool:
    module_name = str(getattr(handler, "__module__", "") or "").strip()
    function_name = str(getattr(handler, "__name__", "") or "").strip()
    if not module_name or not function_name or function_name == "<lambda>":
        return False
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return False
    return getattr(module, function_name, None) is handler


def _callback_source_path(handler: Handler) -> str | None:
    module_name = str(getattr(handler, "__module__", "") or "").strip()
    function_name = str(getattr(handler, "__name__", "") or "").strip()
    if not module_name or not function_name or function_name == "<lambda>":
        return None
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return None
    if getattr(module, function_name, None) is not handler:
        return None
    module_origin = str(getattr(module, "__file__", "") or "").strip()
    return module_origin or None


# Surfaces whose handlers read live in-process registration state (the agent
# tool registry, the planner context) populated during app bootstrap. An
# isolated subprocess starts from a bare interpreter and would have to re-run the
# full app bootstrap to reconstruct that state — prohibitively slow and unreliable
# across cwd/manifest resolution (the subprocess cwd is the read-only site-packages
# dir, so load_plugins() finds no app manifest and returns zero tools). These
# surfaces therefore run in-process. Self-contained surfaces (startup hooks,
# capability providers, trigger evaluators) keep subprocess isolation. See
# PLANNER-SUBPROC-1.
_STATEFUL_IN_PROCESS_CALLBACK_SURFACES: frozenset[str] = frozenset(
    {"run_tool_provider", "planner_context"}
)


def _runtime_callback_spec(
    *,
    surface: str,
    identifier: str,
    handler: Handler,
    expects_argument: bool,
) -> dict[str, Any] | None:
    if surface in _STATEFUL_IN_PROCESS_CALLBACK_SURFACES:
        return None
    module_name = str(getattr(handler, "__module__", "") or "").strip()
    function_name = str(getattr(handler, "__name__", "") or "").strip()
    owner_class = _callback_owner_class(handler)
    if owner_class not in {OWNER_RUNTIME_BUILTIN, OWNER_FIRST_PARTY_APP}:
        return None
    if owner_class == OWNER_RUNTIME_BUILTIN and not module_name.startswith("AINDY."):
        return None
    if owner_class == OWNER_FIRST_PARTY_APP and not module_name.startswith("apps."):
        return None
    if not _callback_is_module_resolvable(handler):
        return None
    return build_runtime_callback_spec(
        surface=surface,
        identifier=identifier,
        owner_class=owner_class,
        module_name=module_name,
        function_name=function_name,
        source_path=_callback_source_path(handler),
        expects_argument=expects_argument,
        bootstrap_register=module_name == "AINDY.platform_layer.runtime_agent_defaults",
    )


def _maybe_wrap_runtime_callback(
    *,
    surface: str,
    identifier: str,
    handler: Handler,
    expects_argument: bool,
) -> Handler:
    spec = _runtime_callback_spec(
        surface=surface,
        identifier=identifier,
        handler=handler,
        expects_argument=expects_argument,
    )
    if spec is None:
        return handler

    if expects_argument:
        @wraps(handler)
        def _wrapped(payload: dict[str, Any] | None = None):
            response = invoke_runtime_callback(spec, argument=_sanitized_extension_input(payload or {}))
            _record_runtime_callback_invocation(spec, response)
            return response.get("result")

        setattr(_wrapped, "__aindy_runtime_callback_spec__", dict(spec))
        return _wrapped

    @wraps(handler)
    def _wrapped_zero_arg():
        response = invoke_runtime_callback(spec, argument=None)
        _record_runtime_callback_invocation(spec, response)
        return response.get("result")

    setattr(_wrapped_zero_arg, "__aindy_runtime_callback_spec__", dict(spec))
    return _wrapped_zero_arg


def register_router(router: Any, *, root: bool = False, legacy_root: bool = False) -> Any:
    _require_in_process_extension_capability(
        INPROC_CAP_REGISTER_LEGACY_ROOT_ROUTER
        if legacy_root
        else (INPROC_CAP_REGISTER_ROOT_ROUTER if root else INPROC_CAP_REGISTER_ROUTER)
    )
    validate_router(router)
    target = _routers
    if legacy_root:
        target = _legacy_root_routers
    elif root:
        target = _root_routers
    if router not in target:
        target.append(router)
    return router


def get_routers() -> list[Any]:
    return list(_routers)


def get_root_routers() -> list[Any]:
    return list(_root_routers)


def get_legacy_root_routers() -> list[Any]:
    return list(_legacy_root_routers)


def register_syscall(name: str, handler: Handler) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_SYSCALL)
    validate_syscall_handler(name, handler)
    _syscalls[name] = handler
    return handler


def get_syscall(name: str) -> Handler | None:
    return _syscalls.get(name)


def iter_syscalls() -> Iterable[tuple[str, Handler]]:
    return tuple(_syscalls.items())


def register_job(name: str, handler: Handler) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_JOB)
    validate_job_handler(name, handler)
    _jobs[name] = handler
    return handler


def get_job(name: str) -> Handler | None:
    return _jobs.get(name)


def iter_jobs() -> Iterable[tuple[str, Handler]]:
    return tuple(_jobs.items())


def register_flow(register_fn: Handler) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_FLOW)
    validate_flow_registration(getattr(register_fn, "__name__", "<anonymous>"), register_fn)
    if register_fn in _flows:
        return register_fn
    _flows.append(register_fn)
    return register_fn


def register_flows() -> None:
    for register_fn in tuple(_flows):
        register_fn()


def register_flow_result(
    flow_name: str,
    *,
    result_key: str | None = None,
    extractor: Handler | None = None,
    completion_event: str | None = None,
) -> None:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_FLOW_RESULT)
    validate_flow_result_registration(
        flow_name,
        result_key=result_key,
        extractor=extractor,
        completion_event=completion_event,
    )
    if result_key is not None:
        _flow_result_keys[flow_name] = result_key
    if extractor is not None:
        _flow_result_extractors[flow_name] = extractor
    if completion_event is not None:
        _flow_completion_events[flow_name] = completion_event


def get_flow_result_key(flow_name: str) -> str | None:
    return _flow_result_keys.get(flow_name)


def get_flow_result_extractor(flow_name: str) -> Handler | None:
    return _flow_result_extractors.get(flow_name)


def get_flow_completion_event(flow_name: str) -> str | None:
    return _flow_completion_events.get(flow_name)


def register_flow_plan(flow_name: str, plan: dict[str, Any]) -> None:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_FLOW_PLAN)
    validate_flow_plan(flow_name, plan)
    _flow_plans[flow_name] = plan


def get_flow_plan(flow_name: str) -> dict[str, Any] | None:
    return _flow_plans.get(flow_name)


def register_event_handler(event_type: str, handler: Handler) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_EVENT_HANDLER)
    validate_event_handler(event_type, handler)
    register_event_type(event_type)
    _event_handlers[event_type].append(handler)
    return handler


def get_event_handlers(event_type: str) -> list[Handler]:
    return list(_event_handlers.get(event_type, ()))


def register_event_type(event_type: str) -> str:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_EVENT_TYPE)
    if not event_type or not event_type.strip():
        raise ValueError("event_type must be a non-empty string")
    _event_types.add(event_type)
    return event_type


def get_event_types() -> set[str]:
    return set(_event_types)


def emit_event(event_type: str, context: dict[str, Any] | None = None) -> list[Any]:
    """Dispatch a generic registry event to app-registered handlers."""
    load_plugins()
    payload = _sanitized_extension_input(context)
    results: list[Any] = []
    handlers = tuple(_event_handlers.get(event_type, ())) + tuple(_event_handlers.get("*", ()))
    for handler in handlers:
        results.append(handler(payload))
    return results


def register_scheduled_job(
    job_id: str,
    handler: Handler,
    *,
    name: str | None = None,
    trigger: str = "interval",
    trigger_kwargs: dict[str, Any] | None = None,
    replace_existing: bool = True,
) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_SCHEDULED_JOB)
    validate_scheduled_job_entry(
        job_id,
        handler=handler,
        trigger=trigger,
        trigger_kwargs=trigger_kwargs,
    )
    _scheduled_jobs[job_id] = {
        "id": job_id,
        "handler": handler,
        "name": name or job_id,
        "trigger": trigger,
        "trigger_kwargs": dict(trigger_kwargs or {}),
        "replace_existing": replace_existing,
    }
    return handler


def get_scheduled_jobs() -> tuple[dict[str, Any], ...]:
    load_plugins()
    return tuple(dict(job) for job in _scheduled_jobs.values())


def register_response_adapter(route_prefix: str, handler: Handler) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_RESPONSE_ADAPTER)
    validate_response_adapter(route_prefix, handler)
    _response_adapters[route_prefix.rstrip(".")] = handler
    return handler


def get_response_adapter(route_prefix: str) -> Handler | None:
    load_plugins()
    return _response_adapters.get(route_prefix.rstrip("."))


def register_route_guard(route_prefix: str, handler: Handler) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_ROUTE_GUARD)
    validate_route_guard(route_prefix, handler)
    _route_guards[route_prefix.rstrip(".")] = handler
    return handler


def get_route_guard(route_prefix: str) -> Handler | None:
    load_plugins()
    return _route_guards.get(route_prefix.rstrip("."))


def register_execution_adapter(entity_type: str, handler: Handler) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_EXECUTION_ADAPTER)
    validate_execution_adapter(entity_type, handler)
    _execution_adapters[entity_type.strip()] = handler
    return handler


def get_execution_adapter(entity_type: str) -> Handler | None:
    load_plugins()
    return _execution_adapters.get(entity_type.strip())


def register_startup_hook(handler: Handler) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_STARTUP_HOOK)
    validate_startup_hook(handler)
    _startup_hooks.append(
        _maybe_wrap_runtime_callback(
            surface="startup_hook",
            identifier=str(getattr(handler, "__name__", "startup_hook")),
            handler=handler,
            expects_argument=True,
        )
    )
    return handler


def run_startup_hooks(context: dict[str, Any] | None = None) -> list[Any]:
    load_plugins()
    payload = _sanitized_extension_input(context)
    results: list[Any] = []
    for handler in tuple(_startup_hooks):
        results.append(handler(payload))
    return results


def register_capture_rule(event_type: str, rule: Any) -> Any:
    return register_memory_policy(event_type, rule)


def get_capture_rule(event_type: str) -> Any | None:
    return get_memory_policy(event_type)


def get_capture_rules() -> dict[str, Any]:
    load_plugins()
    return dict(_capture_rules)


def register_memory_policy(event_type: str, policy: Any) -> Any:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_MEMORY_POLICY)
    validate_memory_policy(event_type, policy)
    _memory_policies[event_type] = policy
    _capture_rules[event_type] = policy
    try:
        from AINDY.memory import memory_capture_engine

        if isinstance(policy, dict):
            memory_capture_engine.EVENT_SIGNIFICANCE[event_type] = policy.get(
                "base_score",
                policy.get("significance", 0.4),
            )
    except Exception:
        logger.debug("memory policy compatibility update skipped", exc_info=True)
    return policy


def get_memory_policy(event_type: str) -> Any | None:
    load_plugins()
    return _memory_policies.get(event_type)


def get_memory_significance_rule(event_type: str) -> float | None:
    policy = get_memory_policy(event_type)
    if not isinstance(policy, dict):
        return None
    value = policy.get("base_score", policy.get("significance"))
    return float(value) if value is not None else None


def register_agent_tool(name: str, tool: Any) -> Any:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_AGENT_TOOL)
    validate_agent_tool(name, tool)
    _agent_tools[name] = tool
    return tool


def get_agent_tool(name: str) -> Any | None:
    _ensure_runtime_agent_defaults()
    return _agent_tools.get(name)


def iter_agent_tools() -> Iterable[tuple[str, Any]]:
    _ensure_runtime_agent_defaults()
    return tuple(_agent_tools.items())


def register_planner_context_provider(run_type: str, handler: Handler) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_PLANNER_CONTEXT)
    validate_agent_planner_context(run_type, handler)
    _agent_planner_contexts[run_type] = _maybe_wrap_runtime_callback(
        surface="planner_context",
        identifier=run_type,
        handler=handler,
        expects_argument=True,
    )
    return handler


def register_agent_planner_backend(name: str, handler: Handler) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_PLANNER_BACKEND)
    validate_agent_planner_backend(name, handler)
    _agent_planner_backends[name] = handler
    return handler


def get_agent_planner_backend(name: str) -> Handler | None:
    _ensure_runtime_agent_defaults()
    return _agent_planner_backends.get(name)


def list_agent_planner_backends() -> list[str]:
    _ensure_runtime_agent_defaults()
    return sorted(_agent_planner_backends)


def get_planner_context(run_type: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_runtime_agent_defaults()
    handler = _agent_planner_contexts.get(run_type) or _agent_planner_contexts.get("default")
    if handler is None:
        return {}
    value = handler(_sanitized_extension_input(context))
    return value if isinstance(value, dict) else {}


def register_agent_planner_context(run_type: str, handler: Handler) -> Handler:
    return register_planner_context_provider(run_type, handler)


def register_run_tool_provider(run_type: str, handler: Handler) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_RUN_TOOL_PROVIDER)
    validate_agent_run_tools(run_type, handler)
    _agent_run_tools[run_type] = _maybe_wrap_runtime_callback(
        surface="run_tool_provider",
        identifier=run_type,
        handler=handler,
        expects_argument=True,
    )
    return handler


def get_tools_for_run(run_type: str, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    _ensure_runtime_agent_defaults()
    handler = _agent_run_tools.get(run_type) or _agent_run_tools.get("default")
    if handler is None:
        return []
    value = handler(_sanitized_extension_input(context))
    return value if isinstance(value, list) else []


def register_agent_run_tools(run_type: str, handler: Handler) -> Handler:
    return register_run_tool_provider(run_type, handler)


def register_agent_completion_hook(run_type: str, handler: Handler) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_AGENT_COMPLETION_HOOK)
    validate_agent_event(f"agent.completion.{run_type}", handler)
    _agent_completion_hooks[run_type].append(
        _maybe_wrap_runtime_callback(
            surface="agent_completion_hook",
            identifier=run_type,
            handler=handler,
            expects_argument=True,
        )
    )
    return handler


def run_agent_completion_hooks(run_type: str, context: dict[str, Any]) -> list[Any]:
    _ensure_runtime_agent_defaults()
    results: list[Any] = []
    payload = _sanitized_extension_input(context)
    handlers = tuple(_agent_completion_hooks.get(run_type, ())) + tuple(
        _agent_completion_hooks.get("default", ()) if run_type != "default" else ()
    )
    for handler in handlers:
        results.append(handler(payload))
    return results


def register_agent_event(event_name: str, handler: Handler) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_AGENT_EVENT)
    validate_agent_event(event_name, handler)
    _agent_event_emitters[event_name].append(handler)
    return handler


def emit_agent_event(event_name: str, context: dict[str, Any]) -> list[Any]:
    results: list[Any] = []
    payload = _sanitized_extension_input(context)
    for handler in tuple(_agent_event_emitters.get(event_name, ())):
        results.append(handler(payload))
    return results


def register_agent_ranking_strategy(handler: Handler) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_AGENT_RANKING_STRATEGY)
    validate_agent_ranking_strategy(handler)
    global _agent_ranking_strategy
    _agent_ranking_strategy = handler
    return handler


def get_agent_ranking_strategy() -> Handler | None:
    load_plugins()
    return _agent_ranking_strategy


def register_trigger_evaluator(trigger_type: str, handler: Handler) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_TRIGGER_EVALUATOR)
    validate_trigger_evaluator(trigger_type, handler)
    _trigger_evaluators[trigger_type] = _maybe_wrap_runtime_callback(
        surface="trigger_evaluator",
        identifier=trigger_type,
        handler=handler,
        expects_argument=True,
    )
    return handler


def get_trigger_evaluator(trigger_type: str) -> Handler | None:
    _ensure_runtime_agent_defaults()
    load_plugins()
    return _trigger_evaluators.get(trigger_type) or _trigger_evaluators.get("default")


def register_flow_strategy(flow_type: str, handler: Handler) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_FLOW_STRATEGY)
    validate_flow_strategy(flow_type, handler)
    _flow_strategies[flow_type] = handler
    return handler


def get_flow_strategy(flow_type: str) -> Handler | None:
    load_plugins()
    return _flow_strategies.get(flow_type) or _flow_strategies.get("default")


def get_all_flow_strategies() -> dict[str, Handler]:
    load_plugins()
    return dict(_flow_strategies)


def register_capability_definition(name: str, metadata: dict[str, Any]) -> dict[str, Any]:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_CAPABILITY_DEFINITION)
    validate_capability_definition(name, metadata)
    _capability_definitions[name] = dict(metadata)
    return _capability_definitions[name]


def register_capability_definition_provider(handler: Handler) -> Handler:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_CAPABILITY_PROVIDER)
    if not callable(handler):
        raise ValueError("capability definition provider must be callable")
    wrapped = _maybe_wrap_runtime_callback(
        surface="capability_definition_provider",
        identifier=str(getattr(handler, "__name__", "capability_definition_provider")),
        handler=handler,
        expects_argument=False,
    )
    for existing in tuple(_capability_definition_providers):
        if existing is wrapped or getattr(existing, "__wrapped__", None) is handler or existing is handler:
            return handler
    _capability_definition_providers.append(wrapped)
    return handler


def _apply_capability_provider_bundle(bundle: CapabilityProviderBundle | dict[str, Any]) -> None:
    if not isinstance(bundle, dict):
        raise ValueError("capability provider must return a dict bundle")

    for name, metadata in (bundle.get("definitions") or {}).items():
        register_capability_definition(name, metadata)
    for tool_name, capabilities in (bundle.get("tool_capabilities") or {}).items():
        register_tool_capabilities(tool_name, capabilities)
    for agent_id, capabilities in (bundle.get("agent_capabilities") or {}).items():
        register_agent_capabilities(agent_id, capabilities)
    for tool_name in (bundle.get("restricted_tools") or []):
        register_restricted_tool(tool_name)


def _load_capability_definition_providers() -> None:
    _ensure_runtime_agent_defaults()
    load_plugins()
    for provider in tuple(_capability_definition_providers):
        try:
            _apply_capability_provider_bundle(provider())
        except Exception as exc:
            logger.warning("Capability definition provider failed: %s", exc)


def _ensure_runtime_agent_defaults() -> None:
    global _runtime_agent_defaults_loaded
    if _runtime_agent_defaults_loaded:
        return
    from AINDY.platform_layer import runtime_agent_defaults

    runtime_agent_defaults.register()
    _runtime_agent_defaults_loaded = True


def get_capability_definition(name: str) -> dict[str, Any] | None:
    _load_capability_definition_providers()
    return _capability_definitions.get(name)


def get_capability_definitions() -> dict[str, dict[str, Any]]:
    _load_capability_definition_providers()
    return {name: dict(metadata) for name, metadata in _capability_definitions.items()}


def register_tool_capabilities(tool_name: str, capability_names: list[str]) -> list[str]:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_TOOL_CAPABILITIES)
    validate_capability_names("Tool capabilities", tool_name, capability_names)
    capabilities = sorted({name for name in capability_names if isinstance(name, str)})
    _tool_capabilities[tool_name] = capabilities
    return capabilities


def get_capabilities_for_tool(tool_name: str) -> list[str]:
    _load_capability_definition_providers()
    return list(_tool_capabilities.get(tool_name, ()))


def register_agent_capabilities(agent_id: str, capability_names: list[str]) -> list[str]:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_AGENT_CAPABILITIES)
    validate_capability_names("Agent capabilities", agent_id, capability_names)
    capabilities = sorted({name for name in capability_names if isinstance(name, str)})
    _agent_capabilities[agent_id] = capabilities
    return capabilities


def get_capabilities_for_agent(agent_id: str) -> list[str]:
    _load_capability_definition_providers()
    return list(_agent_capabilities.get(agent_id, ()))


def register_restricted_tool(tool_name: str) -> str:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_RESTRICTED_TOOL)
    validate_restricted_tool(tool_name)
    _restricted_tools.add(tool_name)
    return tool_name


def get_restricted_tools() -> set[str]:
    _load_capability_definition_providers()
    return set(_restricted_tools)


def register_route_prefix(prefix: str, execution_unit_type: str) -> None:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_ROUTE_PREFIX)
    validate_route_prefix(prefix, execution_unit_type)
    _route_prefixes[prefix] = execution_unit_type


def get_route_prefix(prefix: str) -> str | None:
    return _route_prefixes.get(prefix)


def register_required_flow_node(node_name: str) -> str:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_REQUIRED_FLOW_NODE)
    """Register a flow node name that must exist after bootstrap."""
    if not node_name or not isinstance(node_name, str):
        raise ValueError(f"node_name must be a non-empty string, got {node_name!r}")
    _required_flow_nodes.append(node_name)
    return node_name


def get_required_flow_nodes() -> list[str]:
    return list(_required_flow_nodes)


def register_required_syscall(name: str) -> None:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_REQUIRED_SYSCALL)
    """Declare that a syscall must be present after bootstrap."""
    if not name or not isinstance(name, str):
        raise ValueError(f"name must be a non-empty string, got {name!r}")
    if name not in _required_syscalls:
        _required_syscalls.append(name)


def get_required_syscalls() -> list[str]:
    return list(_required_syscalls)


def register_symbol(name: str, value: Any) -> Any:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_SYMBOL)
    validate_symbol(name)
    _symbols[name] = value
    return value


def get_symbol(name: str) -> Any | None:
    return _symbols.get(name)


def register_symbols(symbols: dict[str, Any]) -> None:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_SYMBOLS)
    validate_symbols(symbols)
    for name, value in symbols.items():
        if not name.startswith("__"):
            register_symbol(name, value)


def publish_degraded_domains(domains: Iterable[str]) -> list[str]:
    published: list[str] = []
    seen: set[str] = set()
    for domain in domains:
        if not isinstance(domain, str):
            continue
        normalized = domain.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        published.append(normalized)

    global _degraded_domains
    _degraded_domains = published
    return list(_degraded_domains)


def get_degraded_domains() -> list[str]:
    return list(_degraded_domains)


def register_health_check(app_name: str, check_fn: Callable[[], dict[str, Any]]) -> Callable[[], dict[str, Any]]:
    _require_in_process_extension_capability(INPROC_CAP_REGISTER_HEALTH_CHECK)
    if not isinstance(app_name, str) or not app_name.strip():
        raise ValueError("app_name must be a non-empty string")
    if not callable(check_fn):
        raise ValueError("check_fn must be callable")
    _health_checks[app_name.strip()] = check_fn
    return check_fn


def get_all_health_checks() -> dict[str, Callable[[], dict[str, Any]]]:
    return dict(_health_checks)


def publish_bootstrap_registration(
    app_name: str,
    dependencies: list[str] | None = None,
    *,
    owner_class: str | None = None,
    module_name: str | None = None,
) -> str:
    _require_in_process_extension_capability(INPROC_CAP_PUBLISH_BOOTSTRAP_REGISTRATION)
    normalized = str(app_name or "").strip()
    if not normalized:
        raise ValueError("app_name must be a non-empty string")
    current_extension = _bootstrap_extension_ctx.get() or {}
    resolved_module_name = str(module_name or current_extension.get("module_name") or "").strip() or None
    resolved_owner_class = str(
        owner_class
        or current_extension.get("owner_class")
        or infer_bootstrap_owner_class(resolved_module_name or normalized)
    ).strip()
    trust_class = str(
        current_extension.get("trust_class")
        or python_extension_trust_class(resolved_owner_class)
    ).strip()
    execution_metadata = python_extension_execution_metadata(resolved_owner_class)
    if resolved_owner_class == OWNER_FIRST_PARTY_APP and normalized not in _registered_apps:
        _registered_apps.append(normalized)
    _bootstrap_dependencies[normalized] = [
        str(dependency).strip()
        for dependency in (dependencies or [])
        if str(dependency).strip()
    ]
    _bootstrap_registrations[normalized] = {
        "name": normalized,
        "abi_surface": current_extension.get("abi_surface") or SURFACE_MANIFEST,
        "abi_version": current_extension.get("abi_version") or LEGACY_UNVERSIONED_MANIFEST,
        "abi_stability": current_extension.get("abi_stability")
        or extension_surface_stability(SURFACE_MANIFEST),
        "owner_class": resolved_owner_class,
        "trust_class": trust_class,
        "execution_model": execution_metadata["execution_model"],
        "sandboxing": execution_metadata["sandboxing"],
        "trusted_override_active": execution_metadata["trusted_override_active"],
        "execution_surface": "manifest-bootstrap",
        "module_name": resolved_module_name,
        "module_origin": current_extension.get("module_origin"),
        "manifest_owner": current_extension.get("manifest_owner"),
        "profile_name": current_extension.get("profile_name"),
        "dependencies": list(_bootstrap_dependencies[normalized]),
        "provenance": dict(current_extension.get("provenance") or {}),
        "capability_boundary_mode": (
            "in-process-bootstrap-capabilities"
            if resolved_owner_class in {OWNER_RUNTIME_BUILTIN, OWNER_FIRST_PARTY_APP}
            else "not-applicable"
        ),
        "allowed_in_process_capabilities": list(
            (_ensure_in_process_extension_audit_record() or {}).get("allowed_capabilities") or []
        ),
        "used_in_process_capabilities": list(
            (_ensure_in_process_extension_audit_record() or {}).get("used_capabilities") or []
        ),
        "denied_in_process_capabilities": list(
            (_ensure_in_process_extension_audit_record() or {}).get("denied_capabilities") or []
        ),
    }
    return normalized


def publish_core_domains(domains: Iterable[str]) -> list[str]:
    _require_in_process_extension_capability(INPROC_CAP_PUBLISH_CORE_DOMAINS)
    published = sorted(
        {
            str(domain).strip()
            for domain in domains
            if isinstance(domain, str) and str(domain).strip()
        }
    )
    global _core_domains
    _core_domains = published
    return list(_core_domains)


def get_registered_apps() -> list[str]:
    return list(_registered_apps)


def get_loaded_extensions() -> list[dict[str, Any]]:
    return [dict(_loaded_extension_records[key]) for key in sorted(_loaded_extension_records)]


def get_bootstrap_registrations() -> dict[str, dict[str, Any]]:
    return {name: dict(metadata) for name, metadata in _bootstrap_registrations.items()}


def get_bootstrap_dependencies() -> dict[str, list[str]]:
    return {name: list(deps) for name, deps in _bootstrap_dependencies.items()}


def get_core_domains() -> list[str]:
    load_plugins()
    return list(_core_domains)


def _default_runtime_manifest_path() -> Path:
    return Path(__file__).resolve().parents[1] / "runtime_plugins.json"


def _source_checkout_app_manifest_path() -> Path:
    return Path(__file__).resolve().parents[2] / "aindy_plugins.json"


def _find_manifest_upwards(start: Path, manifest_name: str) -> Path | None:
    for candidate_root in (start, *start.parents):
        candidate = candidate_root / manifest_name
        if candidate.exists():
            return candidate
    return None


def _default_app_manifest_path() -> Path:
    cwd_manifest = _find_manifest_upwards(Path.cwd(), "aindy_plugins.json")
    if cwd_manifest is not None:
        return cwd_manifest
    return _source_checkout_app_manifest_path()


def _get_manifest_path_from_env(env_name: str) -> Path | None:
    value = os.getenv(env_name, "").strip()
    if not value:
        return None
    return Path(value)


def _resolve_manifest_path(
    manifest_path: str | Path | None = None,
    *,
    profile: str | None = None,
) -> tuple[Path, str, str]:
    if manifest_path is not None:
        return Path(manifest_path), "argument", "explicit"

    explicit_manifest = _get_manifest_path_from_env(_PLUGIN_MANIFEST_ENV_VAR)
    if explicit_manifest is not None:
        return explicit_manifest, _PLUGIN_MANIFEST_ENV_VAR, "explicit"

    requested_profile, _requested_profile_source = _resolve_requested_plugin_profile(profile)
    if requested_profile == "platform-only":
        runtime_manifest = _get_manifest_path_from_env(_RUNTIME_PLUGIN_MANIFEST_ENV_VAR)
        if runtime_manifest is not None:
            return runtime_manifest, _RUNTIME_PLUGIN_MANIFEST_ENV_VAR, "runtime"
        return _default_runtime_manifest_path(), "default-runtime-manifest", "runtime"

    if requested_profile:
        app_manifest = _get_manifest_path_from_env(_APP_PLUGIN_MANIFEST_ENV_VAR)
        if app_manifest is not None:
            return app_manifest, _APP_PLUGIN_MANIFEST_ENV_VAR, "apps"
        return _default_app_manifest_path(), "default-app-manifest", "apps"

    app_manifest = _get_manifest_path_from_env(_APP_PLUGIN_MANIFEST_ENV_VAR)
    if app_manifest is not None:
        return app_manifest, _APP_PLUGIN_MANIFEST_ENV_VAR, "apps"

    default_app_manifest = _default_app_manifest_path()
    if default_app_manifest.exists():
        return default_app_manifest, "default-app-manifest", "apps"

    runtime_manifest = _get_manifest_path_from_env(_RUNTIME_PLUGIN_MANIFEST_ENV_VAR)
    if runtime_manifest is not None:
        return runtime_manifest, _RUNTIME_PLUGIN_MANIFEST_ENV_VAR, "runtime"

    return _default_runtime_manifest_path(), "default-runtime-manifest", "runtime"


def _read_plugin_manifest(manifest_path: str | Path | None = None) -> tuple[Path, dict[str, Any] | None]:
    path, _source, _owner = _resolve_manifest_path(manifest_path)
    if not path.exists():
        return path, None
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_extension_manifest_document(data, path=path)
    return path, data


def _normalize_plugin_profile_plugins(
    plugins: Any,
    *,
    profile_name: str,
    path: Path,
    manifest_owner: str,
) -> list[dict[str, str]]:
    if plugins is None:
        return []
    if not isinstance(plugins, list):
        raise ValueError(
            f"Plugin profile {profile_name!r} in {path} must declare a list of plugins"
        )

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for plugin_entry in plugins:
        if isinstance(plugin_entry, str):
            cleaned = plugin_entry.strip()
            owner_class = infer_bootstrap_owner_class(cleaned)
            declared_provenance = None
        elif isinstance(plugin_entry, dict):
            module_value = plugin_entry.get("module")
            if not isinstance(module_value, str):
                raise ValueError(
                    f"Plugin profile {profile_name!r} in {path} contains a plugin entry without string 'module'"
                )
            cleaned = module_value.strip()
            owner_class = str(plugin_entry.get("owner_class") or infer_bootstrap_owner_class(cleaned)).strip()
            declared_provenance = (
                dict(plugin_entry.get("provenance"))
                if isinstance(plugin_entry.get("provenance"), dict)
                else None
            )
        else:
            raise ValueError(
                f"Plugin profile {profile_name!r} in {path} contains an invalid plugin entry {plugin_entry!r}"
            )
        if not cleaned or cleaned in seen:
            continue
        resolved_owner_class = validate_bootstrap_module_name(
            cleaned,
            owner_class=owner_class,
            manifest_owner=manifest_owner,
        )
        seen.add(cleaned)
        normalized_entry = {
            "module_name": cleaned,
            "owner_class": resolved_owner_class,
        }
        if declared_provenance is not None:
            normalized_entry["provenance"] = declared_provenance
        normalized.append(normalized_entry)
    return normalized


def _normalize_plugin_profile_extensions(
    extensions: Any,
    *,
    profile_name: str,
    path: Path,
) -> list[dict[str, Any]]:
    if extensions is None:
        return []
    if not isinstance(extensions, list):
        raise ValueError(
            f"Plugin profile {profile_name!r} in {path} must declare a list of extensions"
        )

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for extension_entry in extensions:
        if not isinstance(extension_entry, dict):
            raise ValueError(
                f"Plugin profile {profile_name!r} in {path} contains an invalid extension entry {extension_entry!r}"
            )
        kind = str(extension_entry.get("kind") or "").strip()
        if kind == "dynamic-node":
            identifier = f"dynamic-node:{str(extension_entry.get('name') or '').strip()}"
        elif kind == "webhook-subscription":
            identifier = (
                f"webhook-subscription:{str(extension_entry.get('event_type') or '').strip()}:"
                f"{str(extension_entry.get('callback_url') or '').strip()}"
            )
        elif kind == "dynamic-flow":
            identifier = f"dynamic-flow:{str(extension_entry.get('name') or '').strip()}"
        else:
            raise ValueError(
                f"Plugin profile {profile_name!r} in {path} contains unsupported declarative extension kind {kind!r}"
            )
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        normalized.append(dict(extension_entry))
    return normalized


def _resolve_requested_plugin_profile(profile: str | None = None) -> tuple[str | None, str | None]:
    if isinstance(profile, str) and profile.strip():
        return profile.strip(), "argument"
    for env_name in _PLUGIN_PROFILE_ENV_VARS:
        value = os.getenv(env_name, "").strip()
        if value:
            return value, env_name
    boot_mode = get_requested_boot_mode()
    if boot_mode:
        return resolve_profile_for_boot_mode(boot_mode), BOOT_MODE_ENV_VAR
    return None, None


def _plugin_boot_failure(
    *,
    path: Path,
    profile_name: str,
    module_name: str | None = None,
    reason: str,
) -> RuntimeError:
    module_detail = f" plugin module {module_name!r}" if module_name else ""
    return RuntimeError(
        f"Failed to boot plugin profile {profile_name!r} from {path}:{module_detail} {reason}. "
        "If you intend to start the runtime without app plugins, explicitly select "
        f"the zero-plugin profile (for example `{BOOT_MODE_ENV_VAR}=runtime-only` "
        "or `AINDY_BOOT_PROFILE=platform-only`)."
    )


def _resolve_plugin_profile_selection(
    manifest_path: str | Path | None = None,
    *,
    profile: str | None = None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], bool, str]:
    path, manifest_source, manifest_owner = _resolve_manifest_path(
        manifest_path,
        profile=profile,
    )
    _, data = _read_plugin_manifest(path)
    if data is None:
        requested_profile, requested_profile_source = _resolve_requested_plugin_profile(profile)
        explicit_manifest_override = manifest_source in {
            "argument",
            _PLUGIN_MANIFEST_ENV_VAR,
            _RUNTIME_PLUGIN_MANIFEST_ENV_VAR,
            _APP_PLUGIN_MANIFEST_ENV_VAR,
        }
        if requested_profile or explicit_manifest_override:
            profile_name = requested_profile or (
                "platform-only" if manifest_owner == "runtime" else "default-apps"
            )
            reason = f"manifest {path} was not found"
            raise _plugin_boot_failure(
                path=path,
                profile_name=profile_name,
                reason=reason,
            )
        return "missing", [], [], False, "missing-manifest"

    requested_profile, requested_profile_source = _resolve_requested_plugin_profile(profile)
    effective_abi_version = manifest_effective_abi_version(data)
    legacy_plugins = data.get("plugins")
    if effective_abi_version == LEGACY_UNVERSIONED_MANIFEST and isinstance(legacy_plugins, list):
        return "__legacy__", _normalize_plugin_profile_plugins(
            legacy_plugins,
            profile_name="__legacy__",
            path=path,
            manifest_owner=manifest_owner,
        ), [], False, "legacy-manifest"

    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError(f"Plugin manifest at {path} must declare non-empty 'profiles'")

    if requested_profile:
        selected_profile = requested_profile
        explicitly_selected = True
        selection_source = requested_profile_source or "explicit"
    else:
        explicitly_selected = False
        default_profile = data.get("default_profile")
        if isinstance(default_profile, str) and default_profile.strip():
            selected_profile = default_profile.strip()
            selection_source = "default_profile"
        elif "default-apps" in profiles:
            selected_profile = "default-apps"
            selection_source = "implicit-default-apps"
        elif len(profiles) == 1:
            selected_profile = next(iter(profiles))
            selection_source = "single-profile"
        else:
            raise ValueError(
                f"Plugin manifest at {path} must declare 'default_profile' when multiple profiles exist"
            )

    if selected_profile not in profiles:
        raise ValueError(
            f"Plugin profile {selected_profile!r} not found in manifest {path}"
        )

    profile_entry = profiles[selected_profile]
    if not isinstance(profile_entry, dict):
        raise ValueError(
            f"Plugin profile {selected_profile!r} in {path} must be a JSON object"
        )

    return (
        selected_profile,
        _normalize_plugin_profile_plugins(
            profile_entry.get("plugins"),
            profile_name=selected_profile,
            path=path,
            manifest_owner=manifest_owner,
        ),
        _normalize_plugin_profile_extensions(
            profile_entry.get("extensions"),
            profile_name=selected_profile,
            path=path,
        ),
        explicitly_selected,
        selection_source,
    )


def resolve_plugin_profile(
    manifest_path: str | Path | None = None,
    *,
    profile: str | None = None,
) -> tuple[str, list[str]]:
    selected_profile, plugin_entries, _extension_entries, _explicitly_selected, _selection_source = _resolve_plugin_profile_selection(
        manifest_path,
        profile=profile,
    )
    return selected_profile, [entry["module_name"] for entry in plugin_entries]


def resolve_plugin_profile_entries(
    manifest_path: str | Path | None = None,
    *,
    profile: str | None = None,
) -> tuple[str, list[dict[str, str]]]:
    selected_profile, plugin_entries, _extension_entries, _explicitly_selected, _selection_source = _resolve_plugin_profile_selection(
        manifest_path,
        profile=profile,
    )
    return selected_profile, [dict(entry) for entry in plugin_entries]


def resolve_plugin_profile_declarative_extensions(
    manifest_path: str | Path | None = None,
    *,
    profile: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    selected_profile, _plugin_entries, extension_entries, _explicitly_selected, _selection_source = _resolve_plugin_profile_selection(
        manifest_path,
        profile=profile,
    )
    return selected_profile, [dict(entry) for entry in extension_entries]


def get_active_plugin_profile(manifest_path: str | Path | None = None) -> str:
    global _active_plugin_profile, _active_plugin_profile_source
    if isinstance(_active_plugin_profile, str) and _active_plugin_profile.strip():
        return _active_plugin_profile
    profile_name, _plugin_modules, _extension_entries, _explicitly_selected, selection_source = _resolve_plugin_profile_selection(
        manifest_path
    )
    _active_plugin_profile = profile_name
    _active_plugin_profile_source = selection_source
    return profile_name


def get_active_plugin_profile_source(manifest_path: str | Path | None = None) -> str:
    global _active_plugin_profile_source
    if isinstance(_active_plugin_profile_source, str) and _active_plugin_profile_source.strip():
        return _active_plugin_profile_source
    get_active_plugin_profile(manifest_path)
    return _active_plugin_profile_source or "unknown"


def get_plugin_boot_order(
    manifest_path: str | Path | None = None,
    *,
    profile: str | None = None,
) -> list[str]:
    path, _manifest_source, manifest_owner = _resolve_manifest_path(
        manifest_path,
        profile=profile,
    )
    profile_name, plugin_entries, extension_entries, explicitly_selected, _selection_source = _resolve_plugin_profile_selection(
        manifest_path,
        profile=profile,
    )
    if not plugin_entries:
        if profile_name == "missing":
            return []
        if extension_entries:
            return []
        if explicitly_selected or manifest_owner == "runtime":
            return []
        raise _plugin_boot_failure(
            path=path,
            profile_name=profile_name,
            reason="declares zero plugin modules",
        )

    boot_order: list[str] = []
    for plugin_entry in plugin_entries:
        module_name = plugin_entry["module_name"]
        owner_class = plugin_entry["owner_class"]
        if owner_class == OWNER_EXTERNAL_THIRD_PARTY:
            raise _plugin_boot_failure(
                path=path,
                profile_name=profile_name,
                module_name=module_name,
                reason=(
                    "external-third-party bootstrap modules are not supported because "
                    "bootstrap import/registration is an in-process operation. Use "
                    "runtime-built-in or first-party bootstrap code, or move the "
                    "extension behind a webhook or isolated plugin-node boundary."
                ),
            )
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            raise _plugin_boot_failure(
                path=path,
                profile_name=profile_name,
                module_name=module_name,
                reason=f"could not be imported ({exc.__class__.__name__}: {exc})",
            ) from exc
        discover = getattr(module, "get_resolved_boot_order", None)
        if callable(discover):
            try:
                value = discover()
            except Exception as exc:
                raise _plugin_boot_failure(
                    path=path,
                    profile_name=profile_name,
                    module_name=module_name,
                    reason=f"failed during boot-order discovery ({exc.__class__.__name__}: {exc})",
                ) from exc
            if isinstance(value, list):
                boot_order.extend(name for name in value if isinstance(name, str) and name.strip())
                continue
        boot_order.append(module_name)
    return boot_order


def _manifest_declarative_extension_record_key(entry: dict[str, Any]) -> str:
    kind = str(entry.get("kind") or "").strip()
    if kind == "dynamic-node":
        return f"manifest-extension:dynamic-node:{entry['name']}"
    if kind == "dynamic-flow":
        return f"manifest-extension:dynamic-flow:{entry['name']}"
    if kind == "webhook-subscription":
        callback_url = str(entry.get("callback_url") or "")
        hashed = hashlib.sha256(callback_url.encode("utf-8")).hexdigest()[:16]
        return f"manifest-extension:webhook-subscription:{entry['event_type']}:{hashed}"
    raise ValueError(f"unsupported declarative extension kind {kind!r}")


def _load_manifest_declarative_extensions(
    extension_entries: list[dict[str, Any]],
    *,
    path: Path,
    manifest_owner: str,
    active_profile: str,
) -> list[str]:
    if not extension_entries:
        return []

    from AINDY.platform_layer.event_service import subscribe_webhook
    from AINDY.platform_layer.node_registry import register_external_node
    from AINDY.runtime.flow_registry import register_dynamic_flow

    loaded: list[str] = []
    for extension_entry in extension_entries:
        kind = str(extension_entry.get("kind") or "").strip()
        record_key = _manifest_declarative_extension_record_key(extension_entry)
        if record_key in _loaded_extension_records:
            continue
        owner_class = str(extension_entry.get("owner_class") or "").strip()
        if kind == "dynamic-node":
            meta = register_external_node(
                name=str(extension_entry["name"]),
                node_type=str(extension_entry["type"]),
                handler=str(extension_entry["handler"]),
                artifact_path=str(extension_entry["artifact_path"]) if extension_entry.get("artifact_path") else None,
                timeout_seconds=int(extension_entry.get("timeout_seconds", 10)),
                secret=extension_entry.get("secret"),
                owner_class=owner_class,
                capabilities=list(extension_entry.get("capabilities") or []),
                provenance=dict(extension_entry["provenance"]) if isinstance(extension_entry.get("provenance"), dict) else None,
                overwrite=bool(extension_entry.get("overwrite", False)),
                db=None,
            )
        elif kind == "webhook-subscription":
            meta = subscribe_webhook(
                event_type=str(extension_entry["event_type"]),
                callback_url=str(extension_entry["callback_url"]),
                secret=extension_entry.get("secret"),
                owner_class=owner_class,
                provenance=dict(extension_entry["provenance"]) if isinstance(extension_entry.get("provenance"), dict) else None,
                db=None,
            )
        elif kind == "dynamic-flow":
            meta = register_dynamic_flow(
                name=str(extension_entry["name"]),
                nodes=list(extension_entry["nodes"]),
                edges={str(src): list(targets) for src, targets in dict(extension_entry.get("edges") or {}).items()},
                start=str(extension_entry["start"]),
                end=list(extension_entry["end"]),
                owner_class=owner_class,
                provenance=dict(extension_entry["provenance"]) if isinstance(extension_entry.get("provenance"), dict) else None,
                overwrite=bool(extension_entry.get("overwrite", False)),
                db=None,
            )
        else:
            raise ValueError(f"unsupported declarative extension kind {kind!r}")

        _loaded_extension_records[record_key] = {
            "record_key": record_key,
            "abi_surface": SURFACE_MANIFEST,
            "abi_version": manifest_effective_abi_version(json.loads(path.read_text(encoding="utf-8"))),
            "abi_stability": extension_surface_stability(SURFACE_MANIFEST),
            "owner_class": owner_class,
            "trust_class": meta.get("trust_class"),
            "execution_model": meta.get("execution_model") or meta.get("authority_model"),
            "sandboxing": meta.get("sandboxing"),
            "trusted_override_active": False,
            "execution_surface": "manifest-declarative-registration",
            "manifest_owner": manifest_owner,
            "profile_name": active_profile,
            "declarative_kind": kind,
            "extension_name": meta.get("name") or meta.get("id") or meta.get("event_type"),
            "source_ref": meta.get("source_ref") or meta.get("callback_url"),
            "provenance": meta.get("provenance"),
            "bootstrap_callable_present": False,
            "bootstrap_executed": False,
            "loaded_at": datetime.now(timezone.utc).isoformat(),
        }
        loaded.append(record_key)
    return loaded


def load_plugins(
    manifest_path: str | Path | None = None,
    *,
    profile: str | None = None,
) -> list[str]:
    """Load plugin bootstrap modules from the selected runtime or app manifest.

    Manifest ownership is mode-sensitive:

    - runtime-only boot selects the runtime-owned manifest
    - app-profile boot selects the app-owned manifest
    - the monolith defaults to the app manifest when present

    Supported manifest shapes are:

    - the stable versioned manifest ABI
      ``{"kind": "aindy-extension-manifest", "abi_version": "aindy.extension.manifest/v1", ...}``
    - the legacy unversioned ``{"plugins": [...]}`` list
    - the unversioned profile format:

    ``{"default_profile": "default-apps", "profiles": {"platform-only": {"plugins": []}, ...}}``

    Each plugin entry may be either:

    - a legacy string module name such as ``"apps.bootstrap"``
    - an explicit object such as
      ``{"module": "apps.bootstrap", "owner_class": "first-party-app"}``
    """

    path, _manifest_source, manifest_owner = _resolve_manifest_path(
        manifest_path,
        profile=profile,
    )
    global _active_plugin_profile, _active_plugin_profile_source
    active_profile, plugin_entries, extension_entries, explicitly_selected, selection_source = _resolve_plugin_profile_selection(
        manifest_path if manifest_path is not None else path,
        profile=profile,
    )
    _, data = _read_plugin_manifest(path)
    if data is None:
        logger.info("No plugin manifest found at %s", path)
        return []
    manifest_abi_version = manifest_effective_abi_version(data)
    manifest_abi_stability = extension_surface_stability(SURFACE_MANIFEST)
    _active_plugin_profile = active_profile
    _active_plugin_profile_source = selection_source
    if not plugin_entries and not extension_entries:
        if not explicitly_selected and manifest_owner != "runtime":
            raise _plugin_boot_failure(
                path=path,
                profile_name=active_profile,
                reason="declares zero plugin modules",
            )
        logger.info(
            "Active plugin profile %s contains no plugin modules; runtime is starting without apps.",
            active_profile,
        )
        return []
    loaded: list[str] = []
    for plugin_entry in plugin_entries:
        module_name = plugin_entry["module_name"]
        owner_class = plugin_entry["owner_class"]
        declared_provenance = plugin_entry.get("provenance")
        trust_class = assert_python_extension_allowed(
            owner_class,
            surface="manifest bootstrap module",
            identifier=module_name,
        )
        if module_name in _loaded_plugins:
            continue
        bootstrap_token = _bootstrap_extension_ctx.set(
            {
                "module_name": module_name,
                "owner_class": owner_class,
                "trust_class": trust_class,
                "manifest_owner": manifest_owner,
                "profile_name": active_profile,
                "abi_surface": SURFACE_MANIFEST,
                "abi_version": manifest_abi_version,
                "abi_stability": manifest_abi_stability,
            }
        )
        _ensure_in_process_extension_audit_record()
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            _bootstrap_extension_ctx.reset(bootstrap_token)
            raise _plugin_boot_failure(
                path=path,
                profile_name=active_profile,
                module_name=module_name,
                reason=f"could not be imported ({exc.__class__.__name__}: {exc})",
            ) from exc
        module_origin = str(getattr(module, "__file__", "")).strip() or None
        bootstrap = getattr(module, "bootstrap", None)
        resolved_provenance = derive_python_extension_provenance(
            owner_class=owner_class,
            surface="manifest-bootstrap",
            extension_name=module_name,
            module_name=module_name,
            source_path=module_origin,
            declared=declared_provenance,
        )
        _bootstrap_extension_ctx.set(
            {
                "module_name": module_name,
                "module_origin": module_origin,
                "owner_class": owner_class,
                "trust_class": trust_class,
                "manifest_owner": manifest_owner,
                "profile_name": active_profile,
                "abi_surface": SURFACE_MANIFEST,
                "abi_version": manifest_abi_version,
                "abi_stability": manifest_abi_stability,
                "provenance": resolved_provenance,
            }
        )
        if callable(bootstrap):
            try:
                bootstrap()
            except Exception as exc:
                _bootstrap_extension_ctx.reset(bootstrap_token)
                raise _plugin_boot_failure(
                    path=path,
                    profile_name=active_profile,
                    module_name=module_name,
                    reason=f"bootstrap raised {exc.__class__.__name__}: {exc}",
                ) from exc
        _bootstrap_extension_ctx.reset(bootstrap_token)
        _loaded_plugins.add(module_name)
        execution_metadata = python_extension_execution_metadata(owner_class)
        _loaded_extension_records[module_name] = {
            "module_name": module_name,
            "abi_surface": SURFACE_MANIFEST,
            "abi_version": manifest_abi_version,
            "abi_stability": manifest_abi_stability,
            "owner_class": owner_class,
            "trust_class": trust_class,
            "execution_model": execution_metadata["execution_model"],
            "sandboxing": execution_metadata["sandboxing"],
            "trusted_override_active": execution_metadata["trusted_override_active"],
            "execution_surface": "manifest-bootstrap",
            "module_origin": module_origin,
            "manifest_owner": manifest_owner,
            "profile_name": active_profile,
            "source_ref": resolved_provenance.get("source_ref"),
            "provenance": resolved_provenance,
            "bootstrap_callable_present": callable(bootstrap),
            "bootstrap_executed": callable(bootstrap),
            "capability_boundary_mode": (
                "in-process-bootstrap-capabilities"
                if owner_class in {OWNER_RUNTIME_BUILTIN, OWNER_FIRST_PARTY_APP}
                else "not-applicable"
            ),
            "allowed_in_process_capabilities": list(
                (_in_process_extension_capability_audit.get(module_name) or {}).get("allowed_capabilities") or []
            ),
            "used_in_process_capabilities": list(
                (_in_process_extension_capability_audit.get(module_name) or {}).get("used_capabilities") or []
            ),
            "denied_in_process_capabilities": list(
                (_in_process_extension_capability_audit.get(module_name) or {}).get("denied_capabilities") or []
            ),
            "loaded_at": datetime.now(timezone.utc).isoformat(),
        }
        loaded.append(module_name)
    declarative_loaded = _load_manifest_declarative_extensions(
        extension_entries,
        path=path,
        manifest_owner=manifest_owner,
        active_profile=active_profile,
    )
    loaded.extend(declarative_loaded)
    if loaded:
        logger.info(
            "Loaded platform plugins from profile %s: %s",
            active_profile,
            ", ".join(loaded),
        )
    return loaded
