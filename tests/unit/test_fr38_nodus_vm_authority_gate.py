"""FR-38 — the authority gate on the `nodus_vm` backend (AUTHORITY-NEGOTIATION-1 §9; DEC-068..070).

The design's census counted four `CAPABILITY_DENIED` sites and wired negotiation at the one in
`agent_execute_step` — the `agent_flow` backend's. There is a fifth: `execute_tool`'s own
`check_tool_capability`, reached on `nodus_vm` via the worker's `call_tool` → `run_agent_tool` →
`execute_tool`, in the pool worker, before any adapter code. The app defaults to `nodus_vm`, so
the tool it was asked to declare could never fire there: the run failed with `capability.denied`
×3 (the compiled step's retry loop) and no `AUTHORITY_NEGOTIATED`.

§9.6's order of work is the order of the sections below. Step 1 is the ONE unproven assumption
and had to pass before anything else was written: a guest wait raised from inside `call_tool`
halts the COMPILED step — the retry loop does not re-enter the tool, the next step never runs,
and the reply reads `waiting` with the gate record.

Every worker test drives the real `run_one` with the real compiler's output. Every parent test
drives the real segment chain / resume service on SQLite. Mutation-checked: drop the halt in
`_call_tool` → step 1 fails (the retry loop re-enters and the second step runs); drop `skipped`
from `_REPLAYABLE_STEP_STATUSES` → the skip-resume test fails (the step executes and is denied
again); drop the chain's gate branch → the run is `failed` instead of `waiting`.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only

DENIED_TOOL = "vmgate_send_invoice"
VARIANT_TOOL = "vmgate_queue_invoice"
OK_TOOL = "vmgate_lookup"
EVENT = "agent.authority.decision"


def _read(name, labels):
    from AINDY.platform_layer.metrics import REGISTRY

    return REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.fixture
def tools(monkeypatch):
    """One always-granted tool; one denied tool declaring both a variant and the gate; the
    variant is ALSO denied unless a test grants it. `execute_tool` records what actually ran."""
    from AINDY.agents import tool_registry as reg

    monkeypatch.setenv("AINDY_AUTHORITY_NEGOTIATION", "1")
    monkeypatch.setattr(reg, "TOOL_REGISTRY", {}, raising=True)
    reg.register_tool(
        name=OK_TOOL, risk="low", description="ok", capability="c", required_capability="c",
        category="t", egress_scope="none",
    )(lambda **kw: {"ok": True})
    reg.register_tool(
        name=DENIED_TOOL, risk="low", description="denied", capability="c", required_capability="c",
        category="t", egress_scope="none", degraded_variant=VARIANT_TOOL, on_denial="wait",
    )(lambda **kw: {"ok": True})
    reg.register_tool(
        name=VARIANT_TOOL, risk="low", description="variant", capability="c", required_capability="c",
        category="t", egress_scope="none",
    )(lambda **kw: {"ok": True})

    executed: list[str] = []
    granted = {OK_TOOL}

    def _execute_tool(*, tool_name, args, user_id, db, run_id, execution_token):
        executed.append(tool_name)
        return {"success": True, "result": {"ran": tool_name}, "error": None}

    def _check(*, token, run_id, user_id, tool_name):
        if tool_name in granted:
            return {"ok": True, "error": None, "granted_tools": sorted(granted)}
        return {"ok": False, "error": f"capability for {tool_name} not granted", "granted_tools": sorted(granted)}

    monkeypatch.setattr("AINDY.agents.tool_registry.execute_tool", _execute_tool, raising=True)
    monkeypatch.setattr("AINDY.agents.capability_service.check_tool_capability", _check, raising=True)
    monkeypatch.setattr("AINDY.agents.capability_service.check_execution_capability",
                        lambda **kw: {"ok": True, "error": None}, raising=True)
    return {"executed": executed, "granted": granted}


def _compiled_two_steps():
    from AINDY.runtime.agent_plan_compiler import compile_agent_segment

    steps = [
        {"tool": DENIED_TOOL, "args": {"invoice": "inv_1"}, "risk_level": "low", "description": "first"},
        {"tool": OK_TOOL, "args": {}, "risk_level": "low", "description": "second"},
    ]
    return compile_agent_segment(steps, base_index=0, workflow_name="agent_plan_seg0")


def _run_worker(compiled, *, session_factory, continuation=False, run_id="run-fr38"):
    from AINDY.runtime import nodus_worker

    script = compiled["source"] + f"\nrun_workflow({compiled['workflow_name']})\n"
    # the worker resolves `SessionLocal` lazily; hand it the test's factory through the seam
    original = nodus_worker.run_agent_tool

    def _with_factory(*a, **kw):
        kw.setdefault("session_factory", session_factory)
        return original(*a, **kw)

    with patch("AINDY.runtime.nodus_worker.run_agent_tool", side_effect=_with_factory):
        result = nodus_worker.run_one({
            "script": script, "filename": "fr38.nd", "state": {}, "memory_context": {},
            "input_payload": compiled["input_payload"],
            "context": {"user_id": str(uuid.uuid4()), "execution_unit_id": "eu-fr38", "trace_id": "t-fr38",
                        "run_id": run_id, "execution_token": {"sig": "x"}, "continuation": continuation},
        })
    return result


# ═══════════════════════════════════════════════════════════════════════════════════════════
# §9.6 step 1 — the halt, from inside `call_tool`, against the COMPILED step
# ═══════════════════════════════════════════════════════════════════════════════════════════

def test_step_1_a_guest_wait_from_inside_call_tool_halts_the_compiled_step(tools, db_session):
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.agents.authority_negotiation import GATE_STATE_KEY

    factory = MagicMock(return_value=db_session)
    result = _run_worker(_compiled_two_steps(), session_factory=factory)

    assert result["status"] == "waiting", result
    assert result["wait_for"] == EVENT
    assert result["resume_schema"] == {
        "required": ["decision"],
        "properties": {"decision": {"type": "string"}, "note": {"type": "string"}},
    }
    gate = result["output_state"][GATE_STATE_KEY]
    assert gate["step_index"] == 0 and gate["tool"] == DENIED_TOOL and gate["negotiation_outcome"] == "refused_not_granted"
    assert gate["tool_args"] == {"invoice": "inv_1"}
    # ★ the assumption: the retry loop did NOT re-enter, and the second step never ran
    assert tools["executed"] == [], f"a tool ran past the gate: {tools['executed']}"
    assert "__step_1_result" not in result["output_state"]
    # ★ halted AT the call, not failed after it: a `__step_0_result` here would mean the closure
    # returned the denial and the compiled loop's own `throw` did the halting — which works only
    # while `permission` stays non-retryable, and records a failed step the parent must ignore.
    assert "__step_0_result" not in result["output_state"], result["output_state"]
    # the counter is process-local here and rides the reply instead (DEC-070)
    assert result["authority_negotiation"] == {"refused_not_granted": 1, "waiting": 1}


def test_step_1_control_without_the_gate_the_denial_fails_and_retries_as_before(tools, db_session, monkeypatch):
    """The liveness control for step 1: the same plan with `on_denial` undeclared reaches the
    chokepoint, is denied there, and the compiled loop does what it always did."""
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.agents import tool_registry as reg

    reg.TOOL_REGISTRY[DENIED_TOOL]["on_denial"] = "fail"
    reg.TOOL_REGISTRY[DENIED_TOOL]["degraded_variant"] = None
    # the chokepoint's own denial, as `execute_tool` would produce it
    monkeypatch.setattr(
        "AINDY.agents.tool_registry.execute_tool",
        lambda **kw: {"success": False, "result": None, "error": "denied at the chokepoint", "failure_class": "permission"},
        raising=True,
    )
    result = _run_worker(_compiled_two_steps(), session_factory=MagicMock(return_value=db_session))
    # nodus's workflow runner absorbs the step's `throw`; the reply reads `success` and the
    # PARENT reads the failure from the step results (RECOVERY-GRANULARITY-1's finding).
    step0 = result["output_state"]["__step_0_result"]
    assert step0["success"] is False and step0["failure_class"] == "permission", result
    assert "authority_gate" not in result["output_state"]
    assert "__step_1_result" not in result["output_state"], "halt-on-first-failure held"


def test_a_granted_variant_runs_instead_and_is_recorded_from_the_worker(tools, db_session):
    """§9.3(a): one downgrade, the variant passes the chokepoint, the event comes from the worker."""
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.db.models import AgentEvent, AgentRun

    run = AgentRun(user_id=uuid.uuid4(), goal="g", plan={"steps": []}, executive_summary="g",
                   overall_risk="low", status="executing", steps_total=2, correlation_id="c")
    db_session.add(run)
    db_session.commit()
    tools["granted"].add(VARIANT_TOOL)
    result = _run_worker(_compiled_two_steps(), session_factory=MagicMock(return_value=db_session), run_id=str(run.id))

    assert result["status"] == "success", result
    assert tools["executed"] == [VARIANT_TOOL, OK_TOOL]
    assert result["authority_negotiation"] == {"succeeded": 1}
    events = db_session.query(AgentEvent).filter(AgentEvent.run_id == run.id).all()
    negotiated = [e for e in events if e.event_type == "AUTHORITY_NEGOTIATED"]
    assert len(negotiated) == 1 and negotiated[0].payload["fallback_tool"] == VARIANT_TOOL


def test_with_the_flag_off_nothing_here_runs(tools, db_session, monkeypatch):
    pytest.importorskip("nodus.runtime.embedding")
    monkeypatch.delenv("AINDY_AUTHORITY_NEGOTIATION", raising=False)
    monkeypatch.setattr(
        "AINDY.agents.tool_registry.execute_tool",
        lambda **kw: {"success": False, "result": None, "error": "denied", "failure_class": "permission"},
        raising=True,
    )
    result = _run_worker(_compiled_two_steps(), session_factory=MagicMock(return_value=db_session))
    assert result["output_state"]["__step_0_result"]["success"] is False
    assert "authority_gate" not in result["output_state"]
    assert result["authority_negotiation"] == {}


# ═══════════════════════════════════════════════════════════════════════════════════════════
# The worker on the re-drive: an operator's `skipped` row replays (DEC-069)
# ═══════════════════════════════════════════════════════════════════════════════════════════

def test_a_skipped_row_replays_on_the_re_drive_and_the_tool_does_not_run(tools, db_session):
    pytest.importorskip("nodus.runtime.embedding")
    from datetime import datetime, timezone

    from AINDY.db.models import AgentRun, AgentStep

    run = AgentRun(user_id=uuid.uuid4(), goal="g", plan={"steps": []}, executive_summary="g",
                   overall_risk="low", status="executing", steps_total=2, correlation_id="c")
    db_session.add(run)
    db_session.commit()
    db_session.add(AgentStep(run_id=run.id, step_index=0, tool_name=DENIED_TOOL, status="skipped",
                             result={"authority_gate": "skip", "note": "by hand"},
                             executed_at=datetime.now(timezone.utc)))
    db_session.commit()

    result = _run_worker(_compiled_two_steps(), session_factory=MagicMock(return_value=db_session),
                         continuation=True, run_id=str(run.id))
    assert result["status"] == "success", result
    step0 = result["output_state"]["__step_0_result"]
    assert step0["success"] is True and step0["skipped"] is True and step0["replayed"] is True
    assert step0["authority_gate"] == {"authority_gate": "skip", "note": "by hand"}
    assert tools["executed"] == [OK_TOOL], "the skipped tool ran, or the second step did not"
    assert result["output_state"]["__step_1_result"]["success"] is True


def test_a_skipped_row_does_not_replay_on_a_fresh_run(tools, db_session):
    """DEC-066's rule survives: only a CONTINUED run replays; a fresh run with a row is upstream's
    bug and executing is the safe direction — here the fresh run reaches the gate again."""
    pytest.importorskip("nodus.runtime.embedding")
    from datetime import datetime, timezone

    from AINDY.db.models import AgentRun, AgentStep

    run = AgentRun(user_id=uuid.uuid4(), goal="g", plan={"steps": []}, executive_summary="g",
                   overall_risk="low", status="executing", steps_total=2, correlation_id="c")
    db_session.add(run)
    db_session.commit()
    db_session.add(AgentStep(run_id=run.id, step_index=0, tool_name=DENIED_TOOL, status="skipped",
                             result={}, executed_at=datetime.now(timezone.utc)))
    db_session.commit()
    result = _run_worker(_compiled_two_steps(), session_factory=MagicMock(return_value=db_session),
                         continuation=False, run_id=str(run.id))
    assert result["status"] == "waiting" and tools["executed"] == []


# ═══════════════════════════════════════════════════════════════════════════════════════════
# The parent, for real: the segment chain parks, the resume service decides, the re-drive runs
# ═══════════════════════════════════════════════════════════════════════════════════════════

def _gate(step_index=0, tool=DENIED_TOOL):
    from AINDY.agents.authority_negotiation import build_authority_gate

    g = build_authority_gate(step_index=step_index, tool_name=tool, denied_error="not granted",
                             negotiation_outcome="refused_not_granted", variant=VARIANT_TOOL)
    g["tool_args"] = {"invoice": "inv_1"}
    return g


def _gate_flow_result(gate):
    """What the nodus.execute node returns for a segment halted at the gate (a terminal result)."""
    state = {"nodus_wait_event_type": EVENT, "authority_gate": gate}
    return {
        "status": "SUCCESS", "run_id": "flow-run-gate", "trace_id": "t",
        "state": {"nodus_output_state": state, "nodus_status": "authority_gate", "authority_gate": gate},
        "data": {"status": "success", "output_state": state},
    }


def _redrive_flow_result(**kw):
    """What the worker returns on the re-drive: the skipped row replayed, the rest fresh."""
    ip = kw.get("input_payload") or {}
    indices = sorted(int(k[len("__step_"):-len("_tool")]) for k in ip if k.endswith("_tool"))
    output_state = {}
    for i in indices:
        if i == 0:
            output_state["__step_0_result"] = {"success": True, "result": None, "error": None, "replayed": True,
                                               "skipped": True, "authority_gate": {"authority_gate": "skip", "note": "n"}}
        else:
            output_state[f"__step_{i}_result"] = {"success": True, "result": {"i": i}, "error": None}
    assert (kw.get("extra_initial_state") or {}).get("__continuation") is True, "the re-drive must be a continuation"
    return {"status": "SUCCESS", "run_id": "flow-run-redrive", "trace_id": "t",
            "state": {"nodus_output_state": output_state, "nodus_status": "success"},
            "data": {"status": "success", "output_state": output_state}}


@pytest.fixture
def chain(monkeypatch, db_session):
    """The real segment chain on SQLite, the flow stubbed at `run_nodus_script_via_flow`, the
    scheduler captured so the test fires the resume itself."""
    from sqlalchemy.orm import sessionmaker

    from AINDY.runtime import nodus_execution_service as svc

    monkeypatch.setenv("AINDY_AUTHORITY_NEGOTIATION", "1")
    monkeypatch.setattr("AINDY.agents.capability_service.check_execution_capability", lambda **kw: {"ok": True})
    monkeypatch.setattr("AINDY.core.execution_signal_helper.queue_system_event", lambda **kw: None)
    captured: dict = {}

    class _Sched:
        def register_wait(self, **kw):
            captured.update(kw)

        def waiting_for(self, run_id):
            return None

    monkeypatch.setattr("AINDY.kernel.scheduler_engine.get_scheduler_engine", lambda: _Sched())
    monkeypatch.setattr("AINDY.db.database.SessionLocal", sessionmaker(bind=db_session.get_bind()))
    calls: list = []

    def _flow(**kw):
        calls.append(kw)
        return _gate_flow_result(_gate()) if len(calls) == 1 else _redrive_flow_result(**kw)

    monkeypatch.setattr(svc, "run_nodus_script_via_flow", _flow)
    return {"captured": captured, "calls": calls, "svc": svc}


def _vm_run(db):
    from AINDY.db.models import AgentRun

    run = AgentRun(id=uuid.uuid4(), user_id=uuid.uuid4(), goal="g", status="executing", steps_total=2,
                   plan=_PLAN, correlation_id=f"run_{uuid.uuid4()}")
    db.add(run)
    db.commit()
    return run


_PLAN = {"steps": [
    {"tool": DENIED_TOOL, "args": {"invoice": "inv_1"}, "risk_level": "low", "description": "first"},
    {"tool": OK_TOOL, "args": {}, "risk_level": "low", "description": "second"},
]}


def _start(chain, db, run):
    return chain["svc"].execute_agent_run_via_workflow(
        run_id=str(run.id), plan=_PLAN, user_id=str(run.user_id), db=db,
        execution_token={"token_hash": "h", "granted_tools": [OK_TOOL]},
    )


def _reload(db, run):
    from AINDY.db.models import AgentRun

    db.expire_all()
    return db.query(AgentRun).filter(AgentRun.id == run.id).one()


def _rows(db, run):
    from AINDY.db.models import AgentStep

    return db.query(AgentStep).filter(AgentStep.run_id == run.id).order_by(AgentStep.step_index).all()


def test_the_chain_parks_the_run_on_the_gate_instead_of_failing_it(chain, db_session):
    run = _vm_run(db_session)
    result = _start(chain, db_session, run)

    assert result["status"] == "WAITING" and result["wait_for"] == EVENT, result
    agent = _reload(db_session, run)
    assert agent.status == "waiting"
    ws = agent.wait_state
    assert ws["event_type"] == EVENT and ws["continuation"] is True and ws["resume_segment_index"] == 0
    assert ws["authority_gate"]["step_index"] == 0 and ws["authority_gate"]["tool"] == DENIED_TOOL
    assert agent.result == {"steps": []}, "the results accumulated BEFORE the segment — the re-drive re-contributes its own"
    assert chain["captured"]["wait_for_event"] == EVENT and chain["captured"]["eu_type"] == "agent"
    assert _rows(db_session, run) == [], "no step row: the gate is the operator's to write"


def test_skip_writes_the_row_then_the_re_drive_completes_the_run_without_counting_it(chain, db_session):
    from AINDY.agents.runtime_api import resume_agent_run_runtime

    run = _vm_run(db_session)
    _start(chain, db_session, run)
    fired: list = []

    def _publish(event_type, *, correlation_id=None, run_id=None):
        # `record_agent_event` publishes its own `agent.*` notifications through the same bus;
        # only the gate's wake matters here
        if event_type != EVENT:
            return 0
        fired.append((event_type, run_id))
        chain["captured"]["resume_callback"]()
        return 1

    with patch("AINDY.kernel.event_bus.publish_event", _publish):
        reply = resume_agent_run_runtime(db=db_session, user_id=str(run.user_id), run_id=str(run.id),
                                         payload={"decision": "skip", "note": "n"})
    assert reply["authority_gate"]["decision"] == "skip" and fired == [(EVENT, str(run.id))]
    assert len(chain["calls"]) == 2, "the SAME segment was re-driven exactly once"

    agent = _reload(db_session, run)
    assert agent.status == "completed", (agent.status, agent.error_message)
    assert agent.wait_state is None
    rows = _rows(db_session, run)
    assert [(r.step_index, r.status) for r in rows] == [(0, "skipped"), (1, "success")]
    assert rows[0].result == {"authority_gate": "skip", "note": "n"} and rows[0].tool_args == {"invoice": "inv_1"}
    assert agent.steps_completed == 1, "a skipped step is not a completed one"
    assert [s["status"] for s in agent.result["steps"]] == ["skipped", "success"]


def test_abort_fails_the_run_here_and_publishes_nothing(chain, db_session):
    from AINDY.agents.runtime_api import resume_agent_run_runtime

    run = _vm_run(db_session)
    _start(chain, db_session, run)
    with patch("AINDY.kernel.event_bus.publish_event", return_value=0) as publish:
        reply = resume_agent_run_runtime(db=db_session, user_id=str(run.user_id), run_id=str(run.id),
                                         payload={"decision": "abort", "note": "no"})
    woken = [c for c in publish.call_args_list if c.args and c.args[0] == EVENT]
    assert woken == [] and reply["authority_gate"]["run_status"] == "failed"
    agent = _reload(db_session, run)
    assert agent.status == "failed" and "aborted by operator" in agent.error_message and "no" in agent.error_message
    assert agent.wait_state is None
    rows = _rows(db_session, run)
    assert [(r.step_index, r.status) for r in rows] == [(0, "failed")]
    assert len(chain["calls"]) == 1, "no re-drive"


def test_a_resume_without_a_decision_is_refused_and_the_run_stays_parked(chain, db_session):
    from fastapi import HTTPException

    from AINDY.agents.runtime_api import resume_agent_run_runtime

    run = _vm_run(db_session)
    _start(chain, db_session, run)
    with pytest.raises(HTTPException) as exc:
        resume_agent_run_runtime(db=db_session, user_id=str(run.user_id), run_id=str(run.id))
    assert exc.value.status_code == 409 and "skip | abort" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        resume_agent_run_runtime(db=db_session, user_id=str(run.user_id), run_id=str(run.id),
                                 payload={"decision": "grant"})
    assert exc.value.status_code == 422
    agent = _reload(db_session, run)
    assert agent.status == "waiting" and agent.wait_state["authority_gate"]["last_refused_decision"] == "grant"
    assert _rows(db_session, run) == []


def test_rehydration_rebuilds_a_gate_park_as_a_continuation_of_the_same_segment(chain, db_session):
    from datetime import datetime, timezone

    from AINDY.core.agent_run_rehydration import rehydrate_waiting_agent_runs
    from AINDY.db.models import AgentStep

    run = _vm_run(db_session)
    _start(chain, db_session, run)
    chain["captured"].clear()  # "the process restarted": the live registration is gone
    # the operator decided while we were down — the row is the decision's home
    db_session.add(AgentStep(run_id=run.id, step_index=0, tool_name=DENIED_TOOL, status="skipped",
                             result={"authority_gate": "skip", "note": "n"}, executed_at=datetime.now(timezone.utc)))
    db_session.commit()

    assert rehydrate_waiting_agent_runs(db_session) == 1
    assert chain["captured"]["wait_for_event"] == EVENT
    chain["captured"]["resume_callback"]()  # the re-drive asserts `__continuation` itself
    agent = _reload(db_session, run)
    assert agent.status == "completed" and agent.steps_completed == 1
    assert [(r.step_index, r.status) for r in _rows(db_session, run)] == [(0, "skipped"), (1, "success")]


# ── FR-44 — a resume served by a process that does not hold the run's wait ──────────────────
#
# The wait is an in-memory registration on the process that parked the run. The app re-ran the
# nodus_vm denial with the park made in a `docker exec`: the api's `POST …/resume {"decision":
# "skip"}` answered 200 `resuming` with `waiters_notified: 0`, and the run sat `waiting` until an
# api restart re-armed it. These drive the real route function with a scheduler that holds
# registrations PER PROCESS, so "a different process" is a scheduler that never saw the park.


class _ProcessScheduler:
    """One process's in-memory waits, keyed by run id, woken the way `publish_event` wakes."""

    def __init__(self):
        self.waits: dict = {}

    def register_wait(self, **kw):
        self.waits[kw["run_id"]] = kw

    def waiting_for(self, run_id):
        return self.waits.get(run_id)

    def publish(self, event_type, *, correlation_id=None, run_id=None):
        if event_type != EVENT:
            return 0  # `record_agent_event`'s own agent.* notifications
        wait = self.waits.pop(run_id, None)
        if wait is None:
            return 0
        wait["resume_callback"]()
        return 1


