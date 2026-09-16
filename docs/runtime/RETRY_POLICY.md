---
title: "Retry Policy"
last_verified: "2026-09-16"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Retry Policy

## Purpose

`AINDY/core/retry_policy.py` is the single source of truth for all retry semantics across
every execution type in A.I.N.D.Y.

> **Verified against source 2026-08-13.** The structure below holds. Two things did not, and
> both are corrected in place: the `RetryPolicy` dataclass has gained a fifth field
> (`execution_guarantee`), and the **Backoff** section reached a correct conclusion from an
> inverted premise — see that section, it matters if you are about to change a constant.  Before this layer existed, retry limits were
scattered as hardcoded integers across three files:

| File | Hardcoded value | Replaced by |
|---|---|---|
| `runtime/flow_engine/` * | `POLICY["max_retries"] = 3` | `_FLOW_RETRY_POLICY.max_attempts` (`flow_engine/node_executor.py`) |
| `runtime/nodus_adapter.py` | `1 if risk_level == "high" else MAX_STEP_RETRIES` | `resolve_retry_policy(execution_type="agent", risk_level=...)` |
| `platform_layer/async_job_service.py` | implicit always-fail | `log.attempt_count < log.max_attempts` (`:1283`) |

\* `flow_engine` is a **package**, not a module — this row said `flow_engine.py`. The retry gate
lives in `flow_engine/runner_steps.py`; the resolver alias is re-exported from
`flow_engine/shared.py`.

---

## Data model

```python
@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int          # total tries (1 = no retry)
    backoff_ms: int = 0        # base delay between attempts, in ms
    exponential_backoff: bool = False       # multiply backoff_ms by 2**attempt
    high_risk_immediate_fail: bool = False  # stop on first error regardless of max_attempts
    execution_guarantee: str = "AT_LEAST_ONCE"   # "AT_LEAST_ONCE" | "EXACTLY_ONCE"
```

`execution_guarantee` was added after this document was first written and is **load-bearing**:
`AGENT_HIGH_RISK` declares `EXACTLY_ONCE`, and the same field name appears on `SyscallEntry`
(`kernel/syscall_registry.py`) and on tool-registry entries (`agents/tool_registry.py`), where
`syscall_dispatcher.py:470` reads it to decide whether the idempotency gate engages. Three
namespaces, one vocabulary — see `IDEMPOTENCY_CONTRACT.md` and TECH_DEBT `IDEM-10` / the MEB
program. The `RetryPolicy` copy is the **EU-level** one, persisted into `ExecutionUnit.extra`.

---

## Named constants

| Constant | max_attempts | backoff_ms | exponential | high_risk_immediate_fail | execution_guarantee |
|---|---|---|---|---|---|
| `FLOW_NODE_DEFAULT` | 3 | 200 | ✔ | False | `AT_LEAST_ONCE` |
| `AGENT_LOW_MEDIUM` | 3 | 200 | ✔ | False | `AT_LEAST_ONCE` |
| `AGENT_HIGH_RISK` | 1 | 0 | ✘ | **True** | **`EXACTLY_ONCE`** |
| `ASYNC_JOB_DEFAULT` | 1 | 500 | ✔ | False | `AT_LEAST_ONCE` |
| `NODUS_SCHEDULED_DEFAULT` | 3 | 300 | ✔ | False | `AT_LEAST_ONCE` |
| `NO_RETRY` | 1 | 0 | ✘ | False | `AT_LEAST_ONCE` |

*Corrected 2026-08-13.* This table previously carried only `max_attempts` and
`high_risk_immediate_fail`, and the **Backoff** section below asserted `backoff_ms=0` in all six.
Four of the six carry a non-zero backoff. What each replaced historically:
`FLOW_NODE_DEFAULT` ← `flow_engine.POLICY["max_retries"]`; `AGENT_LOW_MEDIUM` ←
`nodus_adapter.MAX_STEP_RETRIES`; `AGENT_HIGH_RISK` ← `if risk_level == "high": break`;
`ASYNC_JOB_DEFAULT` ← the `max_attempts=1` default in `async_job_service`;
`NODUS_SCHEDULED_DEFAULT` ← `NodusScheduledJob.max_retries`; `NO_RETRY` ← the
`task_orchestrate` RETRY→FAILURE mapping.

---

## Resolver

```python
resolve_retry_policy(
    *,
    execution_type: str,           # "flow" | "agent" | "job" | "nodus"
    risk_level: str | None,        # agent only: "low" | "medium" | "high"
    node_max_retries: int | None,  # per-node flow override
    job_max_retries: int | None,   # per-job nodus scheduled override
) -> RetryPolicy
```

Resolution order per execution type:

- `"flow"` — `FLOW_NODE_DEFAULT`; overridden by `node_max_retries` when provided
- `"agent"` — `AGENT_LOW_MEDIUM` for low/medium; `AGENT_HIGH_RISK` for high (default when risk_level is absent)
- `"job"` — `ASYNC_JOB_DEFAULT`
- `"nodus"` — `NODUS_SCHEDULED_DEFAULT`; overridden by `job_max_retries` when provided; also triggered by `workflow_type.startswith("nodus")` on `"job"` EU type
- unknown — `NO_RETRY` (safe default)

---

## Execution paths and where the policy is read

### Flow nodes (`runtime/flow_engine/runner_steps.py::_handle_node_status`)

```text
PersistentFlowRunner.resume()
  node returns "RETRY" status
    → _node_cfg = self.flow.get("node_configs", {}).get(current_node, {})
    → _run_policy = resolve_retry_policy(
          execution_type="flow",
          node_max_retries=_node_cfg.get("max_retries"),  # None → default
      )
    → retry, record = decide_retry(result, site="flow_node", attempt=attempts,
                                   attempts_allowed=attempts < _run_policy.max_attempts)
    → if retry → continue (retry)
    → else → _fail_execution(...)
```

The classification conjunct is real and load-bearing; the original pseudocode omitted it.
Since 2026-09-16 (`RETRY-CLASSIFY-1`) it takes the node's **whole result** so a
`failure_class` the node declared decides — see Error classification. **No sleep occurs
here** — see Backoff.

When `node_configs` is absent (every flow except a per-run override), `_node_cfg` is `{}`
and `get("max_retries")` returns `None`, so `resolve_retry_policy` returns `FLOW_NODE_DEFAULT`
(max_attempts=3) — identical to the old hardcoded behavior.

### Agent steps (`runtime/nodus_adapter.py::_execute_agent_step`, ~`:256`)

```text
_execute_agent_step(step, ...)
  → _step_policy = resolve_retry_policy(
        execution_type="agent",
        risk_level=step.get("risk_level", "high"),
    )
  → max_attempts = _step_policy.max_attempts
  → for attempt in range(1, max_attempts + 1):
        execute_tool(...)
        if success: break
        if _step_policy.high_risk_immediate_fail: break   # was: if risk_level == "high"
        retry, record = decide_retry(tool_result, site="tool_step", attempt=attempt,
                                     attempts_allowed=attempt < max_attempts)
        if not retry: break                                # class veto, or attempts exhausted
        if attempt < max_attempts: log warning
```

**No sleep between attempts**, despite `AGENT_LOW_MEDIUM` declaring `backoff_ms=200` with
exponential backoff — see Backoff.

There is a **third** agent retry surface this document predates. The `nodus_vm` backend
(RTR-1) does not use this loop: `runtime/agent_plan_compiler.py:111` calls
`resolve_retry_policy(execution_type="agent", risk_level=...)` at **compile** time and emits the
attempt bound directly into generated Nodus source as a `while` condition, with
`is_retryable_error` registered as a host function by `nodus_worker.py`. Same policy, same
classifier, resolved one layer earlier. Since `RETRY-CLASSIFY-1` the generated guard is
`is_retryable_error(__result_N)` — the whole `call_tool` result, not `["error"]` — so a class
`execute_tool` declared reaches the guest loop and a model-shaped message cannot decide its own
retry.

### Async jobs (`platform_layer/async_job_service.py::_execute_job_inline`, `:1105`)

```text
_execute_job_inline(log_id, task_name, payload)
  attempt_no = log.attempt_count + 1      ← numbered BEFORE anything can fail
  handler = _JOB_REGISTRY.get(task_name)
    None → raise AsyncJobHandlerNotRegistered   ← TERMINAL, never retried
  log.attempt_count = attempt_no
  handler(payload, db)
    success → log.status = "success"
    exception →
      db.rollback(); log.attempt_count = max(log.attempt_count, attempt_no)  ← the attempt counts
      if retryable and log.attempt_count < log.max_attempts:   ← retry check
          log.status = "pending"; db.commit()
          _schedule_job_retry(...)     ← thread mode: threading.Timer(_compute_retry_delay(log_id))
          return                          distributed: enqueue_delayed via the dispatcher
      else:
          log.status = "failed"   ← terminal
```

`log.max_attempts` is set at submission time (`submit_async_job(max_attempts=1)` default).
When a caller passes `max_attempts > 1`, retries fire automatically — **after an exponential
backoff** (`AINDY_RETRY_BACKOFF_BASE_MS`, default 1000, doubling per attempt, capped by
`AINDY_RETRY_BACKOFF_MAX_MS`, default 30000; the same curve the distributed path always had).

