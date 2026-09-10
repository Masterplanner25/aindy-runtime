"""`AUTHORITY-NEGOTIATION-1` phase 1 — the negotiation itself.

Phase 0 shipped `degraded_variant=` and pinned that nothing consulted it. This is the behaviour
that consults it: on a capability denial, offer **exactly one** downgrade to a fallback the TOOL
declared, and only if the token in hand already authorises it.

Design: `docs/runtime/AUTHORITY_NEGOTIATION_DESIGN.md`. §2 (no new token is needed), §4 (bounded,
no chains) and §7 (what not to build) are the sections these tests encode.

★ **The two properties worth more than the happy path**, because they are what makes the feature
safe rather than merely useful:

- **It cannot grant authority.** Negotiation chooses *which tool to attempt*; `execute_tool` runs
  its own `check_tool_capability`, so a negotiated tool passes exactly the gate an ordinary one
  passes. `test_negotiation_asks_the_enforcing_code_rather_than_reimplementing_it` pins that
  the fallback is re-checked rather than waved through.
- **It is default-OFF.** A behaviour change that ships on is one nobody reviewed. Every test here
  that exercises the mechanism turns the flag on explicitly, and
  `test_the_flag_is_off_by_default` pins that they have to.
"""

from __future__ import annotations

import importlib

import pytest

pytestmark = pytest.mark.runtime_only


@pytest.fixture
def negotiation(monkeypatch):
    """The module under test, with the flag ON and a clean tool registry."""
    from AINDY.agents import tool_registry as registry_mod

    monkeypatch.setenv("AINDY_AUTHORITY_NEGOTIATION", "1")
    monkeypatch.setattr(registry_mod, "TOOL_REGISTRY", {}, raising=True)

    mod = importlib.import_module("AINDY.agents.authority_negotiation")
    return mod, registry_mod


def _register(registry_mod, name, *, variant=None):
    registry_mod.TOOL_REGISTRY[name] = {
        "fn": lambda **kw: {"ok": True},
        "description": name,
        "degraded_variant": variant,
    }


def _capability_stub(monkeypatch, *, ok_for, granted_tools):
    """Stand in for `check_tool_capability` — ok only for the named tools."""
    def _check(*, token, run_id, user_id, tool_name):
        if tool_name in ok_for:
            return {"ok": True, "error": None, "granted_tools": granted_tools,
                    "allowed_capabilities": []}
        return {
            "ok": False,
            "error": f"capability for {tool_name} not granted by execution token",
            "granted_tools": granted_tools,
            "allowed_capabilities": [],
        }

    import AINDY.agents.capability_service as cap
    monkeypatch.setattr(cap, "check_tool_capability", _check, raising=True)


# ── The flag ─────────────────────────────────────────────────────────────────


def test_the_flag_is_off_by_default(monkeypatch):
    """★ Phase 1 ships default-OFF; phase 3 flips it on evidence, not on code."""
    from AINDY.agents import authority_negotiation as an

    monkeypatch.delenv("AINDY_AUTHORITY_NEGOTIATION", raising=False)
    assert an.authority_negotiation_enabled() is False


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("   ", False), ("maybe", False),
])
def test_the_flag_reads_only_affirmative_values(monkeypatch, value, expected):
    """★ Opt-IN, so anything unrecognised must read as OFF.

    The inverse of the store-declaration flag deliberately: there, a blank meant "unset, declare
    a default"; here a blank must mean "off", because the default IS off and an unparseable
    value must never turn a behaviour change on.
    """
    from AINDY.agents import authority_negotiation as an

    monkeypatch.setenv("AINDY_AUTHORITY_NEGOTIATION", value)
    assert an.authority_negotiation_enabled() is expected


def test_disabled_short_circuits_before_touching_the_registry(monkeypatch):
    """★ And it is labelled `disabled`, not `no_variant`.

    Collapsing the two would make "switched off" and "on, and nothing declared a fallback"
    indistinguishable in the counter — the exact ambiguity the counter exists to remove.
    """
    from AINDY.agents import authority_negotiation as an

    monkeypatch.delenv("AINDY_AUTHORITY_NEGOTIATION", raising=False)
    result = an.negotiate_capability_denial(
        tool_name="anything", token={}, run_id="r", user_id="u"
    )
    assert result.outcome == an.OUTCOME_DISABLED
    assert result.granted is False


