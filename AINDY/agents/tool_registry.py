"""Generic agent tool registry and execution boundary."""

from __future__ import annotations

import logging
from typing import Callable

from AINDY.core.execution_signal_helper import queue_system_event
from AINDY.platform_layer.extension_boundary import sanitize_extension_context

logger = logging.getLogger(__name__)

TOOL_REGISTRY: dict[str, dict] = {}
_SUGGESTION_PROVIDERS: list[Callable] = []
_LOADING_PLUGINS = False


def _ensure_tools_loaded() -> None:
    global _LOADING_PLUGINS
    if _LOADING_PLUGINS:
        return
    _LOADING_PLUGINS = True
    try:
        from AINDY.platform_layer.registry import (
            _ensure_runtime_agent_defaults,
            load_plugins,
        )

        load_plugins()
        # Runtime-native tools (memory.read / memory.write) are registered by the
        # runtime agent defaults, NOT by load_plugins — the runtime manifest carries
        # no plugin modules. Ensure them here so tools resolve in EVERY process that
        # executes a tool, including the nodus_worker subprocess whose only tool-load
        # entry point is this function. Without it the nodus_vm call_tool seam returns
        # "Tool not found" for runtime tools (RTR-1 parity blocker). Idempotent.
        _ensure_runtime_agent_defaults()
    except Exception as exc:
        logger.debug("agent tool plugin load skipped: %s", exc)
    finally:
        _LOADING_PLUGINS = False


def register_tool(
    name: str,
    risk: str,
    description: str,
    capability: str,
    required_capability: str,
    category: str,
    egress_scope: str,
):
    """Register an agent tool implementation with platform metadata."""
    def wrapper(fn: Callable) -> Callable:
        TOOL_REGISTRY[name] = {
            "fn": fn,
            "risk": risk,
            "description": description,
            "capability": capability,
            "required_capability": required_capability,
            "category": category,
            "egress_scope": egress_scope,
        }
        return fn

    return wrapper


def register_tool_suggestion_provider(provider: Callable) -> Callable:
    """Register a callable that can suggest tools for an optional context snapshot."""
    if provider not in _SUGGESTION_PROVIDERS:
        _SUGGESTION_PROVIDERS.append(provider)
    return provider


