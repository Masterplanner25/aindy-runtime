"""UNDO-COMPENSATORS-1 — undo must find and reverse effects the REAL gate wrote.

`effect_ledger` never set `EffectRecord.execution_id`, and `undo_run_effects` selects on it, so undo
returned an empty summary for every run while its suite stayed green: those tests seed the column by
hand. **Nothing in this file writes `execution_id`.** Every effect here comes through the dispatcher
or the tool seam, because that path is the one that was broken.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import tests.fixtures.db  # noqa: F401 — registers JSONB/UUID/Vector SQLite compilers
import AINDY.db.model_registry  # noqa: F401 — populate metadata
from AINDY.agents.tool_registry import TOOL_REGISTRY, execute_tool
from AINDY.core.effect_compensation import undo_run_effects
from AINDY.db.database import Base
from AINDY.db.models import AgentRun, EffectRecord, EffectReversal, ExecutionUnit
from AINDY.db.models.memory_node_history import MemoryNodeHistory
from AINDY.db.models.memory_trace_node import MemoryTraceNode
from AINDY.kernel import effect_ledger
from AINDY.kernel.effect_ledger import effect_run_scope, unlinked_effect_scope
from AINDY.kernel.syscall_dispatcher import SyscallContext, get_dispatcher
from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY, register_syscall
from AINDY.memory.memory_persistence import MemoryLinkModel, MemoryNodeModel

pytestmark = pytest.mark.runtime_only

_PROBE = "sys.v1.test.undo_probe"
_WRITE = "sys.v1.memory.write"
_TABLES = (
    "execution_units", "effect_records", "effect_reversals", "agent_runs", "system_events",
    "memory_nodes", "memory_links", "memory_node_history", "memory_traces", "memory_trace_nodes",
)


@pytest.fixture
def factory():
    """A private engine patched in as `SessionLocal`, because the gate opens its own session."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_off(dbapi_connection, _rec):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.close()

    for table in _TABLES:
        Base.metadata.tables[table].create(bind=engine)
    make = sessionmaker(bind=engine)
    with patch("AINDY.db.database.SessionLocal", make), patch(
        "AINDY.db.dao.memory_node_dao.MemoryNodeDAO._enqueue_embedding", lambda *a, **k: None
    ):
        yield make
    engine.dispose()


@pytest.fixture
def db(factory):
    session = factory()
    yield session
    session.close()


@pytest.fixture
def run(db):
    """An agent run's execution unit, whose id differs from the run id, as in production."""
    run_id, user_id = uuid.uuid4(), uuid.uuid4()
    unit = ExecutionUnit(
        type="agent", status="completed", user_id=user_id,
        source_type="agent_run", source_id=str(run_id), priority="normal",
    )
    db.add(unit)
    db.commit()
    return {"run_id": str(run_id), "user_id": str(user_id), "unit_id": unit.id}


@pytest.fixture
def probe():
    calls = []

    def _compensate(effect, context):
        calls.append((effect, context))
        return {"undone": True}

    register_syscall(
        _PROBE, handler=lambda p, c: {"id": "probe"}, capability="test.undo",
        execution_guarantee="EXACTLY_ONCE", compensate=_compensate,
    )
    yield calls
    del SYSCALL_REGISTRY[_PROBE]


def _dispatch(name, payload, *, run, unit, capability):
    return get_dispatcher().dispatch(
        name, payload,
        SyscallContext(
            execution_unit_id=str(unit), user_id=run["user_id"],
            capabilities=[capability], trace_id=run["run_id"],
        ),
    )


def _write_node(run, content="remember this", **extra):
    """A real memory write, attributed the way `execute_tool` attributes one."""
    with effect_run_scope(run["run_id"]):
        envelope = _dispatch(
            _WRITE, {"content": content, **extra},
            run=run, unit=uuid.uuid4(), capability="memory.write",
        )
    assert envelope["status"] == "success", envelope["error"]
    return envelope["data"]["node"]["id"]


