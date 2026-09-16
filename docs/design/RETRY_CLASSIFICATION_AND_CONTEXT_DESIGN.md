---
title: "Retry Classification and Carried Context — Design"
api_version: "1.0"
last_verified: "2026-09-16"
status: current
owner: "platform-team"
---

# Retry classification and carried context — design

**`RETRY-CLASSIFY-1` + `RETRY-CONTEXT-1`. DESIGN ONLY — nothing shipped. Proposal under
`AGENT_WORKING_RULES.md` §8 (runtime behaviour change: what a retry decides, and what it carries).**
The two entries are designed together because both entries say so: a classified failure *is*
the payload a carried failure should contain, and designing either alone means designing the
same dict twice. §2 corrects the census both entries inherit; §3 is the evidence that moved
this from "fragile in principle" to "wrong on our own strings"; §8 is what not to build.

---

## 1. The two findings, in one sentence each

- **Classify:** whether a failed attempt is retried is decided by `is_retryable_error`, which
  lowercases the error *string* and matches nine substrings (`retry_policy.py:95`). Nothing
  records that a classification fired.
- **Carry:** a retry re-attempts the identical call. The prior failure reaches the next attempt
  through no channel at all — not the flow node, not the tool, not the generated plan.

Both are silent: the outcome of a mis-classification is indistinguishable from a hard failure,
and an uninformed retry is indistinguishable from an informed one that happened to fail.

---

## 2. ★ The census, re-derived at HEAD — the entries count five sites and there are four

`RETRY-CLASSIFY-1` lists five call sites. Measured 2026-09-16 (`grep -rn is_retryable_error
AINDY`):

| Site | Role | Live? |
|---|---|---|
| `runtime/flow_engine/runner_steps.py:416` | flow-node `RETRY` gate | **yes** |
| `runtime/nodus_adapter.py:536` | agent tool-step retry loop | **yes** |
| `runtime/nodus_worker.py:509` → `:565` | registered as Nodus host function `is_retryable_error` | **yes** — guest scripts call it |
| `runtime/agent_plan_compiler.py:145` | **emitted into generated plan source** as `is_retryable_error(__result_N["error"])` | **yes** — runs inside the guest via the site above |
| `core/retry_policy.py:224`/`:246` `execute_with_retry` / `_execute_with_retry` | "the default classifier for `execute_with_retry`" | **★ NO — zero callers.** The only other `execute_with_retry` in the tree is a local closure in `scheduler_service.py:827` that shares the name and nothing else |

So the runtime owns **three retry loops** (flow node, tool step, compiled plan), all written
inline, and **none goes through the helper the module presents as the retry primitive**. This
matters for the *carry* half more than the *classify* half: `RETRY-CONTEXT-1` argues "the
runtime owns the loop, so the runtime must carry the failure" — true, but the loop it names
(`execute_with_retry(fn)`) is not the loop anything runs. The channel has to be threaded into
the three real loops, and a fix applied only to `execute_with_retry` would be `ROUTE-AST-UNWIRED-1`
again: correct, covered, and unreachable.

**Recommendation:** delete `execute_with_retry` / `_execute_with_retry` in the implementing PR
rather than teach them the new payload (the DEC-023 rule — an unrunnable twin beside the real
path is what lets a false coverage claim be made). Its docstring already says the system does
not use it; that sentence is true of the helper and false of the classifier, which the entry
also notes.

---

## 3. ★★ Measured: the classifier is wrong on the runtime's own refusals, not just on contrived text

The entry's examples (`"took 404ms"`, `"invalidated cache"`) are plausible but hypothetical.
Running `is_retryable_error` over the literal strings `execute_tool` itself returns
(`tool_registry.py`, 2026-09-16):

| `execute_tool` refusal | Line | Classified | Right? |
|---|---|---|---|
| `run … was cancelled; tool … not executed` | `:575`, `:1033` | **RETRY** | **no** — a cancelled run is re-attempted up to 3× (`nodus_adapter` loop), each refused pre-spawn again |
| `capability token is required for agent run tool execution` | `:790` | **RETRY** | **no** — structural; cannot change between attempts |
| `capability enforcement failed` | `:914` | **RETRY** | **no** — the check *crashed*; retrying re-runs the crash |
| `Tool '…' not found in registry` | `:784` | STOP | yes — by accident of the phrase |
| `Permission denied: requires capability …` | `:829` | STOP | yes |
| `… isolated worker exceeded 30s` / `could not be started` / `failed (exit 1)` | `:583`/`:593`/`:614` | RETRY | yes |

