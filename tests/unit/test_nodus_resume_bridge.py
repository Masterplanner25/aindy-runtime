"""`NODUS-RESUME-BRIDGE-1` — a suspended run must be able to receive what resumed it.

Found by running Tutorial 2 live against 2.13.0 after a source-level pass had already declared
it correct. The script suspended fine. It never resumed with the payload, through any path:

1. `nodus.execute` returned `{"status": "WAIT", "output_patch": {"nodus_wait_event_type": …}}`.
2. The runner's `_merge_superstep` merged **SUCCESS** patches only — its docstring said so — so
   the WAIT patch reached `flow_history.output_patch` and never `flow_runs.state`.
3. `POST …/runs/{id}/resume` → `route_event` injected `state["event"]` and re-enqueued the run.
4. On re-entry the adapter's bridge looked for `state["nodus_wait_event_type"]`, found nothing,
   popped `event` and dropped it. The script saw nil and re-waited. History: `WAIT, WAIT, WAIT…`

★★ **The assertion that matters is about the SECOND run.** Every existing wait test stopped at
"the run is waiting" (`GUEST-BUILTINS-DEAD-1` proved the suspend and stopped), and a unit test of
the bridge function alone passes when handed a state that has the key — the bug was that the
runner never persisted it. So these tests drive `PersistentFlowRunner` start → WAIT → inject →
resume, on a real (SQLite) `FlowRun`, and assert what the re-run node/script saw.

★ The nodus test runs the REAL guest interpreter and the REAL worker entry (`run_one`) — only
the process boundary is removed, by handing the warm pool's payload to `run_one` in-process, the
same seam `test_dur2b_subprocess_and_segment_scope.py` uses. The mechanism under test (runner
merge + adapter bridge) is entirely on this side of that boundary.

★ The scheduler engine is replaced by a SPY, not a no-op: `register_wait` is asserted to have
been called with the event type. A no-op could not prove the wait was registered (variant 13).

Mutation-checked: revert `_MERGED_STATUSES` to `{"SUCCESS"}` and the three second-run tests
fail; drop the fold change and `test_fold_status_set_matches_the_engine` fails.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only

EVENT = "review.approved"
PAYLOAD = {"reviewer": "shawn", "approved": True, "note": "ship it"}


@pytest.fixture
def scheduler_spy():
    """A spy for the WAIT branch's `register_wait`, so the singleton engine is not polluted
    with a wait that outlives the test — and so the registration is ASSERTED, not stubbed."""
    engine = MagicMock()
    with patch("AINDY.kernel.scheduler_engine.get_scheduler_engine", return_value=engine):
        yield engine


def _history_statuses(db, run_id):
    from AINDY.core.flow_history_fold import ordered_flow_history

    return [row.status for row in ordered_flow_history(db, run_id)]


def _inject_like_route_event(db, run, payload):
    """What `route_event` does to the row (verified live: `payload_injected: true`) — the
    injection was never the defect, so it is replicated here rather than driven through the
    event bus, whose resume callback would open its own session against another engine."""
    from AINDY.runtime.flow_engine.serialization import _json_safe

    state = dict(run.state or {})
    state["event"] = payload
    run.state = _json_safe(state)
    db.commit()


# ── Engine level: a WAIT patch lands on the run's state ──────────────────────


def test_a_wait_patch_is_persisted_on_the_run_and_visible_to_the_re_run(db_session, scheduler_spy):
    """Generic — no nodus. A node parks with a patch; the re-run must see that patch AND the
    injected event. This is the runner half of the defect in isolation."""
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    node = "bridge_probe_node"
    seen: list[dict] = []

    @reg.register_node(node)
    def _n(state, context):  # noqa: ANN001
        seen.append(dict(state))
        if "parked_on" not in state:
            return {"status": "WAIT", "wait_for": EVENT, "output_patch": {"parked_on": EVENT}}
        return {"status": "SUCCESS", "output_patch": {"got": state.get("event")}}

    flow = {"start": node, "end": [node], "edges": {}}
    reg.register_flow("bridge_probe_flow", flow)
    try:
        runner = PersistentFlowRunner(flow=flow, db=db_session, user_id=None, workflow_type=None)
        first = runner.start({}, flow_name="bridge_probe_flow")
        assert first["status"] == "WAITING", first

        run = db_session.query(FlowRun).filter(FlowRun.flow_name == "bridge_probe_flow").one()
        assert run.status == "waiting"
        assert run.state.get("parked_on") == EVENT, (
            f"the WAIT patch did not reach flow_runs.state (keys: {sorted(run.state)}) — it is "
            "in flow_history only, and the re-run cannot read flow_history"
        )
        scheduler_spy.register_wait.assert_called_once()
        assert scheduler_spy.register_wait.call_args.kwargs["wait_for_event"] == EVENT

        _inject_like_route_event(db_session, run, PAYLOAD)
        second = PersistentFlowRunner(
            flow=flow, db=db_session, user_id=None, workflow_type=None
        ).resume(str(run.id))

        assert second["status"] in {"COMPLETED", "SUCCESS"}, second
        assert len(seen) == 2 and seen[1]["parked_on"] == EVENT and seen[1]["event"] == PAYLOAD
        db_session.expire_all()
        run = db_session.query(FlowRun).filter(FlowRun.id == run.id).one()
        assert run.status == "success"
        assert run.state.get("got") == PAYLOAD
        assert _history_statuses(db_session, run.id) == ["WAIT", "SUCCESS"]
    finally:
        reg.FLOW_REGISTRY.pop("bridge_probe_flow", None)
        reg.NODE_REGISTRY.pop(node, None)


def test_failure_and_retry_patches_still_do_not_land(db_session, scheduler_spy):
    """Liveness control for the widening: only WAIT joined SUCCESS. A FAILURE patch must not
    start landing as a side effect — `_MERGED_STATUSES` is a set of two, not "anything"."""
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    node = "bridge_failure_node"

    @reg.register_node(node)
    def _n(state, context):  # noqa: ANN001
        return {"status": "FAILURE", "error": "boom", "output_patch": {"leaked": True}}

    flow = {"start": node, "end": [node], "edges": {}}
    reg.register_flow("bridge_failure_flow", flow)
    try:
        PersistentFlowRunner(flow=flow, db=db_session, user_id=None, workflow_type=None).start(
            {}, flow_name="bridge_failure_flow"
        )
        run = db_session.query(FlowRun).filter(FlowRun.flow_name == "bridge_failure_flow").one()
        assert run.status == "failed"
        assert "leaked" not in (run.state or {})
    finally:
        reg.FLOW_REGISTRY.pop("bridge_failure_flow", None)
        reg.NODE_REGISTRY.pop(node, None)


# ── The tutorial's shape: a real Nodus script, second run ────────────────────

# Tutorial 2's script, minus its sys() calls (no dispatcher in this test — the wait/resume
# mechanism is what is under test, and it does not touch the syscall path).
TWO_PHASE_SCRIPT = """
let received = get_state("nodus_received_events")
if (received == nil) {
    set_state("phase", 1i)
    set_state("nodus_wait_requested", true)
    set_state("nodus_wait_event_type", "review.approved")
} else {
    let approval = received["review.approved"]
    set_state("phase", 2i)
    set_state("reviewer", approval["reviewer"])
    set_state("approved", approval["approved"])
}
"""


@pytest.fixture
def in_process_worker(monkeypatch):
    """Serve the adapter's warm-pool request from `nodus_worker.run_one` in this process."""
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.runtime import nodus_worker
    from AINDY.runtime import nodus_worker_pool as pool_mod

    calls: list[dict] = []

    class _InProcessPool:
        def execute(self, payload, *, timeout_s):
            calls.append(payload)
            return nodus_worker.run_one(payload)

    monkeypatch.setenv("AINDY_NODUS_WARM_POOL", "1")
    monkeypatch.setattr(pool_mod, "get_pool", lambda: _InProcessPool())
    return calls


