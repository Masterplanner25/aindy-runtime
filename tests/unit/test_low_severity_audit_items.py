"""Two low-severity audit items.

1. Asking for another user's agent run must look exactly like asking for one that doesn't exist.
   A 403 for "exists, not yours" beside a 404 for "doesn't exist" told a caller which run ids are
   real. Run ids are UUIDs, so this was an oracle, not a way in.
2. The effect ledger keys a slot by action, input and scope, never by tenant. Every scope that
   reaches it is owner-bound since 2.31.2, so no cross-tenant collision is known. If one ever
   happens, the ledger refuses the slot rather than hand one tenant another's cached result.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.runtime_only

_OWNER = uuid.UUID("00000000-0000-0000-0000-00000000f001")
_OTHER = uuid.UUID("00000000-0000-0000-0000-00000000f002")


@pytest.fixture
def test_engine(tmp_path):
    from tests.fixtures.db import _import_model_registry

    from AINDY.db.database import Base

    _import_model_registry()
    engine = create_engine(
        f"sqlite:///{tmp_path / 'low.db'}",
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
    return sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=test_engine)


@pytest.fixture
def testing_session_factory(db_session_factory):
    return db_session_factory


@pytest.fixture
def run_id(db_session_factory):
    from AINDY.db.models import AgentRun

    session = db_session_factory()
    run = AgentRun(id=uuid.uuid4(), user_id=_OWNER, goal="g", status="completed", plan={"steps": []})
    session.add(run)
    session.commit()
    rid = str(run.id)
    session.close()
    return rid


@pytest.fixture
def client(runtime_only_app):
    with TestClient(runtime_only_app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def signed_in_as(runtime_only_app):
    from AINDY.auth.api_key_auth import Scopes
    from AINDY.services.auth_service import get_current_user

    def _apply(user):
        runtime_only_app.dependency_overrides[get_current_user] = lambda: {
            "sub": str(user),
            "user_id": str(user),
            "auth_type": "jwt",
            "is_admin": False,
            "session_scopes": list(Scopes.JWT_SESSION),
        }

    yield _apply
    runtime_only_app.dependency_overrides.pop(get_current_user, None)


# -- 1. no existence oracle on runs -------------------------------------------------------------


def _detail(response) -> str:
    body = response.json()
    return str(body.get("detail", body))


@pytest.mark.parametrize("path", ["/coordination/runs/{id}/children"])
def test_another_users_run_answers_like_a_missing_one(client, signed_in_as, run_id, path):
    signed_in_as(_OTHER)
    absent = str(uuid.uuid4())
    theirs = client.get(path.format(id=run_id))
    missing = client.get(path.format(id=absent))

    assert theirs.status_code == missing.status_code == 404, (theirs.text, missing.text)
    assert _detail(theirs).replace(run_id, "<id>") == _detail(missing).replace(absent, "<id>")


@pytest.mark.parametrize("path", ["/coordination/runs/{id}/children"])
def test_control_the_owner_still_reaches_their_run(client, signed_in_as, run_id, path):
    signed_in_as(_OWNER)
    response = client.get(path.format(id=run_id))
    assert response.status_code == 200, response.text[:400]


@pytest.mark.parametrize(
    "name", ["get_agent_run_runtime", "list_agent_run_steps_runtime", "list_agent_run_events_runtime"]
)
def test_the_shared_owner_check_does_not_tell_them_apart(db_session_factory, run_id, name):
    """Called directly: the app mounts these under `/apps/agent`, and runtime-only mode doesn't."""
    from AINDY.agents import runtime_api

    fn = getattr(runtime_api, name)
    session = db_session_factory()
    try:
        with pytest.raises(HTTPException) as theirs:
            fn(db=session, user_id=_OTHER, run_id=run_id)
        with pytest.raises(HTTPException) as missing:
            fn(db=session, user_id=_OTHER, run_id=str(uuid.uuid4()))
        assert (theirs.value.status_code, theirs.value.detail) == (missing.value.status_code, missing.value.detail) == (
            404,
            "Run not found",
        )
        assert fn(db=session, user_id=_OWNER, run_id=run_id) is not None
    finally:
        session.close()


