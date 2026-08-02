"""
MEM-IMPACT-IGNORES-SIGNIFICANCE-1 — the write lever and the read lever were disconnected.

`calculate_impact_score` is purely graph-derived:

    len(downstream) + (trace_depth * 0.75) + failure_bonus

and `get_relevant_memories` (`memory_scoring_service.py:67`) orders **purely by
`impact_score DESC`** — that is the path feeding the Infinity loop. So the one quality signal
an app controls, the policy-declared `significance`, had no route into what comes back.

Measured app-side: a `decision` node declared `significance: 1.0` stored impact **0.00** and
was never recalled, while every runtime-captured failure started at 1.5 (`failure_bonus`).

Note also that `significance` is **not a column** on the memory node — it is computed at
capture, used for the gate, and discarded. So blending at read time was not available without
a schema change; folding it in at write time is what makes the lever work with none.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from AINDY.memory.memory_capture_engine import (
    SIGNIFICANCE_IMPACT_WEIGHT,
    blend_impact_with_significance,
    calculate_impact_score,
    significance_impact_floor,
)


pytestmark = pytest.mark.runtime_only

_POLICY_PATH = "AINDY.memory.memory_capture_engine.get_memory_policy"


# ── the floor a declared significance implies ───────────────────────────────

def test_declared_significance_produces_a_floor():
    with patch(_POLICY_PATH, return_value={"significance": 1.0}):
        assert significance_impact_floor("domain.decision") == SIGNIFICANCE_IMPACT_WEIGHT


def test_a_fully_significant_memory_outranks_the_failure_floor():
    """The concrete symptom: a declared decision scored 0.00 while every captured failure
    started at 1.5 (`failure_bonus`), so failures were all that ever came back."""
    with patch(_POLICY_PATH, return_value={"significance": 1.0}):
        decision = blend_impact_with_significance(0.0, "domain.decision")
    assert decision > 1.5, f"a declared decision ({decision}) still loses to any failure"


def test_no_policy_means_no_floor():
    """Absence of a policy is not a declaration of unimportance — graph-derived impact must
    stand alone exactly as before, so nothing shifts for un-policied events."""
    with patch(_POLICY_PATH, return_value={}):
        assert significance_impact_floor("system.event") == 0.0
        assert blend_impact_with_significance(3.0, "system.event") == 3.0


def test_low_significance_does_not_lift_above_failures():
    """A policy declaring something minor must not accidentally promote it."""
    with patch(_POLICY_PATH, return_value={"significance": 0.4}):
        assert blend_impact_with_significance(0.0, "minor.event") < 1.5


def test_broken_policy_lookup_does_not_break_capture():
    with patch(_POLICY_PATH, side_effect=RuntimeError("registry down")):
        assert significance_impact_floor("x") == 0.0


# ── it is a floor, not a sum ────────────────────────────────────────────────

def test_a_well_connected_failure_still_outranks_a_declared_decision():
    """Deliberately a floor rather than a sum. Adding would inflate already-high system
    failures by the same amount it lifts a decision, preserving the very ordering this
    exists to fix."""
    with patch(_POLICY_PATH, return_value={"significance": 1.0}):
        failure = blend_impact_with_significance(4.0, "execution.failed")
        decision = blend_impact_with_significance(0.0, "domain.decision")
    assert failure > decision
    assert failure == 4.0, "graph impact must pass through unchanged when it already wins"


def test_graph_impact_is_never_reduced():
    with patch(_POLICY_PATH, return_value={"significance": 0.1}):
        assert blend_impact_with_significance(9.0, "whatever") == 9.0


def test_malformed_graph_impact_is_survivable():
    with patch(_POLICY_PATH, return_value={}):
        assert blend_impact_with_significance(None, "x") == 0.0
        assert blend_impact_with_significance("nonsense", "x") == 0.0


# ── the validator's keys work here too ──────────────────────────────────────

@pytest.mark.parametrize("key", ["significance", "base_score", "default_significance"])
def test_every_accepted_policy_key_feeds_the_floor(key):
    """MEM-POLICY-KEY-1 and this defect compound: a policy had to use the right key AND
    that key had to reach impact. Both halves are pinned."""
    with patch(_POLICY_PATH, return_value={key: 1.0}):
        assert significance_impact_floor("e") == SIGNIFICANCE_IMPACT_WEIGHT


# ── the untouched half ──────────────────────────────────────────────────────

def test_graph_formula_itself_is_unchanged():
    """The blend is applied at the capture site; the causal computation keeps its own
    meaning so causal reporting is not silently redefined."""
    import inspect

    src = inspect.getsource(calculate_impact_score)
    assert "failure_bonus" in src and "trace_depth" in src
    assert "significance" not in src, (
        "significance must be blended at the capture site, not folded into the causal metric"
    )
