"""`EFFECT-PARTIAL-1` + `EFFECT-OUTCOME-UNKNOWN-1` — the envelope half.

#560 gave `EffectRecord.status` the words ``partial`` and ``unknown`` and **nothing emitted
them**. This module is why nothing could: the response envelope has two values, so a handler that
discovered a mixed outcome had nowhere to put it, and the ledger write site passed a hardcoded
``"success"`` regardless of what happened. A 5-unit effect with 2 failures was therefore either a
**lie** (``success``, silently partial) or a **waste** (``error``, discarding the 3 that landed).

★★ WHY WIDENING A VALUE SET IS SAFE **HERE**, WHEN IT USUALLY IS NOT
---------------------------------------------------------------------
Adding a value to a field every consumer branches on is normally a breaking change that fails
silently. Two facts make this one landable, and **if either stops being true this reasoning has
to be redone rather than re-cited**:

1. **Nothing emits the new values.** A handler opts in by returning the reserved key, and no
   registered handler does today — pinned by a test. So upgrading changes no response that any
   consumer receives; the widening is latent until someone deliberately uses it.
2. **Every consumer of a dispatch envelope branches with ``!= "success"``**, so an unaware one
   reads a ``partial`` as a failure: the **waste**, which is the pre-existing behaviour, never
   the **lie**.

   ★★ **That was FALSE when this module was first written, and the correction is the most
   useful thing here.** It was reached by grepping for one form and generalising. Four sites
   consumed a dispatch envelope with ``== "error"`` — `platform_ops_router` (the
   `/platform/syscall` route), `nodus_execution_service`, and two in the flow engine's
   `entrypoints` — and at every one of them a ``partial`` would have fallen straight through to
   the success path. They were changed to ``!= "success"`` as part of this work, and a test now
   stops a fifth appearing. Route-level canonical responses have their own ``status`` in a
   separate value space and are deliberately out of scope.

★ THE RULE THAT MAKES `partial` WORTH HAVING, ENFORCED HERE RATHER THAN DOCUMENTED
-----------------------------------------------------------------------------------
A ``partial`` naming no units is **strictly worse than ``failed``** — it reports that something
went wrong and removes the ability to say what. So an outcome claim without per-unit detail is
**refused**: the call returns an error envelope and the record is written ``failed``, which is
the entry's own words for what that situation actually is. The refusal is counted, because a
handler making a malformed claim is a bug that has to be visible rather than a mystery in a graph.

★ AND WHY A REFUSAL IS NOT A `SyscallContractViolation`
--------------------------------------------------------
The effect has already happened by the time a handler returns. Raising would discard the data the
handler did produce and hand the caller an exception instead — the failure `ROUTE-GUARD-1`
catalogued, where a caller cannot tell "rejected" from "the server broke". The dispatcher's
existing treatment of a non-JSON-serialisable EXACTLY_ONCE result takes the same line for the
same reason: never fail a call whose effect already landed over a bookkeeping problem.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

#: The reserved key a handler adds to its returned dict to claim a non-binary outcome.
#: Stripped before output-schema validation, so a strict ``additionalProperties: false`` schema
#: never sees it — a handler must not have to widen its own schema in order to report the truth.
OUTCOME_KEY = "_outcome"

ENVELOPE_STATUS_SUCCESS = "success"
ENVELOPE_STATUS_PARTIAL = "partial"
ENVELOPE_STATUS_UNKNOWN = "unknown"
ENVELOPE_STATUS_ERROR = "error"

#: Every value the envelope's ``status`` may hold. ★ A consumer must treat an unrecognised value
#: as *not success* and reconcile. That is the documented contract, and it is also what every
#: in-runtime consumer already does — see the module docstring.
ENVELOPE_STATUSES = frozenset({
    ENVELOPE_STATUS_SUCCESS,
    ENVELOPE_STATUS_PARTIAL,
    ENVELOPE_STATUS_UNKNOWN,
    ENVELOPE_STATUS_ERROR,
})

#: The values a handler may *claim*. ``success`` is the default and needs no claim; ``error`` is
#: the dispatcher's own path (a handler signals failure by raising). Allowing either here would
#: give one outcome two spellings, and the second spelling would bypass the machinery built
#: around the first — the shape `EVENTBUS-PUBLISH-LATCH-1` paid for when one field meant two
#: things.
CLAIMABLE_STATUSES = frozenset({ENVELOPE_STATUS_PARTIAL, ENVELOPE_STATUS_UNKNOWN})

#: Envelope status -> the `EffectRecord.status` written for it. ★ A refused claim maps to
#: ``failed`` deliberately: the entry's own argument is that an unaccountable partial *is* a
#: failure, so the record says so rather than inventing a fifth reading of the column.
_LEDGER_STATUS = {
    ENVELOPE_STATUS_SUCCESS: "success",
    ENVELOPE_STATUS_PARTIAL: "partial",
    ENVELOPE_STATUS_UNKNOWN: "unknown",
    ENVELOPE_STATUS_ERROR: "failed",
}


@dataclass(frozen=True)
class ResolvedOutcome:
    """What the dispatcher should do with a handler's return value."""

    status: str
    #: ``{"units": [...], "detail": str | None}`` for a claimed outcome, else ``None``.
    outcome: Optional[dict[str, Any]]
    #: Set when a malformed claim was refused; the message is caller-visible.
    refusal: Optional[str] = None

    @property
    def ledger_status(self) -> str:
        """The `EffectRecord.status` this outcome is recorded as."""
        return _LEDGER_STATUS[self.status]


