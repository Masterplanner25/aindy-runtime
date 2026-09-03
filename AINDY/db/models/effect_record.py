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


#: The complete set of lifecycle values ``EffectRecord.status`` may hold.
#:
#: ★ Declared rather than conventional. This used to be a docstring listing three strings while
#: ``complete_effect_record`` accepted any ``str``, which is how a vocabulary acquires values
#: nobody agreed to and loses the ability to say what it does not cover.
EFFECT_STATUS_PENDING = "pending"
EFFECT_STATUS_SUCCESS = "success"
EFFECT_STATUS_FAILED = "failed"

#: `EFFECT-PARTIAL-1` — some units of a batched effect were applied and some were not.
#:
#: The envelope is binary (``success | error``), so a 5-unit effect with 2 failures forced
#: through it is either a **lie** (``success``, silently partial) or a **waste** (``error``,
#: discarding the 3 that landed). Neither is recoverable from afterwards, because the record
#: does not say which units applied. This value exists so the *record* can be honest even where
#: the envelope cannot be.
#:
#: ★ It is not "mostly succeeded". A ``partial`` record without a payload naming which units
#: landed is strictly worse than ``failed`` — it says something went wrong and removes the
#: ability to say what. Write the per-unit outcome into ``result_payload`` or do not use this.
EFFECT_STATUS_PARTIAL = "partial"

#: `EFFECT-OUTCOME-UNKNOWN-1` — dispatched, outcome unobserved. Genuinely ambiguous.
#:
#: The narrow case from the outcome-ambiguity design: a **read timeout after a full request
#: write**. Everything on either side of that is knowable — DNS failure, connection refused and
#: an incomplete write are *definitely not dispatched*; an ack or an observed result is
#: *definitely landed*. Only that one window is a true unknown, and collapsing it into either
#: neighbour is what makes a human look at something a machine could have decided.
#:
#: ★★ DO NOT REACH FOR ``pending`` INSTEAD. The TTL cleanup job warns on any ``pending`` row
#: older than an hour as a stuck handler, so recording an honest ambiguity there would read as a
#: malfunction — and it would also be excluded from TTL cleanup, because that job hard-excludes
#: pending rows and this one is never going to resolve on its own.
#:
#: ★ It is a claim about the WORLD, not about the runtime's confidence. Do not use it to mean
#: "an exception we did not classify"; that is ``failed``.
EFFECT_STATUS_UNKNOWN = "unknown"

#: Every legal value.
EFFECT_STATUSES = frozenset({
    EFFECT_STATUS_PENDING,
    EFFECT_STATUS_SUCCESS,
    EFFECT_STATUS_FAILED,
    EFFECT_STATUS_PARTIAL,
    EFFECT_STATUS_UNKNOWN,
})

#: Values a *completed* effect may hold — everything except ``pending``.
#:
#: ★ The distinction is load-bearing for the TTL cleanup job, which reaps rows by
#: ``status != "pending"`` and hard-excludes pending ones. ``partial`` and ``unknown`` are
#: terminal, so they are reaped like any other finished effect. An "unknown" that is never
#: cleaned up is an unbounded table, and one that is warned about hourly is noise.
TERMINAL_EFFECT_STATUSES = frozenset(EFFECT_STATUSES - {EFFECT_STATUS_PENDING})


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

    tenant_id = Column(String(256), nullable=True)
    """Attribution (MEB-3b): the tenant that produced this effect. In A.I.N.D.Y.'s
    single-user-per-tenant model tenant_id == user_id. Nullable — recorded when the
    write site knows the caller (both effect-boundary chokepoints pass it); not part of
    the action_id dedup hash (attribution/audit only). See
    docs/runtime/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md (MEB-3b)."""

    session_id = Column(String(256), nullable=True)
    """Attribution (MEB-3b): the session that produced this effect (e.g. the multi-tenant
    MCP session, threaded via kernel.effect_ledger.set_effect_attribution). Nullable and
    not part of the dedup hash — on a replayed action the row keeps the FIRST writer's
    session, which is the correct "which session produced the effect" answer."""

    status = Column(String(32), nullable=False, default=EFFECT_STATUS_PENDING)
    """Lifecycle status — one of :data:`EFFECT_STATUSES`.

    ``pending`` | ``success`` | ``failed`` | ``partial`` | ``unknown``. The last two were added
    2026-09-02 for `EFFECT-PARTIAL-1` and `EFFECT-OUTCOME-UNKNOWN-1`; see the module docstring
    for what each one means and, more importantly, what it must not be used for.

    Still a plain ``String(32)`` with no CHECK constraint, so adding values needs no migration —
    but the set is no longer a docstring convention: ``complete_effect_record`` validates
    against :data:`TERMINAL_EFFECT_STATUSES` and refuses anything else.
    """

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
