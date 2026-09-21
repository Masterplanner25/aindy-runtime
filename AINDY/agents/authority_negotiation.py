"""AUTHORITY-NEGOTIATION-1 phase 1 — one bounded downgrade attempt on a capability denial.

Design: ``docs/design/AUTHORITY_NEGOTIATION_DESIGN.md``. Read §2 and §7 before changing this.

**The problem.** A denied capability terminates the step, and approval is whole-plan, so the only
recovery is a human approving an entirely new run — which discards the durable state the original
accumulated. A run that did nine steps of real work and was refused on the tenth starts from zero.

**The shape:** bounded (exactly one attempt), directional (downgrade only, never escalate), and
recorded.

★ **This module cannot grant authority, and that is structural rather than careful.** It decides
only *which tool to attempt*. The tool it nominates is then executed through ``execute_tool``,
which runs its own ``check_tool_capability`` — so a negotiated tool passes exactly the same gate
an ordinary one does. There is no second minting path, no amendment, and no widening, which is
§7's first prohibition made unreachable rather than merely unauthorised.

★ **No new token is needed, and §2 explains why the tempting formulation is wrong.** The
executable condition is *not* "the fallback's capabilities are a subset of the denied capability"
— that says nothing about whether the token grants them either. It is
``required_capabilities(fallback) ⊆ token.allowed_capabilities``, checked against the token in
hand. Here that is not reimplemented as a set comparison: it is asked of
``check_tool_capability`` directly, which is the code that actually enforces it. A hand-rolled
subset check beside the real one is a second thing to keep in sync, and it is the half nobody
re-reads.

★ **The fallback is declared by the TOOL, at registration — never by the plan or the model.** The
thing being constrained must not choose its own constraint. See ``register_tool(...,
degraded_variant=)`` and ``validate_degraded_variants()`` (phase 0, #600).

★ **Arguments carry over unchanged, and that is a contract on the declaration.** There is no
argument-mapping vocabulary and deliberately so — declaring ``degraded_variant=X`` is the tool
author's promise that ``X`` accepts the original's arguments. This is stated because it is the
one part of the mechanism a declaration cannot express, so a mismatched variant fails at
execution rather than at declaration.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# Outcome labels. Four come from design §6; ``disabled`` and ``chain_refused`` are additions
# explained on the counter in ``metrics.py``.
OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_NO_VARIANT = "no_variant"
OUTCOME_VARIANT_DENIED = "variant_denied"
OUTCOME_REFUSED_NOT_GRANTED = "refused_not_granted"
OUTCOME_DISABLED = "disabled"
OUTCOME_CHAIN_REFUSED = "chain_refused"


def authority_negotiation_enabled() -> bool:
    """Phase 1 ships default-OFF; phase 3 flips it on evidence, not on code.

    ★ The guard is read here and not cached, and it is read BELOW nothing — there is no
    test-mode short-circuit above this decision. Two such short-circuits were found in
    ``FR-15``'s own path within a fortnight, and both made a soak vacuous rather than failing.
    """
    return os.getenv("AINDY_AUTHORITY_NEGOTIATION", "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class NegotiationOutcome:
    """What the negotiation decided, and why."""

    outcome: str
    variant: Optional[str] = None
    error: Optional[str] = None

    @property
    def granted(self) -> bool:
        return self.outcome == OUTCOME_SUCCEEDED


#: FR-38 / DEC-070 — in a nodus_vm pool worker the counter is process-local and never served;
#: inside `negotiation_tally_scope()` every `_count` lands here instead, the tally rides the
#: worker reply as `authority_negotiation`, and the parent records it (deferral replaces
#: observation, DEC-041 — the same shape as `llm_usage` and `args_validation`).
_DEFERRED_TALLY: ContextVar[Optional[dict[str, int]]] = ContextVar(
    "aindy_authority_negotiation_tally", default=None
)


@contextmanager
def negotiation_tally_scope() -> Iterator[dict[str, int]]:
    tally: dict[str, int] = {}
    token = _DEFERRED_TALLY.set(tally)
    try:
        yield tally
    finally:
        _DEFERRED_TALLY.reset(token)


def apply_deferred_negotiation_tally(tally: object) -> int:
    """Record a shipped tally in THIS process. Returns the resolutions recorded. Never raises."""
    if not isinstance(tally, dict):
        return 0
    total = 0
    try:
        from AINDY.platform_layer.metrics import authority_negotiation_total

        for outcome, n in tally.items():
            n = int(n or 0)
            if n > 0:
                authority_negotiation_total.labels(outcome=str(outcome)).inc(n)
                total += n
    except Exception:  # pragma: no cover - observability must not break execution
        pass
    return total


def _count(outcome: str) -> None:
    """Record one negotiation resolution.

    ★ Best-effort and import-local, matching ``effect_ledger._count_gate``. A metrics failure
    must never change whether a step runs: the authority decision is the correctness path and
    the counter is observability, and inverting that would let a Prometheus problem change what
    a run is permitted to do.
    """
    tally = _DEFERRED_TALLY.get()
    if tally is not None:
        tally[outcome] = tally.get(outcome, 0) + 1
        return
    try:
        from AINDY.platform_layer.metrics import authority_negotiation_total

        authority_negotiation_total.labels(outcome=outcome).inc()
    except Exception:  # pragma: no cover - observability must not break execution
        pass


def negotiate_capability_denial(
    *,
    tool_name: str,
    token: Optional[dict],
    run_id: str,
    user_id: str,
) -> NegotiationOutcome:
    """Offer at most one downgrade for a step that was refused at the authority it requested.

    Returns an outcome whose ``granted`` is True only when a declared fallback exists AND the
    token in hand already authorises it. Every other path returns a labelled refusal; none
    raises, because a negotiation failure must degrade to the ordinary denial rather than
    becoming a second, different error on top of it.
    """
    if not authority_negotiation_enabled():
        _count(OUTCOME_DISABLED)
        return NegotiationOutcome(OUTCOME_DISABLED)

    try:
        from AINDY.agents.capability_service import check_tool_capability
        from AINDY.agents.tool_registry import TOOL_REGISTRY

        entry = TOOL_REGISTRY.get(tool_name)
        variant = entry.get("degraded_variant") if isinstance(entry, dict) else None
        if not variant:
            _count(OUTCOME_NO_VARIANT)
            return NegotiationOutcome(OUTCOME_NO_VARIANT)

        # §4 — a fallback may not declare a fallback. `validate_degraded_variants()` refuses
        # chains at startup, but it only sees the tools registered when it runs; a tool
        # registered afterwards is not swept. Re-checked here so the bound holds structurally
        # rather than depending on when registration happened.
        target = TOOL_REGISTRY.get(variant)
        if isinstance(target, dict) and target.get("degraded_variant"):
            _count(OUTCOME_CHAIN_REFUSED)
            logger.warning(
                "[AuthorityNegotiation] %s -> %s refused: the fallback declares its own "
                "degraded_variant=%r. Chains are refused structurally.",
                tool_name, variant, target["degraded_variant"],
            )
            return NegotiationOutcome(
                OUTCOME_CHAIN_REFUSED,
                variant=variant,
                error=f"degraded_variant {variant!r} declares its own fallback; chains are refused",
            )

        # ★ Ask the enforcing code, do not reimplement its rule. This covers the token's
        #   granted_tools, the tool's required capabilities AND the agent's — a hand-rolled
        #   subset check would silently omit the last two.
        check = check_tool_capability(
            token=token, run_id=run_id, user_id=user_id, tool_name=variant
        )
        if check.get("ok"):
            _count(OUTCOME_SUCCEEDED)
            return NegotiationOutcome(OUTCOME_SUCCEEDED, variant=variant)

        # ★ Classified structurally, never by matching the error string. `RETRY-CLASSIFY-1` is
        #   the entry for what substring-matching an error message costs.
        if variant not in (check.get("granted_tools") or []):
            outcome = OUTCOME_REFUSED_NOT_GRANTED
        else:
            outcome = OUTCOME_VARIANT_DENIED
        _count(outcome)
        return NegotiationOutcome(outcome, variant=variant, error=check.get("error"))

    except Exception as exc:  # pragma: no cover - defensive; see the docstring
        # A broken negotiation must look like no negotiation, not like a new failure mode. The
        # caller falls through to the ordinary denial, which is what would have happened anyway.
        _count(OUTCOME_NO_VARIANT)
        logger.warning("[AuthorityNegotiation] negotiation failed for %s: %s", tool_name, exc)
        return NegotiationOutcome(OUTCOME_NO_VARIANT, error=str(exc))


# ── Phase 2 — the WAIT-gate fallback kind (design §5) ────────────────────────
#
# When no variant recovered the denial, a tool that declared `on_denial="wait"` PARKS THE RUN
# on the existing durable wait instead of failing it. The accumulated state survives, and an
# operator decides — with the run's context in front of them, and `sys.v1.agent.simulate`
# available to ask what would have happened (§5: rehearsal informing a human, never replacing
# an effect).
#
# ★ The operator can SKIP the step or ABORT the run. Not GRANT: §7's no-widening rule — the
#   gate cannot mint, amend or widen anything, and the decision vocabulary does not contain the
#   word. Not PROVIDE-A-RESULT either (deferred, recorded in the design): an asserted result is
#   `EFFECT-PARTIAL-1`'s lie in a nicer costume until it is designed on its own terms.
#
# ★ The decision payload is TYPED — the runtime's own first use of `WAIT-TYPED-CONTRACT-1`'s
#   `resume_schema`. A resume whose payload lacks `decision` is refused at the door (422) and
#   the run stays parked; an unknown decision string re-parks the run and is recorded.

OUTCOME_WAITING = "waiting"

#: The event a parked step waits on. One name for every gate; the wake is run-scoped
#: (`RESUME-FANOUT-UNSCOPED-1`), so a shared name cannot cross runs.
AUTHORITY_DECISION_EVENT = "agent.authority.decision"

DECISION_SKIP = "skip"
DECISION_ABORT = "abort"
DECISIONS = (DECISION_SKIP, DECISION_ABORT)

#: Dispatcher dialect (`syscall_versioning.validate_payload`), checked by `route_event`.
AUTHORITY_DECISION_SCHEMA = {
    "required": ["decision"],
    "properties": {"decision": {"type": "string"}, "note": {"type": "string"}},
}

#: The flow-state key carrying the gate while the run is parked. Reserved like
#: `__pending_request`: the runtime's, not a step's.
GATE_STATE_KEY = "authority_gate"


def denial_gate_declared(tool_name: str) -> bool:
    """Whether a refused *and unrecovered* step should park rather than fail.

    True only when negotiation is enabled AND the tool declared ``on_denial="wait"``. The flag
    is read here, not cached, and below no test-mode short-circuit — the same discipline as
    `authority_negotiation_enabled`.
    """
    if not authority_negotiation_enabled():
        return False
    try:
        from AINDY.agents.tool_registry import ON_DENIAL_WAIT, TOOL_REGISTRY

        entry = TOOL_REGISTRY.get(tool_name)
        return isinstance(entry, dict) and entry.get("on_denial") == ON_DENIAL_WAIT
    except Exception:  # pragma: no cover - a broken lookup is "no gate", never a new failure
        return False


def build_authority_gate(*, step_index: int, tool_name: str, denied_error: str | None,
                         negotiation_outcome: str, variant: str | None) -> dict:
    """The record parked beside the wait: what was refused, and why no variant recovered it."""
    return {
        "step_index": int(step_index),
        "tool": str(tool_name),
        "denied_error": denied_error,
        "negotiation_outcome": negotiation_outcome,
        "variant": variant,
        "event": AUTHORITY_DECISION_EVENT,
        "decisions": list(DECISIONS),
    }


def read_gate_decision(payload: object) -> tuple[str | None, str | None]:
    """``(decision, note)`` from a resume payload, or ``(None, note)`` when the decision is not
    one of `DECISIONS`. The schema guarantees the key is present and a string; it cannot
    guarantee the value, so an unknown one is refused HERE — the gate re-parks."""
    if not isinstance(payload, dict):
        return None, None
    decision = str(payload.get("decision") or "").strip().lower()
    note = payload.get("note")
    note = str(note) if note is not None else None
    return (decision if decision in DECISIONS else None), note


def count_gate_outcome(outcome: str) -> None:
    _count(outcome)
