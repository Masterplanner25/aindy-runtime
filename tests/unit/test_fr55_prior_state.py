"""FR-55 (b), DEC-135 — a resumed guest reads what it saved before it parked, through `prior_state(key)`.

A guest script that waits runs again from the top when it is resumed, with an empty namespace plus
`nodus_received_events` (DEC-012, which stands: `get_state` is unchanged). Before this, what it had
saved with `set_state` was recorded on the run and unreachable from the script, so a loop that
sleeps between batches could not tell where it had got to and re-ran every earlier batch on every
wake. `prior_state(key)` is a separate, read-only accessor over what this node's earlier runs saved.

Pinned through the REAL worker (`run_one`) and the REAL interpreter, and end to end through
`PersistentFlowRunner` + `route_event` across two parks:

* `prior_state` returns what an earlier run saved; `get_state` still does not (DEC-012);
* nil on a first run, and the runtime's own wire keys never come back (a handed-back wait key would
  make the resumed run look as if it had already asked to wait);
* a key saved in the first run survives a second park in which it was not saved again;
* a finished node leaves nothing behind for the next node or run.

Mutation-checked: drop the worker registration → the script fails on an unknown name; drop the
WAIT patch's carry → the second run reads nil; drop the merge with the earlier saved state → the
two-park test loses `first_only`; drop the filter → a `nodus_wait_*` key comes back; drop the pop
on completion → the finished run still carries the key.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only

# Counts batches across parks. `0i` is an integer literal; a bare `0` is a float in nodus.
LOOP_SCRIPT = """
let done = prior_state("done")
if (done == nil) { done = 0i }
set_state("get_state_sees_done", get_state("done"))
set_state("done", done + 1i)
if (done == 0i) { set_state("first_only", "kept") }
let tick = await_event("tick." + str(done), nil)
set_state("finished_at", done)
"""


def _run_one(script: str, *, state: dict | None = None, prior: dict | None = None) -> dict:
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.runtime import nodus_worker

    context = {"user_id": str(uuid.uuid4()), "execution_unit_id": str(uuid.uuid4()), "trace_id": "t-prior"}
    if prior is not None:
        context["prior_state"] = prior
    return nodus_worker.run_one({
        "script": script,
        "filename": "prior.nd",
        "state": dict(state or {}),
        "memory_context": {},
        "input_payload": {},
        "context": context,
    })


# ── the worker, directly ─────────────────────────────────────────────────────


def test_a_first_run_reads_nil_and_parks_on_its_first_name():
    result = _run_one(LOOP_SCRIPT)

    assert result["status"] == "waiting", result
    assert result["wait_for"] == "tick.0"
    assert result["output_state"]["done"] == 1


def test_a_resumed_run_reads_what_it_saved_and_get_state_still_does_not():
    result = _run_one(LOOP_SCRIPT, prior={"done": 3})

    assert result["wait_for"] == "tick.3", "prior_state must return the saved value"
    assert result["output_state"]["done"] == 4
    assert result["output_state"]["get_state_sees_done"] is None, (
        "DEC-012 stands: the saved value is NOT seeded into the namespace get_state reads"
    )


def test_prior_state_is_read_only():
    script = """
let saved = prior_state("cfg")
saved["changed"] = true
set_state("cfg_again", prior_state("cfg"))
"""
    result = _run_one(script, prior={"cfg": {"a": 1}})

    assert result["status"] == "success", result
    assert result["output_state"]["cfg_again"] == {"a": 1}
    assert "cfg" not in result["output_state"], "a prior value reaches output_state only if set again"


def test_the_runtime_wire_keys_never_come_back():
    from AINDY.runtime.nodus_adapter import _carryable_state

    carried = _carryable_state({
        "done": 2,
        "nodus_wait_requested": True,
        "nodus_wait_event_type": "tick.1",
        "nodus_wait_resume_schema": {"required": ["x"]},
        "nodus_received_events": {"tick.0": {}},
        "__step_0_result": {"ok": True},
    })
    assert carried == {"done": 2}


def test_prior_state_is_scoped_to_its_node():
    from AINDY.runtime.nodus_adapter import PRIOR_STATE_KEY, _prior_state_for

    state = {PRIOR_STATE_KEY: {"node": "a", "saved": {"done": 2}}}
    assert _prior_state_for(state, "a") == {"done": 2}
    assert _prior_state_for(state, "b") == {}
    assert _prior_state_for({}, "a") == {}


# ── end to end: runner, two parks, completion ────────────────────────────────


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


def test_a_loop_resumes_where_it_got_to_across_two_parks(db_session, scheduler_spy, in_process_worker):
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg, route_event
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner
    from AINDY.runtime.nodus_adapter import PRIOR_STATE_KEY

    flow = {"start": "nodus.execute", "end": ["nodus.execute"], "edges": {}}
    flow_name = "fr55_prior_state_flow"
    reg.register_flow(flow_name, flow)
    user_id = str(uuid.uuid4())
    # Two batches: parks on tick.0, then tick.1, and finishes on the third run without waiting.
    script = """
let done = prior_state("done")
if (done == nil) { done = 0i }
set_state("done", done + 1i)
if (done == 0i) { set_state("first_only", "kept") }
if (done < 2i) { let tick = await_event("tick." + str(done), nil) }
set_state("finished_at", done)
"""

    def _runner():
        return PersistentFlowRunner(flow=flow, db=db_session, user_id=user_id, workflow_type=None)

    def _run():
        db_session.expire_all()
        return db_session.query(FlowRun).filter(FlowRun.flow_name == flow_name).one()

    try:
        first = _runner().start({"nodus_script": script}, flow_name=flow_name)
        assert first["status"] == "WAITING", first
        run = _run()
        assert run.waiting_for == "tick.0"
        assert run.state[PRIOR_STATE_KEY]["saved"] == {"done": 1, "first_only": "kept"}

        # Second run: reads done=1, parks on tick.1 WITHOUT saving first_only again.
        route_event("tick.0", {"n": 0}, db_session, run_id=str(run.id))
        second = _runner().resume(str(run.id))
        assert second["status"] == "WAITING", second
        run = _run()
        assert run.waiting_for == "tick.1", "the second run must read done=1 back"
        saved = run.state[PRIOR_STATE_KEY]["saved"]
        assert saved["done"] == 2
        assert saved["first_only"] == "kept", "a key saved in an earlier park survives a park that did not save it"

        route_event("tick.1", {"n": 1}, db_session, run_id=str(run.id))
        final = _runner().resume(str(run.id))
        assert final["status"] in {"COMPLETED", "SUCCESS"}, final
        run = _run()
        assert run.status == "success"
        assert run.state["nodus_output_state"]["finished_at"] == 2
        assert PRIOR_STATE_KEY not in run.state, "a finished node leaves nothing for the next one"
    finally:
        reg.FLOW_REGISTRY.pop(flow_name, None)
