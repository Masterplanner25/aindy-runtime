"""
OPER-DEFER-002 — GET/POST /automation/logs

Six shapes:
  1. List returns correct envelope (logs + count).
  2. Status filter is applied to the query.
  3. Source filter is applied to the query.
  4. 404 returned for unknown log_id on GET detail.
  5. Correct shape returned on GET detail for known log.
  6. Replay returns 404 for unknown log; 409 for non-replayable status; 200 on success.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_log(**kwargs):
    log = MagicMock()
    log.id = kwargs.get("id", "test-log-001")
    log.job_name = kwargs.get("job_name", "cleanup_job")
    log.task_name = kwargs.get("job_name", "cleanup_job")
    log.source = kwargs.get("source", "scheduler")
    log.status = kwargs.get("status", "success")
    log.attempt_count = kwargs.get("attempt_count", 1)
    log.max_attempts = kwargs.get("max_attempts", 3)
    log.error_message = kwargs.get("error_message", None)
    log.payload = kwargs.get("payload", {"key": "val"})
    log.result = kwargs.get("result", {"ok": True})
    log.started_at = None
    log.completed_at = None
    _ts = MagicMock()
    _ts.isoformat.return_value = "2026-06-15T00:00:00+00:00"
    log.created_at = _ts
    log.updated_at = _ts
    log.scheduled_for = None
    log.trace_id = None
    log.user_id = None
    return log


def _make_db(logs=None, by_id=None):
    db = MagicMock()
    q = MagicMock()
    q.order_by.return_value = q
    q.filter.return_value = q
    q.limit.return_value = q
    q.all.return_value = logs or []
    q.first.return_value = by_id
    db.query.return_value = q
    return db


def _call_list(db, status=None, source=None, limit=50):
    from AINDY.routes.automation_router import list_automation_logs

    return list_automation_logs(request=MagicMock(), status=status, source=source, limit=limit, db=db, _admin={})


def _call_get(db, log_id):
    from AINDY.routes.automation_router import get_automation_log

    return get_automation_log(request=MagicMock(), log_id=log_id, db=db, _admin={})


def _call_replay(db, log_id, replay_return):
    from AINDY.routes.automation_router import replay_automation_log

    with patch(
        "AINDY.platform_layer.scheduler_service.replay_task",
        return_value=replay_return,
    ):
        return replay_automation_log(request=MagicMock(), log_id=log_id, db=db, _admin={})


# ---------------------------------------------------------------------------
# 1. List returns correct envelope
# ---------------------------------------------------------------------------

def test_list_returns_logs_and_count():
    logs = [_make_mock_log(id="a"), _make_mock_log(id="b")]
    db = _make_db(logs=logs)
    result = _call_list(db)

    assert "logs" in result
    assert result["count"] == 2
    assert len(result["logs"]) == 2
    assert result["logs"][0]["id"] == "a"
    assert result["logs"][1]["id"] == "b"


def test_list_empty_db():
    db = _make_db(logs=[])
    result = _call_list(db)

    assert result["logs"] == []
    assert result["count"] == 0


# ---------------------------------------------------------------------------
# 2. Status filter
# ---------------------------------------------------------------------------

def test_list_status_filter_applied():
    db = _make_db(logs=[])
    q = db.query.return_value
    _call_list(db, status="failed")

    filter_calls = [str(c) for c in q.filter.call_args_list]
    assert len(filter_calls) >= 1


# ---------------------------------------------------------------------------
# 3. Source filter
# ---------------------------------------------------------------------------

def test_list_source_filter_applied():
    db = _make_db(logs=[])
    q = db.query.return_value
    _call_list(db, source="worker")

    assert q.filter.called


# ---------------------------------------------------------------------------
# 4. GET detail 404 on missing
# ---------------------------------------------------------------------------

def test_get_detail_404_on_missing_log():
    from fastapi import HTTPException

    db = _make_db(by_id=None)
    with pytest.raises(HTTPException) as exc_info:
        _call_get(db, "nonexistent-id")

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# 5. GET detail returns correct shape
# ---------------------------------------------------------------------------

def test_get_detail_correct_shape():
    mock_log = _make_mock_log(
        id="log-xyz",
        job_name="embed_memory",
        source="worker",
        status="success",
        attempt_count=2,
        payload={"node_id": "n1"},
        result={"embedded": True},
    )
    db = _make_db(by_id=mock_log)
    result = _call_get(db, "log-xyz")

    assert result["id"] == "log-xyz"
    assert result["task_name"] == "embed_memory"
    assert result["source"] == "worker"
    assert result["status"] == "success"
    assert result["attempt_count"] == 2
    assert result["payload"] == {"node_id": "n1"}
    assert result["result"] == {"embedded": True}
    assert result["user_id"] is None
    assert result["trace_id"] is None


# ---------------------------------------------------------------------------
# 6. Replay paths
# ---------------------------------------------------------------------------

def test_replay_404_on_missing_log():
    from fastapi import HTTPException

    db = _make_db(by_id=None)
    with pytest.raises(HTTPException) as exc_info:
        _call_replay(db, "ghost-id", replay_return=False)

    assert exc_info.value.status_code == 404


def test_replay_409_on_non_replayable_status():
    from fastapi import HTTPException

    pending_log = _make_mock_log(id="log-pending", status="pending")
    db = _make_db(by_id=pending_log)
    with pytest.raises(HTTPException) as exc_info:
        _call_replay(db, "log-pending", replay_return=False)

    assert exc_info.value.status_code == 409
    assert "pending" in exc_info.value.detail


def test_replay_success():
    db = _make_db()
    result = _call_replay(db, "log-failed", replay_return=True)

    assert result["replayed"] is True
    assert result["log_id"] == "log-failed"


# ---------------------------------------------------------------------------
# 7. Serialize log — nulls handled
# ---------------------------------------------------------------------------

def test_serialize_log_none_timestamps():
    from AINDY.routes.automation_router import _serialize_log

    mock_log = _make_mock_log()
    mock_log.started_at = None
    mock_log.completed_at = None
    mock_log.scheduled_for = None

    result = _serialize_log(mock_log)
    assert result["started_at"] is None
    assert result["completed_at"] is None
    assert result["scheduled_for"] is None
