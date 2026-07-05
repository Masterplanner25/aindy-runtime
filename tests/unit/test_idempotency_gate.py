"""
tests/unit/test_idempotency_gate.py
────────────────────────────────────
Unit tests for the NF-5 idempotency gate in SyscallDispatcher.dispatch().

All DB interaction is mocked — no real database required.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

import pytest
from sqlalchemy.exc import IntegrityError

import AINDY.kernel.syscall_dispatcher as syscall_dispatcher
import AINDY.kernel.syscall_registry as syscall_registry
from AINDY.core.execution_gate import compute_action_id

pytestmark = pytest.mark.runtime_only


# ── IntegrityError helper ─────────────────────────────────────────────────────

def _make_integrity_error():
    """Create an IntegrityError whose string includes the uq_effect_records_action_id constraint name."""
    orig = Exception(
        'duplicate key value violates unique constraint "uq_effect_records_action_id"'
    )
    return IntegrityError("INSERT INTO effect_records ...", {}, orig)


# ── Helpers ───────────────────────────────────────────────────────────────────

_SYSCALL_NAME = "sys.v1.test.gate_test"

# The idempotency gate keys ExecutionUnit lookups on a UUID primary-key column,
# so a realistic execution_unit_id for the EXACTLY_ONCE path is a bare UUID. A
# non-UUID id (e.g. 'run_<uuid>' / a trace id) can never match that column on a
# real database and now short-circuits to AT_LEAST_ONCE without a query (#157).
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


def _register_handler(handler=None, name=_SYSCALL_NAME):
    if handler is None:
        handler = lambda payload, context: {"result": "ok"}
    syscall_registry.SYSCALL_REGISTRY[name] = syscall_registry.SyscallEntry(
        handler=handler,
        capability="test.capability",
    )


def _unregister(name=_SYSCALL_NAME):
    syscall_registry.SYSCALL_REGISTRY.pop(name, None)


def _make_eu(guarantee: str):
    """Fake EU ORM object with the given execution_guarantee."""
    eu = MagicMock()
    eu.extra = {"retry_policy": {"execution_guarantee": guarantee}}
    return eu


def _make_gate_db(eu, effect_record=None):
    """Fake SQLAlchemy session that returns `eu` on EU query and `effect_record` on EffectRecord query."""
    db = MagicMock()
    query_mock = MagicMock()
    filter_mock = MagicMock()
    filter_mock.first.return_value = effect_record
    query_mock.filter.return_value = filter_mock
    db.query.return_value = query_mock
    return db


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_gate_skipped_for_at_least_once_syscall(monkeypatch):
    """AT_LEAST_ONCE syscalls must not interact with EffectRecord at all."""
    handler_calls = []
    _register_handler(lambda p, c: handler_calls.append(1) or {"called": True})

    dispatcher = syscall_dispatcher.SyscallDispatcher()
    dispatcher._emit_syscall_event = lambda *a, **kw: None

    # EU with AT_LEAST_ONCE guarantee
    eu = _make_eu("AT_LEAST_ONCE")
    gate_db = _make_gate_db(eu)

    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())

    def fake_session_local():
        gate_db.query.return_value.filter.return_value.first.return_value = eu
        return gate_db

    with patch("AINDY.db.database.SessionLocal", fake_session_local):
        result = dispatcher.dispatch(_SYSCALL_NAME, {}, _ctx())

    _unregister()

    assert handler_calls == [1], "handler must be called for AT_LEAST_ONCE"
    assert result["status"] == "success"
    # EffectRecord must not have been queried with EffectRecord model
    # (only ExecutionUnit was queried; second query for EffectRecord never happened)
    from AINDY.db.models.effect_record import EffectRecord
    er_calls = [c for c in gate_db.query.call_args_list if c[0] and c[0][0] is EffectRecord]
    assert len(er_calls) == 0, "EffectRecord must not be queried for AT_LEAST_ONCE syscalls"


def test_gate_short_circuits_on_existing_success_record(monkeypatch):
    """When a success EffectRecord exists, handler must NOT be called."""
    handler_calls = []
    _register_handler(lambda p, c: handler_calls.append(1) or {"fresh": True})

    dispatcher = syscall_dispatcher.SyscallDispatcher()
    dispatcher._emit_syscall_event = lambda *a, **kw: None

    cached_payload = {"cached": "result"}
    eu = _make_eu("EXACTLY_ONCE")

    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())

    call_count = [0]

    def fake_resolve(db, action_id, action_type, payload):
        call_count[0] += 1
        return True, cached_payload

    monkeypatch.setattr(syscall_dispatcher, "_resolve_effect_record", fake_resolve)

    gate_db = MagicMock()
    gate_db.query.return_value.filter.return_value.first.return_value = eu

    with patch("AINDY.db.database.SessionLocal", return_value=gate_db):
        result = dispatcher.dispatch(_SYSCALL_NAME, {}, _ctx())

    _unregister()

    assert handler_calls == [], "handler must NOT be called on cache hit"
    assert result["status"] == "success"
    assert result["data"] == cached_payload
    assert call_count[0] == 1


def test_gate_calls_handler_when_no_prior_record(monkeypatch):
    """When no EffectRecord exists, handler is called and record updated to success."""
    handler_calls = []
    _register_handler(lambda p, c: handler_calls.append(1) or {"fresh": True})

    dispatcher = syscall_dispatcher.SyscallDispatcher()
    dispatcher._emit_syscall_event = lambda *a, **kw: None

    eu = _make_eu("EXACTLY_ONCE")

    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())

    resolve_calls = []
    complete_calls = []

    def fake_resolve(db, action_id, action_type, payload):
        resolve_calls.append(action_id)
        return False, None

    def fake_complete(db, action_id, status, result_payload):
        complete_calls.append((action_id, status))

    monkeypatch.setattr(syscall_dispatcher, "_resolve_effect_record", fake_resolve)
    monkeypatch.setattr(syscall_dispatcher, "_complete_effect_record", fake_complete)

    gate_db = MagicMock()
    gate_db.query.return_value.filter.return_value.first.return_value = eu

    with patch("AINDY.db.database.SessionLocal", return_value=gate_db):
        result = dispatcher.dispatch(_SYSCALL_NAME, {}, _ctx())

    _unregister()

    assert handler_calls == [1], "handler must be called on cache miss"
    assert result["status"] == "success"
    assert len(resolve_calls) == 1
    assert len(complete_calls) == 1
    assert complete_calls[0][1] == "success"


def test_gate_marks_failed_on_handler_exception(monkeypatch):
    """When the handler raises, EffectRecord must be updated to 'failed'."""
    def _raising_handler(payload, context):
        raise RuntimeError("handler blew up")

    _register_handler(_raising_handler)

    dispatcher = syscall_dispatcher.SyscallDispatcher()
    dispatcher._emit_syscall_event = lambda *a, **kw: None

    eu = _make_eu("EXACTLY_ONCE")

    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())

    complete_calls = []

    def fake_resolve(db, action_id, action_type, payload):
        return False, None

    def fake_complete(db, action_id, status, result_payload):
        complete_calls.append((action_id, status))

    monkeypatch.setattr(syscall_dispatcher, "_resolve_effect_record", fake_resolve)
    monkeypatch.setattr(syscall_dispatcher, "_complete_effect_record", fake_complete)

    gate_db = MagicMock()
    gate_db.query.return_value.filter.return_value.first.return_value = eu

    with patch("AINDY.db.database.SessionLocal", return_value=gate_db):
        result = dispatcher.dispatch(_SYSCALL_NAME, {}, _ctx())

    _unregister()

    assert result["status"] == "error"
    assert "handler blew up" in result["error"]
    assert len(complete_calls) == 1
    assert complete_calls[0][1] == "failed"


def test_gate_absent_execution_unit_skips_gate(monkeypatch):
    """A context with no execution_unit_id must skip the gate entirely."""
    handler_calls = []
    _register_handler(lambda p, c: handler_calls.append(1) or {"ok": True})

    dispatcher = syscall_dispatcher.SyscallDispatcher()
    dispatcher._emit_syscall_event = lambda *a, **kw: None

    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())

    session_opens = []

    def fake_session_local():
        session_opens.append(1)
        return MagicMock()

    ctx = syscall_registry.SyscallContext(
        execution_unit_id="",  # empty — no EU
        user_id="user-1",
        capabilities=["test.capability"],
        trace_id="trace-1",
    )

    with patch("AINDY.db.database.SessionLocal", fake_session_local):
        result = dispatcher.dispatch(_SYSCALL_NAME, {}, ctx)

    _unregister()

    assert handler_calls == [1], "handler must be called when no EU"
    assert result["status"] == "success"
    # Session was NOT opened (gate skipped because eu_id is empty)
    assert session_opens == [], "no DB session should be opened when eu_id is absent"


def test_gate_skips_lookup_for_run_scoped_non_uuid_eu_id(monkeypatch):
    """#157: a run-scoped execution_unit_id ('run_<uuid>') must NOT reach the
    ExecutionUnit UUID lookup. On PostgreSQL that binds a non-UUID to a UUID
    column, raising InvalidTextRepresentation and aborting the transaction, which
    then cascades into InFailedSqlTransaction on the handler's INSERT. The gate
    must short-circuit to AT_LEAST_ONCE without opening a session or querying."""
    handler_calls = []
    _register_handler(lambda p, c: handler_calls.append(1) or {"ok": True})

    dispatcher = syscall_dispatcher.SyscallDispatcher()
    dispatcher._emit_syscall_event = lambda *a, **kw: None

    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())

    session_opens = []

    def fake_session_local():
        session_opens.append(1)
        return MagicMock()

    run_scoped_id = "run_897ef792-4918-44fa-856a-ebdbbd548859"

    with patch("AINDY.db.database.SessionLocal", fake_session_local):
        result = dispatcher.dispatch(_SYSCALL_NAME, {}, _ctx(eu_id=run_scoped_id))

    _unregister()

    assert handler_calls == [1], "handler must still run for a run-scoped EU id"
    assert result["status"] == "success"
    assert session_opens == [], (
        "no DB session should be opened for a non-UUID execution_unit_id — the "
        "UUID lookup would poison the transaction (#157)"
    )


def test_gate_opens_lookup_for_valid_uuid_eu_id(monkeypatch):
    """Complement to #157: a bare-UUID execution_unit_id still drives the gate."""
    _register_handler(lambda p, c: {"ok": True})

    dispatcher = syscall_dispatcher.SyscallDispatcher()
    dispatcher._emit_syscall_event = lambda *a, **kw: None

    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())

    eu = _make_eu("AT_LEAST_ONCE")
    session_opens = []

    def fake_session_local():
        session_opens.append(1)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = eu
        return db

    with patch("AINDY.db.database.SessionLocal", fake_session_local):
        result = dispatcher.dispatch(_SYSCALL_NAME, {}, _ctx(eu_id=_VALID_EU_UUID))

    _unregister()

    assert result["status"] == "success"
    assert session_opens == [1], "a valid UUID execution_unit_id must open the gate lookup"


