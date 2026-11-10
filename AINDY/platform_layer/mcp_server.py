"""Server-side MCP interop (ECOGAP-4 / G4b): expose AINDY syscalls as MCP tools to
external MCP clients (Claude Desktop, ChatGPT, etc.).

Three transports:

    aindy-runtime mcp-server --transport stdio            # single local operator
    aindy-runtime mcp-server --transport sse --host ... --port ...   # remote / multi-tenant
    aindy-runtime mcp-server --transport http --host ... --port ...  # streamable HTTP at /mcp (FR-66)

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
        **A platform key is held to its scopes** (DEC-126): the tool's declared capability
        must be one the key's scopes authorize (`key_scopes_authorize`, the map
        `POST /platform/syscall` uses). A session JWT is not scope-gated, as on that route.
  * **Read-only by default.** Writes (memory.write/delete, flow.run, event.emit) require
    ``AINDY_MCP_SERVER_ALLOW_WRITES=true``. ``AINDY_MCP_SERVER_TOOLS`` overrides the allowlist
    with an explicit comma-separated list, and **cannot bring in a write without that flag**
    (DEC-128): a syscall counts as a read only when it declares ``read_only=True``, and the
    server refuses to start naming any other.
  * **Per-session identity requires SSE or HTTP** — over stdio the MCP request context carries
    no per-request headers, so multi-tenant is meaningful only over SSE or streamable HTTP
    (nodus-mcp>=0.1.2: ``/messages/`` mount #7 + ``auth_hook`` header context #8; streamable
    HTTP needs nodus-mcp>=0.1.6, DEC-125).
  * **Streamable HTTP checks ``Host`` and ``Origin``** (DNS-rebinding protection). On a loopback
    bind only loopback names are accepted; on any other bind the server refuses to start unless
    ``AINDY_MCP_SERVER_ALLOWED_HOSTS`` lists the public host name(s) (DEC-127).

MEB-3b (EffectRecord tenant/session attribution columns) records *which* tenant/session
produced each effect. The per-session ``auth_hook`` here stashes the resolved identity +
session id ambiently (``effect_ledger.set_effect_attribution``); any effect record written
under the call (e.g. an ``EXACTLY_ONCE`` syscall) is attributed to that tenant/session. It
is attribution/audit only and not required for the per-session identity mapping.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import sys
from typing import Iterator, NamedTuple, Optional

logger = logging.getLogger(__name__)

# The per-call identity resolved by the SSE auth_hook. None → fall back to the single
# configured identity. Set inside _call_tool's auth_hook and read by the tool handler in
# the same coroutine frame (contextvars propagate within one execution context).
_SESSION_IDENTITY: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "aindy_mcp_session_identity", default=None
)
# The resolved caller's API-key scopes, set by the same hook. None = not scope-gated (a session
# JWT, or the single configured identity); a frozenset = a platform key, held to it (DEC-126).
_SESSION_SCOPES: contextvars.ContextVar[Optional[frozenset]] = contextvars.ContextVar(
    "aindy_mcp_session_scopes", default=None
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


class SessionPrincipal(NamedTuple):
    """Who an MCP call acts as, and what an API key limits it to.

    ``scopes`` is None for a session JWT (not scope-gated, as on ``POST /platform/syscall``) and
    the key's scopes for a platform key (DEC-126).
    """

    user_id: str
    scopes: Optional[frozenset]


def refuse_unflagged_writes(allowlist: list[str]) -> None:
    """DEC-128 — refuse to serve a write unless ``AINDY_MCP_SERVER_ALLOW_WRITES`` is set.

    A syscall is a read only when it declares ``read_only=True``; anything else counts as a write,
    so an app syscall that never said is refused, not guessed at. Checked after the app's syscalls
    are registered. A name that is not registered is left to ``build_registry``, which skips it.
    """
    if _writes_enabled():
        return
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    writes = [
        name for name in allowlist
        if (entry := SYSCALL_REGISTRY.get(name)) is not None and not getattr(entry, "read_only", False)
    ]
    if writes:
        raise RuntimeError(
            f"[mcp-server] refusing to expose {', '.join(writes)}: not declared read-only, so "
            "treated as a write. Set AINDY_MCP_SERVER_ALLOW_WRITES=true to serve writes, or "
            "register the syscall with read_only=True if it only reads (DEC-128)."
        )


def _resolve_session_principal(context: dict) -> Optional[SessionPrincipal]:
    """Map an MCP call's request headers to a real AINDY user, or None.

    Reuses the runtime's existing per-user auth surface — an ``Authorization: Bearer <jwt>``
    or ``X-Platform-Key`` header — rather than inventing a new one. Returns None when no
    credential header is present; raises (via the auth service) when a credential is present
    but invalid, so the caller can deny fail-closed. One database read resolves both the user
    and, for a platform key, its scopes.
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
            user_id = resolved.get("user_id")
            return SessionPrincipal(str(user_id), None) if user_id else None
        if platform_key:
            resolved = auth_service._resolve_platform_key_as_user(platform_key, db)
            user_id = resolved.get("user_id")
            if not user_id:
                return None
            return SessionPrincipal(str(user_id), frozenset(resolved.get("api_key_scopes") or ()))
    finally:
        db.close()
    return None


