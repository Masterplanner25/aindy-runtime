"""FR-64: a node that keeps answering RETRY fails the run with its last error, not only a count.

The app's Genesis node maps a syscall error to RETRY. When the handler's message was "This needs
your own OpenAI API key. Add it in Settings.", the route got "Node … failed after 3 retries" and
answered a generic 500, because the runner dropped the node's error on the last attempt.

(FR-64's other half, the run "disappearing", reproduces only under a test fixture that holds the
app's and the test's session in one outer transaction, where the runner's commit is not a commit
and a rollback inside a handler erases the run. On a real session the run survives.)
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.runtime_only

SENTENCE = "HTTP_402:This needs your own OpenAI API key. Add it in Settings."


def _run_retrying(db_session, error):
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    node = f"retrying_{uuid.uuid4().hex[:6]}"
    calls: list[int] = []

    @reg.register_node(node)
    def _n(state, context):  # noqa: ANN001
        calls.append(1)
        return {"status": "RETRY", "error": error} if error else {"status": "RETRY"}

    flow = {"start": node, "end": [node], "edges": {}}
    name = f"retrying_flow_{uuid.uuid4().hex[:6]}"
    reg.register_flow(name, flow)
    try:
        response = PersistentFlowRunner(flow=flow, db=db_session, user_id=str(uuid.uuid4()), workflow_type=None).start(
            {}, flow_name=name
        )
    finally:
        reg.FLOW_REGISTRY.pop(name, None)
        reg.NODE_REGISTRY.pop(node, None)
    return node, response, calls


def test_the_last_error_reaches_the_runs_result(db_session):
    node, response, calls = _run_retrying(db_session, SENTENCE)

    assert response["status"] == "FAILED", response
    assert len(calls) > 1, "control: the node was retried, so this is the exhaustion path"
    error = response["result"]["error"]  # what `run_flow` callers read; the events carry it anyway
    assert error.startswith(f"Node {node} failed after")
    assert error.endswith(SENTENCE), "the handler's sentence is what the route needs to answer with"


def test_control_a_retry_with_no_error_keeps_the_count_message(db_session):
    node, response, _ = _run_retrying(db_session, None)

    assert response["status"] == "FAILED", response
    assert response["result"]["error"].endswith("retries"), response["result"]
