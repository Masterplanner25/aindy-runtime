---
title: "EFFECT-OUTCOME-UNKNOWN-1 — Ambiguous Effect Outcomes — Design"
api_version: "1.0"
last_verified: "2026-10-03"
status: current
owner: "platform-team"
---

# EFFECT-OUTCOME-UNKNOWN-1: an effect whose outcome was not observed

**Status: PHASE 1 BUILT 2026-10-03.** DEC-085..090 were accepted by the owner the same day. Phase 1
(never re-run: DEC-085, 086, 087, 090, and the tool seam able to record `unknown`) is in
`effect_ledger._resolve_existing_row`, `syscall_outcome.EffectOutcomeUnknown` / `HeldOutcome`, both
seams and the TTL job. **Phase 2 BUILT the same day:** `AT_MOST_ONCE` in both registries, always
strict-locked, refusing wherever `EXACTLY_ONCE` degrades (the ledger now says `DEGRADED`), and the
shortfall counter; soaked on Postgres, 8-way contention runs the handler once with 0 degrades.
**Phase 3 BUILT the same day (the emitters):** an effectful isolated tool's worker lost mid-call (budget or cancel kill, crash, unreadable reply) is `unknown` (DEC-088), a spawn failure stays `transient`; an MCP call that times out after it was sent is `unknown` for a server declared with a `guarantee`; `outbound_http.classify_transport_exception`, and `outbound_request` retries a possibly-processed request (read failure after send, 5xx) only when idempotent, else raises `EffectOutcomeUnknown`. Phase 4 (reconciliation) remains. Read `TECH_DEBT.md` § `EFFECT-OUTCOME-UNKNOWN-1` for the original finding and
the nodus-side reasoning it cites (`03-outcome-ambiguity.md`, §5.3 phase ladder, §7).

---

## 1. Where it stands, measured at source

**The vocabulary half is done.**