def execute_tool(
    tool_name: str,
    args: dict,
    user_id: str,
    db,
    run_id: str = None,
    execution_token: dict = None,
) -> dict:
    """Execute a registered tool by name and return a normalized result."""
    _ensure_tools_loaded()
    entry = TOOL_REGISTRY.get(tool_name)
    if not entry:
        return {
            "success": False,
            "result": None,
            "error": f"Tool '{tool_name}' not found in registry",
        }
    if run_id and execution_token is None:
        return {
            "success": False,
            "result": None,
            "error": "capability token is required for agent run tool execution",
        }
    # AGENT-HARDEN-9 — capabilities the tool may resolve secrets under (from the token).
    _scoped_caps: list = []
    if execution_token is not None:
        if not run_id:
            return {
                "success": False,
                "result": None,
                "error": "run_id is required when execution_token is supplied",
            }
        try:
            from AINDY.agents.capability_service import check_tool_capability

            capability_check = check_tool_capability(
                token=execution_token,
                run_id=run_id,
                user_id=user_id,
                tool_name=tool_name,
            )
            if not capability_check["ok"]:
                queue_system_event(
                    db=db,
                    event_type="capability.denied",
                    user_id=user_id,
                    trace_id=str(run_id),
                    payload={
                        "run_id": str(run_id),
                        "tool_name": tool_name,
                        "error": capability_check["error"],
                        "allowed_capabilities": capability_check.get("allowed_capabilities", []),
                        "granted_tools": capability_check.get("granted_tools", []),
                    },
                    required=True,
                )
                return {
                    "success": False,
                    "result": None,
                    "error": capability_check["error"],
                }
            queue_system_event(
                db=db,
                event_type="capability.allowed",
                user_id=user_id,
                trace_id=str(run_id),
                payload={
                    "run_id": str(run_id),
                    "tool_name": tool_name,
                    "allowed_capabilities": capability_check.get("allowed_capabilities", []),
                    "granted_tools": capability_check.get("granted_tools", []),
                },
                required=True,
            )
            _scoped_caps = list(capability_check.get("allowed_capabilities", []) or [])

            # AGENT-HARDEN-8 — declarative per-capability policy (recipient / domain
            # egress allowlists). Vacuous unless a policy is registered for one of the
            # tool's required capabilities, so no behavior change until opted in.
            from AINDY.agents.capability_policy import (
                enforce_capability_policy,
                enforce_capability_rate,
                has_capability_policies,
            )

            if has_capability_policies():
                from AINDY.agents.capability_service import _get_capabilities_for_tool

                _tool_caps = _get_capabilities_for_tool(tool_name)

                def _deny_policy(result):
                    queue_system_event(
                        db=db,
                        event_type="capability.policy_denied",
                        user_id=user_id,
                        trace_id=str(run_id),
                        payload={
                            "run_id": str(run_id),
                            "tool_name": tool_name,
                            "violations": result["violations"],
                        },
                        required=True,
                    )

                policy_result = enforce_capability_policy(_tool_caps, args)
                if not policy_result["allowed"]:
                    _deny_policy(policy_result)
                    first = policy_result["violations"][0]
                    return {
                        "success": False,
                        "result": None,
                        "error": (
                            f"capability policy violation: {first['kind']} "
                            f"{first['value']!r} not allowed by capability "
                            f"'{first['capability']}'"
                        ),
                    }

                # Rate limits are checked last (they increment a counter, so only
                # otherwise-permitted calls count toward the window).
                rate_result = enforce_capability_rate(_tool_caps, scope=str(user_id))
                if not rate_result["allowed"]:
                    _deny_policy(rate_result)
                    first = rate_result["violations"][0]
                    return {
                        "success": False,
                        "result": None,
                        "error": (
                            f"capability rate limit exceeded: '{first['capability']}' "
                            f"over {first['limit']}/{first['window_secs']}s"
                        ),
                    }
        except Exception as exc:
            logger.warning("[AgentTool] %s capability check failed: %s", tool_name, exc)
            return {
                "success": False,
                "result": None,
                "error": "capability enforcement failed",
            }
    try:
        # AGENT-HARDEN-9 — a tool that calls resolve_secret(name) during execution is
        # gated by the run's granted capabilities via this ambient scope; the secret
        # is consumed inside the tool and never returned to the script.
        from AINDY.platform_layer.secret_broker import capability_scope

        with capability_scope(_scoped_caps):
            result = entry["fn"](args=args, user_id=user_id, db=db)
        return {"success": True, "result": result, "error": None}
    except Exception as exc:
        logger.warning("[AgentTool] %s failed: %s", tool_name, exc)
        return {"success": False, "result": None, "error": str(exc)}


def get_tool_risk(tool_name: str) -> str:
    """Return risk level of a registered tool, or 'high' if unknown."""
    _ensure_tools_loaded()
    entry = TOOL_REGISTRY.get(tool_name)
    return entry["risk"] if entry else "high"


def suggest_tools(
    suggestion_context: dict | None = None,
    user_id: str = None,
    db=None,
    **legacy_kwargs,
) -> list:
    """Return tool suggestions from registered providers.

    The runtime treats the suggestion context as opaque. App-owned providers may
    interpret it however they choose, or may derive their own context from
    plugin-owned jobs and services when it is absent.
    """
    _ensure_tools_loaded()
    if suggestion_context is None and "kpi_snapshot" in legacy_kwargs:
        suggestion_context = legacy_kwargs["kpi_snapshot"]
    sanitized_context = sanitize_extension_context(suggestion_context or {})
    for provider in tuple(_SUGGESTION_PROVIDERS):
        try:
            suggestions = provider(
                suggestion_context=sanitized_context,
                user_id=user_id,
            )
        except TypeError:
            suggestions = provider(kpi_snapshot=sanitized_context, user_id=user_id)
        except Exception as exc:
            logger.warning("[AgentTools] suggestion provider failed: %s", exc)
            continue
        if suggestions:
            return suggestions[:3]
    return []