def _resolve_session_identity(context: dict) -> Optional[str]:
    """The user id alone, for callers that need only who (see `_resolve_session_principal`)."""
    principal = _resolve_session_principal(context)
    return principal.user_id if principal else None


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
        # Cleared first, so a refused call can never run on a previous call's identity or scopes.
        _SESSION_IDENTITY.set(None)
        _SESSION_SCOPES.set(None)
        try:
            principal = _resolve_session_principal(context)
        except Exception as exc:
            logger.warning("[mcp-server] identity resolution failed for %r: %s", name, exc)
            raise PermissionError("MCP call denied: invalid credentials") from exc
        if not principal:
            raise PermissionError(
                "MCP call denied: no identity — send Authorization: Bearer <jwt> or X-Platform-Key"
            )
        identity = principal.user_id
        _SESSION_IDENTITY.set(identity)
        _SESSION_SCOPES.set(principal.scopes)
        # MEB-3b — attribute effects written under this call to the tenant (resolved
        # identity) + session. tenant_id is also set at the dispatcher gate from the
        # SyscallContext, but setting it here covers any effect-boundary write reached in
        # this execution context; the contextvar propagates down into dispatch_syscall.
        from AINDY.kernel.effect_ledger import set_effect_attribution

        set_effect_attribution(tenant_id=str(identity), session_id=_session_token(context))

    return _auth_hook


def _refusal_envelope(syscall_name: str, message: str, failure_class: str) -> dict:
    """A refusal made before dispatch, in the envelope shape the dispatcher returns, so the
    MCP client sees one error contract whichever layer refused."""
    return {
        "status": "error",
        "data": {},
        "outcome": None,
        "trace_id": "",
        "execution_unit_id": "",
        "syscall": syscall_name,
        "version": (syscall_name.split(".") + ["unknown"])[1],
        "duration_ms": 0,
        "error": message,
        "failure_class": failure_class,
        "warning": None,
    }


