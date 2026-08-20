"""AUTHORITY-VALUE-1 — `child_context` must narrow, never widen.

`capabilities` is a *request*, and the parent's grant is the ceiling: asking for something the
parent does not hold is escalation, not delegation. `mint_token` already enforces exactly this
for delegated runs via `capability_ceiling`; this path was left conventional.

**★ The clamp is ON by default since 2026-08-19.** `AINDY_CHILD_CONTEXT_CLAMP=0` restores the
permissive behaviour, in which a widening request is granted and only logged.

**Why it was opt-in, and why that reasoning was right about the mechanic and wrong about the
consequence.** `aindy-apps-monolith`'s `_dispatch_owner_syscall` builds a child granting the
*nested* syscall's capability, while `_resolve_dispatch_capabilities` grants the parent
**exactly the outer syscall's own capability** — so clamping intersects to the empty set. That
is true, and `test_the_app_pattern_is_what_makes_this_opt_in` still pins it.

What was never measured was what the empty set *costs*. Measured 2026-08-19:

* **18 of the 19 functions that widen are never registered** — unreachable, so a clamp cannot
  break them.
* **The one live caller degrades gracefully.** `_handle_agent_suggest_tools` widens for an
  optional cached-suggestions lookup, inside `try/except`, with a full KPI-based fallback
  beneath it. Denied, it warns and recomputes.

So "denies a call that works today" described one optional optimisation with a fallback, not a
working feature. The repo's own rule — tighten a boundary on a count, not an argument — is what
moved the default, and the count is **1 degradation, 0 outages**.
"""
from __future__ import annotations

import logging

import pytest

pytestmark = pytest.mark.runtime_only


def _ctx(capabilities):
    from AINDY.kernel.syscall_registry import SyscallContext

    return SyscallContext(
        execution_unit_id="eu-1",
        user_id="u-1",
        capabilities=list(capabilities),
        trace_id="t-1",
    )


def _child(parent, **kw):
    from AINDY.kernel.syscall_dispatcher import child_context

    return child_context(parent, **kw)


@pytest.fixture
def _clamp_on(monkeypatch):
    monkeypatch.setenv("AINDY_CHILD_CONTEXT_CLAMP", "1")


@pytest.fixture
def _clamp_off(monkeypatch):
    # ★ Explicitly "0", not delenv. The default flipped ON 2026-08-19, so an unset variable now
    # means CLAMPED — and these two tests silently became tests of the clamp rather than of the
    # permissive path. Both went red on the flip, which is the fixture doing its job.
    monkeypatch.setenv("AINDY_CHILD_CONTEXT_CLAMP", "0")


@pytest.fixture
def _clamp_unset(monkeypatch):
    """No value at all — asserts what an operator who configures nothing actually gets."""
    monkeypatch.delenv("AINDY_CHILD_CONTEXT_CLAMP", raising=False)


# --------------------------------------------------------------------------------------
# Behaviour that must hold regardless of the flag
# --------------------------------------------------------------------------------------


def test_inherits_the_parent_grant_when_nothing_is_requested(_clamp_off):
    """Liveness control — if this broke, every narrowing assertion would pass vacuously."""
    child = _child(_ctx(["memory.read", "flow.run"]))

    assert sorted(child.capabilities) == ["flow.run", "memory.read"]


def test_narrowing_is_always_allowed(_clamp_on):
    """A subset is delegation, not escalation, and must survive the clamp untouched."""
    child = _child(_ctx(["memory.read", "memory.write", "flow.run"]), capabilities=["memory.read"])

    assert child.capabilities == ["memory.read"]


def test_identity_fields_still_propagate(_clamp_on):
    """The clamp must touch capabilities only — trace/eu/user identity is the whole point."""
    parent = _ctx(["memory.read"])
    child = _child(parent, capabilities=["memory.read"])

    assert child.execution_unit_id == parent.execution_unit_id
    assert child.user_id == parent.user_id
    assert child.trace_id == parent.trace_id


# --------------------------------------------------------------------------------------
# The escalation itself
# --------------------------------------------------------------------------------------


def test_widening_is_denied_when_the_clamp_is_on(_clamp_on):
    """The defect: a caller could request authority the parent never held."""
    child = _child(_ctx(["memory.read"]), capabilities=["admin.everything"])

    assert child.capabilities == [], (
        "child_context granted a capability the parent did not hold — that is escalation, "
        "not delegation"
    )


def test_partial_widening_keeps_only_the_held_subset(_clamp_on):
    child = _child(
        _ctx(["memory.read"]), capabilities=["memory.read", "admin.everything", "flow.run"]
    )

    assert child.capabilities == ["memory.read"]


def test_widening_still_passes_when_the_clamp_is_off(_clamp_off):
    """Default-off is a deliberate choice, so it is pinned rather than left to drift.

    If someone flips the default without reading why, this test fails and points at the
    reason — which is the app-caller fact encoded below.
    """
    child = _child(_ctx(["memory.read"]), capabilities=["admin.everything"])

    assert child.capabilities == ["admin.everything"]


# --------------------------------------------------------------------------------------
# The widening is visible either way — that is what makes the exposure measurable
# --------------------------------------------------------------------------------------


