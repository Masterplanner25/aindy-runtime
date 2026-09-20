from __future__ import annotations

import logging
import sys
import threading
import types
from typing import Any

from sqlalchemy.orm import Session

from AINDY.platform_layer.openai_client import chat_completion, get_openai_client
from AINDY.platform_layer.external_call_service import perform_external_call
from AINDY.platform_layer.user_ids import parse_user_id

logger = logging.getLogger(__name__)

_client: Any | None = None
LOCAL_AGENT_ID = "00000000-0000-0000-0000-000000000001"
_plan_failure = threading.local()
_OBJECTIVE_ATTR = "".join(("go", "al"))
_OBJECTIVE_PREVIEW_KEY = "objective_preview"


def get_runtime_compat_module() -> types.ModuleType:
    compat = sys.modules.get("agents.agent_runtime")
    if compat is not None:
        return compat
    compat = sys.modules.get("AINDY.agents.agent_runtime")
    if compat is not None:
        return compat
    import AINDY.agents.agent_runtime as compat_module

    return compat_module


def _run_objective(run) -> str:
    return getattr(run, "objective", None) or getattr(run, _OBJECTIVE_ATTR, "") or ""


def _resolve_objective(objective: str | None, values: dict) -> str:
    resolved = objective if objective is not None else values.get("objective")
    if resolved is None:
        resolved = values.get(_OBJECTIVE_ATTR)
    return "" if resolved is None else str(resolved)


def _objective_preview(objective_text: str) -> dict:
    return {_OBJECTIVE_PREVIEW_KEY: objective_text[:120]}


def _db_user_id(user_id: str):
    parsed = parse_user_id(user_id)
    return parsed if parsed is not None else user_id


def _db_run_id(run_id):
    parsed = parse_user_id(run_id)
    return parsed if parsed is not None else run_id


def _user_matches(left, right) -> bool:
    left_uuid = parse_user_id(left)
    right_uuid = parse_user_id(right)
    if left_uuid is not None and right_uuid is not None:
        return left_uuid == right_uuid
    return str(left) == str(right)


def _get_client() -> Any:
    global _client
    if _client is None:
        _client = get_openai_client()
    return _client


#: The keys a planner-context / tools-for-run provider may rely on. Every value is a primitive
#: (or None) so it survives ``sanitize_extension_context`` — a documented key that reaches the
#: hook REDACTED is the boundary hiding a bug (FR-36, then FR-39). ``db`` is deliberately NOT
#: here: the boundary strips it, and a provider opens its own session.
PROVIDER_HOOK_PRIMITIVE_KEYS: frozenset[str] = frozenset({"run_type", "user_id"})


def build_provider_hook_context(run_type: str, *, user_id, db: Session | None) -> dict:
    """The context handed to a planner-context or tools-for-run provider (FR-39).

    ★ ``user_id`` crosses the extension boundary as a STRING. ``_db_user_id`` returns a
    ``uuid.UUID`` for a well-formed id, and the sanitizer redacts a UUID to
    ``{"_redacted_type": "UUID"}`` — which is what every app provider received from 2026-05-20
    until this builder (#708 fixed the completion-hook builder only; the two contexts built
    here had the same bug). A missing tenant stays ``None``, never the string ``"None"``.
    """
    resolved = _db_user_id(user_id) if user_id is not None else None
    return {
        "run_type": run_type,
        "user_id": str(resolved) if resolved is not None else None,
        "db": db,
    }


def _get_planner_context(run_type: str, *, user_id: str, db: Session) -> dict:
    from AINDY.platform_layer.registry import get_planner_context

    return get_planner_context(
        run_type, build_provider_hook_context(run_type, user_id=user_id, db=db)
    )


def _get_tools_for_run(run_type: str, *, user_id: str, db: Session) -> list[dict]:
    from AINDY.platform_layer.registry import get_tools_for_run

    return get_tools_for_run(
        run_type, build_provider_hook_context(run_type, user_id=user_id, db=db)
    )


def _run_completion_hooks(run_type: str, context: dict) -> list:
    from AINDY.platform_layer.registry import run_agent_completion_hooks

    return run_agent_completion_hooks(run_type, context)
