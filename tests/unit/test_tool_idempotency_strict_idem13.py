"""IDEM-13 — strict at-most-once on the TOOL seam (`AINDY_TOOL_IDEMPOTENCY_STRICT`).

FR-27 built the advisory lock and wired it into `syscall_dispatcher` only. `execute_tool` — the
path a first-party consumer's `EXACTLY_ONCE` tool actually lands on — never acquired it, so under
contention every concurrent caller executed (measured on a real channel 2026-09-23: 5 concurrent
sends of one key → 5 delivered messages against 1 ledger row, `degraded` ×20).

These cover the WIRING, on the same isolation the MEB-0 suite uses (`test_tool_idempotency.py`):
the lock is asked for only when the flag is on, it is RELEASED on every exit — success, failure,
and the replay early-return, which returns before the `finally` — and a timeout degrades and is
COUNTED rather than blocking the tool. The contention OUTCOME on live PostgreSQL belongs beside
FR-27's soak (`tests/integration/test_soak_idempotency_strict_fr27.py`), which already covers the
advisory-lock primitive itself.
"""
from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only

from AINDY.agents import tool_registry as tr


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(tr.TOOL_REGISTRY)
    yield
    tr.TOOL_REGISTRY.clear()
    tr.TOOL_REGISTRY.update(saved)


def _register(name, runs, *, boom=False):
    def _fn(args, user_id, db):
        runs.append(args)
        if boom:
            raise RuntimeError("tool asked to fail")
        return {"echoed": args}

    tr.register_tool(
        name=name, risk="low", description="t", capability="c", required_capability="c",
        category="test", egress_scope="none", execution_guarantee="EXACTLY_ONCE",
    )(_fn)


@contextlib.contextmanager
def _mocks(lock_return=("acquired", None), resolve_return=(False, None), lock_side_effect=None):
    """Everything below the gate is stubbed; the LOCK call is the subject."""
    kw = {"side_effect": lock_side_effect} if lock_side_effect else {"return_value": lock_return}
    with patch.object(tr, "_ensure_tools_loaded", lambda: None), patch(
        "AINDY.agents.capability_service.check_tool_capability",
        return_value={"ok": True, "allowed_capabilities": [], "granted_tools": []},
    ), patch.object(tr, "queue_system_event", lambda **k: None), patch(
        "AINDY.platform_layer.secret_broker.capability_scope",
        lambda caps: contextlib.nullcontext(),
    ), patch(
        "AINDY.core.execution_gate.compute_action_id", return_value="AID-IDEM13"
    ), patch(
        "AINDY.kernel.effect_ledger.resolve_effect_record", return_value=resolve_return
    ), patch(
        "AINDY.kernel.effect_ledger.complete_effect_record"
    ), patch(
        "AINDY.agents.tool_registry._tool_idempotency_enabled", return_value=True
    ), patch(
        "AINDY.kernel.effect_ledger.acquire_effect_lock", **kw
    ) as m_lock:
        yield m_lock


def _run(name):
    return tr.execute_tool(name, {"x": 1}, "user-1", MagicMock(),
                           run_id="run-idem13", execution_token={"t": 1})


# ── the flag readers ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [("1", True), ("true", True), ("ON", True),
                                          ("", False), ("0", False), ("no", False)])
def test_the_flag_is_off_unless_explicitly_set(monkeypatch, raw, expected):
    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY_STRICT", raw)
    assert tr._tool_idempotency_strict_enabled() is expected


def test_the_flag_is_read_at_call_time_not_import(monkeypatch):
    """An import-time env read is invisible to behavioural tests — the standing rule."""
    monkeypatch.delenv("AINDY_TOOL_IDEMPOTENCY_STRICT", raising=False)
    assert tr._tool_idempotency_strict_enabled() is False
    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY_STRICT", "1")
    assert tr._tool_idempotency_strict_enabled() is True


def test_the_wait_ceiling_defaults_to_60_and_rejects_nonsense(monkeypatch):
    """Lower than the syscall path's 300s on purpose: a caller blocked behind a wedged winner is
    worse on a tool than an extra delivery."""
    monkeypatch.delenv("AINDY_TOOL_IDEMPOTENCY_STRICT_WAIT_SECONDS", raising=False)
    assert tr._tool_idempotency_strict_wait_seconds() == 60.0
    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY_STRICT_WAIT_SECONDS", "5.5")
    assert tr._tool_idempotency_strict_wait_seconds() == 5.5
    for bad in ("banana", "-1"):
        monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY_STRICT_WAIT_SECONDS", bad)
        assert tr._tool_idempotency_strict_wait_seconds() == 60.0


