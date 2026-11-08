"""App feature requests FR-57, FR-58 and FR-60.

- FR-57: `AINDY_REGISTRATION=closed` stops new accounts, answering exactly as an open register does.
- FR-58: `GET /db/verify` (the whole schema) needs an operator, not just the shared service key.
- FR-60: the trace helpers take an optional `user_id` and keep only that user's events, memory
  nodes and the edges between them.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from tests.fixtures.db import build_private_engine

pytestmark = pytest.mark.runtime_only


# -- FR-57: closing registration -----------------------------------------------------------------


def _post_register(client, email, password="a-good-password"):
    return client.post("/auth/register", json={"email": email, "password": password, "username": None})


def _payload(resp) -> dict:
    data = dict(resp.json().get("data") or {})
    data.pop("execution_envelope", None)
    return data


@pytest.fixture
def registration(monkeypatch):
    from AINDY.config import settings

    def _set(mode):
        monkeypatch.setattr(settings, "AINDY_REGISTRATION", mode, raising=False)

    return _set


@pytest.fixture
def sent(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        "AINDY.platform_layer.email_channel.send_email",
        lambda *a, **k: calls.append((a, k)) or {"success": True},
    )
    return calls


@pytest.mark.parametrize("mode", ["open", ""], ids=["open", "empty_is_unset"])
def test_control_open_registration_creates_the_account(runtime_only_app, mock_db, registration, sent, mode):
    """An unset Compose variable renders as an empty string, which must mean the default, `open`."""
    from AINDY.db.models.user import User

    registration(mode)
    email = f"open-{uuid.uuid4().hex[:8]}@example.test"
    with TestClient(runtime_only_app, raise_server_exceptions=False) as client:
        resp = _post_register(client, email)

    assert resp.status_code == 202
    assert mock_db.query(User).filter(User.email == email).count() == 1
    assert sent, "liveness: an open register sends a verification email"


@pytest.mark.parametrize("mode", ["closed", "CLOSED", "nonsense"], ids=["closed", "case", "unknown_fails_closed"])
def test_closed_registration_creates_and_sends_nothing(runtime_only_app, mock_db, registration, sent, mode):
    from AINDY.db.models.user import User

    registration(mode)
    email = f"closed-{uuid.uuid4().hex[:8]}@example.test"
    with TestClient(runtime_only_app, raise_server_exceptions=False) as client:
        resp = _post_register(client, email)

    assert resp.status_code == 202, resp.text
    assert mock_db.query(User).filter(User.email == email).count() == 0
    assert sent == []


def test_closed_answers_exactly_as_open_does(runtime_only_app, mock_db, registration, sent):
    """The enumeration fix depends on one answer for every address; closing must not add a second."""
    with TestClient(runtime_only_app, raise_server_exceptions=False) as client:
        registration("open")
        opened = _post_register(client, f"a-{uuid.uuid4().hex[:8]}@example.test")
        registration("closed")
        closed_new = _post_register(client, f"b-{uuid.uuid4().hex[:8]}@example.test")
        closed_short = _post_register(client, f"c-{uuid.uuid4().hex[:8]}@example.test", password="x")

    assert opened.status_code == closed_new.status_code == closed_short.status_code == 202
    assert _payload(opened) == _payload(closed_new) == _payload(closed_short)


def test_closed_sends_no_notice_to_an_existing_address(runtime_only_app, mock_db, registration, sent):
    email = f"dup-{uuid.uuid4().hex[:8]}@example.test"
    with TestClient(runtime_only_app, raise_server_exceptions=False) as client:
        registration("open")
        _post_register(client, email)
        sent.clear()
        registration("closed")
        resp = _post_register(client, email)

    assert resp.status_code == 202
    assert sent == []


# -- FR-58: /db/verify needs an operator ---------------------------------------------------------


@pytest.fixture
def mounted_like_the_app():
    """The app mounts this router at the root, behind nothing but its own dependencies."""
    from AINDY.routes.db_verify_router import router as db_verify_router

    app = FastAPI()
    from AINDY.platform_layer.rate_limiter import limiter

    app.state.limiter = limiter
    app.include_router(db_verify_router)
    return app


def _principal(*, admin: bool) -> dict:
    return {"sub": str(uuid.uuid4()), "user_id": str(uuid.uuid4()), "auth_type": "jwt", "is_admin": admin}


def test_the_shared_key_alone_no_longer_reads_the_schema(mounted_like_the_app):
    with TestClient(mounted_like_the_app, raise_server_exceptions=False) as client:
        resp = client.get("/db/verify", headers={"X-API-Key": "test-api-key-for-pytest-only"})

    assert resp.status_code in (401, 403), resp.text
    assert "database_schema" not in resp.text


def test_an_ordinary_user_is_refused(mounted_like_the_app):
    from AINDY.services.auth_service import get_current_user

    mounted_like_the_app.dependency_overrides[get_current_user] = lambda: _principal(admin=False)
    with TestClient(mounted_like_the_app, raise_server_exceptions=False) as client:
        assert client.get("/db/verify").status_code == 403


def test_control_an_operator_reads_it(mounted_like_the_app):
    from AINDY.services.auth_service import get_current_user

    mounted_like_the_app.dependency_overrides[get_current_user] = lambda: _principal(admin=True)
    with TestClient(mounted_like_the_app, raise_server_exceptions=False) as client:
        resp = client.get("/db/verify")

    assert resp.status_code == 200, resp.text
    assert "database_schema" in resp.text


# -- FR-60: trace helpers scoped to one user -----------------------------------------------------

ALICE = uuid.UUID("00000000-0000-0000-0000-0000000060a1")
BOB = uuid.UUID("00000000-0000-0000-0000-0000000060b2")
TRACE = "trace-fr60"


@pytest.fixture
def trace_db(tmp_path):
    from AINDY.db.models.event_edge import EventEdge
    from AINDY.db.models.system_event import SystemEvent
    from AINDY.memory.memory_persistence import MemoryNodeModel

    engine = build_private_engine(tmp_path / "trace.db")
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    a1, a2, b1 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for event_id, user, kind in ((a1, ALICE, "a.start"), (a2, ALICE, "a.end"), (b1, BOB, "b.secret")):
        session.add(SystemEvent(id=event_id, type=kind, user_id=user, trace_id=TRACE, payload={"who": str(user)}))
    alice_node = MemoryNodeModel(id=uuid.uuid4(), content="alice's", tags=[], node_type="insight", user_id=ALICE, extra={})
    bob_node = MemoryNodeModel(id=uuid.uuid4(), content="bob's secret", tags=[], node_type="insight", user_id=BOB, extra={})
    session.add_all([alice_node, bob_node])
    session.flush()
    session.add_all(
        [
            EventEdge(source_event_id=a1, target_event_id=a2, relationship_type="caused"),
            EventEdge(source_event_id=a2, target_event_id=b1, relationship_type="caused"),
            EventEdge(source_event_id=a1, target_memory_node_id=alice_node.id, relationship_type="wrote"),
            EventEdge(source_event_id=b1, target_memory_node_id=bob_node.id, relationship_type="wrote"),
            # One of Alice's events reaching Bob's memory: only the memory filter keeps that node out.
            EventEdge(source_event_id=a2, target_memory_node_id=bob_node.id, relationship_type="read"),
        ]
    )
    session.commit()
    ids = {"a1": str(a1), "a2": str(a2), "b1": str(b1), "alice_node": str(alice_node.id), "bob_node": str(bob_node.id)}
    try:
        yield session, ids
    finally:
        session.close()
        engine.dispose()


def _node_ids(graph) -> set[str]:
    return {node["id"] for node in graph["nodes"]}


def test_control_unscoped_the_graph_holds_both_users(trace_db):
    from AINDY.platform_layer.event_trace_service import build_trace_graph

    db, ids = trace_db
    assert {ids["a1"], ids["b1"], ids["bob_node"]} <= _node_ids(build_trace_graph(db, TRACE))


def test_scoped_the_graph_holds_only_that_users_events_and_memory(trace_db):
    from AINDY.platform_layer.event_trace_service import build_trace_graph

    db, ids = trace_db
    graph = build_trace_graph(db, TRACE, user_id=str(ALICE))

    assert _node_ids(graph) == {ids["a1"], ids["a2"], ids["alice_node"]}
    assert "bob" not in str(graph)
    kept = _node_ids(graph)
    for edge in graph["edges"]:
        assert edge["source"] in kept and edge["target"] in kept, edge


def test_the_other_helpers_take_the_same_scope(trace_db):
    from AINDY.platform_layer.event_trace_service import (
        calculate_trace_span,
        detect_root_event,
        detect_terminal_events,
    )

    db, ids = trace_db
    user = str(ALICE)
    assert detect_root_event(db, TRACE, user_id=user)["id"] == ids["a1"]
    assert [e["id"] for e in detect_terminal_events(db, TRACE, user_id=user)] == [ids["a2"]]
    span = calculate_trace_span(db, TRACE, user_id=user)
    assert span["node_count"] == 3
    assert span["terminal_count"] == 1
    assert span["depth"] == 1, "depth is counted inside the scoped graph, not through Bob's events"


def test_the_execution_graph_route_passes_the_caller(trace_db):
    """`/observability/execution_graph` uses the runtime's own helpers when the app has not
    registered its own, and now hands them the caller."""
    from AINDY.runtime.flow_definitions_observability import observability_rippletrace_node

    db, ids = trace_db
    with patch("AINDY.platform_layer.registry.get_symbol", return_value=None):
        out = observability_rippletrace_node({"trace_id": TRACE}, {"db": db, "user_id": str(ALICE)})

    assert out["status"] == "SUCCESS", out
    result = out["output_patch"]["observability_rippletrace_result"]
    assert ids["b1"] not in _node_ids(result)
    assert "bob" not in str(result)


def test_an_app_helper_without_the_parameter_still_works(trace_db):
    """The app registers its own versions; until it adds `user_id` they are called as before."""
    from AINDY.runtime.flow_definitions_observability import observability_rippletrace_node

    db, _ids = trace_db
    called: list = []

    def _legacy_graph(db, trace_id):
        called.append(trace_id)
        return {"nodes": [], "edges": []}

    symbols = {
        "rippletrace_build_trace_graph": _legacy_graph,
        "rippletrace_calculate_ripple_span": lambda db, trace_id: {"node_count": 0, "edge_count": 0, "depth": 0, "terminal_count": 0},
        "rippletrace_detect_root_event": lambda db, trace_id: None,
        "rippletrace_detect_terminal_events": lambda db, trace_id: [],
        "rippletrace_generate_trace_insights": lambda db, trace_id: [],
    }
    with patch("AINDY.platform_layer.registry.get_symbol", side_effect=lambda name: symbols.get(name)):
        out = observability_rippletrace_node({"trace_id": TRACE}, {"db": db, "user_id": str(ALICE)})

    assert out["status"] == "SUCCESS", out
    assert called == [TRACE]
