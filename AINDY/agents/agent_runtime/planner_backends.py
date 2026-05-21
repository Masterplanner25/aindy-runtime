from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from AINDY.config import settings
from AINDY.platform_layer.external_call_service import perform_external_call
from AINDY.platform_layer.openai_client import chat_completion, get_openai_client


DEFAULT_PLANNER_BACKEND = "openai_chat_compat"
DISABLED_PLANNER_BACKEND = "disabled"


class PlannerBackendError(RuntimeError):
    pass


class PlannerBackendDisabledError(PlannerBackendError):
    pass


@dataclass(frozen=True)
class PlannerRequest:
    objective: str
    run_type: str
    user_id: str | None
    db: Any
    system_prompt: str
    tools: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


def disabled_planner_backend(request: PlannerRequest) -> dict[str, Any]:
    raise PlannerBackendDisabledError(
        "Agent planner backend is disabled by configuration."
    )


def openai_chat_compat_backend(request: PlannerRequest) -> dict[str, Any]:
    response = perform_external_call(
        service_name="openai",
        db=request.db,
        user_id=request.user_id,
        endpoint="chat.completions.create",
        model=settings.AINDY_AGENT_PLANNER_MODEL,
        method="openai.chat",
        extra={
            "purpose": "agent_plan_generation",
            "planner_backend": DEFAULT_PLANNER_BACKEND,
        },
        operation=lambda: chat_completion(
            get_openai_client(),
            model=settings.AINDY_AGENT_PLANNER_MODEL,
            messages=[
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": f"Objective: {request.objective}"},
            ],
            temperature=settings.AINDY_AGENT_PLANNER_TEMPERATURE,
            response_format={"type": "json_object"},
            timeout=settings.OPENAI_CHAT_TIMEOUT_SECONDS,
        ),
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
    if registry._agent_planner_backends.get(DEFAULT_PLANNER_BACKEND) is None:
        registry.register_agent_planner_backend(
            DEFAULT_PLANNER_BACKEND,
            openai_chat_compat_backend,
        )
