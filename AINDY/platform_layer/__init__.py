# platform_layer — AINDY platform surface
#
# Public Python import surface for first-party app integrations.
# Only submodules listed in __all__ / PUBLIC_MODULES are stable import
# targets for apps. All other submodules are internal runtime implementation.
#
# Authoritative contract document: docs/runtime/PUBLIC_API_CONTRACT.md
# Enforcement test: tests/unit/test_platform_layer_boundary.py

__all__ = [
    "app_runtime",
    "async_job_service",
    "bootstrap_contract",
    "bootstrap_graph",
    "deepseek_client",
    "deployment_contract",
    "domain_health",
    "event_service",
    "event_trace_service",
    "external_call_service",
    "memory_runtime",
    "metrics",
    "openai_client",
    "rate_limiter",
    "registry",
    "response_adapters",
    "scheduler_service",
    "system_state_service",
    "trace_context",
    "user_ids",
    "watcher_contract",
]

# Machine-readable public surface. Must stay in sync with
# PUBLIC_API_CONTRACT.md § "Public Runtime API Modules" and
# tests/unit/test_platform_layer_boundary.py.
PUBLIC_MODULES: frozenset[str] = frozenset(
    f"AINDY.platform_layer.{name}" for name in __all__
)
