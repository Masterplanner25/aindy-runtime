"""FR-70 (DEC-133): an app route that takes the caller's own activity can admit a watcher key.

`get_activity_caller` takes a session, or a platform key holding `watcher.signals` (or
`platform.admin`). That includes the watcher-only key, which every other route refuses (DEC-117).
It never takes the shared `X-API-Key`.

The routes here are mounted on the real runtime app the way an app plugin's router is, and every
case calls them over HTTP. Each opt-in case has a control on a `get_current_user` route beside it,
so a pass shows the opt-in is what admitted the key. Runs on a private engine, because these
routes answer 4xx and the key rows have to survive a rollback.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.runtime_only

_ALICE = uuid.UUID("00000000-0000-0000-0000-00000000f701")
_SHARED = {"X-API-Key": "test-api-key-for-pytest-only"}
_ACTIVITY = "/fr70-test/activity"
_SESSION_ONLY = "/fr70-test/session-only"
_SCOPED = "/fr70-test/activity-plus-scope"


@pytest.fixture(autouse=True)
def _scopes_column_works_on_sqlite(monkeypatch):
    """`scopes` is a Postgres ARRAY, which SQLite can't bind (as in test_watcher_user_keys.py)."""
    from sqlalchemy import JSON

    from AINDY.db.models.api_key import PlatformAPIKey

    monkeypatch.setattr(PlatformAPIKey.__table__.c.scopes, "type", JSON())


@pytest.fixture
def test_engine(tmp_path):
    from tests.fixtures.db import _import_model_registry

    from AINDY.db.database import Base

    _import_model_registry()
    engine = create_engine(
        f"sqlite:///{tmp_path / 'fr70.db'}",
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
def client(runtime_only_app):
    from AINDY.services.auth_service import (
        enforce_api_key_scope,
        get_activity_caller,
        get_current_user,
    )

    router = APIRouter()

    @router.post(_ACTIVITY)
    def _activity(current_user: dict = Depends(get_activity_caller)):
        return {"sub": current_user["sub"], "auth_type": current_user.get("auth_type")}

    @router.post(_SESSION_ONLY)
    def _session_only(current_user: dict = Depends(get_current_user)):
        return {"sub": current_user["sub"]}

    @router.post(_SCOPED, dependencies=[Depends(enforce_api_key_scope("flow.read"))])
    def _scoped(current_user: dict = Depends(get_activity_caller)):
        return {"sub": current_user["sub"]}

    runtime_only_app.include_router(router)
    with TestClient(runtime_only_app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def alice(db_session_factory):
    from AINDY.db.models.user import User

    session = db_session_factory()
    session.add(User(id=_ALICE, email=f"{_ALICE}@example.test", hashed_password="x"))
    session.commit()
    session.close()


@pytest.fixture
def mint(db_session_factory, alice):
    from AINDY.auth.api_key_auth import Scopes
    from AINDY.platform_layer.api_key_service import create_api_key

    def _mint(scopes=(Scopes.WATCHER_SIGNALS,)):
        session = db_session_factory()
        try:
            record, raw = create_api_key(str(_ALICE), f"test-{uuid.uuid4()}", list(scopes), session)
            return str(record.id), {"X-Platform-Key": raw}
        finally:
            session.close()

    return _mint


def _bearer() -> dict:
    from AINDY.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token({'sub': str(_ALICE)})}"}


# -- admitted -----------------------------------------------------------------------------------


def test_the_watcher_only_key_is_admitted_and_is_its_owner(client, mint):
    _, key = mint()

    response = client.post(_ACTIVITY, headers=key)

    assert response.status_code == 200, response.text
    assert response.json() == {"sub": str(_ALICE), "auth_type": "api_key"}


def test_control_the_same_key_is_refused_on_a_get_current_user_route(client, mint):
    """Liveness for the opt-in: without it the key is refused, as DEC-117 says."""
    _, key = mint()
    assert client.post(_SESSION_ONLY, headers=key).status_code == 403


def test_a_key_minted_at_watcher_keys_is_admitted(client, alice):
    created = client.post("/watcher/keys", json={"name": "laptop-hook"}, headers=_bearer())
    assert created.status_code == 201, created.text

    response = client.post(_ACTIVITY, headers={"X-Platform-Key": created.json()["key"]})

    assert response.status_code == 200, response.text
    assert response.json()["sub"] == str(_ALICE)


def test_a_session_is_admitted(client, alice):
    response = client.post(_ACTIVITY, headers=_bearer())

    assert response.status_code == 200, response.text
    assert response.json()["sub"] == str(_ALICE)
    assert response.json()["auth_type"] != "api_key"


@pytest.mark.parametrize(
    "scopes",
    [("watcher.signals", "flow.read"), ("platform.admin",)],
    ids=["watcher_plus_another", "platform_admin"],
)
def test_a_wider_key_holding_the_scope_is_admitted(client, mint, scopes):
    _, key = mint(scopes=scopes)
    response = client.post(_ACTIVITY, headers=key)
    assert response.status_code == 200, response.text
    assert response.json()["sub"] == str(_ALICE)


# -- refused ------------------------------------------------------------------------------------


def test_a_key_without_the_scope_is_refused(client, mint):
    """Narrower than a session: a key `get_current_user` takes is refused here."""
    _, key = mint(scopes=("flow.read",))

    assert client.post(_SESSION_ONLY, headers=key).status_code == 200  # control: the key is valid
    refused = client.post(_ACTIVITY, headers=key)
    assert refused.status_code == 403
    assert "watcher.signals" in refused.json()["message"]


def test_the_shared_service_key_is_refused(client, alice):
    """The caller is always a user; the shared key names nobody."""
    assert client.post(_ACTIVITY, headers=_SHARED).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-Platform-Key": "aindy_wrong"}, {"Authorization": "Bearer not-a-token"}],
    ids=["none", "bad_platform", "bad_bearer"],
)
def test_no_valid_credential_is_refused(client, alice, headers):
    assert client.post(_ACTIVITY, headers=headers).status_code == 401


def test_a_revoked_key_is_refused(client, mint):
    key_id, key = mint()
    assert client.post(_ACTIVITY, headers=key).status_code == 200

    assert client.delete(f"/watcher/keys/{key_id}", headers=_bearer()).status_code == 204
    assert client.post(_ACTIVITY, headers=key).status_code == 401


def test_a_platform_key_wins_over_a_session_sent_beside_it(client, mint):
    """As on the watcher routes: a key that fails is not rescued by a bearer token."""
    _, key = mint(scopes=("flow.read",))
    assert client.post(_ACTIVITY, headers={**_bearer(), **key}).status_code == 403


def test_beside_a_scope_check_the_watcher_key_is_refused(client, mint):
    """The docstring's warning, pinned: `enforce_api_key_scope` resolves through
    `get_current_user`, which refuses a watcher-only key, so the route does too."""
    _, key = mint()
    assert client.post(_SCOPED, headers=key).status_code == 403


# -- the watcher routes still answer the same, through the shared helper --------------------------


def test_the_watcher_route_takes_the_same_rule(client, mint):
    """`_watcher_caller` now resolves through `_resolve_activity_key`; the unchanged
    `test_watcher_user_keys.py` pins the rest of its behaviour."""
    _, no_scope = mint(scopes=("flow.read",))
    _, watcher = mint()

    assert client.get("/watcher/signals", headers=no_scope).status_code == 403
    assert client.get("/watcher/signals", headers=watcher).status_code != 403