def _make_handler(syscall_name: str, configured_user_id: Optional[str], capability: str):
    """A sync MCP tool handler that dispatches one syscall.

    Dispatches as the per-session identity when one was resolved (multi-tenant SSE), else
    as the single configured identity (stdio / non-multi-tenant SSE).

    ``capability`` is the one the syscall declares (DEC-112). Left to inference from the name,
    ``sys.v1.memory.search`` was granted ``memory.search`` and refused, because it declares
    ``memory.read``.
    """

    def _handler(args: dict) -> dict:
        from AINDY.kernel.payload_identity import PayloadIdentityViolation, bind_payload_user_id
        from AINDY.kernel.resource_manager import ResourceLimitError, get_resource_manager
        from AINDY.kernel.syscall_dispatcher import dispatch_syscall

        from AINDY.auth.api_key_auth import key_scopes_authorize

        user_id = _SESSION_IDENTITY.get() or configured_user_id
        if not user_id:
            raise RuntimeError("no identity resolved for this MCP call")
        # DEC-126 — a platform key is held to its scopes on every transport, with the map
        # `POST /platform/syscall` uses. Refused before a unit is claimed.
        scopes = _SESSION_SCOPES.get()
        if scopes is not None and not key_scopes_authorize(capability, scopes):
            logger.warning(
                "[mcp-server] %s refused: the key's scopes %s do not authorize %r",
                syscall_name, sorted(scopes), capability,
            )
            return _refusal_envelope(
                syscall_name,
                f"API key scopes {sorted(scopes) or ['(none)']} do not authorize "
                f"'{capability}', which {syscall_name} requires",
                "permission",
            )
        # FR-53 (b) / DEC-099 — the arguments are written by the outside AI, so a `user_id`
        # among them is an assertion. It may repeat the caller's identity, never choose
        # another user: handlers that read `payload["user_id"]` would otherwise serve whoever
        # it names (cross-tenant under multi-tenant SSE). Refused before a unit is claimed.
        try:
            payload = bind_payload_user_id(args or {}, user_id, syscall=syscall_name)
        except PayloadIdentityViolation as exc:
            logger.warning("[mcp-server] %s refused: %s", syscall_name, exc)
            return _refusal_envelope(syscall_name, str(exc), "permission")
        # QUOTA-ACCRUAL-ORPHAN-1 — a transport that dispatches on its own has no pipeline
        # to claim an execution unit for it, so it owns one per call: admitted against the
        # identity's concurrency limit, started, and reaped when the call returns. Without
        # this every call minted a unit nothing cleared — one snapshot per tool call, with
        # no tenant, for the life of a stdio session. A refusal is returned in the same
        # envelope shape the dispatcher uses, so the MCP client sees one error contract.
        try:
            with get_resource_manager().owned_execution(str(user_id)) as eu_id:
                # db=None → the syscall handler opens/closes its own session.
                return dispatch_syscall(
                    syscall_name,
                    payload,
                    user_id=user_id,
                    capability=capability,
                    execution_unit_id=eu_id,
                )
        except ResourceLimitError as exc:
            logger.warning("[mcp-server] %s refused: %s", syscall_name, exc)
            return _refusal_envelope(syscall_name, str(exc), "transient")

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
            syscall_entry_to_tool(
                name, entry, handler=_make_handler(name, configured_user_id, entry.capability)
            )
        )
        exposed.append(name)
    logger.info("[mcp-server] exposing %d syscall(s): %s", len(exposed), ", ".join(exposed) or "(none)")
    return registry


@contextlib.contextmanager
def _stdout_to_stderr() -> Iterator[None]:
    """Point stdout — Python's and file descriptor 1 — at stderr for the duration.

    Over stdio, stdout IS the MCP channel: one stray line from a plugin's import or bootstrap
    and the client fails to parse the stream. The fd-level swap catches writes that bypass
    ``sys.stdout`` (C extensions, subprocesses inheriting the fd).
    """
    sys.stdout.flush()
    try:
        saved_fd = os.dup(1)
    except OSError:
        saved_fd = None
    try:
        if saved_fd is not None:
            os.dup2(2, 1)
        with contextlib.redirect_stdout(sys.stderr):
            yield
    finally:
        sys.stderr.flush()
        if saved_fd is not None:
            os.dup2(saved_fd, 1)
            os.close(saved_fd)


def load_app_syscalls() -> list[str]:
    """FR-53 (a) — register the selected manifest's syscalls in this process.

    App syscalls are registered by each plugin's ``bootstrap()``, which ``load_plugins()``
    runs. This process never called it, so it held the runtime's syscalls only and an app
    syscall named in ``AINDY_MCP_SERVER_TOOLS`` was skipped as "not registered". The manifest
    is chosen exactly as at boot (``AINDY_PLUGIN_MANIFEST`` / ``AINDY_BOOT_PROFILE``);
    runtime-only selects an empty one, so nothing changes there.

    A plugin that fails to load stops the server: serving a quietly smaller tool set than the
    operator configured is the silent-skip this replaces.
    """
    from AINDY.platform_layer.registry import load_plugins

    try:
        with _stdout_to_stderr():
            loaded = load_plugins()
    except Exception as exc:
        raise RuntimeError(f"[mcp-server] plugin load failed: {exc}") from exc
    logger.info("[mcp-server] loaded %d plugin module(s)", len(loaded or []))
    return list(loaded or [])


