"""NODUS-WARMPOOL-1 Phase 1 — warm worker framing + pool lifecycle + adapter wiring.

The warm worker/pool is the net-new, isolation-sensitive code. These tests exercise the
length-prefixed protocol, timeout/crash handling, and the pool's respawn/recycle/drop
lifecycle against fake processes/workers (no real subprocess), plus the adapter's
warm-path-with-fallback wiring. End-to-end (a real warm worker serving a nodus script) is
app-side PG-tier integration.
"""
from __future__ import annotations

import io
import json
import struct
import threading
import types

import pytest

import AINDY.runtime.nodus_worker_pool as pool_mod
from AINDY.runtime.nodus_worker_pool import (
    NodusWorkerPool,
    WarmNodusWorker,
    WorkerCrashed,
    _max_requests,
    warm_pool_enabled,
)

pytestmark = pytest.mark.runtime_only


def _frame(obj) -> bytes:
    data = json.dumps(obj).encode("utf-8")
    return struct.pack(">I", len(data)) + data


class _FakeProc:
    def __init__(self, stdout_bytes: bytes = b""):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(stdout_bytes)
        self._rc = None

    def poll(self):
        return self._rc

    def kill(self):
        self._rc = -9

    def wait(self, timeout=None):
        return self._rc


def _worker_with(proc) -> WarmNodusWorker:
    w = WarmNodusWorker.__new__(WarmNodusWorker)  # bypass __init__ (no real subprocess)
    w._proc = proc
    w.requests = 0
    return w


# ── framing / worker ─────────────────────────────────────────────────────────

def test_execute_round_trip_and_request_framing():
    resp = {"status": "success", "output_state": {"x": 1}}
    w = _worker_with(_FakeProc(_frame(resp)))
    out = w.execute({"script": "let x=1"}, timeout_s=5)
    assert out == resp
    assert w.requests == 1
    # the request went out length-prefixed
    sent = w._proc.stdin.getvalue()
    (length,) = struct.unpack(">I", sent[:4])
    assert json.loads(sent[4 : 4 + length].decode()) == {"script": "let x=1"}


def test_execute_raises_on_eof_crash():
    w = _worker_with(_FakeProc(b""))  # immediate EOF
    with pytest.raises(WorkerCrashed):
        w.execute({"script": "x"}, timeout_s=5)


def test_execute_raises_on_truncated_response():
    truncated = struct.pack(">I", 100) + b"abc"  # header claims 100 bytes, only 3 present
    w = _worker_with(_FakeProc(truncated))
    with pytest.raises(WorkerCrashed):
        w.execute({"script": "x"}, timeout_s=5)


class _BlockingStdout:
    def __init__(self):
        self.ev = threading.Event()

    def read(self, n):
        self.ev.wait()  # blocks until released
        return b""


def test_execute_times_out_when_worker_stuck():
    proc = _FakeProc()
    proc.stdout = _BlockingStdout()
    w = _worker_with(proc)
    try:
        with pytest.raises(TimeoutError):
            w.execute({"script": "loop"}, timeout_s=0.2)
    finally:
        proc.stdout.ev.set()  # release the reader thread


# ── pool lifecycle ───────────────────────────────────────────────────────────

class _FakeWorker:
    def __init__(self):
        self.requests = 0
        self._alive = True
        self.killed = False
        self.raise_on_execute = None

    def alive(self):
        return self._alive

    def execute(self, payload, *, timeout_s):
        if self.raise_on_execute is not None:
            raise self.raise_on_execute
        self.requests += 1
        return {"ok": True, "payload": payload}

    def kill(self):
        self.killed = True
        self._alive = False


def _patch_worker_factory(monkeypatch):
    created: list[_FakeWorker] = []

    def _make():
        w = _FakeWorker()
        created.append(w)
        return w

    monkeypatch.setattr(pool_mod, "WarmNodusWorker", _make)
    return created


def test_pool_reuses_same_warm_worker(monkeypatch):
    created = _patch_worker_factory(monkeypatch)
    p = NodusWorkerPool()
    p.execute({"a": 1}, timeout_s=5)
    p.execute({"a": 2}, timeout_s=5)
    assert len(created) == 1  # amortized — one long-lived worker
    assert created[0].requests == 2


def test_pool_respawns_dead_worker(monkeypatch):
    created = _patch_worker_factory(monkeypatch)
    p = NodusWorkerPool()
    p.execute({}, timeout_s=5)
    created[0]._alive = False  # died while idle
    p.execute({}, timeout_s=5)
    assert len(created) == 2


