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

★ PHASE 3 — THE COUNTER THE GOVERNOR WILL CHECK IS `ResourceManager` (2026-09-13)
-----------------------------------------------------------------------------------
That "cache, keyed and expiring" already existed: `kernel.resource_manager` holds per-unit
snapshots and per-tenant counters, Redis-shared or in-memory, both TTL'd — and since
`QUOTA-ACCRUAL-ORPHAN-1` (#632) its units are reaped and the pipeline binds one for every
request. So `observe_llm_usage` now ALSO accrues the tokens as a resource dimension there:

* on the **run** when an agent run's execution span declared itself (`llm_attribution_scope`,
  set by `execute_run`), else on the **bound execution unit** (`_EU_ID_CTX` — a request, a
  bound worker job), and
* on the **tenant**'s rolling window whenever the tenant is known.

Whose identity is available WHERE is the finding that shaped this: **planning — the expensive
call — runs in `create_run` before the `AgentRun` row exists**, so at planning time only the
tenant can be named; the run id exists only for execution-time calls under `execute_run`. A
per-run budget therefore cannot cover planning; a per-tenant one can. Both accrue here; neither
is enforced — the ceiling is phase 4, gated on evidence the meter moves in a real deployment.

Unattributed calls are allowed and COUNTED (`aindy_llm_calls_total{attributed="none"}`): the
`INITIATOR-IDENTITY-1` rule — an asserted identity may constrain, never widen — so the safe
default is to let the call through and make the unattributed fraction visible.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

# The seam methods whose responses this meter reads — the RAW completion paths, plus `chat()`
# which delegates to them. ★ The governor reserves against EXACTLY this set and nothing else:
# an embedding call goes through the same `call_method` seam, carries no completion usage, is
# never metered, and reserving for it (found live, 2026-09-13: two `reserved` per planner call)
# holds ~2k tokens against the budget for a call that costs none of them. One constant, shared,
# so the meter and the governor cannot disagree about what a "token-spending call" is; pinned
# against a derived AST census of the provider clients in `test_llm_budget.py`.
METERED_METHODS: frozenset[str] = frozenset({"chat", "messages_create", "chat_completion_response"})

# (tenant_id, run_id) the current execution span attributes its LLM calls to. Both optional.
_LLM_ATTRIBUTION: ContextVar[tuple[str | None, str | None]] = ContextVar(
    "aindy_llm_attribution", default=(None, None)
)


@contextmanager
def llm_attribution_scope(
    *, tenant_id: str | None = None, run_id: str | None = None
) -> Iterator[tuple[str | None, str | None]]:
    """Declare who the LLM calls made inside the block belong to.

    A field left ``None`` inherits the enclosing scope's value, so an inner span may add a run
    to an outer tenant without restating it. Token-holding: nothing outlives the block
    (`TEST-ORDER-CONTEXTVAR-1`).
    """
    outer_tenant, outer_run = _LLM_ATTRIBUTION.get()
    value = (
        str(tenant_id) if tenant_id else outer_tenant,
        str(run_id) if run_id else outer_run,
    )
    token = _LLM_ATTRIBUTION.set(value)
    try:
        yield value
    finally:
        _LLM_ATTRIBUTION.reset(token)


def current_llm_attribution() -> tuple[str | None, str | None]:
    """``(tenant_id, run_id)`` the current span attributes LLM calls to."""
    return _LLM_ATTRIBUTION.get()


def resolve_llm_subject() -> tuple[str | None, str | None, str]:
    """``(tenant_id, unit_key, attributed)`` for the current span — ONE resolution shared by the
    meter (which accrues onto it) and the governor (which reserves against it), so the two can
    never disagree about whose budget a call is charged to.

    ``unit_key`` is the attributed run, else the bound execution unit. ``tenant_id`` comes from
    the attribution scope, else from the bound unit's snapshot (a pipeline-bound request unit
    carries its tenant). ``attributed`` is the label value: ``run | unit | tenant | none``.
    """
    from AINDY.kernel.resource_manager import get_resource_manager
    from AINDY.kernel.syscall_dispatcher import _EU_ID_CTX

    tenant_id, run_id = _LLM_ATTRIBUTION.get()
    unit_id = _EU_ID_CTX.get() or None
    if not tenant_id and unit_id:
        tenant_id = get_resource_manager().get_usage(unit_id).get("tenant_id") or None
    attributed = "run" if run_id else "unit" if unit_id else "tenant" if tenant_id else "none"
    return tenant_id, run_id or unit_id, attributed