def test_compute_action_id_used_for_gate_key(monkeypatch):
    """The action_id passed to _resolve_effect_record equals compute_action_id output."""
    _register_handler(lambda p, c: {"ok": True})

    dispatcher = syscall_dispatcher.SyscallDispatcher()
    dispatcher._emit_syscall_event = lambda *a, **kw: None

    eu = _make_eu("EXACTLY_ONCE")
    eu_id = _VALID_EU_UUID
    test_payload = {"key": "value", "num": 42}

    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())

    received_action_ids = []

    def fake_resolve(db, action_id, action_type, payload):
        received_action_ids.append(action_id)
        return False, None

    monkeypatch.setattr(syscall_dispatcher, "_resolve_effect_record", fake_resolve)
    monkeypatch.setattr(syscall_dispatcher, "_complete_effect_record", lambda *a, **kw: None)

    gate_db = MagicMock()
    gate_db.query.return_value.filter.return_value.first.return_value = eu

    with patch("AINDY.db.database.SessionLocal", return_value=gate_db):
        dispatcher.dispatch(_SYSCALL_NAME, test_payload, _ctx(eu_id=eu_id))

    _unregister()

    assert len(received_action_ids) == 1
    expected = compute_action_id(
        action_type=_SYSCALL_NAME,
        input_payload=test_payload,
        scope=eu_id,
    )
    assert received_action_ids[0] == expected, (
        f"gate key mismatch: got {received_action_ids[0]!r}, expected {expected!r}"
    )


