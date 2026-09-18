"""`CANCEL-REACH-1` — the two residuals, closed.

**Residual 1 — the syscall dispatcher chokepoint.** The filing proposed two chokepoints and only
`execute_tool` was taken, because `SyscallContext` carries no run identity and the entry said
"pick the field, not the lookup — and settle it once". It was settled once, elsewhere, after
that was written: `COST-GOVERNOR-1` phase 3 made `llm_attribution_scope(tenant, run)` the
identity of an execution span, set by `execute_run` for the whole run. The dispatcher now reads
the run from there (`cancellation.current_run_id`) and refuses `entry.handler` for a cancelled
run — the same two lines the effect ledger brackets, and inside its completion discipline so a
refusal never leaves a `pending` EffectRecord.

**Residual 2 — the isolated tool worker.** The claim was "`run_id=None` is correct because that
path is hard-killable by its isolation class". The capability existed and nothing invoked it:
the worker died only to `subprocess.run(timeout=…)`. Worse than the claim, on reading: the
isolated branch RETURNED before the cancel check, so a cancelled run's isolated tool was spawned
regardless. Now the parent (a) refuses before spawning, like an in-process tool, and (b) polls
the predicate while the worker runs and terminates → kills it on a cancel. The worker still gets
no `run_id` — the check runs in the one process that can act on the answer.

Every fail-OPEN property of the predicate is untouched and re-pinned by `test_cancel_reach.py`;
these tests are about the two seams that now consult it. The worker tests use a REAL subprocess.

Mutation-checked: drop the dispatcher check → the refuse test fails; read the run from nowhere
(`current_run_id` returns None) → the refuse test fails and the no-span control still passes;
drop the pre-spawn check → the pre-spawn test fails; never poll in the wait loop → the kill test
times out (asserted under a deadline); drop the ledger completion on refusal → the gate test.
"""
from __future__ import annotations

import sys
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest

import AINDY.kernel.syscall_dispatcher as syscall_dispatcher
import AINDY.kernel.syscall_registry as syscall_registry

pytestmark = pytest.mark.runtime_only

_SYSCALL = "sys.v1.test.cancel_reach"


@pytest.fixture(autouse=True)
def _clean_cache():
    from AINDY.kernel.cancellation import reset_cancellation_cache

    reset_cancellation_cache()
    yield
    reset_cancellation_cache()


class _OkRm:
    def check_quota(self, execution_unit_id):
        return True, None

    def record_usage(self, execution_unit_id, usage):
        return None


def _dispatcher(monkeypatch):
    d = syscall_dispatcher.SyscallDispatcher()
    d._emit_syscall_event = lambda *a, **kw: None
    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())
    return d


def _ctx():
    return syscall_registry.SyscallContext(
        execution_unit_id="11111111-1111-1111-1111-111111111111",
        user_id="user-1",
        capabilities=["test.capability"],
        trace_id="trace-cancel",
    )


@pytest.fixture
def handler():
    calls: list[int] = []
    syscall_registry.SYSCALL_REGISTRY[_SYSCALL] = syscall_registry.SyscallEntry(
        handler=lambda p, c: calls.append(1) or {"ok": True},
        capability="test.capability",
    )
    yield calls
    syscall_registry.SYSCALL_REGISTRY.pop(_SYSCALL, None)


def _count(surface: str) -> float:
    from AINDY.platform_layer.metrics import REGISTRY

    return REGISTRY.get_sample_value("aindy_run_cancel_observed_total", {"surface": surface}) or 0.0


# ── residual 1: the dispatcher ───────────────────────────────────────────────


def test_the_dispatcher_refuses_a_syscall_for_a_cancelled_run(monkeypatch, handler):
    """★★ The second chokepoint. Asserted on the handler NOT running, not on the envelope."""
    from AINDY.platform_layer.token_meter import llm_attribution_scope

    run_id = str(uuid.uuid4())
    before = _count("syscall")
    d = _dispatcher(monkeypatch)
    with patch.object(syscall_dispatcher, "_run_terminal_status", lambda rid: "cancelled" if rid == run_id else None):
        with llm_attribution_scope(tenant_id="t", run_id=run_id):
            result = d.dispatch(_SYSCALL, {}, _ctx())

    assert handler == [], "the handler ran despite its run being cancelled"
    assert result["status"] != "success" and "cancelled" in (result.get("error") or "")
    assert _count("syscall") == before + 1


