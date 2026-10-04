"""EFFECT-OUTCOME-UNKNOWN-1 phase 3 — the emitters: where an outcome really is unknown.

Phases 1-2 made `unknown` safe to record (held on replay, never retried, `AT_MOST_ONCE`). Nothing
emitted it. These are the places the runtime genuinely cannot tell whether an effect landed:

* **DEC-088, an isolated tool's worker lost mid-call**: killed by its budget or a cancel, crashed,
  or replied unreadably. It may have acted. Before this it was `transient`, so an `EXACTLY_ONCE`
  isolated tool could be retried and act twice. A spawn failure never started, so it stays
  `transient`. A tool that declared no guarantee keeps its class.
* **An MCP call that times out after it was sent**, for a server declared with an effect
  guarantee. A timeout while connecting is knowably not dispatched.
* **`outbound_request`**: connect / pool / write failures and 408/429 never reached the server and
  are retried for any method. A read failure after sending, or a 5xx, may have been processed: it
  is retried only for an idempotent request, and otherwise raises `EffectOutcomeUnknown`.

Each emitter is tested with its REAL exception type, and with the negative case that must NOT
become `unknown`.
"""
from __future__ import annotations

import asyncio
import contextlib
import subprocess
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from tests.fixtures.db import build_private_engine

pytestmark = pytest.mark.runtime_only


# ── (a) DEC-088: the isolated worker, lost mid-call ────────────────────────────────────────


def _lost(kind):
    """What `_run_worker_or_kill_on_cancel` does in each way a started worker can be lost."""
    if kind == "budget_kill":
        raise subprocess.TimeoutExpired(["tool_worker"], 30)
    if kind == "cancel_kill":
        return None
    if kind == "crash":
        return subprocess.CompletedProcess(["tool_worker"], 137, "", "Killed")
    if kind == "garbled":
        return subprocess.CompletedProcess(["tool_worker"], 0, "not json{", "")
    if kind == "spawn_failure":
        raise OSError("exec format error")
    raise AssertionError(kind)


@pytest.mark.parametrize("kind, undeclared_class", [
    ("budget_kill", "transient"), ("cancel_kill", "cancelled"), ("crash", "transient"), ("garbled", "transient"),
])
def test_a_started_worker_lost_mid_call_is_unknown_only_for_an_effectful_tool(monkeypatch, kind, undeclared_class):
    from AINDY.agents import tool_registry as tr

    monkeypatch.setattr(tr, "_run_worker_or_kill_on_cancel", lambda *a, **k: _lost(kind))
    effectful = tr._run_tool_out_of_process("t", {}, "u", run_id="r", effectful=True)
    plain = tr._run_tool_out_of_process("t", {}, "u", run_id="r", effectful=False)
    assert effectful["failure_class"] == "unknown" and effectful["outcome_unknown"] is True, effectful
    assert "outcome unknown" in effectful["error"]
    assert plain["failure_class"] == undeclared_class and "outcome_unknown" not in plain, (
        "control: a tool that declared no guarantee keeps its class (its author said a repeat is fine)"
    )


def test_a_worker_that_never_started_is_not_unknown(monkeypatch):
    """A spawn failure never ran the tool: knowably not dispatched, so still retryable."""
    from AINDY.agents import tool_registry as tr

    monkeypatch.setattr(tr, "_run_worker_or_kill_on_cancel", lambda *a, **k: _lost("spawn_failure"))
    env = tr._run_tool_out_of_process("t", {}, "u", run_id="r", effectful=True)
    assert env["failure_class"] == "transient" and "outcome_unknown" not in env


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    engine = build_private_engine(tmp_path / "emit.db")
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", factory)
    try:
        yield factory
    finally:
        engine.dispose()


