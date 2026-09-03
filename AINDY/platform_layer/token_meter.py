"""Observe token usage on an LLM response — the meter half of `COST-GOVERNOR-1`.

The runtime enforces a **300-second wall-clock ceiling** and a **256 MiB memory ceiling** on
execution units whose dominant cost is **tokens**, which it did not measure at all. Four quota
dimensions exist in `resource_manager` — wall time, memory, syscalls, concurrency — and none of
them is the one that matters for an LLM runtime.

★★ AND IT WAS WORSE THAN A MISSING CAP: THE QUANTITY WAS DISCARDED AT THE BOUNDARY.
------------------------------------------------------------------------------------
Every provider returns usage on the response. Every client here then did::

    return str(response.choices[0].message.content or "")

…so the numbers existed for the length of one stack frame and were dropped. Nothing downstream
could have metered spend even if it wanted to; there was nothing left to meter. That is why the
entry says **meter first, and the meter is the larger half** — this is not "we measure and fail
to cap", it is "the quantity is never observed".

★ WHAT THIS IS NOT
-------------------
It is **not** the governor. Nothing here refuses a call, and no budget exists yet. Admission
control needs *reserve → call → reconcile* — atomically pre-filling a counter so N concurrent
requests cannot all pass a read-then-compare — checked against a cache on the hot path, never the
database. That is a separate change and it needs this one first, because you cannot reconcile
against an actual you never recorded.

It is also **not** revenue metering (`BILLING-2`, deferred to launch). A governor stops a runaway
loop; an invoice reconciles a month. Sharing a meter is fine, coupling the decisions is not —
deferring the governor behind a commercial-launch gate is how a runaway run becomes a bill.

★ WHY THE LABELS STOP AT PROVIDER AND MODEL
--------------------------------------------
Tenant would be the more useful partition for a governor, and it is deliberately absent: a
Prometheus label is a time series per distinct value, so a tenant label makes cardinality grow
with the customer list and turns the metric into an operational problem of its own. Per-tenant
accounting belongs in the counter the governor will check — a cache, keyed and expiring — not in
the observability surface. Recording that here so the next person does not read the omission as
an oversight and "fix" it.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from AINDY.platform_layer.metrics import llm_tokens_total, llm_usage_unreadable_total

    _METRICS_AVAILABLE = True
except Exception:  # pragma: no cover - metrics are optional at import time
    _METRICS_AVAILABLE = False


def extract_token_usage(response: Any) -> tuple[int, int] | None:
    """``(prompt_tokens, completion_tokens)`` from a provider response, or ``None``.

    Handles the two shapes in use here without importing either SDK:

    * OpenAI / Azure — ``response.usage.prompt_tokens`` / ``.completion_tokens``
    * Anthropic — ``response.usage.input_tokens`` / ``.output_tokens``

    Returns ``None`` when the response carries no readable usage. **That is an answer, not an
    error** — a stubbed client in a test, or a provider that omits usage on a streamed response,
    is not a malfunction. It is counted separately rather than silently ignored, because "no call
    was made" and "a call was made and we could not read it" are different facts and a meter that
    conflates them cannot be trusted for the thing it exists to inform.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    def _int(*names: str) -> int | None:
        for name in names:
            value = getattr(usage, name, None)
            if value is None and isinstance(usage, dict):
                value = usage.get(name)
            if isinstance(value, (int, float)):
                return int(value)
        return None

    prompt = _int("prompt_tokens", "input_tokens")
    completion = _int("completion_tokens", "output_tokens")
    if prompt is None and completion is None:
        return None
    return (prompt or 0, completion or 0)


def observe_llm_usage(*, provider: str, model: str, response: Any) -> None:
    """Record the token usage of one completed LLM call. Never raises.

    ★ Metering must not be able to fail a call that already succeeded — the tokens are spent
    either way, and turning an accounting problem into a user-visible error would be a strictly
    worse outcome than a gap in a graph.

    ★ But an unreadable response is **counted**, not swallowed. `CLAUDE.md`'s soak-harness rule:
    an instrument that cannot distinguish *"the mechanism did not fire"* from *"I failed to
    observe it"* produces exactly the ambiguous result a meter must not produce. So a response
    whose usage cannot be read increments its own counter, and a flat token count next to a
    rising unreadable count is a legible, actionable state rather than a mystery.
    """
    if not _METRICS_AVAILABLE:
        return

    model_label = str(model or "unknown")
    try:
        usage = extract_token_usage(response)
        if usage is None:
            llm_usage_unreadable_total.labels(provider=provider, model=model_label).inc()
            return

        prompt, completion = usage
        llm_tokens_total.labels(provider=provider, model=model_label, kind="prompt").inc(prompt)
        llm_tokens_total.labels(
            provider=provider, model=model_label, kind="completion"
        ).inc(completion)
    except Exception as exc:  # noqa: BLE001 — see the docstring: never fail a completed call
        logger.debug("[token_meter] usage not recorded for %s/%s: %s", provider, model_label, exc)
        try:
            llm_usage_unreadable_total.labels(provider=provider, model=model_label).inc()
        except Exception:
            pass