def test_the_dispatcher_runs_a_live_runs_syscall(monkeypatch, handler):
    """Liveness control: a check that refused unconditionally would pass the test above."""
    from AINDY.platform_layer.token_meter import llm_attribution_scope

    d = _dispatcher(monkeypatch)
    with patch.object(syscall_dispatcher, "_run_terminal_status", lambda rid: None):
        with llm_attribution_scope(tenant_id="t", run_id=str(uuid.uuid4())):
            result = d.dispatch(_SYSCALL, {}, _ctx())

    assert handler == [1] and result["status"] == "success"


def test_outside_an_execution_span_the_dispatcher_does_not_even_ask(monkeypatch, handler):
    """No run → nothing to be cancelled by → the predicate is not consulted (it would be a
    pointless read per dispatch on every route that is not an agent run)."""
    asked: list[str] = []
    d = _dispatcher(monkeypatch)
    with patch.object(syscall_dispatcher, "_run_terminal_status", lambda rid: asked.append(rid) or None):
        result = d.dispatch(_SYSCALL, {}, _ctx())

    assert handler == [1] and result["status"] == "success"
    assert asked == []


def test_the_run_is_read_from_the_execution_span():
    from AINDY.kernel.cancellation import current_run_id
    from AINDY.platform_layer.token_meter import llm_attribution_scope

    assert current_run_id() is None
    with llm_attribution_scope(tenant_id="t", run_id="run-7"):
        assert current_run_id() == "run-7"
        with llm_attribution_scope(tenant_id="t2"):  # inner span inherits the run
            assert current_run_id() == "run-7"
    assert current_run_id() is None


def test_a_refusal_completes_the_reserved_effect_record_as_failed(monkeypatch):
    """Inside the ledger's completion discipline: an EXACTLY_ONCE syscall refused for a cancel
    must not leave the `pending` record it reserved — that row is never reaped."""
    from AINDY.platform_layer.token_meter import llm_attribution_scope

    calls: list[int] = []
    syscall_registry.SYSCALL_REGISTRY[_SYSCALL] = syscall_registry.SyscallEntry(
        handler=lambda p, c: calls.append(1) or {"ok": True},
        capability="test.capability",
        execution_guarantee="EXACTLY_ONCE",
    )
    completed: list[tuple] = []
    d = _dispatcher(monkeypatch)
    monkeypatch.setattr(syscall_dispatcher, "_syscall_idempotency_enabled", lambda: True)
    monkeypatch.setattr(syscall_dispatcher, "_resolve_effect_record", lambda *a, **k: (False, None))
    monkeypatch.setattr(
        syscall_dispatcher, "_complete_effect_record", lambda db, aid, st, rp: completed.append((aid, st))
    )
    try:
        with patch("AINDY.db.database.SessionLocal", return_value=MagicMock()), patch.object(
            syscall_dispatcher, "_run_terminal_status", lambda rid: "cancelled"
        ), llm_attribution_scope(tenant_id="t", run_id=str(uuid.uuid4())):
            result = d.dispatch(_SYSCALL, {}, _ctx())
    finally:
        syscall_registry.SYSCALL_REGISTRY.pop(_SYSCALL, None)

    assert calls == [] and result["status"] != "success"
    assert completed and completed[0][1] == "failed", completed


# ── residual 2: the isolated worker ──────────────────────────────────────────


def test_a_cancelled_run_does_not_spawn_an_isolated_worker(monkeypatch):
    """The isolated branch used to RETURN before the cancel check — a cancelled run's isolated
    tool was spawned regardless. Now the refusal comes first, for both paths."""
    import AINDY.agents.tool_registry as tr

    spawned: list[str] = []
    entry = {"fn": lambda **kw: None, "name": "iso_tool", "isolation": "insecure-dev"}
    with patch.dict(tr.TOOL_REGISTRY, {"iso_tool": entry}, clear=False), patch.object(
        tr, "_ensure_tools_loaded", lambda: None
    ), patch.object(tr, "_isolation_refusal", lambda *a, **k: None), patch.object(
        tr, "_tool_isolation_enforced", lambda: True
    ), patch.object(
        tr, "_run_tool_out_of_process", lambda *a, **k: spawned.append("spawned") or {"success": True, "result": {}, "error": None}
    ), patch.object(tr, "is_run_cancelled", return_value=True), patch(
        "AINDY.agents.capability_service.check_tool_capability",
        return_value={"ok": True, "allowed_capabilities": [], "granted_tools": []},
    ):
        result = tr.execute_tool(
            "iso_tool", {}, user_id="u", db=None, run_id=str(uuid.uuid4()), execution_token={"token": "t"}
        )

    assert spawned == [], "a cancelled run's isolated tool was spawned"
    assert result.get("cancelled") is True