# ── The negotiation ──────────────────────────────────────────────────────────


def test_a_declared_and_authorised_fallback_is_offered(negotiation, monkeypatch):
    an, registry_mod = negotiation
    _register(registry_mod, "send_email", variant="queue_for_review")
    _register(registry_mod, "queue_for_review")
    _capability_stub(monkeypatch, ok_for={"queue_for_review"},
                     granted_tools=["send_email", "queue_for_review"])

    result = an.negotiate_capability_denial(
        tool_name="send_email", token={}, run_id="r", user_id="u"
    )

    assert result.granted is True
    assert result.outcome == an.OUTCOME_SUCCEEDED
    assert result.variant == "queue_for_review"


def test_a_tool_with_no_declaration_gets_no_variant(negotiation, monkeypatch):
    """★ The expected steady state until tools start declaring variants.

    `no_variant` says the mechanism RAN and had nothing to offer, which is what distinguishes it
    from `disabled` and from not being wired at all.
    """
    an, registry_mod = negotiation
    _register(registry_mod, "send_email")
    _capability_stub(monkeypatch, ok_for=set(), granted_tools=["send_email"])

    result = an.negotiate_capability_denial(
        tool_name="send_email", token={}, run_id="r", user_id="u"
    )
    assert result.outcome == an.OUTCOME_NO_VARIANT
    assert result.granted is False


def test_a_fallback_the_token_does_not_grant_is_refused(negotiation, monkeypatch):
    """§2 — the token in hand must already authorise the fallback. Nothing is minted."""
    an, registry_mod = negotiation
    _register(registry_mod, "send_email", variant="queue_for_review")
    _register(registry_mod, "queue_for_review")
    _capability_stub(monkeypatch, ok_for=set(), granted_tools=["send_email"])  # variant NOT granted

    result = an.negotiate_capability_denial(
        tool_name="send_email", token={}, run_id="r", user_id="u"
    )
    assert result.outcome == an.OUTCOME_REFUSED_NOT_GRANTED
    assert result.granted is False


def test_a_granted_fallback_that_still_lacks_capabilities_is_variant_denied(negotiation, monkeypatch):
    """★ Distinguished from `refused_not_granted` STRUCTURALLY, never by matching the error text.

    `RETRY-CLASSIFY-1` is the entry for what substring-matching an error message costs: `"404"`
    matches `"took 404ms"`. The two outcomes are separated by granted-tools membership, which is
    data, not prose.
    """
    an, registry_mod = negotiation
    _register(registry_mod, "send_email", variant="queue_for_review")
    _register(registry_mod, "queue_for_review")
    # Granted as a tool, but the capability check still refuses it.
    _capability_stub(monkeypatch, ok_for=set(),
                     granted_tools=["send_email", "queue_for_review"])

    result = an.negotiate_capability_denial(
        tool_name="send_email", token={}, run_id="r", user_id="u"
    )
    assert result.outcome == an.OUTCOME_VARIANT_DENIED
    assert result.granted is False


# ── §4 — bounded, and no chains ──────────────────────────────────────────────


def test_a_chain_is_refused_at_negotiation_time(negotiation, monkeypatch):
    """§4 — a fallback may not declare a fallback.

    The startup sweep refuses chains, but it only sees the tools registered when it runs. A tool
    registered afterwards is not swept, so the bound is re-established here — structurally,
    rather than as a counter someone can get wrong.
    """
    an, registry_mod = negotiation
    _register(registry_mod, "a", variant="b")
    _register(registry_mod, "b", variant="c")   # the chain
    _register(registry_mod, "c")
    _capability_stub(monkeypatch, ok_for={"b", "c"}, granted_tools=["a", "b", "c"])

    result = an.negotiate_capability_denial(
        tool_name="a", token={}, run_id="r", user_id="u"
    )
    assert result.outcome == an.OUTCOME_CHAIN_REFUSED
    assert result.granted is False


