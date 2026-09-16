"""`WAIT-TYPED-CONTRACT-1` phase 1 — a resume payload is CHECKED against what the waiting node
declared, not trusted.

The entry's finding was an asymmetry: `SyscallDispatcher.dispatch()` validates every syscall
input against a declared schema, and the wait path — outside data, arbitrary delay, across a
restart — validated nothing. What that costs is pinned here first, as the LIVENESS CONTROL:
Tutorial 2's script, resumed with an empty payload and no declaration, has its wait CONSUMED and
leaves ``waiting`` on garbage. With a declaration the same payload is refused at the door and the
run keeps waiting.

Layers, each driven through the real seam:

* **the runner** — `PersistentFlowRunner` start → WAIT records `__pending_request` on the run's
  state (and NOT in `flow_history.output_patch`, which stays what the node returned); no
  declaration → no record; a malformed declaration fails the node loudly; SUCCESS clears it;
* **`route_event`** — a real `SchedulerEngine` (matching logic, not a stub): a rejection writes
  nothing, wakes nothing, leaves the scheduler entry registered; per-run form raises, broadcast
  form skips; untyped runs pass (absent ≠ mismatch); every outcome counted on
  ``aindy_flow_resume_payload_total``;
* **the route** — `POST /platform/flows/runs/{id}/resume` CALLED on the booted app: 422 with the
  validator's errors and the run still waiting; 200 for a payload that satisfies the schema;
* **the guest** — the REAL interpreter and worker entry (`run_one` in-process):
  ``set_state("nodus_wait_resume_schema", …)`` reaches the run, refuses a bad resume, admits a
  good one, and the script reaches phase 2;
* **the fold** — the record is a runner-level write the DUR-4 fold does not reconstruct, so a run
  recovered from a torn snapshot resumes UNTYPED: stated in the entry, pinned here so a later
  change to either side is a deliberate one.

Mutation-checked (each bites): drop the `check_resume_payload` call in `route_event` → the
rejection tests and the route 422 fail; skip recording the pending request in the WAIT branch →
the runner and guest tests fail; drop the SUCCESS-branch clear → the clear test fails; drop the
`resume_schema` forward in `nodus_worker` → the guest test fails.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from AINDY.services.auth_service import get_current_user

pytestmark = pytest.mark.runtime_only

EVENT = "review.approved"
SCHEMA = {
    "required": ["reviewer", "approved"],
    "properties": {
        "reviewer": {"type": "string"},
        "approved": {"type": "boolean"},
        "note": {"type": "string"},
    },
}
GOOD = {"reviewer": "shawn", "approved": True, "note": "ship it"}
MISSING = {"note": "who approved this?"}
WRONG_TYPE = {"reviewer": "shawn", "approved": "yes"}


def _count(outcome: str) -> float:
    from AINDY.platform_layer.metrics import REGISTRY

    return REGISTRY.get_sample_value("aindy_flow_resume_payload_total", {"outcome": outcome}) or 0.0


def _history(db, run_id):
    from AINDY.core.flow_history_fold import ordered_flow_history

    return ordered_flow_history(db, run_id)


# ── the runner: the WAIT branch records the declaration ──────────────────────


@pytest.fixture
def scheduler_spy():
    engine = MagicMock()
    with patch("AINDY.kernel.scheduler_engine.get_scheduler_engine", return_value=engine):
        yield engine


def _two_node_flow(reg, first, second):
    return {"start": first, "end": [second], "edges": {first: [second]}}


def test_a_wait_that_declares_a_schema_records_a_pending_request_and_success_clears_it(
    db_session, scheduler_spy
):
    from AINDY.core.pending_request import PENDING_REQUEST_KEY
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    node = "typed_wait_probe"

    @reg.register_node(node)
    def _n(state, context):  # noqa: ANN001
        if "event" not in state:
            return {
                "status": "WAIT",
                "wait_for": EVENT,
                "resume_schema": SCHEMA,
                "output_patch": {"parked": True},
            }
        return {"status": "SUCCESS", "output_patch": {"got": state["event"]}}

    flow = {"start": node, "end": [node], "edges": {}}
    reg.register_flow("typed_wait_flow", flow)
    try:
        first = PersistentFlowRunner(flow=flow, db=db_session, user_id=None, workflow_type=None).start(
            {}, flow_name="typed_wait_flow"
        )
        assert first["status"] == "WAITING", first
        run = db_session.query(FlowRun).filter(FlowRun.flow_name == "typed_wait_flow").one()
        record = run.state.get(PENDING_REQUEST_KEY)
        assert record == {"node": node, "event": EVENT, "schema": SCHEMA}, sorted(run.state)
        # The node's recorded output is what the node returned — the record is the runtime's.
        (wait_row,) = _history(db_session, run.id)
        assert wait_row.status == "WAIT"
        assert PENDING_REQUEST_KEY not in (wait_row.output_patch or {})

        state = dict(run.state)
        state["event"] = GOOD
        run.state = state
        db_session.commit()
        second = PersistentFlowRunner(
            flow=flow, db=db_session, user_id=None, workflow_type=None
        ).resume(str(run.id))
        assert second["status"] in {"COMPLETED", "SUCCESS"}, second
        db_session.expire_all()
        run = db_session.query(FlowRun).filter(FlowRun.id == run.id).one()
        assert run.status == "success" and run.state.get("got") == GOOD
        assert PENDING_REQUEST_KEY not in run.state, "a consumed request must not outlive its wait"
    finally:
        reg.FLOW_REGISTRY.pop("typed_wait_flow", None)
        reg.NODE_REGISTRY.pop(node, None)


def test_a_wait_without_a_declaration_records_nothing(db_session, scheduler_spy):
    from AINDY.core.pending_request import PENDING_REQUEST_KEY
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    node = "untyped_wait_probe"

    @reg.register_node(node)
    def _n(state, context):  # noqa: ANN001
        return {"status": "WAIT", "wait_for": EVENT, "output_patch": {}}

    flow = {"start": node, "end": [node], "edges": {}}
    reg.register_flow("untyped_wait_flow", flow)
    try:
        PersistentFlowRunner(flow=flow, db=db_session, user_id=None, workflow_type=None).start(
            {}, flow_name="untyped_wait_flow"
        )
        run = db_session.query(FlowRun).filter(FlowRun.flow_name == "untyped_wait_flow").one()
        assert run.status == "waiting"
        assert PENDING_REQUEST_KEY not in run.state, "an undeclared wait is untyped — no record"
    finally:
        reg.FLOW_REGISTRY.pop("untyped_wait_flow", None)
        reg.NODE_REGISTRY.pop(node, None)


def test_a_malformed_declaration_fails_the_node_loudly(db_session, scheduler_spy):
    """Silently treating a bad declaration as "untyped" would make a typo indistinguishable
    from no guard at all — variant 6 by construction."""
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    node = "malformed_schema_probe"

    @reg.register_node(node)
    def _n(state, context):  # noqa: ANN001
        return {"status": "WAIT", "wait_for": EVENT, "resume_schema": "reviewer:string"}

    flow = {"start": node, "end": [node], "edges": {}}
    reg.register_flow("malformed_schema_flow", flow)
    try:
        result = PersistentFlowRunner(flow=flow, db=db_session, user_id=None, workflow_type=None).start(
            {}, flow_name="malformed_schema_flow"
        )
        assert result["status"] == "FAILED", result
        assert "resume_schema" in str(result.get("data") or result), result
        run = db_session.query(FlowRun).filter(FlowRun.flow_name == "malformed_schema_flow").one()
        assert run.status != "waiting", "a node whose declaration is unusable must not park"
        scheduler_spy.register_wait.assert_not_called()
    finally:
        reg.FLOW_REGISTRY.pop("malformed_schema_flow", None)
        reg.NODE_REGISTRY.pop(node, None)


def test_an_earlier_typed_wait_does_not_type_a_later_undeclared_one(db_session, scheduler_spy):
    from AINDY.core.pending_request import PENDING_REQUEST_KEY
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    first, second = "typed_then_untyped_a", "typed_then_untyped_b"

    @reg.register_node(first)
    def _a(state, context):  # noqa: ANN001
        if "event" not in state:
            return {"status": "WAIT", "wait_for": EVENT, "resume_schema": SCHEMA, "output_patch": {}}
        return {"status": "SUCCESS", "output_patch": {}}

    @reg.register_node(second)
    def _b(state, context):  # noqa: ANN001
        return {"status": "WAIT", "wait_for": "second.thing", "output_patch": {}}

    flow = _two_node_flow(reg, first, second)
    reg.register_flow("typed_then_untyped_flow", flow)
    try:
        PersistentFlowRunner(flow=flow, db=db_session, user_id=None, workflow_type=None).start(
            {}, flow_name="typed_then_untyped_flow"
        )
        run = db_session.query(FlowRun).filter(FlowRun.flow_name == "typed_then_untyped_flow").one()
        assert run.state[PENDING_REQUEST_KEY]["node"] == first
        state = dict(run.state)
        state["event"] = GOOD
        run.state = state
        db_session.commit()
        PersistentFlowRunner(flow=flow, db=db_session, user_id=None, workflow_type=None).resume(
            str(run.id)
        )
        db_session.expire_all()
        run = db_session.query(FlowRun).filter(FlowRun.id == run.id).one()
        assert run.status == "waiting" and run.waiting_for == "second.thing"
        assert PENDING_REQUEST_KEY not in run.state, (
            "the first node's schema would otherwise gate the second node's resume"
        )
    finally:
        reg.FLOW_REGISTRY.pop("typed_then_untyped_flow", None)
        reg.NODE_REGISTRY.pop(first, None)
        reg.NODE_REGISTRY.pop(second, None)


# ── route_event: checked before anything is written or woken ─────────────────


@pytest.fixture
def engine():
    from AINDY.kernel.scheduler.engine import SchedulerEngine

    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    with patch("AINDY.kernel.scheduler_engine.get_scheduler_engine", return_value=eng):
        yield eng


def _park(db, engine, *, schema=None, user_id=None, event=EVENT):
    from AINDY.core.pending_request import build_pending_request, PENDING_REQUEST_KEY
    from AINDY.db.models.flow_run import FlowRun

    trace = str(uuid.uuid4())
    state = {"trace_id": trace}
    record = build_pending_request(node="n", event=event, schema=schema)
    if record is not None:
        state[PENDING_REQUEST_KEY] = record
    run = FlowRun(
        id=str(uuid.uuid4()),
        flow_name="typed_probe",
        status="waiting",
        current_node="n",
        state=state,
        waiting_for=event,
        trace_id=trace,
        user_id=user_id,
    )
    db.add(run)
    db.commit()
    engine.register_wait(
        run_id=str(run.id), wait_for_event=event, tenant_id=str(user_id or ""), eu_id="",
        resume_callback=lambda: None, correlation_id=trace, trace_id=trace,
    )
    return run


def _woken(engine) -> set[str]:
    woken = set()
    while (item := engine.dequeue_next()) is not None:
        woken.add(str(item.run_id))
    return woken


@pytest.mark.parametrize("payload", [MISSING, WRONG_TYPE, {}], ids=["missing", "wrong-type", "empty"])
def test_a_rejected_payload_writes_nothing_and_wakes_nothing(db_session, engine, payload):
    from AINDY.core.pending_request import ResumePayloadRejected
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import route_event

    run = _park(db_session, engine, schema=SCHEMA)
    before = _count("rejected")

    with pytest.raises(ResumePayloadRejected) as exc_info:
        route_event(EVENT, payload, db_session, run_id=str(run.id))

    assert exc_info.value.errors, "the validator's own errors travel with the refusal"
    db_session.expire_all()
    row = db_session.query(FlowRun).filter(FlowRun.id == run.id).one()
    assert row.status == "waiting" and "event" not in row.state, "nothing may be written"
    assert engine.waiting_for(str(run.id)) == EVENT, "the scheduler entry must survive"
    assert _woken(engine) == set(), "nothing may be woken"
    assert _count("rejected") == before + 1


def test_a_valid_payload_is_injected_and_counted_accepted(db_session, engine):
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import route_event

    run = _park(db_session, engine, schema=SCHEMA)
    before = _count("accepted")

    results = route_event(EVENT, GOOD, db_session, run_id=str(run.id))

    assert results == [{"run_id": str(run.id), "payload_injected": True, "woken": True}]
    db_session.expire_all()
    assert db_session.query(FlowRun).filter(FlowRun.id == run.id).one().state["event"] == GOOD
    assert _woken(engine) == {str(run.id)}
    assert _count("accepted") == before + 1


def test_an_untyped_run_accepts_anything_and_is_counted_untyped(db_session, engine):
    """Absent ≠ mismatch. A run that declared nothing — every run before this shipped — behaves
    exactly as it did; the label is what tells an operator adoption is zero rather than the
    check unwired."""
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import route_event

    run = _park(db_session, engine, schema=None)
    before = _count("untyped")

    results = route_event(EVENT, MISSING, db_session, run_id=str(run.id))

    assert results == [{"run_id": str(run.id), "payload_injected": True, "woken": True}]
    db_session.expire_all()
    assert db_session.query(FlowRun).filter(FlowRun.id == run.id).one().state["event"] == MISSING
    assert _count("untyped") == before + 1


def test_broadcast_skips_a_rejecting_run_and_injects_the_rest(db_session, engine):
    """The un-scoped form nothing calls today — covered anyway, because a guard that holds on
    one path and not the other is the shape `RESUME-FANOUT-UNSCOPED-1` was filed for. The
    rejecting run is not injected; the wake by event name still reaches it, which it sees as a
    payload-less wake (the semantics a bus emit already has)."""
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import route_event

    typed = _park(db_session, engine, schema=SCHEMA)
    untyped = _park(db_session, engine, schema=None)

    results = route_event(EVENT, MISSING, db_session)

    # Broadcast form: `woken` is a per-RUN answer and the broadcast has none, so the key is absent.
    assert results == [{"run_id": str(untyped.id), "payload_injected": True}]
    db_session.expire_all()
    assert "event" not in db_session.query(FlowRun).filter(FlowRun.id == typed.id).one().state
    assert db_session.query(FlowRun).filter(FlowRun.id == untyped.id).one().state["event"] == MISSING
    assert _woken(engine) == {str(typed.id), str(untyped.id)}


# ── the route, CALLED ────────────────────────────────────────────────────────


def _admin_session_for(uid: str):
    from AINDY.auth.api_key_auth import derive_session_scopes

    def _session():
        return {
            "sub": uid,
            "user_id": uid,
            "auth_type": "jwt",
            "is_admin": True,
            "session_scopes": derive_session_scopes(is_admin=True),
        }

    return _session


@pytest.fixture
def user_id():
    return str(uuid.uuid4())


@pytest.fixture
def client(runtime_only_app, user_id):
    runtime_only_app.dependency_overrides[get_current_user] = _admin_session_for(user_id)
    with TestClient(runtime_only_app, raise_server_exceptions=False) as c:
        yield c


def test_the_route_answers_422_and_leaves_the_run_waiting(client, db_session, engine, user_id):
    from AINDY.db.models.flow_run import FlowRun

    run = _park(db_session, engine, schema=SCHEMA, user_id=uuid.UUID(user_id))

    response = client.post(
        f"/platform/flows/runs/{run.id}/resume", json={"event_type": EVENT, "payload": MISSING}
    )

    assert response.status_code == 422, response.text
    detail = response.json().get("detail") or response.json()
    assert "errors" in str(detail) and "reviewer" in str(detail), detail
    db_session.expire_all()
    row = db_session.query(FlowRun).filter(FlowRun.id == run.id).one()
    assert row.status == "waiting" and "event" not in row.state
    assert engine.waiting_for(str(run.id)) == EVENT
    assert _woken(engine) == set()


def test_the_route_answers_200_for_a_payload_that_satisfies_the_schema(
    client, db_session, engine, user_id
):
    from AINDY.db.models.flow_run import FlowRun

    run = _park(db_session, engine, schema=SCHEMA, user_id=uuid.UUID(user_id))

    response = client.post(
        f"/platform/flows/runs/{run.id}/resume", json={"event_type": EVENT, "payload": GOOD}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["flow_run_resume_result"]["results"] == [
        {"run_id": str(run.id), "payload_injected": True, "woken": True}
    ]
    db_session.expire_all()
    assert db_session.query(FlowRun).filter(FlowRun.id == run.id).one().state["event"] == GOOD
    assert _woken(engine) == {str(run.id)}


# ── the guest: a script declares what may resume it ──────────────────────────

# Tutorial 2's shape, minus its sys() calls, PLUS the declaration.
TYPED_SCRIPT = """
let received = get_state("nodus_received_events")
if (received == nil) {
    set_state("phase", 1i)
    set_state("nodus_wait_requested", true)
    set_state("nodus_wait_event_type", "review.approved")
    set_state("nodus_wait_resume_schema", {
        "required": ["reviewer", "approved"],
        "properties": {"reviewer": {"type": "string"}, "approved": {"type": "boolean"}}
    })
} else {
    let approval = received["review.approved"]
    set_state("phase", 2i)
    set_state("reviewer", approval["reviewer"])
    set_state("approved", approval["approved"])
}
"""

UNTYPED_SCRIPT = TYPED_SCRIPT.replace(
    '''    set_state("nodus_wait_resume_schema", {
        "required": ["reviewer", "approved"],
        "properties": {"reviewer": {"type": "string"}, "approved": {"type": "boolean"}}
    })
''',
    "",
)
assert "nodus_wait_resume_schema" not in UNTYPED_SCRIPT


@pytest.fixture
def in_process_worker(monkeypatch):
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.runtime import nodus_worker
    from AINDY.runtime import nodus_worker_pool as pool_mod

    class _InProcessPool:
        def execute(self, payload, *, timeout_s):
            return nodus_worker.run_one(payload)

    monkeypatch.setenv("AINDY_NODUS_WARM_POOL", "1")
    monkeypatch.setattr(pool_mod, "get_pool", lambda: _InProcessPool())


def _nodus_flow():
    import AINDY.runtime.nodus_adapter  # noqa: F401

    return {"start": "nodus.execute", "end": ["nodus.execute"], "edges": {}}


def _start_guest(db, flow, flow_name, script, user_id):
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    first = PersistentFlowRunner(flow=flow, db=db, user_id=user_id, workflow_type=None).start(
        {"nodus_script": script}, flow_name=flow_name
    )
    assert first["status"] == "WAITING", first
    return db.query(FlowRun).filter(FlowRun.flow_name == flow_name).one()


def test_a_nodus_script_can_declare_the_schema_of_its_resume(db_session, engine, in_process_worker):
    from AINDY.core.pending_request import PENDING_REQUEST_KEY, ResumePayloadRejected
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg, route_event
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    flow, flow_name = _nodus_flow(), "typed_guest_flow"
    reg.register_flow(flow_name, flow)
    user_id = str(uuid.uuid4())
    try:
        run = _start_guest(db_session, flow, flow_name, TYPED_SCRIPT, user_id)
        record = run.state.get(PENDING_REQUEST_KEY)
        assert record and record["node"] == "nodus.execute" and record["event"] == EVENT, sorted(run.state)
        assert record["schema"]["required"] == ["reviewer", "approved"]

        # The real scheduler entry is what `route_event` needs to publish through; the runner
        # registered its wait on the patched engine.
        assert engine.waiting_for(str(run.id)) == EVENT

        with pytest.raises(ResumePayloadRejected):
            route_event(EVENT, MISSING, db_session, run_id=str(run.id))
        db_session.expire_all()
        assert db_session.query(FlowRun).filter(FlowRun.id == run.id).one().status == "waiting"

        route_event(EVENT, GOOD, db_session, run_id=str(run.id))
        final = PersistentFlowRunner(
            flow=flow, db=db_session, user_id=user_id, workflow_type=None
        ).resume(str(run.id))
        assert final["status"] in {"COMPLETED", "SUCCESS"}, final
        db_session.expire_all()
        run = db_session.query(FlowRun).filter(FlowRun.id == run.id).one()
        out = run.state.get("nodus_output_state") or {}
        assert out.get("phase") == 2 and out.get("reviewer") == "shawn" and out.get("approved") is True
        assert PENDING_REQUEST_KEY not in run.state
        assert [r.status for r in _history(db_session, run.id)] == ["WAIT", "SUCCESS"]
    finally:
        reg.FLOW_REGISTRY.pop(flow_name, None)


def test_without_a_declaration_a_malformed_resume_consumes_the_wait(
    db_session, engine, in_process_worker
):
    """★ LIVENESS CONTROL — what the guard prevents, shown on the untyped path. The same empty
    payload is ACCEPTED, the wait is consumed, and the run leaves ``waiting`` on garbage.
    A guard whose absence changes nothing would be vacuous; this is the something."""
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg, route_event
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    flow, flow_name = _nodus_flow(), "untyped_guest_flow"
    reg.register_flow(flow_name, flow)
    user_id = str(uuid.uuid4())
    try:
        run = _start_guest(db_session, flow, flow_name, UNTYPED_SCRIPT, user_id)

        results = route_event(EVENT, {}, db_session, run_id=str(run.id))
        assert results == [{"run_id": str(run.id), "payload_injected": True, "woken": True}]
        assert engine.waiting_for(str(run.id)) is None, "the wait was consumed"

        PersistentFlowRunner(flow=flow, db=db_session, user_id=user_id, workflow_type=None).resume(
            str(run.id)
        )
        db_session.expire_all()
        run = db_session.query(FlowRun).filter(FlowRun.id == run.id).one()
        assert run.status != "waiting", "the run left its wait on a payload it could not use"
        statuses = [r.status for r in _history(db_session, run.id)]
        assert statuses[0] == "WAIT" and statuses[-1] != "WAIT", statuses
    finally:
        reg.FLOW_REGISTRY.pop(flow_name, None)


# ── the fold: stated degradation, pinned ─────────────────────────────────────


def test_a_state_reconstructed_by_the_fold_resumes_untyped(db_session, scheduler_spy):
    """The record is a runner-level write (like `route_event`'s `state["event"]`), not part of
    the node's patch, so the DUR-4 fold does not carry it: a run recovered from a torn snapshot
    is untyped — the pre-feature behaviour, never a wrong rejection. Pinned so that changing
    either side (fold or record placement) is a decision, not a drift."""
    from AINDY.core.flow_history_fold import reconstruct_flow_run_state
    from AINDY.core.pending_request import OUTCOME_UNTYPED, PENDING_REQUEST_KEY, check_resume_payload
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    node = "fold_probe"

    @reg.register_node(node)
    def _n(state, context):  # noqa: ANN001
        return {"status": "WAIT", "wait_for": EVENT, "resume_schema": SCHEMA, "output_patch": {"p": 1}}

    flow = {"start": node, "end": [node], "edges": {}}
    reg.register_flow("fold_probe_flow", flow)
    try:
        PersistentFlowRunner(flow=flow, db=db_session, user_id=None, workflow_type=None).start(
            {}, flow_name="fold_probe_flow"
        )
        run = db_session.query(FlowRun).filter(FlowRun.flow_name == "fold_probe_flow").one()
        assert PENDING_REQUEST_KEY in run.state  # the live snapshot has it …
        folded = reconstruct_flow_run_state(db_session, run.id)
        assert folded.get("p") == 1 and PENDING_REQUEST_KEY not in folded  # … the fold does not
        assert check_resume_payload(folded, MISSING, run_id=run.id, event_type=EVENT) == OUTCOME_UNTYPED
    finally:
        reg.FLOW_REGISTRY.pop("fold_probe_flow", None)
        reg.NODE_REGISTRY.pop(node, None)
