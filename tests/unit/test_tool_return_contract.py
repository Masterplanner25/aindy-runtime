"""TOOL-SEAM-ISOLATION-1 step C1 — is a tool's return able to cross a process boundary?

★ **This measures; it does not reject, and that is the whole design.** By the time a return is
inspected the handler has already run and its effect is real. Failing the call there would
**discard a real effect**, which is strictly worse than passing an awkward value through.
``SyscallDispatcher`` made the same judgement on the syscall path — *"a ledger failure must never
turn that into a caller-visible error"* — and the two boundaries must not disagree.

(An earlier plan for this step said "validate … with a clear error." That was wrong on exactly
this axis, and the syscall path had already written down why.)

★ **Why it exists: it is the gate on step C.** A tool cannot run behind a process boundary unless
its return marshals — a ``UUID``, a session, or any live object crosses no pipe. All 18 tools that
exist return a dict and every one is typed ``-> dict``, but **nothing enforced it**, so "they all
comply" was an assumption. ``aindy_tool_return_contract_violations_total`` turns it into a number,
and a non-zero count is exactly the list of tools that cannot be confined yet.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from AINDY.agents.tool_registry import TOOL_REGISTRY, execute_tool, register_tool
from AINDY.core.execution_environment import ASSURANCE_INSECURE_DEV
from tests.integration.soak_harness import metric_window

pytestmark = pytest.mark.runtime_only

_PROBE = "test.return_contract_probe"
_METRIC = "aindy_tool_return_contract_violations_total"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    TOOL_REGISTRY.pop(_PROBE, None)


def _register(returns, isolation=None):
    register_tool(
        _PROBE,
        risk="low",
        description="probe",
        capability=f"tool:{_PROBE}",
        required_capability="read_memory",
        category="test",
        egress_scope="internal",
        isolation=isolation,
    )(lambda args, user_id, db: returns)


def _invoke():
    return execute_tool(
        _PROBE, {}, user_id="u1", db=MagicMock(), run_id=None, execution_token=None
    )


def _labels(reason, declared="none"):
    return {"reason": reason, "declared_isolation": declared}


# ── Liveness control ─────────────────────────────────────────────────────────


def test_liveness_a_conforming_return_records_no_violation():
    """★ If a good return counted as a violation, every 'is counted' assertion below would pass
    for the wrong reason."""
    _register({"ok": True, "n": 1})

    with metric_window(_METRIC, labels=_labels("not_a_dict")) as m:
        result = _invoke()

    assert result["success"] is True
    m.assert_unchanged(_METRIC)


# ── The two violations ───────────────────────────────────────────────────────


def test_a_non_dict_return_is_counted():
    _register("just a string")

    with metric_window(_METRIC, labels=_labels("not_a_dict")) as m:
        result = _invoke()

    m.assert_increased(_METRIC)
    assert result["success"] is True, "measuring a violation must not fail the call"
    assert result["result"] == "just a string", "the value must still reach the caller"


def test_a_dict_that_does_not_marshal_is_counted():
    """★ The case a plain ``isinstance(result, dict)`` check would miss — and the one that
    actually bit on the syscall path, where a ``UUID`` return came back as an error envelope
    AFTER the effect had already landed."""
    _register({"id": uuid.uuid4()})

    with metric_window(_METRIC, labels=_labels("not_json_serializable")) as m:
        _invoke()

    m.assert_increased(_METRIC)


def test_the_two_reasons_do_not_bleed_into_each_other():
    """`not_a_dict` and `not_json_serializable` are different remediation tasks; a caller reading
    the counter has to be able to tell them apart."""
    _register("a string")

    with metric_window(_METRIC, labels=_labels("not_json_serializable")) as m:
        _invoke()

    m.assert_unchanged(_METRIC)


# ── The property that makes measuring the right choice ───────────────────────


def test_a_violating_call_still_succeeds_and_keeps_its_effect():
    """★ THE design decision. The handler has already run; rejecting here discards a real effect.

    An earlier plan for this step said 'validate with a clear error'. The syscall path had
    already written down why that is wrong, and this test is the executable form of it.
    """
    side_effects: list[str] = []

    def _fn(args, user_id, db):
        side_effects.append("the effect happened")
        return object()  # marshals nowhere

    register_tool(
        _PROBE,
        risk="low",
        description="probe",
        capability=f"tool:{_PROBE}",
        required_capability="read_memory",
        category="test",
        egress_scope="internal",
    )(_fn)

    result = _invoke()

    assert side_effects == ["the effect happened"]
    assert result["success"] is True, (
        "the effect already landed — reporting failure would tell the caller to retry an effect "
        "that has already happened, which is worse than an awkward return value"
    )


def test_a_declared_tool_is_labelled_separately(monkeypatch):
    """★ A tool that declared an isolation class has opted into a boundary its return cannot
    cross — that is a defect in the tool, not an observation about an in-process one, and the
    label separates the two so the remediation list is readable."""
    monkeypatch.setattr(
        "AINDY.core.execution_environment._host_assurance",
        lambda: (ASSURANCE_INSECURE_DEV, "insecure-dev/test"),
    )
    _register("not a dict", isolation=ASSURANCE_INSECURE_DEV)

    with metric_window(_METRIC, labels=_labels("not_a_dict", ASSURANCE_INSECURE_DEV)) as m:
        _invoke()

    m.assert_increased(_METRIC)


def test_a_metrics_failure_never_affects_the_call(monkeypatch):
    """Observability must not sit on the effect path — the same rule as the effect-gate counter."""
    import AINDY.platform_layer.metrics as metrics_mod

    class _Exploding:
        def labels(self, **_kw):
            raise RuntimeError("metrics down")

    monkeypatch.setattr(metrics_mod, "tool_return_contract_violations_total", _Exploding())
    _register("not a dict")

    assert _invoke()["success"] is True