def _nodus_flow():
    import AINDY.runtime.nodus_adapter  # noqa: F401  — registers the `nodus.execute` node

    return {"start": "nodus.execute", "end": ["nodus.execute"], "edges": {}}


def test_a_nodus_script_receives_the_resume_payload_on_its_second_run(
    db_session, scheduler_spy, in_process_worker
):
    """★★ THE test the entry asked for. Phase 1 parks; the resume must reach phase 2."""
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    flow = _nodus_flow()
    flow_name = "bridge_nodus_flow"
    reg.register_flow(flow_name, flow)
    user_id = str(uuid.uuid4())
    try:
        runner = PersistentFlowRunner(flow=flow, db=db_session, user_id=user_id, workflow_type=None)
        first = runner.start({"nodus_script": TWO_PHASE_SCRIPT}, flow_name=flow_name)
        assert first["status"] == "WAITING", first
        assert first["data"]["waiting_for"] == EVENT

        run = db_session.query(FlowRun).filter(FlowRun.flow_name == flow_name).one()
        assert run.status == "waiting" and run.waiting_for == EVENT
        # The three things the live run showed were missing from flow_runs.state:
        assert run.state.get("nodus_wait_event_type") == EVENT
        assert run.state.get("nodus_status") == "waiting"
        assert run.state.get("nodus_output_state", {}).get("phase") == 1, (
            "what the script set before it parked should be readable on the waiting run"
        )
        assert len(in_process_worker) == 1
        assert "nodus_received_events" not in in_process_worker[0]["state"], (
            "phase 1 must start with nothing received, or the script never takes phase 1"
        )

        _inject_like_route_event(db_session, run, PAYLOAD)
        second = PersistentFlowRunner(
            flow=flow, db=db_session, user_id=user_id, workflow_type=None
        ).resume(str(run.id))

        assert second["status"] in {"COMPLETED", "SUCCESS"}, second
        assert len(in_process_worker) == 2, "the script did not run a second time"
        assert in_process_worker[1]["state"] == {"nodus_received_events": {EVENT: PAYLOAD}}, (
            "the re-run's namespace must be seeded with exactly the received event — this is "
            "the seam the bridge feeds, and it was empty on every live resume"
        )

        db_session.expire_all()
        run = db_session.query(FlowRun).filter(FlowRun.id == run.id).one()
        assert run.status == "success"
        out = run.state.get("nodus_output_state") or {}
        assert out.get("phase") == 2, f"the script never reached phase 2: {out}"
        assert out.get("reviewer") == "shawn" and out.get("approved") is True
        assert run.state.get("nodus_received_events") == {EVENT: PAYLOAD}
        assert "nodus_wait_event_type" not in run.state, "cleared once the resume was consumed"
        assert "event" not in run.state, "the raw injection is consumed, not left behind"
        assert _history_statuses(db_session, run.id) == ["WAIT", "SUCCESS"], (
            "the live signature of the defect is WAIT, WAIT — a second WAIT row means the "
            "script re-parked instead of resuming"
        )
    finally:
        reg.FLOW_REGISTRY.pop(flow_name, None)


