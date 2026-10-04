"""EFFECT-OUTCOME-UNKNOWN-1 phase 1 — never re-run an ambiguous effect (DEC-085, 086, 087, 090).

Scoping found that a replay RE-RAN an `unknown` or `partial` effect: `_resolve_existing_row`
reclaimed every status except `success` and a live `pending`. So the first honest `unknown` would
have been executed twice. These tests run the REAL ledger on a private SQLite engine (no shared
outer transaction, so commits are real) through the REAL dispatcher and the REAL tool seam. Only
the quota manager, capability check and event emission are stubbed.

What is pinned:
* a replay of an `unknown` / `partial` row HOLDS it: the handler runs once, and the second call
  gets the recorded outcome back (`held`, `reconcile_required` for unknown), never a success;
* the control: a `failed` row is still reclaimed and re-run (that path is unchanged);
* a handler or tool that RAISES `EffectOutcomeUnknown` is recorded `unknown`, not `failed`;
* `failure_class: "unknown"` is declared on the envelope and no retry classifier retries it;
* an isolated worker carries an exception's declared class back to the parent;
* the TTL job never reaps an `unknown` row, reaps a `success` beside it, and sets the gauge.
"""
from __future__ import annotations

import contextlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

from tests.fixtures.db import build_private_engine

pytestmark = pytest.mark.runtime_only

EU = str(uuid.uuid4())


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """SessionLocal bound to a private engine, so the gate's own sessions commit for real."""
    engine = build_private_engine(tmp_path / "effects.db")
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", factory)
    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "true")
    try:
        yield factory
    finally:
        engine.dispose()


def _row(factory, action_id=None):
    from AINDY.db.models.effect_record import EffectRecord

    s = factory()
    try:
        q = s.query(EffectRecord)
        if action_id:
            q = q.filter(EffectRecord.action_id == action_id)
        return [(r.status, r.result_payload) for r in q.all()]
    finally:
        s.close()


# ── the syscall seam ───────────────────────────────────────────────────────────────────────


class _OkRm:
    def check_quota(self, _x):
        return True, None

    def record_usage(self, _x, _u):
        return None


def _probe(behaviour):
    from AINDY.kernel import syscall_registry as R

    name = f"sys.v1.test.eou_{uuid.uuid4().hex[:8]}"
    runs: list[int] = []

    def handler(payload, ctx):
        runs.append(1)
        return behaviour(len(runs))

    R.SYSCALL_REGISTRY[name] = R.SyscallEntry(handler=handler, capability="test.eou",
                                              execution_guarantee="EXACTLY_ONCE")
    return name, runs


def _dispatch(name, payload=None):
    from AINDY.kernel import syscall_dispatcher as D
    from AINDY.kernel import syscall_registry as R

    d = D.SyscallDispatcher()
    d._emit_syscall_event = lambda *a, **k: None
    ctx = R.SyscallContext(execution_unit_id=EU, user_id="u-1", capabilities=["test.eou"], trace_id="t")
    with patch.object(D, "_get_rm", lambda: _OkRm()):
        return d.dispatch(name, payload or {"p": 1}, ctx)


def test_a_raised_unknown_is_recorded_unknown_and_held_on_replay(ledger):
    from AINDY.kernel.syscall_outcome import EffectOutcomeUnknown

    def _raise(_n):
        raise EffectOutcomeUnknown("read timed out after the request was written")

    name, runs = _probe(_raise)
    first = _dispatch(name)
    assert first["status"] == "unknown" and first["failure_class"] == "unknown", first
    assert [s for s, _ in _row(ledger)] == ["unknown"], "recorded unknown, not failed"

    second = _dispatch(name)
    assert runs == [1], "DEC-085: the replay re-ran an effect whose outcome was unknown"
    assert second["status"] == "unknown" and second["failure_class"] == "unknown"
    assert second["outcome"]["held"] is True and second["outcome"]["reconcile_required"] is True
    assert second["outcome"]["detail"] == "read timed out after the request was written"


def test_a_claimed_partial_is_held_with_its_units(ledger):
    from AINDY.kernel.syscall_outcome import OUTCOME_KEY, partial

    name, runs = _probe(lambda n: {"sent": 2, OUTCOME_KEY: partial([{"to": "a", "ok": True},
                                                                    {"to": "b", "ok": False}])})
    first = _dispatch(name)
    assert first["status"] == "partial", first
    second = _dispatch(name)
    assert runs == [1], "DEC-086: a partial effect was re-run, re-applying the units that landed"
    assert second["status"] == "partial" and second["data"] == {"sent": 2}
    assert second["outcome"]["units"][1] == {"to": "b", "ok": False}
    assert second["outcome"]["held"] is True and second["outcome"]["reconcile_required"] is False
    assert "failure_class" not in second


def test_control_a_failed_effect_is_still_reclaimed_and_re_run(ledger):
    def _fail_then_succeed(n):
        if n == 1:
            raise ValueError("connection refused")
        return {"ok": True}

    name, runs = _probe(_fail_then_succeed)
    assert _dispatch(name)["status"] == "error"
    assert _dispatch(name)["status"] == "success"
    assert runs == [1, 1], "a knowable failure must stay retryable; the hold is for ambiguity only"


def test_control_a_success_replays_as_success(ledger):
    name, runs = _probe(lambda n: {"ok": n})
    assert _dispatch(name)["data"] == {"ok": 1}
    replay = _dispatch(name)
    assert runs == [1] and replay["status"] == "success" and replay["data"] == {"ok": 1}


