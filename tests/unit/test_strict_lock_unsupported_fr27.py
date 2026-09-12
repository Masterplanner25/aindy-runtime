"""FR-27 — the advisory-lock helpers on the non-PostgreSQL path.

The strict-mode fix is PostgreSQL-only (advisory locks are a PG feature). On SQLite — the whole
unit suite — `acquire_effect_lock` must return `unsupported` and the gate must behave exactly as
before (degrade under contention, never block, never raise). The contention *outcome* is proven
on live PG in `tests/integration/test_soak_idempotency_strict_fr27.py`; this keeps the
non-PG branch and the flag readers covered where PG is not available.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.runtime_only


def test_acquire_returns_unsupported_on_sqlite(db_session):
    """db_session here is SQLite in the unit environment. No lock is taken, nothing raises."""
    from AINDY.kernel.effect_ledger import acquire_effect_lock

    bind = db_session.get_bind()
    if getattr(getattr(bind, "engine", bind), "dialect").name == "postgresql":
        pytest.skip("this asserts the non-PG branch; backend is PG")

    state, lock = acquire_effect_lock(db_session, "sys.v1.test.k", wait_seconds=5)
    assert state == "unsupported"
    assert lock is None


def test_lock_key_is_stable_and_signed_64_bit():
    from AINDY.kernel.effect_ledger import _lock_key

    a = _lock_key("sys.v1.test.one")
    assert a == _lock_key("sys.v1.test.one"), "same action_id must map to the same key"
    assert a != _lock_key("sys.v1.test.two")
    assert -(2**63) <= a < 2**63, "pg_advisory_lock takes a signed bigint"


def test_effect_lock_release_is_idempotent():
    """A double release (two close-sites firing, or a finally after an explicit release) must be
    a no-op, not a second unlock on a closed connection."""
    from AINDY.kernel.effect_ledger import EffectLock

    class _Conn:
        def __init__(self):
            self.execs = 0
            self.closed = 0

        def execute(self, *_a, **_k):
            self.execs += 1

        def close(self):
            self.closed += 1

    conn = _Conn()
    lock = EffectLock(conn, 123)
    lock.release()
    lock.release()
    assert conn.execs == 1, "unlock issued once"
    assert conn.closed == 1, "connection closed once"


def test_strict_flag_defaults_off_and_is_opt_in(monkeypatch):
    from AINDY.kernel import syscall_dispatcher as D

    monkeypatch.delenv("AINDY_SYSCALL_IDEMPOTENCY_STRICT", raising=False)
    assert D._syscall_idempotency_strict_enabled() is False
    for on in ("1", "true", "yes", "on", "ON", "True"):
        monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY_STRICT", on)
        assert D._syscall_idempotency_strict_enabled() is True
    for off in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY_STRICT", off)
        assert D._syscall_idempotency_strict_enabled() is False


def test_wait_seconds_defaults_to_the_handler_ceiling(monkeypatch):
    from AINDY.kernel import syscall_dispatcher as D

    monkeypatch.delenv("AINDY_SYSCALL_IDEMPOTENCY_STRICT_WAIT_SECONDS", raising=False)
    assert D._syscall_idempotency_strict_wait_seconds() == 300.0
    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY_STRICT_WAIT_SECONDS", "45")
    assert D._syscall_idempotency_strict_wait_seconds() == 45.0
    for bad in ("garbage", "-5"):
        monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY_STRICT_WAIT_SECONDS", bad)
        assert D._syscall_idempotency_strict_wait_seconds() == 300.0, "bad value falls back, never raises"
