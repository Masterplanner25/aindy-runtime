"""One-shot subprocess that executes a single registered tool (TOOL-SEAM-ISOLATION-1 step C2).

Protocol: read one JSON request from **stdin**, write one JSON response to **stdout**, exit.
Deliberately the same shape as ``nodus_worker.main`` — the runtime already has this pattern and a
second framing would be a second thing to get wrong.

    request   {"tool_name": str, "args": dict, "user_id": str}
    response  {"ok": true,  "result": <json>}
              {"ok": false, "error": str}

★ **Authority is NOT re-evaluated here, and that is deliberate.** The parent's ``execute_tool``
has already checked token, granted tools, capabilities, policy, rate limit, egress and secret
scope before delegating. This worker resolves the function and runs it — nothing more. Re-running
the checks would put the authority decision inside the very process the boundary exists to
distrust, and calling ``execute_tool`` here would recurse: it routes declared tools *to a worker*.

★ **``db`` is ``None``, by measurement not assumption.** All 18 tool functions that exist take a
``db`` parameter and **none of them uses it** (`TOOL-SEAM-ISOLATION-1` step A). A live SQLAlchemy
session cannot cross a process boundary anyway, so a tool that genuinely needs data must reach
through a syscall — which is what every app tool already does. A tool that touches ``db`` here
fails loudly rather than silently receiving a broken object.

★ **stdout is the protocol channel.** Anything printed corrupts the response frame. Use the
module logger (which goes to stderr, captured by the parent) — never ``print()``. The Nodus worker
learned this the same way.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)


def run_one(request: dict[str, Any]) -> dict[str, Any]:
    """Resolve and execute one tool. Never raises — every outcome is a response dict."""
    tool_name = str(request.get("tool_name") or "")
    args = request.get("args") or {}
    user_id = str(request.get("user_id") or "")

    if not tool_name:
        return {"ok": False, "error": "no tool_name in request"}

    try:
        from AINDY.agents.tool_registry import TOOL_REGISTRY, _ensure_tools_loaded

        # The plugin stack is not loaded in a fresh subprocess; app-registered tools live
        # behind it. Idempotent and memoized (see nodus_worker's dispatch_worker_syscall,
        # which solves the same problem for the sys() seam).
        _ensure_tools_loaded()
        entry = TOOL_REGISTRY.get(tool_name)
        if entry is None:
            return {
                "ok": False,
                "error": (
                    f"tool {tool_name!r} is not registered in this worker. The parent resolved "
                    f"it, so the worker's plugin stack differs from the parent's — that is a "
                    f"deployment problem, not a missing tool."
                ),
            }

        result = entry["fn"](args=args, user_id=user_id, db=None)
    except Exception as exc:  # noqa: BLE001 — every failure becomes a response
        logger.warning("[ToolWorker] %s raised: %s", tool_name, exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        json.dumps(result)
    except (TypeError, ValueError):
        # ★ Here this MUST fail, where the in-process seam only counts it (step C1). The value
        # cannot cross the pipe, so there is nothing to return — and unlike the in-process case
        # the effect has landed inside a confined worker whose result we cannot carry back.
        # C1's counter exists precisely so this is known before a tool is moved here.
        return {
            "ok": False,
            "error": (
                f"tool {tool_name!r} returned {type(result).__name__}, which does not marshal "
                f"across the worker boundary. Watch "
                f"aindy_tool_return_contract_violations_total before declaring isolation."
            ),
        }

    return {"ok": True, "result": result}


def main() -> int:
    raw = sys.stdin.read()
    try:
        request = json.loads(raw or "{}")
    except (TypeError, ValueError) as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": f"unreadable request: {exc}"}))
        return 0
    sys.stdout.write(json.dumps(run_one(request)))
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
