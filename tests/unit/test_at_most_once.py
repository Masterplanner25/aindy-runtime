"""EFFECT-OUTCOME-UNKNOWN-1 phase 2 — `AT_MOST_ONCE` (DEC-089).

`EXACTLY_ONCE` deliberately degrades to at-least-once when it cannot hold the slot (IDEM-11): a live
concurrent `pending`, a lock timeout, or a failed gate all let the call run. An at-most-once effect
cannot. So `AT_MOST_ONCE` engages the same gate and differs in exactly the places `EXACTLY_ONCE`
degrades: it always takes the strict lock (whatever the STRICT flags say), engages even with the
master idempotency flag off, and REFUSES (`failure_class: "transient"`, nothing dispatched)
wherever `EXACTLY_ONCE` would run anyway. Every refusal test below has an `EXACTLY_ONCE` control
under the same conditions that runs, which is the difference being pinned.

Real ledger on a private SQLite engine, real dispatcher, real tool seam. SQLite has no advisory
lock (`acquire_effect_lock` -> "unsupported"), so the lock-timeout cases patch the lock's answer;
the real Postgres lock is exercised by `tests/integration/test_soak_at_most_once.py`.
"""
from __future__ import annotations

import contextlib
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from tests.fixtures.db import build_private_engine

pytestmark = pytest.mark.runtime_only

EU = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _restore_syscall_registry():
    """Probe syscalls registered here must not leak into later tests (a census over the global
    registry would read them; #791 CI caught exactly that)."""
    from AINDY.kernel import syscall_registry as R

    before = set(R.SYSCALL_REGISTRY.keys())
    try:
        yield
    finally:
        # Pop only what this test added (the registry guards duplicate writes on __setitem__,
        # so a clear-and-refill restore is not safe; popping is the pattern used elsewhere).
        for name in set(R.SYSCALL_REGISTRY.keys()) - before:
            R.SYSCALL_REGISTRY.pop(name, None)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    engine = build_private_engine(tmp_path / "amo.db")
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", factory)
    monkeypatch.delenv("AINDY_SYSCALL_IDEMPOTENCY_STRICT", raising=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _metric(name, labels=None):
    from AINDY.platform_layer.metrics import REGISTRY

    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


# ── vocabulary ─────────────────────────────────────────────────────────────────────────────


def test_both_registries_accept_it_and_refuse_a_typo():
    from AINDY.agents import tool_registry as tr
    from AINDY.kernel import syscall_registry as R

    name = f"sys.v1.test.amo_reg_{uuid.uuid4().hex[:6]}"
    R.register_syscall(name, lambda p, c: {}, capability="t", execution_guarantee="at_most_once")
    assert R.SYSCALL_REGISTRY[name].execution_guarantee == "AT_MOST_ONCE"
    with pytest.raises(ValueError):
        R.register_syscall(name + "x", lambda p, c: {}, capability="t", execution_guarantee="AT MOST ONCE")

    tname = f"amo_tool_{uuid.uuid4().hex[:6]}"
    try:
        tr.register_tool(name=tname, risk="low", description="t", capability="c", required_capability="c",
                         category="test", egress_scope="none", execution_guarantee="AT_MOST_ONCE")(lambda **k: {})
        assert tr.TOOL_REGISTRY[tname]["execution_guarantee"] == "AT_MOST_ONCE"
        with pytest.raises(ValueError):  # register_tool used to accept any string, i.e. "no gate"
            tr.register_tool(name=tname + "x", risk="low", description="t", capability="c",
                             required_capability="c", category="test", egress_scope="none",
                             execution_guarantee="EXACTLY ONCE")
    finally:
        tr.TOOL_REGISTRY.pop(tname, None)


# ── the syscall seam ───────────────────────────────────────────────────────────────────────


class _OkRm:
    def check_quota(self, _x):
        return True, None

    def record_usage(self, _x, _u):
        return None


def _probe(guarantee, behaviour=None):
    from AINDY.kernel import syscall_registry as R

    name = f"sys.v1.test.amo_{uuid.uuid4().hex[:8]}"
    runs: list[int] = []

    def handler(payload, ctx):
        runs.append(1)
        return (behaviour or (lambda n: {"n": n}))(len(runs))

    R.SYSCALL_REGISTRY[name] = R.SyscallEntry(handler=handler, capability="test.amo", execution_guarantee=guarantee)
    return name, runs


def _dispatch(name, payload=None):
    from AINDY.kernel import syscall_dispatcher as D
    from AINDY.kernel import syscall_registry as R

    d = D.SyscallDispatcher()
    d._emit_syscall_event = lambda *a, **k: None
    ctx = R.SyscallContext(execution_unit_id=EU, user_id="u-1", capabilities=["test.amo"], trace_id="t")
    with patch.object(D, "_get_rm", lambda: _OkRm()):
        return d.dispatch(name, payload or {"p": 1}, ctx)


@pytest.mark.parametrize("guarantee, expected_runs", [("AT_MOST_ONCE", [1]), ("EXACTLY_ONCE", [1, 1])])
def test_with_the_master_flag_off_only_at_most_once_is_still_gated(ledger, monkeypatch, guarantee, expected_runs):
    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "false")
    name, runs = _probe(guarantee)
    _dispatch(name)
    _dispatch(name)
    assert runs == expected_runs, f"{guarantee} with the master flag off"


