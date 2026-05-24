"""
db/models/effect_record.py - Persistent effect deduplication record.

One row per logical tool invocation, keyed by action_id (sha256 hash of
action_type + input_payload + scope produced by compute_action_id()).

When a retry arrives for the same logical action, the runtime can look up
the existing EffectRecord by action_id and short-circuit the handler call,
returning the cached result instead of re-executing the real-world side effect.

This table is the prerequisite for the NF-5 idempotency gate in
SyscallDispatcher (IDEMPOTENCY_AUDIT.md §"Open Findings — Effect-Level
Idempotency Layer", NF-1).

status values: "pending" | "success" | "failed"
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from AINDY.db.database import Base


class EffectRecord(Base):
    __tablename__ = "effect_records"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    action_id = Column(Text, nullable=False)
    """sha256 hex digest from compute_action_id(action_type, input_payload, scope).
    This is the deduplication key — always UNIQUE via uq_effect_records_action_id."""

    action_type = Column(String(256), nullable=False)
    """Logical action name, e.g. the tool/syscall name."""

    input_hash = Column(String(64), nullable=False)
    """sha256 hex digest of the input payload (64 chars)."""

    execution_id = Column(
        UUID(as_uuid=True),
        ForeignKey("execution_units.id", ondelete="SET NULL"),
        nullable=True,
    )
    """FK to the ExecutionUnit that triggered this effect. Nullable — an
    EffectRecord may be created before the EU exists in edge cases."""

    step_id = Column(String(256), nullable=True)
    """Step identifier within the execution (e.g. agent step index as string)."""

    status = Column(String(32), nullable=False, default="pending")
    """Lifecycle status: 'pending' | 'success' | 'failed'."""

    result_payload = Column(JSONB, nullable=True)
    """Stored result returned by the handler on success. Used for cache replay."""

    external_receipt = Column(JSONB, nullable=True)
    """Any external acknowledgement or receipt from the real-world side effect."""

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_effect_records_action_id",
            "action_id",
            unique=True,
        ),
        Index(
            "ix_effect_records_execution_id",
            "execution_id",
        ),
        Index(
            "ix_effect_records_completed_at_status",
            "completed_at",
            "status",
            postgresql_where=text("completed_at IS NOT NULL"),
        ),
    )