def test_negotiation_is_offered_once_and_returns_a_leaf(negotiation, monkeypatch):
    """Exactly one attempt: the outcome names the declared fallback and never walks past it."""
    an, registry_mod = negotiation
    _register(registry_mod, "a", variant="b")
    _register(registry_mod, "b")
    _capability_stub(monkeypatch, ok_for={"b"}, granted_tools=["a", "b"])

    result = an.negotiate_capability_denial(
        tool_name="a", token={}, run_id="r", user_id="u"
    )
    assert result.variant == "b"
    assert registry_mod.TOOL_REGISTRY["b"]["degraded_variant"] is None


# ── §7 — it must not become a second way to grant ────────────────────────────


def test_negotiation_asks_the_enforcing_code_rather_than_reimplementing_it(negotiation, monkeypatch):
    """★ The subset rule is asked of `check_tool_capability`, not re-derived.

    A hand-rolled `required_capabilities(fallback) ⊆ allowed` check would silently omit the
    granted-tools test and the AGENT capabilities that function also enforces — a second thing to
    keep in sync, and the half nobody re-reads.
    """
    an, registry_mod = negotiation
    _register(registry_mod, "a", variant="b")
    _register(registry_mod, "b")

    seen = []

    def _check(*, token, run_id, user_id, tool_name):
        seen.append(tool_name)
        return {"ok": True, "error": None, "granted_tools": ["a", "b"], "allowed_capabilities": []}

    import AINDY.agents.capability_service as cap
    monkeypatch.setattr(cap, "check_tool_capability", _check, raising=True)

    an.negotiate_capability_denial(tool_name="a", token={}, run_id="r", user_id="u")

    assert seen == ["b"], (
        "negotiation must consult the real capability check for the FALLBACK; "
        f"it asked about {seen}"
    )


def test_a_broken_negotiation_degrades_to_no_negotiation(negotiation, monkeypatch):
    """★ It must never become a new failure mode layered on top of the denial.

    The caller falls through to the ordinary capability denial, which is what would have
    happened had negotiation not existed.
    """
    an, registry_mod = negotiation
    _register(registry_mod, "a", variant="b")
    _register(registry_mod, "b")

    def _boom(**kwargs):
        raise RuntimeError("capability service exploded")

    import AINDY.agents.capability_service as cap
    monkeypatch.setattr(cap, "check_tool_capability", _boom, raising=True)

    result = an.negotiate_capability_denial(
        tool_name="a", token={}, run_id="r", user_id="u"
    )
    assert result.granted is False
    assert result.outcome == an.OUTCOME_NO_VARIANT


# ── §6 — recorded, or it does not exist ──────────────────────────────────────


def test_every_outcome_moves_the_counter(negotiation, monkeypatch):
    """★ Without the counter, "never fires because denials are rare" and "not wired to anything"
    are indistinguishable — the ambiguity that made `CANCEL-REACH-1` ship one."""
    an, registry_mod = negotiation
    _register(registry_mod, "a", variant="b")
    _register(registry_mod, "b")
    _capability_stub(monkeypatch, ok_for={"b"}, granted_tools=["a", "b"])

    from AINDY.platform_layer.metrics import authority_negotiation_total

    def _read(outcome):
        for metric in authority_negotiation_total.collect():
            for sample in metric.samples:
                if sample.labels.get("outcome") == outcome and sample.name.endswith("_total"):
                    return sample.value
        return 0.0

    before = _read(an.OUTCOME_SUCCEEDED)
    an.negotiate_capability_denial(tool_name="a", token={}, run_id="r", user_id="u")
    after = _read(an.OUTCOME_SUCCEEDED)

    assert after == before + 1, "the succeeded label did not move"


def test_the_agent_event_type_is_registered(negotiation):
    """`AUTHORITY_NEGOTIATED` must be in the ENFORCED vocabulary or every emission warns.

    ★ There are TWO `AGENT_EVENT_TYPES` sets in this repo and only one is consulted:
    `agent_event_service.py`'s. The copy in `db/models/agent_event.py` has no importer and is
    four types stale — see `GUEST-BUILTINS-DEAD-1`'s neighbourhood. This asserts against the one
    that is actually read.
    """
    from AINDY.agents.agent_event_service import AGENT_EVENT_TYPES

    assert "AUTHORITY_NEGOTIATED" in AGENT_EVENT_TYPES


