"""FR-30 / `EU-FINALIZE-UNCOMMITTED-1` — a request's execution unit must actually reach `completed`.

Filed by the app team on 2026-09-15, one day after their FR-29, while checking the 2.15.0
handoff's sentence *"a request's execution unit describes the request: when the handler returns,
it completes."* Their table said it never had — every route-sourced unit on their stack was
`executing`, 900+ rows since 2026-07-23, 19 more per Tutorial 2 pass — with `execution.completed`
on every trace and nothing logged.

The mechanism, verified from source: `_safe_finalize_eu` (`execution_pipeline/resources.py`)
calls `ExecutionUnitService.update_status`, which ends in `db.flush()` — no commit. It is the
LAST write on the request session: the `execution.completed` emit before it committed, and
`db/database.py::get_db` tears down with `close()`, which rolls the flushed UPDATE back. The
finalize succeeded, recorded `ok`, and was undone on every request.

★ Why no test in this repo could see it — and why this file builds its own instrument. The shared
`db_session` / `runtime_only_app` fixtures bind BOTH the app's request session and the test's
reading session to ONE connection holding ONE outer transaction (SQLite `StaticPool`, and the
`db_connection` fixture's `connection.begin()`). Inside that transaction a flushed UPDATE is
readable by the other session exactly as a committed one would be. `test_wait_detect_reader_park_
fr29.py` asserted `reader.status == "completed"` the day before this was filed, and passed, on
the pre-fix code. **A fixture that shares the connection makes flush and commit
indistinguishable** — catalogue variant 15. So here the app and the test read through DIFFERENT
connections (a file-backed SQLite engine with `NullPool`, sessions with no outer transaction),
and the request session is torn down exactly as production's `get_db` tears it down.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from AINDY.services.auth_service import get_current_user

pytestmark = pytest.mark.runtime_only


# ── the instrument: separate connections, no shared transaction ───────────────


@pytest.fixture
def test_engine(tmp_path):
    """Overrides the session-scoped in-memory `StaticPool` engine for this module only.

    File-backed so that two connections see one database; `NullPool` so every session gets its
    own connection; FKs off to match the shared fixture (the FK half of FR-29 has its own test).
    """
    from tests.fixtures.db import _import_model_registry  # registers compilers + the full model set

    from AINDY.db.database import Base

    _import_model_registry()

    engine = create_engine(
        f"sqlite:///{tmp_path / 'fr30.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        try:
            cur.execute("PRAGMA foreign_keys=OFF")
        finally:
            cur.close()

    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session_factory(test_engine):
    """Plain sessions, one connection each, NOT bound to a connection holding an outer
    transaction — so a request session's `close()` rolls back exactly what production's does."""
    return sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=test_engine)


@pytest.fixture
def testing_session_factory(db_session_factory):
    return db_session_factory


@pytest.fixture
def reader(db_session_factory):
    """The observer: a session on its own connection, opened AFTER the request has returned."""
    def _read_eu(eu_id):
        from AINDY.db.models.execution_unit import ExecutionUnit

        session = db_session_factory()
        try:
            row = session.query(ExecutionUnit).filter(ExecutionUnit.id == uuid.UUID(str(eu_id))).one_or_none()
            assert row is not None, f"no execution_units row for eu_id={eu_id}"
            return row.status
        finally:
            session.close()

    return _read_eu


def _admin_session_for(uid: str):
    from AINDY.auth.api_key_auth import derive_session_scopes

    def _session():
        return {
            "sub": uid,
            "user_id": uid,
            "auth_type": "jwt",
            "is_admin": True,
            "session_scopes": derive_session_scopes(is_admin=True),
        }

    return _session


@pytest.fixture
def client(runtime_only_app):
    runtime_only_app.dependency_overrides[get_current_user] = _admin_session_for(str(uuid.uuid4()))
    with TestClient(runtime_only_app, raise_server_exceptions=False) as c:
        yield c


# ── liveness: the instrument can tell flush from commit ──────────────────────


def test_the_instrument_distinguishes_a_flush_from_a_commit(db_session_factory, reader):
    """If this fails, every assertion below is vacuous — the same blindness the shared fixture
    has. A flushed-then-closed UPDATE must NOT be visible from another connection."""
    from AINDY.db.models.execution_unit import ExecutionUnit

    writer = db_session_factory()
    eu = ExecutionUnit(id=uuid.uuid4(), type="probe", status="executing", source_type="test", source_id="x")
    writer.add(eu)
    writer.commit()
    eu_id = eu.id

    writer.query(ExecutionUnit).filter(ExecutionUnit.id == eu_id).update({"status": "completed"})
    writer.flush()
    writer.close()  # what get_db does: close, no commit
    assert reader(eu_id) == "executing", (
        "a flush followed by close() must roll back — if it reads as committed, the "
        "instrument shares a transaction with the writer and cannot see FR-30"
    )

    writer = db_session_factory()
    writer.query(ExecutionUnit).filter(ExecutionUnit.id == eu_id).update({"status": "completed"})
    writer.commit()
    writer.close()
    assert reader(eu_id) == "completed"


# ── the defect, at the route ──────────────────────────────────────────────────


def test_a_successful_request_leaves_its_unit_completed(client, reader):
    """`GET /platform/flows/runs` — any route through the pipeline. The envelope names the unit;
    read it back through a connection that did not share the request's transaction."""
    response = client.get("/platform/flows/runs")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    assert body["metadata"]["side_effects"]["execution_unit.finalize.completed"]["status"] == "ok", (
        "the pipeline RECORDED the finalize as ok — that is the claim the table must agree with"
    )
    assert reader(body["eu_id"]) == "completed", (
        "the pipeline said it finalised the unit; the database says otherwise — the terminal "
        "status was flushed, never committed, and rolled back on session close"
    )


def _route_unit_ids(db_session_factory):
    """Only the REQUEST's unit (`source_type="route"`) — `get_flow_run` runs the `flow_run_get`
    flow to answer, and that flow mints a unit of its own."""
    from AINDY.db.models.execution_unit import ExecutionUnit

    session = db_session_factory()
    try:
        return {row.id for row in session.query(ExecutionUnit.id).filter(ExecutionUnit.source_type == "route").all()}
    finally:
        session.close()


def test_a_failed_request_leaves_its_unit_failed(client, reader, db_session_factory):
    """The other terminal status takes the same path (`_safe_finalize_eu(ctx, "failed")`). A
    404 body carries no `eu_id`, so the request's unit is the one row the request created."""
    before = _route_unit_ids(db_session_factory)
    response = client.get(f"/platform/flows/runs/{uuid.uuid4()}")
    assert response.status_code == 404, response.text
    created = _route_unit_ids(db_session_factory) - before
    assert len(created) == 1, f"expected exactly one new route unit for the request, got {len(created)}"
    (eu_id,) = created
    assert reader(eu_id) == "failed", "a 404'd request's unit must not sit `executing` forever"


def test_units_do_not_accumulate_in_executing(client, db_session_factory):
    """The live signature: 19 `executing` route units per tutorial pass, ~900 on the stack."""
    from AINDY.db.models.execution_unit import ExecutionUnit

    for _ in range(5):
        assert client.get("/platform/flows/runs").status_code == 200
    session = db_session_factory()
    try:
        stuck = (
            session.query(ExecutionUnit)
            .filter(ExecutionUnit.source_type == "route", ExecutionUnit.status == "executing")
            .count()
        )
    finally:
        session.close()
    assert stuck == 0, f"{stuck} route unit(s) still `executing` after their requests returned"
