"""ECOGAP-6 Phase 3 — real-Postgres crash-continuation of a non-waiting flow (ECOGAP-1).

The unit suite (`test_flow_continuation.py`) covers the decision/claim/scan logic on
SQLite but **mocks `_dispatch_resume`**, so the actual `PersistentFlowRunner.resume()`
re-driving a real flow to completion is never exercised. These tests close that gap on
real Postgres:

  1. `resume()` drives a stranded ("executing", mid-flow) FlowRun through the real node
     loop to terminal status `success` — the substrate crash-continuation depends on.
  2. `try_continue_flow_run` end-to-end: claim → durable attempt increment → resume →
     `success`, with the daemon-thread dispatch swapped for a synchronous call so the
     completion is deterministic.

A single-node continuation-safe flow is the faithful minimal case — it is exactly the
"one node whose commit didn't land re-runs on continuation" semantic.

Requires PostgreSQL (auto-skipped otherwise by tests/integration/conftest.py).
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.integration]

_FLOW = "ecogap6_continuation_flow"
_NODE = "ecogap6_marker_node"


@pytest.fixture
def registered_flow():
    """Register a real 1-node continuation-safe flow for the test's duration."""
    from AINDY.runtime.flow_engine import registry as reg

    @reg.register_node(_NODE)
    def _marker(state, context):  # noqa: ANN001
        return {"status": "SUCCESS"}

    # "end" lists the terminal node(s); reaching one finalizes the flow to status "success".
    reg.register_flow(_FLOW, {"start": _NODE, "end": [_NODE], "edges": {}})
    reg.mark_flow_continuation_safe(_FLOW)
    try:
        yield _FLOW
    finally:
        reg.FLOW_REGISTRY.pop(_FLOW, None)
        reg.NODE_REGISTRY.pop(_NODE, None)
        reg.CONTINUATION_SAFE_FLOWS.discard(_FLOW)


def _make_stranded_run(db_session):
    """A FlowRun stranded mid-run: status 'executing', current_node set, state present."""
    from AINDY.db.models.flow_run import FlowRun

    run_id = str(uuid.uuid4())
    run = FlowRun(
        id=run_id,
        flow_name=_FLOW,
        status="executing",
        current_node=_NODE,
        state={"trace_id": run_id},
    )
    db_session.add(run)
    db_session.commit()
    return run_id


def _reload(db_session, run_id):
    from AINDY.db.models.flow_run import FlowRun

    db_session.expire_all()
    return db_session.query(FlowRun).filter(FlowRun.id == run_id).first()


def _node_ran(db_session, run_id):
    """The runner writes a FlowHistory row per executed node — proof the node ran."""
    from AINDY.db.models.flow_run import FlowHistory

    return (
        db_session.query(FlowHistory)
        .filter(FlowHistory.flow_run_id == run_id, FlowHistory.node_name == _NODE)
        .count()
    )


def test_resume_drives_stranded_flow_to_completion_on_postgres(db_session, registered_flow):
    from AINDY.runtime.flow_engine import FLOW_REGISTRY, PersistentFlowRunner

    run_id = _make_stranded_run(db_session)

    runner = PersistentFlowRunner(
        flow=FLOW_REGISTRY[_FLOW], db=db_session, user_id=None, workflow_type=None
    )
    runner.resume(run_id)

    run = _reload(db_session, run_id)
    assert run.status == "success"  # re-driven to completion on real PG
    assert _node_ran(db_session, run_id) >= 1  # the real node actually executed (FlowHistory)


def test_try_continue_flow_run_resumes_to_completion_on_postgres(
    db_session, registered_flow, monkeypatch
):
    from AINDY.core import flow_continuation as fc

    monkeypatch.setattr(fc, "_continuation_enabled", lambda: True)

    # Run the resume synchronously (same session) instead of on the daemon thread so the
    # end-to-end completion is deterministic to assert.
    def _sync_dispatch(*, run_id, flow_name, user_id, workflow_type):
        from AINDY.runtime.flow_engine import FLOW_REGISTRY, PersistentFlowRunner

        PersistentFlowRunner(
            flow=FLOW_REGISTRY[flow_name], db=db_session, user_id=user_id, workflow_type=workflow_type
        ).resume(run_id)

    monkeypatch.setattr(fc, "_dispatch_resume", _sync_dispatch)

    run_id = _make_stranded_run(db_session)
    run = _reload(db_session, run_id)

    handled = fc.try_continue_flow_run(run, db_session)
    assert handled is True  # continuation handled the run (caller must NOT fail it)

    run = _reload(db_session, run_id)
    assert run.status == "success"
    assert _node_ran(db_session, run_id) >= 1  # the real node executed via continuation
    assert (run.state or {}).get("__continuation_attempts") == 1  # durable attempt recorded