def test_pool_recycles_after_max_requests(monkeypatch):
    created = _patch_worker_factory(monkeypatch)
    monkeypatch.setattr(pool_mod, "_max_requests", lambda: 2)
    p = NodusWorkerPool()
    p.execute({}, timeout_s=5)
    p.execute({}, timeout_s=5)  # worker now at 2 requests
    p.execute({}, timeout_s=5)  # _ensure_worker sees >= limit → recycle
    assert len(created) == 2
    assert created[0].killed is True
    assert created[0].requests == 2


def test_pool_drops_worker_on_crash(monkeypatch):
    created = _patch_worker_factory(monkeypatch)
    p = NodusWorkerPool()
    p.execute({}, timeout_s=5)  # spawns created[0]
    created[0].raise_on_execute = WorkerCrashed("boom")
    with pytest.raises(WorkerCrashed):
        p.execute({}, timeout_s=5)
    assert created[0].killed is True
    assert p._worker is None  # dropped → next call respawns (no retry, no double-exec)


# ── env knobs ────────────────────────────────────────────────────────────────

def test_warm_pool_enabled_flag(monkeypatch):
    monkeypatch.delenv("AINDY_NODUS_WARM_POOL", raising=False)
    assert warm_pool_enabled() is False
    monkeypatch.setenv("AINDY_NODUS_WARM_POOL", "true")
    assert warm_pool_enabled() is True


def test_max_requests_default_and_env(monkeypatch):
    monkeypatch.delenv("AINDY_NODUS_WARM_MAX_REQUESTS", raising=False)
    assert _max_requests() == 500
    monkeypatch.setenv("AINDY_NODUS_WARM_MAX_REQUESTS", "10")
    assert _max_requests() == 10
    monkeypatch.setenv("AINDY_NODUS_WARM_MAX_REQUESTS", "0")  # 0 = never recycle
    assert _max_requests() == 0


# ── adapter wiring (warm path + fallback) ────────────────────────────────────

def _adapter_and_ctx():
    from AINDY.runtime.nodus_runtime_adapter import NodusExecutionContext, NodusRuntimeAdapter

    adapter = NodusRuntimeAdapter(db=None)
    ctx = NodusExecutionContext(user_id="u", execution_unit_id="eu")
    return adapter, ctx


def _stub_deferred(monkeypatch):
    monkeypatch.setattr("AINDY.runtime.nodus_runtime_adapter._apply_deferred_memory_writes", lambda *a, **k: None)
    monkeypatch.setattr("AINDY.runtime.nodus_runtime_adapter._apply_deferred_events", lambda *a, **k: None)


def test_adapter_uses_warm_result_without_subprocess(monkeypatch):
    monkeypatch.setenv("AINDY_NODUS_WARM_POOL", "true")
    _stub_deferred(monkeypatch)
    warm_result = {"output_state": {"ok": 1}, "emitted_events": [], "memory_writes": [], "status": "success"}
    monkeypatch.setattr(pool_mod, "get_pool", lambda: types.SimpleNamespace(
        execute=lambda payload, *, timeout_s: warm_result))

    def _boom(*a, **k):
        raise AssertionError("subprocess.run must NOT run on warm success")

    monkeypatch.setattr("AINDY.runtime.nodus_runtime_adapter.subprocess.run", _boom)
    adapter, ctx = _adapter_and_ctx()
    res = adapter._execute("noop", "t.nd", ctx, max_execution_ms=30_000)
    assert res.status == "success"
    assert ctx.state.get("ok") == 1


def test_adapter_falls_back_to_fresh_when_warm_raises(monkeypatch):
    monkeypatch.setenv("AINDY_NODUS_WARM_POOL", "true")
    _stub_deferred(monkeypatch)

    def _raise(payload, *, timeout_s):
        raise RuntimeError("warm boom")

    monkeypatch.setattr(pool_mod, "get_pool", lambda: types.SimpleNamespace(execute=_raise))
    fresh = types.SimpleNamespace(
        returncode=0,
        stdout=json.dumps({"output_state": {"fresh": 1}, "emitted_events": [], "memory_writes": [], "status": "success"}),
        stderr="",
    )
    monkeypatch.setattr("AINDY.runtime.nodus_runtime_adapter.subprocess.run", lambda *a, **k: fresh)
    adapter, ctx = _adapter_and_ctx()
    res = adapter._execute("noop", "t.nd", ctx, max_execution_ms=30_000)
    assert res.status == "success"
    assert ctx.state.get("fresh") == 1  # fresh subprocess path was used
