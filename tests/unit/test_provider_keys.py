"""FR-59 phase 1 (DEC-121): the encrypted per-user key store, its routes and the resolver.

Runs on a private engine, because the routes answer 4xx and rows must survive a rollback.
"""
from __future__ import annotations

import base64
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.runtime_only

ALICE = uuid.UUID("00000000-0000-0000-0000-000000005901")
BOB = uuid.UUID("00000000-0000-0000-0000-000000005902")
ADMIN = uuid.UUID("00000000-0000-0000-0000-000000005903")
SECRET = "sk-alice-0000000000000000000000-zz9Q"
ENV = "AINDY_SECRET_PROVIDER_KEY_ENCRYPTION_KEYS"


def _master() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    from AINDY.platform_layer import provider_keys

    key = _master()
    monkeypatch.setenv(ENV, key)
    monkeypatch.setattr(provider_keys, "_operator_cache", {})
    return key


@pytest.fixture
def test_engine(tmp_path):
    from tests.fixtures.db import _import_model_registry

    from AINDY.db.database import Base

    _import_model_registry()
    engine = create_engine(
        f"sqlite:///{tmp_path / 'provider_keys.db'}",
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
def users(db_session_factory):
    from AINDY.db.models.user import User

    session = db_session_factory()
    for user_id, admin in ((ALICE, False), (BOB, False), (ADMIN, True)):
        session.add(User(id=user_id, email=f"{user_id}@example.test", hashed_password="x", is_admin=admin))
    session.commit()
    session.close()


@pytest.fixture
def client(runtime_only_app, users):
    with TestClient(runtime_only_app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def mode(monkeypatch):
    from AINDY.config import settings

    def _set(value):
        monkeypatch.setattr(settings, "AINDY_PROVIDER_KEYS", value, raising=False)

    _set("server")
    return _set


def _bearer(user=ALICE) -> dict:
    from AINDY.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token({'sub': str(user)})}"}


def _put(client, provider="openai", key=SECRET, user=ALICE):
    return client.put(f"/auth/provider-keys/{provider}", json={"key": key}, headers=_bearer(user))


def _all_text(db_session_factory) -> str:
    """Every row the pipeline and event layer wrote, as one string, to search for a key."""
    session = db_session_factory()
    try:
        parts = []
        for table in ("execution_units", "system_events", "user_provider_keys"):
            for row in session.execute(text(f"SELECT * FROM {table}")).all():
                parts.append(repr(tuple(row)))
        return "\n".join(parts)
    finally:
        session.close()


def _counts(db_session_factory) -> dict[str, int]:
    session = db_session_factory()
    try:
        return {
            table: session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            for table in ("execution_units", "system_events", "user_provider_keys")
        }
    finally:
        session.close()


# -- encryption ---------------------------------------------------------------------------------


def test_a_sealed_key_opens_only_for_its_own_user_and_provider():
    from AINDY.platform_layer.provider_keys import ProviderKeyConfigError, open_sealed, seal

    ciphertext, version = seal(SECRET, user_id=str(ALICE), provider="openai")

    assert SECRET not in ciphertext
    assert open_sealed(ciphertext, key_version=version, user_id=str(ALICE), provider="openai") == SECRET
    with pytest.raises(ProviderKeyConfigError):
        open_sealed(ciphertext, key_version=version, user_id=str(BOB), provider="openai")
    with pytest.raises(ProviderKeyConfigError):
        open_sealed(ciphertext, key_version=version, user_id=str(ALICE), provider="anthropic")


def test_an_older_master_key_still_opens_its_rows(monkeypatch, master_key):
    from AINDY.platform_layer.provider_keys import open_sealed, seal

    ciphertext, version = seal(SECRET, user_id=str(ALICE), provider="openai")
    monkeypatch.setenv(ENV, f"{_master()},{master_key}")

    assert open_sealed(ciphertext, key_version=version, user_id=str(ALICE), provider="openai") == SECRET


@pytest.mark.parametrize("value", ["", "not-base64-%%%", base64.urlsafe_b64encode(b"short").decode()], ids=["missing", "garbage", "wrong_length"])
def test_a_missing_or_bad_master_key_is_a_config_error(monkeypatch, value):
    from AINDY.platform_layer.provider_keys import ProviderKeyConfigError, load_master_keys

    monkeypatch.setenv(ENV, value)
    with pytest.raises(ProviderKeyConfigError):
        load_master_keys()


def test_per_user_mode_refuses_to_start_without_a_master_key(monkeypatch, mode):
    from AINDY.platform_layer.provider_keys import check_startup

    monkeypatch.delenv(ENV, raising=False)
    mode("server")
    check_startup()  # control: server mode needs no master key
    mode("per_user")
    with pytest.raises(RuntimeError, match="per_user"):
        check_startup()


# -- the routes ---------------------------------------------------------------------------------


def test_set_then_list_shows_only_the_hint(client, db_session_factory):
    created = _put(client)
    listed = client.get("/auth/provider-keys", headers=_bearer())

    assert created.status_code == 200, created.text
    assert listed.status_code == 200, listed.text
    assert listed.json()["keys"] == [{"provider": "openai", "key_hint": "zz9Q", "updated_at": listed.json()["keys"][0]["updated_at"]}]
    assert SECRET not in created.text and SECRET not in listed.text


def test_the_key_is_in_no_record_the_request_left(client, db_session_factory):
    assert _put(client).status_code == 200
    assert _put(client, key=SECRET + "-replaced").status_code == 200

    counts = _counts(db_session_factory)
    assert counts["execution_units"] >= 2 and counts["system_events"] >= 2 and counts["user_provider_keys"] == 1, (
        f"liveness: the requests must have left records to search, got {counts}"
    )
    assert SECRET not in _all_text(db_session_factory), "a plaintext key reached a stored record"


def test_replace_and_delete(client):
    assert _put(client).status_code == 200
    assert _put(client, key="sk-new-key-abcd").json()["key_hint"] == "abcd"
    assert client.delete("/auth/provider-keys/openai", headers=_bearer()).status_code == 204
    assert client.get("/auth/provider-keys", headers=_bearer()).json()["keys"] == []
    assert client.delete("/auth/provider-keys/openai", headers=_bearer()).status_code == 404


def test_one_user_never_sees_another_users_keys(client):
    assert _put(client, user=ALICE).status_code == 200
    assert client.get("/auth/provider-keys", headers=_bearer(BOB)).json()["keys"] == []
    assert client.delete("/auth/provider-keys/openai", headers=_bearer(BOB)).status_code == 404


@pytest.mark.parametrize("provider,key", [("not-a-provider", SECRET), ("openai", "   "), ("openai", "x" * 5000)], ids=["provider", "empty", "too_long"])
def test_a_bad_request_is_refused_without_echoing_the_key(client, provider, key):
    resp = _put(client, provider=provider, key=key)

    assert resp.status_code == 422, resp.text
    assert SECRET not in resp.text and ("x" * 50) not in resp.text


def test_an_api_key_cannot_manage_provider_keys(client, runtime_only_app):
    from AINDY.services.auth_service import get_current_user

    runtime_only_app.dependency_overrides[get_current_user] = lambda: {
        "sub": str(ALICE), "user_id": str(ALICE), "auth_type": "api_key", "api_key_scopes": ["platform.admin"],
    }
    try:
        assert client.put("/auth/provider-keys/openai", json={"key": SECRET}).status_code == 403
        assert client.get("/auth/provider-keys").status_code == 403
    finally:
        runtime_only_app.dependency_overrides.pop(get_current_user, None)


def test_no_session_is_refused(client):
    assert client.get("/auth/provider-keys").status_code == 401


def test_without_a_master_key_nothing_is_stored(client, monkeypatch, db_session_factory):
    monkeypatch.delenv(ENV, raising=False)

    assert _put(client).status_code == 503
    assert _counts(db_session_factory)["user_provider_keys"] == 0


# -- the resolver -------------------------------------------------------------------------------


@pytest.fixture
def server_keys(monkeypatch):
    from AINDY.config import settings

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-server-key", raising=False)


def _resolve(db_session_factory, user, provider="openai"):
    from AINDY.platform_layer.provider_keys import resolve_provider_key

    session = db_session_factory()
    try:
        return resolve_provider_key(provider, str(user) if user else None, db=session)
    finally:
        session.close()


def test_server_mode_ignores_a_stored_key(client, db_session_factory, mode, server_keys):
    assert _put(client).status_code == 200
    resolved = _resolve(db_session_factory, ALICE)

    assert (resolved.source, resolved.value) == ("server", "sk-server-key")


def test_per_user_mode_uses_the_callers_own_key(client, db_session_factory, mode, server_keys):
    assert _put(client).status_code == 200
    mode("per_user")
    resolved = _resolve(db_session_factory, ALICE)

    assert (resolved.source, resolved.value) == ("user", SECRET)
    assert SECRET not in repr(resolved)


def test_a_user_without_a_key_is_refused_not_given_the_servers(client, db_session_factory, mode, server_keys):
    from AINDY.platform_layer.provider_keys import ProviderKeyRequired

    mode("per_user")
    with pytest.raises(ProviderKeyRequired) as refused:
        _resolve(db_session_factory, BOB)

    assert refused.value.provider == "openai"
    assert "sk-server-key" not in str(refused.value)


def test_an_operator_without_a_key_uses_the_servers(client, db_session_factory, mode, server_keys):
    mode("per_user")
    resolved = _resolve(db_session_factory, ADMIN)

    assert (resolved.source, resolved.value) == ("server", "sk-server-key")


def test_a_call_with_no_user_uses_the_servers(db_session_factory, mode, server_keys, users):
    mode("per_user")
    assert _resolve(db_session_factory, None).source == "server"


def test_an_unknown_provider_is_refused(db_session_factory, mode, users):
    mode("per_user")
    with pytest.raises(ValueError):
        _resolve(db_session_factory, ALICE, provider="nope")


def test_the_resolver_opens_its_own_session_when_given_none(client, db_session_factory, mode, monkeypatch):
    from AINDY.platform_layer.provider_keys import resolve_provider_key

    assert _put(client).status_code == 200
    mode("per_user")
    monkeypatch.setattr("AINDY.db.database.SessionLocal", db_session_factory)

    assert resolve_provider_key("openai", str(ALICE)).value == SECRET


# -- rotation -----------------------------------------------------------------------------------


def test_rotation_reseals_under_the_new_key_and_the_old_key_can_go(client, db_session_factory, monkeypatch, master_key, mode):
    from AINDY.platform_layer.provider_keys import rotate_master_key

    assert _put(client).status_code == 200
    new = _master()
    monkeypatch.setenv(ENV, f"{new},{master_key}")

    session = db_session_factory()
    try:
        assert rotate_master_key(session, dry_run=True)["would_reseal"] == 1
        report = rotate_master_key(session, dry_run=False)
    finally:
        session.close()
    assert report["resealed"] == 1

    monkeypatch.setenv(ENV, new)  # the old key dropped
    mode("per_user")
    assert _resolve(db_session_factory, ALICE).value == SECRET
