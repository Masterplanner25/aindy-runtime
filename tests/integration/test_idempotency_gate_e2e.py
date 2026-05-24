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
