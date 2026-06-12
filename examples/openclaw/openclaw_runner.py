"""
OpenClaw Infinite Weave runner — bootstrap + tool wiring + agent loop.

This module shows the three-step Infinite Weave pattern:

  1. Bootstrap  — seed SOUL.md / IDENTITY.md / AGENTS.md as pgvector memory nodes
                  (replaces OpenClaw's per-boot workspace file injection)

  2. Tools      — register 4 OpenClaw-equivalent skills as aindy host functions
                  (replaces Markdown skill files discovered from ~/.openclaw/skills/)

  3. Run        — execute openclaw_agent.nd via NodusRuntime
                  (replaces the pi-agent-core embedded LLM loop)

Production path:
  - bootstrap_workspace_memory() calls AINDY.memory.memory_ingest_service
    so nodes are embedded and persisted in Postgres/pgvector.
  - Tool handlers call AINDY syscalls (sys.v1.memory.read, sys.v1.job.submit, etc.)
    instead of the stubs below.
  - The runner is invoked by a flow node (sys.v1.nodus.execute) rather than
    directly, so the AgentRun lifecycle, ExecutionUnit metrics, and EventBus
    are all wired in automatically.

Standalone demo (no live aindy-runtime required):
  python openclaw_runner.py "search for the latest Python news"
"""
from __future__ import annotations

import json
import os
import pathlib
from typing import Any

# ---------------------------------------------------------------------------
# 1. Bootstrap — workspace files → memory nodes
# ---------------------------------------------------------------------------

def bootstrap_workspace_memory(
    user_id: str,
    workspace_dir: str,
    *,
    force: bool = False,
) -> list[str]:
    """Seed SOUL.md / IDENTITY.md / AGENTS.md as aindy memory nodes.

    In OpenClaw these files are loaded from disk into the system prompt
    on every agent boot. Infinite Weave persists them once as high-significance
    memory nodes so they are semantically retrievable across sessions and
    updatable without restarting the server.

    Returns a list of node types that were ingested.
    """
    try:
        from AINDY.db.database import SessionLocal
        from AINDY.memory.memory_ingest_service import ingest_memory_node
        _live = True
    except ImportError:
        _live = False

    workspace_files = {
        "soul":     ("SOUL.md",     ["soul", "identity", "persona", "openclaw"]),
        "identity": ("IDENTITY.md", ["identity", "persona", "openclaw"]),
        "context":  ("AGENTS.md",   ["context", "workspace", "openclaw"]),
    }

    ingested: list[str] = []

    for node_type, (filename, tags) in workspace_files.items():
        path = pathlib.Path(workspace_dir) / filename
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            continue

        mas_path = f"/memory/{user_id}/openclaw/{node_type}/bootstrap"

        if _live:
            db = SessionLocal()
            try:
                ingest_memory_node(
                    db=db,
                    user_id=user_id,
                    content=content,
                    tags=tags,
                    node_type=node_type,
                    significance=0.9,
                    path=mas_path,
                )
                db.commit()
            finally:
                db.close()
        else:
            print(f"[bootstrap] stub — would ingest {filename} ({len(content)} chars) → {mas_path}")

        ingested.append(node_type)

    return ingested


# ---------------------------------------------------------------------------
# 2. OpenClaw skills → aindy host functions
# ---------------------------------------------------------------------------
# Each function below is an aindy-native equivalent of an OpenClaw Markdown
# skill.  In production each one calls an aindy syscall.  The stubs here let
# the example run standalone (no live server needed).

def tool_recall_memory(query: Any) -> dict:
    """Semantic recall across all past sessions.

    OpenClaw equivalent: the JSONL session-transcript search.
    Infinite Weave: sys.v1.memory.read with pgvector similarity.
    """
    query_str = str(query) if not isinstance(query, dict) else str(query.get("query", ""))
    try:
        from AINDY.db.database import SessionLocal
        from AINDY.kernel.syscall_dispatcher import dispatch_syscall
        db = SessionLocal()
        try:
            result = dispatch_syscall(
                "sys.v1.memory.search",
                {"query": query_str, "limit": 5},
                db=db,
                user_id="",
            )
            return result.get("data") or {"nodes": [], "count": 0}
        finally:
            db.close()
    except Exception:
        return {"nodes": [], "count": 0, "query": query_str}


def tool_web_search(query: Any) -> dict:
    """Brave Search skill equivalent.

    OpenClaw: the brave-search Markdown skill invokes the Brave API.
    Infinite Weave: same HTTP call, result cached as a memory node.

    Stub returns a placeholder so the example runs without a Brave API key.
    """
    query_str = str(query) if not isinstance(query, dict) else str(query.get("query", query))
    return {
        "results": [f"[stub] Web result for: {query_str}"],
        "count": 1,
        "query": query_str,
    }


