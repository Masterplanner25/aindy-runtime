"""RTR-6 — first-class reasoning signals at the memory layer.

``reasoning.signal`` promotes the memory *capture* signal to first class: the
capture path (``MemoryCaptureEngine.evaluate_and_capture``) emits a kind="capture"
signal alongside ``MEMORY_WRITE``. Recall inputs stay first-class via the existing
``RECALL_USED`` event (so ``emit_recall_used`` is left untouched — it must keep
emitting exactly one event). Event-row-as-record, no table.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from AINDY.core.reasoning_signal import emit_reasoning_signal
from AINDY.core.system_event_types import SystemEventTypes

pytestmark = pytest.mark.runtime_only


def test_reasoning_signal_event_type_registered():
    assert SystemEventTypes.REASONING_SIGNAL == "reasoning.signal"


def test_emit_reasoning_signal_valid_kind_emits_with_kind_in_payload():
    calls = {}

    def _capture(**kw):
        calls.update(kw)
        return "evt-1"

    with patch("AINDY.core.execution_signal_helper.queue_system_event", side_effect=_capture):
        out = emit_reasoning_signal(
            db=MagicMock(),
            kind="capture",
            payload={"node_id": "n1", "impact_score": 0.7},
            user_id="u1",
        )
    assert out == "evt-1"
    assert calls["event_type"] == "reasoning.signal"
    assert calls["payload"]["kind"] == "capture"
    assert calls["payload"]["node_id"] == "n1"
    assert calls["required"] is False


def test_emit_reasoning_signal_unknown_kind_is_noop():
    with patch("AINDY.core.execution_signal_helper.queue_system_event") as q:
        out = emit_reasoning_signal(db=MagicMock(), kind="bogus")
    assert out is None
    q.assert_not_called()


def test_emit_reasoning_signal_never_raises():
    with patch(
        "AINDY.core.execution_signal_helper.queue_system_event",
        side_effect=RuntimeError("boom"),
    ):
        assert emit_reasoning_signal(db=MagicMock(), kind="recall") is None


def test_recall_path_unchanged_emits_single_event():
    """RTR-6 must not perturb emit_recall_used — it still emits exactly one event
    (RECALL_USED already serves as the first-class recall reasoning input)."""
    from AINDY.core.execution_recall import emit_recall_used

    emitted = []

    def _capture(**kw):
        emitted.append(kw.get("event_type"))
        return "evt"

    with patch("AINDY.core.execution_signal_helper.queue_system_event", side_effect=_capture):
        emit_recall_used(db=MagicMock(), node_ids=["a", "b"], query="q", operation_type="plan")

    assert emitted == ["recall.used"]


def test_capture_path_emits_capture_reasoning_signal():
    """The capture engine emits a kind='capture' reasoning.signal on store."""
    from AINDY.memory.memory_capture_engine import MemoryCaptureEngine

    engine = MemoryCaptureEngine(db=MagicMock(), user_id="u1", agent_namespace="user")

    causal = {
        "source_event_id": None,
        "root_event_id": None,
        "causal_depth": 2,
        "impact_score": 0.9,
        "memory_type": "insight",
        "extra": {},
    }

    with (
        patch.object(engine, "_score_significance", return_value=0.8),
        patch.object(engine, "_is_duplicate", return_value=False),
        patch.object(engine, "_auto_link"),
        patch.object(engine, "_build_causal_context", return_value=causal),
        patch.object(engine.dao, "save", return_value={"id": "node-123"}),
        patch.object(engine.dao, "_get_model_by_id", return_value=None),
        patch("AINDY.memory.memory_capture_engine.emit_system_event"),
        patch("AINDY.core.reasoning_signal.emit_reasoning_signal", return_value="rs-1") as rs,
    ):
        node = engine.evaluate_and_capture(
            event_type="agent.step.completed",
            content="derived an insight",
            source="agent",
            node_type="insight",
            force=True,
            # Order-independent: a prior test in the full suite may leave the
            # pipeline-active ContextVar set, which would short-circuit capture.
            allow_when_pipeline_active=True,
        )

    assert node == {"id": "node-123"}
    rs.assert_called_once()
    kwargs = rs.call_args.kwargs
    assert kwargs["kind"] == "capture"
    assert kwargs["payload"]["node_id"] == "node-123"
    assert kwargs["payload"]["memory_type"] == "insight"
    assert kwargs["payload"]["impact_score"] == 0.9
    assert kwargs["payload"]["significance"] == 0.8
    assert kwargs["payload"]["causal_depth"] == 2
