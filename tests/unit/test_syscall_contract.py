from __future__ import annotations

import pytest

import AINDY.kernel.syscall_dispatcher as syscall_dispatcher
import AINDY.kernel.syscall_registry as syscall_registry

pytestmark = pytest.mark.runtime_only


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


def test_dispatcher_quota_transport_failure_is_fail_open_in_development(monkeypatch, dispatcher):
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
    monkeypatch.setattr(syscall_dispatcher.settings, "ENV", "development")
    monkeypatch.setattr(syscall_dispatcher.settings, "TESTING", False)
    monkeypatch.setattr(syscall_dispatcher.settings, "TEST_MODE", False)
    try:
        result = dispatcher.dispatch(name, {}, _ctx())
    finally:
        syscall_registry.SYSCALL_REGISTRY.pop(name, None)

    assert calls == ["ran"]
    assert result["status"] == "success"
    assert result["data"] == {"ok": True}


def test_dispatcher_quota_transport_failure_fails_closed_in_production(monkeypatch, dispatcher):
    calls: list[str] = []
    name = "sys.v1.test.fail_closed"
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
    monkeypatch.setattr(syscall_dispatcher.settings, "ENV", "production")
    monkeypatch.setattr(syscall_dispatcher.settings, "TESTING", False)
    monkeypatch.setattr(syscall_dispatcher.settings, "TEST_MODE", False)
    try:
        result = dispatcher.dispatch(name, {}, _ctx())
    finally:
        syscall_registry.SYSCALL_REGISTRY.pop(name, None)

    assert calls == []
    assert result["status"] == "error"
    assert "Quota backend unavailable" in result["error"]


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