def _build_server(*, name: str = "aindy-runtime"):
    """Assemble the NodusServer from config. Returns (server, mode_description)."""
    from nodus_mcp_aindy import NodusServer

    multi = multi_tenant_enabled()
    load_app_syscalls()  # FR-53 (a) — before the allowlist is resolved against the registry
    # In multi-tenant mode the identity is per-session, so a configured fallback is optional.
    configured = os.getenv("AINDY_MCP_SERVER_USER_ID", "").strip() or None
    allowlist = resolve_allowlist()
    refuse_unflagged_writes(allowlist)  # DEC-128, after the app's syscalls are registered
    registry = build_registry(configured, allowlist)
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
            "AINDY_MCP_SERVER_MULTI_TENANT is only meaningful over the SSE or HTTP transport — "
            "stdio carries no per-request headers. Use --transport sse or http, or unset the "
            "flag for stdio."
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


_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")


def _csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def http_security_settings(host: str):
    """The ``Host``/``Origin`` check for the streamable HTTP app (DEC-127). Fails closed.

    * ``AINDY_MCP_SERVER_ALLOWED_HOSTS`` set: exactly those ``Host`` values (``name`` or
      ``name:port``; ``name:*`` for any port), and ``AINDY_MCP_SERVER_ALLOWED_ORIGINS`` for a
      browser ``Origin``. A request with no ``Origin`` (a server-side client) passes.
    * Otherwise, on a loopback bind: loopback names only, any port.
    * Otherwise: refuse to start. nodus-mcp's ``streamable_http_app()`` does no checking at all
      without settings, and behind a proxy the public name is not known here.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = _csv_env("AINDY_MCP_SERVER_ALLOWED_HOSTS")
    origins = _csv_env("AINDY_MCP_SERVER_ALLOWED_ORIGINS")
    if hosts:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
        )
    if host in _LOOPBACK_HOSTS:
        names = ("127.0.0.1", "localhost", "[::1]")
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[*names, *(f"{n}:*" for n in names)],
            allowed_origins=[
                *origins,
                *(f"http://{n}" for n in names),
                *(f"http://{n}:*" for n in names),
            ],
        )
    raise RuntimeError(
        f"--transport http on {host!r} needs AINDY_MCP_SERVER_ALLOWED_HOSTS: the public host "
        "name(s) clients reach this server by (e.g. mcp.example.com). Without it no Host check "
        "would run. Bind 127.0.0.1 to serve locally."
    )


def build_http_app(host: str = "127.0.0.1", path: str = "/mcp"):
    """Build the streamable HTTP app (FR-66 phase 1): the MCP endpoint at ``path``, stateless,
    with the ``Host``/``Origin`` check for ``host``. Returns (app, mode_description)."""
    if not multi_tenant_enabled():
        resolve_identity()  # a single identity to act as, as for SSE
    security = http_security_settings(host)  # before the build: refuse early
    server, mode = _build_server()
    if not hasattr(server, "streamable_http_app"):
        raise RuntimeError(
            "--transport http needs nodus-mcp>=0.1.6 (NodusServer.streamable_http_app). "
            "Upgrade: pip install -U 'aindy-runtime[mcp]'"
        )
    app = server.streamable_http_app(path=path, stateless=True, security_settings=security)
    return app, mode


def serve_http(host: str = "127.0.0.1", port: int = 8080, path: str = "/mcp") -> None:
    """Serve MCP over streamable HTTP (blocks). Supports per-session multi-tenant identity."""
    import uvicorn

    app, mode = build_http_app(host=host, path=path)
    logger.info("[mcp-server] streamable HTTP on %s:%d%s, %s", host, port, path, mode)
    uvicorn.run(app, host=host, port=port)