def test_a_payload_less_wake_re_waits_and_says_so(
    db_session, scheduler_spy, in_process_worker, caplog
):
    """`WAIT-PAYLOAD-PATH-1` (a), the half that is cheap: a wake without a payload (the event
    bus) re-runs the script, which re-parks. That was already the behaviour; what is new is that
    it is logged, the pending type survives for the NEXT resume, and a later payload still
    lands — so the run is not wedged by an early emit. (`caplog` is fine here: the adapter runs
    on this thread, not a worker thread.)"""
    import logging

    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    flow = _nodus_flow()
    flow_name = "bridge_nodus_bare_wake_flow"
    reg.register_flow(flow_name, flow)
    user_id = str(uuid.uuid4())
    try:
        PersistentFlowRunner(flow=flow, db=db_session, user_id=user_id, workflow_type=None).start(
            {"nodus_script": TWO_PHASE_SCRIPT}, flow_name=flow_name
        )
        run = db_session.query(FlowRun).filter(FlowRun.flow_name == flow_name).one()
        run_id = str(run.id)
        assert run.state.get("nodus_wait_event_type") == EVENT, sorted(run.state)

        # A bare wake: no `event` injected.
        with caplog.at_level(logging.WARNING, logger="AINDY.runtime.nodus_adapter"):
            bare = PersistentFlowRunner(
                flow=flow, db=db_session, user_id=user_id, workflow_type=None
            ).resume(run_id)
        assert bare["status"] == "WAITING", bare
        assert any("WITHOUT a payload" in rec.getMessage() for rec in caplog.records), (
            "a payload-less wake must be distinguishable from a first wait"
        )
        db_session.expire_all()
        run = db_session.query(FlowRun).filter(FlowRun.id == run_id).one()
        assert run.status == "waiting" and run.state.get("nodus_wait_event_type") == EVENT
        assert _history_statuses(db_session, run_id) == ["WAIT", "WAIT"]

        # The real resume still works afterwards.
        _inject_like_route_event(db_session, run, PAYLOAD)
        final = PersistentFlowRunner(
            flow=flow, db=db_session, user_id=user_id, workflow_type=None
        ).resume(run_id)
        assert final["status"] in {"COMPLETED", "SUCCESS"}, final
        db_session.expire_all()
        run = db_session.query(FlowRun).filter(FlowRun.id == run_id).one()
        assert (run.state.get("nodus_output_state") or {}).get("phase") == 2
        assert _history_statuses(db_session, run_id) == ["WAIT", "WAIT", "SUCCESS"]
    finally:
        reg.FLOW_REGISTRY.pop(flow_name, None)


def test_a_run_that_completes_after_a_bare_wake_clears_the_pending_type(
    db_session, in_process_worker
):
    """Narrow, at the node: a script woken without a payload that nevertheless finishes (it
    could — from memory, a syscall, anything outside its namespace) must not leave
    `nodus_wait_event_type` behind, or a later resume would bridge a payload into a wait that
    no longer exists. The runner tests above cannot reach this: a namespace-only script has no
    way to tell a bare wake from a first run, so it always re-parks."""
    from AINDY.runtime.nodus_adapter import nodus_execute_node

    state = {
        "nodus_script": 'set_state("done", true)\n',
        "nodus_wait_event_type": EVENT,
        "trace_id": "t-bare-wake",
    }
    context = {"db": db_session, "user_id": str(uuid.uuid4()), "run_id": "r1", "trace_id": "t-bare-wake"}

    result = nodus_execute_node(state, context)

    assert result["status"] == "SUCCESS", result
    assert result["output_patch"]["nodus_output_state"].get("done") is True
    assert "nodus_wait_event_type" not in state