def test_widening_warns_even_when_the_clamp_is_off(_clamp_off, caplog):
    """Flag-off must not mean silent. The count is the input to deciding the flip."""
    with caplog.at_level(logging.WARNING):
        _child(_ctx(["memory.read"]), capabilities=["admin.everything"])

    messages = [r.getMessage() for r in caplog.records]

    assert any("WIDENED authority" in m for m in messages), (
        f"a widening produced no warning: {messages}"
    )
    assert any("admin.everything" in m for m in messages), (
        "the warning must name the capability that was widened, or it cannot be counted"
    )


def test_clamping_says_what_it_dropped(_clamp_on, caplog):
    with caplog.at_level(logging.WARNING):
        _child(_ctx(["memory.read"]), capabilities=["admin.everything"])

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "CLAMPED" in joined and "admin.everything" in joined, (
        f"the clamp must name what it dropped, got: {joined!r}"
    )


def test_no_warning_when_nothing_is_widened(_clamp_off, caplog):
    """Control for the two tests above — otherwise 'warns' could just mean 'always warns'."""
    with caplog.at_level(logging.WARNING):
        _child(_ctx(["memory.read", "flow.run"]), capabilities=["memory.read"])

    assert not [r for r in caplog.records if "child_context" in r.getMessage()]


# --------------------------------------------------------------------------------------
# Why the default is off — encoded, not merely commented
# --------------------------------------------------------------------------------------


def test_an_operator_who_configures_nothing_gets_the_clamp(_clamp_unset):
    """★ The default itself. Every other test in this file sets the flag explicitly, so none of
    them would notice the default moving back."""
    from AINDY.kernel.syscall_dispatcher import _child_context_clamp_enabled

    assert _child_context_clamp_enabled() is True
    parent = _ctx(["task.write"])
    assert _child(parent, capabilities=["admin.everything"]).capabilities == []


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE", "Off"])
def test_the_permissive_behaviour_is_reachable_by_an_operator(monkeypatch, value):
    """A security default that cannot be turned off is a different kind of problem. Every
    spelling an operator is likely to reach for must work, or they will conclude the flag is
    broken and patch the source instead."""
    from AINDY.kernel.syscall_dispatcher import _child_context_clamp_enabled

    monkeypatch.setenv("AINDY_CHILD_CONTEXT_CLAMP", value)
    assert _child_context_clamp_enabled() is False


def test_a_clamped_child_fails_the_dispatch_gracefully_rather_than_raising():
    """★ THE EVIDENCE THAT MOVED THE DEFAULT, and the thing the original reasoning never checked.

    "Clamping denies a call that works today" is true of the mechanic. What it costs depends
    entirely on how the caller handles a denial — and the app's `_dispatch_owner_syscall` reads
    the returned envelope and raises `ValueError`, inside a `try/except` that logs a warning and
    falls through to a full recomputation.

    That chain only degrades gracefully if the dispatcher **returns an error envelope** rather
    than raising something the app's handler cannot catch. This pins that, because it is the
    difference between a lost optimisation and an outage.
    """
    from AINDY.kernel import syscall_registry as R
    from AINDY.kernel.syscall_dispatcher import SyscallDispatcher

    name = "sys.v1.test.clamp_probe"
    R.SYSCALL_REGISTRY[name] = R.SyscallEntry(
        handler=lambda payload, ctx: {"ok": True}, capability="analytics.read"
    )
    try:
        dispatcher = SyscallDispatcher()
        dispatcher._emit_syscall_event = lambda *a, **kw: None
        starved = R.SyscallContext(
            execution_unit_id="eu-1", user_id="u1", capabilities=[], trace_id="t"
        )
        result = dispatcher.dispatch(name, {}, starved)
    finally:
        R.SYSCALL_REGISTRY.pop(name, None)

    assert isinstance(result, dict), "a denial must be an envelope, not an exception"
    assert result.get("status") != "success"
    assert result.get("error"), "the envelope must say why, or the caller cannot log it usefully"


def test_the_app_pattern_is_what_makes_this_opt_in(_clamp_on):
    """Reproduces `aindy-apps-monolith`'s `_dispatch_owner_syscall` shape exactly.

    An app handler for an outer syscall dispatches a nested one, granting the *nested*
    capability. Its parent context holds only the outer syscall's capability, because
    `_resolve_dispatch_capabilities` grants "exactly the requested syscall's own required
    capability".

    So the clamp reduces that child to an empty grant and the nested dispatch is denied.

    ★ **The mechanic below is unchanged and still pinned; the conclusion drawn from it was
    wrong.** This test used to end "this is why the flag defaults off." Measuring the cost
    (2026-08-19) found 18 of 19 widening callers unregistered and the one live caller degrading
    into an existing fallback, so the default moved ON. Keep this assertion — it is the fact.
    Do not re-attach the inference to it.
    """
    parent = _ctx(["task.write"])          # outer syscall's capability, least-privilege
    child = _child(parent, capabilities=["memory.write"])  # nested syscall's capability

    assert child.capabilities == [], (
        "if this is no longer empty the capability model changed; re-check whether "
        "AINDY_CHILD_CONTEXT_CLAMP can now default on"
    )
