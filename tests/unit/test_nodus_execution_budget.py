"""AINDY_NODUS_MAX_EXECUTION_MS budget resolution + run_script precedence.

The Nodus wall-clock budget is set by the runtime (not nodus-lang) and applied to
both the outer subprocess timeout and the inner run_source(timeout_ms=). These tests
pin the resolver and the per-run override precedence so the app-profile cold-start
tuning knob can't silently regress to the hardcoded 30s.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.runtime_only

from AINDY.runtime.nodus_runtime_adapter import (
    NodusExecutionContext,
    NodusRuntimeAdapter,
    _DEFAULT_BOOT_ALLOWANCE_MS,
    _DEFAULT_MAX_EXECUTION_MS,
    _resolve_boot_allowance_ms,
    _resolve_default_max_execution_ms,
)


def test_resolver_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("AINDY_NODUS_MAX_EXECUTION_MS", raising=False)
    assert _resolve_default_max_execution_ms() == _DEFAULT_MAX_EXECUTION_MS == 30_000


def test_resolver_reads_valid_env(monkeypatch):
    monkeypatch.setenv("AINDY_NODUS_MAX_EXECUTION_MS", "180000")
    assert _resolve_default_max_execution_ms() == 180_000


def test_resolver_strips_whitespace(monkeypatch):
    monkeypatch.setenv("AINDY_NODUS_MAX_EXECUTION_MS", "  90000  ")
    assert _resolve_default_max_execution_ms() == 90_000


@pytest.mark.parametrize("bad", ["0", "-5", "notanint", ""])
def test_resolver_falls_back_on_bad_value(monkeypatch, bad):
    monkeypatch.setenv("AINDY_NODUS_MAX_EXECUTION_MS", bad)
    assert _resolve_default_max_execution_ms() == _DEFAULT_MAX_EXECUTION_MS


def _capture_budget(monkeypatch):
    """Patch _execute to record the effective budget run_script computes."""
    seen: dict[str, int] = {}
    adapter = NodusRuntimeAdapter(db=None)

    def fake_execute(script, filename, context, max_execution_ms=None):
        seen["ms"] = max_execution_ms
        return None

    monkeypatch.setattr(adapter, "_execute", fake_execute)
    return adapter, seen


def test_run_script_uses_env_default(monkeypatch):
    monkeypatch.setenv("AINDY_NODUS_MAX_EXECUTION_MS", "120000")
    adapter, seen = _capture_budget(monkeypatch)
    ctx = NodusExecutionContext(user_id="u", execution_unit_id="eu")
    adapter.run_script("noop", ctx)
    assert seen["ms"] == 120_000


def test_context_override_beats_env(monkeypatch):
    monkeypatch.setenv("AINDY_NODUS_MAX_EXECUTION_MS", "120000")
    adapter, seen = _capture_budget(monkeypatch)
    ctx = NodusExecutionContext(user_id="u", execution_unit_id="eu", max_execution_ms=5_000)
    adapter.run_script("noop", ctx)
    assert seen["ms"] == 5_000


def test_explicit_arg_beats_env_but_not_context(monkeypatch):
    monkeypatch.setenv("AINDY_NODUS_MAX_EXECUTION_MS", "120000")
    adapter, seen = _capture_budget(monkeypatch)
    ctx = NodusExecutionContext(user_id="u", execution_unit_id="eu")
    adapter.run_script("noop", ctx, max_execution_ms=45_000)
    assert seen["ms"] == 45_000


# --- NODUS-WARMPOOL-1 Option A: boot-allowance resolver -----------------------


def test_boot_allowance_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("AINDY_NODUS_BOOT_ALLOWANCE_MS", raising=False)
    assert _resolve_boot_allowance_ms() == _DEFAULT_BOOT_ALLOWANCE_MS == 15_000


def test_boot_allowance_reads_valid_env(monkeypatch):
    monkeypatch.setenv("AINDY_NODUS_BOOT_ALLOWANCE_MS", "  60000  ")
    assert _resolve_boot_allowance_ms() == 60_000


def test_boot_allowance_zero_is_valid(monkeypatch):
    """0 is a legitimate value — it restores the old single-shared-budget behavior."""
    monkeypatch.setenv("AINDY_NODUS_BOOT_ALLOWANCE_MS", "0")
    assert _resolve_boot_allowance_ms() == 0


@pytest.mark.parametrize("bad", ["-1", "notanint", ""])
def test_boot_allowance_falls_back_on_bad_value(monkeypatch, bad):
    monkeypatch.setenv("AINDY_NODUS_BOOT_ALLOWANCE_MS", bad)
    assert _resolve_boot_allowance_ms() == _DEFAULT_BOOT_ALLOWANCE_MS


# --- NODUS-WARMPOOL-1 Option A: outer timeout = script budget + boot allowance --


class _FakeProc:
    def __init__(self, stdout):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


def _run_execute(monkeypatch, *, max_ms, boot_env):
    """Run _execute with subprocess.run mocked; capture the outer timeout + inner payload."""
    if boot_env is None:
        monkeypatch.delenv("AINDY_NODUS_BOOT_ALLOWANCE_MS", raising=False)
    else:
        monkeypatch.setenv("AINDY_NODUS_BOOT_ALLOWANCE_MS", boot_env)
    # isolate the timeout/payload wiring from deferred-apply internals
    monkeypatch.setattr("AINDY.runtime.nodus_runtime_adapter._apply_deferred_memory_writes", lambda *a, **k: None)
    monkeypatch.setattr("AINDY.runtime.nodus_runtime_adapter._apply_deferred_events", lambda *a, **k: None)

    captured: dict = {}
    ok_stdout = json.dumps(
        {"output_state": {}, "emitted_events": [], "memory_writes": [], "status": "success"}
    )

    def fake_run(cmd, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        captured["payload"] = json.loads(kwargs.get("input"))
        return _FakeProc(ok_stdout)

    monkeypatch.setattr("AINDY.runtime.nodus_runtime_adapter.subprocess.run", fake_run)
    adapter = NodusRuntimeAdapter(db=None)
    ctx = NodusExecutionContext(user_id="u", execution_unit_id="eu")
    adapter._execute("noop", "test.nd", ctx, max_execution_ms=max_ms)
    return captured


def test_outer_timeout_adds_boot_allowance(monkeypatch):
    cap = _run_execute(monkeypatch, max_ms=30_000, boot_env="15000")
    # outer subprocess kill = (script budget + boot allowance) seconds
    assert cap["timeout"] == 45.0
    # inner script clock handed to the worker is the script budget, unchanged
    assert cap["payload"]["max_execution_ms"] == 30_000


def test_outer_timeout_uses_default_allowance(monkeypatch):
    cap = _run_execute(monkeypatch, max_ms=30_000, boot_env=None)
    assert cap["timeout"] == (30_000 + _DEFAULT_BOOT_ALLOWANCE_MS) / 1000.0
    assert cap["payload"]["max_execution_ms"] == 30_000


def test_boot_allowance_zero_restores_shared_budget(monkeypatch):
    cap = _run_execute(monkeypatch, max_ms=30_000, boot_env="0")
    assert cap["timeout"] == 30.0  # outer == inner == old behavior
    assert cap["payload"]["max_execution_ms"] == 30_000