- `EffectRecord.status` accepts `unknown` and `partial` (#560). `complete_effect_record` validates
  the terminal set and refuses `pending` (`kernel/effect_ledger.py`).
- A syscall handler can claim `unknown` through the reserved `_outcome` key
  (`kernel/syscall_outcome.py::unknown()`). The dispatcher resolves it and writes the ledger status
  (`syscall_dispatcher.py`, `resolve_outcome`).
- `partial` has a real emitter: FLOW-PARALLEL-1's lenient join (#640).

**`unknown` has no emitter, and the behaviour around it is unsafe.** Six findings, which are the
reason for every decision below:

1. **A replay RE-RUNS an `unknown` (or `partial`) effect.** `_resolve_existing_row`
   (`effect_ledger.py`) replays `success` and degrades on a live `pending`. Every other status falls
   through to *"prior failure: reclaim the slot"* and the handler runs again. The first time
   anything correctly records `unknown`, the next retry performs the effect a second time, which is
   the duplicate the status exists to prevent. `partial` re-applies the units that already landed.
2. **The tool seam cannot record `unknown`.** `_finalize_tool_effect` (`agents/tool_registry.py`)
   writes `success` or `failed` only.
3. **The clearest in-tree ambiguity is classified retryable.** When an isolated tool's worker
   exceeds its budget the parent kills it mid-call and returns `failure_class: "transient"`
   (`tool_registry.py`, the `TimeoutExpired` branch). `retry_policy.py` lists *"worker crash"* under
   `transient` by design. So an `EXACTLY_ONCE` isolated tool that timed out after acting is
   retried, its slot is reclaimed, and the effect can happen twice. A cancel-kill mid-call
   (`failure_class: "cancelled"`) is equally ambiguous about the world, though never retried.
4. **Outbound HTTP destroys the distinction the library provides.** `outbound_request`
   (`platform_layer/outbound_http.py`) catches `httpx.HTTPError` (the base class), so
   `ConnectError` (knowably not dispatched) and `ReadTimeout` (the one true ambiguity) are handled
   alike. It retries both, plus 500/502/503/504, `max_retries=2`, **with no method guard**. It has
   no in-tree callers, so this is latent here and live for any consumer that adopted it.
   `authorized_external_call` (email, connectors) re-raises whatever its operation raised,
   unclassified.
5. **An MCP client call that times out** (`platform_layer/mcp_client.py`, `_run_sync(timeout=…)`)
   is a true unknown, and is a plain failure today.
6. **There is no `AT_MOST_ONCE`.** `_VALID_EXECUTION_GUARANTEES` (`kernel/syscall_registry.py`) is
   `{"AT_LEAST_ONCE", "EXACTLY_ONCE"}`. A syscall or tool whose counterparty is not
   transactional cannot honestly declare what it is.

**What `unknown` means, unchanged from #560:** a claim about the WORLD, not the runtime's
confidence. The narrow case is a read timeout after a full request write, or a worker killed after
it may have acted. An exception nobody classified is `failed`. Routing it here would turn a knowable
failure into a permanent ambiguity that a human has to resolve.

---

## 2. Decisions (provisional)

### DEC-085: a replay never re-runs an `unknown` effect; it returns the recorded outcome

`_resolve_existing_row` gains a branch: for an `unknown` row it returns the recorded outcome, and the
caller gets the `unknown` envelope again, with the recorded detail and `reconcile_required: true`.
It is counted as `_count_gate("unknown_held")`. The handler does not run.

*Why return rather than fail:* the caller learns the true state ("still unresolved"), and a
retry loop sees the same non-retryable class (DEC-090) instead of a new error that might be
classified differently.

### DEC-086: `partial` gets the same rule

A replay of a `partial` row returns the recorded per-unit outcome, counted as
`_count_gate("partial_held")`. **This changes current behaviour**, because a retried lenient fan-out
re-ran today. It re-ran *every* branch, including the ones that landed. Recording that is the point
of `partial`, so re-running it was never correct. Anyone who wants the failed units re-done re-issues
them as a new effect, or reconciles (§3, phase 4).

### DEC-087: an unresolved `unknown` row is excluded from TTL cleanup

The model's comment says terminal rows are reaped like any finished effect. For `unknown` that
reaping is itself a duplicate risk: once the row is gone, a later retry finds no slot and runs. So
the TTL job also excludes `status == "unknown"` until it is reconciled (phase 4 moves it to `success`
or `failed`, after which normal retention applies). The table stays bounded because an `unknown`
is rare by construction, and it is visible: a gauge, `aindy_effect_unknown_unresolved`, and a
WARNING in the scan line when nonzero. `pending`'s hourly stale warning does not apply, so an
honest ambiguity never reads as a stuck handler.

### DEC-088: an isolated tool killed mid-call records `unknown` only if it declares an effect guarantee

When the isolated worker is killed by its budget or by a cancel, and the tool declares
`EXACTLY_ONCE` or `AT_MOST_ONCE`, the effect is recorded `unknown` and the step gets
`failure_class: "unknown"`. A tool that declares no guarantee (`AT_LEAST_ONCE`) keeps today's
`transient`/`cancelled` behaviour, because its author has said a repeat is acceptable.

### DEC-089: `AT_MOST_ONCE` joins the guarantee vocabulary now, and it never degrades

`_VALID_EXECUTION_GUARANTEES` becomes `{"AT_LEAST_ONCE", "EXACTLY_ONCE", "AT_MOST_ONCE"}`, on both
`register_syscall` and `register_tool`. It engages the same effect gate. It differs from
`EXACTLY_ONCE` in two ways, and those differences are what make it more than a label:

- **It never degrades under contention.** `EXACTLY_ONCE` deliberately degrades to at-least-once when
  it loses the insert race to a live `pending` row (IDEM-11, documented in
  `IDEMPOTENCY_CONTRACT.md`). An at-most-once effect cannot. It always takes the strict advisory
  lock (FR-27 / IDEM-13), whatever `AINDY_SYSCALL_IDEMPOTENCY_STRICT` / `AINDY_TOOL_IDEMPOTENCY_STRICT`
  say. On lock timeout it **refuses**, with `failure_class: "transient"`: knowably not dispatched,
  so safe to retry.
- **`unknown` is a legitimate terminal outcome for it**, not a shortfall. Under `EXACTLY_ONCE` an
  `unknown` is counted as a contract shortfall (`aindy_effect_contract_shortfall_total{guarantee}`),
  because that label promised completion.

The owner asked for this now rather than after the rest, so it is phase 2 below, not last.

### DEC-090: `failure_class: "unknown"` exists, and nothing retries it

`FAILURE_CLASSES` gains `"unknown"`, which is not in `RETRYABLE_CLASSES`. A step or call that comes
back `unknown` stops its retry loop on both agent backends, on the flow engine's `decide_retry`, and
on the compiled `nodus_vm` retry loop. `is_retryable_error` (whole-dict, RETRY-CLASSIFY-1) reads
it from the envelope.

---

## 3. Phases

| Phase | Builds | Gate to move on |
|---|---|---|
| **1. Never re-run ambiguity** | DEC-085, 086, 087, 090. The tool seam records `unknown`: an `EffectOutcomeUnknown` exception, mapped to the ledger by both seams alongside the `_outcome` key. | Tests: a recorded `unknown` / `partial` is not re-run on replay at either seam (the handler call count stays 1); TTL keeps unresolved `unknown`; `failure_class: "unknown"` stops every retry loop. |
| **2. `AT_MOST_ONCE`** | DEC-089: vocabulary, strict lock always, refusal on lock timeout, shortfall counter. `IDEMPOTENCY_CONTRACT.md` gains the at-most-once row. | Soak harness: N concurrent identical `AT_MOST_ONCE` calls ⇒ the handler runs **at most once**, every other caller replays or is refused, **0 degraded** (contrast IDEM-11's 2-of-8). |
| **3. Emitters** | DEC-088 (isolated-tool kill); the MCP call timeout; a shared transport classifier (`ConnectError`/`ConnectTimeout` → not dispatched; `ReadTimeout`/`RemoteProtocolError` after send → `unknown`); `outbound_request` narrowed to it, retrying ambiguous errors only on idempotent methods and never retrying a 5xx on a non-idempotent one. | Each emitter tested with its real exception type, plus the negative case (a connect error is NOT `unknown`). |
| **4. Reconciliation** | An audited admin route, `POST /platform/effects/{action_id}/resolve {"status": "success"\|"failed", "note"}`: `success` caches the outcome for replay, `failed` frees the slot for a retry. A list route for unresolved `unknown` rows. Optional: an agent run whose step comes back `unknown` parks for a decision, reusing the authority gate's wait (`skip` / `abort` / `resolved`). | Route tests call the routes; resolve is refused for a non-`unknown` row; the audit event carries who and why. |

**Order rationale.** Phase 1 must land first. An emitter added before it turns a recorded ambiguity
into a guaranteed duplicate, which is worse than today's mislabelling. Phase 2 follows at the
owner's request. Phase 3 is small once the status is safe to emit, and without phase 4 its rows are
safe (held, not reaped, counted) but accumulate.

---

## 4. Not in scope

- **Automatic reconciliation by querying the counterparty.** A per-syscall `reconcile=` callback
  that looks up a planted trace is the nodus note's §7.4. It needs `EFFECT-PRECONDITION-1`'s rule
  (record the external system's own version token, never reimplement), and the nonce must be minted
  ONCE before the first dispatch and live inside the payload, because `compute_action_id` hashes
  the request. Later, if ever.
- **A browser driver.** The entry's own ordering puts it last, and it is a library.
- **Guest-side effects** (`nodus` `http_*` builtins inside a script). They are not ledgered.
- **LLM calls.** They cost money but are not effects in this sense.

## 5. Cross-links

`EFFECT-PARTIAL-1` (same column: DEC-086), `IDEM-11` / `IDEM-13` / FR-27 (the strict lock DEC-089
always takes), `CANCEL-REACH-1` (the cancel-kill DEC-088 classifies), `RETRY-CLASSIFY-1` (the
whole-dict classifier DEC-090 extends), `AUTHORITY-NEGOTIATION-1` (the wait pattern phase 4 may
reuse), `PERF-BASELINE-1` (`test_work_budget.py` measures the effect ledger at 4 queries per effect;
DEC-085's branch must not add one to the success path).
