"""DEC-124: a runtime write handler that fails on a caller's session undoes only its own work.

`_finish_handler_write` rolled back the caller's whole session when a handler raised, against
`_acquire_handler_db`'s own contract. That discarded whatever the caller had not committed yet,
and under a test fixture sharing one outer transaction it erased the caller's `FlowRun` (FR-64).
A write handler now puts its work in a savepoint and rolls back to it.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session, sessionmaker

from tests.fixtures.db import build_private_engine

pytestmark = pytest.mark.runtime_only

USER = uuid.UUID("00000000-0000-0000-0000-0000000124a1")


@pytest.fixture
def engine(tmp_path):
    engine = build_private_engine(tmp_path / "savepoint.db")
    try:
        yield engine
    finally:
        engine.dispose()


def _ctx(db):
    from AINDY.kernel.syscall_dispatcher import SyscallContext

    return SyscallContext(execution_unit_id=str(uuid.uuid4()), user_id=str(USER), capabilities=["memory.delete"],
                          trace_id="t-124", metadata={"_db": db})


def _event(kind):
    from AINDY.db.models.system_event import SystemEvent

    return SystemEvent(id=uuid.uuid4(), type=kind, user_id=USER, trace_id="t-124", payload={})


def _types(engine) -> set[str]:
    from AINDY.db.models.system_event import SystemEvent

    with Session(engine) as s:
        return {row[0] for row in s.query(SystemEvent.type).all()}


@pytest.fixture
def failing_delete(monkeypatch):
    """`memory.delete` whose DAO writes something, flushes, and then fails."""
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    def _delete(self, node_id, user_id=None):
        self.db.add(_event("handler.partial"))
        self.db.flush()
        raise RuntimeError("the delete failed half way")

    monkeypatch.setattr(MemoryNodeDAO, "delete_by_id", _delete)


def _fail_the_handler(db):
    from AINDY.kernel.syscall_registry import _handle_memory_delete

    with pytest.raises(RuntimeError, match="half way"):
        _handle_memory_delete({"node_id": str(uuid.uuid4())}, _ctx(db))


def test_a_failing_handler_keeps_the_callers_work_and_drops_its_own(engine, failing_delete):
    db = sessionmaker(bind=engine)()
    try:
        db.add(_event("caller.pending"))
        db.flush()
        _fail_the_handler(db)
        db.commit()  # the caller decides its own transaction
    finally:
        db.close()

    types = _types(engine)
    assert "caller.pending" in types, "the handler's failure took the caller's uncommitted work with it"
    assert "handler.partial" not in types, "the failed handler's own write was kept"


def test_control_without_the_savepoint_the_caller_loses_its_work(engine, failing_delete, monkeypatch):
    """What happened before DEC-124: the whole caller session was rolled back."""
    from AINDY.kernel import syscall_registry

    monkeypatch.setattr(syscall_registry, "_begin_handler_write", lambda db, owns_session: None)
    db = sessionmaker(bind=engine)()
    try:
        db.add(_event("caller.pending"))
        db.flush()
        _fail_the_handler(db)
        db.commit()
    finally:
        db.close()

    assert "caller.pending" not in _types(engine)


def test_a_succeeding_handler_still_writes(engine):
    from AINDY.kernel.syscall_registry import _handle_event_emit

    db = sessionmaker(bind=engine)()
    try:
        db.add(_event("caller.pending"))
        db.flush()
        result = _handle_event_emit({"event_type": "handler.ok", "payload": {}}, _ctx(db))
        db.commit()
    finally:
        db.close()

    assert result.get("event_id")
    assert {"caller.pending", "handler.ok"} <= _types(engine)


def test_fr64_the_flow_run_survives_a_failing_handler_in_a_shared_transaction(engine, failing_delete):
    """FR-64's report: the app's and the test's sessions share one outer transaction, so the
    runner's commit is not a commit, and a handler's rollback used to erase the run."""
    from AINDY.kernel.syscall_dispatcher import get_dispatcher
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    node = f"deleting_{uuid.uuid4().hex[:6]}"

    @reg.register_node(node)
    def _n(state, context):  # noqa: ANN001
        envelope = get_dispatcher().dispatch("sys.v1.memory.delete", {"node_id": str(uuid.uuid4())}, _ctx(context["db"]))
        return {"status": "FAILURE", "error": envelope.get("error")} if envelope["status"] != "success" else {
            "status": "SUCCESS", "output_patch": {}}

    flow = {"start": node, "end": [node], "edges": {}}
    name = f"deleting_flow_{uuid.uuid4().hex[:6]}"
    reg.register_flow(name, flow)
    conn = engine.connect()
    outer = conn.begin()
    db = Session(bind=conn)
    try:
        response = PersistentFlowRunner(flow=flow, db=db, user_id=str(USER), workflow_type=None).start({}, flow_name=name)
    finally:
        db.close()
        outer.rollback()
        conn.close()
        reg.FLOW_REGISTRY.pop(name, None)
        reg.NODE_REGISTRY.pop(node, None)

    error = (response.get("result") or {}).get("error") or ""
    assert response["status"] == "FAILED", response
    assert "disappeared" not in error, error
    assert "half way" in error, "control: the handler's failure is what ended the run"
