from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from AINDY.agents.agent_runtime.planner_backends import (
    DEFAULT_PLANNER_BACKEND,
    DISABLED_PLANNER_BACKEND,
    PlannerBackendDisabledError,
    PlannerBackendError,
    PlannerRequest,
)
from AINDY.agents.agent_runtime.shared import get_runtime_compat_module, logger
from AINDY.config import settings

PLANNER_SYSTEM_PROMPT = """You are a generic agent planner.

Produce a structured execution plan using only the injected tool catalog.

Available tools are provided by registered application extensions.

Risk rules:
- overall_risk = the highest risk_level of any step
- If ANY step is high risk, overall_risk must be "high"

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
- Keep plans concise (3-7 steps maximum)
- Be specific in args using the request context
- overall_risk must match the highest step risk_level
- Return ONLY the JSON object, no markdown, no extra text
"""


def _requires_approval(overall_risk: str, user_id: str, db: Session) -> bool:
    if overall_risk == "high":
        return True

    from AINDY.db.models import AgentTrustSettings
    from AINDY.platform_layer.user_ids import parse_user_id

    owner_user_id = parse_user_id(user_id)
    owner_filter_value = owner_user_id if owner_user_id is not None else user_id
    trust = db.query(AgentTrustSettings).filter(AgentTrustSettings.user_id == owner_filter_value).first()
    if not trust:
        return True
    if overall_risk == "medium":
        return not trust.auto_execute_medium
    if overall_risk == "low":
        return not trust.auto_execute_low
    return True


def _build_kpi_context_block(user_id: str, db: Session) -> str:
    try:
        compat = get_runtime_compat_module()

        return compat._get_planner_context("default", user_id=user_id, db=db).get("context_block", "")
    except Exception:
        return ""


def _legacy_planner_context_block_disabled(user_id: str, db: Session) -> str:
    return ""


def _resolve_planner_backend_name(planner_context: dict[str, object]) -> tuple[str, str]:
    explicit_backend = str(settings.AINDY_AGENT_PLANNER_BACKEND or "").strip()
    if explicit_backend:
        return explicit_backend, "settings.AINDY_AGENT_PLANNER_BACKEND"
    context_backend = str(planner_context.get("planner_backend") or "").strip()
    if context_backend:
        return context_backend, "planner_context"
    return DEFAULT_PLANNER_BACKEND, "runtime_default"


def _build_planner_prompt(
    *,
    system_prompt: str,
    planner_context: dict[str, object],
    tools: list[dict],
) -> str:
    prompt = str(system_prompt or "")
    context_block = str(planner_context.get("context_block") or "")
    if context_block and context_block not in prompt:
        prompt += context_block
    if tools:
        prompt += "\n\nAvailable tools:\n" + "\n".join(
            f"- {tool.get('name')}: {tool.get('description', '')} (risk={tool.get('risk', 'unknown')})"
            for tool in tools
            if isinstance(tool, dict) and tool.get("name")
        )
    return prompt


def _get_planner_backend(name: str):
    from AINDY.platform_layer.registry import get_agent_planner_backend

    backend = get_agent_planner_backend(name)
    if backend is None:
        raise PlannerBackendError(
            f"Agent planner backend {name!r} is not registered."
        )
    return backend


def _invoke_planner_backend(
    *,
    backend_name: str,
    objective_text: str,
    run_type: str,
    user_id: str | None,
    db: Session | None,
    system_prompt: str,
    tools: list[dict],
    planner_context: dict[str, object],
) -> dict:
    backend = _get_planner_backend(backend_name)
    plan = backend(
        PlannerRequest(
            objective=objective_text,
            run_type=run_type,
            user_id=None if user_id is None else str(user_id),
            db=db,
            system_prompt=system_prompt,
            tools=tuple(tools),
            metadata={
                "planner_backend": backend_name,
                "planner_context_keys": sorted(planner_context.keys()),
            },
        )
    )
    if not isinstance(plan, dict):
        raise PlannerBackendError(
            f"Agent planner backend {backend_name!r} returned {type(plan).__name__}, expected dict."
        )
    return plan


def generate_plan(
    objective: str | None = None,
    user_id: str | None = None,
    db: Session | None = None,
    **values,
) -> Optional[dict]:
    try:
        compat = get_runtime_compat_module()

        objective_text = compat._resolve_objective(objective, values)
        run_type = "default"
        planner_context = compat._get_planner_context(run_type, user_id=user_id, db=db)
        tools = compat._get_tools_for_run(run_type, user_id=user_id, db=db)
        system_prompt = str(planner_context.get("system_prompt") or "")
        if not system_prompt:
            logger.warning("[AgentRuntime] No planner context registered for %s", run_type)
            return None
        backend_name, _backend_source = _resolve_planner_backend_name(planner_context)
        if backend_name == DISABLED_PLANNER_BACKEND:
            raise PlannerBackendDisabledError(
                "Agent planner backend is disabled by configuration."
            )
        system_prompt = _build_planner_prompt(
            system_prompt=system_prompt,
            planner_context=planner_context,
            tools=tools,
        )
        plan = _invoke_planner_backend(
            backend_name=backend_name,
            objective_text=objective_text,
            run_type=run_type,
            user_id=user_id,
            db=db,
            system_prompt=system_prompt,
            tools=tools,
            planner_context=planner_context,
        )
        if "steps" not in plan or "overall_risk" not in plan:
            logger.warning("[AgentRuntime] Plan missing required fields: %s", plan)
            return None

        step_risks = [step.get("risk_level", "high") for step in plan["steps"]]
        risk_order = {"low": 0, "medium": 1, "high": 2}
        max_risk = max(step_risks, key=lambda risk: risk_order.get(risk, 2), default="high")
        if risk_order.get(plan["overall_risk"], 0) < risk_order.get(max_risk, 0):
            plan["overall_risk"] = max_risk
        return plan
    except Exception as exc:
        compat = get_runtime_compat_module()

        compat._plan_failure.reason = f"{type(exc).__name__}: {exc}"
        logger.warning("[AgentRuntime] Plan generation failed: %s", exc)
        return None
