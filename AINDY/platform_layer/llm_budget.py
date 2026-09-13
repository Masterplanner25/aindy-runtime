"""The LLM token governor — reserve → call → reconcile (COST-GOVERNOR-1 phase 4).

The runtime enforced a wall-clock, a memory and a syscall ceiling on execution units whose
dominant cost is tokens, and did not measure tokens at all. Phase 0 metered them; phase 2
proved the meter moves on a real deployment (one planner call, 2 244 tokens); phase 3 gave
every call a subject — the attributed run, else the bound execution unit, and the tenant — and
accrued the actual onto `ResourceManager`. This is the half that refuses.

★ WHY A RESERVATION AND NOT A CHECK
------------------------------------
A read-then-compare admits N concurrent callers who all read the same number. A reservation
pre-fills the counter it is checked against (`ResourceManager.reserve_tokens`: INCRBY-then-
compare on Redis, one critical section in memory), so the (N+1)th caller sees the N already
there. LiteLLM's `budget_reservation.py` is the reference shape and its own comment records why
the re-check must not use `>=`: the reservation already admitted at the boundary.

★ THE ESTIMATE NEVER BECOMES THE RECORD
----------------------------------------
Only an estimate can refuse a call before it costs money, and estimates are wrong. So: reserve
an estimate; the meter records the ACTUAL inside the call (`observe_llm_usage`, additive); on
exit the estimate is released. Net effect on the counters = actual. A call that raises releases
its estimate and records nothing — the call never happened. The estimate is `max_tokens` (the
completion cap the caller asked for, else `AINDY_LLM_BUDGET_DEFAULT_RESERVE`) plus a
four-chars-per-token guess at the prompt. It is a guess; it is documented as one.

★ WHAT IS BUDGETED, AND THE ONE THING THE RUN BUDGET CANNOT DO
---------------------------------------------------------------
Two ceilings, both OPT-IN (default 0 = unlimited): `AINDY_QUOTA_MAX_TOKENS` per execution
(agent run or bound unit) and `AINDY_QUOTA_MAX_TENANT_TOKENS` per tenant window. Phase 3's
finding decides which one matters where: **planning runs before the AgentRun row exists**, so
a planner call has a tenant and no run — the per-run ceiling cannot catch a runaway planner;
the tenant window is the backstop for that. An unattributed call (no tenant, no unit) cannot be
budgeted and is admitted — `INITIATOR-IDENTITY-1`: an asserted identity may constrain, never
widen — and the meter already counts it as `attributed="none"`.

★ FAIL-OPEN OR FAIL-CLOSED
---------------------------
The same answer `resource_manager` gives the other four dimensions (closed in prod, open in
dev/test — `syscall_dispatcher._quota_backend_failure_may_fail_open`), not a fifth policy. A
store failure in dev/test admits the call and counts `degraded`; in prod it refuses, because a
governor that is silently not governing is the ROUTE-AST-UNWIRED-1 shape.

★ WHERE IT SITS
----------------
`CircuitBreakerLLMClient._call_with_breaker`, OUTSIDE the breaker: a refusal is not a provider
failure and must never count toward opening the circuit. Every seam call — `chat()` and
`call_method()` — passes through there exactly once, so a call is reserved exactly once.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger(__name__)

RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"

# Completion tokens reserved when the caller did not pass `max_tokens`. Providers' own defaults
# sit around 1–4k; an estimate that is too LOW admits a call that lands over budget by its
# overshoot, too HIGH refuses a call that would have fit. Reconciliation corrects the record
# either way; only admission is decided on it.
DEFAULT_RESERVE_TOKENS = int(os.getenv("AINDY_LLM_BUDGET_DEFAULT_RESERVE", "2048") or 2048)
_CHARS_PER_TOKEN = 4


def _count(scope: str, outcome: str) -> None:
    try:
        from AINDY.platform_layer.metrics import llm_budget_outcomes_total

        llm_budget_outcomes_total.labels(scope=scope, outcome=outcome).inc()
    except Exception:  # pragma: no cover - metrics optional
        logger.debug("[llm_budget] outcome metric skipped", exc_info=True)


def estimate_reservation(args: tuple, kwargs: dict) -> int:
    """Tokens to reserve for a call: the completion cap asked for, plus a prompt guess."""
    completion = kwargs.get("max_tokens")
    try:
        completion = int(completion) if completion else DEFAULT_RESERVE_TOKENS
    except (TypeError, ValueError):
        completion = DEFAULT_RESERVE_TOKENS
    try:
        prompt_material = {k: v for k, v in kwargs.items() if k != "max_tokens"}
        text = json.dumps([args, prompt_material], default=str)
        prompt = len(text) // _CHARS_PER_TOKEN
    except Exception:  # noqa: BLE001 - an unserialisable payload is still a call to admit
        prompt = 0
    return max(1, completion + prompt)


def _caps() -> tuple[int, int]:
    """Read the ceilings per call — the FR-10 rule: never cache an env read at import."""
    from AINDY.kernel import resource_manager as rm_mod

    return int(rm_mod.MAX_TOKENS_PER_EXECUTION or 0), int(rm_mod.MAX_TOKENS_PER_TENANT_WINDOW or 0)


def _may_fail_open() -> bool:
    from AINDY.kernel.syscall_dispatcher import _quota_backend_failure_may_fail_open

    return _quota_backend_failure_may_fail_open()


@contextmanager
def llm_budget_reservation(*, provider: str, args: tuple = (), kwargs: dict | None = None) -> Iterator[None]:
    """Reserve the call's estimated tokens against the caller's budgets; release on exit.

    Raises ``LLMBudgetExceededError`` (an ``LLMCallError``) BEFORE the call when either
    ceiling would be exceeded. Yields immediately, reserving nothing, when both ceilings are
    unlimited or the call has no subject to charge.
    """
    from AINDY.platform_layer.llm_client import LLMBudgetExceededError

    kwargs = kwargs or {}
    exec_cap, tenant_cap = _caps()
    if exec_cap <= 0 and tenant_cap <= 0:
        yield
        return

    from AINDY.kernel.resource_manager import get_resource_manager
    from AINDY.platform_layer.token_meter import resolve_llm_subject

    rm = get_resource_manager()
    try:
        tenant_id, unit_key, _attributed = resolve_llm_subject()
    except Exception:  # noqa: BLE001 - see fail-open note in the module docstring
        if _may_fail_open():
            _count("execution", "degraded")
            yield
            return
        raise

    if not tenant_id and not unit_key:
        # Nothing to charge. Admitted and, by the meter, counted as attributed="none".
        yield
        return

    reserve = estimate_reservation(args, kwargs)
    held_unit = held_tenant = False
    try:
        try:
            if unit_key and exec_cap > 0:
                if not rm.reserve_tokens(unit_key, reserve, exec_cap, tenant_id=tenant_id):
                    used = int(rm.get_usage(unit_key).get("tokens") or 0)
                    _count("execution", "refused")
                    raise LLMBudgetExceededError(
                        f"{RESOURCE_LIMIT_EXCEEDED}: execution {unit_key!r} llm token budget "
                        f"({used} used + {reserve} reserved > {exec_cap})",
                        scope="execution", subject=str(unit_key), used=used, reserved=reserve, cap=exec_cap,
                    )
                held_unit = True
                _count("execution", "reserved")
            if tenant_id and tenant_cap > 0:
                if not rm.reserve_tenant_tokens(tenant_id, reserve, tenant_cap):
                    used = int(rm.get_tenant_tokens(tenant_id) or 0)
                    _count("tenant", "refused")
                    raise LLMBudgetExceededError(
                        f"{RESOURCE_LIMIT_EXCEEDED}: tenant {tenant_id!r} llm token budget "
                        f"({used} used + {reserve} reserved > {tenant_cap} in window)",
                        scope="tenant", subject=str(tenant_id), used=used, reserved=reserve, cap=tenant_cap,
                    )
                held_tenant = True
                _count("tenant", "reserved")
        except LLMBudgetExceededError as exc:
            logger.warning("[llm_budget] %s refused: %s", provider, exc)
            raise
        except Exception as exc:  # noqa: BLE001 - the budget STORE failed, not the budget
            if _may_fail_open():
                logger.warning("[llm_budget] store failure, admitting (dev/test fail-open): %s", exc)
                _count("tenant" if held_unit else "execution", "degraded")
            else:
                raise LLMBudgetExceededError(
                    f"{RESOURCE_LIMIT_EXCEEDED}: llm budget store unavailable ({exc}); refusing (fail-closed)",
                    scope="execution", subject=str(unit_key or tenant_id), used=0, reserved=reserve, cap=exec_cap or tenant_cap,
                ) from exc
        yield
    finally:
        # Reconcile: the meter recorded the ACTUAL inside the call (or nothing, if it raised);
        # the estimate comes back out either way. Never fatal.
        try:
            if held_unit and unit_key:
                rm.release_tokens(unit_key, reserve)
            if held_tenant and tenant_id:
                rm.release_tenant_tokens(tenant_id, reserve)
        except Exception:  # noqa: BLE001
            logger.debug("[llm_budget] reservation release skipped", exc_info=True)
