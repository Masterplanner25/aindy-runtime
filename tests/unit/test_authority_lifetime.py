"""AUTHORITY-LIFETIME-1 — a token is valid while its run is live, not while the clock says so
(DEC-056..059).

`TOKEN_TTL_HOURS = 24`; `verify_token` was a stateless HMAC + expiry check, and a token minted
for a run that finished in 90 seconds was accepted for the rest of the day. The runtime already
had a stateful, run-keyed, hot-path read of exactly the right shape — `CANCEL-REACH-1`'s
`is_run_cancelled` (own session, cached per run for 2 s, fails OPEN) — for ONE terminal value of
the same column. This widens that read to every terminal status, at the same two sites and no
third, and makes a terminal answer sticky for the process lifetime.

★ Asserted on the tool / handler NOT running, never only on the envelope — "refused" and "ran
and then reported a refusal" produce the same envelope and opposite outcomes.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

import AINDY.kernel.syscall_dispatcher as syscall_dispatcher
import AINDY.kernel.syscall_registry as syscall_registry
from AINDY.agents import capability_service as cs
from AINDY.agents import tool_registry as tr
from AINDY.kernel import cancellation

pytestmark = pytest.mark.runtime_only

_TOOL = "test.lifetime_probe"
_SYSCALL = "sys.v1.test.lifetime"


@pytest.fixture(autouse=True)
def _clean():
    cancellation.reset_cancellation_cache()
    yield
    cancellation.reset_cancellation_cache()
    tr.TOOL_REGISTRY.pop(_TOOL, None)
    syscall_registry.SYSCALL_REGISTRY.pop(_SYSCALL, None)


def _count(status: str, surface: str) -> float:
    from AINDY.platform_layer.metrics import authority_lifetime_refusals_total

    return authority_lifetime_refusals_total.labels(status=status, surface=surface)._value.get()


# ── The tool seam ─────────────────────────────────────────────────────────────


def _tool(ran: list):
    tr.TOOL_REGISTRY[_TOOL] = {
        "fn": lambda args, user_id, db: ran.append(1) or {"ok": True},
        "risk": "low", "description": "t", "capability": "read_memory",
        "required_capability": "read_memory", "category": "test", "egress_scope": "internal",
    }


def _execute(run_id: str, *, status, validated: list | None = None):
    """Drive the REAL `check_tool_capability` with the run's status stubbed at the read.

    `validate_token` is spied (and made to pass) so the ORDER is observable: a terminal run
    must refuse BEFORE the HMAC check, i.e. without ever calling it.
    """
    def _validate(**kw):
        if validated is not None:
            validated.append(kw["run_id"])
        return {
            "ok": True, "error": None, "granted_tools": [_TOOL],
            "allowed_capabilities": ["read_memory"], "agent_type": "default",
        }

    reads = status if callable(status) else (lambda _r: status)
    with patch.object(tr, "_ensure_tools_loaded", lambda: None), patch.object(
        tr, "queue_system_event", lambda **k: None
    ), patch.object(cs, "validate_token", _validate), patch.object(
        cs, "_get_capabilities_for_tool", return_value=["read_memory"]
    ), patch.object(cs, "_get_capabilities_for_agent", return_value=[]), patch.object(
        cancellation, "_read_status", side_effect=reads
    ):
        return tr.execute_tool(_TOOL, {}, "u", MagicMock(), run_id=run_id, execution_token={"t": 1})


def test_a_completed_run_is_refused_at_the_tool_seam():
    """★ The entry's finding: authority ends with the run."""
    ran: list = []
    _tool(ran)
    run_id = str(uuid.uuid4())
    before = _count("completed", "tool")
    validated: list = []

    result = _execute(run_id, status="completed", validated=validated)

    assert ran == [], "the tool executed for a run that had already completed"
    assert result["success"] is False
    assert result["failure_class"] == "permission"
    assert run_id in result["error"] and "completed" in result["error"]
    assert "authority ended with the run" in result["error"]
    assert validated == [], "the HMAC check ran before the lifetime check — order is the point"
    assert _count("completed", "tool") == before + 1


