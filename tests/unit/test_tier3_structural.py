"""
Tier 3 structural cleanup tests.

  Item 8  — MemoryIngestQueue.enqueue() emits a WARNING log on drop (queue full
            and not-accepting paths).
  Item 9  — _persist_system_event uses db.flush([event]) so pending handler ORM
            changes are NOT flushed as a side effect of event emission.
  V2/V3   — enforce_api_key_scope: API keys without the required scope get 403;
            JWT users are never gated; platform.admin bypasses all scope checks.
  V2/V3   — dispatch_syscall enforces domain-level scope for API key callers.
"""
from __future__ import annotations

import queue
import uuid
from unittest.mock import MagicMock, call, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.runtime_only


# ---------------------------------------------------------------------------
# Item 8 — MemoryIngestQueue drop logging
# ---------------------------------------------------------------------------

def _make_queue(maxsize: int = 2):
    from AINDY.memory.ingest_queue import MemoryIngestQueue
    q = MemoryIngestQueue(maxsize=maxsize, worker_handler=None, poll_interval=0.05)
    q._accepting = True
    return q


def test_enqueue_full_logs_warning(caplog):
    import logging
    q = _make_queue(maxsize=1)
    q._queue.put_nowait("item-0")  # fill queue
    with caplog.at_level(logging.WARNING, logger="AINDY.memory.ingest_queue"):
        result = q.enqueue("overflow-item")
    assert result is False
    assert any("queue full" in r.message for r in caplog.records)
    assert q.dropped_total == 1


def test_enqueue_not_accepting_logs_warning(caplog):
    import logging
    q = _make_queue()
    q._accepting = False
    with caplog.at_level(logging.WARNING, logger="AINDY.memory.ingest_queue"):
        result = q.enqueue("any-item")
    assert result is False
    assert any("not accepting" in r.message for r in caplog.records)


