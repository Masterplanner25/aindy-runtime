"""EVENT-OUTBOX-1 — inside a pipeline, a queued system event rides the handler's transaction
(DEC-060..062).

Before: `queue_system_event` appended a dict to an in-memory bucket on the execution context;
the handler committed its own work; the pipeline flushed the bucket AFTER the handler, each emit
on its own commit under a swallowing `try`. A crash or an emit failure between the handler's
commit and that flush kept the work and lost the record of it. (Narrower than filed: the buffer
engages only inside a request pipeline — every other path already writes on the caller's
session — and a lost row lowers a memory-significance SCORE, it gates nothing.)

Now the row is ADDED to the handler's session without commit. It rides whatever the handler
commits next — and the FR-30 EU finalize is the last commit of every request — and it rolls
back with the work if the work rolls back. The post-handler pass runs only the DERIVED effects
(internal handlers, feedback signals, memory capture, webhooks) for a row already persisted.

★ Every assertion about the row reads through a connection that did NOT share the request's
transaction — `test_request_eu_finalize_commits_fr30.py`'s instrument (catalogue variant 15:
the shared fixture cannot tell a flush from a commit). Liveness control first.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi import APIRouter, Depends, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from AINDY.core.execution_helper import execute_with_pipeline
from AINDY.core.execution_signal_helper import queue_system_event
from AINDY.db.database import get_db
from AINDY.services.auth_service import get_current_user

pytestmark = pytest.mark.runtime_only

_TYPE = "test.outbox.probe"


# ── the instrument: separate connections, no shared transaction (FR-30's) ────


@pytest.fixture
def test_engine(tmp_path):
    from tests.fixtures.db import build_private_engine

    engine = build_private_engine(tmp_path / "outbox.db")
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session_factory(test_engine):
    return sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=test_engine)


@pytest.fixture
def testing_session_factory(db_session_factory):
    return db_session_factory


@pytest.fixture
def rows(db_session_factory):
    """The observer: a fresh session on its own connection, opened AFTER the request returned."""
    def _rows(marker: str):
        from AINDY.db.models.system_event import SystemEvent

        s = db_session_factory()
        try:
            return [
                r for r in s.query(SystemEvent).filter(SystemEvent.type == _TYPE).all()
                if (r.payload or {}).get("marker") == marker
            ]
        finally:
            s.close()

    return _rows


def _admin_session_for(uid: str):
    from AINDY.auth.api_key_auth import derive_session_scopes

    def _session():
        return {"sub": uid, "user_id": uid, "auth_type": "jwt", "is_admin": True,
                "session_scopes": derive_session_scopes(is_admin=True)}

    return _session


@pytest.fixture
def client(runtime_only_app):
    """Three ad-hoc pipeline routes: a handler that queues and returns; one that queues and
    RAISES; one that queues and never writes anything else (a pure read)."""
    router = APIRouter()

    def _queue(db, marker):
        return queue_system_event(db=db, event_type=_TYPE, payload={"marker": marker}, source="test")

    @router.get("/outbox/ok")
    async def ok(request: Request, marker: str, db=Depends(get_db)):
        async def handler(ctx):
            return {"event_id": str(_queue(db, marker))}

        return await execute_with_pipeline(request=request, route_name="test.outbox.ok", handler=handler, metadata={"db": db})

    @router.get("/outbox/raises")
    async def raises(request: Request, marker: str, db=Depends(get_db)):
        async def handler(ctx):
            _queue(db, marker)
            raise RuntimeError("handler failed after queuing")

        return await execute_with_pipeline(request=request, route_name="test.outbox.raises", handler=handler, metadata={"db": db})

    @router.get("/outbox/readonly")
    async def readonly(request: Request, marker: str, db=Depends(get_db)):
        async def handler(ctx):
            _queue(db, marker)
            return {"read": True}  # no write, no commit of its own

        return await execute_with_pipeline(request=request, route_name="test.outbox.readonly", handler=handler, metadata={"db": db})

    runtime_only_app.include_router(router)
    runtime_only_app.dependency_overrides[get_current_user] = _admin_session_for(str(uuid.uuid4()))
    with TestClient(runtime_only_app, raise_server_exceptions=False) as c:
        yield c


# ── liveness: the instrument can tell flush from commit ──────────────────────


def test_the_instrument_distinguishes_a_flush_from_a_commit(db_session_factory, rows):
    from AINDY.db.models.system_event import SystemEvent

    marker = str(uuid.uuid4())
    writer = db_session_factory()
    writer.add(SystemEvent(id=uuid.uuid4(), type=_TYPE, payload={"marker": marker}))
    writer.flush()
    writer.close()  # what get_db does: close, no commit
    assert rows(marker) == [], "a flushed-then-closed row read as committed — the instrument is blind"

    writer = db_session_factory()
    writer.add(SystemEvent(id=uuid.uuid4(), type=_TYPE, payload={"marker": marker}))
    writer.commit()
    writer.close()
    assert len(rows(marker)) == 1


# ── DEC-060: the event rides the handler's transaction ───────────────────────


def test_a_queued_event_survives_a_crashed_post_handler_flush(client, rows):
    """★ The window the entry named. The flush is made to raise; the row is there anyway,
    because it was on the handler's session before the handler returned."""
    from AINDY.core.execution_pipeline import pipeline as pl

    marker = str(uuid.uuid4())

    def _crash(self, ctx, events_signal):
        raise RuntimeError("post-handler flush crashed")

    with patch.object(pl.ExecutionPipeline, "_apply_event_signals", _crash):
        response = client.get("/outbox/ok", params={"marker": marker})

    assert response.status_code in (200, 500), response.text
    found = rows(marker)
    assert len(found) == 1, (
        "the handler's event was lost when the post-handler flush crashed — it was buffered in "
        "memory and written after the work instead of riding the work's transaction"
    )
    assert found[0].source == "test"


