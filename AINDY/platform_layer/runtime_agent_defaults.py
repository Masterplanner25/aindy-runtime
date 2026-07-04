from __future__ import annotations

import logging
from typing import Any

from AINDY.agents.agent_runtime.planner_backends import (
    DEFAULT_PLANNER_BACKEND,
    register_builtin_planner_backends,
)
from AINDY.agents.tool_registry import TOOL_REGISTRY, register_tool
from AINDY.kernel.syscall_dispatcher import get_dispatcher, make_syscall_ctx_from_tool

logger = logging.getLogger(__name__)

_DEFAULT_PLANNER_PROMPT = """You are a runtime-owned AINDY agent planner.

Produce a structured execution plan using only the injected tool catalog.

Return ONLY valid JSON with exactly this structure:
{
  "executive_summary": "2-3 sentence summary of what the agent will do",
  "steps": [
    {
      "tool": "<tool_name>",
      "args": {<tool-specific args>},
      "risk_level": "low|medium|high",
      "description": "one sentence explaining this step"
    }
  ],
  "overall_risk": "low|medium|high"
}

Rules:
- Use only tools listed above
- Keep plans concise (1-5 steps maximum)
- Prefer low-risk tools when they are sufficient
- overall_risk must match the highest step risk_level
- Return ONLY the JSON object, no markdown, no extra text
"""


def build_planner_context(context: dict[str, Any]) -> dict[str, str]:
    return {
        "system_prompt": _DEFAULT_PLANNER_PROMPT,
        "context_block": "",
        "planner_backend": DEFAULT_PLANNER_BACKEND,
    }


def get_tools_for_run(_context: dict[str, Any]) -> list[dict[str, Any]]:
    # Diagnostic tools are executable + capability-wired but are NOT offered to the
    # planner's tool catalog (they exist to verify the execution path, not to plan).
    return [
        {
            "name": name,
            "risk": metadata.get("risk"),
            "description": metadata.get("description"),
            "capability": metadata.get("capability"),
            "required_capability": metadata.get("required_capability"),
            "category": metadata.get("category"),
            "egress_scope": metadata.get("egress_scope"),
        }
        for name, metadata in TOOL_REGISTRY.items()
        if isinstance(metadata, dict) and metadata.get("category") != "diagnostic"
    ]


def _dispatch_runtime_tool(
    syscall_name: str,
    payload: dict[str, Any],
    user_id: str,
    *,
    syscall_capabilities: list[str],
) -> dict[str, Any]:
    ctx = make_syscall_ctx_from_tool(
        str(user_id or ""),
        capabilities=syscall_capabilities,
    )
    result = get_dispatcher().dispatch(syscall_name, payload, ctx)
    if result.get("status") != "success":
        raise RuntimeError(result.get("error") or f"{syscall_name} failed")
    data = result.get("data")
    return data if isinstance(data, dict) else {}


def memory_recall(args: dict[str, Any], user_id: str, db) -> dict[str, Any]:
    return _dispatch_runtime_tool(
        "sys.v1.memory.read",
        dict(args or {}),
        user_id,
        syscall_capabilities=["memory.read"],
    )


def memory_write(args: dict[str, Any], user_id: str, db) -> dict[str, Any]:
    payload = {"source": "agent", **dict(args or {})}
    data = _dispatch_runtime_tool(
        "sys.v1.memory.write",
        payload,
        user_id,
        syscall_capabilities=["memory.write"],
    )
    node = data.get("node")
    return {
        "node_id": node.get("id") if isinstance(node, dict) else None,
        "path": data.get("path"),
    }


_SELFTEST_ATTEMPTS: dict[str, int] = {}


def runtime_selftest(args: dict[str, Any], user_id: str, db) -> dict[str, Any]:
    """Diagnostic tool: verify the agent tool-execution path end-to-end.

    Echoes a caller-requested outcome so an operator (or an integration test) can
    confirm the runtime handles success, failure, retry, and halt correctly through
    the real call_tool seam — including inside the nodus_worker subprocess. It has
    no external effect.

    Args:
      outcome: "success" (default) or "fail".
      error:   error message to raise when outcome="fail" (its substrings drive the
               is_retryable_error classification, e.g. "permission" → non-retryable).
      attempt_key: when set, count invocations under this key (per process, so per
               subprocess = per segment) and append " (attempt N)" to the raised
               error — lets a caller observe how many times the retry loop re-ran.

    Returns the echoed args on success; raises RuntimeError on requested failure
    (execute_tool converts the raise into {success: False, error: str}).
    """
    args = dict(args or {})
    attempt_key = str(args.get("attempt_key") or "")
    attempt = None
    if attempt_key:
        _SELFTEST_ATTEMPTS[attempt_key] = _SELFTEST_ATTEMPTS.get(attempt_key, 0) + 1
        attempt = _SELFTEST_ATTEMPTS[attempt_key]

    if str(args.get("outcome", "success")).lower() == "fail":
        base = str(args.get("error") or "selftest requested failure")
        raise RuntimeError(f"{base} (attempt {attempt})" if attempt is not None else base)

    return {"ok": True, "attempt": attempt, "echo": {k: v for k, v in args.items() if k != "attempt_key"}}