def test_exactly_once_non_dict_return_raises_contract_violation(monkeypatch):
    """EXACTLY_ONCE handler returning non-dict must raise SyscallContractViolation."""
    _register_handler(lambda p, c: "not a dict")

    dispatcher = syscall_dispatcher.SyscallDispatcher()
    dispatcher._emit_syscall_event = lambda *a, **kw: None

    eu = _make_eu("EXACTLY_ONCE")
    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())

    complete_calls = []

    def fake_resolve(db, action_id, action_type, payload):
        return False, None  # cache miss — handler runs

    def fake_complete(db, action_id, status, result_payload):
        complete_calls.append((action_id, status))

    monkeypatch.setattr(syscall_dispatcher, "_resolve_effect_record", fake_resolve)
    monkeypatch.setattr(syscall_dispatcher, "_complete_effect_record", fake_complete)

    gate_db = MagicMock()
    gate_db.query.return_value.filter.return_value.first.return_value = eu

    with patch("AINDY.db.database.SessionLocal", return_value=gate_db):
        with pytest.raises(syscall_dispatcher.SyscallContractViolation) as exc_info:
            dispatcher.dispatch(_SYSCALL_NAME, {}, _ctx())

    _unregister()

    assert "EXACTLY_ONCE" in str(exc_info.value)
    assert _SYSCALL_NAME in str(exc_info.value)
    assert len(complete_calls) == 1, "EffectRecord must be finalized before raise"
    assert complete_calls[0][1] == "failed"


