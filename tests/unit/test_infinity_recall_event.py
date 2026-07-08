"""INFINITY-RUNTIME-1 Gap 1 — recall->planning link + RECALL_USED event.

Covers the canonical RECALL_USED emitter, the runtime-owned planner-memory
recall helper (flag-gated), and the planner-prompt injection seam. No database.
"""

from __future__ import annotations

import pytest

from AINDY.config import settings
from AINDY.core.execution_recall import emit_recall_used
from AINDY.core.system_event_types import SystemEventTypes


@pytest.fixture
def captured_events(monkeypatch):
    events: list[dict] = []

    def _fake(**kwargs):
        events.append(kwargs)
        return "evt-id"

    monkeypatch.setattr(
        "AINDY.core.execution_signal_helper.queue_system_event", _fake
    )
    return events


# --- emit_recall_used --------------------------------------------------------


def test_recall_event_payload_shape(captured_events):
    emit_recall_used(
        db=None,
        node_ids=["a", "b", "c"],
        query="do the thing" * 50,  # long — must be truncated
        trace_id="trace-1",
        user_id="user-1",
        operation_type="agent_planning",
        source="agent",
    )
    assert len(captured_events) == 1
    call = captured_events[0]
    assert call["event_type"] == SystemEventTypes.RECALL_USED == "recall.used"
    assert call["trace_id"] == "trace-1"
    payload = call["payload"]
    assert payload["node_ids"] == ["a", "b", "c"]
    assert payload["count"] == 3
    assert payload["operation_type"] == "agent_planning"
    assert len(payload["query"]) <= 200


def test_recall_event_noop_on_empty(captured_events):
    assert emit_recall_used(db=None, node_ids=[]) is None
    assert emit_recall_used(db=None, node_ids=None) is None
    assert captured_events == []


def test_recall_event_filters_falsy_ids(captured_events):
    emit_recall_used(db=None, node_ids=["x", None, "", "y"])
    assert captured_events[0]["payload"]["node_ids"] == ["x", "y"]
    assert captured_events[0]["payload"]["count"] == 2


def test_recall_event_is_best_effort(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("bus down")

    monkeypatch.setattr(
        "AINDY.core.execution_signal_helper.queue_system_event", _boom
    )
    assert emit_recall_used(db=None, node_ids=["a"]) is None


# --- _recall_planner_memory (flag-gated) ------------------------------------


class _FakeContext:
    def __init__(self, formatted, ids):
        self.formatted = formatted
        self.items = []
        self._ids = ids

    @property
    def ids(self):
        return self._ids


class _FakeOrchestrator:
    def __init__(self, *a, **k):
        pass

    def get_context(self, **kwargs):
        return _FakeContext("- past outcome: shipped X", ["n1", "n2"])


def test_planner_recall_off_by_default(monkeypatch):
    from AINDY.agents.agent_runtime.planning import _recall_planner_memory

    monkeypatch.setattr(settings, "AINDY_PLANNER_MEMORY_INJECTION", False)
    block, ids = _recall_planner_memory("objective", "user-1", object())
    assert block == "" and ids == []


def test_planner_recall_returns_block_when_enabled(monkeypatch):
    from AINDY.agents.agent_runtime.planning import _recall_planner_memory

    monkeypatch.setattr(settings, "AINDY_PLANNER_MEMORY_INJECTION", True)
    monkeypatch.setattr(
        "AINDY.runtime.memory.MemoryOrchestrator", _FakeOrchestrator
    )
    block, ids = _recall_planner_memory("ship the feature", "user-1", object())
    assert block == "- past outcome: shipped X"
    assert ids == ["n1", "n2"]


def test_planner_recall_needs_user_and_db(monkeypatch):
    from AINDY.agents.agent_runtime.planning import _recall_planner_memory

    monkeypatch.setattr(settings, "AINDY_PLANNER_MEMORY_INJECTION", True)
    assert _recall_planner_memory("obj", None, object()) == ("", [])
    assert _recall_planner_memory("obj", "user-1", None) == ("", [])


def test_planner_recall_best_effort_on_failure(monkeypatch):
    from AINDY.agents.agent_runtime.planning import _recall_planner_memory

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("db exploded")

    monkeypatch.setattr(settings, "AINDY_PLANNER_MEMORY_INJECTION", True)
    monkeypatch.setattr("AINDY.runtime.memory.MemoryOrchestrator", _Boom)
    assert _recall_planner_memory("obj", "user-1", object()) == ("", [])


# --- _build_planner_prompt injection seam ------------------------------------


def test_prompt_appends_memory_block():
    from AINDY.agents.agent_runtime.planning import _build_planner_prompt

    out = _build_planner_prompt(
        system_prompt="BASE",
        planner_context={"context_block": "CTX"},
        tools=[],
        memory_block="MEMORY-XYZ",
    )
    assert "BASE" in out and "CTX" in out and "MEMORY-XYZ" in out
    assert "Relevant prior memory" in out


def test_prompt_no_memory_block_is_unchanged():
    from AINDY.agents.agent_runtime.planning import _build_planner_prompt

    out = _build_planner_prompt(
        system_prompt="BASE",
        planner_context={"context_block": ""},
        tools=[],
    )
    assert "Relevant prior memory" not in out


def test_prompt_memory_block_not_duplicated():
    from AINDY.agents.agent_runtime.planning import _build_planner_prompt

    already = "BASE\n\nRelevant prior memory (recalled for this objective):\nMEM"
    out = _build_planner_prompt(
        system_prompt=already,
        planner_context={},
        tools=[],
        memory_block="MEM",
    )
    assert out.count("MEM") == 1
