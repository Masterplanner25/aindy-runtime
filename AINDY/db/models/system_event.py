import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from AINDY.db.database import Base


class SystemEvent(Base):
    __tablename__ = "system_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type = Column(String(64), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agent_registry.agent_id"), nullable=True, index=True)
    trace_id = Column(String(128), nullable=True, index=True)
    parent_event_id = Column(UUID(as_uuid=True), ForeignKey("system_events.id"), nullable=True, index=True)
    # 128 matches `trace_id`. The pipeline writes every request's route name here; at 32 a
    # 33+ character name failed its REQUIRED `execution.started` on every request, at WARNING,
    # and the request proceeded unrecorded (FR-41, #730). The width is read back by
    # `system_event_service.system_event_source_max_length()` — never restate the number.
    source = Column(String(128), nullable=True, index=True)
    payload = Column(JSONB, nullable=True)
    timestamp = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        Index("ix_system_events_user_id_timestamp", "user_id", "timestamp"),
        Index("ix_system_events_agent_id_timestamp", "agent_id", "timestamp"),
        Index("ix_system_events_trace_id_timestamp", "trace_id", "timestamp"),
        Index("ix_system_events_parent_event_id_timestamp", "parent_event_id", "timestamp"),
    )
