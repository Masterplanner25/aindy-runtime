"""
Runtime-owned lifecycle event log for agent runs.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from AINDY.db.database import Base


# The agent-event vocabulary lives in `AINDY/agents/agent_event_types.py`, NOT here.
#
# ★ A copy used to sit at this spot and was four types stale with zero importers. It rotted
#   for a structural reason worth not recreating: `scripts/check_schema_version.py`
#   content-hashes every file under `AINDY/db/models/`, so adding one string to a set here
#   trips the schema contract and demands a version bump, a baseline regeneration and two
#   test-assertion edits — for a change with no DDL at all, since `event_type` below is a
#   plain String(32) with no constraint. Every commit that added a type paid the cheap path
#   instead, which was the rational choice each time.
#
# ★ Do NOT reintroduce a list here "for locality". The vocabulary is pinned by
#   `tests/unit/test_agent_event_contract.py`; locality is what cost it four types.


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    correlation_id = Column(String(72), nullable=True, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    event_type = Column(String(32), nullable=False, index=True)
    payload = Column(JSONB, nullable=True)
    system_event_id = Column(UUID(as_uuid=True), ForeignKey("system_events.id"), nullable=True, index=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_agent_events_run_id_occurred_at", "run_id", "occurred_at"),
        Index("ix_agent_events_user_id_occurred_at", "user_id", "occurred_at"),
    )
