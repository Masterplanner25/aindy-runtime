"""RECOVERY-GRANULARITY-1 — a step's result is durable before the segment's checkpoint
(DEC-063..066).

On the `nodus_vm` backend a segment runs as ONE guest script; the parent wrote the `AgentStep`
rows in a batch only when the script returned, and crash continuation restarted a partial
segment from its FIRST step — every LLM call re-issued. Now the worker seam (`run_agent_tool`,
the `call_tool` host function, which already holds a session) writes the row as each step
completes, keyed on the plan's STEP INDEX (the compiler's third `call_tool` argument — the retry
loop lives inside the guest, so a call ordinal would key attempt 2 of step 3 as step 4); on a
CONTINUED run a `success` row for `(run_id, step_index)` is replayed and the tool does not run.

★ Rows are read through a SEPARATE session on a private engine: the claim is durability before
the script returns, and the shared fixture cannot tell a flush from a commit (variant 15).
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from AINDY.runtime import nodus_worker
from AINDY.runtime.nodus_worker import run_agent_tool

pytestmark = pytest.mark.runtime_only

_TOKEN = {"sig": "x"}


@pytest.fixture
def factory(tmp_path, monkeypatch):
    from tests.fixtures.db import build_private_engine

    engine = build_private_engine(tmp_path / "steps.db")
    f = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=engine)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", f)
    try:
        yield f
    finally:
        engine.dispose()


def _rows(factory, run_id):
    from AINDY.db.models import AgentStep
    from AINDY.runtime.nodus_adapter import _db_run_id

    s = factory()
    try:
        return sorted(
            s.query(AgentStep).filter(AgentStep.run_id == _db_run_id(run_id)).all(), key=lambda r: r.step_index
        )
    finally:
        s.close()


def _seed_success(factory, run_id, step_index, result):
    from datetime import datetime, timezone

    from AINDY.db.models import AgentStep
    from AINDY.runtime.nodus_adapter import _db_run_id

    s = factory()
    try:
        s.add(AgentStep(run_id=_db_run_id(run_id), step_index=step_index, tool_name="t", status="success",
                        result=result, executed_at=datetime.now(timezone.utc)))
        s.commit()
    finally:
        s.close()


class _Tool:
    """A stand-in for `execute_tool`: records calls, answers from a script of outcomes."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list = []

    def __call__(self, *, tool_name, args, user_id, db, run_id, execution_token, step_index=None):
        self.calls.append(tool_name)
        outcome = self.outcomes.pop(0) if self.outcomes else {"success": True, "result": {"live": True}, "error": None}
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _call(tool, factory, run_id, *, step_index=None, continuation=False, name="t"):
    with patch("AINDY.agents.tool_registry.execute_tool", tool):
        return run_agent_tool(
            name, {"k": 1}, user_id="u", run_id=run_id, execution_token=_TOKEN,
            session_factory=factory, step_index=step_index, continuation=continuation,
        )


# ── DEC-063 / DEC-064: the seam writes the row, on agent_steps, as the step completes ───


def test_a_completed_step_is_durable_before_the_script_returns(factory):
    run_id = str(uuid.uuid4())
    tool = _Tool({"success": True, "result": {"n": 1}, "error": None})

    result = _call(tool, factory, run_id, step_index=0)

    assert result["success"] is True and "replayed" not in result
    rows = _rows(factory, run_id)
    assert len(rows) == 1 and rows[0].step_index == 0 and rows[0].status == "success"
    assert rows[0].result == {"n": 1} and rows[0].tool_name == "t" and rows[0].tool_args == {"k": 1}


def test_a_failed_attempt_writes_a_failed_row(factory):
    run_id = str(uuid.uuid4())
    tool = _Tool({"success": False, "result": None, "error": "boom", "failure_class": "transient"})

    result = _call(tool, factory, run_id, step_index=0)

    assert result["success"] is False
    (row,) = _rows(factory, run_id)
    assert row.status == "failed" and row.error_message == "boom"


def test_the_seams_own_exception_writes_a_failed_row(factory):
    run_id = str(uuid.uuid4())
    tool = _Tool(RuntimeError("seam blew up"))

    result = _call(tool, factory, run_id, step_index=2)

    assert result["success"] is False
    (row,) = _rows(factory, run_id)
    assert (row.step_index, row.status) == (2, "failed") and "seam blew up" in row.error_message


def test_without_a_step_index_nothing_is_written(factory):
    """A hand-written guest script calls `call_tool(name, args)` — unkeyed, unrecorded, unchanged."""
    run_id = str(uuid.uuid4())
    tool = _Tool()

    result = _call(tool, factory, run_id)

    assert result["success"] is True
    assert _rows(factory, run_id) == []


# ── DEC-065: identity is the plan's step index, not a call ordinal ───────────


