import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from AINDY.db.database import get_db
from AINDY.db.models.agent import Agent
from AINDY.db.models.user import User
from AINDY.services.auth_service import require_admin_principal

router = APIRouter()


def _serialize_user(u: User) -> dict:
    return {
        "id": str(u.id),
        "email": u.email,
        "is_admin": u.is_admin,
        "is_active": u.is_active,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


def _serialize_agent(a: Agent) -> dict:
    return {
        "id": str(a.id),
        "name": a.name,
        "agent_type": a.agent_type,
        "description": a.description,
        "memory_namespace": a.memory_namespace,
        "is_active": a.is_active,
        "owner_user_id": str(a.owner_user_id) if a.owner_user_id else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


# ── Users ────────────────────────────────────────────────────────────────────

@router.get("/admin/users", response_model=None)
def list_users(
    request: Request,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin_principal),
):
    """List all registered users. Admin-only."""
    users = db.query(User).order_by(User.created_at.asc()).all()
    return {"users": [_serialize_user(u) for u in users]}


@router.post("/admin/users/{user_id}/promote", response_model=None)
def promote_user(
    request: Request,
    user_id: str,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin_principal),
):
    """Grant admin privileges to a user. Grant-only — never revokes."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.is_admin:
        return {**_serialize_user(user), "already_admin": True}
    user.is_admin = True
    db.commit()
    db.refresh(user)
    return {**_serialize_user(user), "already_admin": False}


# ── Agents ───────────────────────────────────────────────────────────────────

class AgentRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    memory_namespace: str = Field(..., min_length=1, max_length=128)
    agent_type: str = Field(default="custom", max_length=64)
    description: str | None = Field(default=None, max_length=1024)


@router.get("/admin/agents", response_model=None)
def list_agents(
    request: Request,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin_principal),
):
    """List all registered agents. Admin-only."""
    agents = db.query(Agent).order_by(Agent.created_at.asc()).all()
    return {"agents": [_serialize_agent(a) for a in agents]}


@router.post("/admin/agents/register", response_model=None)
def register_agent(
    request: Request,
    body: AgentRegisterRequest,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin_principal),
):
    """Register a named agent definition. Idempotent on memory_namespace — updates if exists.

    FR-12: the seven platform system namespaces are reserved. Without this guard the
    idempotent-update branch below silently rewrote the platform's own Runtime / Memory /
    Nodus rows — name, type and description — for anyone with admin, and the next boot
    would not repair it because ``_bootstrap_system_agents`` only *inserts* when the row
    is absent. The same reservation is enforced in ``registry.register_agent``.
    """
    from AINDY.db.models.agent import SYSTEM_AGENTS

    if body.memory_namespace in SYSTEM_AGENTS:
        raise HTTPException(
            status_code=409,
            detail=(
                f"memory_namespace {body.memory_namespace!r} is reserved for a platform "
                f"system agent. Reserved: {sorted(SYSTEM_AGENTS)}"
            ),
        )

    existing = db.query(Agent).filter(Agent.memory_namespace == body.memory_namespace).first()
    if existing:
        existing.name = body.name
        existing.agent_type = body.agent_type
        existing.description = body.description
        existing.is_active = True
        db.commit()
        db.refresh(existing)
        return {**_serialize_agent(existing), "created": False}

    agent = Agent(
        id=str(uuid.uuid4()),
        name=body.name,
        memory_namespace=body.memory_namespace,
        agent_type=body.agent_type,
        description=body.description,
        is_active=True,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return {**_serialize_agent(agent), "created": True}


@router.delete("/admin/agents/{namespace}", response_model=None)
def deactivate_agent(
    request: Request,
    namespace: str,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin_principal),
):
    """Deactivate an agent by namespace. Soft-delete only — preserves memory nodes.

    Deactivating a *platform system* agent is **permitted by decision (2026-08-15)**, not
    merely unguarded. It stays consequential — ``flow_definitions_memory`` filters on
    ``is_active``, so the agent disappears from listings and memory routing — so the
    response carries a warning saying exactly that, and boot deliberately does not reverse
    it. ``POST /admin/agents/{namespace}/restore`` is the way back, without a restart.

    Do not add a reserved-namespace guard here. The reservation on
    ``POST /admin/agents/register`` exists because its *update* branch silently rewrote
    platform rows; deactivation is an explicit, visible, reversible operator action, which
    is a different thing.
    """
    from AINDY.db.models.agent import SYSTEM_AGENTS

    agent = db.query(Agent).filter(Agent.memory_namespace == namespace).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found.")
    agent.is_active = False
    db.commit()
    db.refresh(agent)

    payload = {**_serialize_agent(agent), "deactivated": True}
    if namespace in SYSTEM_AGENTS:
        payload["warning"] = (
            f"{namespace!r} is a platform system agent. It is now excluded from agent "
            f"listings and memory routing, and a restart does NOT re-enable it. "
            f"Restore it with POST /platform/admin/agents/{namespace}/restore."
        )
    return payload


@router.post("/admin/agents/{namespace}/restore", response_model=None)
def restore_agent(
    request: Request,
    namespace: str,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin_principal),
):
    """Reactivate a deactivated agent, and repair a system agent's identity fields.

    This is the repair path that was missing. Before it, a deactivated agent had no way
    back through the API at all for the namespaces that matter most: the boot seed only
    ever *inserted*, and ``POST /admin/agents/register`` — whose update branch does set
    ``is_active = True`` — refuses reserved namespaces, so closing that hole (correctly)
    also closed the only accidental route back for a system agent.

    For a reserved namespace this also restores ``name`` / ``agent_type`` /
    ``description`` from ``SYSTEM_AGENT_SPECS``, so one call fully repairs a row rather
    than leaving it active-but-wrong until the next boot.
    """
    from AINDY.db.models.agent import SYSTEM_AGENTS, SYSTEM_AGENT_SPECS

    agent = db.query(Agent).filter(Agent.memory_namespace == namespace).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found.")

    was_active = bool(agent.is_active)
    repaired: list[str] = []

    if namespace in SYSTEM_AGENTS:
        spec = next(s for s in SYSTEM_AGENT_SPECS if s["namespace"] == namespace)
        for field in ("name", "agent_type", "description"):
            if getattr(agent, field) != spec[field]:
                setattr(agent, field, spec[field])
                repaired.append(field)

    agent.is_active = True
    db.commit()
    db.refresh(agent)
    return {
        **_serialize_agent(agent),
        "restored": True,
        "was_active": was_active,
        "repaired_fields": repaired,
    }