def test_an_unknown_skips_output_validation_so_it_is_not_recorded_failed(ledger):
    """A stable syscall with an output schema: the unknown's empty data would fail validation and
    be recorded `failed`, which is reclaimable, so a retry would re-run it."""
    from AINDY.kernel import syscall_registry as R
    from AINDY.kernel.syscall_outcome import EffectOutcomeUnknown

    name, runs = _probe(lambda n: (_ for _ in ()).throw(EffectOutcomeUnknown("timeout")))
    R.SYSCALL_REGISTRY[name].output_schema = {"required": ["id"], "properties": {"id": {"type": "string"}}}
    R.SYSCALL_REGISTRY[name].stable = True
    assert _dispatch(name)["status"] == "unknown"
    assert [s for s, _ in _row(ledger)] == ["unknown"]


# ── the tool seam ──────────────────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _tool_env(monkeypatch):
    from AINDY.agents import tool_registry as tr

    monkeypatch.setattr(tr, "_tool_idempotency_enabled", lambda: True)
    with patch.object(tr, "_ensure_tools_loaded", lambda: None), patch(
        "AINDY.agents.capability_service.check_tool_capability",
        return_value={"ok": True, "allowed_capabilities": [], "granted_tools": []},
    ), patch.object(tr, "queue_system_event", lambda **k: None), patch(
        "AINDY.platform_layer.secret_broker.capability_scope", lambda caps: contextlib.nullcontext(),
    ):
        yield tr


def _tool(tr, behaviour):
    name = f"eou_tool_{uuid.uuid4().hex[:8]}"
    runs: list[int] = []

    def fn(args, user_id, db):
        runs.append(1)
        return behaviour(len(runs))

    tr.register_tool(name=name, risk="low", description="t", capability="c", required_capability="c",
                     category="test", egress_scope="none", execution_guarantee="EXACTLY_ONCE")(fn)
    return name, runs


def test_a_tool_raising_unknown_is_recorded_unknown_and_held(ledger, monkeypatch):
    from AINDY.kernel.syscall_outcome import EffectOutcomeUnknown

    with _tool_env(monkeypatch) as tr:
        def _raise(_n):
            raise EffectOutcomeUnknown("smtp: no reply after DATA")

        name, runs = _tool(tr, _raise)
        try:
            db = ledger()
            first = tr.execute_tool(name, {"to": "x"}, "u-1", db, run_id=f"run_{EU}", execution_token={"t": 1})
            assert first["failure_class"] == "unknown" and first["success"] is False, first
            assert [s for s, _ in _row(ledger)] == ["unknown"]
            second = tr.execute_tool(name, {"to": "x"}, "u-1", db, run_id=f"run_{EU}", execution_token={"t": 1})
        finally:
            db.close()
            tr.TOOL_REGISTRY.pop(name, None)
    assert runs == [1], "DEC-085: the tool seam re-ran an effect whose outcome was unknown"
    assert second["failure_class"] == "unknown" and second["idempotent_replay"] is True
    assert second["outcome"]["held"] is True and "smtp: no reply after DATA" in second["outcome"]["detail"]


def test_the_worker_carries_a_declared_class_back(monkeypatch):
    """An isolated tool raising EffectOutcomeUnknown: the worker reply must say `unknown`, or the
    parent records the effect `failed` and a retry re-runs it."""
    from AINDY.agents import tool_registry as tr
    from AINDY.agents import tool_worker
    from AINDY.kernel.syscall_outcome import EffectOutcomeUnknown

    def _raise(**_kw):
        raise EffectOutcomeUnknown("worker: read timeout")

    monkeypatch.setitem(tr.TOOL_REGISTRY, "eou_worker_tool", {"fn": _raise})
    monkeypatch.setattr(tr, "_ensure_tools_loaded", lambda: None)
    reply = tool_worker.run_one({"tool_name": "eou_worker_tool", "args": {}, "user_id": "u"})
    assert reply["ok"] is False and reply["failure_class"] == "unknown", reply


# ── retry classification (DEC-090) ─────────────────────────────────────────────────────────


def test_unknown_is_a_class_and_nothing_retries_it():
    from AINDY.core.retry_policy import FAILURE_CLASSES, RETRYABLE_CLASSES, classify_failure, is_retryable_error

    assert "unknown" in FAILURE_CLASSES and "unknown" not in RETRYABLE_CLASSES
    shaped = {"success": False, "error": "read timeout", "failure_class": "unknown"}
    assert is_retryable_error(shaped) is False
    assert classify_failure(shaped).failure_class == "unknown"
    # control: the same text without the declaration is classified by the fallback table
    assert classify_failure({"success": False, "error": "read timeout"}).failure_class != "unknown"


# ── TTL (DEC-087) ──────────────────────────────────────────────────────────────────────────


def test_the_ttl_job_never_reaps_an_unknown_and_sets_the_gauge(ledger, monkeypatch):
    from AINDY.db.models.effect_record import EffectRecord
    from AINDY.platform_layer import scheduler_service
    from AINDY.platform_layer.metrics import REGISTRY

    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3650)
    s = ledger()
    for status in ("unknown", "success"):
        s.add(EffectRecord(action_id=f"aid-{status}", action_type="t", input_hash="h" * 64,
                           status=status, created_at=old, completed_at=old))
    s.commit()
    s.close()

    scheduler_service._cleanup_expired_effect_records()

    assert [s for s, _ in _row(ledger)] == ["unknown"], "the unknown was reaped, or the success was not"
    assert REGISTRY.get_sample_value("aindy_effect_unknown_unresolved") == 1.0
