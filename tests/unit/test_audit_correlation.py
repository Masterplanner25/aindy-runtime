"""AUDIT-CORRELATION-1 — the audit trail can join an effect to the dispatch that produced it,
and a dispatch to the authority it required (DEC-052..055).

Before: `syscall.executed` carried `syscall_name`, `execution_unit_id`, `status`, `extension_call`
— not the capability the dispatch required, not the guarantee the gate was asked for, and not the
`action_id` that names the ledger row. The only join was
`effect_records.execution_id = payload->>'execution_unit_id'`: unindexed JSONB on one side, and
it yields every event of the UNIT, not the one dispatch. On the tool path the admission event
(`capability.allowed`) was emitted before the action id existed.

Now three additive keys on `syscall.executed` (`capability`, `guarantee`, `action_id`) and
`action_id` on `capability.allowed`. No schema; no FK in either direction (the ledger row commits
in its own session before the handler runs, the event is written after on another session under
a swallowing try) — a documented convention on the unique-indexed `action_id` column.

★ These tests read the emitted event row and the ledger row back through SEPARATE sessions on
the real engine (the dispatcher opens its own `SessionLocal()` for both), not through a mock of
the emit — a mock would let the payload be right in the call and wrong in the row.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

import AINDY.kernel.syscall_dispatcher as syscall_dispatcher
import AINDY.kernel.syscall_registry as syscall_registry
from AINDY.core.execution_gate import compute_action_id

pytestmark = pytest.mark.runtime_only

_SYSCALL = "sys.v1.test.audit_join"
_CAP = "test.audit.capability"


class _OkRm:
    def check_quota(self, execution_unit_id):
        return True, None

    def record_usage(self, execution_unit_id, usage):
        return None


@pytest.fixture
def real_sessions(testing_session_factory, monkeypatch):
    """Route every `SessionLocal()` the dispatcher opens (gate + emit) to the test engine, and
    hand back a reader that opens a FRESH session — so a row is only visible if it was committed."""
    monkeypatch.setattr("AINDY.db.database.SessionLocal", testing_session_factory)
    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())
    monkeypatch.setattr(syscall_dispatcher, "_syscall_idempotency_enabled", lambda: True)
    monkeypatch.setattr(syscall_dispatcher, "_syscall_idempotency_strict_enabled", lambda: False)
    created: list = []

    def _read(model, **where):
        s = testing_session_factory()
        try:
            q = s.query(model)
            for k, v in where.items():
                q = q.filter(getattr(model, k) == v)
            rows = q.all()
            created.extend(rows)
            return rows
        finally:
            s.close()

    yield _read
    # Leave the session-scoped engine as we found it for whoever runs next.
    from AINDY.db.models.effect_record import EffectRecord
    from AINDY.db.models.system_event import SystemEvent

    s = testing_session_factory()
    try:
        for model, col in ((EffectRecord, "action_type"), (SystemEvent, "source")):
            s.query(model).filter(getattr(model, col) == (_SYSCALL if model is EffectRecord else "syscall_dispatcher")).delete(
                synchronize_session=False
            )
        s.commit()
    finally:
        s.close()


def _register(guarantee):
    syscall_registry.SYSCALL_REGISTRY[_SYSCALL] = syscall_registry.SyscallEntry(
        handler=lambda payload, context: {"ok": True},
        capability=_CAP,
        execution_guarantee=guarantee,
    )


@pytest.fixture(autouse=True)
def _unregister():
    yield
    syscall_registry.SYSCALL_REGISTRY.pop(_SYSCALL, None)


def _ctx():
    return syscall_registry.SyscallContext(
        execution_unit_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        capabilities=[_CAP],
        trace_id=f"trace-{uuid.uuid4()}",
    )


def _event_for(read, trace_id):
    from AINDY.db.models.system_event import SystemEvent

    rows = [r for r in read(SystemEvent, trace_id=trace_id) if r.type == "syscall.executed"]
    assert len(rows) == 1, f"expected exactly one syscall.executed for the trace, got {len(rows)}"
    return rows[0]


# ── Join (3): effect ↔ the dispatch that produced it ─────────────────────────


def test_a_gated_dispatch_names_its_effect_record(real_sessions):
    """★ The join the entry wanted. The event row's `action_id` IS the ledger row's key."""
    from AINDY.db.models.effect_record import EffectRecord

    _register("EXACTLY_ONCE")
    ctx = _ctx()
    payload = {"k": "v"}

    result = syscall_dispatcher.SyscallDispatcher().dispatch(_SYSCALL, payload, ctx)

    assert result["status"] == "success", result
    expected = compute_action_id(action_type=_SYSCALL, input_payload=payload, scope=ctx.execution_unit_id)
    ledger = real_sessions(EffectRecord, action_id=expected)
    assert len(ledger) == 1, "the gate did not engage — this test is not exercising the join"
    assert ledger[0].status == "success"

    event = _event_for(real_sessions, ctx.trace_id)
    assert event.payload["action_id"] == expected == ledger[0].action_id
    # dispatch → effect is a lookup on the unique-indexed column; effect → dispatch is the
    # payload key. Both directions, from the rows, not from the values we passed in.
    assert real_sessions(EffectRecord, action_id=event.payload["action_id"])[0].id == ledger[0].id


def test_an_ungated_dispatch_carries_a_null_action_id(real_sessions):
    """Control for the gate branch: `AT_LEAST_ONCE` never touches the ledger, so the key is
    present and null — a reader can tell "no effect record exists" from "the key was dropped"."""
    from AINDY.db.models.effect_record import EffectRecord

    _register("AT_LEAST_ONCE")
    ctx = _ctx()

    result = syscall_dispatcher.SyscallDispatcher().dispatch(_SYSCALL, {"k": "v"}, ctx)

    assert result["status"] == "success"
    event = _event_for(real_sessions, ctx.trace_id)
    assert "action_id" in event.payload
    assert event.payload["action_id"] is None
    assert real_sessions(EffectRecord, action_type=_SYSCALL) == []


# ── Join (1): dispatch ↔ the authority it required ───────────────────────────


def test_the_event_carries_the_capability_and_guarantee(real_sessions):
    _register("EXACTLY_ONCE")
    ctx = _ctx()

    syscall_dispatcher.SyscallDispatcher().dispatch(_SYSCALL, {}, ctx)

    event = _event_for(real_sessions, ctx.trace_id)
    assert event.payload["capability"] == _CAP
    assert event.payload["guarantee"] == "EXACTLY_ONCE"
    # The keys that were there before are still there — additive, never a rename.
    assert event.payload["syscall_name"] == _SYSCALL
    assert event.payload["execution_unit_id"] == ctx.execution_unit_id
    assert event.payload["status"] == "success"


def test_a_handler_error_still_carries_the_keys(real_sessions):
    """The error emit sites are the same helper: a failed dispatch is as joinable as a
    successful one — that is when the join is wanted most."""
    from AINDY.db.models.effect_record import EffectRecord

    def _boom(payload, context):
        raise RuntimeError("handler failed")

    syscall_registry.SYSCALL_REGISTRY[_SYSCALL] = syscall_registry.SyscallEntry(
        handler=_boom, capability=_CAP, execution_guarantee="EXACTLY_ONCE"
    )
    ctx = _ctx()
    payload = {"x": 1}

    result = syscall_dispatcher.SyscallDispatcher().dispatch(_SYSCALL, payload, ctx)

    assert result["status"] == "error"
    expected = compute_action_id(action_type=_SYSCALL, input_payload=payload, scope=ctx.execution_unit_id)
    assert real_sessions(EffectRecord, action_id=expected)[0].status == "failed"
    event = _event_for(real_sessions, ctx.trace_id)
    assert event.payload["status"] == "error"
    assert event.payload["action_id"] == expected
    assert event.payload["capability"] == _CAP


# ── Tool path: the admission event names the ledger row ──────────────────────


def _tool_admission_event(*, idempotency_on: bool):
    """Drive `execute_tool` with a granted token and capture the `capability.allowed` payload.

    The ledger is mocked on this path (`resolve_effect_record` is a PG-typed write the unit
    harness cannot commit for a tool); the assertion is that the id on the event is the id the
    ledger was asked to reserve, computed the same way.
    """
    from AINDY.agents import tool_registry as tr

    tool = "test.audit_tool"
    tr.TOOL_REGISTRY[tool] = {
        "fn": lambda args, user_id, db: {"ok": True},
        "risk": "low", "description": "t", "capability": "read_memory",
        "required_capability": "read_memory", "category": "test", "egress_scope": "internal",
        "execution_guarantee": "EXACTLY_ONCE",
    }
    events: list = []
    reserved: list = []
    run_id = str(uuid.uuid4())
    args = {"a": 1}

    def _resolve(db, action_id, *a, **k):
        reserved.append(action_id)
        return False, None

    try:
        with patch.object(tr, "_ensure_tools_loaded", lambda: None), patch(
            "AINDY.agents.capability_service.check_tool_capability",
            return_value={"ok": True, "allowed_capabilities": ["read_memory"], "granted_tools": [tool]},
        ), patch.object(tr, "queue_system_event", lambda **k: events.append(k)), patch.object(
            tr, "_tool_idempotency_enabled", lambda: idempotency_on
        ), patch("AINDY.kernel.effect_ledger.resolve_effect_record", _resolve), patch.object(
            tr, "_finalize_tool_effect", lambda *a, **k: None
        ):
            result = tr.execute_tool(tool, args, "user-1", MagicMock(), run_id=run_id, execution_token={"t": 1})
    finally:
        tr.TOOL_REGISTRY.pop(tool, None)
    assert result["success"] is True, result
    admission = [e for e in events if e["event_type"] == "capability.allowed"]
    assert len(admission) == 1
    return admission[0]["payload"], reserved, compute_action_id(action_type=tool, input_payload=args, scope=run_id)


def test_the_tool_admission_event_names_the_ledger_row():
    payload, reserved, expected = _tool_admission_event(idempotency_on=True)

    assert reserved == [expected], "the ledger was not asked for the id this test expects"
    assert payload["action_id"] == expected
    assert payload["allowed_capabilities"] == ["read_memory"]  # still there


def test_the_tool_admission_event_is_null_when_the_gate_is_off():
    payload, reserved, _ = _tool_admission_event(idempotency_on=False)

    assert reserved == []
    assert "action_id" in payload and payload["action_id"] is None


# ── What is NOT built ────────────────────────────────────────────────────────


def test_no_foreign_key_between_the_ledger_and_the_events():
    """DEC-054 — a documented convention on the unique-indexed column, no FK either way. An FK
    would either require the event before the effect (it is written after) or turn a swallowed
    emit failure into a constraint violation."""
    from AINDY.db.models.effect_record import EffectRecord
    from AINDY.db.models.system_event import SystemEvent

    # The self-reference `system_events.parent_event_id → system_events` is the causal graph,
    # not this join; what must not exist is an FK BETWEEN the two tables.
    for model, other in ((EffectRecord, "system_events"), (SystemEvent, "effect_records")):
        for col in model.__table__.columns:
            for fk in col.foreign_keys:
                assert fk.column.table.name != other, (
                    f"{model.__tablename__}.{col.name} → {other}: the join is a convention, not a constraint"
                )
    assert any(idx.unique and [c.name for c in idx.columns] == ["action_id"] for idx in EffectRecord.__table__.indexes) or any(
        c.name == "action_id" and c.unique for c in EffectRecord.__table__.columns
    ), "the join relies on action_id being unique-indexed"


def test_syscall_executed_stays_operational_retention():
    """DEC-055 — the join is time-bounded on both sides by construction; this design does not
    reclass the event. A deployment that wants it longer widens the operational window."""
    from AINDY.core.system_event_retention import RETENTION_OPERATIONAL, retention_class_for

    assert retention_class_for("syscall.executed") == RETENTION_OPERATIONAL
