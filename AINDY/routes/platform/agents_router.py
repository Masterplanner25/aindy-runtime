"""User-owned agent registration — the authenticated half of APP-FR-* FR-12.

FR-12 shipped the platform *hook* (``registry.register_agent``) and the admin route.
Neither lets an ordinary authenticated user own an agent, so ``agents.owner_user_id``
— a column the table has had all along — was still written by exactly one path and
read by none. Live data said the same thing: ``count(owner_user_id) = 0``.

That half was deferred as "app-layer policy". It is not. Ownership, per-owner name
scoping, and owner-scoped reads are properties of the *table*, and every app that
wanted user-owned agents would have had to reimplement all three against a schema
that actively fought them — a global ``UNIQUE (agents.name)`` means the first app to
register "Assistant" wins it for the whole deployment. The runtime owns the
mechanism; what an app does with an agent stays app policy.

Three decisions worth stating outright:

**The namespace is derived, not accepted.** ``memory_namespace`` is globally unique
because it is the tag on every memory node the agent writes. If users chose it
directly, a taken namespace would have to 409 — and that 409 reports on a row the
caller cannot see, which is a cross-tenant existence oracle of exactly the kind
tracked for ``/auth/register``. Deriving it as ``u:<user_id>:<slug>`` makes a
cross-user collision impossible by construction rather than merely detected, so
every conflict a user can observe is with their own agent.

**``agent_type`` is forced to ``custom``.** ``agent_capability_mappings`` is keyed by
``agent_type``, so a user-settable type is a self-service claim about what class of
agent this is. Nothing in the runtime grants capability from that column today, and
this route is not the place to find out whether that stays true.

**Reads are owner-scoped.** Un-owned rows (system agents, app-registered identities)
are shared and visible to everyone; an owned row is visible only to its owner. This
mirrors ``registry.list_agent_specs_for_owner`` for the declarative half.
"""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from AINDY.db.database import get_db
from AINDY.db.models.agent import Agent
from AINDY.auth.api_key_auth import Scopes
from AINDY.services.auth_service import enforce_api_key_scope, get_current_user
from AINDY.utils.uuid_utils import normalize_uuid

router = APIRouter()

# ── HTTP-SCOPE-GAP-1 — scope gate for user-owned agents ───────────────────────────────────
#
# These five routes are mounted on the app directly (`prefix="/platform"`), NOT through
# `platform_router`, so they do **not** inherit its `require_platform_admin_access` gate — by
# design, since FR-12b exists so an ordinary user can own an agent. That also meant they had
# no authority check of any kind, only identity.
#
# `agent.run` for all five: creating, renaming, deactivating and restoring an agent are
# authority over agents, and the vocabulary has no `agent.manage` to distinguish them from
# running one. Owner scoping is unchanged and still does the work a scope cannot — a scope
# answers "may you touch agents", never "may you touch *this* agent".
_REQUIRE_AGENT = Depends(enforce_api_key_scope(Scopes.AGENT_RUN))

#: A slug is the user-chosen half of a derived namespace. Lowercase so the derived
#: namespace is stable under any case-folding a downstream store applies, and no
#: ``/`` or whitespace so it cannot smuggle structure into a memory address.
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

USER_NAMESPACE_PREFIX = "u:"


def derive_user_namespace(user_id: str, slug: str) -> str:
    """Namespace for a user-owned agent: ``u:<user_id>:<slug>``.

    Uniqueness is per-user by construction, so the global unique index on
    ``memory_namespace`` can never be tripped by another user's row.
    """
    return f"{USER_NAMESPACE_PREFIX}{user_id}:{slug}"


def _serialize(agent: Agent) -> dict:
    return {
        "id": str(agent.id),
        "name": agent.name,
        "slug": _slug_of(agent),
        "agent_type": agent.agent_type,
        "description": agent.description,
        "memory_namespace": agent.memory_namespace,
        "metadata": agent.agent_metadata,
        "is_active": agent.is_active,
        "owner_user_id": str(agent.owner_user_id) if agent.owner_user_id else None,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
    }


def _slug_of(agent: Agent) -> str | None:
    """The user-facing half of a derived namespace, or None for a shared agent."""
    ns = agent.memory_namespace or ""
    if not ns.startswith(USER_NAMESPACE_PREFIX):
        return None
    _, _, tail = ns.partition(":")
    _, _, slug = tail.partition(":")
    return slug or None


