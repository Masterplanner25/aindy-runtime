"""`/auth/mcp-grants`: a user sees and ends their own connected MCP apps (FR-66 phase 2).

Session-only, like the provider-key routes. A grant that isn't the caller's answers 404, as one
that doesn't exist. Runs on a private engine: these routes answer 4xx, and the grant rows have to
survive the app's rollback.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

pytest.importorskip("mcp")

pytestmark = pytest.mark.runtime_only

ALICE = uuid.UUID("00000000-0000-0000-0000-0000000a71ce")
BOB = uuid.UUID("00000000-0000-0000-0000-0000000000b0")
REDIRECT = "https://connector.example/callback"


@pytest.fixture(autouse=True)
def _scopes_column_works_on_sqlite(monkeypatch):
    from sqlalchemy import JSON

    from AINDY.db.models.api_key import PlatformAPIKey

    monkeypatch.setattr(PlatformAPIKey.__table__.c.scopes, "type", JSON())


@pytest.fixture
def test_engine(tmp_path):
    from tests.fixtures.db import _import_model_registry

    from AINDY.db.database import Base

    _import_model_registry()
    engine = create_engine(
        f"sqlite:///{tmp_path / 'grants.db'}",
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
def db_session_factory(test_engine, monkeypatch):
    factory = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=test_engine)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", factory)
    return factory


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

    db = db_session_factory()
    for uid in (ALICE, BOB):
        db.add(User(id=uid, email=f"{uid}@example.test", hashed_password="x"))
    db.commit()
    db.close()


def _grant(user: uuid.UUID, scopes=("memory.read",)) -> str:
    """A client, and a consented grant for `user`, made by the same functions the consent page uses."""
    from AINDY.db.database import SessionLocal
    from AINDY.db.models.mcp_oauth import McpOAuthGrant
    from AINDY.platform_layer.mcp_oauth import create_grant_with_code, register_client_cli

    made = register_client_cli(name=f"Connector {uuid.uuid4().hex[:6]}", redirect_uris=[REDIRECT], public=True)
    create_grant_with_code(
        user_id=str(user),
        pending={"cid": made["client_id"], "ru": REDIRECT, "rx": True, "cc": "x" * 43, "res": None},
        scopes=list(scopes),
    )
    db = SessionLocal()
    try:
        return str(db.query(McpOAuthGrant).filter(McpOAuthGrant.client_id == made["client_id"]).one().id)
    finally:
        db.close()


def _bearer(user: uuid.UUID) -> dict:
    from AINDY.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token({'sub': str(user)})}"}


def test_the_list_shows_only_your_live_grants(client, users):
    mine = _grant(ALICE)
    _grant(BOB)

    listed = client.get("/auth/mcp-grants", headers=_bearer(ALICE))

    assert listed.status_code == 200, listed.text
    grants = listed.json()["grants"]
    assert [g["id"] for g in grants] == [mine]
    assert grants[0]["redirect_host"] == "connector.example"
    assert grants[0]["scopes"] == ["memory.read"]
    assert not any(k in grants[0] for k in ("refresh_token_hash", "code_hash"))


def test_ending_a_grant_removes_it_and_stops_its_tokens(client, users):
    from AINDY.platform_layer.mcp_oauth import grant_is_live

    gid = _grant(ALICE)
    assert grant_is_live(gid, 0) is True  # control

    assert client.delete(f"/auth/mcp-grants/{gid}", headers=_bearer(ALICE)).status_code == 204
    assert client.get("/auth/mcp-grants", headers=_bearer(ALICE)).json()["grants"] == []
    assert grant_is_live(gid, 0) is False


def test_you_cannot_end_someone_elses_grant(client, users):
    from AINDY.platform_layer.mcp_oauth import grant_is_live

    bobs = _grant(BOB)

    assert client.delete(f"/auth/mcp-grants/{bobs}", headers=_bearer(ALICE)).status_code == 404
    assert grant_is_live(bobs, 0) is True


@pytest.mark.parametrize("grant_id", ["not-a-uuid", str(uuid.uuid4())])
def test_an_unknown_grant_is_404(client, users, grant_id):
    assert client.delete(f"/auth/mcp-grants/{grant_id}", headers=_bearer(ALICE)).status_code == 404


def test_an_api_key_cannot_see_or_end_grants(client, users, db_session_factory):
    from AINDY.platform_layer.api_key_service import create_api_key

    gid = _grant(ALICE)
    db = db_session_factory()
    _, raw = create_api_key(str(ALICE), "k", ["platform.admin"], db)
    db.close()
    key = {"X-Platform-Key": raw}

    assert client.get("/auth/mcp-grants", headers=key).status_code == 403
    assert client.delete(f"/auth/mcp-grants/{gid}", headers=key).status_code == 403
    assert client.get("/auth/mcp-grants").status_code == 401
