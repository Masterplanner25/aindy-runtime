from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from AINDY.config import settings
from AINDY.platform_layer.external_call_service import perform_external_call
from AINDY.platform_layer.openai_client import chat_completion, get_openai_client


DEFAULT_PLANNER_BACKEND = "runtime_local"
RUNTIME_LOCAL_PLANNER_BACKEND = "runtime_local"
OPENAI_COMPAT_PLANNER_BACKEND = "openai_chat_compat"
DISABLED_PLANNER_BACKEND = "disabled"


class PlannerBackendError(RuntimeError):
    pass


class PlannerBackendDisabledError(PlannerBackendError):
    pass


@dataclass(frozen=True)
class PlannerRuntimeApi:
    openai_chat_completion: Any


@dataclass(frozen=True)
class PlannerRequest:
    objective: str
    run_type: str
    user_id: str | None
    system_prompt: str
    tools: tuple[dict[str, Any], ...] = ()
    runtime_api: PlannerRuntimeApi | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def disabled_planner_backend(request: PlannerRequest) -> dict[str, Any]:
    raise PlannerBackendDisabledError(
        "Agent planner backend is disabled by configuration."
    )


def _normalize_objective_text(objective: str) -> str:
    return " ".join(str(objective or "").strip().split())


def _objective_keywords(objective: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9_]+", str(objective or "").lower())
        if token
    }


def _tool_tokens(tool: dict[str, Any]) -> set[str]:
    raw = " ".join(
        [
            str(tool.get("name") or ""),
            str(tool.get("description") or ""),
            str(tool.get("category") or ""),
            str(tool.get("capability") or ""),
            str(tool.get("required_capability") or ""),
        ]
    ).lower()
    return {
        token
        for token in re.findall(r"[a-z0-9_]+", raw)
        if token
    }


def _tool_risk_rank(tool: dict[str, Any]) -> int:
    risk = str(tool.get("risk") or "medium").lower()
    return {"low": 0, "medium": 1, "high": 2}.get(risk, 1)


def _plan_args_for_tool(tool_name: str, objective: str) -> dict[str, Any]:
    normalized_objective = _normalize_objective_text(objective)
    lowered = normalized_objective.lower()
    if tool_name == "memory.recall":
        return {"query": normalized_objective}
    if tool_name == "memory.write":
        tags = ["agent", "runtime-plan"]
        if "memory" not in tags and any(
            keyword in lowered for keyword in ("memory", "remember", "recall")
        ):
            tags.append("memory")
        return {"content": normalized_objective, "tags": tags}
    return {"objective": normalized_objective}


def _choose_runtime_local_tool(request: PlannerRequest) -> dict[str, Any]:
    tools = [tool for tool in request.tools if isinstance(tool, dict) and tool.get("name")]
    if not tools:
        raise PlannerBackendError(
            "Runtime-local planner backend requires at least one registered tool."
        )

    objective_tokens = _objective_keywords(request.objective)
    objective_text = _normalize_objective_text(request.objective).lower()
    explicit_write = any(
        keyword in objective_text
        for keyword in ("write", "save", "store", "remember", "record", "note")
    )
    explicit_read = any(
        keyword in objective_text
        for keyword in ("recall", "find", "lookup", "search", "retrieve", "read")
    )

    def score(tool: dict[str, Any]) -> tuple[int, int, int]:
        overlap = len(objective_tokens & _tool_tokens(tool))
        name = str(tool.get("name") or "")
        preference = 0
        if explicit_write and name == "memory.write":
            preference = 3
        elif explicit_read and name == "memory.recall":
            preference = 3
        elif not explicit_write and not explicit_read and name == "memory.recall":
            preference = 2
        elif name == "memory.write":
            preference = 1
        return (
            preference,
            overlap,
            -_tool_risk_rank(tool),
        )

    return max(tools, key=score)


def runtime_local_planner_backend(request: PlannerRequest) -> dict[str, Any]:
    selected_tool = _choose_runtime_local_tool(request)
    tool_name = str(selected_tool["name"])
    risk_level = str(selected_tool.get("risk") or "low").lower()
    objective = _normalize_objective_text(request.objective)
    args = _plan_args_for_tool(tool_name, objective)
    description = (
        f"Use {tool_name} to make forward progress on the objective "
        f"{objective!r} with runtime-local planning."
    )
    return {
        "executive_summary": (
            f"Use the runtime-local planner to select {tool_name} as the first "
            f"concrete action for {objective!r}."
        ),
        "steps": [
            {
                "tool": tool_name,
                "args": args,
                "risk_level": risk_level if risk_level in {"low", "medium", "high"} else "low",
                "description": description,
            }
        ],
        "overall_risk": risk_level if risk_level in {"low", "medium", "high"} else "low",
    }


def openai_chat_compat_backend(request: PlannerRequest) -> dict[str, Any]:
    runtime_api = request.runtime_api
    if runtime_api is None or not callable(getattr(runtime_api, "openai_chat_completion", None)):
        raise PlannerBackendError(
            "Planner runtime API does not provide openai_chat_completion."
        )
    response = runtime_api.openai_chat_completion(
        system_prompt=request.system_prompt,
        objective=request.objective,
        user_id=request.user_id,
        metadata={
            "purpose": "agent_plan_generation",
            "planner_backend": OPENAI_COMPAT_PLANNER_BACKEND,
        },
    )
    content = str(response.choices[0].message.content or "")
    plan = json.loads(content)
    if not isinstance(plan, dict):
        raise PlannerBackendError(
            "Planner backend returned a non-object JSON payload."
        )
    return plan


def register_builtin_planner_backends() -> None:
    from AINDY.platform_layer import registry

    if registry._agent_planner_backends.get(DISABLED_PLANNER_BACKEND) is None:
        registry.register_agent_planner_backend(
            DISABLED_PLANNER_BACKEND,
            disabled_planner_backend,
        )
    if registry._agent_planner_backends.get(RUNTIME_LOCAL_PLANNER_BACKEND) is None:
        registry.register_agent_planner_backend(
            RUNTIME_LOCAL_PLANNER_BACKEND,
            runtime_local_planner_backend,
        )
    if registry._agent_planner_backends.get(OPENAI_COMPAT_PLANNER_BACKEND) is None:
        registry.register_agent_planner_backend(
            OPENAI_COMPAT_PLANNER_BACKEND,
            openai_chat_compat_backend,
        )
