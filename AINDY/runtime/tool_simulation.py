"""
runtime/tool_simulation.py - Shadow tool-execution seam (AGENT-HARDEN-4).

Effect simulation / true dry-run. ``simulate_agent_tool`` is the shadow of
``nodus_worker.run_agent_tool``: it enforces the SAME read-only capability gate,
then returns a predicted ``{success, result, error}`` and a structured
"would-write" intent **without invoking the real tool** — zero side effects.

Routed in at the capability-enforced ``call_tool`` seam when a run executes in
simulate mode, it lets a full plan run through the real VM + plan compilation +
control flow with every tool shadowed, producing a predicted-effect report.

v1 prediction is deterministic (tool name, args, declared risk, capability
verdict). A predictor/verifier model returning model-predicted outputs is the
documented upgrade path — the seam does not change.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _tool_risk(tool_name: str) -> Optional[str]:
    try:
        from AINDY.agents.tool_registry import TOOL_REGISTRY

        meta = TOOL_REGISTRY.get(tool_name)
        if isinstance(meta, dict):
            return meta.get("risk")
    except Exception:
        pass
    return None


def simulate_agent_tool(
    tool_name: str,
    args: Any,
    *,
    user_id: str,
    run_id: str,
    execution_token: Optional[dict],
    session_factory: Any = None,
) -> dict[str, Any]:
    """Predict a tool's effect with ZERO execution.

    Same fail-closed capability contract as ``run_agent_tool`` (a scoped token is
    required; ``check_tool_capability`` validates token + grants), but the real
    tool is never called. Returns::

        {
          "call_result": {"success", "result", "error"},   # what the script sees
          "would_write": { ... structured intent ... },     # the predicted effect
        }

    ``call_result.success`` is True on a capability-permitted simulated call so the
    surrounding plan keeps flowing (downstream steps also simulate); a
    capability-denied call returns success=False, mirroring real enforcement.
    """
    tool_args = dict(args) if isinstance(args, dict) else {}
    tool = str(tool_name)

    if not execution_token:
        error = "tool execution requires a capability token"
        return {
            "call_result": {"success": False, "result": None, "error": error},
            "would_write": {
                "tool": tool, "args": tool_args, "risk_level": _tool_risk(tool),
                "capability_ok": False, "capability_error": error,
                "predicted_result": None, "executed": False,
            },
        }

    # Read-only capability gate — same check the real seam enforces, no side effect.
    capability_ok = True
    capability_error: Optional[str] = None
    if session_factory is None:
        from AINDY.db.database import SessionLocal as session_factory

    db = session_factory()
    try:
        from AINDY.agents.capability_service import check_tool_capability

        verdict = check_tool_capability(
            token=execution_token, run_id=run_id, user_id=user_id, tool_name=tool
        )
        capability_ok = bool(verdict.get("ok"))
        capability_error = verdict.get("error")
    except Exception as exc:  # fail-closed: an errored gate is a denied simulation
        capability_ok = False
        capability_error = str(exc)
    finally:
        with contextlib.suppress(Exception):
            db.close()

    predicted_result = {
        "simulated": True,
        "tool": tool,
        "note": "predicted effect — no real side effect executed",
    }
    would_write = {
        "tool": tool,
        "args": tool_args,
        "risk_level": _tool_risk(tool),
        "capability_ok": capability_ok,
        "capability_error": capability_error,
        "predicted_result": predicted_result if capability_ok else None,
        "executed": False,  # invariant: simulation never runs the real tool
    }

    if not capability_ok:
        return {
            "call_result": {"success": False, "result": None, "error": capability_error},
            "would_write": would_write,
        }

    return {
        "call_result": {"success": True, "result": predicted_result, "error": None},
        "would_write": would_write,
    }