def _park_then_switch_process(chain, db, monkeypatch):
    """Park on process A, then serve everything after it from process B."""
    parking = _ProcessScheduler()
    monkeypatch.setattr("AINDY.kernel.scheduler_engine.get_scheduler_engine", lambda: parking)
    run = _vm_run(db)
    _start(chain, db, run)
    assert str(run.id) in parking.waits, "liveness: the park registered on the parking process"
    serving = _ProcessScheduler()
    monkeypatch.setattr("AINDY.kernel.scheduler_engine.get_scheduler_engine", lambda: serving)
    return run, parking, serving


def test_fr44_a_resume_on_another_process_arms_the_wait_and_the_run_completes(chain, db_session, monkeypatch):
    from AINDY.agents.runtime_api import resume_agent_run_runtime

    run, _parking, serving = _park_then_switch_process(chain, db_session, monkeypatch)
    assert serving.waits == {}, "the serving process never saw the park: the app's topology"

    with patch("AINDY.kernel.event_bus.publish_event", serving.publish):
        reply = resume_agent_run_runtime(db=db_session, user_id=str(run.user_id), run_id=str(run.id),
                                         payload={"decision": "skip", "note": "n"})

    assert reply["waiters_notified"] == 1
    assert reply["authority_gate"]["run_status"] == "resuming"
    agent = _reload(db_session, run)
    assert agent.status == "completed", (agent.status, agent.error_message)
    assert [(r.step_index, r.status) for r in _rows(db_session, run)] == [(0, "skipped"), (1, "success")]
    assert len(chain["calls"]) == 2, "re-driven exactly once"