def test_an_effectful_isolated_tool_killed_mid_call_is_recorded_unknown_and_never_re_run(ledger, monkeypatch):
    """End to end through the real tool seam and ledger: the case that could act twice today."""
    from AINDY.agents import tool_registry as tr
    from AINDY.db.models.effect_record import EffectRecord

    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY", "true")
    spawned: list[int] = []

    def _worker(*a, **k):
        spawned.append(1)
        raise subprocess.TimeoutExpired(["tool_worker"], 30)

    name = f"emit_iso_{uuid.uuid4().hex[:8]}"
    with patch.object(tr, "_ensure_tools_loaded", lambda: None), patch(
        "AINDY.agents.capability_service.check_tool_capability",
        return_value={"ok": True, "allowed_capabilities": [], "granted_tools": []},
    ), patch.object(tr, "queue_system_event", lambda **k: None), patch(
        "AINDY.platform_layer.secret_broker.capability_scope", lambda caps: contextlib.nullcontext(),
    ), patch.object(tr, "_tool_isolation_enforced", lambda: True), patch.object(
        tr, "_isolation_refusal", lambda *a, **k: None,
    ), patch.object(tr, "_run_worker_or_kill_on_cancel", _worker):
        tr.register_tool(name=name, risk="high", description="t", capability="c", required_capability="c",
                         category="test", egress_scope="none", execution_guarantee="EXACTLY_ONCE",
                         isolation="insecure-dev")(lambda args, user_id, db: {"sent": True})
        db = ledger()
        scope = f"run_{uuid.uuid4()}"
        try:
            first = tr.execute_tool(name, {"to": "y"}, "u-1", db, run_id=scope, execution_token={"t": 1})
            retry = tr.execute_tool(name, {"to": "y"}, "u-1", db, run_id=scope, execution_token={"t": 1})
        finally:
            db.close()
            tr.TOOL_REGISTRY.pop(name, None)

    assert first["failure_class"] == "unknown" and first["outcome_unknown"] is True, first
    assert retry["failure_class"] == "unknown" and retry.get("idempotent_replay") is True, retry
    assert retry["outcome"]["held"] is True and "outcome unknown" in retry["outcome"]["detail"]
    assert spawned == [1], f"the held retry spawned the worker again: {len(spawned)} spawns for one effect"
    s = ledger()
    try:
        assert [r.status for r in s.query(EffectRecord).all()] == ["unknown"]
    finally:
        s.close()


# ── (b) MCP: a timeout after the call was sent ─────────────────────────────────────────────


class _FakeAdapter:
    hang_on = "call"

    def __init__(self, url, timeout):
        pass

    async def connect(self):
        if self.hang_on == "connect":
            await asyncio.sleep(2)

    async def call_tool(self, name, args):
        if self.hang_on == "call":
            await asyncio.sleep(2)
        return {"ok": True}

    async def disconnect(self):
        return None


@pytest.fixture
def mcp(monkeypatch):
    nodus_mcp_aindy = pytest.importorskip("nodus_mcp_aindy")
    from AINDY.platform_layer import mcp_client

    monkeypatch.setattr(nodus_mcp_aindy, "MCPClientAdapter", _FakeAdapter)
    orig = mcp_client._run_sync
    monkeypatch.setattr(mcp_client, "_run_sync", lambda coro, timeout: orig(coro, timeout=0.2))
    return mcp_client


@pytest.mark.parametrize("hang_on, effectful, expected", [
    ("call", True, "unknown"),      # sent, then no answer: the server may have acted
    ("call", False, "timeout"),     # an undeclared server keeps today's retryable timeout
    ("connect", True, "timeout"),   # never sent: knowably not dispatched
])
def test_an_mcp_timeout_is_unknown_only_after_sending_on_an_effectful_server(mcp, monkeypatch, hang_on, effectful, expected):
    from AINDY.kernel.syscall_outcome import EffectOutcomeUnknown

    monkeypatch.setattr(_FakeAdapter, "hang_on", hang_on)
    fn = mcp._make_tool_fn("http://mcp.example", "send_invoice", 1.0, effectful=effectful)
    with pytest.raises(Exception) as exc:
        fn({"amount": 5})
    if expected == "unknown":
        assert isinstance(exc.value, EffectOutcomeUnknown), exc.value
        assert exc.value.failure_class == "unknown"
    else:
        assert not isinstance(exc.value, EffectOutcomeUnknown), exc.value