def runtime_capability_bundle() -> dict[str, Any]:
    return {
        "definitions": {
            "execute_flow": {
                "description": "Start and continue a scoped workflow execution.",
                "risk_level": "low",
            },
            "read_memory": {
                "description": "Read memory and recall prior context.",
                "risk_level": "low",
            },
            "write_memory": {
                "description": "Create or update durable memory.",
                "risk_level": "low",
            },
            "runtime_selftest": {
                "description": "Exercise the agent tool-execution path (diagnostic).",
                "risk_level": "low",
            },
        },
        "tool_capabilities": {
            "memory.recall": ["read_memory"],
            "memory.write": ["write_memory"],
            "runtime.selftest": ["runtime_selftest"],
        },
        "agent_capabilities": {
            "default": ["execute_flow"],
        },
    }


def evaluate_trigger(payload: dict[str, Any]) -> dict[str, Any]:
    trigger = payload.get("trigger") if isinstance(payload.get("trigger"), dict) else {}
    trigger_type = str(payload.get("trigger_type") or trigger.get("trigger_type") or "system").lower()
    importance = trigger.get("importance")
    try:
        priority = float(importance if importance is not None else 0.8 if trigger_type == "user" else 0.55)
    except (TypeError, ValueError):
        priority = 0.55
    priority = max(0.0, min(1.0, round(priority, 4)))

    if trigger_type == "watcher" and priority < 0.4:
        return {
            "decision": "ignore",
            "priority": priority,
            "reason": "watcher trigger is below the runtime default execution threshold",
            "defer_seconds": 0,
        }
    if priority < 0.35:
        return {
            "decision": "defer",
            "priority": priority,
            "reason": "trigger priority is below the runtime default execution threshold",
            "defer_seconds": 300,
        }
    return {
        "decision": "execute",
        "priority": priority,
        "reason": "runtime default trigger evaluator approved execution",
        "defer_seconds": 0,
    }


def handle_agent_run_completed(context: dict[str, Any]) -> None:
    return None


def register() -> None:
    from AINDY.platform_layer import registry
    register_builtin_planner_backends()

    registry.publish_bootstrap_registration(
        "runtime-agent-defaults",
        owner_class="runtime-built-in",
        module_name=__name__,
    )

    if "memory.recall" not in TOOL_REGISTRY:
        register_tool(
            "memory.recall",
            risk="low",
            description="Recall relevant memory nodes for a given query",
            capability="tool:memory.recall",
            required_capability="read_memory",
            category="memory",
            egress_scope="internal",
        )(memory_recall)
    if "memory.write" not in TOOL_REGISTRY:
        register_tool(
            "memory.write",
            risk="low",
            description="Write a memory node with content and tags",
            capability="tool:memory.write",
            required_capability="write_memory",
            category="memory",
            egress_scope="internal",
        )(memory_write)
    if "runtime.selftest" not in TOOL_REGISTRY:
        register_tool(
            "runtime.selftest",
            risk="low",
            description="Diagnostic: exercise the agent tool-execution path",
            capability="tool:runtime.selftest",
            required_capability="runtime_selftest",
            category="diagnostic",
            egress_scope="internal",
        )(runtime_selftest)

    if "default" not in registry._agent_planner_contexts:
        registry.register_planner_context_provider("default", build_planner_context)
    if "default" not in registry._agent_run_tools:
        registry.register_run_tool_provider("default", get_tools_for_run)
    if "default" not in registry._trigger_evaluators:
        registry.register_trigger_evaluator("default", evaluate_trigger)
    if not registry._agent_completion_hooks.get("default"):
        registry.register_agent_completion_hook("default", handle_agent_run_completed)

    registry.register_capability_definition_provider(runtime_capability_bundle)
    logger.debug("Runtime-owned agent defaults registered")