@pytest.mark.parametrize("guarantee", ["AT_MOST_ONCE", "EXACTLY_ONCE"])
def test_a_live_concurrent_call_refuses_at_most_once_and_degrades_exactly_once(ledger, monkeypatch, guarantee):
    """The ledger answers "a live concurrent call holds the slot" as `(False, DEGRADED)`.
    ★ Driven by that answer directly: on SQLite a planted `pending` row makes
    `_resolve_existing_row` raise (naive vs aware `created_at`), which exercised the FAILED-GATE
    refusal instead and let this branch go untested (caught by mutation). The ledger's own
    `DEGRADED` return is pinned in `test_idempotency_gate.py`; the real lock in the soak."""
    from AINDY.kernel import syscall_dispatcher as D
    from AINDY.kernel.effect_ledger import DEGRADED

    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "true")
    monkeypatch.setattr(D, "_resolve_effect_record", lambda *a, **k: (False, DEGRADED))
    name, runs = _probe(guarantee)
    env = _dispatch(name)
    if guarantee == "AT_MOST_ONCE":
        assert runs == [] and env.get("failure_class") == "transient", env
        assert "concurrent call" in str(env["error"]), "refused, but not by the degrade branch"
    else:
        assert runs == [1], "control: EXACTLY_ONCE degrades and runs alongside the live call (IDEM-11)"


@pytest.mark.parametrize("guarantee", ["AT_MOST_ONCE", "EXACTLY_ONCE"])
def test_a_failed_gate_refuses_at_most_once_and_runs_exactly_once(ledger, monkeypatch, guarantee):
    from AINDY.kernel import syscall_dispatcher as D

    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "true")
    monkeypatch.setattr(D, "_resolve_effect_record", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    name, runs = _probe(guarantee)
    env = _dispatch(name)
    if guarantee == "AT_MOST_ONCE":
        assert runs == [] and env.get("failure_class") == "transient", env
    else:
        assert runs == [1], "control: EXACTLY_ONCE runs unguarded when the gate fails"


@pytest.mark.parametrize("guarantee", ["AT_MOST_ONCE", "EXACTLY_ONCE"])
def test_a_lock_timeout_refuses_at_most_once_and_degrades_exactly_once(ledger, monkeypatch, guarantee):
    import AINDY.kernel.effect_ledger as L

    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "true")
    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY_STRICT", "true")  # so EXACTLY_ONCE asks for the lock too
    asked: list[str] = []
    monkeypatch.setattr(L, "acquire_effect_lock", lambda db, aid, **k: asked.append(aid) or ("timeout", None))
    name, runs = _probe(guarantee)
    env = _dispatch(name)
    assert asked, "the strict lock was never requested"
    if guarantee == "AT_MOST_ONCE":
        assert runs == [] and env.get("failure_class") == "transient", env
    else:
        assert runs == [1], "control: EXACTLY_ONCE degrades on a lock timeout"