def _node_exists(db, node_id) -> bool:
    db.expire_all()
    return db.query(MemoryNodeModel).filter(MemoryNodeModel.id == uuid.UUID(node_id)).first() is not None


# ── the link ─────────────────────────────────────────────────────────────────────────────


def test_undo_finds_an_effect_scoped_to_the_units_own_id(db, run, probe):
    assert _dispatch(_PROBE, {"a": 1}, run=run, unit=run["unit_id"], capability="test.undo")["status"] == "success"

    summary = undo_run_effects(run["run_id"], db=db)

    assert summary["reversed"] == [_PROBE]
    assert len(probe) == 1


def test_undo_finds_an_effect_scoped_to_the_run_id(db, run, probe):
    """`make_syscall_ctx_from_tool` passes a run id as the unit; undo wants the unit's own id."""
    _dispatch(_PROBE, {"a": 1}, run=run, unit=run["run_id"], capability="test.undo")

    assert undo_run_effects(run["run_id"], db=db)["reversed"] == [_PROBE]


def test_undo_finds_an_effect_under_a_minted_unit_through_the_ambient_run(db, run, probe):
    """The default agent write: a fresh unit id that cannot name the run, so only ambient can."""
    with effect_run_scope(run["run_id"]):
        _dispatch(_PROBE, {"a": 1}, run=run, unit=uuid.uuid4(), capability="test.undo")

    assert undo_run_effects(run["run_id"], db=db)["reversed"] == [_PROBE]


def test_an_effect_of_another_run_is_not_undone(db, run, probe):
    with effect_run_scope(str(uuid.uuid4())):
        _dispatch(_PROBE, {"a": 1}, run=run, unit=uuid.uuid4(), capability="test.undo")

    summary = undo_run_effects(run["run_id"], db=db)

    assert summary["reversed"] == [] and probe == []
    # Liveness: the effect was recorded, so the empty summary is about the link and not the gate.
    assert db.query(EffectRecord).filter(EffectRecord.action_type == _PROBE).count() == 1


def test_a_scope_naming_no_unit_leaves_the_row_unlinked(db, run, probe):
    _dispatch(_PROBE, {"a": 1}, run=run, unit=uuid.uuid4(), capability="test.undo")

    rows = db.query(EffectRecord).filter(EffectRecord.action_type == _PROBE).all()
    assert [r.execution_id for r in rows] == [None]


def test_a_replay_still_replays_and_does_not_degrade(db, run, probe):
    """The existing-row path takes the scope too; a mistake there is swallowed as a degrade."""
    runs = []
    SYSCALL_REGISTRY[_PROBE].handler = lambda p, c: runs.append(p) or {"id": "probe"}

    with patch.object(effect_ledger, "_count_gate") as counted:
        first = _dispatch(_PROBE, {"a": 1}, run=run, unit=run["unit_id"], capability="test.undo")
        second = _dispatch(_PROBE, {"a": 1}, run=run, unit=run["unit_id"], capability="test.undo")

    assert first["status"] == second["status"] == "success"
    assert len(runs) == 1, "the replay re-ran the handler"
    assert [call.args[0] for call in counted.call_args_list] == ["reserved", "replayed"]


def test_a_reclaimed_row_written_before_the_link_gains_it(db, run, probe):
    """A failed row from before this fix re-runs under the link, so undo can find it."""
    action_id = "reclaim-me"
    db.add(EffectRecord(action_id=action_id, action_type=_PROBE, input_hash="h", status="failed"))
    db.commit()

    already, _ = effect_ledger.resolve_effect_record(
        db, action_id, _PROBE, {}, execution_ref=run["run_id"]
    )

    assert already is False
    db.expire_all()
    assert db.query(EffectRecord).one().execution_id == run["unit_id"]


