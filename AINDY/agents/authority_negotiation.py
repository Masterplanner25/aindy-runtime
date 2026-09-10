"""AUTHORITY-NEGOTIATION-1 phase 1 — one bounded downgrade attempt on a capability denial.

Design: ``docs/runtime/AUTHORITY_NEGOTIATION_DESIGN.md``. Read §2 and §7 before changing this.

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


def _count(outcome: str) -> None:
    """Record one negotiation resolution.

    ★ Best-effort and import-local, matching ``effect_ledger._count_gate``. A metrics failure
    must never change whether a step runs: the authority decision is the correctness path and
    the counter is observability, and inverting that would let a Prometheus problem change what
    a run is permitted to do.
    """
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
