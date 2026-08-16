"""AUTHORITY-VALUE-1 — `child_context` must narrow, never widen.

`capabilities` is a *request*, and the parent's grant is the ceiling: asking for something the
parent does not hold is escalation, not delegation. `mint_token` already enforces exactly this
for delegated runs via `capability_ceiling`; this path was left conventional.

**Why the clamp is opt-in rather than simply applied.** It is not the two-line change it looks
like. `aindy-apps-monolith`'s `_dispatch_owner_syscall` builds a child granting the *nested*
syscall's capability, while `_resolve_dispatch_capabilities` grants the parent **exactly the
outer syscall's own capability** — so clamping intersects to the empty set and denies a call
that works today. `test_the_app_pattern_is_what_makes_this_opt_in` encodes that reasoning as an
executable fact rather than a comment, so a future reader cannot flip the default on the
assumption that it is free.

With the flag off the only change is a WARNING. That is deliberate: the real exposure has never
been measured, and this repo's own history says a boundary should be tightened on a count, not
on an argument.
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


def test_the_app_pattern_is_what_makes_this_opt_in(_clamp_on):
    """Reproduces `aindy-apps-monolith`'s `_dispatch_owner_syscall` shape exactly.

    An app handler for an outer syscall dispatches a nested one, granting the *nested*
    capability. Its parent context holds only the outer syscall's capability, because
    `_resolve_dispatch_capabilities` grants "exactly the requested syscall's own required
    capability".

    So the clamp reduces that child to an empty grant and the nested dispatch is denied.
    **This is why the flag defaults off**, and this test exists so that fact is discovered by
    a failing assertion rather than by an app-side outage.
    """
    parent = _ctx(["task.write"])          # outer syscall's capability, least-privilege
    child = _child(parent, capabilities=["memory.write"])  # nested syscall's capability

    assert child.capabilities == [], (
        "if this is no longer empty the capability model changed; re-check whether "
        "AINDY_CHILD_CONTEXT_CLAMP can now default on"
    )