def test_a_failed_lookup_never_stops_the_effect(db, run, probe):
    """Linking is attribution. A broken lookup must not cost the caller its effect."""
    with patch("sqlalchemy.or_", side_effect=RuntimeError("lookup broke")):
        envelope = _dispatch(_PROBE, {"a": 1}, run=run, unit=run["unit_id"], capability="test.undo")

    assert envelope["status"] == "success"
    assert db.query(EffectRecord).filter(EffectRecord.action_type == _PROBE).one().execution_id is None


def test_a_deferred_nodus_write_is_surfaced_through_the_attribution_span(db, run):
    """Parent-side deferred writes have no `execute_tool` around them, only `execute_run`'s span.

    Their rows hold no node id and are named `memory.write`, which is not a syscall, so undo
    surfaces them as irreversible rather than guessing at what to delete.
    """
    from AINDY.platform_layer.token_meter import llm_attribution_scope

    with llm_attribution_scope(tenant_id=run["user_id"], run_id=run["run_id"]):
        effect_ledger.resolve_effect_record(db, "deferred-write", "memory.write", {"seq": 0})
    effect_ledger.complete_effect_record(db, "deferred-write", "success", None)

    summary = undo_run_effects(run["run_id"], db=db)

    assert summary["irreversible"] == ["memory.write"] and summary["reversed"] == []


def test_the_ambient_run_is_cleared_after_its_scope(run, db):
    with effect_run_scope(run["run_id"]):
        assert effect_ledger._linked_execution_id(db, None) == run["unit_id"]
    assert effect_ledger._linked_execution_id(db, None) is None


def test_an_unlinked_scope_wins_over_the_ambient_run(run, db):
    with effect_run_scope(run["run_id"]), unlinked_effect_scope():
        assert effect_ledger._linked_execution_id(db, run["run_id"]) is None


def test_undo_does_not_list_its_own_ledger_row(db, run, probe):
    """Undo declares EXACTLY_ONCE, so its own row is linked to the run it just reversed."""
    db.add(AgentRun(id=uuid.UUID(run["run_id"]), user_id=uuid.UUID(run["user_id"]), goal="g", status="completed"))
    db.commit()
    _dispatch(_PROBE, {"a": 1}, run=run, unit=run["unit_id"], capability="test.undo")

    undo = _dispatch(
        "sys.v1.agent.undo", {"run_id": run["run_id"]},
        run=run, unit=run["unit_id"], capability="agent.undo",
    )
    assert undo["status"] == "success", undo["error"]
    assert undo["data"]["reversed"] == [_PROBE]
    # Liveness: the row this test is about does exist and is linked to the run's unit.
    db.expire_all()
    own = db.query(EffectRecord).filter(EffectRecord.action_type == "sys.v1.agent.undo").one()
    assert own.execution_id == run["unit_id"]

    again = undo_run_effects(run["run_id"], db=db)

    assert again["irreversible"] == [] and again["already_reversed"] == [_PROBE]


# ── the compensator ──────────────────────────────────────────────────────────────────────


def test_undo_deletes_the_node_a_run_wrote(db, run):
    node_id = _write_node(run)
    assert _node_exists(db, node_id)

    summary = undo_run_effects(run["run_id"], db=db)

    assert summary["reversed"] == [_WRITE], summary
    assert not _node_exists(db, node_id)
    audit = db.query(EffectReversal).one()
    assert audit.status == "reversed" and audit.receipt == {"node_id": node_id, "deleted": True}


def test_a_second_undo_reports_already_reversed(db, run):
    _write_node(run)
    undo_run_effects(run["run_id"], db=db)

    second = undo_run_effects(run["run_id"], db=db)

    assert second["reversed"] == [] and second["already_reversed"] == [_WRITE]


def test_a_node_already_gone_counts_as_reversed(db, run):
    node_id = _write_node(run)
    db.query(MemoryNodeModel).filter(MemoryNodeModel.id == uuid.UUID(node_id)).delete()
    db.commit()

    summary = undo_run_effects(run["run_id"], db=db)

    assert summary["reversed"] == [_WRITE]
    assert db.query(EffectReversal).one().receipt == {"node_id": node_id, "deleted": False}