def partial(units: Sequence[Any], *, detail: Optional[str] = None) -> dict[str, Any]:
    """Build the reserved-key value for a partially-applied effect.

    ``units`` must name what happened per unit and must not be empty — a ``partial`` that cannot
    say which units landed is refused by :func:`resolve_outcome`, so producing one here would
    only move the failure further from its cause.
    """
    return {"status": ENVELOPE_STATUS_PARTIAL, "units": list(units), "detail": detail}


def unknown(
    *, detail: Optional[str] = None, units: Optional[Sequence[Any]] = None
) -> dict[str, Any]:
    """Build the reserved-key value for a dispatched effect whose outcome was not observed.

    ★ This is a claim about the **world**, not about the runtime's confidence — the narrow case
    is a read timeout after a full request write. An exception nobody classified is ``failed``;
    routing it here turns a knowable failure into a permanent ambiguity a human has to resolve.
    """
    return {
        "status": ENVELOPE_STATUS_UNKNOWN,
        "units": list(units or []),
        "detail": detail,
    }


def resolve_outcome(data: dict[str, Any]) -> tuple[dict[str, Any], ResolvedOutcome]:
    """Split a handler's return value into ``(payload, outcome)``.

    The reserved key is always removed from the payload, **including when the claim is refused**
    — a caller must never receive the marker itself, or two representations of the same fact
    reach consumers and they will eventually disagree.
    """
    if OUTCOME_KEY not in data:
        return data, ResolvedOutcome(ENVELOPE_STATUS_SUCCESS, None)

    payload = {k: v for k, v in data.items() if k != OUTCOME_KEY}
    claim = data.get(OUTCOME_KEY)

    def _refuse(reason: str) -> tuple[dict[str, Any], ResolvedOutcome]:
        return payload, ResolvedOutcome(ENVELOPE_STATUS_ERROR, None, refusal=reason)

    if not isinstance(claim, dict):
        return _refuse(f"{OUTCOME_KEY} must be a dict, got {type(claim).__name__}")

    status = claim.get("status")
    if status not in CLAIMABLE_STATUSES:
        return _refuse(
            f"{OUTCOME_KEY}.status must be one of {sorted(CLAIMABLE_STATUSES)}, got {status!r}"
        )

    units = claim.get("units")
    if units is not None and not isinstance(units, list):
        return _refuse(f"{OUTCOME_KEY}.units must be a list, got {type(units).__name__}")

    if status == ENVELOPE_STATUS_PARTIAL and not units:
        # ★ The rule this module exists to enforce — see the module docstring.
        return _refuse(
            "a 'partial' outcome must name the per-unit results in .units; a partial that "
            "cannot say which units landed is strictly worse than 'failed'"
        )

    detail = claim.get("detail")
    if detail is not None and not isinstance(detail, str):
        return _refuse(f"{OUTCOME_KEY}.detail must be a string, got {type(detail).__name__}")

    return payload, ResolvedOutcome(status, {"units": units or [], "detail": detail})
