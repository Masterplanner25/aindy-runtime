"""auth/api_key_auth.py — Platform API key scope constants."""
from __future__ import annotations


class Scopes:
    FLOW_READ       = "flow.read"
    FLOW_EXECUTE    = "flow.execute"
    MEMORY_READ     = "memory.read"
    MEMORY_WRITE    = "memory.write"
    MEMORY_DELETE   = "memory.delete"
    AGENT_RUN       = "agent.run"
    EXECUTION_READ  = "execution.read"
    EVENT_EMIT      = "event.emit"
    WEBHOOK_MANAGE  = "webhook.manage"
    PLATFORM_ADMIN  = "platform.admin"

    ALL: list[str] = [
        FLOW_READ,
        FLOW_EXECUTE,
        MEMORY_READ,
        MEMORY_WRITE,
        MEMORY_DELETE,
        AGENT_RUN,
        EXECUTION_READ,
        EVENT_EMIT,
        WEBHOOK_MANAGE,
        PLATFORM_ADMIN,
    ]

    #: HTTP-SCOPE-GAP-1 — the scopes a JWT *session* carries, derived from the user row.
    #
    # Sourced from the app team's real call surface (their answer to `APP_HANDOFF_v2.1.0.md`
    # §6), not from our guess: Tasks, MasterPlan, Genesis, memory, search, social and identity
    # between them do recall, node create/update, feedback, share and flow runs.
    #
    # ★ Derived from `User.is_admin`, deliberately NOT from a token claim. A `scopes` claim
    # would invalidate every live session on upgrade — 2.0.0 already did that once via
    # `purpose` — and would create a second source of truth for "is this person an operator",
    # which the app team explicitly asked us not to do.
    JWT_SESSION: list[str] = [
        FLOW_READ,
        FLOW_EXECUTE,
        MEMORY_READ,
        MEMORY_WRITE,
        AGENT_RUN,
        EXECUTION_READ,
    ]

    #: What an admin session adds. Their operator console (`client/src/api/operator.js`) does
    #: webhook CRUD, DLQ drain, user promotion and the execution graph.
    JWT_ADMIN_EXTRA: list[str] = [
        WEBHOOK_MANAGE,
        PLATFORM_ADMIN,
    ]

    #: Deliberately in NEITHER derived set: `memory.delete` (nothing in the client issues a
    #: DELETE against memory) and `event.emit` (nothing in the client emits directly). An API
    #: key can still be granted them explicitly; a browser session cannot inherit them.


#: Module-level alias so `grantable_scopes` reads without a forward reference to the class body.
PLATFORM_ADMIN_SCOPE = Scopes.PLATFORM_ADMIN


def grantable_scopes(current_user: dict) -> set[str]:
    """What scopes this caller may put on a **new** API key (`KEY-SCOPE-ESCALATION-1`).

    A `flow.read`-only API key could call `POST /platform/keys` asking for
    `["platform.admin", "memory.delete", "event.emit"]` and receive exactly that. The only
    validation was membership in `ALL` — *"is this a real scope"*, never *"may you grant it"*.
    Demonstrated end to end on PostgreSQL: the escalated key then listed every user and promoted
    its own account to `is_admin`, which persists in the user row after the key is revoked.

    The rule is delegation: **you cannot grant what you do not hold.**

    * an API key may grant only scopes it carries itself — no self-widening;
    * an ordinary session may grant only its derived scopes;
    * a holder of ``platform.admin`` may grant anything.

    That last branch is deliberate and is not a loophole. `platform.admin` already satisfies
    every scope gate and reaches `POST /platform/admin/users/{id}/promote`, so refusing it
    `memory.delete` on a key would be theatre — and it preserves the documented affordance that
    an API key *can* be granted `memory.delete`/`event.emit`, which no session inherits.
    """
    if current_user.get("auth_type") == "api_key":
        held = set(current_user.get("api_key_scopes") or [])
    else:
        held = set(derive_session_scopes(is_admin=bool(current_user.get("is_admin", False))))

    if PLATFORM_ADMIN_SCOPE in held:
        return set(Scopes.ALL)
    return held


def derive_session_scopes(*, is_admin: bool) -> list[str]:
    """Scopes for a JWT-authenticated session, derived from the user row.

    HTTP-SCOPE-GAP-1. Before this, `enforce_api_key_scope` gated API-key callers only —
    its own docstring said *"JWT users carry full trust and are never gated"* — which made
    an interactive browser session **strictly more privileged than any API key**.

    The admin set is keyed on `User.is_admin`, the flag the platform already uses, so there
    is exactly one answer to "is this person an operator". The app's UI already draws this
    line itself (`useAuth().isAdmin`, `<AdminAccessRequired />`) but **only in the frontend**;
    this makes the server enforce a boundary that already exists rather than inventing one.
    """
    scopes = list(Scopes.JWT_SESSION)
    if is_admin:
        scopes.extend(Scopes.JWT_ADMIN_EXTRA)
    return scopes
