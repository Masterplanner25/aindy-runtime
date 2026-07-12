"""
tests/unit/test_idempotency_gate.py
────────────────────────────────────
Unit tests for the syscall idempotency gate in SyscallDispatcher.dispatch().

MEB-1b: the gate fires from a per-syscall ``SyscallEntry.execution_guarantee``
declaration + the ``AINDY_SYSCALL_IDEMPOTENCY`` flag (mocked here via
``_syscall_idempotency_enabled``) — NOT the old ``ExecutionUnit.extra`` lookup.
All DB interaction is mocked — no real database required.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

import AINDY.kernel.syscall_dispatcher as syscall_dispatcher
import AINDY.kernel.syscall_registry as syscall_registry
from AINDY.core.execution_gate import compute_action_id

pytestmark = pytest.mark.runtime_only


def _make_integrity_error():
    orig = Exception(
        'duplicate key value violates unique constraint "uq_effect_records_action_id"'
    )
    return IntegrityError("INSERT INTO effect_records ...", {}, orig)


_SYSCALL_NAME = "sys.v1.test.gate_test"
_VALID_EU_UUID = "11111111-1111-1111-1111-111111111111"


class _OkRm:
    def check_quota(self, execution_unit_id):
        return True, None

    def record_usage(self, execution_unit_id, usage):
        return None


def _ctx(*, eu_id: str = _VALID_EU_UUID, capabilities=None):
    return syscall_registry.SyscallContext(
        execution_unit_id=eu_id,
        user_id="user-1",
        capabilities=capabilities or ["test.capability"],
        trace_id="trace-gate-1",
    )


def _register_handler(handler=None, name=_SYSCALL_NAME, guarantee="AT_LEAST_ONCE"):
    if handler is None:
        handler = lambda payload, context: {"result": "ok"}  # noqa: E731
    syscall_registry.SYSCALL_REGISTRY[name] = syscall_registry.SyscallEntry(
        handler=handler,
        capability="test.capability",
        execution_guarantee=guarantee,
    )


def _unregister(name=_SYSCALL_NAME):
    syscall_registry.SYSCALL_REGISTRY.pop(name, None)


def _dispatcher(monkeypatch, *, flag=True):
    d = syscall_dispatcher.SyscallDispatcher()
    d._emit_syscall_event = lambda *a, **kw: None
    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())
    monkeypatch.setattr(syscall_dispatcher, "_syscall_idempotency_enabled", lambda: flag)
    return d


# ── Gate firing / gating ──────────────────────────────────────────────────────

def test_gate_skipped_for_at_least_once_syscall(monkeypatch):
    """AT_LEAST_ONCE syscalls never touch the effect ledger, even with the flag on."""
    handler_calls, resolve_calls = [], []
    _register_handler(lambda p, c: handler_calls.append(1) or {"called": True}, guarantee="AT_LEAST_ONCE")
    d = _dispatcher(monkeypatch, flag=True)
    monkeypatch.setattr(syscall_dispatcher, "_resolve_effect_record",
                        lambda *a, **k: resolve_calls.append(1) or (False, None))
    result = d.dispatch(_SYSCALL_NAME, {}, _ctx())
    _unregister()
    assert handler_calls == [1]
    assert result["status"] == "success"
    assert resolve_calls == [], "AT_LEAST_ONCE must not consult the effect ledger"


def test_gate_skipped_when_flag_off(monkeypatch):
    """An EXACTLY_ONCE syscall is not deduped when the global flag is off (default)."""
    handler_calls, resolve_calls = [], []
    _register_handler(lambda p, c: handler_calls.append(1) or {"ok": True}, guarantee="EXACTLY_ONCE")
    d = _dispatcher(monkeypatch, flag=False)
    monkeypatch.setattr(syscall_dispatcher, "_resolve_effect_record",
                        lambda *a, **k: resolve_calls.append(1) or (False, None))
    result = d.dispatch(_SYSCALL_NAME, {}, _ctx())
    _unregister()
    assert handler_calls == [1]
    assert result["status"] == "success"
    assert resolve_calls == []


def test_gate_short_circuits_on_existing_success_record(monkeypatch):
    """When a success EffectRecord exists, the handler must NOT be called (replay)."""
    handler_calls = []
    _register_handler(lambda p, c: handler_calls.append(1) or {"fresh": True}, guarantee="EXACTLY_ONCE")
    d = _dispatcher(monkeypatch, flag=True)
    cached = {"cached": "result"}
    monkeypatch.setattr(syscall_dispatcher, "_resolve_effect_record", lambda *a, **k: (True, cached))
    with patch("AINDY.db.database.SessionLocal", return_value=MagicMock()):
        result = d.dispatch(_SYSCALL_NAME, {}, _ctx())
    _unregister()
    assert handler_calls == []
    assert result["status"] == "success"
    assert result["data"] == cached


def test_gate_calls_handler_when_no_prior_record(monkeypatch):
    """Cache miss → handler runs and the record is finalized success."""
    handler_calls, resolve_calls, complete_calls = [], [], []
    _register_handler(lambda p, c: handler_calls.append(1) or {"fresh": True}, guarantee="EXACTLY_ONCE")
    d = _dispatcher(monkeypatch, flag=True)
    monkeypatch.setattr(syscall_dispatcher, "_resolve_effect_record",
                        lambda db, aid, at, pl, **k: resolve_calls.append(aid) or (False, None))
    monkeypatch.setattr(syscall_dispatcher, "_complete_effect_record",
                        lambda db, aid, st, rp: complete_calls.append((aid, st)))
    with patch("AINDY.db.database.SessionLocal", return_value=MagicMock()):
        result = d.dispatch(_SYSCALL_NAME, {}, _ctx())
    _unregister()
    assert handler_calls == [1]
    assert result["status"] == "success"
    assert len(resolve_calls) == 1
    assert complete_calls and complete_calls[0][1] == "success"


def test_gate_marks_failed_on_handler_exception(monkeypatch):
    """Handler raises → the record is finalized failed."""
    def _raise(p, c):
        raise RuntimeError("handler blew up")

    _register_handler(_raise, guarantee="EXACTLY_ONCE")
    d = _dispatcher(monkeypatch, flag=True)
    complete_calls = []
    monkeypatch.setattr(syscall_dispatcher, "_resolve_effect_record", lambda *a, **k: (False, None))
    monkeypatch.setattr(syscall_dispatcher, "_complete_effect_record",
                        lambda db, aid, st, rp: complete_calls.append((aid, st)))
    with patch("AINDY.db.database.SessionLocal", return_value=MagicMock()):
        result = d.dispatch(_SYSCALL_NAME, {}, _ctx())
    _unregister()
    assert result["status"] == "error"
    assert "handler blew up" in result["error"]
    assert complete_calls and complete_calls[0][1] == "failed"


def test_gate_absent_execution_unit_skips_gate(monkeypatch):
    """EXACTLY_ONCE + flag but no execution_unit_id → gate skipped (ledger untouched)."""
    handler_calls, resolve_calls = [], []
    _register_handler(lambda p, c: handler_calls.append(1) or {"ok": True}, guarantee="EXACTLY_ONCE")
    d = _dispatcher(monkeypatch, flag=True)
    monkeypatch.setattr(syscall_dispatcher, "_resolve_effect_record",
                        lambda *a, **k: resolve_calls.append(1) or (False, None))
    ctx = syscall_registry.SyscallContext(
        execution_unit_id="", user_id="user-1", capabilities=["test.capability"], trace_id="t")
    result = d.dispatch(_SYSCALL_NAME, {}, ctx)
    _unregister()
    assert handler_calls == [1]
    assert result["status"] == "success"
    assert resolve_calls == []


def test_gate_skips_non_uuid_scope(monkeypatch):
    """#157 guard retained: a run-scoped (non-UUID) execution_unit_id must NOT engage the
    gate, even EXACTLY_ONCE + flag on."""
    handler_calls, resolve_calls = [], []
    _register_handler(lambda p, c: handler_calls.append(1) or {"ok": True}, guarantee="EXACTLY_ONCE")
    d = _dispatcher(monkeypatch, flag=True)
    monkeypatch.setattr(syscall_dispatcher, "_resolve_effect_record",
                        lambda *a, **k: resolve_calls.append(1) or (False, None))
    result = d.dispatch(_SYSCALL_NAME, {}, _ctx(eu_id="run_897ef792-4918-44fa-856a-ebdbbd548859"))
    _unregister()
    assert handler_calls == [1]
    assert result["status"] == "success"
    assert resolve_calls == [], "non-UUID scope must not engage the gate (#157 guard)"


def test_gate_fires_for_valid_uuid_scope(monkeypatch):
    """The complement: a bare-UUID scope + EXACTLY_ONCE + flag fires the gate."""
    resolve_calls = []
    _register_handler(lambda p, c: {"ok": True}, guarantee="EXACTLY_ONCE")
    d = _dispatcher(monkeypatch, flag=True)
    monkeypatch.setattr(syscall_dispatcher, "_resolve_effect_record",
                        lambda *a, **k: resolve_calls.append(1) or (False, None))
    monkeypatch.setattr(syscall_dispatcher, "_complete_effect_record", lambda *a, **k: None)
    with patch("AINDY.db.database.SessionLocal", return_value=MagicMock()):
        result = d.dispatch(_SYSCALL_NAME, {}, _ctx(eu_id=_VALID_EU_UUID))
    _unregister()
    assert result["status"] == "success"
    assert resolve_calls == [1]


def test_compute_action_id_used_for_gate_key(monkeypatch):
    """The action_id passed to the ledger equals compute_action_id(name, payload, scope)."""
    received = []
    _register_handler(lambda p, c: {"ok": True}, guarantee="EXACTLY_ONCE")
    d = _dispatcher(monkeypatch, flag=True)
    monkeypatch.setattr(syscall_dispatcher, "_resolve_effect_record",
                        lambda db, aid, at, pl, **k: received.append(aid) or (False, None))
    monkeypatch.setattr(syscall_dispatcher, "_complete_effect_record", lambda *a, **k: None)
    payload = {"key": "value", "num": 42}
    with patch("AINDY.db.database.SessionLocal", return_value=MagicMock()):
        d.dispatch(_SYSCALL_NAME, payload, _ctx(eu_id=_VALID_EU_UUID))
    _unregister()
    assert received == [
        compute_action_id(action_type=_SYSCALL_NAME, input_payload=payload, scope=_VALID_EU_UUID)
    ]


def test_exactly_once_non_dict_return_raises_contract_violation(monkeypatch):
    """EXACTLY_ONCE handler returning non-dict raises SyscallContractViolation (finalized failed)."""
    complete_calls = []
    _register_handler(lambda p, c: "not a dict", guarantee="EXACTLY_ONCE")
    d = _dispatcher(monkeypatch, flag=True)
    monkeypatch.setattr(syscall_dispatcher, "_resolve_effect_record", lambda *a, **k: (False, None))
    monkeypatch.setattr(syscall_dispatcher, "_complete_effect_record",
                        lambda db, aid, st, rp: complete_calls.append((aid, st)))
    with patch("AINDY.db.database.SessionLocal", return_value=MagicMock()):
        with pytest.raises(syscall_dispatcher.SyscallContractViolation) as exc:
            d.dispatch(_SYSCALL_NAME, {}, _ctx())
    _unregister()
    assert "EXACTLY_ONCE" in str(exc.value)
    assert complete_calls and complete_calls[0][1] == "failed"


def test_at_least_once_non_dict_return_does_not_raise(monkeypatch):
    """AT_LEAST_ONCE non-dict is a normal error envelope, not a contract violation."""
    _register_handler(lambda p, c: "not a dict", guarantee="AT_LEAST_ONCE")
    d = _dispatcher(monkeypatch, flag=True)
    result = d.dispatch(_SYSCALL_NAME, {}, _ctx())
    _unregister()
    assert result["status"] == "error"
    assert "contract violation" in result["error"].lower()


# ── In-band stale-pending recovery (exercise the ledger primitive directly) ────
# resolve_effect_record lives in kernel/effect_ledger (imported into the dispatcher as
# _resolve_effect_record since MEB-1a); these tests are unaffected by the gate rewrite.

def test_stale_pending_record_recovered_in_band():
    from AINDY.kernel.syscall_dispatcher import (
        _resolve_effect_record,
        STALE_PENDING_THRESHOLD_SECONDS,
    )

    stale_time = datetime.now(timezone.utc) - timedelta(seconds=STALE_PENDING_THRESHOLD_SECONDS + 60)
    stale_record = MagicMock()
    stale_record.status = "pending"
    stale_record.created_at = stale_time
    stale_record.completed_at = MagicMock()

    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [None, stale_record]
    db.commit.side_effect = [_make_integrity_error(), None]

    done, payload = _resolve_effect_record(db, "action-stale", "sys.v1.test", {})

    assert not done
    assert payload is None
    db.rollback.assert_called_once()
    assert stale_record.status == "pending"
    assert stale_record.completed_at is None
    assert stale_record.created_at != stale_time
    assert db.commit.call_count == 2


def test_concurrent_live_pending_skips_gate():
    from AINDY.kernel.syscall_dispatcher import _resolve_effect_record

    fresh_record = MagicMock()
    fresh_record.status = "pending"
    fresh_record.created_at = datetime.now(timezone.utc)
    fresh_record.completed_at = None

    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [None, fresh_record]
    db.commit.side_effect = [_make_integrity_error(), None]

    done, payload = _resolve_effect_record(db, "action-live", "sys.v1.test", {})

    assert not done
    assert payload is None
    db.rollback.assert_called_once()
    assert db.commit.call_count == 1


def test_unique_constraint_race_with_success_returns_cached():
    from AINDY.kernel.syscall_dispatcher import _resolve_effect_record

    success_record = MagicMock()
    success_record.status = "success"
    success_record.result_payload = {"winner": "first_caller"}

    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [None, success_record]
    db.commit.side_effect = _make_integrity_error()

    done, payload = _resolve_effect_record(db, "action-race-success", "sys.v1.test", {})

    assert done is True
    assert payload == {"winner": "first_caller"}
    db.rollback.assert_called_once()


def test_failed_record_recovery():
    from AINDY.kernel.syscall_dispatcher import _resolve_effect_record

    failed_time = datetime.now(timezone.utc) - timedelta(hours=1)
    failed_record = MagicMock()
    failed_record.status = "failed"
    failed_record.created_at = failed_time
    failed_record.completed_at = failed_time

    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [None, failed_record]
    db.commit.side_effect = [_make_integrity_error(), None]

    done, payload = _resolve_effect_record(db, "action-failed", "sys.v1.test", {})

    assert not done
    assert payload is None
    db.rollback.assert_called_once()
    assert failed_record.status == "pending"
    assert failed_record.completed_at is None
    assert failed_record.created_at != failed_time
    assert db.commit.call_count == 2
