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
    """Register a named agent definition. Idempotent on memory_namespace — updates if exists."""
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
    """Deactivate an agent by namespace. Soft-delete only — preserves memory nodes."""
    agent = db.query(Agent).filter(Agent.memory_namespace == namespace).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found.")
    agent.is_active = False
    db.commit()
    db.refresh(agent)
    return {**_serialize_agent(agent), "deactivated": True}
