"""FR-61 (DEC-123): outbound-call events name the user whose call they were.

Only the agent planner passed a user to ``perform_external_call``. The embedding job, the hub of
the app's largest traces, passed none, so FR-60's per-user trace graph dropped those events and
every edge through them (584 nodes / 583 edges unscoped, 291 edges scoped). New events now take
the call site's user, else an attribution, else the request's caller; old ones are filled by a
one-time command, only in a trace that belongs to one user.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

from tests.fixtures.db import build_private_engine

pytestmark = pytest.mark.runtime_only

ALICE = uuid.UUID("00000000-0000-0000-0000-0000000061a1")
BOB = uuid.UUID("00000000-0000-0000-0000-0000000061b2")
CALL = "external.call.completed"


@pytest.fixture
def db(tmp_path):
    engine = build_private_engine(tmp_path / "fr61.db")
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _call_users(db, trace=None) -> list:
    from AINDY.db.models.system_event import SystemEvent

    query = db.query(SystemEvent.user_id).filter(SystemEvent.type.like("external.call.%"))
    if trace is not None:
        query = query.filter(SystemEvent.trace_id == trace)
    return [row[0] for row in query.all()]


def _call(db, **kwargs):
    from AINDY.platform_layer.external_call_service import perform_external_call

    return perform_external_call(service_name="openai", operation=lambda: "ok", db=db, **kwargs)


# ── new events ─────────────────────────────────────────────────────────────────────────────


def test_control_a_call_no_user_made_records_no_user(db):
    _call(db)
    assert _call_users(db) == [None, None]


def test_an_attributed_call_records_that_user(db):
    from AINDY.platform_layer.external_call_service import external_calls_attributed_to

    with external_calls_attributed_to(ALICE):
        _call(db)
    _call(db)  # the attribution ends with the block
    assert sorted(_call_users(db), key=str) == sorted([ALICE, ALICE, None, None], key=str)


def test_a_call_inside_a_request_records_its_caller(db):
    from AINDY.platform_layer.trace_context import reset_current_execution_context, set_current_execution_context

    token = set_current_execution_context(SimpleNamespace(user_id=str(ALICE)))
    try:
        _call(db)
    finally:
        reset_current_execution_context(token)
    assert _call_users(db) == [ALICE, ALICE]


def test_the_call_sites_own_user_wins(db):
    from AINDY.platform_layer.external_call_service import external_calls_attributed_to

    with external_calls_attributed_to(BOB):
        _call(db, user_id=ALICE)
    assert _call_users(db) == [ALICE, ALICE]


def test_the_embedding_job_attributes_its_model_call_to_the_nodes_owner(db, monkeypatch):
    from AINDY.memory import embedding_jobs
    from AINDY.memory.embedding_providers import resolve_embedding_column_dimensions
    from AINDY.memory.memory_persistence import MemoryNodeModel

    node = MemoryNodeModel(content="alice's note", tags=[], node_type="insight", user_id=ALICE, extra={},
                           embedding_pending=True)
    db.add(node)
    db.commit()

    def _embed(text):  # the provider's own call, without the network
        _call(db)
        return [0.1] * resolve_embedding_column_dimensions()

    monkeypatch.setattr(embedding_jobs, "generate_embedding", _embed)
    result = embedding_jobs.process_embedding_job({"memory_id": str(node.id)}, db)

    assert result.get("embedding_pending") is False, result
    assert _call_users(db) == [ALICE, ALICE]


# ── old events: the one-time backfill ─────────────────────────────────────────────────────


def _event(db, kind, trace, user=None):
    from AINDY.db.models.system_event import SystemEvent

    event = SystemEvent(id=uuid.uuid4(), type=kind, user_id=user, trace_id=trace, payload={})
    db.add(event)
    return event


@pytest.fixture
def old_traces(db):
    """``alone``: Alice's events plus userless calls. ``shared``: Alice and Bob plus a userless call.
    ``nobody``: a userless call only. Also a userless non-call event in ``alone``, which is not ours."""
    for kind in ("flow.started", "flow.completed"):
        _event(db, kind, "alone", ALICE)
    for kind in ("external.call.started", CALL, "error.external_call"):
        _event(db, kind, "alone")
    _event(db, "execution.started", "alone")
    _event(db, "a.step", "shared", ALICE)
    _event(db, "b.step", "shared", BOB)
    _event(db, CALL, "shared")
    _event(db, CALL, "nobody")
    db.commit()


def _users(db, trace, kind=None):
    from AINDY.db.models.system_event import SystemEvent

    query = db.query(SystemEvent.type, SystemEvent.user_id).filter(SystemEvent.trace_id == trace)
    if kind:
        query = query.filter(SystemEvent.type == kind)
    return dict(query.all())


def test_a_dry_run_counts_and_changes_nothing(db, old_traces):
    from AINDY.core.external_call_user_backfill import backfill_external_call_users

    report = backfill_external_call_users(db, dry_run=True)
    assert report["would_fill"] == 3 and report["filled"] == 0
    assert report["skipped_shared_traces"] == 1 and report["skipped_no_user_traces"] == 1
    assert _users(db, "alone")[CALL] is None


@pytest.mark.parametrize("batch_size", [1, 500])
def test_only_a_one_user_trace_is_filled(db, old_traces, batch_size):
    from AINDY.core.external_call_user_backfill import backfill_external_call_users

    report = backfill_external_call_users(db, dry_run=False, batch_size=batch_size)
    db.expire_all()

    alone = _users(db, "alone")
    assert alone["external.call.started"] == alone[CALL] == alone["error.external_call"] == ALICE
    assert alone["execution.started"] is None, "only outbound-call events are filled"
    assert _users(db, "shared")[CALL] is None, "a trace with two users is left alone"
    assert _users(db, "nobody")[CALL] is None
    assert report["filled"] == 3 and report["traces_examined"] == 3

    again = backfill_external_call_users(db, dry_run=False, batch_size=batch_size)
    assert again["filled"] == 0 and again["would_fill"] == 0


def test_after_the_backfill_the_scoped_graph_keeps_the_call_and_its_edges(db):
    """The app's symptom, end to end: the model call was the hub of the owner's trace."""
    from AINDY.core.external_call_user_backfill import backfill_external_call_users
    from AINDY.db.models.event_edge import EventEdge
    from AINDY.platform_layer.event_trace_service import build_trace_graph

    start = _event(db, "embedding.started", "hub", ALICE)
    call = _event(db, CALL, "hub")
    done = _event(db, "embedding.completed", "hub", ALICE)
    db.flush()
    db.add_all([
        EventEdge(source_event_id=start.id, target_event_id=call.id, relationship_type="caused"),
        EventEdge(source_event_id=call.id, target_event_id=done.id, relationship_type="caused"),
    ])
    db.commit()

    before = build_trace_graph(db, "hub", user_id=str(ALICE))
    assert len(before["edges"]) == 0, "control: the userless hub cuts the owner's chain"

    backfill_external_call_users(db, dry_run=False)
    db.expire_all()
    after = build_trace_graph(db, "hub", user_id=str(ALICE))
    assert {n["id"] for n in after["nodes"]} >= {str(start.id), str(call.id), str(done.id)}
    assert len(after["edges"]) == 2


def test_the_key_source_counter_has_a_public_name_and_keeps_the_old_one():
    """The app's FR-59 phase 3 imports `_count_key_source`; the public name is what it should use."""
    from AINDY.platform_layer import llm_client
    from AINDY.platform_layer.metrics import llm_key_source_total

    before = llm_key_source_total.labels(provider="perplexity", key_source="user")._value.get()
    llm_client.count_key_source("perplexity", "user")
    assert llm_key_source_total.labels(provider="perplexity", key_source="user")._value.get() == before + 1
    assert llm_client._count_key_source is llm_client.count_key_source
