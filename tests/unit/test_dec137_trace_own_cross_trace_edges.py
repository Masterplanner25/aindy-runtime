"""DEC-137 — a trace graph scoped to one user keeps that user's edges that leave the trace.

The app's 2.34.0 report read trace `d534a3fd…` scoped to its owner: 290 `async_child` edges were
dropped, each pointing to an `execution.started` event of the SAME owner in another trace (queued
work runs under its own trace). FR-60's rule kept only edges whose both ends are nodes, so the
scoped graph lost every one, while the unscoped graph keeps them dangling. Hiding the caller's own
events from the caller protects nobody.

Pinned:

* the caller's edge out to its own event in another trace is kept, dangling (no node added);
* so is an edge INTO the trace from the caller's own event elsewhere;
* an edge out to another user's event, or a userless one, is still dropped (FR-60 stands);
* the source of a kept outbound edge is no longer terminal, matching the unscoped graph;
* depth counts only edges between nodes, and a child trace's root is still its first event.

Mutation-checked: drop the own-elsewhere exception → the first two tests fail; drop the owner
filter in its query → Bob's and the userless edge come back; drop the node check in
`_depth_within` → depth becomes 3.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from tests.fixtures.db import build_private_engine

ALICE = uuid.UUID("00000000-0000-0000-0000-0000000137a1")
BOB = uuid.UUID("00000000-0000-0000-0000-0000000137b2")
TRACE = "trace-dec137"
CHILD_TRACE = "trace-dec137-child"


@pytest.fixture
def trace_db(tmp_path):
    from AINDY.db.models.event_edge import EventEdge
    from AINDY.db.models.system_event import SystemEvent

    engine = build_private_engine(tmp_path / "trace.db")
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    t0 = datetime(2026, 11, 19, tzinfo=timezone.utc)
    ids = {name: uuid.uuid4() for name in ("parent", "start", "note", "end", "child", "child_step", "bob_child", "system_child", "upstream")}
    rows = [
        # name, user, trace, type, seconds after t0
        ("upstream", ALICE, "trace-dec137-upstream", "execution.completed", 0),
        ("parent", ALICE, TRACE, "execution.started", 1),
        ("start", ALICE, TRACE, "job.queued", 2),
        ("note", ALICE, TRACE, "memory.written", 2),
        ("end", ALICE, TRACE, "execution.completed", 3),
        ("child", ALICE, CHILD_TRACE, "execution.started", 4),
        ("child_step", ALICE, CHILD_TRACE, "execution.completed", 5),
        ("bob_child", BOB, "trace-dec137-bob", "execution.started", 6),
        ("system_child", None, "trace-dec137-system", "execution.started", 7),
    ]
    for name, user, trace, kind, seconds in rows:
        session.add(
            SystemEvent(
                id=ids[name], type=kind, user_id=user, trace_id=trace, payload={},
                timestamp=t0 + timedelta(seconds=seconds),
            )
        )
    session.flush()
    session.add_all(
        [
            EventEdge(source_event_id=ids["upstream"], target_event_id=ids["parent"], relationship_type="async_child"),
            EventEdge(source_event_id=ids["parent"], target_event_id=ids["start"], relationship_type="related_to"),
            EventEdge(source_event_id=ids["start"], target_event_id=ids["end"], relationship_type="related_to"),
            EventEdge(source_event_id=ids["start"], target_event_id=ids["note"], relationship_type="related_to"),
            EventEdge(source_event_id=ids["end"], target_event_id=ids["child"], relationship_type="async_child"),
            EventEdge(source_event_id=ids["child"], target_event_id=ids["child_step"], relationship_type="related_to"),
            EventEdge(source_event_id=ids["end"], target_event_id=ids["bob_child"], relationship_type="async_child"),
            EventEdge(source_event_id=ids["end"], target_event_id=ids["system_child"], relationship_type="async_child"),
        ]
    )
    session.commit()
    try:
        yield session, {name: str(value) for name, value in ids.items()}
    finally:
        session.close()
        engine.dispose()


def _pairs(graph) -> set[tuple[str, str]]:
    return {(edge["source"], edge["target"]) for edge in graph["edges"]}


def test_control_unscoped_keeps_every_edge_dangling(trace_db):
    from AINDY.platform_layer.event_trace_service import build_trace_graph

    db, ids = trace_db
    pairs = _pairs(build_trace_graph(db, TRACE))
    assert (ids["end"], ids["child"]) in pairs
    assert (ids["end"], ids["bob_child"]) in pairs


def test_scoped_keeps_the_callers_edge_out_to_its_own_event_in_another_trace(trace_db):
    from AINDY.platform_layer.event_trace_service import build_trace_graph

    db, ids = trace_db
    graph = build_trace_graph(db, TRACE, user_id=str(ALICE))

    assert (ids["end"], ids["child"]) in _pairs(graph), "the caller's async child must stay visible"
    node_ids = {node["id"] for node in graph["nodes"]}
    assert node_ids == {ids["parent"], ids["start"], ids["note"], ids["end"]}, "the far event is not added as a node"


def test_scoped_keeps_the_callers_edge_into_the_trace(trace_db):
    from AINDY.platform_layer.event_trace_service import build_trace_graph

    db, ids = trace_db
    assert (ids["upstream"], ids["parent"]) in _pairs(build_trace_graph(db, TRACE, user_id=str(ALICE)))


def test_scoped_still_drops_edges_to_another_users_or_a_userless_event(trace_db):
    from AINDY.platform_layer.event_trace_service import build_trace_graph

    db, ids = trace_db
    graph = build_trace_graph(db, TRACE, user_id=str(ALICE))
    pairs = _pairs(graph)
    assert (ids["end"], ids["bob_child"]) not in pairs
    assert (ids["end"], ids["system_child"]) not in pairs
    assert ids["bob_child"] not in str(graph)


def test_terminal_depth_and_root_stay_inside_the_trace(trace_db):
    from AINDY.platform_layer.event_trace_service import (
        calculate_trace_span,
        detect_root_event,
        detect_terminal_events,
    )

    db, ids = trace_db
    user = str(ALICE)
    terminals = [event["id"] for event in detect_terminal_events(db, TRACE, user_id=user)]
    assert terminals == [ids["note"]], "a node that queued the caller's own work is not terminal, as unscoped"
    span = calculate_trace_span(db, TRACE, user_id=user)
    assert span["depth"] == 2, "parent → start → end; the edge out of the trace adds no depth"
    assert span["node_count"] == 4
    assert span["edge_count"] == 5

    # The child trace: its first event has an edge in from the parent trace, and is still the root.
    assert detect_root_event(db, CHILD_TRACE, user_id=user)["id"] == ids["child"]
    assert detect_root_event(db, CHILD_TRACE)["id"] == ids["child"]