def test_a_retry_overwrites_the_same_row(factory):
    """★ The mutation that proves §3: attempt 1 fails, attempt 2 succeeds — ONE row, `success`.
    Keyed on a call ordinal there would be two rows and the second would be 'step 1'."""
    run_id = str(uuid.uuid4())
    tool = _Tool(
        {"success": False, "result": None, "error": "flaky", "failure_class": "transient"},
        {"success": True, "result": {"ok": 1}, "error": None},
    )

    first = _call(tool, factory, run_id, step_index=0)
    second = _call(tool, factory, run_id, step_index=0)

    assert first["success"] is False and second["success"] is True
    rows = _rows(factory, run_id)
    assert len(rows) == 1, f"a retry of step 0 produced {len(rows)} rows — keyed on the ordinal, not the index"
    assert rows[0].status == "success" and rows[0].result == {"ok": 1}


def test_the_compiler_emits_the_step_index():
    from AINDY.runtime.agent_plan_compiler import compile_agent_segment

    compiled = compile_agent_segment([{"tool": "a", "args": {}}, {"tool": "b", "args": {}}], base_index=3)
    src = compiled["source"]
    assert 'call_tool(input_payload["__step_3_tool"], input_payload["__step_3_args"], 3)' in src
    assert 'call_tool(input_payload["__step_4_tool"], input_payload["__step_4_args"], 4)' in src
    # the retry loop inside the step passes the SAME index — that is the point of §3
    assert src.count(", 3)") == 2 and src.count(", 4)") == 2


# ── DEC-066: replay only on a continued run, only from a success row ─────────


def test_a_continued_run_replays_a_success_row_without_running_the_tool(factory):
    run_id = str(uuid.uuid4())
    _seed_success(factory, run_id, 0, {"from": "before the crash"})
    tool = _Tool()

    result = _call(tool, factory, run_id, step_index=0, continuation=True)

    assert tool.calls == [], "the tool RAN on a continued run whose step had already succeeded"
    assert result == {"success": True, "result": {"from": "before the crash"}, "error": None, "replayed": True}
    assert len(_rows(factory, run_id)) == 1  # no duplicate row


def test_a_success_row_with_a_null_result_still_replays(factory):
    """Found by a surviving mutation: `None` meant both 'no row' and 'row whose result is None',
    so a tool that returned nothing re-ran on every continuation."""
    run_id = str(uuid.uuid4())
    _seed_success(factory, run_id, 0, None)
    tool = _Tool()

    result = _call(tool, factory, run_id, step_index=0, continuation=True)

    assert tool.calls == []
    assert result["replayed"] is True and result["result"] is None


def test_a_fresh_run_never_replays(factory):
    """A run id is never reused, so a manufactured success row for a fresh run is a bug
    upstream — and even then, a fresh run executes."""
    run_id = str(uuid.uuid4())
    _seed_success(factory, run_id, 0, {"stale": True})
    tool = _Tool({"success": True, "result": {"live": True}, "error": None})

    result = _call(tool, factory, run_id, step_index=0, continuation=False)

    assert tool.calls == ["t"]
    assert result["result"] == {"live": True} and "replayed" not in result


def test_a_continued_run_re_executes_a_failed_step(factory):
    run_id = str(uuid.uuid4())
    tool = _Tool({"success": False, "result": None, "error": "first", "failure_class": "transient"})
    _call(tool, factory, run_id, step_index=0)
    tool.outcomes = [{"success": True, "result": {"second": True}, "error": None}]

    result = _call(tool, factory, run_id, step_index=0, continuation=True)

    assert tool.calls == ["t", "t"]
    assert result["success"] is True and "replayed" not in result
    (row,) = _rows(factory, run_id)
    assert row.status == "success"


def test_a_continued_run_executes_a_step_with_no_row(factory):
    run_id = str(uuid.uuid4())
    tool = _Tool()

    result = _call(tool, factory, run_id, step_index=5, continuation=True)

    assert tool.calls == ["t"] and result["success"] is True
    assert [r.step_index for r in _rows(factory, run_id)] == [5]


# ── The real worker: a compiled two-step plan through run_one ────────────────


def _register_tools(outcomes: dict):
    from AINDY.agents import tool_registry

    names = {}
    for key, outcome in outcomes.items():
        name = f"__rg_{key}_" + uuid.uuid4().hex[:6]
        names[key] = name

        def _impl(args, user_id, db, _o=outcome):
            if isinstance(_o, Exception):
                raise _o
            return _o

        tool_registry.TOOL_REGISTRY[name] = {
            "fn": _impl, "risk": "low", "isolation": None, "execution_guarantee": "AT_LEAST_ONCE",
            "egress_scope": None, "capability": None, "args_schema": None,
        }
    return names


