from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from AINDY.agents.agent_runtime.planner_backends import (
    DEFAULT_PLANNER_BACKEND,
    DISABLED_PLANNER_BACKEND,
    PlannerRuntimeApi,
    PlannerBackendDisabledError,
    PlannerBackendError,
    PlannerRequest,
)
from AINDY.agents.agent_runtime.shared import get_runtime_compat_module, logger
from AINDY.config import settings
from AINDY.platform_layer.external_call_service import perform_external_call
from AINDY.platform_layer.openai_client import chat_completion, get_openai_client

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

A step may instead be a WAIT step that pauses the run mid-plan until an external
event (e.g. a human approval) arrives, then continues:
    { "wait_for": "<event.type>", "description": "why the run pauses here" }
A WAIT step has no "tool" and no "risk_level". Use one only to gate a later step
on an external signal (e.g. wait for "agent.approval.granted" before a risky send).

Rules:
- Use only tools listed above
- Keep plans concise (3-7 steps maximum)
- Be specific in args using the request context
- overall_risk must match the highest tool step risk_level (WAIT steps have no risk)
- Return ONLY the JSON object, no markdown, no extra text
"""

# Canonical event a policy-inserted approval WAIT step waits for. A resume route
# publishes this (scoped to the run's correlation) to continue the run.
AGENT_APPROVAL_EVENT = "agent.approval.granted"


def _agent_execution_backend() -> str:
    """The active agent execution backend (matches nodus_execution_service)."""
    import os

    return os.getenv("AINDY_AGENT_EXECUTION_BACKEND", "agent_flow").strip().lower()


def apply_wait_policy(plan: dict, *, backend: str | None = None) -> dict:
    """Post-process a generated plan's WAIT steps for the active execution backend.

    Mid-plan WAIT steps only execute on the ``nodus_vm`` backend (RTR-1 Phase 2e);
    the default AGENT_FLOW path has no wait concept and would try to run a WAIT
    step as a tool-less tool and fail. So:

    * On any non-``nodus_vm`` backend, **strip** every WAIT step (safety — whether
      it came from the LLM or a policy) so AGENT_FLOW only ever sees tool steps.
    * On ``nodus_vm`` with ``AINDY_AGENT_WAIT_BEFORE_HIGH_RISK`` enabled, **insert**
      a human-approval WAIT (``AGENT_APPROVAL_EVENT``) before the first high-risk
      step, so the run does its safe prep, then pauses for approval before the
      risky action. The inserted step carries no ``correlation_key`` — the executor
      scopes the wait to the run's own correlation id.

    Mutates and returns ``plan``.
    """
    from AINDY.runtime.agent_plan_compiler import _is_wait_step

    steps = list(plan.get("steps") or [])
    backend = (backend or _agent_execution_backend())

    if backend != "nodus_vm":
        filtered = [s for s in steps if not _is_wait_step(s)]
        if len(filtered) != len(steps):
            logger.info(
                "[AgentPlanner] stripped %d WAIT step(s) — backend %r cannot execute them",
                len(steps) - len(filtered), backend,
            )
        plan["steps"] = filtered
        return plan

    if getattr(settings, "AINDY_AGENT_WAIT_BEFORE_HIGH_RISK", False):
        new_steps: list = []
        inserted = False
        for step in steps:
            if (
                not inserted
                and not _is_wait_step(step)
                and str((step or {}).get("risk_level", "")).lower() == "high"
            ):
                new_steps.append({
                    "wait_for": AGENT_APPROVAL_EVENT,
                    "description": "Await human approval before the high-risk step",
                })
                inserted = True
            new_steps.append(step)
        if inserted:
            logger.info("[AgentPlanner] inserted approval WAIT before first high-risk step")
        plan["steps"] = new_steps
    return plan


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
    runtime_api = PlannerRuntimeApi(
        openai_chat_completion=lambda *, system_prompt, objective, user_id, metadata: perform_external_call(
            service_name="openai",
            db=db,
            user_id=user_id,
            endpoint="chat.completions.create",
            model=settings.AINDY_AGENT_PLANNER_MODEL,
            method="openai.chat",
            extra=dict(metadata or {}),
            operation=lambda: chat_completion(
                get_openai_client(),
                model=settings.AINDY_AGENT_PLANNER_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Objective: {objective}"},
                ],
                temperature=settings.AINDY_AGENT_PLANNER_TEMPERATURE,
                response_format={"type": "json_object"},
                timeout=settings.OPENAI_CHAT_TIMEOUT_SECONDS,
            ),
        )
    )
    plan = backend(
        PlannerRequest(
            objective=objective_text,
            run_type=run_type,
            user_id=None if user_id is None else str(user_id),
            system_prompt=system_prompt,
            tools=tuple(tools),
            runtime_api=runtime_api,
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

        from AINDY.runtime.agent_plan_compiler import _is_wait_step

        # WAIT steps carry no risk_level — exclude them from risk aggregation so a
        # pause point never inflates overall_risk to "high".
        step_risks = [
            step.get("risk_level", "high")
            for step in plan["steps"]
            if not _is_wait_step(step)
        ]
        risk_order = {"low": 0, "medium": 1, "high": 2}
        max_risk = max(step_risks, key=lambda risk: risk_order.get(risk, 2), default="high")
        if risk_order.get(plan["overall_risk"], 0) < risk_order.get(max_risk, 0):
            plan["overall_risk"] = max_risk

        # Reconcile WAIT steps with the execution backend (strip on AGENT_FLOW;
        # optionally insert an approval gate on nodus_vm).
        plan = apply_wait_policy(plan)
        return plan
    except Exception as exc:
        compat = get_runtime_compat_module()

        compat._plan_failure.reason = f"{type(exc).__name__}: {exc}"
        logger.warning("[AgentRuntime] Plan generation failed: %s", exc)
        return None
