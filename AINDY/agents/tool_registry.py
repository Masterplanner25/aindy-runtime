"""Generic agent tool registry and execution boundary."""

from __future__ import annotations

import contextlib
import logging
import os
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
        # ECOGAP-4 / G4b — register client-side MCP tools here for the same reason
        # (available wherever execute_tool runs). No-op unless AINDY_MCP_CLIENT_ENABLED;
        # memoized so the network discovery runs at most once per process. platform-only
        # must stay manifest-empty, so this is wired here rather than via a plugin entry.
        _ensure_mcp_client_tools()
        # ECOGAP-4 / G4a (MEB-2a) — register config-driven capability policies + secret
        # scopes here too, so the (dormant) enforce_capability_policy / resolve_secret gates
        # in execute_tool are active in EVERY process. No-op unless the config env is set.
        _ensure_capability_governance()
    except Exception as exc:
        logger.debug("agent tool plugin load skipped: %s", exc)
    finally:
        _LOADING_PLUGINS = False


_MCP_CLIENT_LOADED = False
_GOVERNANCE_LOADED = False


def _ensure_mcp_client_tools() -> None:
    """Bootstrap client-side MCP tools once per process (memoized, boot-safe)."""
    global _MCP_CLIENT_LOADED
    if _MCP_CLIENT_LOADED:
        return
    _MCP_CLIENT_LOADED = True
    from AINDY.platform_layer import mcp_client

    mcp_client.bootstrap()  # itself a no-op when disabled; never raises


def _ensure_capability_governance() -> None:
    """MEB-2a: register config-driven capability policies + secret scopes once per process
    (memoized). No-op unless AINDY_CAPABILITY_POLICIES / AINDY_SECRET_SCOPES are set; never
    raises (a config-load failure must not break tool execution)."""
    global _GOVERNANCE_LOADED
    if _GOVERNANCE_LOADED:
        return
    _GOVERNANCE_LOADED = True
    try:
        from AINDY.agents.capability_policy import load_capability_policies_from_env
        from AINDY.platform_layer.secret_broker import load_secret_scopes_from_env

        load_capability_policies_from_env()
        load_secret_scopes_from_env()
    except Exception as exc:
        logger.debug("capability governance load skipped: %s", exc)


def register_tool(
    name: str,
    risk: str,
    description: str,
    capability: str,
    required_capability: str,
    category: str,
    egress_scope: str,
    execution_guarantee: str = "AT_LEAST_ONCE",
):
    """Register an agent tool implementation with platform metadata.

    execution_guarantee (MEB-0): "AT_LEAST_ONCE" (default) or "EXACTLY_ONCE". A tool that
    is non-idempotent (send_email, etc.) declares "EXACTLY_ONCE" to opt into the tool-path
    effect boundary — a retry with the same (run, tool, args) replays the cached result
    instead of re-executing. Only active when AINDY_TOOL_IDEMPOTENCY is also enabled.
    """
    def wrapper(fn: Callable) -> Callable:
        TOOL_REGISTRY[name] = {
            "fn": fn,
            "risk": risk,
            "description": description,
            "capability": capability,
            "required_capability": required_capability,
            "category": category,
            "egress_scope": egress_scope,
            "execution_guarantee": execution_guarantee,
        }
        return fn

    return wrapper


def _tool_idempotency_enabled() -> bool:
    return os.getenv("AINDY_TOOL_IDEMPOTENCY", "").strip().lower() in {"1", "true", "yes"}