def test_undo_with_no_context_uses_the_effects_owner(db, run):
    """The Verifier passes `context=None`; the owner has to come from the record."""
    node_id = _write_node(run)

    assert undo_run_effects(run["run_id"], db=db, context=None)["reversed"] == [_WRITE]
    assert not _node_exists(db, node_id)


def test_another_tenants_node_is_never_deleted(db, run):
    node_id = _write_node(run)
    stranger = SyscallContext(
        execution_unit_id="x", user_id=str(uuid.uuid4()), capabilities=[], trace_id="x",
    )

    summary = undo_run_effects(run["run_id"], db=db, context=stranger)

    # It reads as already gone, by design: a foreign id must not reveal that it exists.
    assert summary["reversed"] == [_WRITE]
    assert _node_exists(db, node_id)


def _edit_content(db, node_id):
    db.query(MemoryNodeModel).filter(MemoryNodeModel.id == uuid.UUID(node_id)).update({"content": "edited"})


def _edit_tags(db, node_id):
    db.query(MemoryNodeModel).filter(MemoryNodeModel.id == uuid.UUID(node_id)).update({"tags": ["edited"]})


def _add_history(db, node_id):
    db.add(MemoryNodeHistory(node_id=uuid.UUID(node_id), change_type="content"))


def _add_outgoing_link(db, node_id):
    db.add(MemoryLinkModel(source_node_id=uuid.UUID(node_id), target_node_id=uuid.uuid4(), link_type="related"))


def _add_incoming_link(db, node_id):
    db.add(MemoryLinkModel(source_node_id=uuid.uuid4(), target_node_id=uuid.UUID(node_id), link_type="related"))


def _add_trace_membership(db, node_id):
    db.add(MemoryTraceNode(trace_id=uuid.uuid4(), node_id=uuid.UUID(node_id), position=0))


@pytest.mark.parametrize(
    "build_on_node, reason",
    [
        (_edit_content, "content changed"),
        (_edit_tags, "tags changed"),
        (_add_history, "edited"),
        (_add_outgoing_link, "links"),
        (_add_incoming_link, "links"),
        (_add_trace_membership, "memory trace"),
    ],
)
def test_a_node_something_was_built_on_is_refused_and_kept(db, run, build_on_node, reason):
    node_id = _write_node(run)
    build_on_node(db, node_id)
    db.commit()

    summary = undo_run_effects(run["run_id"], db=db)

    assert summary["reversed"] == []
    assert [f["action_type"] for f in summary["failed"]] == [_WRITE]
    assert reason in summary["failed"][0]["error"]
    assert _node_exists(db, node_id)


def test_a_refused_effect_can_be_retried(db, run):
    """`failed` stays retryable: once the link is gone the next undo succeeds (IDEM-12)."""
    node_id = _write_node(run)
    _add_outgoing_link(db, node_id)
    db.commit()
    assert undo_run_effects(run["run_id"], db=db)["failed"]

    db.query(MemoryLinkModel).delete()
    db.commit()

    assert undo_run_effects(run["run_id"], db=db)["reversed"] == [_WRITE]
    assert not _node_exists(db, node_id)


def test_a_refusal_does_not_stop_the_other_effects(db, run):
    """The savepoint: one refused effect must not lose the others' work or their audit rows."""
    kept = _write_node(run, content="linked, so refused")
    gone = _write_node(run, content="free to delete")
    _add_outgoing_link(db, kept)
    db.commit()

    summary = undo_run_effects(run["run_id"], db=db)

    assert summary["reversed"] == [_WRITE] and len(summary["failed"]) == 1
    assert _node_exists(db, kept) and not _node_exists(db, gone)
    assert sorted(r.status for r in db.query(EffectReversal).all()) == ["failed", "reversed"]


