"""Per-user watcher keys: the signal's user comes from the key, and the key opens nothing else.

The watcher runs on the user's own machine, so its key must be one that user can hold. Runs on a
private engine, because these routes answer 4xx and the key rows have to survive a rollback.
"""
from __future__ import annotations

import sys
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.runtime_only

_ALICE = uuid.UUID("00000000-0000-0000-0000-00000000e001")
_BOB = uuid.UUID("00000000-0000-0000-0000-00000000e002")
_SHARED = {"X-API-Key": "test-api-key-for-pytest-only"}


@pytest.fixture(autouse=True)
def _scopes_column_works_on_sqlite(monkeypatch):
    """`scopes` is a Postgres ARRAY, which SQLite can't bind. The Postgres path has its own test
    in `tests/integration/test_watcher_user_keys_postgres.py`."""
    from sqlalchemy import JSON

    from AINDY.db.models.api_key import PlatformAPIKey

    monkeypatch.setattr(PlatformAPIKey.__table__.c.scopes, "type", JSON())


@pytest.fixture
def test_engine(tmp_path):
    from tests.fixtures.db import _import_model_registry

    from AINDY.db.database import Base

    _import_model_registry()
    engine = create_engine(
        f"sqlite:///{tmp_path / 'watcher.db'}",
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
    with TestClient(runtime_only_app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def users(db_session_factory):
    from AINDY.db.models.user import User

    session = db_session_factory()
    for user_id in (_ALICE, _BOB):
        session.add(User(id=user_id, email=f"{user_id}@example.test", hashed_password="x"))
    session.commit()
    session.close()


@pytest.fixture
def mint(db_session_factory, users):
    from AINDY.auth.api_key_auth import Scopes
    from AINDY.platform_layer.api_key_service import create_api_key

    def _mint(user=_ALICE, scopes=(Scopes.WATCHER_SIGNALS,)):
        session = db_session_factory()
        try:
            record, raw = create_api_key(str(user), f"test-{uuid.uuid4()}", list(scopes), session)
            return str(record.id), {"X-Platform-Key": raw}
        finally:
            session.close()

    return _mint


@pytest.fixture
def ingested(monkeypatch):
    import AINDY.routes.watcher_router  # noqa: F401  (the package attribute is the router)

    seen: list[dict] = []

    def _flow(flow_name, payload, db, user_id):
        seen.append({"user_id": user_id, "signals": payload["signals"]})
        return {"accepted": len(payload["signals"]), "session_ended_count": 0}

    monkeypatch.setattr(sys.modules["AINDY.routes.watcher_router"], "_run_flow_watcher", _flow)
    return seen


@pytest.fixture
def queried(monkeypatch):
    seen: list[str | None] = []

    def _list(db, *, session_id=None, signal_type=None, user_id=None, limit=100, offset=0):
        seen.append(user_id)
        return {"signals": []}

    monkeypatch.setattr("AINDY.platform_layer.watcher_service.list_signals", _list)
    return seen


def _bearer(user=_ALICE) -> dict:
    from AINDY.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token({'sub': str(user)})}"}


def _batch(*user_ids) -> dict:
    return {
        "signals": [
            {
                "signal_type": "heartbeat",
                "session_id": "s",
                "timestamp": "2026-11-07T12:00:00Z",
                "app_name": "editor",
                "activity_type": "work",
                **({"user_id": str(u)} if u else {}),
            }
            for u in (user_ids or (None,))
        ]
    }


# -- the shared key still works, for services ---------------------------------------------------


def test_control_the_shared_key_can_still_name_a_user(client, ingested):
    response = client.post("/watcher/signals", json=_batch(_BOB), headers=_SHARED)

    assert response.status_code == 201, response.text
    assert ingested[0]["user_id"] == str(_BOB)


def test_control_the_shared_key_can_still_query_a_user(client, queried):
    assert client.get("/watcher/signals", params={"user_id": str(_BOB)}, headers=_SHARED).status_code == 200
    assert queried == [str(_BOB)]


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}, {"X-Platform-Key": "aindy_wrong"}], ids=["none", "bad_shared", "bad_platform"])
def test_no_valid_credential_is_refused(client, ingested, headers):
    assert client.post("/watcher/signals", json=_batch(), headers=headers).status_code == 401
    assert ingested == []


# -- a per-user key -----------------------------------------------------------------------------


def test_a_signal_with_no_user_belongs_to_the_keys_owner(client, ingested, mint):
    _, key = mint()
    response = client.post("/watcher/signals", json=_batch(), headers=key)

    assert response.status_code == 201, response.text
    assert ingested[0]["user_id"] == str(_ALICE)
    assert ingested[0]["signals"][0]["user_id"] == str(_ALICE)


def test_a_key_cannot_post_as_another_user(client, ingested, mint):
    _, key = mint()
    assert client.post("/watcher/signals", json=_batch(_BOB), headers=key).status_code == 403
    assert ingested == []


def test_one_signal_naming_another_user_refuses_the_batch(client, ingested, mint):
    _, key = mint()
    assert client.post("/watcher/signals", json=_batch(_ALICE, _BOB), headers=key).status_code == 403
    assert ingested == []


def test_naming_yourself_in_another_spelling_is_fine(client, ingested, mint):
    _, key = mint()
    body = _batch()
    body["signals"][0]["user_id"] = str(_ALICE).upper()

    assert client.post("/watcher/signals", json=body, headers=key).status_code == 201
    assert ingested[0]["signals"][0]["user_id"] == str(_ALICE)


