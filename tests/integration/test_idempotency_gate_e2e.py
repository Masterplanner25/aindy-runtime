"""
tests/integration/test_idempotency_gate_e2e.py
───────────────────────────────────────────────
End-to-end test for the NF-5 idempotency gate in-band stale-pending recovery.

Requires a live PostgreSQL instance (DATABASE_URL must be a postgresql:// URL).
Start services with: docker-compose -f docker-compose.test.yml up -d
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


@pytest.mark.integration
def test_in_band_stale_pending_recovery_e2e(testing_session_factory):
    """
    Verify the stale-pending in-band recovery path against real Postgres.

    Simulates the TOCTOU concurrent-insert race:
    1. A stale pending EffectRecord is committed to the DB (session A).
    2. A second session is opened (session B, simulating the late-arriving retry).
       Its initial query is patched to return None — as if the row wasn't visible
       at query time — so the INSERT is attempted against the real DB.
    3. The INSERT hits the real unique constraint and raises a real IntegrityError.
    4. The in-band recovery path fires: rollback → re-query → detect stale →
       reset created_at in-place → commit.
    5. Assert that the row in the DB was actually reset (created_at refreshed,
       completed_at cleared, status still "pending").
    """
    from unittest.mock import MagicMock

    from AINDY.db.models.effect_record import EffectRecord
    from AINDY.kernel.syscall_dispatcher import (
        _resolve_effect_record,
        STALE_PENDING_THRESHOLD_SECONDS,
    )

    action_id_val = f"e2e-stale-{uuid.uuid4().hex}"
    stale_time = datetime.now(timezone.utc) - timedelta(
        seconds=STALE_PENDING_THRESHOLD_SECONDS + 300
    )

    # ── Step 1: commit a stale pending row (session A) ────────────────────────
    session_a = testing_session_factory()
    try:
        stale_row = EffectRecord(
            action_id=action_id_val,
            action_type="sys.v1.test.e2e_stale_recovery",
            input_hash="0" * 64,
            status="pending",
            created_at=stale_time,
            completed_at=None,
        )
        session_a.add(stale_row)
        session_a.commit()
        stale_row_id = stale_row.id
    finally:
        session_a.close()

    # ── Step 2-4: simulate the TOCTOU race in session B ───────────────────────
    session_b = testing_session_factory()
    try:
        real_query = session_b.query
        first_call_done = [False]

        def toctou_query(model):
            if not first_call_done[0]:
                first_call_done[0] = True
                m = MagicMock()
                m.filter.return_value.first.return_value = None
                return m
            return real_query(model)

        session_b.query = toctou_query
        done, payload = _resolve_effect_record(
            session_b,
            action_id_val,
            "sys.v1.test.e2e_stale_recovery",
            {},
        )
    finally:
        session_b.query = real_query
        session_b.close()

    # ── Step 5: verify the row was reset in the real DB ───────────────────────
    session_c = testing_session_factory()
    try:
        refreshed = (
            session_c.query(EffectRecord)
            .filter(EffectRecord.id == stale_row_id)
            .first()
        )
        assert refreshed is not None, "EffectRecord row was deleted unexpectedly"
        assert not done, "_resolve_effect_record must return False (handler should run)"
        assert payload is None
        assert refreshed.status == "pending", (
            f"Expected status='pending' after reset, got {refreshed.status!r}"
        )
        assert refreshed.completed_at is None, (
            "completed_at must be cleared after in-band reset"
        )
        # created_at must be substantially newer than the original stale time
        created_at = refreshed.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_from_stale = (created_at - stale_time).total_seconds()
        assert age_from_stale > STALE_PENDING_THRESHOLD_SECONDS, (
            f"created_at not refreshed: {created_at!r} is only {age_from_stale:.0f}s"
            f" after the stale time {stale_time!r}"
        )
    finally:
        session_c.close()


@pytest.mark.integration
def test_syscall_idempotency_dedup_e2e(monkeypatch, testing_session_factory):
    """MEB-1b end-to-end on real Postgres: an EXACTLY_ONCE syscall dispatched twice with the
    same scope + payload runs its handler ONCE and replays the cached result on the retry.

    Exercises the full composed path the unit tests mock: dispatch → gate reads the entry's
    execution_guarantee (flag on) → the gate's own SessionLocal writes/reads the real
    effect_records row → replay. The kernel-sensitive _gate_db lifecycle under a real
    transaction is only covered here.
    """
    from unittest.mock import patch

    from AINDY.db.models.effect_record import EffectRecord
    from AINDY.kernel import syscall_registry as R
    from AINDY.kernel import syscall_dispatcher as D
    from AINDY.core.execution_gate import compute_action_id

    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "true")

    name = f"sys.v1.test.eo_{uuid.uuid4().hex[:8]}"
    eu_id = str(uuid.uuid4())
    caller_id = str(uuid.uuid4())  # fixed so we can assert MEB-3b attribution below
    payload = {"x": 1}
    action_id = compute_action_id(action_type=name, input_payload=payload, scope=eu_id)
    runs = []

    def handler(p, ctx):
        runs.append(1)
        return {"ran": len(runs), "echo": p}

    R.SYSCALL_REGISTRY[name] = R.SyscallEntry(
        handler=handler, capability="test.idem", execution_guarantee="EXACTLY_ONCE"
    )

    class _OkRm:
        def check_quota(self, x):
            return True, None

        def record_usage(self, x, u):
            return None

    def _ctx():
        return R.SyscallContext(
            execution_unit_id=eu_id, user_id=caller_id,
            capabilities=["test.idem"], trace_id="t",
        )

    dispatcher = D.SyscallDispatcher()  # fresh instance — don't mutate the singleton
    dispatcher._emit_syscall_event = lambda *a, **kw: None
    try:
        with patch.object(D, "_get_rm", lambda: _OkRm()):
            r1 = dispatcher.dispatch(name, payload, _ctx())
            r2 = dispatcher.dispatch(name, payload, _ctx())  # same scope+payload → replay
        assert r1["status"] == "success"
        assert r2["status"] == "success"
        assert len(runs) == 1, f"EXACTLY_ONCE handler must run once; ran {len(runs)}"
        assert r2["data"] == r1["data"], "retry must replay the first result"

        # MEB-3b — the gate attributes the effect row to the caller (tenant_id == user_id);
        # session_id is unset here (no MCP session in this path).
        check = testing_session_factory()
        try:
            row = check.query(EffectRecord).filter(
                EffectRecord.action_id == action_id
            ).first()
            assert row is not None, "gate must have written the effect row"
            assert row.tenant_id == caller_id, row.tenant_id
            assert row.session_id is None, row.session_id
        finally:
            check.close()
    finally:
        R.SYSCALL_REGISTRY.pop(name, None)
        cleanup = testing_session_factory()
        try:
            cleanup.query(EffectRecord).filter(
                EffectRecord.action_id == action_id
            ).delete()
            cleanup.commit()
        finally:
            cleanup.close()