def tool_schedule_reminder(payload: Any) -> dict:
    """Aindy SchedulerEngine job — replaces OpenClaw's cron tool.

    OpenClaw: `openclaw cron add "0 9 * * *" "daily summary"` or the
    `/schedule` command from a channel.
    Infinite Weave: sys.v1.job.submit with a delay, persisted in DB.
    """
    p = payload if isinstance(payload, dict) else {}
    try:
        from AINDY.db.database import SessionLocal
        from AINDY.kernel.syscall_dispatcher import dispatch_syscall
        db = SessionLocal()
        try:
            result = dispatch_syscall(
                "sys.v1.job.submit",
                {
                    "job_type": "reminder",
                    "payload": {
                        "message": p.get("message", ""),
                        "user_id": p.get("user_id", ""),
                    },
                    "delay_seconds": 3600,
                },
                db=db,
                user_id=p.get("user_id", ""),
            )
            if result.get("status") == "success":
                return {"status": "scheduled", "job_id": result["data"].get("job_id", "")}
            return {"status": "error", "error": result.get("error", "dispatch failed")}
        finally:
            db.close()
    except Exception as exc:
        return {"status": "scheduled", "stub": True, "message": str(p.get("message", ""))}


def tool_remember_turn(payload: Any) -> dict:
    """Persist a conversation turn as a pgvector memory node.

    OpenClaw: writes JSONL to ~/.openclaw/workspace/memory/ via session-memory hook.
    Infinite Weave: sys.v1.memory.write — durable, cross-session, semantically indexed.
    """
    p = payload if isinstance(payload, dict) else {"content": str(payload)}
    try:
        from AINDY.db.database import SessionLocal
        from AINDY.kernel.syscall_dispatcher import dispatch_syscall
        db = SessionLocal()
        try:
            result = dispatch_syscall(
                "sys.v1.memory.write",
                {
                    "content": p.get("content", ""),
                    "tags": p.get("tags", ["conversation", "openclaw"]),
                    "node_type": p.get("node_type", "conversation"),
                    "significance": 0.6,
                    "namespace": "openclaw",
                },
                db=db,
                user_id=p.get("user_id", ""),
            )
            return {"status": "ok", "node_id": (result.get("data") or {}).get("node_id", "")}
        finally:
            db.close()
    except Exception:
        return {"status": "ok", "stub": True}


# ---------------------------------------------------------------------------
# 3. Run the agent loop
# ---------------------------------------------------------------------------

def run_openclaw_agent(
    message: str,
    *,
    user_id: str = "demo-user",
    session_id: str | None = None,
    workspace_dir: str | None = None,
    bootstrap: bool = True,
) -> dict:
    """Run one OpenClaw agent turn via Nodus.

    Args:
        message:       Incoming user message.
        user_id:       Authenticated user identifier.
        session_id:    Conversation session key. Defaults to ``openclaw-{user_id}``.
        workspace_dir: Directory containing SOUL.md / IDENTITY.md / AGENTS.md.
                       Defaults to the current working directory.
        bootstrap:     Whether to seed workspace files as memory nodes on first run.

    Returns:
        The NodusRuntime result dict ``{"ok": bool, "stdout": str, ...}``.
    """
    from nodus.runtime.embedding import NodusRuntime

    session_id = session_id or f"openclaw-{user_id}"
    workspace_dir = workspace_dir or os.getcwd()

    # Seed workspace files on first call (idempotent in production via MAS path).
    if bootstrap:
        ingested = bootstrap_workspace_memory(user_id, workspace_dir)
        if ingested:
            print(f"[runner] bootstrapped workspace memory: {ingested}")

    # Build the runtime with the four skill-equivalent host functions.
    agent_state: dict[str, Any] = {}

    runtime = NodusRuntime(allowed_paths=None)
    runtime.register_function("recall_memory",      tool_recall_memory,     arity=1)
    runtime.register_function("web_search",         tool_web_search,        arity=1)
    runtime.register_function("schedule_reminder",  tool_schedule_reminder, arity=1)
    runtime.register_function("remember_turn",      tool_remember_turn,     arity=1)
    runtime.register_function("set_state",          lambda k, v: agent_state.update({str(k): v}), arity=2)
    runtime.register_function("get_state",          lambda k: agent_state.get(str(k)),             arity=1)

    script_path = pathlib.Path(__file__).parent / "openclaw_agent.nd"

    result = runtime.run_file(
        str(script_path),
        initial_globals={
            "message":    message,
            "user_id":    user_id,
            "session_id": session_id,
        },
    )

    result["agent_state"] = agent_state
    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    msg = " ".join(sys.argv[1:]).strip() or "Hello! Who are you?"
    print(f"[openclaw] message: {msg!r}\n")

    result = run_openclaw_agent(msg, bootstrap=True)

    if result.get("ok"):
        print("[openclaw] agent stdout:")
        print(result.get("stdout", "").strip())
    else:
        print("[openclaw] agent error:")
        print(json.dumps(result.get("error") or result, indent=2))

    state = (result.get("extras") or {}).get("globals", {})
    print(f"\n[openclaw] persona_loaded={state.get('persona_loaded', '?')}  "
          f"history_turns={state.get('history_turns', '?')}")