# ── the wiring ───────────────────────────────────────────────────────────────────────────

def test_no_lock_is_taken_when_the_flag_is_off(monkeypatch):
    monkeypatch.delenv("AINDY_TOOL_IDEMPOTENCY_STRICT", raising=False)
    runs = []
    _register("t_off", runs)
    with _mocks() as m_lock:
        assert _run("t_off")["success"] is True
    m_lock.assert_not_called()
    assert runs, "the tool still runs; only the lock is absent"


def test_the_lock_is_taken_with_the_wait_ceiling_and_released_on_success(monkeypatch):
    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY_STRICT", "1")
    lock = MagicMock(name="EffectLock")
    runs = []
    _register("t_ok", runs)
    with _mocks(lock_return=("acquired", lock)) as m_lock:
        assert _run("t_ok")["success"] is True
    m_lock.assert_called_once()
    assert m_lock.call_args.kwargs["wait_seconds"] == 60.0
    lock.release.assert_called_once()


def test_the_lock_is_released_when_the_tool_raises(monkeypatch):
    """A lock held past a failure wedges every later caller of that action_id."""
    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY_STRICT", "1")
    lock = MagicMock()
    runs = []
    _register("t_boom", runs, boom=True)
    with _mocks(lock_return=("acquired", lock)):
        assert _run("t_boom")["success"] is False
    lock.release.assert_called_once()


def test_the_lock_is_released_on_the_replay_early_return(monkeypatch):
    """That return happens BEFORE the finally — it has to release for itself."""
    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY_STRICT", "1")
    lock = MagicMock()
    runs = []
    _register("t_replay", runs)
    with _mocks(lock_return=("acquired", lock),
                resolve_return=(True, {"result": {"cached": True}})):
        out = _run("t_replay")
    assert out["idempotent_replay"] is True and out["result"] == {"cached": True}
    assert runs == [], "a replay must not run the tool"
    lock.release.assert_called_once()


def test_a_timeout_degrades_and_is_counted(monkeypatch):
    from AINDY.platform_layer.metrics import REGISTRY

    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY_STRICT", "1")
    runs = []
    _register("t_timeout", runs)
    before = REGISTRY.get_sample_value("aindy_effect_gate_outcomes_total",
                                       {"outcome": "degraded_lock_timeout"}) or 0.0
    with _mocks(lock_return=("timeout", None)):
        out = _run("t_timeout")
    after = REGISTRY.get_sample_value("aindy_effect_gate_outcomes_total",
                                      {"outcome": "degraded_lock_timeout"}) or 0.0
    assert out["success"] is True, "a timeout degrades to AT_LEAST_ONCE; it never blocks the tool"
    assert runs, "the tool must still run after a timed-out wait"
    assert after == before + 1, "an operator must be able to SEE the degrade"


def test_a_lock_failure_degrades_instead_of_raising(monkeypatch):
    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY_STRICT", "1")
    runs = []
    _register("t_lockfail", runs)
    with _mocks(lock_side_effect=RuntimeError("no connection")):
        out = _run("t_lockfail")
    assert out["success"] is True and runs, "a ledger failure never blocks the effect path"


def test_unsupported_backend_behaves_exactly_as_before(monkeypatch):
    """SQLite has no advisory locks: `unsupported`, no lock object, ordinary execution."""
    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY_STRICT", "1")
    runs = []
    _register("t_unsupported", runs)
    with _mocks(lock_return=("unsupported", None)) as m_lock:
        assert _run("t_unsupported")["success"] is True
    m_lock.assert_called_once()
    assert runs


def test_the_real_helper_reports_unsupported_on_this_suites_backend(db_session):
    """Liveness control: every test above patches the lock, so prove the real one is reachable."""
    from AINDY.kernel.effect_ledger import acquire_effect_lock

    bind = db_session.get_bind()
    if getattr(getattr(bind, "engine", bind), "dialect").name == "postgresql":
        pytest.skip("this asserts the non-PG branch; backend is PG")
    assert acquire_effect_lock(db_session, "idem13-probe-action") == ("unsupported", None)