def test_dispatcher_stable_output_schema_mismatch_fails_closed(monkeypatch, dispatcher, caplog):
    name = "sys.v1.test.output_stable"
    syscall_registry.SYSCALL_REGISTRY[name] = syscall_registry.SyscallEntry(
        handler=lambda payload, context: {"present": True},
        capability="test.capability",
        output_schema={"required": ["missing"]},
        stable=True,
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
    assert "Stable syscall output validation failed" in result["error"]
    assert "stable output schema mismatch" in caplog.text


def test_dispatcher_experimental_output_schema_mismatch_is_warning_only(monkeypatch, dispatcher, caplog):
    name = "sys.v1.test.output_warning"
    syscall_registry.SYSCALL_REGISTRY[name] = syscall_registry.SyscallEntry(
        handler=lambda payload, context: {"present": True},
        capability="test.capability",
        output_schema={"required": ["missing"]},
        stable=False,
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
    assert "experimental" in caplog.text


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


def test_agent_ensure_initial_run_owns_and_closes_internal_session(monkeypatch):
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
    monkeypatch.setattr("AINDY.db.database.SessionLocal", lambda: fake_db)

    result = syscall_registry._handle_agent_ensure_initial_run(
        {"user_id": "user-1"},
        _ctx(capabilities=["agent.write"]),
    )

    assert result == {"run_id": "run-created", "created": True}
    assert fake_db.commit_calls == 1
    assert fake_db.flush_calls == 0
    assert fake_db.close_calls == 1


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


def test_agent_read_helpers_reject_cross_tenant_user_id(monkeypatch):
    fake_db = _FakeDb()

    monkeypatch.setattr("AINDY.platform_layer.user_ids.parse_user_id", lambda user_id: user_id)

    with pytest.raises(PermissionError, match="TENANT_VIOLATION"):
        syscall_registry._handle_agent_count_runs(
            {"user_id": "other-user"},
            _ctx(capabilities=["agent.read"], metadata={"_db": fake_db}),
        )


def test_dispatcher_rejects_extension_call_tenant_metadata_mismatch(monkeypatch, dispatcher):
    name = "sys.v1.test.extension_tenant"
    syscall_registry.SYSCALL_REGISTRY[name] = syscall_registry.SyscallEntry(
        handler=lambda payload, context: {"ok": True},
        capability="test.capability",
    )

    class _OkRm:
        def check_quota(self, execution_unit_id):
            return True, None

        def record_usage(self, execution_unit_id, usage):
            return None

    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())
    try:
        result = dispatcher.dispatch(
            name,
            {},
            _ctx(
                metadata={
                    "_extension_call": {
                        "surface": "extension-runtime-api",
                        "operation": "memory.read",
                        "tenant_user_id": "other-user",
                        "extension_name": "demo-plugin",
                        "owner_class": "external-third-party",
                    }
                }
            ),
        )
    finally:
        syscall_registry.SYSCALL_REGISTRY.pop(name, None)

    assert result["status"] == "error"
    assert "TENANT_VIOLATION" in result["error"]


def test_event_emit_adds_extension_scope_and_source(monkeypatch):
    fake_db = _FakeDb()
    event_calls: list[dict] = []

    monkeypatch.setattr(
        "AINDY.core.system_event_service.emit_system_event",
        lambda **kwargs: event_calls.append(kwargs) or "evt-1",
    )

    result = syscall_registry._handle_event_emit(
        {"event_type": "test.event", "payload": {"x": 1}},
        _ctx(
            capabilities=["event.emit"],
            metadata={
                "_db": fake_db,
                "_extension_call": {
                    "surface": "extension-runtime-api",
                    "operation": "event.emit",
                    "tenant_user_id": "user-1",
                    "extension_name": "demo-plugin",
                    "owner_class": "external-third-party",
                },
            },
        ),
    )

    assert result == {"event_id": "evt-1"}
    assert event_calls[0]["source"] == "extension:demo-plugin"
    assert event_calls[0]["payload"]["_runtime_extension_scope"] == {
        "tenant_user_id": "user-1",
        "extension_name": "demo-plugin",
        "owner_class": "external-third-party",
        "operation": "event.emit",
    }


def test_flow_run_injects_extension_scope_into_initial_state(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeRunner:
        def __init__(self, *, flow, db, user_id, workflow_type):
            captured["flow"] = flow
            captured["db"] = db
            captured["user_id"] = user_id
            captured["workflow_type"] = workflow_type

        def start(self, initial_state, *, flow_name):
            captured["initial_state"] = initial_state
            captured["flow_name"] = flow_name
            return {"status": "ok"}

    monkeypatch.setattr("AINDY.runtime.flow_engine.PersistentFlowRunner", _FakeRunner)
    monkeypatch.setattr("AINDY.runtime.flow_engine.FLOW_REGISTRY", {"demo-flow": {"start": "alpha"}})

    fake_db = _FakeDb()
    result = syscall_registry._handle_flow_run(
        {"flow_name": "demo-flow", "initial_state": {"x": 1}},
        _ctx(
            capabilities=["flow.run"],
            metadata={
                "_db": fake_db,
                "_extension_call": {
                    "surface": "extension-runtime-api",
                    "operation": "flow.run",
                    "tenant_user_id": "user-1",
                    "extension_name": "demo-plugin",
                    "owner_class": "external-third-party",
                },
            },
        ),
    )

    assert result == {"flow_result": {"status": "ok"}}
    assert captured["user_id"] == "user-1"
    assert captured["initial_state"]["_runtime_extension_scope"] == {
        "tenant_user_id": "user-1",
        "extension_name": "demo-plugin",
        "owner_class": "external-third-party",
        "operation": "flow.run",
    }


# ---------------------------------------------------------------------------
# Duplicate-registration guard (added 2026-08-13)
#
# `SYSCALL_REGISTRY` is a custom mapping, not a dict: the guard lives in
# `SyscallRegistry.__setitem__`, not in `register_syscall`. It had no test, which
# is how an audit came to conclude — from reading `register_syscall`'s body alone
# — that no guard existed. These lock the real behaviour down.
# ---------------------------------------------------------------------------

def test_reregistration_with_different_handler_raises():
    """A second handler for a live name is fatal, not a silent overwrite.

    This is what keeps a plugin from replacing a runtime syscall *and its
    capability* — which would change what the dispatcher enforces for every
    later caller.
    """
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY, register_syscall

    name = "sys.v1.test.collision_probe"

    def first(payload, context):
        return {"who": "first"}

    def second(payload, context):
        return {"who": "second"}

    try:
        register_syscall(name, first, "test.cap", "first registration")

        with pytest.raises(ValueError, match="already registered with a different handler"):
            register_syscall(name, second, "other.cap", "second registration")

        # The original survives intact — a failed re-registration must not
        # partially apply.
        entry = SYSCALL_REGISTRY[name]
        assert entry.handler is first
        assert entry.capability == "test.cap"
    finally:
        SYSCALL_REGISTRY.pop(name, None)


def test_reregistration_with_same_handler_is_allowed():
    """Idempotent re-registration must stay a no-op — plugin load can repeat."""
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY, register_syscall

    name = "sys.v1.test.idempotent_probe"

    def only(payload, context):
        return {}

    try:
        register_syscall(name, only, "test.cap", "first")
        register_syscall(name, only, "test.cap", "again")  # must not raise
        assert SYSCALL_REGISTRY[name].handler is only
    finally:
        SYSCALL_REGISTRY.pop(name, None)
