"""
tests/unit/test_effect_record_cleanup.py
─────────────────────────────────────────
Unit tests for _cleanup_expired_effect_records() in scheduler_service.py.

All DB interaction is mocked; no real Postgres required.
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from AINDY.platform_layer.scheduler_service import (
    EFFECT_RECORD_DELETE_BATCH_SIZE,
    EFFECT_RECORD_TTL_DAYS,
    _cleanup_expired_effect_records,
)

pytestmark = pytest.mark.runtime_only


def _make_db(
    *,
    total: int = 0,
    pending: int = 0,
    eligible: int = 0,
    stale_pending: int = 0,
    execute_rowcounts: list[int] | None = None,
):
    """Return a MagicMock session pre-configured for the observability queries."""
    if execute_rowcounts is None:
        execute_rowcounts = [0]

    db = MagicMock()

    # db.query(func.count(...)).scalar() → total_count
    # db.query(func.count(...)).filter(...).scalar() → pending, eligible, stale_pending
    scalar_chain = db.query.return_value.scalar
    scalar_chain.return_value = total

    filter_scalar_chain = db.query.return_value.filter.return_value.scalar
    filter_scalar_chain.side_effect = [pending, eligible, stale_pending]

    # db.execute(...) → result with .rowcount
    execute_results = []
    for rc in execute_rowcounts:
        r = MagicMock()
        r.rowcount = rc
        execute_results.append(r)
    db.execute.side_effect = execute_results

    return db


# ── 1. No eligible rows ───────────────────────────────────────────────────────

def test_no_eligible_rows_logs_zero_deleted(caplog):
    db = _make_db(total=10, pending=2, eligible=0, stale_pending=0, execute_rowcounts=[0])
    with patch("AINDY.db.database.SessionLocal", return_value=db):
        with caplog.at_level("INFO", logger="AINDY.platform_layer.scheduler_service"):
            _cleanup_expired_effect_records()

    assert "deleted=0" in caplog.text
    db.commit.assert_called_once()
    db.close.assert_called_once()


# ── 2. Finalized rows deleted ─────────────────────────────────────────────────

def test_finalized_rows_older_than_ttl_are_deleted(caplog):
    db = _make_db(total=50, pending=0, eligible=5, stale_pending=0, execute_rowcounts=[5])
    with patch("AINDY.db.database.SessionLocal", return_value=db):
        with caplog.at_level("INFO", logger="AINDY.platform_layer.scheduler_service"):
            _cleanup_expired_effect_records()

    assert "deleted=5" in caplog.text
    assert "eligible=5" in caplog.text
    db.execute.assert_called_once()
    db.commit.assert_called_once()


# ── 3. Batch loop continues until partial batch ───────────────────────────────

def test_batch_loop_continues_until_partial_batch(caplog):
    """When the first batch fills the limit, a second DELETE is issued."""
    rowcounts = [EFFECT_RECORD_DELETE_BATCH_SIZE, 7]
    db = _make_db(
        total=10_007,
        pending=0,
        eligible=10_007,
        stale_pending=0,
        execute_rowcounts=rowcounts,
    )
    with patch("AINDY.db.database.SessionLocal", return_value=db):
        with caplog.at_level("INFO", logger="AINDY.platform_layer.scheduler_service"):
            _cleanup_expired_effect_records()

    assert db.execute.call_count == 2
    assert db.commit.call_count == 2
    expected_total = EFFECT_RECORD_DELETE_BATCH_SIZE + 7
    assert f"deleted={expected_total}" in caplog.text


# ── 4. Single full batch stops the loop ──────────────────────────────────────

def test_exactly_one_full_batch_exits_loop(caplog):
    """When first batch == batch_size but second is 0, the loop runs twice."""
    rowcounts = [EFFECT_RECORD_DELETE_BATCH_SIZE, 0]
    db = _make_db(
        total=10_000,
        pending=0,
        eligible=10_000,
        stale_pending=0,
        execute_rowcounts=rowcounts,
    )
    with patch("AINDY.db.database.SessionLocal", return_value=db):
        _cleanup_expired_effect_records()

    assert db.execute.call_count == 2
    assert db.commit.call_count == 2


# ── 5. Stale pending warning ──────────────────────────────────────────────────

def test_stale_pending_warning_logged(caplog):
    """Pending rows older than 1 hour trigger a WARNING."""
    db = _make_db(total=5, pending=3, eligible=0, stale_pending=3, execute_rowcounts=[0])
    with patch("AINDY.db.database.SessionLocal", return_value=db):
        with caplog.at_level("WARNING", logger="AINDY.platform_layer.scheduler_service"):
            _cleanup_expired_effect_records()

    assert "pending EffectRecord row(s) older than 1 hour" in caplog.text
    assert "3" in caplog.text


# ── 6. Exception is caught and logged; function does not raise ────────────────

def test_exception_is_caught_and_does_not_raise(caplog):
    with patch(
        "AINDY.db.database.SessionLocal",
        side_effect=RuntimeError("DB unavailable"),
    ):
        with caplog.at_level("ERROR", logger="AINDY.platform_layer.scheduler_service"):
            _cleanup_expired_effect_records()  # must not propagate

    assert "[effect_record_cleanup] failed" in caplog.text
    assert "DB unavailable" in caplog.text
