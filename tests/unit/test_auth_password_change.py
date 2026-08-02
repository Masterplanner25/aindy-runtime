"""
FR-6 item 1 — self-service password change (`POST /auth/password/change`).

Two layers:
  * service contract — `change_user_password` rejection matrix, against a stub user
    and a MagicMock session (no DB).
  * route contract — the real router through TestClient with a real User row, so the
    ExecutionPipeline → handler → event-emit chain is covered.

Session invalidation is the load-bearing behavior: a password change must advance
`token_version`, which is what makes every previously-issued JWT stop verifying.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from AINDY.services.auth_service import (
    MIN_PASSWORD_LENGTH,
    bump_token_version,
    change_user_password,
    get_current_user,
    hash_password,
    verify_password,
)


pytestmark = pytest.mark.runtime_only

_OLD = "correct-horse"
_NEW = "battery-staple-9"


# ── service layer ───────────────────────────────────────────────────────────

class _StubUser:
    def __init__(self, password=_OLD, is_active=True, token_version=0):
        self.id = "11111111-1111-1111-1111-111111111111"
        self.email = "user@example.com"
        self.hashed_password = hash_password(password)
        self.is_active = is_active
        self.is_admin = False
        self.token_version = token_version


def _db_returning(user):
    """A MagicMock session whose `.query(...).filter(...).first()` yields `user`."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user
    return db


def _change(user, current, new, db=None):
    return change_user_password(
        user_id=getattr(user, "id", "missing"),
        current_password=current,
        new_password=new,
        db=db or _db_returning(user),
    )


def test_change_password_writes_new_hash():
    user = _StubUser()
    _change(user, _OLD, _NEW)
    assert verify_password(_NEW, user.hashed_password)
    assert not verify_password(_OLD, user.hashed_password)


def test_change_password_bumps_token_version_and_commits():
    user = _StubUser(token_version=4)
    db = _db_returning(user)
    _change(user, _OLD, _NEW, db=db)
    assert user.token_version == 5
    db.commit.assert_called_once()


def test_wrong_current_password_is_401_and_leaves_hash_untouched():
    user = _StubUser()
    original = user.hashed_password
    with pytest.raises(HTTPException) as exc:
        _change(user, "not-the-password", _NEW)
    assert exc.value.status_code == 401
    assert user.hashed_password == original
    assert user.token_version == 0


def test_short_new_password_is_400():
    user = _StubUser()
    with pytest.raises(HTTPException) as exc:
        _change(user, _OLD, "a" * (MIN_PASSWORD_LENGTH - 1))
    assert exc.value.status_code == 400


def test_reusing_current_password_is_400():
    user = _StubUser()
    with pytest.raises(HTTPException) as exc:
        _change(user, _OLD, _OLD)
    assert exc.value.status_code == 400


def test_disabled_account_is_403():
    user = _StubUser(is_active=False)
    with pytest.raises(HTTPException) as exc:
        _change(user, _OLD, _NEW)
    assert exc.value.status_code == 403


def test_missing_user_is_404():
    with pytest.raises(HTTPException) as exc:
        _change(None, _OLD, _NEW)
    assert exc.value.status_code == 404


def test_bump_token_version_wraps_at_smallint_ceiling():
    user = _StubUser(token_version=32766)
    assert bump_token_version(user) == 0


# ── route layer ─────────────────────────────────────────────────────────────

@pytest.fixture
def user_row(mock_db):
    from AINDY.db.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"pwchange-{suffix}@example.com",
        username=f"pwchange_{suffix}",
        hashed_password=hash_password(_OLD),
        is_active=True,
        is_admin=False,
        token_version=3,
    )
    mock_db.add(user)
    mock_db.commit()
    mock_db.refresh(user)
    return user


@pytest.fixture
def client_as(runtime_only_app, mock_db):
    """TestClient factory that authenticates as a given principal dict."""
    def _make(principal):
        runtime_only_app.dependency_overrides[get_current_user] = lambda: principal
        return TestClient(runtime_only_app, raise_server_exceptions=False)

    yield _make
    runtime_only_app.dependency_overrides.pop(get_current_user, None)


def _jwt_principal(user):
    return {
        "sub": str(user.id),
        "user_id": str(user.id),
        "email": user.email,
        "is_admin": False,
        "auth_type": "jwt",
    }


def test_route_change_password_succeeds_and_returns_fresh_token(
    client_as, mock_db, user_row
):
    with client_as(_jwt_principal(user_row)) as client:
        resp = client.post(
            "/auth/password/change",
            json={"current_password": _OLD, "new_password": _NEW},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    # Canonical envelope, same as /auth/login — the client unwraps `data`.
    assert body["data"]["token_type"] == "bearer"
    assert body["data"]["access_token"]

    mock_db.refresh(user_row)
    assert verify_password(_NEW, user_row.hashed_password)
    # Every JWT minted at the old version is now dead...
    assert user_row.token_version == 4
    # ...including the one the caller used; the returned token carries the new version.
    from AINDY.services.auth_service import decode_access_token
    assert decode_access_token(body["data"]["access_token"])["tv"] == 4


def test_route_rejects_wrong_current_password(client_as, mock_db, user_row):
    with client_as(_jwt_principal(user_row)) as client:
        resp = client.post(
            "/auth/password/change",
            json={"current_password": "wrong", "new_password": _NEW},
        )

    assert resp.status_code == 401
    mock_db.refresh(user_row)
    assert verify_password(_OLD, user_row.hashed_password)
    assert user_row.token_version == 3


def test_route_rejects_short_new_password(client_as, user_row):
    with client_as(_jwt_principal(user_row)) as client:
        resp = client.post(
            "/auth/password/change",
            json={
                "current_password": _OLD,
                "new_password": "a" * (MIN_PASSWORD_LENGTH - 1),
            },
        )
    assert resp.status_code == 400


def test_route_rejects_api_key_principal(client_as, mock_db, user_row):
    """A platform API key has no password to rotate — must not fall through."""
    principal = {"sub": str(user_row.id), "auth_type": "api_key", "api_key_scopes": []}
    with client_as(principal) as client:
        resp = client.post(
            "/auth/password/change",
            json={"current_password": _OLD, "new_password": _NEW},
        )

    assert resp.status_code == 401
    mock_db.refresh(user_row)
    assert verify_password(_OLD, user_row.hashed_password)


def test_route_requires_authentication(runtime_only_app):
    with TestClient(runtime_only_app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/auth/password/change",
            json={"current_password": _OLD, "new_password": _NEW},
        )
    assert resp.status_code in (401, 403)


def test_passwords_never_reach_the_trace_logged_input_payload():
    """`input_payload` is persisted on the ExecutionUnit — passwords must not go there."""
    import importlib
    import inspect
    import sys

    # `from AINDY.routes import auth_router` returns the APIRouter, not the module
    # (the routes-package namespace shadow — see CLAUDE.md).
    module = sys.modules.get("AINDY.routes.auth_router") or importlib.import_module(
        "AINDY.routes.auth_router"
    )
    src = inspect.getsource(module)
    change_src = src.split("def change_password(")[1].split("@router.post")[0]
    pipeline_call = change_src.split("return execute_with_pipeline_sync(")[1]
    assert "input_payload" not in pipeline_call
