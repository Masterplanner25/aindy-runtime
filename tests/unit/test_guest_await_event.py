"""DEC-017 — the guest wait is `await_event(event_type, schema)`, a host function over the three keys.

`GUEST-BUILTINS-DEAD-1` found that the documented guest wait (`event.wait()` in
`nodus_builtins.py`, 530 lines) had zero importers and could not work: the design assumed a host
exception would propagate out of the guest, and **nodus swallows host-function exceptions into an
``{"ok": False}`` result** (measured 2026-09-16). The wait that DID work was reachable only by
three undocumented state keys. DEC-017 keeps the keys as the wire contract and puts one function
over them — `await_event` (not `wait`, a reserved nodus built-in) — which sets the keys and halts
the script at the call, and RETURNS the payload on the resumed run.

What is pinned, through the REAL worker entry (`run_one`) and the REAL interpreter:

* first run: the script halts AT the call (nothing after it runs), the worker reports
  ``waiting`` with the event and the schema, state set before the call survives;
* resumed run: the call returns the delivered payload and the script continues linearly;
* end to end through `PersistentFlowRunner` + `route_event`: the typed schema is honoured (a bad
  payload is refused at the door), a good one reaches phase 2 — the same second-run assertion
  `NODUS-RESUME-BRIDGE-1` established;
* the raise-based module and signal are gone, and nothing under `AINDY/` imports them;
* `wait` is still reserved — pinned so a future nodus that frees it is noticed, not assumed.

Mutation-checked: drop the key-setting before the raise → the first-run test reports a failure
instead of a wait; drop the resume branch → the second-run test halts again; reorder the worker's
flag check after `ok` → every await reads as a failure.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only

EVENT = "review.approved"
SCHEMA = {"required": ["reviewer", "approved"], "properties": {"reviewer": {"type": "string"}, "approved": {"type": "boolean"}}}

LINEAR_SCRIPT = """
set_state("phase1_ran", true)
set_state("before", 1i)
let approval = await_event("review.approved", {
    "required": ["reviewer", "approved"],
    "properties": {"reviewer": {"type": "string"}, "approved": {"type": "boolean"}}
})
set_state("phase2_ran", true)
set_state("reviewer", approval["reviewer"])
set_state("approved", approval["approved"])
"""

UNTYPED_SCRIPT = """
let payload = await_event("review.approved", nil)
set_state("got", payload)
"""


def _run_one(script: str, state: dict | None = None) -> dict:
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.runtime import nodus_worker

    return nodus_worker.run_one({
        "script": script,
        "filename": "await.nd",
        "state": dict(state or {}),
        "memory_context": {},
        "input_payload": {},
        "user_id": str(uuid.uuid4()),
        "execution_unit_id": str(uuid.uuid4()),
        "trace_id": "t-await",
    })


# ── the worker, directly ─────────────────────────────────────────────────────


def test_the_first_run_halts_at_the_call_and_reports_a_typed_wait():
    result = _run_one(LINEAR_SCRIPT)

    assert result["status"] == "waiting", result
    assert result["wait_for"] == EVENT
    assert result["resume_schema"] == SCHEMA, "the schema argument must become the wait's resume_schema"
    assert result["error"] is None, "a halt is not a failure"
    out = result["output_state"]
    assert out.get("before") == 1, "state set before the call survives"
    assert "reviewer" not in out and "approved" not in out, "nothing after the call may run on the first pass"
    assert out.get("phase1_ran") is True and "phase2_ran" not in out
    assert out.get("nodus_wait_event_type") == EVENT and "nodus_wait_requested" not in out, (
        "the keys are the wire contract: the event type stays for the adapter, the request flag is consumed"
    )


def test_the_resumed_run_gets_the_payload_back_from_the_same_call():
    delivered = {"nodus_received_events": {EVENT: {"reviewer": "shawn", "approved": True}}}
    result = _run_one(LINEAR_SCRIPT, state=delivered)

    assert result["status"] == "success", result
    out = result["output_state"]
    assert out.get("reviewer") == "shawn" and out.get("approved") is True
    assert out.get("phase1_ran") is True and out.get("phase2_ran") is True, (
        "the script runs from the top — phase 1 runs AGAIN, by contract (DEC-012), on an EMPTY "
        "namespace (no output_state seeding); the call then returns"
    )


def test_an_untyped_await_passes_nil_and_declares_no_schema():
    result = _run_one(UNTYPED_SCRIPT)
    assert result["status"] == "waiting" and result["wait_for"] == EVENT
    assert result.get("resume_schema") is None


def test_an_empty_event_type_is_an_error_not_a_wait():
    result = _run_one('await_event("", nil)\n')
    assert result["status"] == "failure", result
    assert "non-empty" in (result["error"] or "")


# ── end to end: runner + typed check + resume ────────────────────────────────


@pytest.fixture
def scheduler_spy():
    engine = MagicMock()
    with patch("AINDY.kernel.scheduler_engine.get_scheduler_engine", return_value=engine):
        yield engine


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


def test_await_event_end_to_end_through_the_runner_and_the_typed_resume(
    db_session, scheduler_spy, in_process_worker
):
    from AINDY.core.pending_request import PENDING_REQUEST_KEY, ResumePayloadRejected
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg, route_event
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner
    import AINDY.runtime.nodus_adapter  # noqa: F401 — registers `nodus.execute`

    flow = {"start": "nodus.execute", "end": ["nodus.execute"], "edges": {}}
    flow_name = "await_event_flow"
    reg.register_flow(flow_name, flow)
    user_id = str(uuid.uuid4())
    eng = MagicMock()
    try:
        first = PersistentFlowRunner(flow=flow, db=db_session, user_id=user_id, workflow_type=None).start(
            {"nodus_script": LINEAR_SCRIPT}, flow_name=flow_name
        )
        assert first["status"] == "WAITING", first
        run = db_session.query(FlowRun).filter(FlowRun.flow_name == flow_name).one()
        assert run.state[PENDING_REQUEST_KEY]["schema"] == SCHEMA, "the schema reached the run — typed wait"

        with patch("AINDY.kernel.scheduler_engine.get_scheduler_engine", return_value=eng):
            with pytest.raises(ResumePayloadRejected):
                route_event(EVENT, {"note": "no decision fields"}, db_session, run_id=str(run.id))
            route_event(EVENT, {"reviewer": "shawn", "approved": True}, db_session, run_id=str(run.id))

        final = PersistentFlowRunner(flow=flow, db=db_session, user_id=user_id, workflow_type=None).resume(str(run.id))
        assert final["status"] in {"COMPLETED", "SUCCESS"}, final
        db_session.expire_all()
        run = db_session.query(FlowRun).filter(FlowRun.id == run.id).one()
        out = run.state.get("nodus_output_state") or {}
        assert out.get("reviewer") == "shawn" and out.get("approved") is True, out
        assert run.status == "success"
    finally:
        reg.FLOW_REGISTRY.pop(flow_name, None)


# ── the surface that was removed, and the name that is still reserved ────────


def test_the_raise_based_module_and_signal_are_gone():
    import ast
    import importlib.util
    import pathlib

    assert importlib.util.find_spec("AINDY.runtime.nodus_builtins") is None
    root = pathlib.Path(__file__).resolve().parents[2] / "AINDY"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "WorkerWaitSignal":
                pytest.fail(f"{path}: WorkerWaitSignal is referenced")


def test_wait_is_still_a_reserved_nodus_name():
    """The reason the function is `await_event`. If a nodus release frees `wait`, this fails and
    the rename becomes a decision rather than an accident."""
    from nodus.runtime.embedding import NodusRuntime

    with pytest.raises(ValueError, match="built-in"):
        NodusRuntime().register_function("wait", lambda a, b: None, arity=2)
