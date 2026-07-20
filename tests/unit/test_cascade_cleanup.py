"""
RT-MEMTXN-LEAK-1 cascade-debris cleanup.

The scoping predicate is the load-bearing part: it must match exactly the nodes the
fixed capture path would now refuse to create, and nothing a user or app authored.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from AINDY.core.memory_capture_guard import RUNTIME_INTERNAL_TASK_NAMES
from AINDY.memory import cascade_cleanup

pytestmark = pytest.mark.runtime_only


def _session(dialect: str, rows=None, rowcounts=None):
    """A session stub that records the SQL it is handed."""
    db = MagicMock()
    db.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name=dialect))
    db.statements = []
    counts = list(rowcounts or [])

    def _execute(stmt, params=None):
        sql = str(stmt)
        db.statements.append((sql, params or {}))
        result = MagicMock()
        if sql.strip().upper().startswith("DELETE"):
            result.rowcount = counts.pop(0) if counts else 0
        else:
            result.fetchall.return_value = list(rows or [])
        return result

    db.execute.side_effect = _execute
    return db


# ── scoping ─────────────────────────────────────────────────────────────────────

def test_predicate_targets_the_runtime_internal_task_names():
    db = _session("postgresql")
    cascade_cleanup.summarize_cascade_debris(db)

    sql, params = db.statements[0]
    assert "extra -> 'event_payload' ->> 'task_name'" in sql
    assert set(params.values()) == set(RUNTIME_INTERNAL_TASK_NAMES)


def test_predicate_uses_json_extract_on_sqlite():
    db = _session("sqlite")
    cascade_cleanup.summarize_cascade_debris(db)

    sql, _ = db.statements[0]
    assert "json_extract(extra, '$.event_payload.task_name')" in sql


def test_unsupported_dialect_is_refused_not_guessed():
    db = _session("mysql")
    with pytest.raises(RuntimeError, match="does not support"):
        cascade_cleanup.summarize_cascade_debris(db)


def test_empty_task_names_is_refused():
    """An empty IN-list would render an unscoped predicate — never allow it."""
    db = _session("postgresql")
    with pytest.raises(ValueError, match="unscoped"):
        cascade_cleanup.summarize_cascade_debris(db, task_names=[])


# ── reporting ───────────────────────────────────────────────────────────────────

def test_summary_splits_global_and_owned():
    rows = [
        ("memory.generate_embedding", "execution.started", True, 1774),
        ("memory.generate_embedding", "feedback.abandonment_detected", True, 137),
        ("memory.generate_embedding", "execution.started", False, 1),
    ]
    report = cascade_cleanup.summarize_cascade_debris(_session("postgresql", rows=rows))

    assert report["matched"] == 1912
    assert report["global"] == 1911
    assert report["owned"] == 1
    assert len(report["breakdown"]) == 3


# ── deletion ────────────────────────────────────────────────────────────────────

def test_dry_run_never_issues_a_delete():
    rows = [("memory.generate_embedding", "execution.started", True, 10)]
    db = _session("postgresql", rows=rows)

    report = cascade_cleanup.prune_cascade_debris(dry_run=True, db=db)

    assert report["dry_run"] is True
    assert report["matched"] == 10
    assert report["deleted"] == 0
    assert not any(sql.strip().upper().startswith("DELETE") for sql, _ in db.statements)


def test_delete_runs_in_committed_batches_until_drained():
    """Batches must commit as they go — one long transaction is the very failure
    mode this whole item exists to prevent."""
    rows = [("memory.generate_embedding", "execution.started", True, 250)]
    db = _session("postgresql", rows=rows, rowcounts=[100, 100, 50, 0])

    report = cascade_cleanup.prune_cascade_debris(dry_run=False, batch_size=100, db=db)

    assert report["deleted"] == 250
    assert report["batches"] == 3
    deletes = [s for s, _ in db.statements if s.strip().upper().startswith("DELETE")]
    assert len(deletes) == 4  # three productive + the one that drains
    assert db.commit.call_count == 4
    assert "LIMIT :batch_size" in deletes[0]


def test_nothing_matched_short_circuits():
    db = _session("postgresql", rows=[])
    report = cascade_cleanup.prune_cascade_debris(dry_run=False, db=db)

    assert report["matched"] == 0
    assert report["deleted"] == 0
    assert not any(sql.strip().upper().startswith("DELETE") for sql, _ in db.statements)


def test_batch_size_must_be_positive():
    with pytest.raises(ValueError, match="batch_size"):
        cascade_cleanup.prune_cascade_debris(batch_size=0, db=_session("postgresql"))


def test_caller_owned_session_is_not_closed():
    db = _session("postgresql", rows=[])
    cascade_cleanup.prune_cascade_debris(dry_run=True, db=db)
    db.close.assert_not_called()
