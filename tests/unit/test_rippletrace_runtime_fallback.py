"""RTR-7 — execution-graph endpoint falls back to the runtime's own causal graph.

``observability_rippletrace_node`` (GET /observability/execution_graph/{trace_id})
previously returned FAILURE when the app hadn't registered the ``rippletrace_*``
symbols. The runtime owns a fully-capable equivalent (EventEdge +
event_trace_service), so it now falls back to it — only app-domain ``insights``
are unavailable (→ empty list). App-registered symbols still win when present.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from AINDY.runtime.flow_definitions_observability import observability_rippletrace_node

pytestmark = pytest.mark.runtime_only


def _ctx_with_nonempty_trace():
    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 1
    return {"db": db, "user_id": str(uuid.uuid4())}


def test_falls_back_to_runtime_service_when_app_symbols_absent():
    ctx = _ctx_with_nonempty_trace()
    state = {"trace_id": "trace-xyz"}

    with (
        patch("AINDY.platform_layer.registry.get_symbol", return_value=None),
        patch("AINDY.platform_layer.event_trace_service.build_trace_graph", return_value={"nodes": [{"id": "e1"}], "edges": []}),
        patch("AINDY.platform_layer.event_trace_service.detect_root_event", return_value={"id": "e1"}),
        patch("AINDY.platform_layer.event_trace_service.detect_terminal_events", return_value=[{"id": "e1"}]),
        patch("AINDY.platform_layer.event_trace_service.calculate_trace_span", return_value={"node_count": 1, "edge_count": 0, "depth": 0, "terminal_count": 1}),
    ):
        out = observability_rippletrace_node(state, ctx)

    assert out["status"] == "SUCCESS"
    result = out["output_patch"]["observability_rippletrace_result"]
    assert result["trace_id"] == "trace-xyz"
    assert result["nodes"] == [{"id": "e1"}]
    assert result["root_event"] == {"id": "e1"}
    assert result["ripple_span"]["terminal_count"] == 1
    assert result["insights"] == []  # app-domain insights unavailable in fallback


def test_empty_trace_short_circuits_without_builders():
    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 0
    ctx = {"db": db, "user_id": str(uuid.uuid4())}

    with patch("AINDY.platform_layer.registry.get_symbol", return_value=None):
        out = observability_rippletrace_node({"trace_id": "empty"}, ctx)

    assert out["status"] == "SUCCESS"
    result = out["output_patch"]["observability_rippletrace_result"]
    assert result["nodes"] == [] and result["edges"] == []
    assert result["ripple_span"]["node_count"] == 0


def test_app_registered_symbols_take_precedence():
    ctx = _ctx_with_nonempty_trace()
    app_graph = MagicMock(return_value={"nodes": [{"id": "app"}], "edges": []})

    def _get_symbol(name):
        return {
            "rippletrace_build_trace_graph": app_graph,
            "rippletrace_calculate_ripple_span": MagicMock(return_value={"node_count": 1}),
            "rippletrace_detect_root_event": MagicMock(return_value={"id": "app"}),
            "rippletrace_detect_terminal_events": MagicMock(return_value=[]),
            "rippletrace_generate_trace_insights": MagicMock(return_value=["app-insight"]),
        }[name]

    with (
        patch("AINDY.platform_layer.registry.get_symbol", side_effect=_get_symbol),
        patch("AINDY.platform_layer.event_trace_service.build_trace_graph") as runtime_graph,
    ):
        out = observability_rippletrace_node({"trace_id": "t"}, ctx)

    assert out["status"] == "SUCCESS"
    result = out["output_patch"]["observability_rippletrace_result"]
    assert result["nodes"] == [{"id": "app"}]
    assert result["insights"] == ["app-insight"]
    app_graph.assert_called_once()
    runtime_graph.assert_not_called()  # runtime fallback not used when app present


def test_serialize_memory_node_reads_causal_depth():
    """RTR-7 hygiene: the de-obfuscated attribute access still reads causal_depth."""
    from AINDY.platform_layer.event_trace_service import _serialize_memory_node

    row = MagicMock()
    row.id = "m1"
    row.memory_type = "insight"
    row.extra = {"trace_id": "t"}
    row.created_at = None
    row.source = "memory"
    row.content = "c"
    row.tags = []
    row.impact_score = 0.5
    row.causal_depth = 4
    row.source_event_id = None
    row.root_event_id = None

    out = _serialize_memory_node(row)
    assert out["payload"]["relationship_depth"] == 4
