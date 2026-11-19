"""FR-55 (a), DEC-136 — `await_event_for(event_type, schema, seconds)` resumes at its own deadline.

A guest wait could end only on its event or in failure: the deadline failed the run
(`WAIT_TIMEOUT`). `await_event_for` parks like `await_event`, gets its own `wait_deadline`, and when
that passes the resumed script gets `{"timed_out": true, "deadline": …}` back from the same call.
With nothing that will emit the event, it is a durable sleep. (c), a per-workflow timeout, is
answered by the per-wait deadline.

Pinned through the REAL worker and interpreter, the REAL runner and `route_event`:

* the worker reports the wait's seconds; a plain `await_event` reports none and keeps the
  flow-level deadline with no record (nothing changes for it);
* end to end: the run's deadline comes from the wait, the job delivers the marker at it, the
  resumed script reads `timed_out`, and a TYPED wait still gets its marker (runtime-authored, so
  it skips the resume schema that would refuse it);
* a run that already holds a payload is re-woken, never overwritten;
* none of the three scans that fail a waiting run takes a deadline-resume run, and each still
  fails a plain one (the liveness control); the stuck-run scan leaves a wait whose own deadline
  is still ahead.

Mutation-checked: drop the worker registration; drop the runner's own deadline; drop the
schema bypass; drop the skip in each of the three scans; drop the "already injected" branch.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only

SLEEP_SCRIPT = """
let r = await_event_for("tick.0", nil, 60i)
set_state("timed_out", r["timed_out"])
set_state("deadline", r["deadline"])
"""

TYPED_SCRIPT = """
let r = await_event_for("review.approved", {"required": ["reviewer"]}, 30i)
set_state("got", r)
"""


def _run_one(script: str, state: dict | None = None) -> dict:
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.runtime import nodus_worker

    return nodus_worker.run_one({
        "script": script,
        "filename": "for.nd",
        "state": dict(state or {}),
        "memory_context": {},
        "input_payload": {},
        "context": {"user_id": str(uuid.uuid4()), "execution_unit_id": str(uuid.uuid4()), "trace_id": "t-for"},
    })


# ── the worker and the rules ─────────────────────────────────────────────────


def test_the_worker_reports_the_waits_own_seconds():
    result = _run_one(SLEEP_SCRIPT)

    assert result["status"] == "waiting", result
    assert result["wait_for"] == "tick.0"
    assert result["wait_timeout_seconds"] == 60


def test_a_plain_await_event_reports_no_seconds():
    result = _run_one('let r = await_event("x", nil)')
    assert result["status"] == "waiting"
    assert result["wait_timeout_seconds"] is None


def test_the_resumed_call_returns_the_marker_or_the_payload():
    marker = {"timed_out": True, "deadline": "2026-11-18T00:00:00+00:00"}
    timed = _run_one(SLEEP_SCRIPT, {"nodus_received_events": {"tick.0": marker}})
    assert timed["status"] == "success" and timed["output_state"]["timed_out"] is True

    got = _run_one(TYPED_SCRIPT, {"nodus_received_events": {"review.approved": {"reviewer": "a"}}})
    assert got["output_state"]["got"] == {"reviewer": "a"}


@pytest.mark.parametrize("bad", ["0i", "-5i", "2592001i", '"60"'])
def test_seconds_out_of_bounds_is_a_script_error_not_a_wait(bad):
    result = _run_one(f'let r = await_event_for("x", nil, {bad})')
    assert result["status"] == "failure", result
    assert "seconds" in (result["error"] or "")


def test_validate_wait_seconds_rounds_up_and_bounds():
    from AINDY.core.wait_on_timeout import MAX_WAIT_SECONDS, validate_wait_seconds

    assert validate_wait_seconds(1) == 1
    assert validate_wait_seconds(1.2) == 2
    assert validate_wait_seconds(MAX_WAIT_SECONDS) == MAX_WAIT_SECONDS
    for bad in (0, 0.0, -1, MAX_WAIT_SECONDS + 1, True, "5", None):
        with pytest.raises(ValueError):
            validate_wait_seconds(bad)


def test_the_record_must_name_the_event_the_run_waits_on_now():
    from AINDY.core.wait_on_timeout import WAIT_ON_TIMEOUT_KEY, build_record, resumes_at_deadline

    run = MagicMock(waiting_for="tick.1", state={WAIT_ON_TIMEOUT_KEY: build_record("tick.0", 60)})
    assert not resumes_at_deadline(run), "a record from an earlier wait must not apply to this one"
    run.state = {WAIT_ON_TIMEOUT_KEY: build_record("tick.1", 60)}
    assert resumes_at_deadline(run)
    run.state = {}
    assert not resumes_at_deadline(run)


# ── end to end ───────────────────────────────────────────────────────────────


@pytest.fixture
def scheduler_spy():
    engine = MagicMock()
    with patch("AINDY.kernel.scheduler_engine.get_scheduler_engine", return_value=engine):
        yield engine


@pytest.fixture
def in_process_worker(monkeypatch):
    pytest.importorskip("nodus.runtime.embedding")
    import AINDY.runtime.nodus_adapter  # noqa: F401 — registers `nodus.execute`
    from AINDY.runtime import nodus_worker
    from AINDY.runtime import nodus_worker_pool as pool_mod

    class _InProcessPool:
        def execute(self, payload, *, timeout_s):
            return nodus_worker.run_one(payload)

    monkeypatch.setenv("AINDY_NODUS_WARM_POOL", "1")
    monkeypatch.setattr(pool_mod, "get_pool", lambda: _InProcessPool())


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@pytest.mark.parametrize("script,event,seconds", [(SLEEP_SCRIPT, "tick.0", 60), (TYPED_SCRIPT, "review.approved", 30)])
def test_the_deadline_resumes_the_run_with_the_marker(db_session, scheduler_spy, in_process_worker, script, event, seconds):
    from AINDY.core.wait_on_timeout import WAIT_ON_TIMEOUT_KEY, resume_due_deadline_waits
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.kernel.clock import frozen_at
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    flow = {"start": "nodus.execute", "end": ["nodus.execute"], "edges": {}}
    flow_name = f"fr55_deadline_{event}"
    reg.register_flow(flow_name, flow)
    user_id = str(uuid.uuid4())
    t0 = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    try:
        with frozen_at(t0):
            first = PersistentFlowRunner(flow=flow, db=db_session, user_id=user_id, workflow_type=None).start(
                {"nodus_script": script}, flow_name=flow_name
            )
        assert first["status"] == "WAITING", first
        run = db_session.query(FlowRun).filter(FlowRun.flow_name == flow_name).one()
        assert _aware(run.wait_deadline) == t0 + timedelta(seconds=seconds), "the wait's own deadline, not the flow's 30 min"
        assert run.state[WAIT_ON_TIMEOUT_KEY] == {"policy": "resume", "event": event, "seconds": seconds}

        assert resume_due_deadline_waits(db_session, now=t0 + timedelta(seconds=seconds - 1)) == 0, "not due yet"
        assert resume_due_deadline_waits(db_session, now=t0 + timedelta(seconds=seconds + 1)) == 1

        final = PersistentFlowRunner(flow=flow, db=db_session, user_id=user_id, workflow_type=None).resume(str(run.id))
        assert final["status"] in {"COMPLETED", "SUCCESS"}, final
        db_session.expire_all()
        run = db_session.query(FlowRun).filter(FlowRun.id == run.id).one()
        out = run.state["nodus_output_state"]
        got = out.get("got") or out
        assert got["timed_out"] is True
        assert got["deadline"].startswith("2026-11-18T12:0")
    finally:
        reg.FLOW_REGISTRY.pop(flow_name, None)


def test_a_plain_wait_keeps_the_flow_deadline_and_no_record(db_session, scheduler_spy, in_process_worker):
    from AINDY.config import settings
    from AINDY.core.wait_on_timeout import WAIT_ON_TIMEOUT_KEY
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.kernel.clock import frozen_at
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    flow = {"start": "nodus.execute", "end": ["nodus.execute"], "edges": {}}
    flow_name = "fr55_plain_wait"
    reg.register_flow(flow_name, flow)
    t0 = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    try:
        with frozen_at(t0):
            PersistentFlowRunner(flow=flow, db=db_session, user_id=str(uuid.uuid4()), workflow_type=None).start(
                {"nodus_script": 'let r = await_event("x", nil)'}, flow_name=flow_name
            )
        run = db_session.query(FlowRun).filter(FlowRun.flow_name == flow_name).one()
        assert _aware(run.wait_deadline) == t0 + timedelta(minutes=settings.FLOW_WAIT_TIMEOUT_MINUTES)
        assert WAIT_ON_TIMEOUT_KEY not in run.state
    finally:
        reg.FLOW_REGISTRY.pop(flow_name, None)


# ── the job and the scans, on rows ───────────────────────────────────────────


def _waiting_run(db, *, resume: bool, deadline: datetime, event: str = "tick.0", state_extra: dict | None = None,
                 updated_at: datetime | None = None):
    from AINDY.core.wait_on_timeout import WAIT_ON_TIMEOUT_KEY, build_record
    from AINDY.db.models.flow_run import FlowRun

    state = dict(state_extra or {})
    if resume:
        state[WAIT_ON_TIMEOUT_KEY] = build_record(event, 60)
    run = FlowRun(
        id=str(uuid.uuid4()), flow_name="fr55_rows", workflow_type="flow", status="waiting",
        current_node="nodus.execute", state=state, waiting_for=event, wait_deadline=deadline,
        trace_id=str(uuid.uuid4()), user_id=None,
    )
    db.add(run)
    db.flush()
    if updated_at is not None:
        run.updated_at = updated_at
        db.flush()
    return run


def test_a_run_that_already_holds_a_payload_is_rewoken_not_overwritten(db_session):
    from AINDY.core.wait_on_timeout import resume_due_deadline_waits

    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    run = _waiting_run(db_session, resume=True, deadline=past, state_extra={"event": {"real": True}})
    with patch("AINDY.kernel.event_bus.publish_event", return_value=1) as publish, \
         patch("AINDY.runtime.flow_engine.route_event") as route:
        assert resume_due_deadline_waits(db_session) == 1
    route.assert_not_called()
    publish.assert_called_once()
    assert run.state["event"] == {"real": True}


def test_expire_timed_out_waits_leaves_a_resume_run_and_still_fails_a_plain_one(db_session):
    from AINDY.platform_layer.recovery_jobs import expire_timed_out_waits

    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    resume = _waiting_run(db_session, resume=True, deadline=past)
    plain = _waiting_run(db_session, resume=False, deadline=past)

    asyncio.run(expire_timed_out_waits(db_session))

    assert plain.status == "failed" and plain.error_message == "WAIT_TIMEOUT", "liveness control"
    assert resume.status == "waiting"


def test_expire_timed_out_wait_flows_leaves_a_resume_run_and_still_fails_a_plain_one(db_session):
    from AINDY.db.models.waiting_flow_run import WaitingFlowRun
    from AINDY.platform_layer.recovery_jobs import expire_timed_out_wait_flows

    long_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    resume = _waiting_run(db_session, resume=True, deadline=long_ago)
    plain = _waiting_run(db_session, resume=False, deadline=long_ago)
    for run in (resume, plain):
        db_session.add(WaitingFlowRun(run_id=run.id, event_type="tick.0", waited_since=long_ago, max_wait_seconds=60))
    db_session.flush()

    asyncio.run(expire_timed_out_wait_flows(db_session))

    assert plain.status == "failed", "liveness control"
    assert resume.status == "waiting"
    assert db_session.query(WaitingFlowRun).filter(WaitingFlowRun.run_id == resume.id).count() == 1


def test_the_stuck_scan_respects_a_runs_own_deadline_and_the_resume_policy(db_session):
    from AINDY.agents.stuck_run_service import scan_and_recover_stuck_runs

    old = datetime.now(timezone.utc) - timedelta(hours=2)
    ahead = datetime.now(timezone.utc) + timedelta(hours=1)
    plain = _waiting_run(db_session, resume=False, deadline=old, updated_at=old)
    long_wait = _waiting_run(db_session, resume=False, deadline=ahead, updated_at=old)
    resume = _waiting_run(db_session, resume=True, deadline=old, updated_at=old)

    moved: list[str] = []
    with patch("AINDY.agents.dead_letter_service.move_to_dead_letter",
               side_effect=lambda db, run_id, reason: moved.append(run_id) or True):
        scan_and_recover_stuck_runs(db_session, include_wait_timeouts=True)

    assert plain.id in moved, "liveness control: a plain wait past the global timeout is still dead-lettered"
    assert long_wait.id not in moved, "a wait whose own deadline is ahead is not timed out"
    assert resume.id not in moved
