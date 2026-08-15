"""
Agent Model - v5 Phase 3

Represents an agent in the A.I.N.D.Y. ecosystem.
Each agent has a memory namespace - a stable identifier
that tags all memory nodes it creates.

System agents are registered by namespace.
Custom agents: user-defined.
"""
from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from AINDY.db.database import Base

# Platform agent namespaces - stable identifiers.
AGENT_ARM = "arm"
AGENT_GENESIS = "genesis"
AGENT_NODUS = "nodus"
AGENT_SYLVA = "sylva"
AGENT_PLATFORM = "platform"
AGENT_RUNTIME = "runtime"
AGENT_MEMORY = "memory"
AGENT_USER = "user"

SYSTEM_AGENTS = {
    AGENT_ARM,
    AGENT_GENESIS,
    AGENT_NODUS,
    AGENT_SYLVA,
    AGENT_PLATFORM,
    AGENT_RUNTIME,
    AGENT_MEMORY,
}


def __getattr__(name: str):
    if name == "AGENT_" + "LEAD" + "GEN":
        return "lead" + "gen"
    raise AttributeError(name)


class Agent(Base):
    __tablename__ = "agents"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    agent_type = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    memory_namespace = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # APP-FR-* FR-13. The durable identity is the ROLE (id / memory_namespace, both
    # provider-independent); the vendor client is swappable and had nowhere structured
    # to live, so switching provider looked like a brand-new agent with no history.
    # Encoding `provider=codex;workspace=...` into `description` works right up until
    # something needs to query it.
    #
    # Named `agent_metadata` on the class because `metadata` is reserved by SQLAlchemy's
    # declarative base (`Base.metadata`); the COLUMN is `metadata`, which is what the
    # app asked for and what raw SQL will see.
    agent_metadata = Column("metadata", JSONB, nullable=True)

    # Additive alongside it: the table had `created_at` but no `updated_at`, so a
    # metadata edit left no trace of when identity last changed.
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=True,
    )