def test_fr44_a_wait_this_process_already_holds_is_not_armed_twice(chain, db_session, monkeypatch):
    """The live registration wins: the on-demand arm is for the gap between boots only."""
    from AINDY.agents.runtime_api import resume_agent_run_runtime

    parking = _ProcessScheduler()
    monkeypatch.setattr("AINDY.kernel.scheduler_engine.get_scheduler_engine", lambda: parking)
    run = _vm_run(db_session)
    _start(chain, db_session, run)
    original = parking.waits[str(run.id)]["resume_callback"]

    with patch("AINDY.core.agent_run_rehydration.rehydrate_waiting_agent_runs") as rehydrate, \
            patch("AINDY.kernel.event_bus.publish_event", parking.publish):
        reply = resume_agent_run_runtime(db=db_session, user_id=str(run.user_id), run_id=str(run.id),
                                         payload={"decision": "skip"})
    rehydrate.assert_not_called()
    assert reply["waiters_notified"] == 1 and _reload(db_session, run).status == "completed"
    assert original is not None


def test_fr44_nothing_woken_is_reported_as_waiting_not_resuming(chain, db_session, monkeypatch):
    """Ask 2: a 200 that says `resuming` while the run cannot move is the quiet failure. Here
    the run cannot be rebuilt from its row (plan gone), so there is nothing to arm."""
    from AINDY.agents.runtime_api import resume_agent_run_runtime

    run, _parking, serving = _park_then_switch_process(chain, db_session, monkeypatch)
    agent = _reload(db_session, run)
    agent.plan = {}
    db_session.commit()

    with patch("AINDY.kernel.event_bus.publish_event", serving.publish):
        reply = resume_agent_run_runtime(db=db_session, user_id=str(run.user_id), run_id=str(run.id),
                                         payload={"decision": "skip", "note": "n"})

    assert reply["waiters_notified"] == 0
    assert reply["authority_gate"]["run_status"] == "waiting"
    assert reply["authority_gate"]["reason"] == "no_local_waiter_woken"
    assert _reload(db_session, run).status == "waiting"
    # the decision is still the row's: the next re-drive (a restart's rehydration) replays it
    assert [(r.step_index, r.status) for r in _rows(db_session, run)] == [(0, "skipped")]


