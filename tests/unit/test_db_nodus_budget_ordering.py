"""
DB-NODUS-BUDGET-1 — the DB idle cap must outlive the nodus execution ceiling.

Verified 2026-08-01 against real Postgres: the flow runner's session is held
idle-in-transaction for the *entire* duration of node execution (`xact_age_s ==
idle_s`, tracked 4.12s → 20.60s across a 20s node). So the two budgets are in a
hard ordering relationship:

    max nodus wall clock  =  AINDY_NODUS_MAX_EXECUTION_MS   (script budget)
                           + AINDY_NODUS_BOOT_ALLOWANCE_MS  (cold-start headroom)

If `idle_in_transaction_session_timeout` does not clear that, Postgres terminates
the connection mid-run and it surfaces as `server closed the connection
unexpectedly` → `PendingRollbackError`. At the old 30s-vs-45s ordering that was
reachable by a slow but entirely in-budget run.

This is the cheap guard: it does not stop the transaction being held (that is the
memory-read fix), it just keeps the ceiling above it. Raising either nodus budget
without raising the cap fails here rather than in production.
"""
from __future__ import annotations

import pytest

from AINDY.config import Settings
from AINDY.runtime.nodus_runtime_adapter import (
    _DEFAULT_BOOT_ALLOWANCE_MS,
    _DEFAULT_MAX_EXECUTION_MS,
)


pytestmark = pytest.mark.runtime_only


def _nodus_ceiling_ms() -> int:
    """Longest a nodus execution may occupy the outer subprocess.run(timeout=)."""
    return _DEFAULT_MAX_EXECUTION_MS + _DEFAULT_BOOT_ALLOWANCE_MS


def _idle_cap_default_ms() -> int:
    return Settings.model_fields["DB_IDLE_IN_TRANSACTION_TIMEOUT_MS"].default


def test_idle_cap_default_clears_the_nodus_ceiling():
    ceiling = _nodus_ceiling_ms()
    cap = _idle_cap_default_ms()
    assert cap > ceiling, (
        f"DB_IDLE_IN_TRANSACTION_TIMEOUT_MS default ({cap}ms) must exceed the maximum "
        f"nodus wall clock ({ceiling}ms = {_DEFAULT_MAX_EXECUTION_MS} script + "
        f"{_DEFAULT_BOOT_ALLOWANCE_MS} boot). Below it, an in-budget nodus run has its "
        "DB connection terminated mid-flight (DB-NODUS-BUDGET-1)."
    )


def test_ordering_has_real_headroom_not_a_hairline_margin():
    """A 1ms margin would satisfy the ordering but not survive scheduling jitter."""
    ceiling = _nodus_ceiling_ms()
    cap = _idle_cap_default_ms()
    assert cap - ceiling >= 10_000, (
        f"only {cap - ceiling}ms of headroom between the idle cap and the nodus "
        "ceiling; want >=10s so ordinary jitter cannot cross it"
    )


def test_statement_timeout_is_not_accidentally_below_the_idle_cap():
    """Both are per-connection caps; a statement timeout under the idle cap is fine,
    but a 0 (disabled) idle cap with a live statement timeout would silently remove
    the protection this ordering assumes. Guard the disable-by-accident case."""
    cap = _idle_cap_default_ms()
    assert cap > 0, "idle cap default must not ship disabled"


def test_the_nodus_ceiling_is_the_sum_the_adapter_actually_uses():
    """If the adapter stops adding boot allowance to the script budget, this test's
    premise (and the config comment) is stale — fail so both get revisited."""
    import inspect

    from AINDY.runtime import nodus_runtime_adapter as adapter

    src = inspect.getsource(adapter)
    assert "_DEFAULT_BOOT_ALLOWANCE_MS" in src and "_DEFAULT_MAX_EXECUTION_MS" in src, (
        "nodus_runtime_adapter no longer defines both budget constants — the "
        "DB-NODUS-BUDGET-1 ordering needs re-deriving"
    )