def test_at_most_once_asks_for_the_lock_even_with_strict_off(ledger, monkeypatch):
    import AINDY.kernel.effect_ledger as L

    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "true")
    asked: list[str] = []
    monkeypatch.setattr(L, "acquire_effect_lock", lambda db, aid, **k: asked.append(aid) or ("unsupported", None))
    for guarantee, expect in (("AT_MOST_ONCE", 1), ("EXACTLY_ONCE", 0)):
        asked.clear()
        name, _runs = _probe(guarantee)
        _dispatch(name)
        assert len(asked) == expect, (guarantee, asked)


@pytest.mark.parametrize("guarantee, counted", [("EXACTLY_ONCE", 1.0), ("AT_MOST_ONCE", 0.0)])
def test_an_unknown_is_a_shortfall_only_under_exactly_once(ledger, monkeypatch, guarantee, counted):
    from AINDY.kernel.syscall_outcome import EffectOutcomeUnknown

    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "true")
    labels = {"guarantee": "EXACTLY_ONCE", "seam": "syscall"}
    before = _metric("aindy_effect_contract_shortfall_total", labels)

    def _raise(_n):
        raise EffectOutcomeUnknown("read timeout")

    name, _runs = _probe(guarantee, _raise)
    assert _dispatch(name)["status"] == "unknown"
    assert _metric("aindy_effect_contract_shortfall_total", labels) - before == counted


# ── the tool seam ──────────────────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _tool_env():
    from AINDY.agents import tool_registry as tr

    with patch.object(tr, "_ensure_tools_loaded", lambda: None), patch(
        "AINDY.agents.capability_service.check_tool_capability",
        return_value={"ok": True, "allowed_capabilities": [], "granted_tools": []},
    ), patch.object(tr, "queue_system_event", lambda **k: None), patch(
        "AINDY.platform_layer.secret_broker.capability_scope", lambda caps: contextlib.nullcontext(),
    ):
        yield tr


def _tool_call(tr, ledger, guarantee):
    name = f"amo_tool_{uuid.uuid4().hex[:8]}"
    runs: list[int] = []
    tr.register_tool(name=name, risk="low", description="t", capability="c", required_capability="c",
                     category="test", egress_scope="none", execution_guarantee=guarantee)(
        lambda args, user_id, db: runs.append(1) or {"ok": True})
    db = ledger()
    try:
        out = [tr.execute_tool(name, {"x": 1}, "u-1", db, run_id=f"run_{EU}", execution_token={"t": 1}) for _ in (1, 2)]
    finally:
        db.close()
        tr.TOOL_REGISTRY.pop(name, None)
    return out, runs


@pytest.mark.parametrize("guarantee, expected_runs", [("AT_MOST_ONCE", [1]), ("EXACTLY_ONCE", [1, 1])])
def test_tool_seam_with_the_flag_off_only_at_most_once_is_gated(ledger, monkeypatch, guarantee, expected_runs):
    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY", "false")
    with _tool_env() as tr:
        out, runs = _tool_call(tr, ledger, guarantee)
    assert runs == expected_runs
    if guarantee == "AT_MOST_ONCE":
        assert out[1].get("idempotent_replay") is True


@pytest.mark.parametrize("guarantee", ["AT_MOST_ONCE", "EXACTLY_ONCE"])
def test_tool_seam_lock_timeout_refuses_at_most_once(ledger, monkeypatch, guarantee):
    import AINDY.kernel.effect_ledger as L

    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY", "true")
    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY_STRICT", "true")
    monkeypatch.setattr(L, "acquire_effect_lock", lambda db, aid, **k: ("timeout", None))
    with _tool_env() as tr:
        out, runs = _tool_call(tr, ledger, guarantee)
    if guarantee == "AT_MOST_ONCE":
        assert runs == [] and out[0]["failure_class"] == "transient" and "not dispatched" in out[0]["error"]
    else:
        # control: EXACTLY_ONCE PROCEEDS without the lock (the degrade) and succeeds; the ledger still
        # dedups the sequential second call, which replays. AT_MOST_ONCE refused even the first.
        assert runs == [1] and out[0]["success"] is True and out[1].get("idempotent_replay") is True
