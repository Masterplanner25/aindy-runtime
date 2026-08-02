"""
`register_user` enforces MIN_PASSWORD_LENGTH (FR-6 sub-item, 2026-08-01).

Until now the floor guarded `change_user_password` only, so of the paths that can set a
password, registration — the one an unauthenticated caller reaches — was the unguarded
one. A floor applied to one path is not a floor.

Deliberately breaking-ish and shipped anyway: security tightening on a published package.
What it does NOT do is invalidate stored passwords — login is untouched, and only new
registrations under the length are rejected. The blast radius is a downstream registration
form that previously permitted shorter passwords.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from unittest.mock import MagicMock

from AINDY.services.auth_service import (
    MIN_PASSWORD_LENGTH,
    register_user,
    verify_password,
)


pytestmark = pytest.mark.runtime_only


def _db_with_no_existing_user():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    return db


def _register(password, db=None):
    return register_user(
        email="new@example.com",
        password=password,
        username="newuser",
        db=db if db is not None else _db_with_no_existing_user(),
    )


# ── rejection ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("length", [0, 1, MIN_PASSWORD_LENGTH - 1])
def test_short_password_is_rejected_with_400(length):
    with pytest.raises(HTTPException) as exc:
        _register("a" * length)
    assert exc.value.status_code == 400
    assert str(MIN_PASSWORD_LENGTH) in exc.value.detail


def test_rejection_happens_before_any_database_work():
    """The length check needs no round-trip; a doomed request shouldn't cost a query."""
    db = _db_with_no_existing_user()
    with pytest.raises(HTTPException):
        _register("short", db=db)
    db.query.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_rejected_registration_creates_no_user():
    db = _db_with_no_existing_user()
    with pytest.raises(HTTPException):
        _register("short", db=db)
    db.add.assert_not_called()


# ── acceptance ──────────────────────────────────────────────────────────────

def test_password_at_exactly_the_minimum_is_accepted():
    """Boundary: the floor is inclusive — exactly MIN_PASSWORD_LENGTH must pass."""
    db = _db_with_no_existing_user()
    _register("a" * MIN_PASSWORD_LENGTH, db=db)
    db.add.assert_called_once()
    db.commit.assert_called_once()


def test_accepted_password_is_hashed_not_stored_raw():
    db = _db_with_no_existing_user()
    _register("battery-staple-9", db=db)
    created = db.add.call_args[0][0]
    assert created.hashed_password != "battery-staple-9"
    assert verify_password("battery-staple-9", created.hashed_password)


# ── ordering against the existing-email check ───────────────────────────────

def test_short_password_wins_over_duplicate_email():
    """Both conditions true -> 400, not 409. The cheap check runs first, and it also
    avoids confirming an email is registered to a caller who sent an invalid password."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = object()  # email taken
    with pytest.raises(HTTPException) as exc:
        _register("short", db=db)
    assert exc.value.status_code == 400


def test_duplicate_email_still_409_when_password_is_valid():
    """The pre-existing 409 behaviour must survive the new check."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = object()
    with pytest.raises(HTTPException) as exc:
        _register("a" * MIN_PASSWORD_LENGTH, db=db)
    assert exc.value.status_code == 409


# ── the floor is shared, not duplicated ─────────────────────────────────────

def test_register_and_change_share_one_constant():
    """If these ever diverge, one path silently becomes the weak one."""
    import inspect

    from AINDY.services import auth_service

    src = inspect.getsource(auth_service.register_user)
    assert "MIN_PASSWORD_LENGTH" in src, (
        "register_user must reference the shared constant, not a literal"
    )
