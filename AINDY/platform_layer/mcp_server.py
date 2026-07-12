"""Server-side MCP interop (ECOGAP-4 / G4b): expose AINDY syscalls as MCP tools to
external MCP clients (Claude Desktop, ChatGPT, etc.).

Two transports:

    aindy-runtime mcp-server --transport stdio            # single local operator
    aindy-runtime mcp-server --transport sse --host ... --port ...   # remote / multi-tenant

Requires DATABASE_URL. stdio requires AINDY_MCP_SERVER_USER_ID.

Design (verified against the runtime seams):
  * Exposes an allowlist of **syscalls** — the rich, schema'd capability surface. Each MCP
    tool handler calls ``dispatch_syscall(name, args, user_id=<identity>)``, which grants
    the syscall its own capability (least-privilege, SDK-SYSCALL-GRANT-1) and lets the
    handler open/close its own DB session. The allowlist is the gate.
  * **Identity model — two modes:**
      - *Single configured identity* (stdio, or SSE without multi-tenant): every external
        call acts as ``AINDY_MCP_SERVER_USER_ID``. Correct for the canonical local
        single-operator case (Claude Desktop on your machine).
      - *Per-session identity* (MEB-3a, SSE + ``AINDY_MCP_SERVER_MULTI_TENANT=true``): the
        ``auth_hook`` resolves each session's ``Authorization: Bearer <jwt>`` or
        ``X-Platform-Key`` header to a real user via the existing auth surface
        (``decode_access_token`` / platform-key), and every call dispatches as *that*
        identity. Fail-closed: a call with no resolvable identity is denied. The syscall
        dispatcher then enforces per-syscall capability + tenant isolation for that user.
  * **Read-only by default.** Writes (memory.write/delete, flow.run, event.emit) require
    ``AINDY_MCP_SERVER_ALLOW_WRITES=true``. ``AINDY_MCP_SERVER_TOOLS`` overrides the allowlist
    with an explicit comma-separated list.
  * **Per-session identity requires SSE** — over stdio the MCP request context carries no
    per-request headers, so multi-tenant is meaningful only over the SSE/HTTP transport
    (nodus-mcp>=0.1.2: ``/messages/`` mount #7 + ``auth_hook`` header context #8).

MEB-3b (EffectRecord tenant/session attribution columns) records *which* tenant/session
produced each effect. The per-session ``auth_hook`` here stashes the resolved identity +
session id ambiently (``effect_ledger.set_effect_attribution``); any effect record written
under the call (e.g. an ``EXACTLY_ONCE`` syscall) is attributed to that tenant/session. It
is attribution/audit only and not required for the per-session identity mapping.
"""
from __future__ import annotations

import contextvars
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# The per-call identity resolved by the SSE auth_hook. None → fall back to the single
# configured identity. Set inside _call_tool's auth_hook and read by the tool handler in
# the same coroutine frame (contextvars propagate within one execution context).
_SESSION_IDENTITY: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "aindy_mcp_session_identity", default=None
)

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
    """The single configured identity all external MCP calls act as. Required for stdio."""
    user_id = os.getenv("AINDY_MCP_SERVER_USER_ID", "").strip()
    if not user_id:
        raise RuntimeError(
            "AINDY_MCP_SERVER_USER_ID is required — the MCP server runs every external call "
            "as this configured identity. Set it to a registered user's id."
        )
    return user_id


def multi_tenant_enabled() -> bool:
    """Per-session identity mode (MEB-3a). Only meaningful over the SSE transport."""
    return os.getenv("AINDY_MCP_SERVER_MULTI_TENANT", "").strip().lower() in {"1", "true", "yes"}


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


def _resolve_session_identity(context: dict) -> Optional[str]:
    """Map an MCP session's request headers to a real AINDY user id, or None.

    Reuses the runtime's existing per-user auth surface — an ``Authorization: Bearer <jwt>``
    or ``X-Platform-Key`` header — rather than inventing a new one. Returns None when no
    credential header is present; raises (via the auth service) when a credential is present
    but invalid, so the caller can deny fail-closed.
    """
    raw = (context or {}).get("headers") or {}
    headers = {str(k).lower(): v for k, v in raw.items()}
    auth = str(headers.get("authorization", "") or "")
    platform_key = str(headers.get("x-platform-key", "") or "")
    if not auth and not platform_key:
        return None

    from AINDY.db.database import SessionLocal
    from AINDY.services import auth_service

    db = SessionLocal()
    try:
        if auth.lower().startswith("bearer "):
            token = auth[len("bearer "):].strip()
            payload = auth_service.decode_access_token(token)
            resolved = auth_service._resolve_authenticated_jwt_user(payload, db)
            return resolved.get("user_id")
        if platform_key:
            resolved = auth_service._resolve_platform_key_as_user(platform_key, db)
            return resolved.get("user_id")
    finally:
        db.close()
    return None


