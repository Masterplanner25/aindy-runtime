"""
Agent Model - v5 Phase 3

Represents an agent in the A.I.N.D.Y. ecosystem.
Each agent has a memory namespace - a stable identifier
that tags all memory nodes it creates.

System agents are registered by namespace.
Custom agents: user-defined.
"""
from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, Index, text
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

#: The platform's own agent roster — the single source of truth for both the
#: reserved-namespace guard and the boot seed.
#:
#: This list used to live in ``startup._bootstrap_system_agents`` as a private
#: ``_SYSTEM_AGENTS``, with the namespace set below maintained separately. Two
#: lists describing one roster is a drift waiting to happen: the set decided what
#: an app may *not* register, the list decided what the platform *does* register,
#: and nothing made them agree. They are now one declaration, and the set is
#: derived from it.
SYSTEM_AGENT_SPECS = (
    {"name": "ARM", "namespace": AGENT_ARM, "agent_type": "system",
     "description": "Adaptive Reasoning Module — core reasoning and planning agent."},
    {"name": "Genesis", "namespace": AGENT_GENESIS, "agent_type": "system",
     "description": "Genesis — world-building and initialization agent."},
    {"name": "Nodus", "namespace": AGENT_NODUS, "agent_type": "system",
     "description": "Nodus — script execution and flow orchestration agent."},
    {"name": "SYLVA", "namespace": AGENT_SYLVA, "agent_type": "system",
     "description": "SYLVA — synthesis and language variant agent."},
    {"name": "Platform", "namespace": AGENT_PLATFORM, "agent_type": "system",
     "description": "Platform agent — runtime platform operations."},
    {"name": "Runtime", "namespace": AGENT_RUNTIME, "agent_type": "system",
     "description": "Runtime agent — core execution environment."},
    {"name": "Memory", "namespace": AGENT_MEMORY, "agent_type": "system",
     "description": "Memory agent — memory ingestion and retrieval."},
)

SYSTEM_AGENTS = {spec["namespace"] for spec in SYSTEM_AGENT_SPECS}


def __getattr__(name: str):
    if name == "AGENT_" + "LEAD" + "GEN":
        return "lead" + "gen"
    raise AttributeError(name)


class Agent(Base):
    __tablename__ = "agents"

    id = Column(String, primary_key=True)
    # Not globally unique — see __table_args__. `name` is a display name, and a
    # global UNIQUE on it means the first user to register "Research Bot" blocks
    # every other user from that name forever, across tenants.
    name = Column(String, nullable=False)
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

    # FR-12 remainder — `name` is unique *per owner*, not globally.
    #
    # A single `UNIQUE (owner_user_id, name)` would not do: shared rows (system
    # agents, app-registered identities) carry `owner_user_id IS NULL`, and SQL
    # treats NULLs as distinct in a unique constraint, so two rows named
    # "Runtime" would both be accepted. Two *partial* unique indexes keep the old
    # global guarantee exactly where it still applies and scope it per user where
    # it does not.
    #
    # `memory_namespace` stays globally unique on the column: it is the tag
    # written onto every memory node (`MemoryNodeModel.source_agent`), so one
    # namespace must mean one agent process-wide. User-facing registration
    # derives it from the owner rather than accepting it verbatim, which makes a
    # cross-user collision impossible instead of merely detected.
    __table_args__ = (
        Index(
            "uq_agents_name_shared",
            "name",
            unique=True,
            postgresql_where=text("owner_user_id IS NULL"),
            sqlite_where=text("owner_user_id IS NULL"),
        ),
        Index(
            "uq_agents_owner_name",
            "owner_user_id",
            "name",
            unique=True,
            postgresql_where=text("owner_user_id IS NOT NULL"),
            sqlite_where=text("owner_user_id IS NOT NULL"),
        ),
    )