def test_at_least_once_non_dict_return_does_not_raise(monkeypatch):
    """AT_LEAST_ONCE handler returning non-dict must NOT raise; returns error envelope."""
    _register_handler(lambda p, c: "not a dict")

    dispatcher = syscall_dispatcher.SyscallDispatcher()
    dispatcher._emit_syscall_event = lambda *a, **kw: None

    eu = _make_eu("AT_LEAST_ONCE")
    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())

    gate_db = MagicMock()
    gate_db.query.return_value.filter.return_value.first.return_value = eu

    with patch("AINDY.db.database.SessionLocal", return_value=gate_db):
        result = dispatcher.dispatch(_SYSCALL_NAME, {}, _ctx())

    _unregister()

    assert result["status"] == "error"
    assert "contract violation" in result["error"].lower()
    # No SyscallContractViolation raised — AT_LEAST_ONCE non-dict is a normal error.
    # No EffectRecord interaction — gate was closed before handler executed.


# ── In-band stale-pending recovery tests ─────────────────────────────────────
# These tests exercise _resolve_effect_record() directly with a mock DB.
# They simulate the concurrent-insert race: two callers both see record=None,
# both try to INSERT; the second hits the unique constraint IntegrityError.

def test_stale_pending_record_recovered_in_band():
    """On IntegrityError, a stale pending row is reset in-place and the slot is claimed."""
    from AINDY.kernel.syscall_dispatcher import (
        _resolve_effect_record,
        STALE_PENDING_THRESHOLD_SECONDS,
    )

    stale_time = (
        datetime.now(timezone.utc)
        - timedelta(seconds=STALE_PENDING_THRESHOLD_SECONDS + 60)
    )
    stale_record = MagicMock()
    stale_record.status = "pending"
    stale_record.created_at = stale_time
    stale_record.completed_at = MagicMock()  # has a value (will be cleared)

    db = MagicMock()
    # First query: None (TOCTOU — no row visible yet)
    # Second query (after rollback): the stale pending row
    db.query.return_value.filter.return_value.first.side_effect = [None, stale_record]
    db.commit.side_effect = [_make_integrity_error(), None]

    done, payload = _resolve_effect_record(db, "action-stale", "sys.v1.test", {})

    assert not done
    assert payload is None
    db.rollback.assert_called_once()
    assert stale_record.status == "pending"
    assert stale_record.completed_at is None
    # created_at must have been refreshed (no longer equal to the stale time)
    assert stale_record.created_at != stale_time
    assert db.commit.call_count == 2  # failed insert + recovery commit


def test_concurrent_live_pending_skips_gate():
    """On IntegrityError, a fresh pending row means another call is live — degrade to AT_LEAST_ONCE."""
    from AINDY.kernel.syscall_dispatcher import (
        _resolve_effect_record,
        STALE_PENDING_THRESHOLD_SECONDS,
    )

    fresh_record = MagicMock()
    fresh_record.status = "pending"
    fresh_record.created_at = datetime.now(timezone.utc)  # just created
    fresh_record.completed_at = None

    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [None, fresh_record]
    db.commit.side_effect = [_make_integrity_error(), None]

    done, payload = _resolve_effect_record(db, "action-live", "sys.v1.test", {})

    assert not done  # gate skipped; handler will run as AT_LEAST_ONCE
    assert payload is None
    db.rollback.assert_called_once()
    # No reset commit — only the failed INSERT attempt
    assert db.commit.call_count == 1


def test_unique_constraint_race_with_success_returns_cached():
    """On IntegrityError, if the concurrent call succeeded first, return the cached payload."""
    from AINDY.kernel.syscall_dispatcher import _resolve_effect_record

    success_record = MagicMock()
    success_record.status = "success"
    success_record.result_payload = {"winner": "first_caller"}

    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [None, success_record]
    db.commit.side_effect = _make_integrity_error()  # always raises

    done, payload = _resolve_effect_record(db, "action-race-success", "sys.v1.test", {})

    assert done is True
    assert payload == {"winner": "first_caller"}
    db.rollback.assert_called_once()


def test_failed_record_recovery():
    """On IntegrityError, a failed row is reset to pending (option a — retry after failure)."""
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
    assert failed_record.created_at != failed_time  # reset to now
    assert db.commit.call_count == 2
