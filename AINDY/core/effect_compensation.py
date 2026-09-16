"""
core/effect_compensation.py - Compensating-undo engine (AGENT-HARDEN-3).

Reversal walks a run's successful ``EffectRecord``s (the ledger the EXACTLY_ONCE
idempotency gate already writes) in reverse and, for each, invokes the owning
syscall's ``compensate`` hook when one is declared. Every attempt is recorded in
the append-only ``effect_reversals`` audit log:

  - reversed     — a compensator ran; the effect was undone.
  - irreversible — no compensator declared for the syscall; surfaced, not skipped.
  - failed       — a compensator was invoked but raised.

IDEM-12: reversal is re-entrant at its own layer. An effect that already has a
``reversed`` audit row is skipped and reported ``already_reversed`` — a second undo
never re-invokes a compensator. Only ``reversed`` suppresses; ``irreversible`` and
``failed`` rows do not, so a transient compensator failure stays retryable. This does
not depend on the EXACTLY_ONCE idempotency gate (which keys on the request, not the
effect, and is a flag).

This is the rollback mechanism AGENT-HARDEN-6 (the Verifier) invokes when a run's
post-conditions fail. Replay (``agent_runtime/replay.py``) *re-does*; this *undoes*.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

REVERSAL_STATUSES = ("reversed", "irreversible", "failed")


def _lookup_compensator(action_type: str):
    """Return the (compensate_callable, entry) for a syscall name, or (None, None)."""
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    try:
        entry = SYSCALL_REGISTRY[action_type]
    except Exception:
        return None, None
    return getattr(entry, "compensate", None), entry


def _record_reversal(
    db: Session,
    *,
    effect_record,
    run_id: str,
    execution_id,
    status: str,
    detail: Optional[str] = None,
    receipt: Optional[dict] = None,
) -> None:
    from AINDY.db.models import EffectReversal

    db.add(
        EffectReversal(
            effect_record_id=getattr(effect_record, "id", None),
            run_id=str(run_id),
            execution_id=execution_id,
            action_type=getattr(effect_record, "action_type", "") or "",
            status=status,
            detail=detail,
            receipt=receipt if isinstance(receipt, dict) else None,
        )
    )


def _already_reversed_effect_ids(db: Session, records) -> set:
    """Ids of the given EffectRecords that already carry a ``reversed`` audit row.

    Keyed on the effect record, not the action type — one run's reversal says nothing
    about another run's effect of the same syscall. Only ``reversed`` counts: an
    ``irreversible`` or ``failed`` row must leave the effect eligible for a retry.
    """
    from AINDY.db.models import EffectReversal

    ids = [rec.id for rec in records if getattr(rec, "id", None) is not None]
    if not ids:
        return set()
    rows = (
        db.query(EffectReversal.effect_record_id)
        .filter(
            EffectReversal.effect_record_id.in_(ids),
            EffectReversal.status == "reversed",
        )
        .all()
    )
    return {row[0] for row in rows}


def undo_run_effects(
    run_id: str,
    *,
    db: Session,
    context: Any = None,
    source_type: str = "agent_run",
) -> dict:
    """Reverse a run's reversible effects; surface irreversible ones.

    Resolves the run's ``ExecutionUnit`` (by ``source_type``/``run_id``), walks its
    successful ``EffectRecord``s newest-first, and for each invokes the syscall's
    ``compensate`` hook if one is declared. Returns a summary and writes one
    ``effect_reversals`` audit row per effect. Commits the audit log + compensator
    side effects.

    The compensator receives ``(effect: dict, context)`` where *effect* carries the
    recorded outcome (``result_payload`` / ``external_receipt`` / ids) it needs to
    reverse — the original input is not retained, by design, so compensators key off
    the effect's result.
    """
    from AINDY.core.execution_unit_service import ExecutionUnitService
    from AINDY.db.models import EffectRecord

    summary: dict[str, Any] = {
        "run_id": str(run_id),
        "reversed": [],
        "already_reversed": [],
        "irreversible": [],
        "failed": [],
    }

    eu = ExecutionUnitService(db).get_by_source(source_type, str(run_id))
    if eu is None:
        summary["error"] = f"no execution unit for {source_type} {run_id!r}"
        return summary

    records = (
        db.query(EffectRecord)
        .filter(
            EffectRecord.execution_id == eu.id,
            EffectRecord.status == "success",
        )
        .order_by(EffectRecord.created_at.desc(), EffectRecord.id.desc())
        .all()
    )

    already_reversed = _already_reversed_effect_ids(db, records)

    for rec in records:
        if rec.id in already_reversed:
            # IDEM-12 — a compensator already ran for THIS effect; do not run it again and
            # do not write a second `reversed` row. Reported so the caller can tell a
            # no-op undo from one with nothing to undo.
            summary["already_reversed"].append(rec.action_type)
            continue
        compensator, _entry = _lookup_compensator(rec.action_type)
        effect = {
            "effect_record_id": str(rec.id),
            "action_type": rec.action_type,
            "action_id": rec.action_id,
            "execution_id": str(rec.execution_id) if rec.execution_id else None,
            "step_id": rec.step_id,
            "result_payload": rec.result_payload,
            "external_receipt": rec.external_receipt,
        }

        if compensator is None:
            _record_reversal(
                db, effect_record=rec, run_id=run_id, execution_id=eu.id,
                status="irreversible",
                detail=f"no compensator declared for '{rec.action_type}'",
            )
            summary["irreversible"].append(rec.action_type)
            continue

        try:
            receipt = compensator(effect, context)
            _record_reversal(
                db, effect_record=rec, run_id=run_id, execution_id=eu.id,
                status="reversed",
                receipt=receipt if isinstance(receipt, dict) else None,
            )
            summary["reversed"].append(rec.action_type)
        except Exception as exc:
            logger.warning(
                "[EffectCompensation] compensator for '%s' failed on run %s: %s",
                rec.action_type, run_id, exc,
            )
            _record_reversal(
                db, effect_record=rec, run_id=run_id, execution_id=eu.id,
                status="failed", detail=str(exc),
            )
            summary["failed"].append({"action_type": rec.action_type, "error": str(exc)})

    # Durable: the audit log and compensator side effects must survive session close.
    db.commit()
    return summary
