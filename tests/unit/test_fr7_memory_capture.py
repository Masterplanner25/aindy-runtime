"""
FR-7 — three memory-capture defects that made recall return the wrong things.

Filed by the app team from a live 1,799-node corpus where recall returned, for their only
real user: four copies of one already-fixed bug, two feedback counters, and two content-free
labels. Nothing a strategy could act on. All three causes were verified in source before
being fixed.
"""
from __future__ import annotations

import pytest

from AINDY.memory.memory_capture_engine import (
    AUTO_MEMORY_EVENT_TYPES,
    _forced_capture_suppressed,
    _policy_base_significance,
    normalize_for_dedup,
)


pytestmark = pytest.mark.runtime_only


# ── MEM-POLICY-KEY-1 — validator and engine disagreed on the key name ───────

def test_significance_key_is_honoured():
    """`validate_memory_policy` REQUIRES `significance` or `base_score`. The engine read
    only `default_significance`, so a policy that passed validation had no effect at all."""
    assert _policy_base_significance({"significance": 0.9}) == 0.9


def test_base_score_key_is_honoured():
    assert _policy_base_significance({"base_score": 0.8}) == 0.8


def test_validator_keys_win_over_the_legacy_key():
    """When both are present the validator's key must win — otherwise a policy written to
    satisfy validation still would not take effect."""
    assert _policy_base_significance({"significance": 0.9, "default_significance": 0.1}) == 0.9


def test_legacy_key_still_works():
    """Policies written against the engine's previous behaviour must not break."""
    assert _policy_base_significance({"default_significance": 0.7}) == 0.7


def test_missing_policy_falls_back_to_the_default():
    assert _policy_base_significance({}) == 0.4
    assert _policy_base_significance(None) == 0.4


def test_non_numeric_policy_value_does_not_raise():
    """A malformed policy must not take down capture."""
    assert _policy_base_significance({"significance": "high"}) == 0.4


def test_validator_and_engine_now_accept_the_same_keys():
    """The actual defect was disagreement between two components. Pin the agreement."""
    from AINDY.platform_layer import registry_contracts

    import inspect

    src = inspect.getsource(registry_contracts.validate_memory_policy)
    for key in ("significance", "base_score"):
        assert key in src, f"validator no longer mentions {key}"
        assert _policy_base_significance({key: 0.55}) == 0.55, f"engine ignores {key}"


# ── MEM-DEDUP-TRACEID-1 — dedup was exact-match over trace-id-bearing text ──

def test_same_failure_with_different_trace_ids_normalizes_equal():
    """The defect: one recurring failure produced N distinct contents, one per trace id,
    and consumed half the recall budget."""
    a = "Completion finalization failed: run 3f2b9c1e-7d4a-4f10-9b2e-1a2b3c4d5e6f aborted"
    b = "Completion finalization failed: run 91c0de52-11aa-4bb3-8cc4-9d8e7f6a5b40 aborted"
    assert normalize_for_dedup(a) == normalize_for_dedup(b)


def test_long_hex_identifiers_are_normalized():
    a = "task deadbeefcafebabe0123 failed"
    b = "task 0123deadbeefcafebabe failed"
    assert normalize_for_dedup(a) == normalize_for_dedup(b)


def test_numbers_are_deliberately_left_alone():
    """Not everything volatile is noise. Two latency observations are genuinely different
    facts; collapsing them would erase signal rather than duplication."""
    assert normalize_for_dedup("Latency spike detected at 5417.09ms") != normalize_for_dedup(
        "Latency spike detected at 12.00ms"
    )


def test_genuinely_different_content_stays_different():
    assert normalize_for_dedup("payment captured") != normalize_for_dedup("payment refused")


def test_whitespace_and_case_are_normalized():
    assert normalize_for_dedup("Payment   Captured\n") == normalize_for_dedup("payment captured")


def test_empty_content_is_safe():
    assert normalize_for_dedup("") == ""
    assert normalize_for_dedup(None) == ""


# ── MEM-FORCE-UNGATED-1 — forced capture bypassed every policy ──────────────

def test_execution_started_is_still_auto_captured():
    """The obvious fix — dropping it from the set — would have silently undone an invariant
    RT-MEMTXN-LEAK-1 deliberately preserved: ordinary jobs must keep emitting loop-closure
    signal for INFINITY-RUNTIME-1. Only runtime-internal maintenance tasks were cut there.
    The suppression lever moved to policy instead."""
    from AINDY.core.system_event_types import SystemEventTypes

    assert SystemEventTypes.EXECUTION_STARTED in AUTO_MEMORY_EVENT_TYPES


def test_outcome_events_are_still_captured():
    from AINDY.core.system_event_types import SystemEventTypes

    assert SystemEventTypes.EXECUTION_COMPLETED in AUTO_MEMORY_EVENT_TYPES
    assert SystemEventTypes.EXECUTION_FAILED in AUTO_MEMORY_EVENT_TYPES


def test_feedback_signals_are_still_captured():
    from AINDY.core.system_event_types import SystemEventTypes

    assert SystemEventTypes.FEEDBACK_LATENCY_SPIKE in AUTO_MEMORY_EVENT_TYPES
    assert SystemEventTypes.FEEDBACK_REPEATED_FAILURE in AUTO_MEMORY_EVENT_TYPES


def test_explicit_policy_threshold_now_suppresses_a_forced_capture():
    """The actual defect: force=True skipped the gate entirely, so an app could not turn
    these captures off from its side at all."""
    assert _forced_capture_suppressed({"min_significance": 0.5}, 0.2) is True


def test_forced_capture_survives_when_the_policy_permits_it():
    assert _forced_capture_suppressed({"min_significance": 0.1}, 0.8) is False


def test_absent_threshold_does_not_suppress():
    """A missing key means the deployment never expressed an opinion — NOT "0.0, keep
    everything". Force must keep winning there, so nothing changes for deployments that
    never wrote a policy."""
    assert _forced_capture_suppressed({}, 0.0) is False
    assert _forced_capture_suppressed(None, 0.0) is False


def test_malformed_threshold_does_not_suppress():
    """A broken policy must not start silently dropping captures."""
    assert _forced_capture_suppressed({"min_significance": "high"}, 0.1) is False
