"""`trace_scope` — establish a trace id for a block, never for the rest of the thread.

`ensure_trace_id` sets the ambient trace with no token when none is current and never releases
it. Inside a request or a flow node a trace is already set, so it only reads — but reached from
a long-lived thread with no ambient trace (a scheduler worker), the first caller pins that
thread's trace id for every later unit of work it runs (`PersistentFlowRunner.start` did exactly
that; fixed in #633, `TEST-ORDER-CONTEXTVAR-1`). `ensure_trace_id` stays because app flow nodes
call it in the read-only position; `trace_scope` is the token-holding form for runtime code, and
`agents/runtime_api.py`'s two former `ensure_trace_id` sites now use it.

The last test drives the real `create_agent_run_runtime` entry with NO ambient trace and asserts
the ContextVar is unchanged afterwards. That path is not reachable that way today — its only
caller is the agent route, under the middleware's trace — so this pins the latent shape, not a
live bug. The autouse ContextVar guard in `tests/unit/conftest.py` would also catch a leak here;
the explicit assertion says which property is being claimed. Mutation-checked: swap
`trace_scope()` back to `ensure_trace_id()` in `_decision_or_defer_response` and
`test_create_agent_run_runtime_leaves_no_trace_behind` fails.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from AINDY.platform_layer.trace_context import (
    get_trace_id,
    reset_trace_id,
    set_trace_id,
    trace_scope,
)

pytestmark = pytest.mark.runtime_only


def test_trace_scope_reuses_an_ambient_trace_untouched():
    token = set_trace_id("ambient-1")
    try:
        with trace_scope("ignored-when-ambient") as trace_id:
            assert trace_id == "ambient-1"
            assert get_trace_id() == "ambient-1"
        assert get_trace_id() == "ambient-1", "an ambient trace must survive the block"
    finally:
        reset_trace_id(token)


def test_trace_scope_establishes_and_releases_when_absent():
    assert get_trace_id() is None, "precondition: no ambient trace (the conftest guard keeps it so)"
    with trace_scope() as trace_id:
        assert trace_id and get_trace_id() == trace_id
        uuid.UUID(trace_id)  # a generated id is a real uuid
    assert get_trace_id() is None, "what the block established must not outlive it"

    with trace_scope("requested-7") as trace_id:
        assert trace_id == "requested-7" and get_trace_id() == "requested-7"
    assert get_trace_id() is None


def test_trace_scope_releases_on_exception():
    with pytest.raises(RuntimeError, match="inside"):
        with trace_scope():
            assert get_trace_id() is not None
            raise RuntimeError("inside")
    assert get_trace_id() is None


def test_create_agent_run_runtime_leaves_no_trace_behind(monkeypatch):
    """The runtime_api entry, entered with no ambient trace, records one and releases it."""
    from AINDY.agents import runtime_api

    recorded: dict = {}

    def _evaluate(**kwargs):
        return {"decision": "ignore", "reason": "probe", "confidence": 0.0}

    def _record(**kwargs):
        recorded["trace_id"] = kwargs["trace_id"]
        recorded["ambient_during_call"] = get_trace_id()

    monkeypatch.setattr(runtime_api, "async_heavy_execution_enabled", lambda: False)
    monkeypatch.setattr(runtime_api, "evaluate_live_trigger", _evaluate)
    monkeypatch.setattr(runtime_api, "record_decision", _record)
    monkeypatch.setattr(
        runtime_api, "build_decision_response", lambda evaluation, trace_id=None, **kw: {"trace_id": trace_id}
    )

    assert get_trace_id() is None
    result = runtime_api.create_agent_run_runtime(goal="probe goal", db=MagicMock(), user_id="u-1")

    # A trace was established for the call and used consistently within it...
    assert recorded["trace_id"] and recorded["ambient_during_call"] == recorded["trace_id"]
    assert result["_decision_response"]["trace_id"] == recorded["trace_id"]
    # ...and did not outlive it.
    assert get_trace_id() is None, "create_agent_run_runtime pinned the thread's trace id"
