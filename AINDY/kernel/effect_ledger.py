"""EffectRecord idempotency ledger — the reusable primitive for the Mediated Effect
Boundary program (MEB). See docs/runtime/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md.

Write-ahead effect-record upsert giving side-effecting calls at-most-once semantics:
write a ``pending`` row keyed by a deterministic ``action_id``, execute, then finalize
``success``/``failed`` (caching the result for replay). Race-safe via the unique
constraint on ``action_id`` with stale-pending recovery.

Both effect-boundary chokepoints use this module: the agent tool path (``execute_tool``,
MEB-0) and the syscall dispatcher gate (which imports ``resolve_effect_record`` /
``complete_effect_record`` as ``_resolve_effect_record`` / ``_complete_effect_record``
since MEB-1a consolidated its duplicated private copies away).

MEB-3b adds optional tenant/session attribution: pass ``tenant_id`` / ``session_id`` to
``resolve_effect_record`` (or set them ambiently via ``set_effect_attribution``) to record
which tenant/session produced each effect. Attribution is stored on the row only — never
part of the ``action_id`` dedup hash.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
from datetime import timedelta
from typing import Any, Optional

from AINDY.kernel.clock import utcnow

logger = logging.getLogger(__name__)

# Abandoned ``pending`` rows older than this are reclaimable (mirrors the dispatcher).
STALE_PENDING_THRESHOLD_SECONDS = 900

# MEB-3b — ambient attribution for effect records. A write site that can't pass tenant/
# session explicitly (e.g. a syscall dispatched several frames below a multi-tenant MCP
# auth_hook) sets this contextvar; ``resolve_effect_record`` reads it as a per-field
# fallback. Contextvars propagate down the synchronous/async call tree within one
# execution context, so a value set in the MCP auth_hook is visible to the effect write
# inside the handler's ``dispatch_syscall``. Attribution/audit only — never part of the
# action_id dedup hash.
_effect_attribution: contextvars.ContextVar[tuple[Optional[str], Optional[str]]] = (
    contextvars.ContextVar("aindy_effect_attribution", default=(None, None))
)


def set_effect_attribution(
    *, tenant_id: Optional[str] = None, session_id: Optional[str] = None
) -> contextvars.Token:
    """Set the ambient (tenant_id, session_id) attribution for effect records written in
    this execution context. Returns the token so a caller can ``reset_effect_attribution``.
    Either field may be None to leave that field unattributed."""
    return _effect_attribution.set((tenant_id, session_id))


def reset_effect_attribution(token: contextvars.Token) -> None:
    """Restore the attribution contextvar to its prior value (pairs with the returned token)."""
    try:
        _effect_attribution.reset(token)
    except (ValueError, LookupError):
        # Token from a different context (e.g. reset on another thread) — best-effort.
        pass


def current_effect_attribution() -> tuple[Optional[str], Optional[str]]:
    """Return the ambient (tenant_id, session_id) attribution, or (None, None)."""
    return _effect_attribution.get()


# DUR-2 (durable execution) — per-run "at-most-once" signal. A continuation driver sets
# this while re-driving a crashed run so the effect-boundary chokepoints (memory / syscall /
# tool) dedup that run's effects WITHOUT each tool/syscall having to declare EXACTLY_ONCE —
# i.e. declaration-free at-most-once, scoped to the re-driven run. Like all contextvars it
# stays within one execution context: it reaches parent-side effects (deferred memory
# writes) and in-process dispatches, but NOT a nodus worker subprocess (that propagation is
# DUR-2b). See docs/runtime/DURABLE_EXECUTION_PROGRAM.md (DUR-2).
_durable_effects: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "aindy_durable_effects", default=False
)


@contextlib.contextmanager
def durable_effects_scope():
    """Mark the current execution context as at-most-once for all effects written in it.

    Effect-boundary chokepoints consulted via ``durable_effects_active()`` will dedup even
    when the individual tool/syscall is AT_LEAST_ONCE and its per-effect master flag is off.
    """
    token = _durable_effects.set(True)
    try:
        yield
    finally:
        _durable_effects.reset(token)


def durable_effects_active() -> bool:
    """True inside a ``durable_effects_scope()`` — the run wants declaration-free at-most-once."""
    return _durable_effects.get()


def _count_gate(outcome: str) -> None:
    """Record a gate resolution (IDEM-11).

    ★ Best-effort and import-local on purpose. A metrics failure must never change whether an
    effect executes — the ledger is the correctness path and the counter is observability, and
    inverting that would let a Prometheus problem become a duplicate side effect.
    """
    try:
        from AINDY.platform_layer.metrics import effect_gate_outcomes_total

        effect_gate_outcomes_total.labels(outcome=outcome).inc()
    except Exception:  # pragma: no cover - observability must never break the effect path
        pass


def resolve_effect_record(
    db,
    action_id: str,
    action_type: str,
    payload: dict,
    *,
    tenant_id: Optional[str] = None,
    session_id: Optional[str] = None,
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

    # MEB-3b — resolve attribution: explicit kwargs win per-field; else the ambient
    # contextvar. Recorded on the row only; never folded into action_id/input_hash.
    _ctx_tenant, _ctx_session = current_effect_attribution()
    eff_tenant = tenant_id if tenant_id is not None else _ctx_tenant
    eff_session = session_id if session_id is not None else _ctx_session

    record = db.query(EffectRecord).filter(EffectRecord.action_id == action_id).first()
    if record is not None and record.status == "success":
        _count_gate("replayed")
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
                    tenant_id=eff_tenant,
                    session_id=eff_session,
                )
            )
            db.commit()
            _count_gate("reserved")
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
                _count_gate("replayed")
                return True, record.result_payload
            stale_cutoff = utcnow() - timedelta(seconds=STALE_PENDING_THRESHOLD_SECONDS)
            if record.status == "pending" and record.created_at >= stale_cutoff:
                # A live concurrent call holds the slot; degrade to AT_LEAST_ONCE for
                # this invocation (strict at-most-once under concurrency needs advisory
                # locking — see IDEMPOTENCY_CONTRACT.md).
                _count_gate("degraded")
                logger.warning(
                    "[effect_ledger] concurrent pending EffectRecord for action_id=%s;"
                    " degrading to AT_LEAST_ONCE for this call",
                    action_id,
                )
                return False, None
            # Stale pending (abandoned) or prior failure: reclaim the slot in-place and
            # re-attribute it to the writer that is reclaiming it (MEB-3b).
            record.status = "pending"
            record.completed_at = None
            record.created_at = utcnow()
            record.tenant_id = eff_tenant
            record.session_id = eff_session
            db.commit()
            _count_gate("reclaimed")
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
