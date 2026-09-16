"""`AUTHORITY-NEGOTIATION-1` phase 2 — the WAIT-gate fallback kind.

Phase 1 offered one downgrade to a tool-declared variant. When no variant recovers the denial, a
tool that declared ``on_denial="wait"`` now PARKS THE RUN on the durable wait instead of failing
it: the accumulated state survives, and an operator decides — ``skip`` the step or ``abort`` the
run. Never ``grant`` (design §7: no widening path, not even an authorised one).

★ This is the first time `AGENT_FLOW` — the backend the app runs — has ever waited. Its
orchestration read any non-SUCCESS flow result as failure, so the test that matters most here is
the one where the AgentRun is ``waiting`` and not ``failed`` while the FlowRun is parked.

★ The gate's resume payload is TYPED — the runtime's own first use of `WAIT-TYPED-CONTRACT-1`'s
`resume_schema` (#677): a resume without ``decision`` is refused at the door and the run stays
parked; an unknown decision re-parks it, recorded on the gate.

Every test drives the REAL `PersistentFlowRunner` over the REAL `AGENT_FLOW` with real
`AgentRun` / `AgentStep` rows on SQLite; only the tool body and the capability check are stubbed,
at the seams the production code calls (`execute_tool`, `check_tool_capability`). The resume goes
through the REAL `route_event` (the injection + the typed check) and the REAL `resume()`.

Mutation-checked: drop `denial_gate_declared` (always False) → the park tests fail; make the
orchestration skip the WAITING branch → `test_the_agent_run_is_waiting_not_failed` fails; drop
the decision vocabulary check → the unknown-decision test fails; drop `_mark_agent_run_failed`
from the abort path → the abort test fails on the AgentRun status; drop the `resume_schema` from
the WAIT result → the 422-path test fails.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only

DENIED_TOOL = "gate_send_invoice"
VARIANT_TOOL = "gate_queue_invoice"
OK_TOOL = "gate_lookup"


@pytest.fixture
def scheduler_spy():
    engine = MagicMock()
    with patch("AINDY.kernel.scheduler_engine.get_scheduler_engine", return_value=engine):
        yield engine


@pytest.fixture
def tools(monkeypatch):
    """A registry with one always-granted tool, one denied tool declaring the gate, and a variant
    that is also denied (so the gate is reached AFTER a failed negotiation); tool bodies succeed."""
    from AINDY.agents import tool_registry as reg
    from AINDY.runtime import nodus_adapter

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

    def _execute_tool(*, tool_name, args, user_id, db, run_id, execution_token):
        executed.append(tool_name)
        return {"success": True, "result": {"ran": tool_name}, "error": None}

    def _check(*, token, run_id, user_id, tool_name):
        if tool_name == OK_TOOL:
            return {"ok": True, "error": None, "granted_tools": [OK_TOOL]}
        return {"ok": False, "error": f"capability for {tool_name} not granted", "granted_tools": [OK_TOOL]}

    monkeypatch.setattr(nodus_adapter, "execute_tool", _execute_tool, raising=True)
    monkeypatch.setattr(nodus_adapter, "check_tool_capability", _check, raising=True)
    monkeypatch.setattr("AINDY.agents.capability_service.check_tool_capability", _check, raising=True)
    monkeypatch.setattr("AINDY.agents.capability_service.check_execution_capability",
                        lambda **kw: {"ok": True, "error": None}, raising=True)
    return executed


def _agent_run(db, *, steps):
    from AINDY.db.models import AgentRun

    run = AgentRun(
        user_id=uuid.uuid4(), goal="gate test", plan={"steps": steps}, executive_summary="g",
        overall_risk="low", status="executing", steps_total=len(steps),
        correlation_id=f"run_{uuid.uuid4()}",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _plan_steps():
    return [
        {"tool": OK_TOOL, "args": {}, "risk_level": "low", "description": "first"},
        {"tool": DENIED_TOOL, "args": {"invoice": "inv_1"}, "risk_level": "low", "description": "second"},
        {"tool": OK_TOOL, "args": {}, "risk_level": "low", "description": "third"},
    ]


def _start(db, run, token={"sig": "x"}):
    from AINDY.runtime.nodus_execution_service import execute_agent_flow_orchestration

    return execute_agent_flow_orchestration(
        run_id=str(run.id), plan={"steps": _plan_steps()}, user_id=str(run.user_id), db=db,
        correlation_id=run.correlation_id, execution_token=token, capability_token=None,
    )


def _flow_run(db, flow_run_id):
    from AINDY.db.models.flow_run import FlowRun

    db.expire_all()
    return db.query(FlowRun).filter(FlowRun.id == str(flow_run_id)).one()


def _reload(db, run):
    from AINDY.db.models import AgentRun

    db.expire_all()
    return db.query(AgentRun).filter(AgentRun.id == run.id).one()


def _resume(db, flow_run_id, payload, *, user_id):
    """The route's effect: `route_event` (typed check + injection + wake) then the REAL
    `resume()` on the parked FlowRun, as the scheduler callback would."""
    from AINDY.runtime.flow_engine import route_event
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner
    from AINDY.runtime.nodus_adapter import AGENT_FLOW

    route_event("agent.authority.decision", payload, db, run_id=str(flow_run_id))
    return PersistentFlowRunner(
        flow=AGENT_FLOW, db=db, user_id=user_id, workflow_type="agent_execution"
    ).resume(str(flow_run_id))


def _steps(db, run):
    from AINDY.db.models import AgentStep

    return db.query(AgentStep).filter(AgentStep.run_id == run.id).order_by(AgentStep.step_index).all()


# ── the park ─────────────────────────────────────────────────────────────────


def test_the_agent_run_is_waiting_not_failed(db_session, scheduler_spy, tools):
    """★★ The load-bearing one. AGENT_FLOW never waited before; its orchestration read any
    non-SUCCESS result as failure."""
    from AINDY.core.pending_request import PENDING_REQUEST_KEY

    run = _agent_run(db_session, steps=_plan_steps())
    result = _start(db_session, run)

    assert result["status"] == "WAITING", result
    assert result["data"]["waiting_for"] == "agent.authority.decision"
    assert tools == [OK_TOOL], "the first step ran; the denied one did not; nothing after it"

    flow = _flow_run(db_session, result["run_id"])
    assert flow.status == "waiting" and flow.waiting_for == "agent.authority.decision"
    gate = flow.state.get("authority_gate")
    assert gate and gate["step_index"] == 1 and gate["tool"] == DENIED_TOOL
    assert gate["negotiation_outcome"] == "refused_not_granted" and gate["variant"] == VARIANT_TOOL
    assert flow.state[PENDING_REQUEST_KEY]["schema"]["required"] == ["decision"], (
        "the gate's resume is TYPED — the runtime's first use of resume_schema"
    )

    agent = _reload(db_session, run)
    assert agent.status == "waiting", f"the AgentRun is {agent.status!r}; the orchestration read the park as a failure"
    assert agent.wait_state["event_type"] == "agent.authority.decision"
    assert agent.wait_state["flow_run_id"] == str(flow.id)
    assert agent.wait_state["authority_gate"]["step_index"] == 1
    assert agent.steps_completed == 1
    scheduler_spy.register_wait.assert_called_once()
    assert scheduler_spy.register_wait.call_args.kwargs["wait_for_event"] == "agent.authority.decision"


def test_without_the_declaration_the_denial_fails_exactly_as_before(db_session, scheduler_spy, tools):
    from AINDY.agents import tool_registry as reg

    reg.TOOL_REGISTRY[DENIED_TOOL]["on_denial"] = "fail"
    run = _agent_run(db_session, steps=_plan_steps())
    result = _start(db_session, run)

    assert result["status"] == "FAILED", result
    agent = _reload(db_session, run)
    assert agent.status == "failed" and "Capability denied" in (agent.error_message or "")
    scheduler_spy.register_wait.assert_not_called()


def test_with_the_flag_off_the_declaration_is_inert(db_session, scheduler_spy, tools, monkeypatch):
    monkeypatch.delenv("AINDY_AUTHORITY_NEGOTIATION", raising=False)
    run = _agent_run(db_session, steps=_plan_steps())
    result = _start(db_session, run)

    assert result["status"] == "FAILED", result
    assert _reload(db_session, run).status == "failed"
    scheduler_spy.register_wait.assert_not_called()


# ── the decisions ────────────────────────────────────────────────────────────


def test_skip_resumes_the_run_and_completes_it_with_the_step_recorded_skipped(db_session, scheduler_spy, tools):
    run = _agent_run(db_session, steps=_plan_steps())
    parked = _start(db_session, run)
    flow_id = parked["run_id"]

    final = _resume(db_session, flow_id, {"decision": "skip", "note": "handled by hand"}, user_id=str(run.user_id))

    assert final["status"] in {"COMPLETED", "SUCCESS"}, final
    assert tools == [OK_TOOL, OK_TOOL], "steps 1 and 3 ran; the denied step 2 was skipped, not retried"
    agent = _reload(db_session, run)
    assert agent.status == "completed", agent.status
    assert agent.wait_state is None
    rows = _steps(db_session, run)
    assert [r.status for r in rows] == ["success", "skipped", "success"]
    assert rows[1].result == {"authority_gate": "skip", "note": "handled by hand"}
    assert (agent.result or {}).get("steps", [{}, {}])[1].get("status") == "skipped"
    flow = _flow_run(db_session, flow_id)
    assert flow.status == "success" and "authority_gate" not in flow.state and "event" not in flow.state


def test_abort_fails_the_run_with_the_operators_reason(db_session, scheduler_spy, tools):
    run = _agent_run(db_session, steps=_plan_steps())
    parked = _start(db_session, run)
    flow_id = parked["run_id"]

    final = _resume(db_session, flow_id, {"decision": "abort", "note": "not this quarter"}, user_id=str(run.user_id))

    assert final["status"] == "FAILED", final
    assert tools == [OK_TOOL], "nothing ran after the abort"
    agent = _reload(db_session, run)
    assert agent.status == "failed", (
        f"the AgentRun is {agent.status!r}: a failure AFTER a resume never reached the AgentRun "
        "on this backend before — the orchestration's post-hoc block only runs after start()"
    )
    assert "aborted by operator" in agent.error_message and "not this quarter" in agent.error_message
    assert agent.wait_state is None and agent.completed_at is not None
    assert [r.status for r in _steps(db_session, run)] == ["success", "failed"]
    assert _flow_run(db_session, flow_id).status == "failed"


def test_an_unknown_decision_re_parks_the_run_and_records_it(db_session, scheduler_spy, tools):
    """The schema admits any string; the vocabulary does not. A typo must not kill a run an
    operator is trying to steer — and `grant` is not in the vocabulary, on purpose."""
    run = _agent_run(db_session, steps=_plan_steps())
    parked = _start(db_session, run)
    flow_id = parked["run_id"]

    again = _resume(db_session, flow_id, {"decision": "grant"}, user_id=str(run.user_id))

    assert again["status"] == "WAITING", again
    assert tools == [OK_TOOL], "nothing ran"
    flow = _flow_run(db_session, flow_id)
    assert flow.status == "waiting"
    assert flow.state["authority_gate"]["last_refused_decision"] == "grant"
    assert "event" not in flow.state, "the refused payload is consumed, not left to re-bridge"
    assert scheduler_spy.register_wait.call_count == 2, "parked twice: the original and the re-park"

    # The run is still steerable.
    final = _resume(db_session, flow_id, {"decision": "skip"}, user_id=str(run.user_id))
    assert final["status"] in {"COMPLETED", "SUCCESS"}, final
    assert _reload(db_session, run).status == "completed"


def test_a_payload_without_a_decision_is_refused_at_the_door(db_session, scheduler_spy, tools):
    """`WAIT-TYPED-CONTRACT-1` in use: nothing is injected, nothing woken, the run stays parked
    — the route answers 422 with this exception's errors."""
    from AINDY.core.pending_request import ResumePayloadRejected
    from AINDY.runtime.flow_engine import route_event

    run = _agent_run(db_session, steps=_plan_steps())
    parked = _start(db_session, run)
    flow_id = parked["run_id"]

    with pytest.raises(ResumePayloadRejected) as exc_info:
        route_event("agent.authority.decision", {"note": "forgot the decision"}, db_session, run_id=str(flow_id))

    assert any("decision" in e for e in exc_info.value.errors)
    flow = _flow_run(db_session, flow_id)
    assert flow.status == "waiting" and "event" not in flow.state
    assert _reload(db_session, run).status == "waiting"


