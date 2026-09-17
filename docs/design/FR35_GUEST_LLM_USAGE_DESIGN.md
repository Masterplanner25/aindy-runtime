---
title: "FR-35 — Guest-Path LLM Usage: Metered Where It Is Spent, Recorded Where It Is Owned"
api_version: "1.0"
last_verified: "2026-09-17"
status: current
owner: "platform-team"
---

# FR-35 — guest-path LLM usage: metered where it is spent, recorded where it is owned

**FR-35 (app filing 2026-09-16, runtime 2.19.0). SHIPPED 2026-09-17 (#712; DEC-040 … DEC-045).**
Live record: `token_meter.py` (`LlmUsageLedger`, `llm_usage_deferral_scope`, `record_llm_usage`),
`nodus_worker.run_one` (the scopes; `llm_usage` on every reply), `nodus_runtime_adapter.
_apply_deferred_llm_usage`, `genai_telemetry.replay_deferred_llm_span`.

> **As built, and one thing the build found:** the worker's `run_agent_tool` normalised the
> tool result to `{success, result, error}` — it had been DROPPING `failure_class` (and
> `cancelled`), so on `nodus_vm` RETRY-CLASSIFY-1's class never reached the compiled plan's
> guard and a cancelled / refused / budget-exceeded step was substring-classified in the guest
> after all. Found by §9's admission test (the refusal arrived typed at `execute_tool` and
> untyped in the guest). Fixed here; `LLMBudgetExceededError` now declares
> `failure_class = "transient"`. Admission-in-the-worker is tested with a fake resource
> manager that records the tenant the reserve saw (the forwarded one); the Redis-backed
> integration half is owed. Mutation-tested 9/9.

**Originally:** DESIGN ONLY — proposal under `AGENT_WORKING_RULES.md` §8 (a change to what the
worker reply carries and what the parent records). The filing's ask 1 is the right shape and this design takes it; §2 says why the
other two are declined. §3 is what the filing did not name — the governor's *admission* has the
same split as its *accounting*, and they resolve differently. §7 is what not to build.

---

## 1. The finding, verified against source

With `AINDY_AGENT_EXECUTION_BACKEND=nodus_vm` — the app's default — a plan is compiled to a
native workflow and its `call_tool` steps execute inside the Nodus worker process (warm pool
`nodus_worker_pool.WarmNodusWorker.execute`, or a one-shot subprocess). A tool that calls an
LLM there runs the provider client *in the worker*, so:

