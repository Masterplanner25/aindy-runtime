"""DUR-3 — flip continuation default-safe (opt-in) + opt-out deny-list.

With DUR-1/2/2b/2c making a re-run's runtime-mediated effects at-most-once, the per-flow /
per-agent continuation-safe DECLARATION is no longer required for safety. DUR-3 adds an opt-in
`AINDY_DURABLE_CONTINUATION_ALL` flag that permits continuation for ALL flows/agents except those
explicitly deny-listed (raw un-mediated side effects). Default off = declaration still required.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only


@pytest.fixture(autouse=True)
def _clean_registries():
    from AINDY.runtime.flow_engine import registry as fr
    from AINDY.core import agent_continuation as ac

    fr.CONTINUATION_SAFE_FLOWS.clear()
    fr.CONTINUATION_UNSAFE_FLOWS.clear()
    ac.CONTINUATION_SAFE_AGENT_TYPES.clear()
    ac.CONTINUATION_UNSAFE_AGENT_TYPES.clear()
    yield
    fr.CONTINUATION_SAFE_FLOWS.clear()
    fr.CONTINUATION_UNSAFE_FLOWS.clear()
    ac.CONTINUATION_SAFE_AGENT_TYPES.clear()
    ac.CONTINUATION_UNSAFE_AGENT_TYPES.clear()


# ── Flow side ──────────────────────────────────────────────────────────────────

def test_flow_default_off_requires_declaration(monkeypatch):
    from AINDY.core import flow_continuation as fc
    from AINDY.runtime.flow_engine import mark_flow_continuation_safe

    monkeypatch.setattr(fc, "_default_safe_enabled", lambda: False)
    assert fc._flow_continuation_permitted("undeclared") is False   # not declared → denied
    mark_flow_continuation_safe("declared")
    assert fc._flow_continuation_permitted("declared") is True


def test_flow_default_safe_permits_all_but_denylisted(monkeypatch):
    from AINDY.core import flow_continuation as fc
    from AINDY.runtime.flow_engine import mark_flow_continuation_unsafe

    monkeypatch.setattr(fc, "_default_safe_enabled", lambda: True)
    # An undeclared flow is now permitted (no declaration needed).
    assert fc._flow_continuation_permitted("any_flow") is True
    # …unless it's on the opt-out deny-list.
    mark_flow_continuation_unsafe("raw_side_effects")
    assert fc._flow_continuation_permitted("raw_side_effects") is False


# ── Agent side ─────────────────────────────────────────────────────────────────

def test_agent_default_off_requires_declaration(monkeypatch):
    from AINDY.core import agent_continuation as ac

    monkeypatch.setattr(ac, "_default_safe_enabled", lambda: False)
    assert ac._agent_continuation_permitted("undeclared") is False
    ac.mark_agent_type_continuation_safe("declared")
    assert ac._agent_continuation_permitted("declared") is True


def test_agent_default_safe_permits_all_but_denylisted(monkeypatch):
    from AINDY.core import agent_continuation as ac

    monkeypatch.setattr(ac, "_default_safe_enabled", lambda: True)
    assert ac._agent_continuation_permitted("any_agent") is True
    ac.mark_agent_type_continuation_unsafe("raw_side_effects")
    assert ac._agent_continuation_permitted("raw_side_effects") is False


def test_default_safe_flag_reads_settings(monkeypatch):
    from AINDY.core import flow_continuation as fc
    from AINDY.config import settings

    monkeypatch.setattr(settings, "AINDY_DURABLE_CONTINUATION_ALL", True, raising=False)
    assert fc._default_safe_enabled() is True
    monkeypatch.setattr(settings, "AINDY_DURABLE_CONTINUATION_ALL", False, raising=False)
    assert fc._default_safe_enabled() is False
