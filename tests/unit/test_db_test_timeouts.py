"""
Test-mode Postgres timeouts are overridable (and default to 10s).

`AINDY/db/database.py` caps `statement_timeout` and
`idle_in_transaction_session_timeout` at 10s whenever `settings.is_testing`. That
guard is correct — it makes a hung query or a transaction held open across a slow
call fail fast instead of wedging the suite — but it used to be *hardcoded*, so
`DB_STATEMENT_TIMEOUT_MS` / `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` were silently
ignored in exactly the environment that needed to raise them.

The integration tier legitimately exceeds 10s: it holds a session open across a
nodus worker's one-time plugin load, and Postgres then terminates the backend
mid-run (`server closed the connection unexpectedly` → `PendingRollbackError`).

Two properties matter and are asserted here:
  * the 10s default still applies when nothing is set — the guard cannot go missing;
  * an explicitly set env var wins — so a tier can raise it deliberately.

`settings.DB_*_TIMEOUT_MS` is deliberately not consulted: its 30s default would
silently triple the cap for every test run.
"""
from __future__ import annotations

import pytest

from AINDY.db.database import _TEST_TIMEOUT_DEFAULT_MS, _test_timeout_ms


pytestmark = pytest.mark.runtime_only

_VAR = "DB_IDLE_IN_TRANSACTION_TIMEOUT_MS"


def test_default_is_ten_seconds_when_unset(monkeypatch):
    monkeypatch.delenv(_VAR, raising=False)
    assert _test_timeout_ms(_VAR) == 10000
    assert _TEST_TIMEOUT_DEFAULT_MS == 10000


def test_blank_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(_VAR, "   ")
    assert _test_timeout_ms(_VAR) == _TEST_TIMEOUT_DEFAULT_MS


def test_explicit_value_wins(monkeypatch):
    monkeypatch.setenv(_VAR, "60000")
    assert _test_timeout_ms(_VAR) == 60000


def test_zero_is_honoured_as_disable(monkeypatch):
    """Postgres reads 0 as 'no timeout' — an explicit opt-out must pass through."""
    monkeypatch.setenv(_VAR, "0")
    assert _test_timeout_ms(_VAR) == 0


def test_garbage_falls_back_to_default_rather_than_crashing(monkeypatch):
    """A malformed value must not take the engine down at import time."""
    monkeypatch.setenv(_VAR, "not-a-number")
    assert _test_timeout_ms(_VAR) == _TEST_TIMEOUT_DEFAULT_MS


def test_negative_is_clamped(monkeypatch):
    monkeypatch.setenv(_VAR, "-5000")
    assert _test_timeout_ms(_VAR) == 0


def test_statement_timeout_resolves_independently(monkeypatch):
    """The two knobs must not read each other's variable."""
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", "20000")
    monkeypatch.delenv(_VAR, raising=False)
    assert _test_timeout_ms("DB_STATEMENT_TIMEOUT_MS") == 20000
    assert _test_timeout_ms(_VAR) == _TEST_TIMEOUT_DEFAULT_MS
