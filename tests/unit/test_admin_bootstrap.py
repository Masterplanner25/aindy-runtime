"""
tests/unit/test_admin_bootstrap.py
────────────────────────────────────
Unit tests for AINDY_BOOTSTRAP_ADMIN_EMAIL (startup bootstrap) and
the `aindy-runtime auth promote-admin` CLI subcommand.

All DB interaction is mocked — no real Postgres required.
"""
from __future__ import annotations

import os
import subprocess
import sys
import sysconfig
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from AINDY.startup import _bootstrap_admin_email

# ── helpers ──────────────────────────────────────────────────────────────────

_EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""
AINDY_RUNTIME = Path(sysconfig.get_path("scripts")) / f"aindy-runtime{_EXE_SUFFIX}"

pytestmark = pytest.mark.runtime_only


def _make_user(*, is_admin: bool, email: str = "admin@example.com") -> MagicMock:
    u = MagicMock()
    u.email = email
    u.is_admin = is_admin
    return u


def _make_db(user=None):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user
    return db


# ── _bootstrap_admin_email ────────────────────────────────────────────────────


def test_bootstrap_no_op_when_env_unset(caplog):
    """No AINDY_BOOTSTRAP_ADMIN_EMAIL set → function returns immediately, no DB touch."""
    with patch("AINDY.startup.settings") as mock_settings:
        mock_settings.AINDY_BOOTSTRAP_ADMIN_EMAIL = None
        with patch("AINDY.startup.SessionLocal") as mock_sl:
            with caplog.at_level("INFO", logger="AINDY.main"):
                _bootstrap_admin_email()
            mock_sl.assert_not_called()


def test_bootstrap_no_op_when_user_not_found(caplog):
    """Email configured but user not registered yet → INFO log, no commit."""
    db = _make_db(user=None)
    with patch("AINDY.startup.settings") as mock_settings, \
         patch("AINDY.startup.SessionLocal", return_value=db), \
         patch("AINDY.db.models.user.User"):
        mock_settings.AINDY_BOOTSTRAP_ADMIN_EMAIL = "notyet@example.com"
        with caplog.at_level("INFO", logger="AINDY.main"):
            _bootstrap_admin_email()
        db.commit.assert_not_called()
        assert any("no matching user" in r.message for r in caplog.records)


def test_bootstrap_grants_admin_when_not_admin(caplog):
    """User exists but is not admin → is_admin set to True, commit called."""
    user = _make_user(is_admin=False, email="promote@example.com")
    db = _make_db(user=user)
    with patch("AINDY.startup.settings") as mock_settings, \
         patch("AINDY.startup.SessionLocal", return_value=db), \
         patch("AINDY.db.models.user.User"):
        mock_settings.AINDY_BOOTSTRAP_ADMIN_EMAIL = "promote@example.com"
        _bootstrap_admin_email()
    assert user.is_admin is True
    db.commit.assert_called_once()


def test_bootstrap_idempotent_when_already_admin(caplog):
    """User is already admin → no commit, no change."""
    user = _make_user(is_admin=True, email="already@example.com")
    db = _make_db(user=user)
    with patch("AINDY.startup.settings") as mock_settings, \
         patch("AINDY.startup.SessionLocal", return_value=db), \
         patch("AINDY.db.models.user.User"):
        mock_settings.AINDY_BOOTSTRAP_ADMIN_EMAIL = "already@example.com"
        with caplog.at_level("INFO", logger="AINDY.main"):
            _bootstrap_admin_email()
    db.commit.assert_not_called()
    assert any("already admin" in r.message for r in caplog.records)


def test_bootstrap_does_not_demote(caplog):
    """Unsetting the var (None) never touches existing admins."""
    user = _make_user(is_admin=True, email="keep@example.com")
    db = _make_db(user=user)
    with patch("AINDY.startup.settings") as mock_settings, \
         patch("AINDY.startup.SessionLocal", return_value=db):
        mock_settings.AINDY_BOOTSTRAP_ADMIN_EMAIL = None
        _bootstrap_admin_email()
    # DB not even opened
    db.commit.assert_not_called()
    assert user.is_admin is True


# ── CLI subcommand: aindy-runtime auth promote-admin ─────────────────────────


def _run_cli(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    base_env = {k: v for k, v in os.environ.items() if k in {"PATH", "SystemRoot", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE"}}
    if env:
        base_env.update(env)
    return subprocess.run(
        [str(AINDY_RUNTIME), *args],
        env=base_env,
        capture_output=True,
        text=True,
    )


def test_cli_auth_promote_admin_in_help():
    """auth promote-admin appears in the top-level help output."""
    result = _run_cli("--help")
    assert result.returncode == 0
    assert "auth" in result.stdout


def test_cli_auth_promote_admin_subcommand_help():
    """auth promote-admin --help exits 0 and describes the command."""
    result = _run_cli("auth", "promote-admin", "--help")
    assert result.returncode == 0
    assert "promote-admin" in result.stdout or "email" in result.stdout.lower()


def test_cli_auth_promote_admin_requires_database_url():
    """Without DATABASE_URL, promote-admin exits non-zero with a clear error."""
    result = _run_cli("auth", "promote-admin", "user@example.com")
    assert result.returncode != 0
    assert "DATABASE_URL" in result.stderr or "DATABASE_URL" in result.stdout


def test_cli_auth_no_subcommand_prints_help():
    """auth with no subcommand exits 0 and prints help."""
    result = _run_cli("auth")
    assert result.returncode == 0