# ── ★ The wiring: the adapter must actually call it ──────────────────────────
#
# Everything above tests the negotiation module in isolation. None of it proves that
# `agent_execute_step` — the one CAPABILITY_DENIED site where a TOOL was refused — calls it, or
# that a granted outcome actually redirects execution. `CLAUDE.md`'s rule for routes generalises:
# reading the handler proves the code was written, not that the caller receives its answer.
#
# ★ There were no existing tests driving `agent_execute_step` at all, so this is scaffolding
#   rather than a pattern being followed.


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **kw):
        return self

    def first(self):
        return self._result


class _FakeDB:
    """Enough Session for `agent_execute_step`: add / query().filter().first() / commit."""

    def __init__(self, agent_run=None):
        self.added = []
        self.commits = 0
        self._agent_run = agent_run

    def add(self, obj):
        self.added.append(obj)

    def query(self, *a, **kw):
        return _FakeQuery(self._agent_run)

    def commit(self):
        self.commits += 1


@pytest.fixture
def adapter_harness(monkeypatch):
    """Drive the real `agent_execute_step` with its collaborators stubbed."""
    from AINDY.runtime import nodus_adapter

    monkeypatch.setenv("AINDY_AUTHORITY_NEGOTIATION", "1")

    executed = []

    def _execute_tool(*, tool_name, args, user_id, db, run_id, execution_token):
        executed.append(tool_name)
        return {"success": True, "result": {"ok": True}, "error": None}

    events = []

    def _record_agent_event(**kw):
        events.append(kw)
        return "evt"

    monkeypatch.setattr(nodus_adapter, "execute_tool", _execute_tool, raising=True)
    monkeypatch.setattr(nodus_adapter, "record_agent_event", _record_agent_event, raising=True)
    monkeypatch.setattr(nodus_adapter, "queue_system_event", lambda **kw: "sys", raising=True)
    monkeypatch.setattr(nodus_adapter, "emit_system_event", lambda **kw: "sys", raising=True)
    monkeypatch.setattr(
        "AINDY.memory.memory_helpers.enrich_context", lambda ctx: ctx, raising=True
    )
    return nodus_adapter, executed, events


def _drive(nodus_adapter, *, tool_name):
    state = {
        "agent_run_id": "00000000-0000-0000-0000-000000000001",
        "user_id": "00000000-0000-0000-0000-000000000002",
        "capability_token": {"sig": "x"},
        "correlation_id": "run_test",
        "steps": [{"tool": tool_name, "args": {}, "risk_level": "low", "description": "d"}],
        "current_step_index": 0,
        "step_results": [],
    }
    return nodus_adapter.agent_execute_step(state, {"db": _FakeDB(), "trace_id": "t"})


def test_the_adapter_redirects_execution_to_the_negotiated_fallback(adapter_harness, monkeypatch):
    """★ The load-bearing wiring test: a denial + a granted negotiation runs the FALLBACK."""
    nodus_adapter, executed, events = adapter_harness
    from AINDY.agents import authority_negotiation as an

    monkeypatch.setattr(
        nodus_adapter, "check_tool_capability",
        lambda **kw: (
            {"ok": True, "error": None, "granted_tools": [], "allowed_capabilities": []}
            if kw["tool_name"] == "queue_for_review"
            else {"ok": False, "error": "denied", "granted_tools": [],
                  "allowed_capabilities": []}
        ),
        raising=True,
    )
    monkeypatch.setattr(
        an, "negotiate_capability_denial",
        lambda **kw: an.NegotiationOutcome(an.OUTCOME_SUCCEEDED, variant="queue_for_review"),
        raising=True,
    )

    result = _drive(nodus_adapter, tool_name="send_email")

    assert executed == ["queue_for_review"], (
        f"the fallback was not the tool executed; ran {executed}"
    )
    assert result["status"] != "FAILURE", result
    negotiated = [e for e in events if e.get("event_type") == "AUTHORITY_NEGOTIATED"]
    assert negotiated, "no AUTHORITY_NEGOTIATED event was recorded"
    assert negotiated[0]["payload"]["denied_tool"] == "send_email"
    assert negotiated[0]["payload"]["fallback_tool"] == "queue_for_review"


