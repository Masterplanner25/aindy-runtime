"""AINDY_NODUS_MAX_EXECUTION_MS budget resolution + run_script precedence.

The Nodus wall-clock budget is set by the runtime (not nodus-lang) and applied to
both the outer subprocess timeout and the inner run_source(timeout_ms=). These tests
pin the resolver and the per-run override precedence so the app-profile cold-start
tuning knob can't silently regress to the hardcoded 30s.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only

from AINDY.runtime.nodus_runtime_adapter import (
    NodusExecutionContext,
    NodusRuntimeAdapter,
    _DEFAULT_MAX_EXECUTION_MS,
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