# ── the record ───────────────────────────────────────────────────────────────


def test_the_gate_is_recorded_as_a_negotiation_outcome(db_session, scheduler_spy, tools):
    from AINDY.db.models.agent_event import AgentEvent
    from AINDY.platform_layer.metrics import REGISTRY

    def _count(label):
        return REGISTRY.get_sample_value("aindy_authority_negotiation_total", {"outcome": label}) or 0.0

    before_wait, before_skip = _count("waiting"), _count("gate_skip")
    run = _agent_run(db_session, steps=_plan_steps())
    parked = _start(db_session, run)
    _resume(db_session, parked["run_id"], {"decision": "skip"}, user_id=str(run.user_id))

    db_session.expire_all()
    negotiated = (
        db_session.query(AgentEvent)
        .filter(AgentEvent.run_id == run.id, AgentEvent.event_type == "AUTHORITY_NEGOTIATED")
        .order_by(AgentEvent.created_at)
        .all()
    )
    outcomes = [(e.payload or {}).get("outcome") for e in negotiated]
    assert outcomes == ["waiting", "waiting"], outcomes
    assert (negotiated[0].payload or {}).get("wait_for") == "agent.authority.decision"
    assert (negotiated[1].payload or {}).get("decision") == "skip"
    assert _count("waiting") == before_wait + 1 and _count("gate_skip") == before_skip + 1


def test_on_denial_is_validated_at_registration():
    from AINDY.agents import tool_registry as reg

    with pytest.raises(ValueError, match="on_denial"):
        reg.register_tool(
            name="gate_bad", risk="low", description="d", capability="c", required_capability="c",
            category="t", egress_scope="none", on_denial="park",
        )(lambda **kw: None)