def _finalize_tool_effect(db, action_id: str, status: str, result, tool_name: str) -> None:
    """Finalize an EffectRecord best-effort — a ledger failure must never mask the tool
    outcome. On success, cache a JSON-safe result for replay; on failure, cache nothing
    (a ``failed``/left-``pending`` row does not block a later retry)."""
    from AINDY.kernel.effect_ledger import complete_effect_record

    payload = None
    if status == "success":
        try:
            import json as _json

            _json.dumps(result)
            payload = {"result": result}
        except (TypeError, ValueError):
            logger.warning(
                "[AgentTool] %s EXACTLY_ONCE result is not JSON-serializable; "
                "caching empty (replay will return None)",
                tool_name,
            )
            payload = {"result": None}
    try:
        complete_effect_record(db, action_id, status, payload)
    except Exception as exc:
        logger.warning("[AgentTool] %s effect finalize (%s) failed: %s", tool_name, status, exc)


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
    _egress_domains: set = set()  # MEB-2b — socket-level allowlist for this tool's caps
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
                get_capability_policy,
                has_capability_policies,
            )

            if has_capability_policies():
                from AINDY.agents.capability_service import _get_capabilities_for_tool

                _tool_caps = _get_capabilities_for_tool(tool_name)
                # MEB-2b — collect the domain allowlist for socket-level egress enforcement
                # (applied around the fn call below when AINDY_EGRESS_ENFORCEMENT is on).
                for _c in _tool_caps:
                    _pol = get_capability_policy(_c)
                    if _pol is not None and _pol.domains:
                        _egress_domains.update(_pol.domains)

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
    # MEB-0 — tool-path effect boundary (idempotency). Doubly-gated and opt-in: the global
    # AINDY_TOOL_IDEMPOTENCY flag AND a per-tool execution_guarantee of EXACTLY_ONCE, with a
    # stable run scope. Default AT_LEAST_ONCE = current behavior (no dedup). Keys only on
    # EffectRecord.action_id (text) — never the ExecutionUnit UUID — so it sidesteps the
    # #157 lookup path. See docs/runtime/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md (MEB-0).
    _guarantee = str(entry.get("execution_guarantee", "AT_LEAST_ONCE")).upper()
    _idempotent = _guarantee == "EXACTLY_ONCE" and bool(run_id) and _tool_idempotency_enabled()
    _action_id = None
    if _idempotent:
        from AINDY.core.execution_gate import compute_action_id
        from AINDY.kernel.effect_ledger import resolve_effect_record

        _action_id = compute_action_id(
            action_type=tool_name, input_payload=args or {}, scope=str(run_id)
        )
        try:
            _already, _cached = resolve_effect_record(
                db, _action_id, tool_name, args or {},
                # MEB-3b — attribute the effect to the caller (tenant_id == user_id).
                tenant_id=str(user_id) if user_id else None,
            )
        except Exception as exc:
            # A ledger failure must not block the tool — degrade to AT_LEAST_ONCE.
            logger.warning("[AgentTool] %s effect resolve failed; running unguarded: %s", tool_name, exc)
            _already, _cached, _idempotent = False, None, False
        else:
            if _already:
                return {
                    "success": True,
                    "result": (_cached or {}).get("result") if isinstance(_cached, dict) else None,
                    "error": None,
                    "idempotent_replay": True,
                }
    # MEB-2b — socket-level egress chokepoint. When a domain policy applies to this tool's
    # capability and AINDY_EGRESS_ENFORCEMENT is on, enforce the allowlist at DNS resolution
    # for the duration of the fn call — catching runtime-built URLs that MEB-2a's static
    # arg-string inspection misses. Inert otherwise. See MEDIATED_EFFECT_BOUNDARY_PROGRAM.md.
    _egress_cm = contextlib.nullcontext()
    if _egress_domains:
        from AINDY.platform_layer.egress_guard import egress_enforcement_enabled

        if egress_enforcement_enabled():
            from AINDY.platform_layer.egress_guard import egress_scope, install_egress_guard

            install_egress_guard()
            _egress_cm = egress_scope(_egress_domains)
    try:
        # AGENT-HARDEN-9 — a tool that calls resolve_secret(name) during execution is
        # gated by the run's granted capabilities via this ambient scope; the secret
        # is consumed inside the tool and never returned to the script.
        from AINDY.platform_layer.secret_broker import capability_scope

        with _egress_cm, capability_scope(_scoped_caps):
            result = entry["fn"](args=args, user_id=user_id, db=db)
        if _idempotent:
            _finalize_tool_effect(db, _action_id, "success", result, tool_name)
        return {"success": True, "result": result, "error": None}
    except Exception as exc:
        logger.warning("[AgentTool] %s failed: %s", tool_name, exc)
        if _idempotent:
            _finalize_tool_effect(db, _action_id, "failed", None, tool_name)
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