def _caller_id(current_user: dict) -> str:
    """The authenticated principal's user id, or 400 if it cannot be resolved.

    A platform API key resolves to a user, so key-authenticated callers work; a
    principal with no resolvable user cannot own anything and is refused rather than
    silently writing ``owner_user_id = NULL``, which would create a *shared* agent.
    """
    user_id = current_user.get("user_id") or current_user.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail="This endpoint requires a principal that resolves to a user account.",
        )
    try:
        # `owner_user_id` is a UUID column, so a non-UUID principal id would raise
        # deep inside the query and surface as a 500. Fail here, with a reason.
        uuid.UUID(str(user_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="This endpoint requires a principal whose user id is a UUID.",
        )
    return str(user_id)


def _require_slug(slug: str) -> str:
    if not SLUG_PATTERN.match(slug or ""):
        raise HTTPException(
            status_code=422,
            detail=(
                "slug must be 1-64 characters, lowercase, starting with a letter or "
                "digit, and may contain only a-z, 0-9, '.', '_' and '-'."
            ),
        )
    return slug


def _owned_or_404(db: Session, owner_id: str, slug: str) -> Agent:
    """Fetch the caller's agent by slug.

    Deliberately 404 rather than 403 when the row exists but belongs to someone else:
    a 403 would confirm that another user holds that slug. Since the namespace is
    derived from the caller's own id, a foreign row cannot even be addressed here —
    this is defence in depth against a future non-derived lookup.
    """
    namespace = derive_user_namespace(owner_id, slug)
    agent = (
        db.query(Agent)
        .filter(
            Agent.memory_namespace == namespace,
            Agent.owner_user_id == normalize_uuid(owner_id),
        )
        .first()
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found.")
    return agent


class AgentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    slug: str = Field(..., min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=1024)
    metadata: dict | None = Field(default=None)


class AgentUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    metadata: dict | None = Field(default=None)


@router.get("/agents", response_model=None)
def list_my_agents(
    request: Request,
    include_shared: bool = Query(
        default=False,
        description="Also return active shared agents (system + app-registered).",
    ),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _scope: None = _REQUIRE_AGENT,
):
    """List the caller's agents. Never returns another user's agents."""
    owner_id = _caller_id(current_user)
    query = db.query(Agent)
    if include_shared:
        query = query.filter(
            or_(
                Agent.owner_user_id == normalize_uuid(owner_id),
                Agent.owner_user_id.is_(None),
            )
        )
    else:
        query = query.filter(Agent.owner_user_id == normalize_uuid(owner_id))
    agents = query.order_by(Agent.created_at.asc()).all()
    return {"agents": [_serialize(a) for a in agents]}


@router.post("/agents", response_model=None, status_code=201)
def create_my_agent(
    request: Request,
    body: AgentCreateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _scope: None = _REQUIRE_AGENT,
):
    """Register an agent owned by the caller.

    Not idempotent, unlike the admin route: re-POSTing an existing slug is far more
    likely to be a mistake than an intent to overwrite, and the admin route's
    idempotent-update branch is precisely what silently rewrote platform rows before
    FR-12 reserved them. Use PATCH to change an existing agent.
    """
    owner_id = _caller_id(current_user)
    slug = _require_slug(body.slug)
    namespace = derive_user_namespace(owner_id, slug)

    if db.query(Agent).filter(Agent.memory_namespace == namespace).first():
        raise HTTPException(
            status_code=409,
            detail=f"You already have an agent with slug {slug!r}.",
        )

    agent = Agent(
        id=str(uuid.uuid4()),
        name=body.name,
        memory_namespace=namespace,
        agent_type="custom",
        description=body.description,
        owner_user_id=normalize_uuid(owner_id),
        agent_metadata=body.metadata,
        is_active=True,
    )
    db.add(agent)
    try:
        db.commit()
    except IntegrityError:
        # Either a concurrent POST of the same slug, or the per-owner name index.
        # Both are the caller colliding with themselves — the partial unique indexes
        # make a cross-user collision impossible.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                f"You already have an agent named {body.name!r} or with slug {slug!r}."
            ),
        )
    db.refresh(agent)
    return _serialize(agent)


@router.patch("/agents/{slug}", response_model=None)
def update_my_agent(
    request: Request,
    slug: str,
    body: AgentUpdateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _scope: None = _REQUIRE_AGENT,
):
    """Update name / description / metadata on an agent the caller owns.

    ``slug`` and therefore ``memory_namespace`` are immutable: the namespace is the tag
    already written onto this agent's memory nodes, so changing it would orphan its
    history — the exact continuity FR-13's metadata bag exists to preserve.
    """
    owner_id = _caller_id(current_user)
    agent = _owned_or_404(db, owner_id, _require_slug(slug))

    if body.name is not None:
        agent.name = body.name
    if body.description is not None:
        agent.description = body.description
    if body.metadata is not None:
        agent.agent_metadata = body.metadata

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"You already have another agent named {body.name!r}.",
        )
    db.refresh(agent)
    return _serialize(agent)


@router.delete("/agents/{slug}", response_model=None)
def deactivate_my_agent(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _scope: None = _REQUIRE_AGENT,
):
    """Deactivate an agent the caller owns. Soft — memory nodes are preserved."""
    owner_id = _caller_id(current_user)
    agent = _owned_or_404(db, owner_id, _require_slug(slug))
    agent.is_active = False
    db.commit()
    db.refresh(agent)
    return {**_serialize(agent), "deactivated": True}


@router.post("/agents/{slug}/restore", response_model=None)
def restore_my_agent(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _scope: None = _REQUIRE_AGENT,
):
    """Reactivate an agent the caller owns.

    Deactivation is soft and the namespace is retained, so restoring reconnects the
    agent to the memory it already wrote. Without this, a user's own soft-delete would
    be as unrecoverable as a system agent's was.
    """
    owner_id = _caller_id(current_user)
    agent = _owned_or_404(db, owner_id, _require_slug(slug))
    was_active = bool(agent.is_active)
    agent.is_active = True
    db.commit()
    db.refresh(agent)
    return {**_serialize(agent), "restored": True, "was_active": was_active}
