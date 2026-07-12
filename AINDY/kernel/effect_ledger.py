"""EffectRecord idempotency ledger — the reusable primitive for the Mediated Effect
Boundary program (MEB). See docs/runtime/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md.

Write-ahead effect-record upsert giving side-effecting calls at-most-once semantics:
write a ``pending`` row keyed by a deterministic ``action_id``, execute, then finalize
``success``/``failed`` (caching the result for replay). Race-safe via the unique
constraint on ``action_id`` with stale-pending recovery.

MEB-0 uses this at the agent tool path (``execute_tool``). The syscall dispatcher gate
still carries its own byte-identical private copies
(``syscall_dispatcher._resolve_effect_record`` / ``_complete_effect_record``); **MEB-1
consolidates the dispatcher onto this module and removes that duplication.** Keep the two
in sync until then.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from typing import Any

from AINDY.kernel.clock import utcnow

logger = logging.getLogger(__name__)

# Abandoned ``pending`` rows older than this are reclaimable (mirrors the dispatcher).
STALE_PENDING_THRESHOLD_SECONDS = 900


def resolve_effect_record(
    db, action_id: str, action_type: str, payload: dict
) -> tuple[bool, Any]:
    """Claim or replay an effect slot. Returns ``(already_succeeded, cached_result)``.

    ``(True, cached)`` — this ``action_id`` already completed successfully; the caller
    must NOT re-execute and should return ``cached``.
    ``(False, None)`` — the slot is claimed ``pending`` (or degraded to AT_LEAST_ONCE
    under a live concurrent call); the caller executes, then calls
    ``complete_effect_record``.
    """
    from AINDY.db.models.effect_record import EffectRecord
    from sqlalchemy.exc import IntegrityError

    record = db.query(EffectRecord).filter(EffectRecord.action_id == action_id).first()
    if record is not None and record.status == "success":
        return True, record.result_payload
    if record is None:
        payload_bytes = json.dumps(
            dict(payload or {}), sort_keys=True, separators=(",", ":")
        ).encode()
        input_hash = hashlib.sha256(payload_bytes).hexdigest()
        try:
            db.add(
                EffectRecord(
                    action_id=action_id,
                    action_type=action_type,
                    input_hash=input_hash,
                    status="pending",
                )
            )
            db.commit()
        except IntegrityError as exc:
            # Only the action_id unique-constraint race is recoverable; anything else
            # (FK, check) must propagate.
            _err = str(exc) + str(getattr(exc, "orig", ""))
            if "uq_effect_records_action_id" not in _err:
                raise
            db.rollback()
            record = (
                db.query(EffectRecord).filter(EffectRecord.action_id == action_id).first()
            )
            if record is None:
                raise
            if record.status == "success":
                return True, record.result_payload
            stale_cutoff = utcnow() - timedelta(seconds=STALE_PENDING_THRESHOLD_SECONDS)
            if record.status == "pending" and record.created_at >= stale_cutoff:
                # A live concurrent call holds the slot; degrade to AT_LEAST_ONCE for
                # this invocation (strict at-most-once under concurrency needs advisory
                # locking — see IDEMPOTENCY_CONTRACT.md).
                logger.warning(
                    "[effect_ledger] concurrent pending EffectRecord for action_id=%s;"
                    " degrading to AT_LEAST_ONCE for this call",
                    action_id,
                )
                return False, None
            # Stale pending (abandoned) or prior failure: reclaim the slot in-place.
            record.status = "pending"
            record.completed_at = None
            record.created_at = utcnow()
            db.commit()
    return False, None


def complete_effect_record(db, action_id: str, status: str, result_payload) -> None:
    """Finalize an effect slot (``success``/``failed``), caching a dict result for replay."""
    from AINDY.db.models.effect_record import EffectRecord

    record = db.query(EffectRecord).filter(EffectRecord.action_id == action_id).first()
    if record is not None:
        record.status = status
        record.result_payload = result_payload if isinstance(result_payload, dict) else None
        record.completed_at = utcnow()
        db.commit()