@pytest.mark.parametrize("status", ["failed", "verify_failed", "refused"])
def test_every_terminal_status_ends_authority(status):
    ran: list = []
    _tool(ran)

    result = _execute(str(uuid.uuid4()), status=status)

    assert ran == [] and result["success"] is False
    assert result["failure_class"] == "permission"


def test_a_live_run_still_runs():
    """Liveness control: a check that refused unconditionally would pass everything above."""
    ran: list = []
    _tool(ran)

    result = _execute(str(uuid.uuid4()), status="executing")

    assert ran == [1] and result["success"] is True


def test_a_waiting_run_keeps_its_authority():
    """DEC-059 — OpenHands' pause-null is DECLINED. A parked run resumes with the same token;
    revoking on wait would make every resume a re-mint, which needs a grant path `DEC-016`
    denied the authority WAIT gate."""
    ran: list = []
    _tool(ran)

    assert _execute(str(uuid.uuid4()), status="waiting")["success"] is True
    assert ran == [1]


def test_a_resumed_run_runs_with_the_same_token():
    ran: list = []
    _tool(ran)
    run_id = str(uuid.uuid4())
    statuses = iter(["waiting", "executing"])

    first = _execute(run_id, status=lambda _r: next(statuses))
    cancellation.reset_cancellation_cache()  # past the 2 s window without sleeping
    second = _execute(run_id, status=lambda _r: next(statuses))

    assert first["success"] and second["success"] and ran == [1, 1]


def test_a_cancelled_run_keeps_the_cancel_envelope():
    """`cancelled` is one terminal value; refusing it here (earlier, before the ledger reserves
    anything) must not change what `CANCEL-REACH-1` promised the caller."""
    ran: list = []
    _tool(ran)

    result = _execute(str(uuid.uuid4()), status="cancelled")

    assert ran == []
    assert result["failure_class"] == "cancelled"
    assert result.get("cancelled") is True
    assert "cancelled" in result["error"]


# ── The read: sticky terminal, fail-open ─────────────────────────────────────


def test_a_terminal_answer_is_sticky_for_the_process():
    """DEC-057 — the negative cache IS the cache. After one terminal read the row is never
    re-read: a run cannot leave a terminal state, so re-asking is pure cost. Mutating the row
    back to `executing` is impossible in production — that is the point of doing it here."""
    run_id = str(uuid.uuid4())
    statuses = iter(["completed", "executing", "executing"])
    with patch.object(cancellation, "_read_status", side_effect=lambda _r: next(statuses)) as read:
        assert cancellation.run_terminal_status(run_id) == "completed"
        assert cancellation.run_terminal_status(run_id) == "completed"
        assert cancellation.run_terminal_status(run_id, ttl_seconds=0.0) == "completed"
    assert read.call_count == 1


def test_an_unreadable_status_fails_open():
    """DEC-058 — as cancel does, and for the same reason: an aborted effect is not recoverable
    by retrying the check; the token's expiry still bounds the window."""
    ran: list = []
    _tool(ran)

    def _boom(_r):
        raise RuntimeError("db down")

    result = _execute(str(uuid.uuid4()), status=_boom)

    assert ran == [1] and result["success"] is True


def test_a_missing_run_is_not_terminal():
    with patch.object(cancellation, "_read_status", return_value=None):
        assert cancellation.run_terminal_status(str(uuid.uuid4())) is None
    assert cancellation.run_terminal_status(None) is None


def test_is_run_cancelled_is_the_same_read():
    """Pinned: `is_run_cancelled` is `run_terminal_status(...) == "cancelled"` — one read, one
    cache, and a cancelled answer stays sticky exactly as before."""
    run_id = str(uuid.uuid4())
    with patch.object(cancellation, "_read_status", return_value="cancelled") as read:
        assert cancellation.is_run_cancelled(run_id) is True
        assert cancellation.run_terminal_status(run_id) == "cancelled"
        assert cancellation.is_run_cancelled(run_id) is True
    assert read.call_count == 1
    with patch.object(cancellation, "_read_status", return_value="completed"):
        assert cancellation.is_run_cancelled(str(uuid.uuid4())) is False