def test_an_mcp_server_can_declare_its_guarantee(mcp, monkeypatch):
    import nodus_mcp_aindy
    from AINDY.agents import tool_registry as tr

    async def _discover(url, timeout):
        return [SimpleNamespace(name="charge", description="charge a card")]

    monkeypatch.setattr(nodus_mcp_aindy, "discover_tools", _discover)
    monkeypatch.setattr(mcp, "_run_sync", lambda coro, timeout: asyncio.run(coro))
    names = mcp.discover_and_register({"name": "pay", "url": "http://x", "guarantee": "at_most_once"})
    try:
        assert tr.TOOL_REGISTRY[names[0]]["execution_guarantee"] == "AT_MOST_ONCE"
        default = mcp.discover_and_register({"name": "pay2", "url": "http://y"})
        assert tr.TOOL_REGISTRY[default[0]]["execution_guarantee"] == "AT_LEAST_ONCE"
    finally:
        for n in names + locals().get("default", []):
            tr.TOOL_REGISTRY.pop(n, None)


# ── (c) outbound_request ───────────────────────────────────────────────────────────────────


@pytest.fixture
def outbound():
    from AINDY.platform_layer.outbound_http import reset_circuit_breakers

    reset_circuit_breakers()

    def _fake(*, service_name, operation, **kwargs):
        return operation()

    with patch("AINDY.platform_layer.external_call_service.perform_external_call", side_effect=_fake):
        yield
    reset_circuit_breakers()


def _call(method, failure, *, idempotent=None, max_retries=2):
    from AINDY.platform_layer.outbound_http import outbound_request

    calls = {"n": 0}

    def fake_request(m, url, **kwargs):
        calls["n"] += 1
        if isinstance(failure, int):
            return httpx.Response(failure)
        raise failure

    with patch("httpx.request", side_effect=fake_request):
        try:
            outbound_request(method, "https://api.example.com/v1", service_name=f"s{uuid.uuid4().hex[:6]}",
                             capability="outbound.ex", max_retries=max_retries, backoff_base=0,
                             idempotent=idempotent)
            return calls["n"], None
        except Exception as exc:  # noqa: BLE001
            return calls["n"], exc


@pytest.mark.parametrize("exc, expected", [
    (httpx.ConnectError("refused"), "not_dispatched"),
    (httpx.ConnectTimeout("slow"), "not_dispatched"),
    (httpx.PoolTimeout("pool"), "not_dispatched"),
    (httpx.WriteTimeout("partial"), "not_dispatched"),
    (httpx.ReadTimeout("no answer"), "unknown"),
    (httpx.RemoteProtocolError("dropped"), "unknown"),
    (httpx.ReadError("reset"), "unknown"),
])
def test_the_transport_classifier(exc, expected):
    from AINDY.platform_layer.outbound_http import classify_transport_exception

    assert classify_transport_exception(exc) == expected


def test_a_post_that_timed_out_reading_is_unknown_and_not_retried(outbound):
    from AINDY.kernel.syscall_outcome import EffectOutcomeUnknown

    n, exc = _call("POST", httpx.ReadTimeout("no answer"))
    assert isinstance(exc, EffectOutcomeUnknown) and n == 1, (n, exc)


def test_a_post_that_got_a_5xx_is_unknown_and_not_retried(outbound):
    from AINDY.kernel.syscall_outcome import EffectOutcomeUnknown

    n, exc = _call("POST", 503)
    assert isinstance(exc, EffectOutcomeUnknown) and n == 1, (n, exc)


@pytest.mark.parametrize("method, failure, idempotent", [
    ("POST", httpx.ConnectError("refused"), None),   # never reached the server
    ("POST", 429, None),                             # the server said it did not process it
    ("GET", httpx.ReadTimeout("no answer"), None),   # idempotent by method
    ("POST", httpx.ReadTimeout("no answer"), True),  # declared idempotent (an idempotency key)
])
def test_controls_that_stay_retried(outbound, method, failure, idempotent):
    from AINDY.kernel.syscall_outcome import EffectOutcomeUnknown
    from AINDY.platform_layer.outbound_http import TransientHTTPError

    n, exc = _call(method, failure, idempotent=idempotent, max_retries=2)
    assert n == 3, f"{method} {failure!r}: expected 3 attempts, got {n}"
    assert isinstance(exc, TransientHTTPError) and not isinstance(exc, EffectOutcomeUnknown), exc