def test_a_query_with_no_user_reads_the_keys_owner(client, queried, mint):
    _, key = mint()
    assert client.get("/watcher/signals", headers=key).status_code == 200
    assert queried == [str(_ALICE)]


def test_a_key_cannot_query_another_user(client, queried, mint):
    _, key = mint()
    assert client.get("/watcher/signals", params={"user_id": str(_BOB)}, headers=key).status_code == 403
    assert queried == []


def test_a_key_without_the_scope_is_refused(client, ingested, mint):
    _, key = mint(scopes=("flow.read",))
    assert client.post("/watcher/signals", json=_batch(), headers=key).status_code == 403
    assert ingested == []


def test_a_platform_admin_key_passes_but_is_still_its_owner(client, ingested, mint):
    _, key = mint(scopes=("platform.admin",))
    assert client.post("/watcher/signals", json=_batch(), headers=key).status_code == 201
    assert ingested[0]["user_id"] == str(_ALICE)


def test_a_platform_key_wins_over_a_shared_key_sent_beside_it(client, ingested, mint):
    _, key = mint()
    response = client.post("/watcher/signals", json=_batch(_BOB), headers={**_SHARED, **key})
    assert response.status_code == 403
    assert ingested == []


# -- the key opens nothing else -----------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/platform/flows"),
        ("GET", "/platform/keys"),
        ("POST", "/platform/syscall"),
        ("GET", "/coordination/graph"),
        ("GET", "/watcher/keys"),
    ],
)
def test_a_watcher_only_key_is_refused_everywhere_else(client, mint, method, path):
    _, key = mint()
    assert client.request(method, path, headers=key, json={} if method == "POST" else None).status_code == 403


def test_the_shared_resolver_refuses_it_for_every_other_caller(db_session_factory, mint):
    """MCP resolves keys through this function directly, without `get_current_user`."""
    from AINDY.services.auth_service import _resolve_platform_key_as_user

    _, key = mint()
    session = db_session_factory()
    try:
        with pytest.raises(HTTPException) as refused:
            _resolve_platform_key_as_user(key["X-Platform-Key"], session)
        assert refused.value.status_code == 403
        allowed = _resolve_platform_key_as_user(key["X-Platform-Key"], session, allow_watcher_only=True)
        assert allowed["sub"] == str(_ALICE)
    finally:
        session.close()


def test_control_a_key_with_another_scope_is_not_confined(client, mint):
    _, key = mint(scopes=("watcher.signals", "flow.read"))
    assert client.get("/platform/flows", headers=key).status_code == 200


# -- minting, for an ordinary user --------------------------------------------------------------


def test_an_ordinary_session_can_mint_a_key_that_works(client, ingested, users):
    created = client.post("/watcher/keys", json={"name": "laptop"}, headers=_bearer())

    assert created.status_code == 201, created.text
    assert created.json()["scopes"] == ["watcher.signals"]
    posted = client.post("/watcher/signals", json=_batch(), headers={"X-Platform-Key": created.json()["key"]})
    assert posted.status_code == 201
    assert ingested[0]["user_id"] == str(_ALICE)


def test_a_body_cannot_widen_a_minted_key(client, users):
    created = client.post(
        "/watcher/keys", json={"name": "laptop", "scopes": ["platform.admin"]}, headers=_bearer()
    )
    assert created.json()["scopes"] == ["watcher.signals"]


def test_a_duplicate_name_is_a_clean_refusal(client, users):
    assert client.post("/watcher/keys", json={"name": "laptop"}, headers=_bearer()).status_code == 201
    assert client.post("/watcher/keys", json={"name": "laptop"}, headers=_bearer()).status_code == 409
    assert client.post("/watcher/keys", json={"name": "desktop"}, headers=_bearer()).status_code == 201


def test_minting_needs_a_session(client, mint):
    assert client.post("/watcher/keys", json={"name": "x"}).status_code == 401
    _, admin_key = mint(scopes=("platform.admin",))
    assert client.post("/watcher/keys", json={"name": "x"}, headers=admin_key).status_code == 403


def test_the_list_shows_only_your_watcher_keys_without_secrets(client, mint):
    mine, _ = mint()
    mint(user=_BOB)
    mint(scopes=("flow.read",))

    listed = client.get("/watcher/keys", headers=_bearer())

    assert listed.status_code == 200, listed.text
    keys = listed.json()["keys"]
    assert [k["id"] for k in keys] == [mine]
    assert "key" not in keys[0] and "key_hash" not in keys[0]


def test_revoking_your_key_stops_it(client, ingested, mint):
    key_id, key = mint()
    assert client.post("/watcher/signals", json=_batch(), headers=key).status_code == 201

    assert client.delete(f"/watcher/keys/{key_id}", headers=_bearer()).status_code == 204
    assert client.post("/watcher/signals", json=_batch(), headers=key).status_code == 401


def test_you_cannot_revoke_someone_elses_key(client, ingested, mint):
    key_id, key = mint(user=_BOB)

    assert client.delete(f"/watcher/keys/{key_id}", headers=_bearer(_ALICE)).status_code == 404
    assert client.post("/watcher/signals", json=_batch(), headers=key).status_code == 201


def test_this_route_cannot_revoke_a_non_watcher_key(client, mint):
    key_id, key = mint(scopes=("flow.read",))

    assert client.delete(f"/watcher/keys/{key_id}", headers=_bearer()).status_code == 404
    assert client.get("/platform/flows", headers=key).status_code == 200
