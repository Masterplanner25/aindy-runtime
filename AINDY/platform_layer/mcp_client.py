"""Client-side MCP interop (ECOGAP-4 / G4b): register an external MCP server's tools
as AINDY agent tools so agents can call them through ``execute_tool``.

Opt-in and off by default. ``bootstrap()`` is invoked (memoized) from
``tool_registry._ensure_tools_loaded`` — the one entry point that runs in every
tool-executing process, including the nodus_worker subprocess — so MCP tools resolve
wherever ``execute_tool`` runs. It is a no-op unless ``AINDY_MCP_CLIENT_ENABLED`` is set
and ``AINDY_MCP_SERVERS`` lists at least one server. (It is wired there rather than via a
plugin manifest entry because the runtime ``platform-only`` profile must stay
manifest-empty — the "runtime boots clean without plugins" contract.)

Design notes (verified against the runtime seams):
  * Tools register via ``register_tool`` → ``TOOL_REGISTRY`` — the **executable** path.
    ``register_agent_tool`` (``_agent_tools``) is a discovery/metrics surface only and
    is never read by ``execute_tool``. Registering here means MCP tools inherit the
    capability gate in ``execute_tool`` and (once activated) G4a egress enforcement.
  * The tool ``fn`` is called synchronously as ``fn(args=, user_id=, db=)``, but the
    MCP client is fully async. All async work runs on one dedicated background event
    loop (``_run_sync``), so it is safe whether or not the caller is already inside a
    running loop — unlike ``run_until_complete``, which raises inside a live loop.
  * v1 connects per call (correctness over speed). A persistent per-server connection
    pool is a deferred optimization.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

# Dedicated capability for outbound MCP tool egress — distinct from outbound.http so
# G4a can gate MCP specifically. Registered in extension_capabilities for discovery.
MCP_EGRESS_CAPABILITY = "outbound.mcp"

# --- sync<->async bridge: one background event loop for all MCP calls ---------
_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            loop = asyncio.new_event_loop()
            threading.Thread(
                target=loop.run_forever, name="aindy-mcp-loop", daemon=True
            ).start()
            _loop = loop
        return _loop


def _run_sync(coro, *, timeout: float) -> Any:
    """Drive an async coroutine to completion from sync code, loop-safe.

    Runs on a separate background loop, so calling this from inside another running
    event loop does not raise (it blocks the calling thread on the result future).
    """
    future = asyncio.run_coroutine_threadsafe(coro, _get_loop())
    return future.result(timeout)


# --- config -------------------------------------------------------------------


def _is_enabled() -> bool:
    return os.getenv("AINDY_MCP_CLIENT_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def _parse_servers() -> list[dict]:
    """Parse AINDY_MCP_SERVERS: a JSON array of {name, url, timeout?, risk?}."""
    raw = os.getenv("AINDY_MCP_SERVERS", "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("[mcp] AINDY_MCP_SERVERS is not valid JSON: %s", exc)
        return []
    if not isinstance(data, list):
        logger.error("[mcp] AINDY_MCP_SERVERS must be a JSON array of {name, url, ...}")
        return []
    valid = [s for s in data if isinstance(s, dict) and s.get("url") and s.get("name")]
    if len(valid) != len(data):
        logger.warning(
            "[mcp] ignored %d AINDY_MCP_SERVERS entries missing 'name'/'url'",
            len(data) - len(valid),
        )
    return valid


# --- registration -------------------------------------------------------------


def _make_tool_fn(server_url: str, remote_name: str, timeout: float):
    """Build a sync AINDY tool fn that proxies to a remote MCP tool (per-call connect)."""

    def _fn(args: dict, user_id: str = None, db: Any = None) -> dict:
        from nodus_mcp_aindy import MCPClientAdapter

        async def _call() -> dict:
            adapter = MCPClientAdapter(server_url, timeout=timeout)
            await adapter.connect()
            try:
                return await adapter.call_tool(remote_name, args or {})
            finally:
                await adapter.disconnect()

        return _run_sync(_call(), timeout=timeout + 5.0)

    return _fn


def discover_and_register(server: dict) -> list[str]:
    """Discover one MCP server's tools and register each as an AINDY agent tool.

    Returns the namespaced tool names registered. Resilient: an unreachable server
    logs and returns ``[]`` rather than raising (so one bad server can't fail boot).
    """
    from AINDY.agents.tool_registry import register_tool
    from nodus_mcp_aindy import discover_tools

    name = str(server["name"])
    url = str(server["url"])
    timeout = float(server.get("timeout", 10.0))
    risk = str(server.get("risk", "high"))
    prefix = f"mcp_{name}_"

    try:
        remote_tools = _run_sync(discover_tools(url, timeout=timeout), timeout=timeout + 5.0)
    except Exception as exc:  # unreachable / handshake failure — skip this server
        logger.error("[mcp] discovery failed for server %r (%s): %s", name, url, exc)
        return []

    registered: list[str] = []
    for td in remote_tools:
        local_name = f"{prefix}{td.name}"
        register_tool(
            name=local_name,
            risk=risk,
            description=(getattr(td, "description", None) or f"MCP tool {td.name} from {name}"),
            capability=MCP_EGRESS_CAPABILITY,
            required_capability=MCP_EGRESS_CAPABILITY,
            category="mcp",
            egress_scope=name,
        )(_make_tool_fn(url, td.name, timeout))
        registered.append(local_name)

    logger.info("[mcp] registered %d tool(s) from server %r", len(registered), name)
    return registered


def bootstrap() -> None:
    """Register client-side MCP tools. No-op unless enabled + servers configured.

    Boot-safe: this runs during tool load, so it MUST NOT raise — an exception would
    surface as a tool-load failure. All failures are logged and swallowed; a broken or
    unreachable MCP server degrades to "no MCP tools", never a boot/tool-load failure.
    """
    try:
        if not _is_enabled():
            logger.debug("[mcp] client disabled (AINDY_MCP_CLIENT_ENABLED not set)")
            return
        servers = _parse_servers()
        if not servers:
            logger.warning("[mcp] client enabled but AINDY_MCP_SERVERS is empty or invalid")
            return
        total = sum(len(discover_and_register(server)) for server in servers)
        logger.info(
            "[mcp] client bootstrap complete: %d tool(s) across %d server(s)",
            total,
            len(servers),
        )
    except Exception as exc:  # never fail boot
        logger.error("[mcp] client bootstrap failed (continuing without MCP tools): %s", exc)
