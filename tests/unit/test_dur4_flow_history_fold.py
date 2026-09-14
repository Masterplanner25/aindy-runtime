"""DUR-4 — fold the FlowHistory event log back into FlowRun.state.

Each row carries a full pre-node ``input_state`` checkpoint + the node's ``output_patch``, so
the post-last-node state is the last row's input_state with its patch applied ONLY on SUCCESS
or WAIT (parity with the live engine's per-status apply — ``runner_steps._MERGED_STATUSES``;
WAIT joined 2026-09-13, ``NODUS-RESUME-BRIDGE-1``). Recovery/audit primitive; normal resume
still trusts the durable snapshot.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.runtime_only

from AINDY.core.flow_history_fold import fold_flow_history_state


def _row(status, input_state, output_patch=None):
    return SimpleNamespace(status=status, input_state=input_state, output_patch=output_patch)


def test_fold_empty_log_is_empty_state():
    assert fold_flow_history_state([]) == {}


def test_fold_success_applies_patch():
    assert fold_flow_history_state([_row("SUCCESS", {"a": 1}, {"b": 2})]) == {"a": 1, "b": 2}


def test_fold_wait_applies_patch():
    # A WAIT patch is the node's durable request for its own re-run (`nodus_wait_event_type`
    # lives in it) and the engine merges it — NODUS-RESUME-BRIDGE-1. Before 2026-09-13 this
    # test asserted the opposite, in parity with an engine that dropped it.
    assert fold_flow_history_state([_row("WAIT", {"a": 1}, {"b": 2})]) == {"a": 1, "b": 2}


def test_fold_failure_and_retry_do_not_apply_patch():
    assert fold_flow_history_state([_row("FAILURE", {"a": 1}, {"b": 2})]) == {"a": 1}
    assert fold_flow_history_state([_row("RETRY", {"a": 1}, {"b": 2})]) == {"a": 1}


def test_fold_status_set_matches_the_engine():
    """★ The fold reconstructs what the engine persisted. Two hand-written sets drift; this
    pins them equal so a status added to one side without the other is a CI failure."""
    from AINDY.core.flow_history_fold import FOLDED_STATUSES
    from AINDY.runtime.flow_engine.runner_steps import _MERGED_STATUSES

    assert FOLDED_STATUSES == _MERGED_STATUSES
    assert FOLDED_STATUSES, "an empty census satisfies every guard built on it"


def test_fold_uses_last_rows_checkpoint():
    rows = [
        _row("SUCCESS", {"n": 0}, {"x": 0}),
        _row("SUCCESS", {"n": 1, "x": 0}, {"x": 1}),
    ]
    # The last row's full input_state (n=1, x=0) + its patch (x=1) → n=1, x=1.
    assert fold_flow_history_state(rows) == {"n": 1, "x": 1}


def test_fold_is_shallow_merge():
    # patch's key wholesale-replaces base's (dict.update semantics), matching the engine.
    rows = [_row("SUCCESS", {"a": {"nested": 1}}, {"a": {"new": 2}})]
    assert fold_flow_history_state(rows) == {"a": {"new": 2}}


def test_fold_tolerates_missing_columns():
    rows = [_row("SUCCESS", None, None)]
    assert fold_flow_history_state(rows) == {}


def test_fold_repair_flag_reads_settings(monkeypatch):
    from AINDY.core import flow_continuation as fc
    from AINDY.config import settings

    monkeypatch.setattr(settings, "AINDY_DURABLE_FOLD_REPAIR", True, raising=False)
    assert fc._fold_repair_enabled() is True
    monkeypatch.setattr(settings, "AINDY_DURABLE_FOLD_REPAIR", False, raising=False)
    assert fc._fold_repair_enabled() is False