def test_a_read_only_handler_event_rides_the_eu_finalize_commit(client, rows):
    """§4's hazard: a handler that never commits. The FR-30 finalize is the last commit on
    every request, and the row rides it — read through the separate connection."""
    marker = str(uuid.uuid4())

    response = client.get("/outbox/readonly", params={"marker": marker})

    assert response.status_code == 200, response.text
    assert len(rows(marker)) == 1


def test_the_returned_id_is_the_rows_id(client, rows):
    """DEC-061 — the id stays client-assigned; a caller holding it can reference the row."""
    marker = str(uuid.uuid4())

    response = client.get("/outbox/ok", params={"marker": marker})

    assert response.status_code == 200, response.text
    returned = response.json()["data"]["event_id"]
    (row,) = rows(marker)
    assert str(row.id) == returned


# ── DEC-062: rollback semantics ──────────────────────────────────────────────


def test_a_handler_that_raises_leaves_no_event_row(client, rows):
    """The control for the first test, and the point of the mechanism: no record of work that
    did not happen. The pipeline's own `execution.failed` is written on its own path."""
    marker = str(uuid.uuid4())

    response = client.get("/outbox/raises", params={"marker": marker})

    assert response.status_code == 500, response.text
    assert rows(marker) == [], "an event for work that rolled back was committed anyway"


# ── the derived effects still run, once, for a persisted row ─────────────────


def test_derived_effects_run_once_for_a_persisted_event(client, rows):
    """The post-handler pass no longer INSERTS the row, but the things `emit_system_event` did
    after inserting (internal handlers, feedback signals, capture, webhooks) are derived from
    the record and still happen — exactly once, with the row's own id."""
    marker = str(uuid.uuid4())
    seen: list = []

    def _spy(**kw):
        if kw.get("event_type") == _TYPE:
            seen.append(kw)

    with patch("AINDY.platform_layer.event_service.dispatch_internal_event_handlers", _spy):
        response = client.get("/outbox/ok", params={"marker": marker})

    assert response.status_code == 200, response.text
    (row,) = rows(marker)
    assert len(seen) == 1, f"derived effects ran {len(seen)} times for one event"
    assert seen[0]["event_id"] == str(row.id)


# ── the non-pipeline path is untouched ───────────────────────────────────────


def test_outside_a_pipeline_the_event_is_emitted_and_committed_directly(db_session_factory, rows):
    """Every non-request path already wrote on the caller's session and committed; that is the
    entry's own 'cheaper fix' and it is not changed."""
    marker = str(uuid.uuid4())
    db = db_session_factory()
    try:
        event_id = queue_system_event(db=db, event_type=_TYPE, payload={"marker": marker}, source="test")
    finally:
        db.close()

    assert event_id is not None
    (row,) = rows(marker)
    assert row.id == event_id


def test_the_execution_contract_gate_still_refuses_outside_a_pipeline(db_session_factory, monkeypatch):
    from AINDY.core import system_event_service as ses

    monkeypatch.setattr(ses.settings, "ENFORCE_EXECUTION_CONTRACT", True, raising=False)
    db = db_session_factory()
    try:
        with pytest.raises(RuntimeError, match="ExecutionContract violation"):
            ses.emit_system_event(db=db, event_type="execution.started", payload={})
    finally:
        db.close()


def test_the_in_pipeline_path_does_not_commit(client, monkeypatch):
    """The handler decides when. `queue_system_event` must never commit the handler's session
    itself — a commit there would land pending handler changes as a side effect, which is the
    'eager emit' the entry forbids."""
    from AINDY.core import execution_signal_helper as helper

    commits: list = []
    original = helper._persist_on_session

    def _spy(db, **kw):
        real_commit = db.commit

        def _count():
            commits.append(1)
            return real_commit()

        db.commit = _count
        try:
            return original(db, **kw)
        finally:
            db.commit = real_commit

    with patch.object(helper, "_persist_on_session", _spy):
        assert client.get("/outbox/ok", params={"marker": str(uuid.uuid4())}).status_code == 200
    assert commits == []