def _session_token(context: dict) -> Optional[str]:
    """A stable-per-connection session identifier string for effect attribution (MEB-3b).

    The MCP request context surfaces ``session`` — the ``ServerSession`` object, stable for
    the life of one SSE connection. We stringify its identity as ``mcp:<id>``; this is
    process-local (an ``id()`` distinguishes concurrent sessions within one server process,
    which is what "which session produced this effect" needs for audit). Returns None when
    no session object is present (e.g. stdio).
    """
    sess = (context or {}).get("session")
    if sess is None:
        return None
    return f"mcp:{id(sess)}"


def build_auth_hook():
    """auth_hook for per-session (multi-tenant) identity: resolve the caller and stash it.

    Fail-closed: a call whose headers resolve to no identity is denied. Raising any
    exception makes NodusServer refuse the tool call. Also stashes the session id ambiently
    (MEB-3b) so any effect record written under this call is attributed to the session.
    """

    def _auth_hook(name: str, args: dict, context: dict) -> None:
        try:
            identity = _resolve_session_identity(context)
        except Exception as exc:
            logger.warning("[mcp-server] identity resolution failed for %r: %s", name, exc)
            raise PermissionError("MCP call denied: invalid credentials") from exc
        if not identity:
            raise PermissionError(
                "MCP call denied: no identity — send Authorization: Bearer <jwt> or X-Platform-Key"
            )
        _SESSION_IDENTITY.set(identity)
        # MEB-3b — attribute effects written under this call to the tenant (resolved
        # identity) + session. tenant_id is also set at the dispatcher gate from the
        # SyscallContext, but setting it here covers any effect-boundary write reached in
        # this execution context; the contextvar propagates down into dispatch_syscall.
        from AINDY.kernel.effect_ledger import set_effect_attribution

        set_effect_attribution(tenant_id=str(identity), session_id=_session_token(context))

    return _auth_hook


def _make_handler(syscall_name: str, configured_user_id: Optional[str]):
    """A sync MCP tool handler that dispatches one syscall.

    Dispatches as the per-session identity when one was resolved (multi-tenant SSE), else
    as the single configured identity (stdio / non-multi-tenant SSE).
    """

    def _handler(args: dict) -> dict:
        from AINDY.kernel.syscall_dispatcher import dispatch_syscall

        user_id = _SESSION_IDENTITY.get() or configured_user_id
        if not user_id:
            raise RuntimeError("no identity resolved for this MCP call")
        # db=None → the syscall handler opens/closes its own session. Least-privilege
        # capability is inferred from the syscall name inside dispatch_syscall.
        return dispatch_syscall(syscall_name, args or {}, user_id=user_id)

    return _handler


def build_registry(configured_user_id: Optional[str], allowlist: list[str]):
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
        registry.register(
            syscall_entry_to_tool(name, entry, handler=_make_handler(name, configured_user_id))
        )
        exposed.append(name)
    logger.info("[mcp-server] exposing %d syscall(s): %s", len(exposed), ", ".join(exposed) or "(none)")
    return registry


def _build_server(*, name: str = "aindy-runtime"):
    """Assemble the NodusServer from config. Returns (server, mode_description)."""
    from nodus_mcp_aindy import NodusServer

    multi = multi_tenant_enabled()
    # In multi-tenant mode the identity is per-session, so a configured fallback is optional.
    configured = os.getenv("AINDY_MCP_SERVER_USER_ID", "").strip() or None
    registry = build_registry(configured, resolve_allowlist())
    if not registry.names():
        raise RuntimeError(
            "[mcp-server] no tools to expose — check AINDY_MCP_SERVER_TOOLS / the allowlist"
        )
    auth_hook = build_auth_hook() if multi else None
    server = NodusServer(registry, name=name, auth_hook=auth_hook)
    mode = "per-session identity (multi-tenant)" if multi else "single configured identity"
    return server, mode


def serve_stdio() -> None:
    """Build the registry from config and serve MCP over stdio (blocks until the client exits)."""
    if multi_tenant_enabled():
        raise RuntimeError(
            "AINDY_MCP_SERVER_MULTI_TENANT is only meaningful over the SSE transport — stdio "
            "carries no per-request headers. Use --transport sse, or unset the flag for stdio."
        )
    resolve_identity()  # stdio requires the single configured identity — fail early if unset
    server, mode = _build_server()
    logger.info("[mcp-server] stdio, %s", mode)
    server.run(transport="stdio")


def serve_sse(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Serve MCP over SSE/HTTP (blocks). Supports per-session multi-tenant identity."""
    import uvicorn

    if not multi_tenant_enabled():
        # Non-multi-tenant SSE still needs a single identity to act as.
        resolve_identity()
    server, mode = _build_server()
    logger.info("[mcp-server] SSE on %s:%d, %s", host, port, mode)
    uvicorn.run(server.run_sse_app(), host=host, port=port)
