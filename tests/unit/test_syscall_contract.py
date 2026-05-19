from __future__ import annotations

import pytest

import AINDY.kernel.syscall_dispatcher as syscall_dispatcher
import AINDY.kernel.syscall_registry as syscall_registry


@pytest.fixture
def dispatcher():
    dispatcher = syscall_dispatcher.SyscallDispatcher()
    dispatcher._emit_syscall_event = lambda *args, **kwargs: None
    return dispatcher


def _ctx(*, capabilities: list[str] | None = None, metadata: dict | None = None):
    return syscall_registry.SyscallContext(
        execution_unit_id="eu-1",
        user_id="user-1",
        capabilities=capabilities or ["test.capability"],
        trace_id="trace-1",
        metadata=metadata or {},
    )


def test_dispatcher_quota_transport_failure_is_fail_open(monkeypatch, dispatcher):
    calls: list[str] = []
    name = "sys.v1.test.fail_open"
    syscall_registry.SYSCALL_REGISTRY[name] = syscall_registry.SyscallEntry(
        handler=lambda payload, context: calls.append("ran") or {"ok": True},
        capability="test.capability",
    )

    class _BrokenRm:
        def check_quota(self, execution_unit_id):
            raise RuntimeError("quota backend offline")

        def record_usage(self, execution_unit_id, usage):
            return None

    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _BrokenRm())
    try:
        result = dispatcher.dispatch(name, {}, _ctx())
    finally:
        syscall_registry.SYSCALL_REGISTRY.pop(name, None)

    assert calls == ["ran"]
    assert result["status"] == "success"
    assert result["data"] == {"ok": True}


def test_dispatcher_rejects_non_dict_handler_output(monkeypatch, dispatcher):
    name = "sys.v1.test.bad_output"
    syscall_registry.SYSCALL_REGISTRY[name] = syscall_registry.SyscallEntry(
        handler=lambda payload, context: ["not", "a", "dict"],
        capability="test.capability",
    )

    class _OkRm:
        def check_quota(self, execution_unit_id):
            return True, None

        def record_usage(self, execution_unit_id, usage):
            return None

    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())
    try:
        result = dispatcher.dispatch(name, {}, _ctx())
    finally:
        syscall_registry.SYSCALL_REGISTRY.pop(name, None)

    assert result["status"] == "error"
    assert "returned list, expected dict" in result["error"]


def test_dispatcher_output_schema_mismatch_is_warning_only(monkeypatch, dispatcher, caplog):
    name = "sys.v1.test.output_warning"
    syscall_registry.SYSCALL_REGISTRY[name] = syscall_registry.SyscallEntry(
        handler=lambda payload, context: {"present": True},
        capability="test.capability",
        output_schema={"required": ["missing"]},
    )

    class _OkRm:
        def check_quota(self, execution_unit_id):
            return True, None

        def record_usage(self, execution_unit_id, usage):
            return None

    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())
    try:
        result = dispatcher.dispatch(name, {}, _ctx())
    finally:
        syscall_registry.SYSCALL_REGISTRY.pop(name, None)

    assert result["status"] == "success"
    assert result["data"] == {"present": True}
    assert "output schema mismatch" in caplog.text


class _FakeQuery:
    def __init__(self, db, model):
        self.db = db
        self.model = model

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def first(self):
        return self.db.first_result

    def all(self):
        return list(self.db.all_result)

    def count(self):
        return self.db.count_result


class _FakeDb:
    def __init__(self):
        self.first_result = None
        self.all_result = []
        self.count_result = 0
        self.added = []
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.refresh_calls = 0
        self.close_calls = 0

    def query(self, model):
        return _FakeQuery(self, model)

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flush_calls += 1

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1

    def refresh(self, row):
        self.refresh_calls += 1
        if getattr(row, "id", None) is None:
            row.id = "run-created"

    def close(self):
        self.close_calls += 1


def test_agent_ensure_initial_run_preserves_external_db_transaction(monkeypatch):
    fake_db = _FakeDb()

    class _FakeAgentRun:
        user_id = "user_id"
        goal = "goal"

        def __init__(self, user_id, goal, status, overall_risk, steps_total):
            self.id = None
            self.user_id = user_id
            self.goal = goal
            self.status = status
            self.overall_risk = overall_risk
            self.steps_total = steps_total

    monkeypatch.setattr("AINDY.platform_layer.user_ids.parse_user_id", lambda user_id: user_id)
    monkeypatch.setattr("AINDY.db.models.AgentRun", _FakeAgentRun)

    result = syscall_registry._handle_agent_ensure_initial_run(
        {"user_id": "user-1"},
        _ctx(capabilities=["agent.write"], metadata={"_db": fake_db}),
    )

    assert result == {"run_id": "run-created", "created": True}
    assert fake_db.flush_calls == 1
    assert fake_db.commit_calls == 0
    assert fake_db.close_calls == 0


def test_event_emit_reuses_external_db_without_committing(monkeypatch):
    fake_db = _FakeDb()
    event_calls: list[dict] = []

    monkeypatch.setattr(
        "AINDY.core.system_event_service.emit_system_event",
        lambda **kwargs: event_calls.append(kwargs) or "evt-1",
    )

    result = syscall_registry._handle_event_emit(
        {"event_type": "test.event", "payload": {"x": 1}},
        _ctx(capabilities=["event.emit"], metadata={"_db": fake_db}),
    )

    assert result == {"event_id": "evt-1"}
    assert fake_db.flush_calls == 1
    assert fake_db.commit_calls == 0
    assert fake_db.close_calls == 0
    assert event_calls[0]["db"] is fake_db


def test_memory_read_family_uses_memory_read_capability():
    assert syscall_registry.SYSCALL_REGISTRY["sys.v1.memory.search"].capability == "memory.read"
    assert syscall_registry.SYSCALL_REGISTRY["sys.v1.memory.list"].capability == "memory.read"
    assert syscall_registry.SYSCALL_REGISTRY["sys.v1.memory.tree"].capability == "memory.read"
    assert syscall_registry.SYSCALL_REGISTRY["sys.v1.memory.trace"].capability == "memory.read"