def test_a_compensator_that_fails_part_way_leaves_nothing_behind(db, run):
    """A partial write by a raising compensator is unwound by undo's savepoint."""
    def _half_done(effect, context):
        context.metadata["_db"].add(
            MemoryNodeModel(content="half done", node_type="insight", memory_type="insight")
        )
        context.metadata["_db"].flush()
        raise RuntimeError("failed part-way")

    register_syscall(
        _PROBE, handler=lambda p, c: {"id": "probe"}, capability="test.undo",
        execution_guarantee="EXACTLY_ONCE", compensate=_half_done,
    )
    try:
        _dispatch(_PROBE, {"a": 1}, run=run, unit=run["unit_id"], capability="test.undo")
        summary = undo_run_effects(run["run_id"], db=db)
    finally:
        del SYSCALL_REGISTRY[_PROBE]

    assert [f["action_type"] for f in summary["failed"]] == [_PROBE]
    assert db.query(MemoryNodeModel).filter(MemoryNodeModel.content == "half done").count() == 0


# ── report only ──────────────────────────────────────────────────────────────────────────


def test_report_only_lists_what_undo_would_do_and_changes_nothing(db, run):
    node_id = _write_node(run)

    summary = undo_run_effects(run["run_id"], db=db, report_only=True)

    assert summary["reversible"] == [_WRITE] and summary["reversed"] == []
    assert _node_exists(db, node_id)
    assert db.query(EffectReversal).count() == 0


def test_report_only_surfaces_an_effect_with_no_compensator(db, run):
    register_syscall(
        _PROBE, handler=lambda p, c: {"id": "probe"}, capability="test.undo",
        execution_guarantee="EXACTLY_ONCE",
    )
    try:
        _dispatch(_PROBE, {"a": 1}, run=run, unit=run["unit_id"], capability="test.undo")
        summary = undo_run_effects(run["run_id"], db=db, report_only=True)
    finally:
        del SYSCALL_REGISTRY[_PROBE]

    assert summary["irreversible"] == [_PROBE] and summary["reversible"] == []


# ── the tool seam ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def tool_seam():
    """The real `execute_tool` and the real `memory.write` tool, past the token check only."""
    from AINDY.platform_layer.runtime_agent_defaults import register

    register()
    with patch(
        "AINDY.agents.capability_service.check_tool_capability",
        return_value={"ok": True, "allowed_capabilities": [], "granted_tools": ["memory.write"]},
    ):
        yield


def _call_write_tool(db, run, content="from the tool"):
    outcome = execute_tool(
        "memory.write", {"content": content}, run["user_id"], db,
        run_id=run["run_id"], execution_token={"token": "t"},
    )
    assert outcome["success"], outcome
    return outcome["result"]["node_id"]


def test_the_memory_write_tool_declares_the_syscall_that_records_its_effect(tool_seam):
    declared = TOOL_REGISTRY["memory.write"]["effect_syscall"]
    assert declared == _WRITE and SYSCALL_REGISTRY[declared].reversible


def test_a_default_agent_write_is_undone_end_to_end(db, run, tool_seam):
    """The tool seam's own gate is off by default, so only the inner syscall's row exists."""
    node_id = _call_write_tool(db, run)

    summary = undo_run_effects(run["run_id"], db=db)

    assert summary["reversed"] == [_WRITE], summary
    assert not _node_exists(db, node_id)


def test_with_the_tool_gate_on_undo_sees_one_effect_once(db, run, tool_seam, monkeypatch):
    """The tool's row is unlinked, or undo would call this one write irreversible as well."""
    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY", "1")
    monkeypatch.setitem(TOOL_REGISTRY["memory.write"], "execution_guarantee", "EXACTLY_ONCE")
    node_id = _call_write_tool(db, run)
    # Liveness: the tool seam's gate really did write its own row.
    assert db.query(EffectRecord).filter(EffectRecord.action_type == "memory.write").count() == 1

    summary = undo_run_effects(run["run_id"], db=db)

    assert summary["reversed"] == [_WRITE] and summary["irreversible"] == [], summary
    assert not _node_exists(db, node_id)