**Direction check, because it changes severity:** all three misclassifications are *false
negatives* — the runtime **retries** something that cannot succeed. The entry filed the
opposite direction (false positives: giving up on a retryable error) as the safe one, and it
is. The measured direction is also safe for effects — the cancel check refuses pre-spawn and
the token check refuses pre-dispatch, so nothing runs twice — but it costs **wall time and
attempts** on a run that is already cancelled, and it costs it *silently*. P2 stands; the
evidence is now measured rather than argued.

**★ And note what `:575` already does:** it returns `"cancelled": True` *beside* the error
string. The runtime already has one typed flag outside the message, on the one refusal where
someone needed to branch on it. The design below generalises that precedent; it does not
introduce a new idea.

---

## 4. The payload — one dict, both entries

```python
# AINDY/core/retry_policy.py (proposed)
FAILURE_CLASSES = frozenset({
    "transient",     # retry may succeed: timeout, worker crash, 5xx, connection reset
    "cancelled",     # run cancelled — never retry, never re-plan
    "permission",    # capability / scope / policy refusal
    "not_found",     # tool, route, resource absent
    "invalid",       # caller-side: bad args, schema violation
    "fatal",         # anything else the raising site knows is terminal
})

@dataclass(frozen=True)
class FailureRecord:
    error: str                      # the message, unchanged — what callers read today
    failure_class: str              # one of FAILURE_CLASSES
    classified_by: str              # "site" | "substring" | "default"
    attempt: int                    # 1-based attempt that produced it
    site: str                       # "flow_node" | "tool_step" | "compiled_plan"
```

**Rules that make it small:**

- **`failure_class` is set at the raising site when the site knows.** `execute_tool`'s
  fourteen `"success": False` returns each know their class; they gain a `failure_class` key
  beside `error` (the `cancelled: True` precedent, made uniform). A syscall error envelope
  can carry it the same way — one key, resolved at `_error_envelope`, the single funnel that
  already counts and logs every error.
- **The substring table survives as the fallback for un-classed strings only**, and when it
  fires the record says `classified_by="substring"`. A `"default"` classification means
  neither the site nor the table decided — treated as `transient` (today's behaviour for a
  no-match string), so **the default flip changes nothing** for any string the table did not
  already stop.
- **The runtime carries; it does not interpret.** `failure_class` is a *class*, not a
  diagnosis. No runtime code branches on `error`'s content beyond the fallback table, and
  nothing new does.

**Why one string field, not a typed exception hierarchy.** The three loops consume *dict
results* (`tool_result`, node `result`, `__result_N` in the guest), not exceptions — the
guest boundary swallows host exceptions into `ok: False` (`GUEST-BUILTINS-DEAD-1`), so an
exception type cannot cross it and a string can. The class is a string for the same reason the
outcome vocabulary in `syscall_outcome.py` is.

---

## 5. Classify — phase 1

`is_retryable_error(error)` becomes a thin wrapper over a new
`classify_failure(result_or_error, *, site) -> FailureRecord`, so all four live sites keep
working unchanged while the record becomes available:

| Site | Change |
|---|---|
| `runner_steps.py:416` | read `result.get("failure_class")` first; fall back to the table; **record** the classification on the `flow.node.failed` event payload and a counter |
| `nodus_adapter.py:536` | same, on `tool_result`; record on `agent.step.failed` and the `AgentStep` row (`error_message` stays; add nothing to the schema — the record goes in the event payload) |
| `nodus_worker.py:509` host function | accept a dict *or* a string (today: string only); a guest passing `__result_N` gets site classification for free |
| `agent_plan_compiler.py:145` | emit `is_retryable_error(__result_N)` — the whole result, not `["error"]` — so a compiled plan stops on `cancelled` / `permission` without substring matching a model-shaped message |

**The operator signal — the rule from the green-check catalogue variant 10:** a Prometheus
counter, `aindy_retry_classifications_total{site, failure_class, classified_by, decision}`,
incremented at the one place each loop decides. `classified_by="substring"` is the number to
watch: it is the residue the table still owns, and the phase-1 exit criterion is that it goes
to zero on the runtime's own strings (§3's table is the test).

**Guard, derived not hand-written (variant 12):** a test walks `tool_registry.py`'s AST for
every `return {... "success": False ...}` and fails if any lacks `failure_class`. The census is
the source, and the test asserts it is non-empty.

**No schema change.** `AgentStep.error_message` and `SystemEvent.payload` already hold what
is needed; a new column on `agent_steps` would cost a contract bump for a field the event
payload carries better.

---

## 6. Carry — phase 2

**The channel is a scope, never an argument.** This is the constraint the entry did not
state and it is the whole design:

> **The carried failure must not enter the tool's `args`, the node's `state`, or the plan's
> `input_payload`.** `EffectRecord` keys on `sha256({action_type, input, scope})`
> (`IDEMPOTENCY_CONTRACT.md`) — a failure folded into `input` makes every retry a *different
> effect*, and the `EXACTLY_ONCE` gate that makes retries safe stops deduplicating them. The
> entry's guard rail 2 ("not part of an idempotency key") is not a policy choice; it falls out
> of where the key is computed.

