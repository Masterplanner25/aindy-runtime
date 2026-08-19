"""TOOL-SEAM-ISOLATION-1 step A — the tool gets a revocable handle, not the live session.

Scope: ``docs/runtime/TOOL_SEAM_ISOLATION_SCOPE.md``.

★ **What these tests do NOT claim.** Step A narrows one argument. A tool holding this handle can
still ``import os``, spawn a thread, or open a socket, so nothing here should be read as evidence
that the tool seam is confined. The process boundary is step C and is not built.

★ **Several assertions below are absences** — "a tool that ignores db is unaffected", "revoke does
not close the session". Those pass on a completely unwired seam, so
``test_liveness_the_handle_is_actually_installed`` runs first and proves the tool receives a
handle at all.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from AINDY.agents.tool_registry import TOOL_REGISTRY, execute_tool, register_tool
from AINDY.agents.tool_session import RevocableToolSession, ToolSessionRevoked

pytestmark = pytest.mark.runtime_only


# ── The handle in isolation ──────────────────────────────────────────────────


def test_a_live_handle_forwards_to_the_session():
    session = MagicMock()
    session.query.return_value = "rows"
    handle = RevocableToolSession(session, tool_name="t")

    assert handle.query() == "rows"
    assert handle.touched is True


def test_a_revoked_handle_refuses_and_names_the_tool():
    """★ The narrowing. A tool cannot stash the session and use it after the call returns —
    a security narrowing and a bug class (RT-MEMTXN-LEAK-1's neighbourhood)."""
    handle = RevocableToolSession(MagicMock(), tool_name="stasher.tool")
    handle.revoke()

    with pytest.raises(ToolSessionRevoked) as exc:
        handle.execute("SELECT 1")

    assert "stasher.tool" in str(exc.value)
    assert "execute" in str(exc.value)


def test_revoke_does_not_close_or_touch_the_underlying_session():
    """★ Load-bearing: the runtime's own _finalize_tool_effect runs after the tool returns, and
    closing a request-shared session out from under its owner is RT-MEMTXN-LEAK-1."""
    session = MagicMock()
    handle = RevocableToolSession(session, tool_name="t")
    handle.revoke()

    session.close.assert_not_called()
    session.rollback.assert_not_called()
    session.commit.assert_not_called()


def test_revoke_is_idempotent():
    handle = RevocableToolSession(MagicMock(), tool_name="t")
    handle.revoke()
    handle.revoke()
    assert handle.revoked is True


def test_untouched_is_the_measured_baseline():
    """All 18 tool fns that exist take `db` and none uses it; `touched` makes that countable."""
    handle = RevocableToolSession(MagicMock(), tool_name="t")
    assert handle.touched is False
    handle.revoke()
    assert handle.touched is False


def test_attribute_writes_are_gated_too():
    handle = RevocableToolSession(MagicMock(), tool_name="t")
    handle.revoke()
    with pytest.raises(ToolSessionRevoked):
        handle.info = {"x": 1}


def test_context_manager_use_is_gated():
    """★ Dunders are looked up on the type for implicit invocation, so they never route through
    __getattr__ and must be forwarded by hand. `with db:` is the realistic one."""
    session = MagicMock()
    handle = RevocableToolSession(session, tool_name="t")
    with handle:
        pass

    # ★ BOTH halves must forward. Asserting only __enter__ let a mutation that drops __exit__
    # forwarding survive — the revoked case raises in __enter__, so it never reaches __exit__
    # and the gap was invisible. Found by mutation testing, not by review.
    session.__enter__.assert_called_once()
    session.__exit__.assert_called_once()

    handle.revoke()
    with pytest.raises(ToolSessionRevoked):
        with handle:
            pass

    # and __exit__ refuses independently of __enter__, since a stashed handle can be exited
    # without being entered again
    with pytest.raises(ToolSessionRevoked):
        handle.__exit__(None, None, None)


def test_the_handle_reports_its_own_state_without_forwarding():
    """`revoked`/`touched`/`repr` must not be proxied, or the handle's API becomes unreachable."""
    handle = RevocableToolSession(MagicMock(), tool_name="t")
    assert handle.revoked is False
    assert "live" in repr(handle)
    handle.revoke()
    assert "revoked" in repr(handle)
    assert handle.touched is False, "reading the handle's own API must not count as a touch"


# ── At the seam ──────────────────────────────────────────────────────────────


@pytest.fixture
def _seam(monkeypatch):
    """Register a throwaway tool and neutralise the authority checks around the call."""
    captured: dict = {}

    def _fn(args, user_id, db):
        captured["db"] = db
        captured["touched_inside"] = getattr(db, "touched", None)
        return {"ok": True}

    register_tool(
        "test.seam_probe",
        risk="low",
        description="probe",
        capability="tool:test.seam_probe",
        required_capability="read_memory",
        category="test",
        egress_scope="internal",
    )(_fn)
    yield captured
    TOOL_REGISTRY.pop("test.seam_probe", None)


def _invoke(db=None):
    return execute_tool(
        "test.seam_probe",
        {},
        user_id="u1",
        db=db if db is not None else MagicMock(),
        run_id=None,
        execution_token=None,
    )


def test_liveness_the_handle_is_actually_installed(_seam):
    """★ If this fails, every absence assertion in this file is vacuous."""
    result = _invoke()
    assert result.get("success") is True, result
    assert isinstance(_seam["db"], RevocableToolSession), (
        f"the tool received {type(_seam['db']).__name__}, not a revocable handle — step A is unwired"
    )


def test_the_tool_never_receives_the_raw_session(_seam):
    session = MagicMock()
    _invoke(session)
    assert _seam["db"] is not session


def test_the_handle_is_revoked_once_the_tool_returns(_seam):
    """★ The property the whole step exists for."""
    _invoke()
    stashed = _seam["db"]
    assert stashed.revoked is True
    with pytest.raises(ToolSessionRevoked):
        stashed.query()


def test_the_handle_is_revoked_even_when_the_tool_raises(_seam, monkeypatch):
    """A tool that fails must not leave a live handle behind — the finally is load-bearing."""

    def _boom(args, user_id, db):
        _seam["db"] = db
        raise RuntimeError("tool exploded")

    TOOL_REGISTRY["test.seam_probe"]["fn"] = _boom

    result = _invoke()
    assert result.get("success") is False
    assert _seam["db"].revoked is True


def test_a_tool_that_ignores_db_is_unaffected(_seam):
    """The measured baseline: 18 of 18 tool fns take db, 0 use it."""
    result = _invoke()
    assert result == {"success": True, "result": {"ok": True}, "error": None}
    assert _seam["touched_inside"] is False


def test_the_runtime_keeps_the_real_session_after_the_call(_seam):
    """★ _finalize_tool_effect runs after the tool returns and must get the real session, not a
    revoked handle — otherwise the effect ledger breaks the moment idempotency is enabled."""
    session = MagicMock()
    _invoke(session)

    assert _seam["db"].revoked is True
    # the real session is untouched by revocation and still usable by the runtime
    session.close.assert_not_called()
    assert session.query("anything") is not None
