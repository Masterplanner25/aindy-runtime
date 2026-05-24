"""
tests/integration/test_effect_record_cleanup_e2e.py
─────────────────────────────────────────────────────
End-to-end test for the IDEM-9 EffectRecord TTL cleanup job.

Requires a live PostgreSQL instance (DATABASE_URL must be a postgresql:// URL).
Start services with: docker-compose -f docker-compose.test.yml up -d
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


@pytest.mark.integration
def test_effect_record_cleanup_deletes_expired_rows(testing_session_factory):
    """
    Verify the TTL cleanup job against real Postgres.

    Inserts three EffectRecord rows:
      - old_success: status=success, completed_at > TTL days ago → must be deleted
      - pending_row: status=pending, completed_at=None → must NOT be deleted
      - recent_success: status=success, completed_at=now → must NOT be deleted

    Then calls _cleanup_expired_effect_records() (which opens its own SessionLocal)
    and verifies the expected rows remain.
    """
    from AINDY.db.models.effect_record import EffectRecord
    from AINDY.platform_layer.scheduler_service import (
        EFFECT_RECORD_TTL_DAYS,
        _cleanup_expired_effect_records,
    )

    action_id_old = f"cleanup-e2e-old-{uuid.uuid4().hex}"
    action_id_pending = f"cleanup-e2e-pending-{uuid.uuid4().hex}"
    action_id_recent = f"cleanup-e2e-recent-{uuid.uuid4().hex}"

    old_completed = datetime.now(timezone.utc) - timedelta(days=EFFECT_RECORD_TTL_DAYS + 1)

    # ── Insert test rows (committed so SessionLocal inside job can see them) ──
    session = testing_session_factory()
    try:
        session.add(EffectRecord(
            action_id=action_id_old,
            action_type="sys.v1.test.cleanup.old",
            input_hash="0" * 64,
            status="success",
            completed_at=old_completed,
        ))
        session.add(EffectRecord(
            action_id=action_id_pending,
            action_type="sys.v1.test.cleanup.pending",
            input_hash="0" * 64,
            status="pending",
            completed_at=None,
        ))
        session.add(EffectRecord(
            action_id=action_id_recent,
            action_type="sys.v1.test.cleanup.recent",
            input_hash="0" * 64,
            status="success",
            completed_at=datetime.now(timezone.utc),
        ))
        session.commit()
    finally:
        session.close()

    # ── Run the cleanup job (opens its own SessionLocal internally) ────────────
    _cleanup_expired_effect_records()

    # ── Verify ────────────────────────────────────────────────────────────────
    verify = testing_session_factory()
    try:
        old_row = (
            verify.query(EffectRecord)
            .filter(EffectRecord.action_id == action_id_old)
            .first()
        )
        assert old_row is None, (
            f"Old finalized EffectRecord should have been deleted but still exists: {old_row!r}"
        )

        pending_row = (
            verify.query(EffectRecord)
            .filter(EffectRecord.action_id == action_id_pending)
            .first()
        )
        assert pending_row is not None, "Pending EffectRecord must not be deleted by TTL cleanup"

        recent_row = (
            verify.query(EffectRecord)
            .filter(EffectRecord.action_id == action_id_recent)
            .first()
        )
        assert recent_row is not None, "Recent finalized EffectRecord must not be deleted (within TTL)"
    finally:
        verify.close()