| Where | What happens | What the API process sees |
|---|---|---|
| worker: `llm_operation(...).record(response)` (#706) → `observe_llm_usage` | Prometheus counters in the **worker's** registry increment; `_attribute_usage` calls `resolve_llm_subject()` — ContextVars set in the API process, absent here — and accrues **nothing** to any run or tenant | `/metrics` never serves the worker's registry; `aindy:rm:tenant:*` unchanged; `_rm.get_usage(run_id)["tokens"]` reads 0 |
| worker: the `chat {model}` span (#706) | started on a tracer with no provider in that process | no span, ever |
| worker: `llm_budget_reservation` (the governor's reserve) | `resolve_llm_subject()` → no subject → *"nothing to charge"*, admitted | the tenant window never moves and never refuses |

The app measured it on the first agent run whose step spent tokens at execution time: 1,965
DeepSeek tokens, an `analysis_results` row written, and every API-side reading at zero. **On this
backend the cost governor sees planning and nothing else.**

**★ The channel already exists.** The worker's reply to `run_one` is a JSON envelope that
already carries three *deferred* collections the parent applies after the fact —
`memory_writes` (`_apply_deferred_memory_writes`), `emitted_events` (`_apply_deferred_events`)
and `simulated_effects` — precisely because a guest cannot commit, emit or accrue across the
process boundary. `nodus_runtime_adapter.run_script` is where they land. LLM usage is a fourth
deferred collection of exactly that kind, and it had simply never been added.

---

## 2. The three asks, and why ask 1

| Ask | Verdict | Why |
|---|---|---|
| **1. Meter at the seam, in the parent** — carry each call's usage on the worker reply, record it in the API process under the run's scope | **taken** | One counter, one process, one registry. Attribution can be **explicit** — the reply's `context` already carries `user_id`, `run_id`, `execution_unit_id` — rather than depending on a ContextVar that happens to be set on the calling thread. The same channel carries the timing that lets the parent emit the `chat {model}` span #706 could not (§5). Rides a mechanism with two years of precedent in this file |
| 2. Forward the identity into the worker and accrue to Redis there | **declined for accounting; taken for admission (§3)** | Accrual from the worker is correct only when the resource manager is Redis-backed; in thread mode with an in-memory RM the worker's accrual lands in a process nobody reads. Two accrual sites is also the double-count `token_meter.py`'s own design rejects (`test_a_chat_call_is_metered_exactly_once`). But the *reserve* must run where the call is made, and it needs a subject — so the identity IS forwarded, for that half only |
| 3. `prometheus_client` multiprocess mode | **declined** | Solves the graph, not the governor (the filing says so); and a multiprocess registry is a file-backed shared state with its own lifecycle, for four counters that ask 1 makes correct without it |

---

## 3. ★ The half the filing did not name: admission and accounting split differently

`llm_budget_reservation` is *reserve → call → reconcile*, and **reconcile only releases the
estimate** — the ACTUAL is recorded by the meter, independently. So the two halves of the
governor have different homes on the guest path:

| Half | Where it must run | What it needs | With ask 1 |
|---|---|---|---|
| **Admission** (refuse *before* the call) | in the **worker** — the call is made there and cannot be refused from outside | a subject (`tenant_id`, `run_id`) and a resource manager that can see the tenant window | the worker enters `llm_attribution_scope(tenant_id=ctx.user_id, run_id=ctx.run_id)` around the script, so `resolve_llm_subject()` answers and `reserve_tenant_tokens` runs **where the RM is Redis-backed**. With an in-memory RM the worker's window is empty — admission is vacuous there, and the design says so rather than pretending |
| **Accounting** (record the actual) | in the **parent** — the process that owns `/metrics`, the run's usage and the tenant window everyone reads | the numbers | the deferred ledger, replayed by `run_script` under an explicit subject — correct on every RM backend |

**★ The two must not both accrue.** Under deferral the worker's `observe_llm_usage` appends to
the ledger and does **nothing else** — no local counters, no `_attribute_usage` — or a
Redis-backed deployment counts every guest call twice (worker accrual + parent replay). Deferral
*replaces* observation in that process; it does not add to it.

---

## 4. The ledger

```python
# token_meter.py (proposed)
@dataclass(frozen=True)
class LlmUsageRecord:
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    started_at_ms: int          # epoch ms — so the parent can emit a span with real timestamps
    duration_ms: int
    tool: str | None            # the guest tool whose step spent it, when the seam knows
    outcome: str                # "ok" | "error:<Type>"

_DEFERRED_LLM_USAGE: ContextVar[list[LlmUsageRecord] | None] = ContextVar(..., default=None)

@contextmanager
def llm_usage_deferral_scope() -> Iterator[list[LlmUsageRecord]]:
    """Entered by the WORKER around a script: every observe_llm_usage inside appends here
    and observes nothing locally. The list is what the reply carries."""
```

- `observe_llm_usage` gains one branch at the top: *if a deferral list is active, append a
  record and return.* `LlmOperation.record` (#706) is unchanged — it already calls this — and
  it is where `started_at_ms` / `duration_ms` / `outcome` are known, so the record is built
  there and handed in; the raw `observe_llm_usage(provider, model, response)` path builds one
  with `duration_ms=0` for callers that bypass the span helper (none in tree; the census
  refuses it).
- **Bound:** `AINDY_NODUS_LLM_LEDGER_MAX` per-call records (default **256**); beyond that the
  worker aggregates the tail per `(provider, model)` into `{calls, prompt_tokens,
  completion_tokens}`. A guest loop that makes ten thousand calls must not produce a
  ten-thousand-entry reply. Accounting reads both; spans are emitted for per-call records only.
- The reply gains `"llm_usage": {"records": [...], "tail": [...]}` beside `memory_writes`.
  A worker that crashes mid-script loses its ledger with its reply — the same class as
  `memory_writes` on a crash, and the same answer: the reply is the only channel, and a lost
  reply is a lost segment (`RECOVERY-GRANULARITY-1` re-drives it).

---

## 5. The parent: record, then replay the span

In `nodus_runtime_adapter.run_script`, beside the three existing applies:

```python
_apply_deferred_llm_usage(result.get("llm_usage"), context)
```

which, for each record and tail bucket, calls a new
`token_meter.record_llm_usage(provider, model, prompt, completion, *, tenant_id, run_id,
unit_id)` — the accrual half of `observe_llm_usage` factored out so it can take an **explicit
subject**: `tenant_id=context.user_id`, `run_id=context.run_id` (the agent run when the
segment belongs to one), `unit_id=context.execution_unit_id`. ContextVars remain the fallback
when the context carries nothing — a `sys.v1.nodus.execute` script with no run still accrues
to its unit and tenant. The Prometheus counters move in the parent's registry, with the same
`{provider, model, kind}` labels as an in-process call, plus `attributed="run"` where a run was
named — the label the app's 2.13.0 reading said only ever appeared on `agent_flow`.

**And the span (#706's gap).** For each per-call record the parent emits `chat {model}` through
`genai_telemetry` with `start_time` / `end_time` from the record, nested under the current span
(the flow node's, or `invoke_agent` when the segment runs inside `execute_run`), carrying the
usual `gen_ai.*` attributes plus `aindy.deferred = true` and `gen_ai.tool.name` when the seam
knew the tool. OTel allows explicit timestamps on `start_span` / `end`; the span is *late*, not
*wrong* — it says when the call happened, in the trace the operator is looking at, which the
worker's tracer-less span never could.

`gen_ai.client.token.usage` / `operation.duration` (the OTel metrics) are recorded from the
same replay. Nothing about the `agent_flow` backend changes; a tool that runs in-process is
metered as it is today, and the deferral scope is never active there.

---

## 6. What the app sees after this

| Reading (their table) | Before | After |
|---|---|---|
| `/metrics` `aindy_llm_tokens_total{provider="deepseek"}` | no sample | **1965** |
| `aindy_llm_calls_total{attributed="run"}` | no sample | **1** |
| Redis `aindy:rm:tenant:<user>:tokens` | planner only (5876) | **+1965** |
| `score.computed.dimensions.llm_tokens` | 0 | **1965** — `execute_run` reads `_rm.get_usage(run.id)` *after* the segment, and the replay has landed by then |
| governor refusal on a runaway guest loop | never | **with a Redis-backed RM: yes, in the worker, before the call**; with an in-memory RM: no (documented) |

---

## 7. What not to build

- **Not worker-side accrual of actuals** (§3) — double-counts on Redis, invisible on in-memory.
- **Not ContextVar forwarding as the accounting mechanism.** The identity rides the reply's
  `context`, explicitly; a ContextVar that "happened to be set" is how this was lost to begin
  with.
- **Not multiprocess Prometheus.** Four counters, one owner.
- **Not a new metric name.** The point is that the *existing* names read true on this backend.
- **Not a per-record `EffectRecord`.** Usage carries no authority and constitutes no effect; it
  is not in any idempotency key (`PROGRESS-CHANNEL-1`'s three properties). A replayed segment
  that spends tokens again *is* new spend, and is counted again — correctly.

---

## 8. Decisions this design asks for (`DEC-NNN` in the implementing PR)

1. Ask 1: usage is **carried on the worker reply and recorded in the parent**, as a fourth
   deferred collection beside `memory_writes` / `emitted_events` / `simulated_effects` (§1–§2).
2. **Deferral replaces observation in the worker** — no local counters, no accrual (§3).
3. **Admission stays in the worker** under a forwarded subject; it is real only with a
   Redis-backed resource manager, and the docs say so (§3).
4. The parent attributes from the reply's **explicit** context, ContextVars as fallback (§5).
5. Spans are **replayed with recorded timestamps**, marked `aindy.deferred` (§5).
6. Ledger cap 256 per-call records, aggregate tail per (provider, model) (§4).

## 9. Tests that must exist

- **Assert the mechanism (the standing rule):** a test that runs `run_one` **in-process** with
  a stub tool whose LLM client returns a usage-bearing response, and asserts (a) the reply
  carries the record, (b) the *worker-side* counters did **not** move, (c) the worker entered
  the attribution scope (a spy on `resolve_llm_subject` sees the forwarded tenant).
- The parent: a stub reply with `llm_usage` through the real `run_script` → the parent's
  counters move exactly once, `attributed="run"`, the tenant window accrues, and a `chat
  {model}` span with the recorded timestamps lands under the current span.
- **Double-count control:** the same stub reply applied twice must count twice (it is two
  segments' spend); a record appearing in both `records` and `tail` must not — the aggregation
  boundary is asserted at the cap.
- The `agent_flow` backend: `test_a_chat_call_is_metered_exactly_once` unchanged and green —
  the deferral scope is never active in-process.
- Governor in the worker: with the attribution scope forwarded and a Redis RM
  (`tests/integration`), a guest call over the tenant window is refused **inside the worker**
  and the refusal reaches the step as `failure_class: "transient"` (a budget refusal is a
  window, not a fault — `RETRY-CLASSIFY-1`'s class for it).
- Mutation: drop the early-return in `observe_llm_usage` under deferral → the worker-side
  counters move → red; drop the replay → the parent's do not → red.