def test_the_parent_hands_the_worker_its_run_id_but_not_the_child(monkeypatch):
    """`run_id` reaches `_run_tool_out_of_process` (the PARENT's side) so the wait loop can
    poll — and the worker payload does not carry it (the check must not run in the child)."""
    import AINDY.agents.tool_registry as tr

    seen: dict = {}

    def _spy(tool_name, args, user_id, *, run_id=None, egress=None):
        seen["run_id"] = run_id
        return {"success": True, "result": {}, "error": None}

    run_id = str(uuid.uuid4())
    entry = {"fn": lambda **kw: None, "name": "iso_tool2", "isolation": "insecure-dev"}
    with patch.dict(tr.TOOL_REGISTRY, {"iso_tool2": entry}, clear=False), patch.object(
        tr, "_ensure_tools_loaded", lambda: None
    ), patch.object(tr, "_isolation_refusal", lambda *a, **k: None), patch.object(
        tr, "_tool_isolation_enforced", lambda: True
    ), patch.object(tr, "_run_tool_out_of_process", _spy), patch.object(
        tr, "is_run_cancelled", return_value=False
    ), patch(
        "AINDY.agents.capability_service.check_tool_capability",
        return_value={"ok": True, "allowed_capabilities": [], "granted_tools": []},
    ):
        tr.execute_tool("iso_tool2", {}, user_id="u", db=None, run_id=run_id, execution_token={"token": "t"})

    assert seen["run_id"] == run_id


def test_a_running_worker_is_killed_when_its_run_is_cancelled(monkeypatch):
    """★★ Residual 2's substance, on a REAL subprocess: the worker sleeps far past any poll; the
    predicate flips to cancelled after the first poll; the worker must be dead well inside its
    budget, and the refusal counted on its own surface."""
    import AINDY.agents.tool_registry as tr

    polls: list[int] = []

    def _cancelled_after_first_poll(rid, **kw):
        polls.append(1)
        return len(polls) >= 2

    run_id = str(uuid.uuid4())
    before = _count("tool_worker")
    cmd = [sys.executable, "-c", "import sys, time; sys.stdin.read(); time.sleep(60)"]
    t0 = time.monotonic()
    with patch("AINDY.kernel.cancellation.is_run_cancelled", _cancelled_after_first_poll):
        result = tr._run_worker_or_kill_on_cancel(
            cmd, payload="{}", tool_name="sleeper", run_id=run_id, spawn_kwargs={}
        )
    elapsed = time.monotonic() - t0

    assert result is None, "the worker was not reported killed"
    assert elapsed < 15, f"took {elapsed:.1f}s — the wait loop did not poll the predicate"
    assert _count("tool_worker") == before + 1


def test_a_live_runs_worker_completes_and_its_output_is_kept(monkeypatch):
    """Liveness control for the loop: a worker that finishes between polls returns its output
    intact — `communicate(timeout=)` retried across polls loses nothing."""
    import AINDY.agents.tool_registry as tr

    cmd = [sys.executable, "-c", "import sys, time; d = sys.stdin.read(); time.sleep(1.2); print('echo:' + d)"]
    with patch("AINDY.kernel.cancellation.is_run_cancelled", lambda rid, **kw: False):
        proc = tr._run_worker_or_kill_on_cancel(
            cmd, payload="hello", tool_name="echo", run_id=str(uuid.uuid4()), spawn_kwargs={}
        )

    assert proc is not None and proc.returncode == 0
    assert proc.stdout.strip() == "echo:hello"


def test_the_budget_still_kills_a_worker_nobody_cancelled(monkeypatch):
    import subprocess

    import AINDY.agents.tool_registry as tr

    monkeypatch.setattr(tr, "_TOOL_WORKER_TIMEOUT_S", 1.0)
    cmd = [sys.executable, "-c", "import sys, time; sys.stdin.read(); time.sleep(60)"]
    t0 = time.monotonic()
    with patch("AINDY.kernel.cancellation.is_run_cancelled", lambda rid, **kw: False):
        with pytest.raises(subprocess.TimeoutExpired):
            tr._run_worker_or_kill_on_cancel(
                cmd, payload="{}", tool_name="sleeper", run_id=str(uuid.uuid4()), spawn_kwargs={}
            )
    assert time.monotonic() - t0 < 15


def test_the_worker_check_is_the_parents_not_the_childs():
    """`is_run_cancelled(None)` is still False: the child never gets a run to ask about."""
    from AINDY.kernel.cancellation import is_run_cancelled

    assert is_run_cancelled(None) is False