def _run_plan(names, run_id, *, continuation=False):
    from AINDY.runtime.agent_plan_compiler import compile_agent_segment

    compiled = compile_agent_segment([{"tool": names["a"], "args": {}}, {"tool": names["b"], "args": {}}])
    script = compiled["source"] + f"\nrun_workflow({compiled['workflow_name']})\n"
    return nodus_worker.run_one({
        "script": script, "filename": "rg.nd", "state": {}, "memory_context": {},
        "input_payload": compiled["input_payload"],
        "context": {"user_id": str(uuid.uuid4()), "execution_unit_id": "eu-rg", "trace_id": "t-rg",
                    "run_id": run_id, "execution_token": _TOKEN, "continuation": continuation},
    })


def test_through_the_real_worker_the_first_step_is_durable_when_the_second_fails(factory, monkeypatch):
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.agents import tool_registry

    monkeypatch.setattr(
        "AINDY.agents.capability_service.check_tool_capability",
        lambda **kw: {"ok": True, "error": None, "granted_tools": [], "allowed_capabilities": []},
        raising=True,
    )
    names = _register_tools({"a": {"first": True}, "b": RuntimeError("second step fails")})
    run_id = str(uuid.uuid4())
    try:
        result = _run_plan(names, run_id)
    finally:
        for n in names.values():
            tool_registry.TOOL_REGISTRY.pop(n, None)

    # nodus's workflow runner absorbs the step's `throw`; the PARENT reads the failure from the
    # step results (`reconstruct_agent_step_results` → any_failed). What matters here is the row.
    state = result.get("output_state") or {}
    assert state["__step_0_result"]["success"] is True and state["__step_1_result"]["success"] is False, result
    rows = _rows(factory, run_id)
    assert [(r.step_index, r.status) for r in rows] == [(0, "success"), (1, "failed")], (
        "the worker did not write the completed step before the script returned"
    )
    assert rows[0].result == {"first": True}


def test_through_the_real_worker_a_continuation_replays_step_0_and_runs_step_1(factory, monkeypatch):
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.agents import tool_registry

    monkeypatch.setattr(
        "AINDY.agents.capability_service.check_tool_capability",
        lambda **kw: {"ok": True, "error": None, "granted_tools": [], "allowed_capabilities": []},
        raising=True,
    )
    run_id = str(uuid.uuid4())
    _seed_success(factory, run_id, 0, {"first": "recorded"})
    ran: list = []

    def _spy_a(args, user_id, db):
        ran.append("a")
        return {"first": "live"}

    names = _register_tools({"a": None, "b": {"second": True}})
    tool_registry.TOOL_REGISTRY[names["a"]]["fn"] = _spy_a
    try:
        result = _run_plan(names, run_id, continuation=True)
    finally:
        for n in names.values():
            tool_registry.TOOL_REGISTRY.pop(n, None)

    assert result.get("status") == "success", result
    assert ran == [], "step 0 re-executed on a continued run that had already recorded it"
    state = result.get("output_state") or {}
    assert state["__step_0_result"]["replayed"] is True and state["__step_0_result"]["result"] == {"first": "recorded"}
    assert state["__step_1_result"]["success"] is True and "replayed" not in state["__step_1_result"]
    assert [(r.step_index, r.status) for r in _rows(factory, run_id)] == [(0, "success"), (1, "success")]
    # FR-35: a replayed step made no LLM call, so the ledger sees nothing for it
    assert not (result.get("llm_usage") or {}).get("records")


# ── The parent: an upsert that fills only what the worker did not write ──────


def test_reconstruct_carries_the_replayed_flag():
    from AINDY.runtime.nodus_execution_service import reconstruct_agent_step_results

    meta = [{"index": 0, "tool": "a", "result_key": "__step_0_result"}, {"index": 1, "tool": "b", "result_key": "__step_1_result"}]
    results, failed = reconstruct_agent_step_results(meta, {
        "__step_0_result": {"success": True, "result": 1, "error": None, "replayed": True},
        "__step_1_result": {"success": True, "result": 2, "error": None},
    })
    assert failed is False
    assert results[0]["replayed"] is True and "replayed" not in results[1]


def test_count_completed_segments_lands_mid_segment():
    """The docstring used to say completed_steps always lands on a boundary. It may not now —
    and the function still picks the segment that contains the first unfinished step."""
    from AINDY.core.agent_continuation import _count_completed_segments

    segments = [{"tool_steps": [1, 2]}, {"tool_steps": [3, 4, 5]}, {"tool_steps": [6]}]
    assert _count_completed_segments(segments, 2) == 1
    assert _count_completed_segments(segments, 3) == 1  # mid-segment → re-run segment 1 (replay makes it cheap)
    assert _count_completed_segments(segments, 5) == 2
    assert "boundary" not in (_count_completed_segments.__doc__ or "").lower() or "mid-segment" in (_count_completed_segments.__doc__ or "")
