"""Server-side MCP interop (ECOGAP-4 / G4b): expose AINDY syscalls as MCP tools to
external MCP clients (Claude Desktop, ChatGPT, etc.).

v1 scope: stdio transport, single configured identity, read-only tools by default.
Run it as a standalone process the MCP client spawns:

    aindy-runtime mcp-server --transport stdio

Requires DATABASE_URL and AINDY_MCP_SERVER_USER_ID.

Design (verified against the runtime seams):
  * Exposes an allowlist of **syscalls** — the rich, schema'd capability surface. Each MCP
    tool handler calls ``dispatch_syscall(name, args, user_id=<configured>)``, which grants
    the syscall its own capability (least-privilege, SDK-SYSCALL-GRANT-1) and lets the
    handler open/close its own DB session. The allowlist is the gate.
  * **Single configured identity:** every external call acts as ``AINDY_MCP_SERVER_USER_ID``.
    Correct for the canonical local single-operator case (Claude Desktop on your machine).
    Per-session auth (multi-tenant, via ``NodusServer.auth_hook`` + minted capability tokens)
    is the deferred G4a path.
  * **Read-only by default.** Writes (memory.write/delete, flow.run, event.emit) require
    ``AINDY_MCP_SERVER_ALLOW_WRITES=true``. ``AINDY_MCP_SERVER_TOOLS`` overrides the allowlist
    with an explicit comma-separated list.
  * **SSE transport deferred:** ``nodus_mcp_aindy.NodusServer.run_sse_app`` omits the
    ``/messages/`` POST mount (nodus-mcp #7); stdio is unaffected.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Read-only syscalls (memory.read capability) — the safe default exposure.
READ_ONLY_SYSCALLS = (
    "sys.v1.memory.read",
    "sys.v1.memory.search",
    "sys.v1.memory.list",
    "sys.v1.memory.tree",
    "sys.v1.memory.trace",
)
# Write / side-effecting syscalls — opt-in only.
WRITE_SYSCALLS = (
    "sys.v1.memory.write",
    "sys.v1.memory.delete",
    "sys.v1.flow.run",
    "sys.v1.event.emit",
)


def resolve_identity() -> str:
    """The single configured identity all external MCP calls act as. Required."""
    user_id = os.getenv("AINDY_MCP_SERVER_USER_ID", "").strip()
    if not user_id:
        raise RuntimeError(
            "AINDY_MCP_SERVER_USER_ID is required — the MCP server runs every external call "
            "as this configured identity. Set it to a registered user's id."
        )
    return user_id


def _writes_enabled() -> bool:
    return os.getenv("AINDY_MCP_SERVER_ALLOW_WRITES", "").strip().lower() in {"1", "true", "yes"}


def resolve_allowlist() -> list[str]:
    """Which syscalls to expose. Explicit override wins; else read-only (+ writes if enabled)."""
    explicit = os.getenv("AINDY_MCP_SERVER_TOOLS", "").strip()
    if explicit:
        return [s.strip() for s in explicit.split(",") if s.strip()]
    allow = list(READ_ONLY_SYSCALLS)
    if _writes_enabled():
        allow += list(WRITE_SYSCALLS)
    return allow


def _make_handler(syscall_name: str, user_id: str):
    """A sync MCP tool handler that dispatches one syscall as the configured identity."""

    def _handler(args: dict) -> dict:
        from AINDY.kernel.syscall_dispatcher import dispatch_syscall

        # db=None → the syscall handler opens/closes its own session. Least-privilege
        # capability is inferred from the syscall name inside dispatch_syscall.
        return dispatch_syscall(syscall_name, args or {}, user_id=user_id)

    return _handler


def build_registry(user_id: str, allowlist: list[str]):
    """Build a nodus_mcp_aindy ToolRegistry exposing the allowlisted syscalls."""
    from nodus_mcp_aindy import ToolRegistry, syscall_entry_to_tool
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    registry = ToolRegistry()
    exposed: list[str] = []
    for name in allowlist:
        entry = SYSCALL_REGISTRY.get(name)
        if entry is None:
            logger.warning("[mcp-server] allowlisted syscall %r is not registered; skipping", name)
            continue
        registry.register(syscall_entry_to_tool(name, entry, handler=_make_handler(name, user_id)))
        exposed.append(name)
    logger.info("[mcp-server] exposing %d syscall(s): %s", len(exposed), ", ".join(exposed) or "(none)")
    return registry


def serve_stdio() -> None:
    """Build the registry from config and serve MCP over stdio (blocks until the client exits)."""
    from nodus_mcp_aindy import NodusServer

    user_id = resolve_identity()
    registry = build_registry(user_id, resolve_allowlist())
    if not registry.names():
        raise RuntimeError(
            "[mcp-server] no tools to expose — check AINDY_MCP_SERVER_TOOLS / the allowlist"
        )
    NodusServer(registry, name="aindy-runtime").run(transport="stdio")
