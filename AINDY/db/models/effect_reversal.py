"""
db/models/effect_reversal.py - Append-only compensating-undo audit log (AGENT-HARDEN-3).

One row per attempt to reverse a single completed effect (an ``EffectRecord``)
during an "undo run" pass. Reversal walks a run's successful ``EffectRecord``s in
reverse and, for each, invokes the owning syscall's ``compensate`` hook when one
is declared:

  - reversed     — a compensator ran and the effect was undone.
  - irreversible — no compensator is declared for the syscall; the effect is
                   surfaced, not silently skipped (e.g. an email already sent).
  - failed       — a compensator was invoked but raised.

The table is append-only: rows are never updated or deleted by the runtime, so it
is a durable audit trail of what was (and could not be) undone.

status values: "reversed" | "irreversible" | "failed"
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from AINDY.db.database import Base


class EffectReversal(Base):
    __tablename__ = "effect_reversals"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    effect_record_id = Column(
        UUID(as_uuid=True),
        ForeignKey("effect_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    """FK to the EffectRecord being reversed. Nullable so the audit row survives
    even if the original effect record is later pruned."""

    run_id = Column(String(72), nullable=True)
    """The undo target — the agent run id / correlation this reversal belongs to.
    Indexed via ``ix_effect_reversals_run_id`` in ``__table_args__``."""

    execution_id = Column(
        UUID(as_uuid=True),
        ForeignKey("execution_units.id", ondelete="SET NULL"),
        nullable=True,
    )
    """FK to the ExecutionUnit whose effects were reversed."""

    action_type = Column(String(256), nullable=False)
    """Logical action name of the reversed effect (the syscall/tool name)."""

    status = Column(String(32), nullable=False)
    """'reversed' | 'irreversible' | 'failed'."""

    detail = Column(Text, nullable=True)
    """Human-readable note: the compensator error for 'failed', or why an effect
    is irreversible."""

    receipt = Column(JSONB, nullable=True)
    """Optional acknowledgement returned by the compensator (e.g. the id of the
    deleted resource)."""

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_effect_reversals_run_id", "run_id"),
        Index("ix_effect_reversals_execution_id", "execution_id"),
        Index("ix_effect_reversals_effect_record_id", "effect_record_id"),
    )
