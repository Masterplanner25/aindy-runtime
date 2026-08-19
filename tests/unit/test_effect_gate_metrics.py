"""IDEM-11 — the idempotency gate must be observable.

★ **Why this exists.** Until 2026-08-19 nothing observed the gate at all. ``aindy_durable_effects``
and ``aindy_effect_attribution`` are ContextVars, not metrics — so with
``AINDY_SYSCALL_IDEMPOTENCY`` enabled an operator had no way to tell whether the gate was firing,
replaying, or **silently degrading**. That absence was the real blocker on a production soak:
there was nothing to read.

★ **``degraded`` is the label the whole counter exists for.** `EXACTLY_ONCE` is not exactly-once
under contention — when the gate loses the insert race to a live pending row it downgrades to
`AT_LEAST_ONCE` for that call, which is correct, documented in `IDEMPOTENCY_CONTRACT.md`, and was
**invisible**. A deployment where `degraded` is a meaningful fraction of `reserved` is one where
the guarantee the operator believes they enabled is not the one they have.
"""

from __future__ import annotations

import pytest

from AINDY.kernel.effect_ledger import _count_gate
from AINDY.platform_layer.metrics import effect_gate_outcomes_total
from tests.integration.soak_harness import metric_window, read_metric

pytestmark = pytest.mark.runtime_only

_NAME = "aindy_effect_gate_outcomes_total"
_OUTCOMES = ("reserved", "replayed", "degraded", "reclaimed")


@pytest.mark.parametrize("outcome", _OUTCOMES)
def test_each_outcome_is_countable(outcome: str):
    with metric_window(_NAME, labels={"outcome": outcome}) as m:
        _count_gate(outcome)
    m.assert_increased(_NAME)


def test_outcomes_do_not_bleed_into_each_other():
    """A single mislabelled call site would make `degraded` unreadable, which is the one label
    an operator needs to trust."""
    with metric_window(_NAME, labels={"outcome": "degraded"}) as degraded:
        _count_gate("reserved")
    degraded.assert_unchanged(_NAME)


def test_the_counter_is_registered_under_the_expected_name():
    """★ The soak asserts on this exact name. `read_metric` raises on an unknown one, but a
    rename here plus a rename there would move together and hide the break — pin the name."""
    assert read_metric(_NAME, {"outcome": "degraded"}) >= 0.0
    assert effect_gate_outcomes_total._name == "aindy_effect_gate_outcomes"


def test_a_metrics_failure_never_breaks_the_effect_path(monkeypatch):
    """★ Load-bearing: the ledger is the correctness path, the counter is observability.

    Inverting that would let a Prometheus problem become a **duplicate side effect** — the exact
    class the gate exists to prevent. `_count_gate` swallows everything.
    """
    import AINDY.platform_layer.metrics as metrics_mod

    class _Exploding:
        def labels(self, **_kw):
            raise RuntimeError("metrics backend is down")

    monkeypatch.setattr(metrics_mod, "effect_gate_outcomes_total", _Exploding())

    _count_gate("degraded")  # must not raise


def test_an_unknown_outcome_label_still_records_rather_than_raising():
    """A typo'd label is an observability bug, not an execution one — it must not throw."""
    _count_gate("not-a-real-outcome")
    assert read_metric(_NAME, {"outcome": "not-a-real-outcome"}) >= 1.0
