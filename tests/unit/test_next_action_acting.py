"""INFINITY-RUNTIME-1 Deliverable C — act on a post-execution NextAction (opt-in).

Covers the bounded, default-off acting half: gating (flag, verb, source, objective),
the net-new chain-depth cap, the admission cap, the dispatch payload, the chain-depth
walk, and the follow-up job's approval-respecting create→execute. No database — the
DB is a light fake and the dispatch primitives are monkeypatched.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from AINDY.core import next_action_dispatch as nad
from AINDY.core.next_action import TRIGGER_EXECUTION, make_next_action

pytestmark = pytest.mark.runtime_only


@pytest.fixture
def capture_submit(monkeypatch):
    """Capture submit_async_job calls; count_active_executions defaults to 0."""
    calls: list[dict] = []
    monkeypatch.setattr(
        "AINDY.platform_layer.async_job_service.submit_async_job",
        lambda **kw: calls.append(kw) or "job-id",
    )
    monkeypatch.setattr(
        "AINDY.agents.autonomous_controller.count_active_executions",
        lambda db, user_id=None: 0,
    )
    return calls


def _enable(monkeypatch, *, max_chain=3, max_active=1):
    monkeypatch.setattr(nad, "_acting_settings", lambda: (True, max_chain, max_active))


def _trigger(objective="do the next thing", source="completion_hook"):
    return make_next_action(TRIGGER_EXECUTION, args={"objective": objective}, source=source)


def _run(run_id="run-1", parent_run_id=None):
    return SimpleNamespace(id=run_id, parent_run_id=parent_run_id, trace_id="trace-1")


# --- gating ------------------------------------------------------------------


def test_no_dispatch_when_disabled(monkeypatch, capture_submit):
    monkeypatch.setattr(nad, "_acting_settings", lambda: (False, 3, 1))
    monkeypatch.setattr(nad, "_completing_run_depth", lambda *a, **k: 0)
    assert nad.maybe_act_on_next_action(_run(), _trigger(), db=object(), user_id="u1") is False
    assert capture_submit == []


def test_no_dispatch_for_non_trigger_verb(monkeypatch, capture_submit):
    _enable(monkeypatch)
    monkeypatch.setattr(nad, "_completing_run_depth", lambda *a, **k: 0)
    done = make_next_action("done", source="completion_hook")
    assert nad.maybe_act_on_next_action(_run(), done, db=object(), user_id="u1") is False
    assert capture_submit == []


def test_no_dispatch_on_runtime_default_source(monkeypatch, capture_submit):
    """Belt-and-suspenders: never act on a runtime-invented decision."""
    _enable(monkeypatch)
    monkeypatch.setattr(nad, "_completing_run_depth", lambda *a, **k: 0)
    action = _trigger(source="runtime_default")
    assert nad.maybe_act_on_next_action(_run(), action, db=object(), user_id="u1") is False
    assert capture_submit == []


def test_no_dispatch_without_objective(monkeypatch, capture_submit):
    _enable(monkeypatch)
    monkeypatch.setattr(nad, "_completing_run_depth", lambda *a, **k: 0)
    action = make_next_action(TRIGGER_EXECUTION, args={}, source="completion_hook")
    assert nad.maybe_act_on_next_action(_run(), action, db=object(), user_id="u1") is False
    assert capture_submit == []


def test_no_dispatch_none_action(monkeypatch, capture_submit):
    _enable(monkeypatch)
    assert nad.maybe_act_on_next_action(_run(), None, db=object(), user_id="u1") is False
    assert capture_submit == []


# --- net-new chain-depth cap -------------------------------------------------


def test_no_dispatch_at_chain_depth_cap(monkeypatch, capture_submit):
    _enable(monkeypatch, max_chain=3)
    monkeypatch.setattr(nad, "_completing_run_depth", lambda *a, **k: 3)  # == cap
    assert nad.maybe_act_on_next_action(_run(), _trigger(), db=object(), user_id="u1") is False
    assert capture_submit == []


def test_dispatch_just_under_chain_cap(monkeypatch, capture_submit):
    _enable(monkeypatch, max_chain=3)
    monkeypatch.setattr(nad, "_completing_run_depth", lambda *a, **k: 2)  # < cap
    assert nad.maybe_act_on_next_action(_run(), _trigger(), db=object(), user_id="u1") is True
    assert len(capture_submit) == 1
    assert capture_submit[0]["payload"]["chain_depth"] == 3  # depth + 1


# --- admission cap -----------------------------------------------------------


def test_no_dispatch_when_active_cap_hit(monkeypatch, capture_submit):
    _enable(monkeypatch, max_active=1)
    monkeypatch.setattr(nad, "_completing_run_depth", lambda *a, **k: 0)
    monkeypatch.setattr(
        "AINDY.agents.autonomous_controller.count_active_executions",
        lambda db, user_id=None: 1,  # at cap
    )
    assert nad.maybe_act_on_next_action(_run(), _trigger(), db=object(), user_id="u1") is False
    assert capture_submit == []


def test_active_cap_zero_disables_admission_check(monkeypatch, capture_submit):
    _enable(monkeypatch, max_active=0)
    monkeypatch.setattr(nad, "_completing_run_depth", lambda *a, **k: 0)
    monkeypatch.setattr(
        "AINDY.agents.autonomous_controller.count_active_executions",
        lambda db, user_id=None: 999,
    )
    assert nad.maybe_act_on_next_action(_run(), _trigger(), db=object(), user_id="u1") is True
    assert len(capture_submit) == 1


# --- happy path payload ------------------------------------------------------


def test_dispatch_payload_shape(monkeypatch, capture_submit):
    _enable(monkeypatch)
    monkeypatch.setattr(nad, "_completing_run_depth", lambda *a, **k: 0)
    action = _trigger(objective="summarize the report")
    assert nad.maybe_act_on_next_action(_run("parent-9"), action, db=object(), user_id="u7") is True
    kw = capture_submit[0]
    assert kw["task_name"] == nad.FOLLOWUP_JOB_NAME
    assert kw["source"] == "next_action"
    assert kw["user_id"] == "u7"
    assert kw["payload"]["objective"] == "summarize the report"
    assert kw["payload"]["parent_run_id"] == "parent-9"
    assert kw["payload"]["chain_depth"] == 1


# --- chain-depth walk --------------------------------------------------------


class _FakeDB:
    """Minimal query().filter(AgentRun.id == x).first() over a dict of runs."""

    def __init__(self, runs_by_id):
        self.runs_by_id = runs_by_id
        self._pending = None

    def query(self, _model):
        return self

    def filter(self, expr):
        # expr is `AgentRun.id == <value>` — pull the bound value off the RHS.
        self._pending = expr.right.value
        return self

    def first(self):
        return self.runs_by_id.get(self._pending)


def test_completing_run_depth_walks_parents():
    root = _run("root", parent_run_id=None)
    child = _run("child", parent_run_id="root")
    grandchild = _run("grand", parent_run_id="child")
    db = _FakeDB({"root": root, "child": child, "grand": grandchild})
    assert nad._completing_run_depth(root, db, cap=5) == 0
    assert nad._completing_run_depth(child, db, cap=5) == 1
    assert nad._completing_run_depth(grandchild, db, cap=5) == 2


def test_completing_run_depth_is_hop_bounded():
    # Self-cycle must not loop forever.
    looped = _run("a", parent_run_id="a")
    db = _FakeDB({"a": looped})
    assert nad._completing_run_depth(looped, db, cap=3) <= 4


# --- follow-up job -----------------------------------------------------------


def _get_job():
    from AINDY.platform_layer import async_job_service as ajs

    return ajs._JOB_REGISTRY[nad.FOLLOWUP_JOB_NAME]


def test_followup_job_executes_when_auto_approved(monkeypatch):
    executed: list[dict] = []
    monkeypatch.setattr(
        "AINDY.agents.agent_runtime.create_run",
        lambda **kw: {"run_id": "new-1", "status": "approved"},
    )
    monkeypatch.setattr(
        "AINDY.agents.agent_runtime.execute_run",
        lambda **kw: executed.append(kw) or {"status": "completed"},
    )
    monkeypatch.setattr(nad, "_link_parent", lambda *a, **k: None)

    out = _get_job()({"objective": "go", "user_id": "u1", "parent_run_id": "p1"}, db=object())
    assert out["run_id"] == "new-1"
    assert out["status"] == "completed"
    assert len(executed) == 1 and executed[0]["run_id"] == "new-1"


def test_followup_job_respects_pending_approval(monkeypatch):
    """A high-risk / untrusted follow-up is NOT force-executed — human approval wins."""
    executed: list[dict] = []
    monkeypatch.setattr(
        "AINDY.agents.agent_runtime.create_run",
        lambda **kw: {"run_id": "new-2", "status": "pending_approval"},
    )
    monkeypatch.setattr(
        "AINDY.agents.agent_runtime.execute_run",
        lambda **kw: executed.append(kw) or {"status": "completed"},
    )
    monkeypatch.setattr(nad, "_link_parent", lambda *a, **k: None)

    out = _get_job()({"objective": "go", "user_id": "u1", "parent_run_id": "p1"}, db=object())
    assert out["status"] == "pending_approval"
    assert executed == []  # never executed


def test_followup_job_handles_create_failure(monkeypatch):
    monkeypatch.setattr("AINDY.agents.agent_runtime.create_run", lambda **kw: None)
    out = _get_job()({"objective": "go", "user_id": "u1", "parent_run_id": "p1"}, db=object())
    assert out["error"] == "create_run_failed"


def test_followup_job_no_objective_short_circuits():
    out = _get_job()({"objective": "", "user_id": "u1"}, db=object())
    assert out["error"] == "no_objective"