# ---------------------------------------------------------------------------
# FR-35 — the guest-path ledger: usage METERED where it is spent, RECORDED where it is owned
# ---------------------------------------------------------------------------
#
# On the `nodus_vm` backend a tool step's LLM call runs the provider client in the WORKER
# process: this module's counters land in a registry nobody scrapes, `resolve_llm_subject()`
# finds no scope there, and the run, the tenant window and `/metrics` all read zero for spend
# that happened. The worker reply already carries three DEFERRED collections the parent applies
# after the fact (`memory_writes`, `emitted_events`, `simulated_effects`); usage is a fourth.
#
# ★ Deferral REPLACES observation in the worker — append and nothing else — or a Redis-backed
# deployment counts every guest call twice (worker accrual + parent replay).
# Design: docs/design/FR35_GUEST_LLM_USAGE_DESIGN.md.

LLM_LEDGER_MAX_ENV = "AINDY_NODUS_LLM_LEDGER_MAX"
DEFAULT_LLM_LEDGER_MAX = 256


@dataclass(frozen=True)
class LlmUsageRecord:
    """One LLM call as the worker saw it — everything the parent needs to record and to trace."""

    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    started_at_ms: int = 0
    duration_ms: int = 0
    tool: Optional[str] = None
    outcome: str = "ok"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class LlmUsageLedger:
    """Bounded per-call ledger + an aggregate tail, so a guest loop cannot produce a reply the
    size of its call count. Accounting reads both halves; spans are replayed from records only."""

    def __init__(self, max_records: Optional[int] = None) -> None:
        self.max_records = max_records if max_records is not None else ledger_max_records()
        self.records: list[LlmUsageRecord] = []
        self.tail: dict[tuple[str, str], dict[str, int]] = {}
        self.dropped_unreadable = 0

    def append(self, record: LlmUsageRecord) -> None:
        if len(self.records) < self.max_records:
            self.records.append(record)
            return
        key = (record.provider, record.model)
        bucket = self.tail.setdefault(key, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
        bucket["calls"] += 1
        bucket["prompt_tokens"] += int(record.prompt_tokens)
        bucket["completion_tokens"] += int(record.completion_tokens)

    def as_dict(self) -> dict[str, Any]:
        return {
            "records": [r.as_dict() for r in self.records],
            "tail": [{"provider": p, "model": m, **v} for (p, m), v in self.tail.items()],
            "unreadable": self.dropped_unreadable,
        }

    def __len__(self) -> int:
        return len(self.records) + sum(v["calls"] for v in self.tail.values())


def ledger_max_records() -> int:
    raw = os.getenv(LLM_LEDGER_MAX_ENV)
    try:
        return max(1, int(raw)) if raw and raw.strip() else DEFAULT_LLM_LEDGER_MAX
    except (TypeError, ValueError):
        return DEFAULT_LLM_LEDGER_MAX


_DEFERRED_LLM_USAGE: ContextVar[Optional[LlmUsageLedger]] = ContextVar("aindy_llm_usage_deferral", default=None)
_CURRENT_TOOL: ContextVar[Optional[str]] = ContextVar("aindy_llm_usage_tool", default=None)


@contextmanager
def llm_usage_deferral_scope(max_records: Optional[int] = None) -> Iterator[LlmUsageLedger]:
    """Entered by the WORKER around a script: every `observe_llm_usage` inside appends to the
    yielded ledger and observes NOTHING locally. The ledger is what the reply carries."""
    ledger = LlmUsageLedger(max_records)
    token = _DEFERRED_LLM_USAGE.set(ledger)
    try:
        yield ledger
    finally:
        _DEFERRED_LLM_USAGE.reset(token)


def current_llm_usage_ledger() -> Optional[LlmUsageLedger]:
    return _DEFERRED_LLM_USAGE.get()


@contextmanager
def llm_usage_tool_scope(tool_name: Optional[str]) -> Iterator[None]:
    """Name the guest tool whose step is spending, so a deferred record (and its replayed span)
    can say which tool it was. Cheap, and only meaningful inside a deferral scope."""
    token = _CURRENT_TOOL.set(str(tool_name) if tool_name else None)
    try:
        yield
    finally:
        _CURRENT_TOOL.reset(token)


def record_llm_usage(
    *,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    tenant_id: Optional[str] = None,
    run_id: Optional[str] = None,
    unit_id: Optional[str] = None,
) -> None:
    """Record an ALREADY-KNOWN usage under an explicit subject — the accrual half of
    `observe_llm_usage`, factored out so the parent can replay a worker's ledger (FR-35).

    An explicit subject wins; a field left ``None`` falls back to the ambient one
    (`resolve_llm_subject`), so a `sys.v1.nodus.execute` script with no run still accrues to
    its unit and tenant. Never raises.
    """
    if not _METRICS_AVAILABLE:
        return
    model_label = str(model or "unknown")
    try:
        prompt = int(prompt_tokens or 0)
        completion = int(completion_tokens or 0)
        llm_tokens_total.labels(provider=provider, model=model_label, kind="prompt").inc(prompt)
        llm_tokens_total.labels(provider=provider, model=model_label, kind="completion").inc(completion)
        _attribute_usage(
            provider=provider, tokens=prompt + completion,
            tenant_id=tenant_id, run_id=run_id, unit_id=unit_id,
        )
    except Exception as exc:  # noqa: BLE001 — accounting must not fail anything
        logger.debug("[token_meter] usage not recorded for %s/%s: %s", provider, model_label, exc)

try:
    from AINDY.platform_layer.metrics import (
        llm_calls_total,
        llm_tokens_total,
        llm_usage_unreadable_total,
    )

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


def observe_llm_usage(
    *,
    provider: str,
    model: str,
    response: Any,
    started_at_ms: Optional[int] = None,
    duration_ms: Optional[int] = None,
) -> None:
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
    model_label = str(model or "unknown")
    # FR-35 — inside a worker's deferral scope the call is APPENDED to the ledger and observed
    # nowhere else: the parent records it under the run's real subject. Two accrual sites would
    # be the double-count this meter's design rejects.
    ledger = _DEFERRED_LLM_USAGE.get()
    if ledger is not None:
        try:
            usage = extract_token_usage(response)
            if usage is None:
                ledger.dropped_unreadable += 1
                return
            prompt, completion = usage
            ledger.append(LlmUsageRecord(
                provider=str(provider), model=model_label, prompt_tokens=int(prompt),
                completion_tokens=int(completion),
                started_at_ms=int(started_at_ms) if started_at_ms else int(time.time() * 1000),
                duration_ms=int(duration_ms or 0), tool=_CURRENT_TOOL.get(), outcome="ok",
            ))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[token_meter] deferred usage not recorded for %s/%s: %s", provider, model_label, exc)
        return
    if not _METRICS_AVAILABLE:
        return
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
        _attribute_usage(provider=provider, tokens=prompt + completion)
    except Exception as exc:  # noqa: BLE001 — see the docstring: never fail a completed call
        logger.debug("[token_meter] usage not recorded for %s/%s: %s", provider, model_label, exc)
        try:
            llm_usage_unreadable_total.labels(provider=provider, model=model_label).inc()
        except Exception:
            pass


def _attribute_usage(
    *,
    provider: str,
    tokens: int,
    tenant_id: Optional[str] = None,
    run_id: Optional[str] = None,
    unit_id: Optional[str] = None,
) -> None:
    """Accrue one call's tokens onto the identity the span declared (phase 3). Never raises.

    Precedence for the UNIT key: the attributed run, else the bound execution unit. The tenant
    window is accrued whenever a tenant is known, from the attribution scope first and, failing
    that, from the bound unit's snapshot (a pipeline-bound request unit carries its tenant).
    An EXPLICIT subject (FR-35: the parent replaying a worker's ledger from the reply's context)
    overrides the ambient one field by field.

    Reads the dispatcher's ContextVar lazily so this module keeps no kernel import at load.
    """
    try:
        from AINDY.kernel.resource_manager import get_resource_manager

        amb_tenant, amb_key, amb_attributed = resolve_llm_subject()
        if tenant_id or run_id or unit_id:
            key = str(run_id) if run_id else str(unit_id) if unit_id else amb_key
            tenant_id = str(tenant_id) if tenant_id else amb_tenant
            attributed = "run" if run_id else "unit" if unit_id else amb_attributed
        else:
            tenant_id, key, attributed = amb_tenant, amb_key, amb_attributed
        rm = get_resource_manager()
        if key:
            rm.record_tokens(key, tokens, tenant_id=tenant_id)
        if tenant_id:
            rm.record_tenant_tokens(tenant_id, tokens)
        llm_calls_total.labels(provider=provider, attributed=attributed).inc()
    except Exception as exc:  # noqa: BLE001 — accounting must not fail a completed call
        logger.debug("[token_meter] attribution not recorded for %s: %s", provider, exc)
