"""`EFFECT-PARTIAL-1` + `EFFECT-OUTCOME-UNKNOWN-1` — the envelope half.

#560 added ``partial`` and ``unknown`` to `EffectRecord.status` and **nothing emitted them**.
Two things prevented it, and both are fixed together here because fixing either alone changes
nothing observable: the response envelope held two values, and the ledger write site passed a
hardcoded ``"success"`` no matter what the handler reported.

★★ THE TWO FACTS THE ENTIRE SAFETY ARGUMENT RESTS ON, ASSERTED RATHER THAN ASSUMED
------------------------------------------------------------------------------------
Widening a value set that every consumer branches on is normally a silent breaking change. It is
safe here for two reasons, and **each gets a test, because each is a claim about the codebase
that can stop being true without anyone noticing**:

1. Nothing emits the new values, so upgrading changes no response anyone receives.
2. Every consumer of a dispatch envelope branches with ``!= "success"``, so an unaware one
   reads a ``partial`` as a failure — the **waste**, which is the pre-existing behaviour,
   never the **lie**.

**(2) was false when this was first written** — four sites used ``== "error"``, where a partial
would have read as success. They were fixed here. That is exactly why both facts get a test
rather than a comment: the first version of this file asserted (2) in prose, and the prose was
wrong.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only


# ── the claim vocabulary ─────────────────────────────────────────────────────


def test_no_claim_is_a_success():
    """The default path: an ordinary handler return is untouched."""
    from AINDY.kernel.syscall_outcome import resolve_outcome

    payload, outcome = resolve_outcome({"rows": 3})
    assert payload == {"rows": 3}
    assert outcome.status == "success"
    assert outcome.outcome is None
    assert outcome.ledger_status == "success"


def test_a_partial_claim_is_resolved_and_the_marker_is_stripped():
    from AINDY.kernel.syscall_outcome import OUTCOME_KEY, partial, resolve_outcome

    units = [{"id": 1, "ok": True}, {"id": 2, "ok": False}]
    payload, outcome = resolve_outcome({"sent": 1, OUTCOME_KEY: partial(units, detail="2 failed")})

    assert OUTCOME_KEY not in payload, (
        "the reserved marker reached the caller. Two representations of one fact will "
        "eventually disagree."
    )
    assert payload == {"sent": 1}
    assert outcome.status == "partial"
    assert outcome.outcome["units"] == units
    assert outcome.ledger_status == "partial"


def test_an_unknown_claim_needs_no_units():
    """★ `unknown` is a claim about the WORLD — a read timeout after a full request write.

    There is nothing to enumerate: that is the whole point of the value. Requiring units here
    would force a handler to fabricate detail it does not have, which is how an honest ambiguity
    turns into a fake answer.
    """
    from AINDY.kernel.syscall_outcome import OUTCOME_KEY, resolve_outcome, unknown

    _, outcome = resolve_outcome({OUTCOME_KEY: unknown(detail="read timeout after full write")})
    assert outcome.status == "unknown"
    assert outcome.ledger_status == "unknown"
    assert outcome.refusal is None


# ── the rule that makes `partial` worth having ───────────────────────────────


def test_a_partial_with_no_units_is_refused():
    """★★ THE RULE THIS WHOLE MECHANISM EXISTS TO ENFORCE.

    A `partial` naming no units reports that something went wrong and removes the ability to say
    what — strictly worse than `failed`, which at least does not pretend to be informative.
    """
    from AINDY.kernel.syscall_outcome import OUTCOME_KEY, resolve_outcome

    _, outcome = resolve_outcome({OUTCOME_KEY: {"status": "partial", "units": []}})
    assert outcome.status == "error"
    assert outcome.refusal is not None
    assert "units" in outcome.refusal


def test_a_refused_claim_is_recorded_as_failed_not_as_partial():
    """★ The entry's own reading: an unaccountable partial *is* a failure.

    Writing `partial` to the ledger here would put a row in the table that says something went
    wrong and cannot say what — the exact state the value was added to prevent.
    """
    from AINDY.kernel.syscall_outcome import OUTCOME_KEY, resolve_outcome

    _, outcome = resolve_outcome({OUTCOME_KEY: {"status": "partial"}})
    assert outcome.ledger_status == "failed"


@pytest.mark.parametrize(
    "claim",
    [
        "not-a-dict",
        {"status": "success"},          # success needs no claim
        {"status": "error"},            # error is the dispatcher's own path
        {"status": "partial", "units": "not-a-list"},
        {"status": "unknown", "detail": 42},
    ],
)
def test_malformed_claims_are_refused(claim):
    """★ `success` and `error` are refused deliberately, not by oversight.

    Allowing them would give one outcome two spellings, and the second would bypass the
    machinery built around the first — one field meaning two things is what
    `EVENTBUS-PUBLISH-LATCH-1` cost.
    """
    from AINDY.kernel.syscall_outcome import OUTCOME_KEY, resolve_outcome

    _, outcome = resolve_outcome({OUTCOME_KEY: claim})
    assert outcome.status == "error"
    assert outcome.refusal is not None


def test_the_marker_is_stripped_even_when_refused():
    """A caller must never see the reserved key, including on the failure path."""
    from AINDY.kernel.syscall_outcome import OUTCOME_KEY, resolve_outcome

    payload, _ = resolve_outcome({"kept": 1, OUTCOME_KEY: "garbage"})
    assert payload == {"kept": 1}


# ── the two facts the safety argument rests on ───────────────────────────────


def test_no_registered_handler_emits_an_outcome_claim():
    """★★ FACT 1 — this is what makes widening the value set a no-op on upgrade.

    Checked over the AST rather than by string match: a comment or docstring mentioning the key
    must not satisfy it. If this ever fails, the widening is no longer latent and the release
    notes have to say so before the handler ships.
    """
    import ast
    from pathlib import Path

    offenders = []
    for path in Path("AINDY").rglob("*.py"):
        if path.name == "syscall_outcome.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "_outcome":
                offenders.append(str(path))
            elif isinstance(node, ast.Name) and node.id == "OUTCOME_KEY":
                offenders.append(str(path))

    assert not offenders, (
        f"a handler emits an outcome claim: {sorted(set(offenders))}. The new envelope values "
        f"are no longer latent — consumers must be told before this ships."
    )


def test_no_envelope_consumer_branches_on_status_equals_error():
    """★★ FACT 2, AND IT WAS FALSE WHEN THIS CHANGE WAS FIRST WRITTEN.

    The safety argument began as "every consumer uses `!= success`, so a partial reads as a
    failure — the waste, never the lie." That was reached by grepping for one form and
    generalising, and it was **wrong**: four sites consumed a dispatch envelope with
    `== "error"`, where a `partial` would have fallen straight through to the success path.
    They were fixed as part of this change; this test is what stops a fifth appearing.

    ★ Scoped to files that actually call the dispatcher. A route-level canonical response has
    its own `status` in a separate value space, and folding those in produced six false hits
    that buried the four real ones — a check nobody can read is a check nobody keeps.
    """
    import re
    from pathlib import Path

    consumes = re.compile(r"get_dispatcher\(\)\.dispatch\(|dispatch_syscall\(")
    # Plain substrings rather than a quote-juggling regex: both spellings of the unsafe branch,
    # in both quote styles. Readable beats clever in a test whose whole job is to be believed.
    unsafe_forms = [
        '["status"] == "error"', "['status'] == 'error'",
        '.get("status") == "error"', ".get('status') == 'error'",
    ]

    offenders = []
    for path in Path("AINDY").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if not consumes.search(text):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if any(form in line for form in unsafe_forms):
                offenders.append(f"{path}:{i}")

    assert not offenders, (
        f"a dispatch-envelope consumer branches on status == 'error', so a 'partial' reads "
        f"there as success — the lie this change exists to prevent: {offenders}"
    )


# ── the dispatcher carries it end to end ─────────────────────────────────────


def test_the_envelope_carries_a_partial_status_and_its_units():
    """★★ END TO END — the behaviour the entry asked for.

    Asserted on a real `_dispatch` return rather than on `resolve_outcome` alone: the whole
    defect was that the resolution existed nowhere in the path a caller actually travels.
    """
    from unittest.mock import patch

    from AINDY.kernel.syscall_outcome import OUTCOME_KEY, partial
    from AINDY.kernel.syscall_registry import SyscallContext

    units = [{"id": "a", "ok": True}, {"id": "b", "ok": False}]

    def _handler(payload, context):
        return {"sent": 1, OUTCOME_KEY: partial(units, detail="1 of 2 failed")}

    envelope = _drive(_handler)
    assert envelope["status"] == "partial", (
        "the dispatcher flattened a partial outcome back into the binary envelope"
    )
    assert envelope["outcome"]["units"] == units
    assert envelope["data"] == {"sent": 1}
    assert OUTCOME_KEY not in envelope["data"]


def test_an_ordinary_handler_is_completely_unaffected():
    """★ The liveness control for every assertion above, and the deployment safety property.

    "status is success" is also what a totally unwired mechanism produces, so this only means
    something next to the partial case passing.
    """
    envelope = _drive(lambda payload, context: {"rows": 2})
    assert envelope["status"] == "success"
    assert envelope["outcome"] is None
    assert envelope["data"] == {"rows": 2}


def test_a_malformed_claim_returns_an_error_envelope_not_an_exception():
    """★ Refused, not raised — the effect already happened.

    Raising would discard the handler's data and hand the caller an exception instead of an
    answer, which is `ROUTE-GUARD-1`'s confusion: a caller cannot tell "rejected" from "the
    server broke".
    """
    from AINDY.kernel.syscall_outcome import OUTCOME_KEY

    envelope = _drive(lambda payload, context: {OUTCOME_KEY: {"status": "partial"}})
    assert envelope["status"] == "error"
    assert "outcome contract violation" in envelope["error"]


def test_the_error_envelope_keeps_the_key_shape_stable():
    """A consumer reading `envelope["outcome"]` must not KeyError on the error path."""
    def _boom(payload, context):
        raise RuntimeError("handler exploded")

    envelope = _drive(_boom)
    assert envelope["status"] == "error"
    assert envelope["outcome"] is None


# ── harness ──────────────────────────────────────────────────────────────────


def _drive(handler):
    """Dispatch a stub syscall through the real dispatcher and return its envelope."""
    from unittest.mock import patch

    from AINDY.kernel.syscall_dispatcher import SyscallDispatcher
    from AINDY.kernel.syscall_registry import SyscallContext, SyscallEntry

    entry = SyscallEntry(handler, "memory.read", "probe")

    dispatcher = SyscallDispatcher()
    ctx = SyscallContext(
        execution_unit_id="eu-1", user_id="u-1", capabilities=["memory.read"],
        trace_id="t-1", memory_context={}, metadata={},
    )
    with patch.dict(
        "AINDY.kernel.syscall_registry.SYSCALL_REGISTRY",
        {"sys.v1.test.outcome": entry},
        clear=False,
    ):
        return dispatcher.dispatch("sys.v1.test.outcome", {}, ctx)


# ── the ledger, which is where the original defect actually lived ────────────


def _drive_with_gate(handler):
    """Dispatch through the real EXACTLY_ONCE gate, capturing the ledger write.

    ★ This harness exists because a mutation survived without it. Reverting the ledger write to
    the hardcoded ``"success"`` it used to be — *the original defect* — left all seventeen other
    tests green. `partial` on the column and `partial` in the envelope are both worthless if the
    record still says the effect fully applied.
    """
    import uuid
    from unittest.mock import MagicMock, patch

    from AINDY.kernel.syscall_dispatcher import SyscallDispatcher
    from AINDY.kernel.syscall_registry import SyscallContext, SyscallEntry

    entry = SyscallEntry(handler, "memory.read", "probe")
    entry.execution_guarantee = "EXACTLY_ONCE"

    eu_id = str(uuid.uuid4())
    ctx = SyscallContext(
        execution_unit_id=eu_id, user_id="u-1", capabilities=["memory.read"],
        trace_id=eu_id, memory_context={}, metadata={},
    )

    completed: list[tuple] = []
    with patch.dict(
        "AINDY.kernel.syscall_registry.SYSCALL_REGISTRY",
        {"sys.v1.test.gated": entry}, clear=False,
    ), patch("AINDY.db.database.SessionLocal", return_value=MagicMock()), patch(
        "AINDY.kernel.syscall_dispatcher._resolve_effect_record", return_value=(False, None)
    ), patch(
        "AINDY.kernel.syscall_dispatcher._complete_effect_record",
        side_effect=lambda db, aid, status, payload: completed.append((status, payload)),
    ):
        envelope = SyscallDispatcher().dispatch("sys.v1.test.gated", {}, ctx)
    return envelope, completed


def test_the_effect_record_is_written_partial_not_success():
    """★★ THE ORIGINAL DEFECT, and the assertion that was missing until a mutation found it.

    #560 added `partial` to `EffectRecord.status` and the dispatcher's ledger write passed a
    hardcoded `"success"` — so the column could hold the value and no code path could ever put
    it there. A record that says an effect fully applied when 2 of 5 units failed is the **lie**
    in durable form, and unlike the envelope it is what an operator reconciles from afterwards.
    """
    from AINDY.kernel.syscall_outcome import OUTCOME_KEY, partial

    units = [{"id": "a", "ok": True}, {"id": "b", "ok": False}]
    envelope, completed = _drive_with_gate(
        lambda payload, context: {"sent": 1, OUTCOME_KEY: partial(units)}
    )

    assert envelope["status"] == "partial"
    assert completed, "the gate never wrote an effect record; the harness is not exercising it"
    assert completed[-1][0] == "partial", (
        f"the effect record was written {completed[-1][0]!r}. A partial effect recorded as "
        f"success is the lie in durable form — and the record is what an operator reconciles "
        f"from once the response is long gone."
    )


def test_an_ungated_success_still_records_success():
    """★ Liveness control: without it, a ledger write hardcoded to `partial` would pass above."""
    _, completed = _drive_with_gate(lambda payload, context: {"rows": 1})
    assert completed and completed[-1][0] == "success"


def test_a_refused_claim_records_failed():
    """An unaccountable partial is a failure in the record too, not a `partial` row with no units."""
    from AINDY.kernel.syscall_outcome import OUTCOME_KEY

    envelope, completed = _drive_with_gate(
        lambda payload, context: {OUTCOME_KEY: {"status": "partial"}}
    )
    assert envelope["status"] == "error"
    assert completed and completed[-1][0] == "failed"