> **Corrected 2026-09-13 (`ASYNC-JOB-UNREGISTERED-STORM-1`).** The diagram above used to be
> wrong in three ways that compounded into a live incident: the increment came AFTER the
> handler lookup, so an unregistered handler raised at attempt 0 and `0 < 1` retried forever;
> the increment was only committed as a side effect of the started-event emit and the except
> branch's `rollback()` discarded it otherwise; and the thread-mode reschedule was an immediate
> executor submit with no delay. Measured: ~87 re-dispatches per second per job. An
> unregistered handler is now a distinct terminal class (`AsyncJobHandlerNotRegistered`,
> a `RuntimeError` subclass); the attempt is restored after the rollback; the retry waits.

### Nodus scheduled jobs — full data flow

This path previously had a gap: `NodusScheduledJob.max_retries` was stored correctly in
`AutomationLog.max_attempts` but the flow engine always defaulted to 3 retries.

```text
NodusScheduledJob  (DB row, max_retries=1)
  └── _run_scheduled_nodus_job(job_id)        ← runtime/nodus_schedule_service.py:58
        AutomationLog.max_attempts = job.max_retries   ← correct audit trail

        run_nodus_script_via_flow(            ← runtime/nodus_execution_service.py:177
            script          = job.script,
            error_policy    = job.error_policy,
            node_max_retries = job.max_retries,        ← NEW: threads the value in
            ...
        )
          ↓ routes through sys.v1.nodus.execute for capability + quota enforcement
          ↓ (falls back to _run_nodus_via_flow_direct when user_id is absent)

          if node_max_retries is not None:
              flow = {
                  **FLOW_REGISTRY["nodus_execute"],
                  "node_configs": {
                      "nodus.execute": {"max_retries": node_max_retries}
                                     ↑
                              max_retries ENTERS node_config HERE
                  },
              }
          PersistentFlowRunner(flow=flow, ...)
            └── resume()
                  nodus.execute returns "RETRY"
                    → _node_cfg = flow["node_configs"]["nodus.execute"]
                                  = {"max_retries": 1}
                    → _run_policy = resolve_retry_policy(
                          execution_type="flow",
                          node_max_retries=1,
                      )  → RetryPolicy(max_attempts=1)
                    → if 0 < 1 → retry  (attempt 1)
                    → if 1 < 1 → False  → _fail_execution()
```

Verified accurate 2026-08-13 (`nodus_execution_service.py:147-153`), with one hop added
above: `run_nodus_script_via_flow` is not a direct call into the runner — it dispatches through
`sys.v1.nodus.execute` (`syscall_registry.py:_handle_nodus_execute`), which forwards
`node_max_retries` unchanged.

The shared `NODUS_SCRIPT_FLOW` module-level dict is never mutated.
Each call to `run_nodus_script_via_flow` with `node_max_retries` gets its own
shallow-copied flow dict with the per-run `node_configs` key.

### ExecutionUnit metadata

`require_execution_unit()` in `core/execution_gate.py` resolves and persists
`retry_policy` into `ExecutionUnit.extra` (JSONB) for every execution:

```text
require_execution_unit(eu_type="job", extra={"workflow_type": "nodus_schedule", ...})
  → _resolve_policy_for_eu("job", {"workflow_type": "nodus_schedule", ...})
       workflow_type.startswith("nodus") → exec_type = "nodus"
       resolve_retry_policy(execution_type="nodus")
       → {"max_attempts": 3, "backoff_ms": 300,
          "exponential_backoff": True, "high_risk_immediate_fail": False,
          "execution_guarantee": "AT_LEAST_ONCE"}          ← 5 keys, not 4
  → extra["retry_policy"] = <above dict>
  → ExecutionUnit.extra = extra   (JSONB persisted)
```

Any code holding the EU can read `eu.extra["retry_policy"]` without importing `RetryPolicy`.

---

## Backoff

> **Rewritten 2026-08-13. The old text said `backoff_ms=0` in all current policy constants, and
> concluded there is no sleep between retries anywhere in the execution layer. The conclusion is
> right; the premise is backwards, and acting on the old advice would have been a no-op.**

Four of the six constants carry a **non-zero** backoff — 200ms (flow, agent low/medium), 300ms
(nodus scheduled), 500ms (async job) — all with `exponential_backoff=True`. The module also
implements the delay properly: `_retry_delay_seconds` applies `2 ** attempt`, adds up to
`_MAX_JITTER_MS` (50ms) of jitter, and caps the result at `_MAX_BACKOFF_SECONDS` (10s).