def test_fr44_an_abort_arms_nothing(chain, db_session, monkeypatch):
    """An abort fails the run here and publishes nothing. A wait armed for it would sit in
    memory with no event ever coming."""
    from AINDY.agents.runtime_api import resume_agent_run_runtime

    run, _parking, serving = _park_then_switch_process(chain, db_session, monkeypatch)
    with patch("AINDY.kernel.event_bus.publish_event", serving.publish):
        reply = resume_agent_run_runtime(db=db_session, user_id=str(run.user_id), run_id=str(run.id),
                                         payload={"decision": "ABORT "})
    assert reply["authority_gate"]["run_status"] == "failed"
    assert serving.waits == {}


def test_fr44_an_ordinary_plan_wait_is_armed_too(db_session, monkeypatch):
    """Not only the gate: a plan-declared WAIT served by another process had the same gap."""
    from AINDY.agents.runtime_api import resume_agent_run_runtime
    from AINDY.db.models import AgentRun

    serving = _ProcessScheduler()
    monkeypatch.setattr("AINDY.kernel.scheduler_engine.get_scheduler_engine", lambda: serving)
    run = AgentRun(id=uuid.uuid4(), user_id=uuid.uuid4(), goal="g", status="waiting", steps_total=1,
                   plan={"steps": [{"tool": OK_TOOL, "args": {}, "risk_level": "low", "description": "d"}]},
                   correlation_id=f"run_{uuid.uuid4()}",
                   wait_state={"event_type": "invoice.approved", "resume_segment_index": 0})
    db_session.add(run)
    db_session.commit()
    published: list = []

    def _publish(event_type, *, correlation_id=None, run_id=None):
        published.append(event_type)
        return 1 if str(run.id) in serving.waits else 0

    with patch("AINDY.kernel.event_bus.publish_event", _publish):
        reply = resume_agent_run_runtime(db=db_session, user_id=str(run.user_id), run_id=str(run.id))

    assert str(run.id) in serving.waits, "armed on the serving process before the publish"
    assert serving.waits[str(run.id)]["wait_for_event"] == "invoice.approved"
    assert published == ["invoice.approved"] and reply["waiters_notified"] == 1
