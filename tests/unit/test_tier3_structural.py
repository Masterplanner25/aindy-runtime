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


def test_scope_guard_passes_jwt_user_holding_the_scope():
    """HTTP-SCOPE-GAP-1 — was `test_scope_guard_passes_jwt_user`, and it asserted the defect.

    It previously passed a JWT dict with **no scopes at all** and required `flow.read` to be
    allowed, encoding the old rule that *"JWT users carry full trust and are never gated"* —
    which made a browser session strictly more privileged than any API key.

    Kept rather than deleted, and rewritten to the new contract: a session passes a scope it
    **holds**. `flow.read` is in the ordinary derived set, so the same route it was protecting
    still works for the same user; only the reason changed from "JWT" to "grants".
    """
    from AINDY.auth.api_key_auth import derive_session_scopes

    user = {
        "auth_type": "jwt",
        "is_admin": False,
        "sub": "user-1",
        "session_scopes": derive_session_scopes(is_admin=False),
    }
    _call_scope_guard("flow.read", user)  # must not raise


def test_scope_guard_rejects_jwt_user_without_the_scope():
    """The other half of the same change — fail closed rather than trust the token type."""
    from AINDY.auth.api_key_auth import derive_session_scopes

    user = {
        "auth_type": "jwt",
        "is_admin": False,
        "sub": "user-1",
        "session_scopes": derive_session_scopes(is_admin=False),
    }
    with pytest.raises(HTTPException) as exc_info:
        _call_scope_guard("platform.admin", user)
    assert exc_info.value.status_code == 403


def test_scope_guard_rejects_jwt_user_carrying_no_grant():
    """A principal with no `session_scopes` has no authority — deny, do not assume.

    Real requests always carry a grant (seeded in `_resolve_authenticated_jwt_user` before its
    degraded return paths), so this is the fail-closed behaviour for a hand-built or truncated
    principal rather than a reachable state.
    """
    with pytest.raises(HTTPException) as exc_info:
        _call_scope_guard("flow.read", {"auth_type": "jwt", "is_admin": False, "sub": "u"})
    assert exc_info.value.status_code == 403


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


def _dispatch(syscall_name: str, user: dict) -> list[str]:
    """Resolve the least-privilege capability grant for a dispatch, via the real
    router helper (SDK-SYSCALL-GRANT-1). Returns the granted capability list;
    raises HTTPException(403) when an API-key lacks an authorizing scope."""
    from AINDY.routes.platform.platform_ops_router import _resolve_dispatch_capabilities

    return _resolve_dispatch_capabilities(syscall_name, user)


def _jwt_user() -> dict:
    return {"auth_type": "jwt", "sub": str(uuid.uuid4()), "user_id": str(uuid.uuid4())}


# --- memory: write requires write scope; read honors read-or-write scope -------

def test_dispatch_memory_write_requires_memory_write_scope():
    user = _make_syscall_user(["memory.read"])  # has read but not write
    with pytest.raises(HTTPException) as exc_info:
        _dispatch("sys.v1.memory.write", user)
    assert exc_info.value.status_code == 403


def test_dispatch_memory_write_passes_with_correct_scope():
    user = _make_syscall_user(["memory.write"])
    assert _dispatch("sys.v1.memory.write", user) == ["memory.write"]


def test_dispatch_memory_read_honors_read_scope():
    user = _make_syscall_user(["memory.read"])
    assert _dispatch("sys.v1.memory.read", user) == ["memory.read"]


def test_dispatch_memory_read_honors_write_scope():
    # write implies read — a write-scoped key can still read
    user = _make_syscall_user(["memory.write"])
    assert _dispatch("sys.v1.memory.read", user) == ["memory.read"]


def test_dispatch_memory_read_rejected_without_memory_scope():
    user = _make_syscall_user(["flow.execute"])
    with pytest.raises(HTTPException) as exc_info:
        _dispatch("sys.v1.memory.read", user)
    assert exc_info.value.status_code == 403


# --- flow.run: the previously-ungrantable capability (SDK-SYSCALL-GRANT-1) ------

def test_dispatch_flow_run_granted_for_jwt():
    assert _dispatch("sys.v1.flow.run", _jwt_user()) == ["flow.run"]


def test_dispatch_flow_run_requires_flow_execute_scope_for_api_key():
    user = _make_syscall_user(["flow.read"])  # read but not execute
    with pytest.raises(HTTPException) as exc_info:
        _dispatch("sys.v1.flow.run", user)
    assert exc_info.value.status_code == 403


def test_dispatch_flow_run_granted_with_flow_execute_scope():
    user = _make_syscall_user(["flow.execute"])
    assert _dispatch("sys.v1.flow.run", user) == ["flow.run"]


# --- event.emit: now grantable to API-keys via the event.emit scope ------------

def test_dispatch_event_emit_granted_for_jwt():
    assert _dispatch("sys.v1.event.emit", _jwt_user()) == ["event.emit"]


def test_dispatch_event_emit_requires_event_emit_scope_for_api_key():
    user = _make_syscall_user(["memory.write"])  # no event.emit scope
    with pytest.raises(HTTPException) as exc_info:
        _dispatch("sys.v1.event.emit", user)
    assert exc_info.value.status_code == 403


def test_dispatch_event_emit_granted_with_event_emit_scope():
    user = _make_syscall_user(["event.emit"])
    assert _dispatch("sys.v1.event.emit", user) == ["event.emit"]


# --- execution.read: unchanged — API-key needs the execution.read scope --------

def test_dispatch_execution_get_requires_execution_read_scope():
    user = _make_syscall_user(["memory.read"])
    with pytest.raises(HTTPException) as exc_info:
        _dispatch("sys.v1.execution.get", user)
    assert exc_info.value.status_code == 403


def test_dispatch_execution_get_granted_with_execution_read_scope():
    user = _make_syscall_user(["execution.read"])
    assert _dispatch("sys.v1.execution.get", user) == ["execution.read"]


# --- off-surface + admin -------------------------------------------------------

def test_dispatch_offsurface_syscall_grants_nothing():
    # agent / job / nodus syscalls are not on the public dispatch surface;
    # they grant no capability and the dispatcher denies them downstream.
    admin = _make_syscall_user(["platform.admin"])
    assert _dispatch("sys.v1.agent.run", admin) == []
    assert _dispatch("sys.v1.job.submit", admin) == []
    assert _dispatch("sys.v1.nodus.execute", admin) == []


def test_dispatch_unknown_syscall_grants_nothing():
    assert _dispatch("sys.v1.bogus.call", _jwt_user()) == []


def test_dispatch_platform_admin_bypasses_scope_gate():
    user = _make_syscall_user(["platform.admin"])
    assert _dispatch("sys.v1.memory.write", user) == ["memory.write"]
    assert _dispatch("sys.v1.flow.run", user) == ["flow.run"]
    assert _dispatch("sys.v1.event.emit", user) == ["event.emit"]