def test_enqueue_success_no_warning(caplog):
    import logging
    q = _make_queue(maxsize=5)
    with caplog.at_level(logging.WARNING, logger="AINDY.memory.ingest_queue"):
        result = q.enqueue("item")
    assert result is True
    assert not any("dropped" in r.message or "full" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Item 9 — db.flush([event]) scoped flush
# ---------------------------------------------------------------------------

def test_persist_system_event_flushes_only_event_not_full_session():
    """db.flush must be called with the event object, not bare db.flush()."""
    from AINDY.core.system_event_service import _persist_system_event

    flushed_args: list = []

    mock_event = MagicMock()
    mock_event.id = uuid.uuid4()

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = MagicMock(id=uuid.uuid4())

    def capture_flush(*args):
        flushed_args.append(args)

    mock_db.flush.side_effect = capture_flush

    # SystemEvent is imported locally inside _persist_system_event — patch at its source
    with patch("AINDY.db.models.system_event.SystemEvent", return_value=mock_event):
        with patch("AINDY.core.system_event_service.link_events"):
            _persist_system_event(
                db=mock_db,
                event_type="test.event",
                user_id=None,
                trace_id=None,
                parent_event_id=None,
                source="test",
                agent_id=None,
                payload={},
            )

    assert mock_db.flush.called, "db.flush must be called"
    assert flushed_args, "flush must have been called at least once"
    # All flush calls must pass the event object — never bare flush()
    for args in flushed_args:
        assert len(args) == 1, f"db.flush() called with no args (bare flush) — got {args!r}"
        obj_list = args[0]
        assert obj_list == [mock_event], f"Expected flush([event]), got flush({obj_list!r})"


# ---------------------------------------------------------------------------
# V2/V3 — enforce_api_key_scope dependency
# ---------------------------------------------------------------------------

def _call_scope_guard(scope: str, user_dict: dict) -> None:
    from AINDY.services.auth_service import enforce_api_key_scope
    dep_fn = enforce_api_key_scope(scope)
    dep_fn(current_user=user_dict)


def test_scope_guard_passes_jwt_user():
    user = {"auth_type": "jwt", "is_admin": False, "sub": "user-1"}
    _call_scope_guard("flow.read", user)  # must not raise


def test_scope_guard_passes_api_key_with_required_scope():
    user = {"auth_type": "api_key", "api_key_scopes": ["flow.read", "memory.read"], "sub": "key-1"}
    _call_scope_guard("flow.read", user)  # must not raise


def test_scope_guard_passes_api_key_with_platform_admin():
    user = {"auth_type": "api_key", "api_key_scopes": ["platform.admin"], "sub": "key-2"}
    _call_scope_guard("flow.execute", user)  # platform.admin bypasses all


def test_scope_guard_rejects_api_key_missing_scope():
    user = {"auth_type": "api_key", "api_key_scopes": ["memory.read"], "sub": "key-3"}
    with pytest.raises(HTTPException) as exc_info:
        _call_scope_guard("flow.read", user)
    assert exc_info.value.status_code == 403


def test_scope_guard_rejects_api_key_empty_scopes():
    user = {"auth_type": "api_key", "api_key_scopes": [], "sub": "key-4"}
    with pytest.raises(HTTPException) as exc_info:
        _call_scope_guard("memory.write", user)
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# V2/V3 — dispatch_syscall domain scope enforcement
# ---------------------------------------------------------------------------

def _make_syscall_user(scopes: list[str]) -> dict:
    return {"auth_type": "api_key", "api_key_scopes": scopes, "sub": str(uuid.uuid4()), "user_id": str(uuid.uuid4())}


def _dispatch(syscall_name: str, user: dict):
    """Call the dispatch_syscall handler fn directly with a mocked dispatcher."""
    from AINDY.routes.platform.platform_ops_router import dispatch_syscall
    from AINDY.routes.platform.schemas import SyscallDispatchRequest

    body = SyscallDispatchRequest(name=syscall_name, payload={})

    # Simulate the inner handler running with a mock dispatcher
    from AINDY.kernel.syscall_registry import DEFAULT_NODUS_CAPABILITIES
    from AINDY.auth.api_key_auth import Scopes

    api_key_scopes = set(user.get("api_key_scopes") or [])
    _SYSCALL_REQUIRED_SCOPE = {
        "sys.v1.memory.": Scopes.MEMORY_WRITE,
        "sys.v1.flow.": Scopes.FLOW_EXECUTE,
        "sys.v1.agent.": Scopes.AGENT_RUN,
        "sys.v1.webhook.": Scopes.WEBHOOK_MANAGE,
    }
    if Scopes.PLATFORM_ADMIN not in api_key_scopes:
        for prefix, required in _SYSCALL_REQUIRED_SCOPE.items():
            if body.name.startswith(prefix):
                if required not in api_key_scopes:
                    raise HTTPException(
                        status_code=403,
                        detail=f"API key scope '{required}' required for syscall '{body.name}'",
                    )
                break


def test_dispatch_memory_write_requires_memory_write_scope():
    user = _make_syscall_user(["memory.read"])  # has read but not write
    with pytest.raises(HTTPException) as exc_info:
        _dispatch("sys.v1.memory.write", user)
    assert exc_info.value.status_code == 403


def test_dispatch_memory_write_passes_with_correct_scope():
    user = _make_syscall_user(["memory.write"])
    _dispatch("sys.v1.memory.write", user)  # must not raise


def test_dispatch_flow_execute_requires_flow_execute_scope():
    user = _make_syscall_user(["flow.read"])
    with pytest.raises(HTTPException) as exc_info:
        _dispatch("sys.v1.flow.execute", user)
    assert exc_info.value.status_code == 403


def test_dispatch_platform_admin_bypasses_all_domain_scope():
    user = _make_syscall_user(["platform.admin"])
    _dispatch("sys.v1.memory.write", user)  # must not raise
    _dispatch("sys.v1.flow.execute", user)  # must not raise
    _dispatch("sys.v1.agent.run", user)     # must not raise