def test_the_adapter_records_both_attempts_not_just_the_one_that_ran(adapter_harness, monkeypatch):
    """§6 — a record showing only the successful fallback describes a run that never hit a denial."""
    nodus_adapter, executed, events = adapter_harness
    from AINDY.agents import authority_negotiation as an

    monkeypatch.setattr(
        nodus_adapter, "check_tool_capability",
        lambda **kw: (
            {"ok": True, "error": None, "granted_tools": [], "allowed_capabilities": []}
            if kw["tool_name"] == "queue_for_review"
            else {"ok": False, "error": "denied", "granted_tools": [],
                  "allowed_capabilities": []}
        ),
        raising=True,
    )
    monkeypatch.setattr(
        an, "negotiate_capability_denial",
        lambda **kw: an.NegotiationOutcome(an.OUTCOME_SUCCEEDED, variant="queue_for_review"),
        raising=True,
    )

    result = _drive(nodus_adapter, tool_name="send_email")
    step = result["output_patch"]["step_results"][-1]

    assert step["tool"] == "queue_for_review"
    assert step["negotiated_from"] == "send_email"


def test_a_refused_negotiation_leaves_the_denial_exactly_as_it_was(adapter_harness, monkeypatch):
    """★ The no-regression case, and the one that matters most while the flag is off.

    When negotiation declines, the step must fail exactly as it does today — same FAILURE, same
    CAPABILITY_DENIED — and no `negotiated_from` key, so the record cannot suggest a negotiation
    that never happened.
    """
    nodus_adapter, executed, events = adapter_harness
    from AINDY.agents import authority_negotiation as an

    monkeypatch.setattr(
        nodus_adapter, "check_tool_capability",
        lambda **kw: {"ok": False, "error": "denied", "granted_tools": [],
                      "allowed_capabilities": []},
        raising=True,
    )
    monkeypatch.setattr(
        an, "negotiate_capability_denial",
        lambda **kw: an.NegotiationOutcome(an.OUTCOME_NO_VARIANT),
        raising=True,
    )

    result = _drive(nodus_adapter, tool_name="send_email")

    assert result["status"] == "FAILURE"
    assert executed == [], "a refused negotiation must execute nothing"
    assert [e for e in events if e.get("event_type") == "CAPABILITY_DENIED"]
    assert not [e for e in events if e.get("event_type") == "AUTHORITY_NEGOTIATED"]
    step = result["output_patch"]["step_results"][-1]
    assert "negotiated_from" not in step


def test_negotiated_from_survives_into_what_flow_history_persists(adapter_harness, monkeypatch):
    """§6 asks for both attempts in FlowHistory. This closes the last link in that chain.

    ★ The two halves were each proven and the JOIN between them was not, which is how a claim
    ends up true-by-inspection. `PersistentFlowRunner` stores a node's `output_patch` as
    `FlowHistory.output_patch = _json_safe(patch)` (`runner.py:427`), so the question is only
    whether `negotiated_from` survives that serialisation — asserted here against the runner's
    real `_json_safe`, not a stand-in for it.
    """
    nodus_adapter, executed, events = adapter_harness
    from AINDY.agents import authority_negotiation as an
    from AINDY.runtime.flow_engine.serialization import _json_safe

    monkeypatch.setattr(
        nodus_adapter, "check_tool_capability",
        lambda **kw: (
            {"ok": True, "error": None, "granted_tools": [], "allowed_capabilities": []}
            if kw["tool_name"] == "queue_for_review"
            else {"ok": False, "error": "denied", "granted_tools": [],
                  "allowed_capabilities": []}
        ),
        raising=True,
    )
    monkeypatch.setattr(
        an, "negotiate_capability_denial",
        lambda **kw: an.NegotiationOutcome(an.OUTCOME_SUCCEEDED, variant="queue_for_review"),
        raising=True,
    )

    result = _drive(nodus_adapter, tool_name="send_email")
    persisted = _json_safe(result["output_patch"])

    step = persisted["step_results"][-1]
    assert step["negotiated_from"] == "send_email", (
        "negotiated_from did not survive the serialisation FlowHistory stores through, so the "
        "history would show only the fallback — a run that never hit a denial"
    )
    assert step["tool"] == "queue_for_review"