**None of it runs.** `_sleep_before_retry` and `_sleep_before_retry_async` have **zero callers**
outside `retry_policy.py`. Their only consumers were `execute_with_retry` and
`_execute_with_retry`, which also had **zero callers** and were **deleted 2026-09-16**
(`RETRY-CLASSIFY-1`, DEC-024) — the `execute_with_retry` in
`platform_layer/scheduler_service.py` is a same-named *local* function, unrelated.

Every real retry loop is hand-rolled and reads exactly two fields, `max_attempts` and
`high_risk_immediate_fail`:

| Loop | Reads | Sleeps |
|---|---|---|
| `flow_engine/runner_steps.py:266` | `max_attempts` | no |
| `nodus_adapter.py:262` | `max_attempts`, `high_risk_immediate_fail` | no |
| `async_job_service.py` `_execute_job_inline` | `log.max_attempts` (from the DB row) | **yes since 2026-09-13** — reschedules after `_compute_retry_delay` (a `Timer` in thread mode, `enqueue_delayed` distributed); an unregistered handler is terminal |
| `agent_plan_compiler.py:111` | `max_attempts` baked into generated Nodus | no |

**Consequence for anyone changing this.** The old instruction — *"when a caller wants backoff it
should update the relevant policy constant"* — would change nothing at all. `backoff_ms` and
`exponential_backoff` are **declared and persisted but never applied**: they ride into
`ExecutionUnit.extra` as retry *metadata*, so an EU can report a backoff the runtime never
honours. Introducing real backoff means calling `_sleep_before_retry` from a loop — not editing
a constant.

---

## Error classification (`RETRY-CLASSIFY-1`, 2026-09-16)

**A failure carries a class, set at the raising site.** Every `execute_tool` refusal and every
dispatcher error envelope returns `failure_class` beside `error`, one of:

| Class | Meaning | Retried? |
|---|---|---|
| `transient` | a retry may succeed — timeout, worker crash, 5xx, tripped breaker, quota window | **yes** |
| `cancelled` | the run was cancelled | no |
| `permission` | capability / scope / policy / tenant refusal, missing token | no |
| `not_found` | tool, syscall or resource absent | no |
| `invalid` | caller-side: bad args, schema violation | no |
| `fatal` | the raising site knows it is terminal (contract violation, host cannot provide the declared isolation) | no |

`classify_failure(result_or_error, *, site, attempt) -> FailureRecord` resolves one attempt:
a class the site declared wins (`classified_by="site"`); otherwise the substring table decides
(`"substring"` — the same nine needles as before, each mapped to a class); otherwise
`transient` (`"default"`, the pre-existing behaviour for an unmatched string). `decide_retry`
combines the class with the policy's attempt budget and **counts the decision** on
`aindy_retry_classifications_total{site, failure_class, classified_by, decision}` — the
operator signal that a mis-classification used to lack. `classified_by="substring"` is the
residue the table still owns.

`is_retryable_error(result_or_error)` is the boolean form and accepts the **result dict** (the
loops pass it whole) or a bare string. It is wired at the flow-node RETRY gate
(`runner_steps.py`), the tool-step loop (`nodus_adapter.py`), the Nodus host function of the
same name (`nodus_worker.py`) and into every compiled agent plan (`agent_plan_compiler.py`).
The `flow.node.*` and `agent.step.*` failure events carry the record under `payload.retry`.

**Why a string on the dict and not an exception type:** the three loops consume dicts, and the
guest boundary swallows host exceptions into `ok: False` — a type cannot cross it.

**Measured before the change, on the runtime's own refusal strings:** *cancelled*, *capability
token is required* and *capability enforcement failed* all matched nothing and read RETRY, so a
cancelled run's tool was re-attempted up to 3× (each refused again). The census is derived:
`test_retry_classification.py` walks `tool_registry.py`'s AST and fails on any
`"success": False` return without a class.

Design: `docs/design/RETRY_CLASSIFICATION_AND_CONTEXT_DESIGN.md`. Phase 2 (carrying the record
forward to the next attempt — `RETRY-CONTEXT-1`) is not built.

---

## Adding a new execution type

1. Add a named constant to `core/retry_policy.py`.
2. Add the `execution_type` string to `resolve_retry_policy()`.
3. Add the mapping in `_EU_TYPE_TO_EXEC_TYPE` in `core/execution_gate.py` if it needs
   a new EU type.
4. Replace any inline retry integer in the new caller with a call to `resolve_retry_policy`.
5. Decide retries with `decide_retry(result, site=..., attempt=..., attempts_allowed=...)` so the
   classification is counted, and pass the **whole** result dict, never `result["error"]`.
6. Every refusal the new path returns declares `failure_class`; extend the AST census in
   `test_retry_classification.py` to the new file.