# -- 2. the ledger never hands one tenant another's slot ----------------------------------------


def _claim_and_finish(session, action_id, tenant, result):
    from AINDY.kernel.effect_ledger import complete_effect_record, resolve_effect_record

    already, _ = resolve_effect_record(session, action_id, "memory.write", {"seq": 0}, tenant_id=tenant)
    assert already is False
    complete_effect_record(session, action_id, "success", result)


def test_another_tenant_is_refused_the_slot(db_session_factory):
    from AINDY.kernel.effect_ledger import EffectTenantMismatch, resolve_effect_record

    session = db_session_factory()
    try:
        _claim_and_finish(session, "aid-1", str(_OWNER), {"secret": "owner's"})

        with pytest.raises(EffectTenantMismatch):
            resolve_effect_record(session, "aid-1", "memory.write", {"seq": 0}, tenant_id=str(_OTHER))
    finally:
        session.close()


def test_control_the_owner_still_gets_the_replay(db_session_factory):
    from AINDY.kernel.effect_ledger import resolve_effect_record

    session = db_session_factory()
    try:
        _claim_and_finish(session, "aid-2", str(_OWNER), {"secret": "owner's"})

        already, cached = resolve_effect_record(
            session, "aid-2", "memory.write", {"seq": 0}, tenant_id=str(_OWNER).upper()
        )
        assert already is True
        assert cached == {"secret": "owner's"}
    finally:
        session.close()


def test_a_refusal_leaves_the_owners_row_alone(db_session_factory):
    from AINDY.db.models.effect_record import EffectRecord
    from AINDY.kernel.effect_ledger import EffectTenantMismatch, resolve_effect_record

    session = db_session_factory()
    try:
        _claim_and_finish(session, "aid-3", str(_OWNER), {"secret": "owner's"})
        with pytest.raises(EffectTenantMismatch):
            resolve_effect_record(session, "aid-3", "memory.write", {"seq": 0}, tenant_id=str(_OTHER))
        session.rollback()

        row = session.query(EffectRecord).filter(EffectRecord.action_id == "aid-3").one()
        assert (row.tenant_id, row.status, row.result_payload) == (str(_OWNER), "success", {"secret": "owner's"})
    finally:
        session.close()


def test_a_failed_slot_is_not_reclaimed_across_tenants(db_session_factory):
    """Reclaiming a failed row re-attributes it to the caller, so it must check the tenant first."""
    from AINDY.db.models.effect_record import EffectRecord
    from AINDY.kernel.effect_ledger import (
        EffectTenantMismatch,
        complete_effect_record,
        resolve_effect_record,
    )

    session = db_session_factory()
    try:
        resolve_effect_record(session, "aid-4", "memory.write", {"seq": 0}, tenant_id=str(_OWNER))
        complete_effect_record(session, "aid-4", "failed", None)

        with pytest.raises(EffectTenantMismatch):
            resolve_effect_record(session, "aid-4", "memory.write", {"seq": 0}, tenant_id=str(_OTHER))
        session.rollback()

        assert session.query(EffectRecord.tenant_id).filter(EffectRecord.action_id == "aid-4").scalar() == str(_OWNER)
    finally:
        session.close()


@pytest.mark.parametrize("row_tenant,caller", [(None, str(_OTHER)), (str(_OWNER), None)], ids=["row_unknown", "caller_unknown"])
def test_an_unknown_tenant_on_either_side_keeps_todays_behaviour(db_session_factory, row_tenant, caller):
    """Rows written before attribution existed carry no tenant; refusing them would break replay."""
    from AINDY.kernel.effect_ledger import resolve_effect_record

    session = db_session_factory()
    try:
        _claim_and_finish(session, "aid-5", row_tenant, {"r": 1})
        already, cached = resolve_effect_record(session, "aid-5", "memory.write", {"seq": 0}, tenant_id=caller)
        assert (already, cached) == (True, {"r": 1})
    finally:
        session.close()