def test_the_terminal_set_is_derived_from_the_status_vocabulary():
    """A hand-written set here would be variant 12; the set is the kernel's own."""
    from AINDY.kernel.condition_codes import AGENT_TERMINAL_STATUSES

    assert AGENT_TERMINAL_STATUSES <= cancellation.TERMINAL_RUN_STATUSES
    assert "waiting" not in cancellation.TERMINAL_RUN_STATUSES
    assert "executing" not in cancellation.TERMINAL_RUN_STATUSES


# ── The dispatcher's agent gate — the second site, and no third ──────────────


class _OkRm:
    def check_quota(self, execution_unit_id):
        return True, None

    def record_usage(self, execution_unit_id, usage):
        return None


def _dispatcher(monkeypatch, handler_calls: list):
    syscall_registry.SYSCALL_REGISTRY[_SYSCALL] = syscall_registry.SyscallEntry(
        handler=lambda payload, context: handler_calls.append(1) or {"ok": True},
        capability="test.capability",
    )
    d = syscall_dispatcher.SyscallDispatcher()
    d._emit_syscall_event = lambda *a, **kw: None
    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())
    monkeypatch.setattr(syscall_dispatcher, "_syscall_idempotency_enabled", lambda: False)
    return d


def _ctx():
    return syscall_registry.SyscallContext(
        execution_unit_id=str(uuid.uuid4()), user_id="u", capabilities=["test.capability"], trace_id="t"
    )


def test_the_dispatcher_refuses_a_completed_runs_syscall(monkeypatch):
    from AINDY.platform_layer.token_meter import llm_attribution_scope

    calls: list = []
    d = _dispatcher(monkeypatch, calls)
    run_id = str(uuid.uuid4())
    before = _count("completed", "syscall")
    with patch.object(cancellation, "_read_status", return_value="completed"), llm_attribution_scope(
        tenant_id="t", run_id=run_id
    ):
        result = d.dispatch(_SYSCALL, {}, _ctx())

    assert calls == [], "the handler ran for a completed run"
    assert result["status"] == "error"
    assert result["failure_class"] == "permission"
    assert run_id in result["error"] and "authority ended with the run" in result["error"]
    assert _count("completed", "syscall") == before + 1


def test_the_dispatcher_keeps_the_cancel_envelope(monkeypatch):
    from AINDY.platform_layer.token_meter import llm_attribution_scope

    calls: list = []
    d = _dispatcher(monkeypatch, calls)
    with patch.object(cancellation, "_read_status", return_value="cancelled"), llm_attribution_scope(
        tenant_id="t", run_id=str(uuid.uuid4())
    ):
        result = d.dispatch(_SYSCALL, {}, _ctx())

    assert calls == []
    assert result["failure_class"] == "cancelled" and "cancelled" in result["error"]


def test_the_dispatcher_runs_a_live_runs_syscall(monkeypatch):
    from AINDY.platform_layer.token_meter import llm_attribution_scope

    calls: list = []
    d = _dispatcher(monkeypatch, calls)
    with patch.object(cancellation, "_read_status", return_value="executing"), llm_attribution_scope(
        tenant_id="t", run_id=str(uuid.uuid4())
    ):
        result = d.dispatch(_SYSCALL, {}, _ctx())

    assert calls == [1] and result["status"] == "success"


def test_the_token_itself_stays_stateless():
    """DEC-056 — the two sites and no third. `verify_token` / `validate_token` do not read the
    run: the planner path and any caller without a run keep the pure HMAC check."""
    import ast
    import inspect

    src = inspect.getsource(cs)
    tree = ast.parse(src)
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in ("verify_token", "validate_token")):
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)} | {
            n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)
        }
        assert not names & {"run_terminal_status", "is_run_cancelled", "_read_status"}, (
            f"{fn.name} reads the run — the token was meant to stay stateless"
        )