So the channel is the shape the runtime already uses for out-of-band per-execution facts:

```python
# AINDY/core/retry_context.py (proposed) — mirrors token_meter.llm_attribution_scope
_RETRY_CONTEXT: ContextVar[tuple[FailureRecord, ...]] = ContextVar("aindy_retry_context", default=())

@contextmanager
def retry_context_scope(prior: tuple[FailureRecord, ...]): ...
def prior_failures() -> tuple[FailureRecord, ...]: ...
```

| Loop | Where the scope is entered | Who can read it |
|---|---|---|
| flow node (`runner_steps.py`) | around the node call on attempt ≥ 2; **also** `context["last_failure"]` beside the existing `context["attempts"]` — `context`, not `state`, so it is in no `FlowHistory` patch and no graph signature | the node callable |
| tool step (`nodus_adapter.py`) | around `execute_tool(...)` on attempt ≥ 2 | the tool, via `tool_session` — **in-process tools only**; the isolated worker (`tool_worker.py`) gets it in the worker request envelope as a *read-only* field, never as an arg |
| compiled plan | nothing to enter — `__result_N` is already in guest scope; a `prior_failures()` host function returns the same record for symmetry | the guest script |

**Bound: K = 1 by default, `AINDY_RETRY_CONTEXT_DEPTH` up to 3, each record's `error`
truncated to 2 KiB.** Decided here, per guard rail 1, so it is not decided after a prompt
overflows. The tuple is the *last K*, oldest first.

**Test that must exist before the flag flips (variant 13 — a fixture that hides the
interaction):** a real `execute_tool` retry, through `nodus_adapter`, with a spy tool that
asserts `prior_failures()` is empty on attempt 1 and holds attempt 1's record on attempt 2 —
*and* that the two attempts produced **one** `EffectRecord` key. A unit test of the ContextVar
alone cannot see the second half, and the second half is the constraint.

---

## 7. What consumes the carried failure — honestly, nothing yet

The compiled-plan loop cannot make a better-informed call: its `tool_args` are fixed at plan
time, and re-planning mid-segment is `RTR-3` territory. A flow node or an in-process tool
*could* read `prior_failures()` today, and none does. Devika's "repair handler before DLQ" is
the natural consumer and it is **app content** by this design's own boundary (the runtime
carries, it does not interpret) — the runtime's job is to make the failure *reachable*, not to
act on it.

So phase 2 ships **default-off** (`AINDY_RETRY_CONTEXT`, like every other channel), with the
spy test as the only consumer, and the entry stays open until a first-party consumer reads
it — `SUBSTRATE-WITNESS-1`'s rule. Phase 1 has no such dependency: recording classifications
is an operator signal the moment it exists, and §3 shows it will not read zero.

---

## 8. What not to build

- **Not a typed exception hierarchy** (§4) — it cannot cross the guest boundary.
- **Not a per-tool `retryable_errors=[...]` declaration on `register_tool`.** Tempting because
  `on_denial="wait"` lives there, but a tool declaring which of its *messages* are retryable
  is the substring table moved into the manifest. The site that raises knows the class; make
  it say so.
- **Not a repair stage, not a re-planner** (§7). Interpreting the carried error is the line
  the entry drew; the first consumer decides what a repair is, and it is not the runtime.
- **Not a new column anywhere.** The event payload and the counter are the record.
- **Not a change to `RetryPolicy` or the backoff maths** — `RETRY_POLICY.md` stays true.

---

## 9. Decisions this design asks for (to be recorded as `DEC-NNN` in the implementing PR)

1. `execute_with_retry` / `_execute_with_retry` are **deleted**, not taught (§2).
2. The failure class is a **string on the result dict**, set at the raising site; the
   substring table is the fallback and says so when it fires (§4).
3. The carried failure rides a **scope, never an argument** — because of the idempotency key,
   not as a preference (§6).
4. Depth K = 1 default, max 3, 2 KiB per record (§6).
5. Phase 2 ships default-off with no consumer and does not close `RETRY-CONTEXT-1` (§7).

## 10. Phasing

| Phase | Ships | Closes |
|---|---|---|
| 1 | `FailureRecord`, `classify_failure`, `failure_class` on every `execute_tool` refusal and the dispatcher envelope, the counter, the AST census test, `execute_with_retry` deleted | `RETRY-CLASSIFY-1` — when `classified_by="substring"` reads zero on §3's strings |
| 2 | `retry_context_scope`, the three loop hooks, the `prior_failures` host function, the spy + single-`EffectRecord` test, `AINDY_RETRY_CONTEXT` default-off | nothing — `RETRY-CONTEXT-1` closes on a consumer |
