"""FR-49 — a memory recall failure is counted, not only logged.

The app's soak of `AINDY_MEMORY_RECALL_OWN_SESSION` read its absence signal from a WARNING line
(`[MemoryOrchestrator] recall failed`), and ~6 container recreates lost most of that log. The
filing also believed the pipeline's recall had no witness at all; checked at source, a failure
INSIDE the recall is caught by `get_context` and logged at WARNING, including the own-session
fallback. What had no witness was the pipeline failing BEFORE the recall started (DEBUG), and the
fact that every witness was a log line.

What is pinned:
* `aindy_memory_recall_failures_total{site, stage}` counts `recall` (the recall raised; an empty
  context is returned), `own_session` (the flag could not open its session; the recall still ran on
  the caller's) and `setup` (the pipeline failed before the recall);
* a successful recall counts nothing (the control);
* the pipeline passes `site="pipeline"`, and its early failure is WARNING now, not DEBUG;
* every runtime `get_context` call names its site: a census DERIVED from the AST, asserted
  non-empty, so a new call site cannot silently count as `unspecified`.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.runtime_only

REPO = Path(__file__).resolve().parents[2]


def _count(site, stage):
    from AINDY.platform_layer.metrics import REGISTRY

    return REGISTRY.get_sample_value(
        "aindy_memory_recall_failures_total", {"site": site, "stage": stage}
    ) or 0.0


def _orchestrator():
    from AINDY.runtime.memory import MemoryOrchestrator

    return MemoryOrchestrator(MagicMock())


def _recall(orch, site="test_site"):
    return orch.get_context(user_id="u1", query="q", db=MagicMock(), metadata={"limit": 3}, site=site)


def test_a_failing_recall_is_counted_and_returns_an_empty_context(monkeypatch):
    monkeypatch.delenv("AINDY_MEMORY_RECALL_OWN_SESSION", raising=False)
    orch = _orchestrator()
    monkeypatch.setattr(orch, "_recall_candidates", MagicMock(side_effect=RuntimeError("pool exhausted")))
    before = _count("test_site", "recall")
    context = _recall(orch)
    assert list(context.items) == []
    assert _count("test_site", "recall") == before + 1


def test_a_successful_recall_counts_nothing(monkeypatch):
    """The control: the counter is not incremented on the ordinary path."""
    monkeypatch.delenv("AINDY_MEMORY_RECALL_OWN_SESSION", raising=False)
    orch = _orchestrator()
    monkeypatch.setattr(orch, "_recall_candidates", MagicMock(return_value=[]))
    before = {stage: _count("test_site", stage) for stage in ("recall", "own_session")}
    _recall(orch)
    assert {stage: _count("test_site", stage) for stage in before} == before


def test_the_own_session_fallback_is_counted_and_the_recall_still_runs(monkeypatch):
    monkeypatch.setenv("AINDY_MEMORY_RECALL_OWN_SESSION", "true")
    monkeypatch.setattr("AINDY.db.database.SessionLocal", MagicMock(side_effect=RuntimeError("no connection")))
    orch = _orchestrator()
    recall = MagicMock(return_value=[])
    monkeypatch.setattr(orch, "_recall_candidates", recall)
    before = _count("test_site", "own_session")
    _recall(orch)
    assert _count("test_site", "own_session") == before + 1
    assert recall.called, "the recall must still run on the caller's session"


def test_a_caller_that_names_no_site_counts_as_unspecified(monkeypatch):
    monkeypatch.delenv("AINDY_MEMORY_RECALL_OWN_SESSION", raising=False)
    orch = _orchestrator()
    monkeypatch.setattr(orch, "_recall_candidates", MagicMock(side_effect=RuntimeError("x")))
    before = _count("unspecified", "recall")
    orch.get_context(user_id="u1", query="q", db=MagicMock())
    assert _count("unspecified", "recall") == before + 1


# ── the pipeline ─────────────────────────────────────────────────────────────────────────────


def _ctx():
    return SimpleNamespace(metadata={"db": MagicMock()}, user_id="u1",
                           input_payload={"query": "q"}, route_name="r")


def test_the_pipeline_names_its_site(monkeypatch):
    from AINDY.core.execution_pipeline import signals
    from AINDY.runtime import memory as runtime_memory

    seen = {}

    class _Spy:
        def __init__(self, _dao):
            pass

        def get_context(self, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(items=[1, 2])

    monkeypatch.setattr(runtime_memory, "MemoryOrchestrator", _Spy)
    host = SimpleNamespace(_record_side_effect=MagicMock())
    assert signals._safe_recall_memory_count(host, _ctx()) == 2
    assert seen["site"] == "pipeline"


def test_a_pipeline_failure_before_the_recall_is_counted_at_warning(monkeypatch, caplog):
    from AINDY.core.execution_pipeline import signals
    from AINDY.runtime import memory as runtime_memory

    monkeypatch.setattr(runtime_memory, "MemoryOrchestrator", MagicMock(side_effect=RuntimeError("cannot build")))
    host = SimpleNamespace(_record_side_effect=MagicMock())
    before = _count("pipeline", "setup")
    caplog.set_level(logging.WARNING, logger=signals.logger.name)
    assert signals._safe_recall_memory_count(host, _ctx()) == 0
    assert _count("pipeline", "setup") == before + 1
    assert any("memory_recall_skipped" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)
    host._record_side_effect.assert_called_once()


# ── the census ───────────────────────────────────────────────────────────────────────────────


def test_every_runtime_get_context_call_names_its_site():
    calls = []
    for path in (REPO / "AINDY").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "get_context":
                calls.append((path.relative_to(REPO).as_posix(), node.lineno,
                              any(k.arg == "site" for k in node.keywords)))
    assert len(calls) >= 10, f"census too small to be the runtime's call sites: {calls}"
    unnamed = [f"{p}:{line}" for p, line, has_site in calls if not has_site]
    assert unnamed == [], f"get_context calls with no site= (they would count as 'unspecified'): {unnamed}"
