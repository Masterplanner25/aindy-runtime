"""`RESUME-FANOUT-UNSCOPED-1` — a per-run resume touches that run and nothing else.

Observed live (2026-09-13): `POST /platform/flows/runs/{A}/resume` returned
`results: [{run_id: <B>, payload_injected: true}, {run_id: <A>, …}]`. The route checked that A
belonged to the caller and was waiting on the event, then called `route_event(event_type,
payload)` with no run id — which peeked EVERY wait on that event name (no tenant), injected the
payload into each, and published a wake that resumed them all. The ownership check protected the
path parameter; the effect ignored it. `platform.admin`-gated, so an isolation defect an operator
triggers by using the route as documented, not an exploit.

What is pinned, and why each matters:

* **the route** (`flow_run_resume_node`, the real node) resumes the named run only — another
  tenant's run on the same event name is neither injected nor woken;
* **siblings in ONE trace** are separated too — a flow WAIT's correlation is the run's
  `trace_id`, and a trace is shared by every run started under one request, so "pass the
  correlation" alone (the entry's first fix shape) would still fan out to them. The run id is
  the only thing unique to the run; this test fails under correlation-only scoping;
* **the scheduler seam** honours `run_id` on the local scan and through the pre-rehydration
  buffer, so a wake buffered during boot does not widen when replayed;
* **the bus subscriber** forwards `run_id` (in `test_event_bus.py`) and the cross-instance
  fallback honours it (`tests/integration/test_multi_instance_resume.py`), because a scope
  that holds on one path and not the others is the shape this entry was filed for.

Mutation-checked: drop the `run_id` filter in `notify_event` and the sibling test + scheduler
tests fail; drop the targeted query in `route_event` and the two-tenant test fails on
injection; stop passing `run_id` from the route node and every route test fails.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.runtime_only

EVENT = "review.approved"
PAYLOAD = {"reviewer": "shawn", "approved": True}


@pytest.fixture
def engine():
    """A fresh, rehydrated SchedulerEngine standing in for the process singleton everywhere
    the resume path looks it up. Real matching logic; nothing stubbed."""
    from AINDY.kernel.scheduler.engine import SchedulerEngine

    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    with patch("AINDY.kernel.scheduler_engine.get_scheduler_engine", return_value=eng):
        yield eng


def _park(db, engine, *, user_id, trace_id, event=EVENT):
    """A waiting FlowRun + its scheduler wait, registered the way the WAIT branch does it
    (correlation = trace id)."""
    from AINDY.db.models.flow_run import FlowRun

    run = FlowRun(
        id=str(uuid.uuid4()),
        flow_name="fanout_probe",
        status="waiting",
        current_node="n",
        state={"trace_id": trace_id},
        waiting_for=event,
        trace_id=trace_id,
        user_id=user_id,
    )
    db.add(run)
    db.commit()
    engine.register_wait(
        run_id=str(run.id),
        wait_for_event=event,
        tenant_id=str(user_id),
        eu_id="",
        resume_callback=lambda: None,
        correlation_id=trace_id,
        trace_id=trace_id,
    )
    return run


def _resume_via_route(db, run, user_id, payload=PAYLOAD):
    from AINDY.runtime.flow_definitions_engine import flow_run_resume_node

    return flow_run_resume_node(
        {"run_id": str(run.id), "event_type": EVENT, "payload": payload},
        {"db": db, "user_id": str(user_id)},
    )


def _woken(engine) -> set[str]:
    woken = set()
    while True:
        item = engine.dequeue_next()
        if item is None:
            return woken
        woken.add(str(item.run_id))


# ── the route ─────────────────────────────────────────────────────────────────


def test_resuming_one_tenants_run_leaves_another_tenants_run_alone(db_session, engine):
    """★ THE live observation, inverted. Two tenants, one event name, resume A."""
    from AINDY.db.models.flow_run import FlowRun

    u1, u2 = uuid.uuid4(), uuid.uuid4()
    a = _park(db_session, engine, user_id=u1, trace_id=str(uuid.uuid4()))
    b = _park(db_session, engine, user_id=u2, trace_id=str(uuid.uuid4()))

    result = _resume_via_route(db_session, a, u1)

    assert result["status"] == "SUCCESS", result
    touched = {r["run_id"] for r in result["output_patch"]["flow_run_resume_result"]["results"]}
    assert touched == {str(a.id)}, f"the resume touched {touched}; only A was named"

    db_session.expire_all()
    a_state = db_session.query(FlowRun).filter(FlowRun.id == a.id).one().state
    b_state = db_session.query(FlowRun).filter(FlowRun.id == b.id).one().state
    assert a_state.get("event") == PAYLOAD
    assert "event" not in b_state, "another tenant's run received this tenant's payload"

    assert _woken(engine) == {str(a.id)}, "the wake was not scoped to the named run"
    assert engine.waiting_for(str(b.id)) == EVENT, "B was un-parked by A's resume"


def test_sibling_runs_in_one_trace_are_separated_by_run_id_not_correlation(db_session, engine):
    """★★ Correlation alone cannot do this. Both runs carry the SAME trace id — the shape of two
    flows started under one request — so a wake scoped only by correlation matches both."""
    from AINDY.db.models.flow_run import FlowRun

    u = uuid.uuid4()
    trace = str(uuid.uuid4())
    a = _park(db_session, engine, user_id=u, trace_id=trace)
    b = _park(db_session, engine, user_id=u, trace_id=trace)
    # Liveness: without the run-id scope, a correlation-scoped peek sees BOTH.
    assert set(engine.peek_matching_run_ids(EVENT, correlation_id=trace)) == {str(a.id), str(b.id)}

    result = _resume_via_route(db_session, a, u)

    assert result["status"] == "SUCCESS", result
    db_session.expire_all()
    assert "event" not in db_session.query(FlowRun).filter(FlowRun.id == b.id).one().state
    assert _woken(engine) == {str(a.id)}
    assert engine.waiting_for(str(b.id)) == EVENT


def test_the_named_run_is_actually_resumed(db_session, engine):
    """Liveness control for the two above — "touches nothing" must not be satisfied by a route
    that touches nothing at all."""
    from AINDY.db.models.flow_run import FlowRun

    u = uuid.uuid4()
    a = _park(db_session, engine, user_id=u, trace_id=str(uuid.uuid4()))

    result = _resume_via_route(db_session, a, u)

    assert result["status"] == "SUCCESS", result
    assert result["output_patch"]["flow_run_resume_result"]["results"] == [
        {"run_id": str(a.id), "payload_injected": True, "woken": True}
    ]
    db_session.expire_all()
    assert db_session.query(FlowRun).filter(FlowRun.id == a.id).one().state["event"] == PAYLOAD
    assert _woken(engine) == {str(a.id)}
    assert engine.waiting_for(str(a.id)) is None


def test_another_tenant_cannot_resume_the_run_at_all(db_session, engine):
    """The ownership check on the path parameter, pinned beside the effect it now governs."""
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    a = _park(db_session, engine, user_id=u1, trace_id=str(uuid.uuid4()))

    result = _resume_via_route(db_session, a, u2)

    assert result["status"] == "FAILURE" and result["error"].startswith("HTTP_404")
    assert engine.waiting_for(str(a.id)) == EVENT
    assert _woken(engine) == set()


# ── the scheduler seam ────────────────────────────────────────────────────────


def _reg(engine, run_id, *, corr=None):
    engine.register_wait(
        run_id=run_id, wait_for_event=EVENT, tenant_id="t", eu_id="",
        resume_callback=lambda: None, correlation_id=corr,
    )


def test_notify_event_with_run_id_wakes_that_run_only():
    from AINDY.kernel.scheduler.engine import SchedulerEngine

    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    _reg(eng, "run-A", corr="trace-1")
    _reg(eng, "run-B", corr="trace-1")  # same event, same correlation — a sibling
    _reg(eng, "run-C")                  # same event, no correlation — matches any emit

    assert eng.notify_event(EVENT, correlation_id="trace-1", run_id="run-A", broadcast=False) == 1
    assert _woken(eng) == {"run-A"}
    assert eng.waiting_for("run-B") == EVENT and eng.waiting_for("run-C") == EVENT


def test_notify_event_without_run_id_is_still_the_broadcast():
    """Liveness for the test above: the filter is the run id, not a change to matching."""
    from AINDY.kernel.scheduler.engine import SchedulerEngine

    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    _reg(eng, "run-A", corr="trace-1")
    _reg(eng, "run-B", corr="trace-1")

    assert eng.notify_event(EVENT, correlation_id="trace-1", broadcast=False) == 2


def test_a_run_scoped_wake_buffered_before_rehydration_replays_scoped():
    """★ A scope that is dropped by the boot-time buffer widens exactly when the most waits are
    being restored. The buffer entry carries the run id."""
    from AINDY.kernel.scheduler.engine import SchedulerEngine

    eng = SchedulerEngine()  # NOT rehydrated
    _reg(eng, "run-A")
    _reg(eng, "run-B")

    assert eng.notify_event(EVENT, run_id="run-A", broadcast=False) == 0  # buffered
    with eng._lock:
        assert eng._pre_rehydration_buffer == [(EVENT, None, "run-A")]

    eng.mark_rehydration_complete()

    assert _woken(eng) == {"run-A"}
    assert eng.waiting_for("run-B") == EVENT


def test_publish_event_threads_run_id_to_the_engine():
    """`publish_event` is the only sanctioned entry; a kwarg it swallowed would scope nothing."""
    from unittest.mock import MagicMock

    from AINDY.kernel.event_bus import publish_event

    eng = MagicMock()
    eng.notify_event.return_value = 1
    with patch("AINDY.kernel.scheduler_engine.get_scheduler_engine", return_value=eng):
        publish_event(EVENT, correlation_id="trace-1", run_id="run-A")
    eng.notify_event.assert_called_once_with(
        EVENT, correlation_id="trace-1", run_id="run-A", broadcast=True
    )
