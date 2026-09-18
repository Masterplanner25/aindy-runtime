---
title: "Recovery Granularity — Design"
api_version: "1.0"
last_verified: "2026-09-17"
status: current
owner: "platform-team"
---

# `RECOVERY-GRANULARITY-1` — a step's result is durable before the segment's checkpoint — design

**IMPLEMENTED 2026-09-17 (#722) — DEC-063..066 accepted as written. `continuation` is an explicit
flag threaded from `continue_crashed_agent_runs` to the worker context (not derived from the
`durable_effects` signal — one field, one meaning); `call_tool` is registered with arity `(2, 3)`.
Tests: `tests/unit/test_recovery_granularity.py`.** Originally a proposal under
`AGENT_WORKING_RULES.md` §8 (a change to what crash continuation re-executes). The entry names the shape (DBOS `operation_outputs`,
replay-by-ordinal) and the constraint (does NOT reopen the declined kernel replay). §1 measures
where the per-step write actually happens today; §2 is the finding that makes the build small —
the seam that can write per step already has a session; §3 is why the identity is the plan's
step index, not an ordinal; §5 is what not to build.

---

## 1. Where the agent layer writes, measured

On the `nodus_vm` backend (the app's default) a **segment** of the compiled plan runs as ONE
guest script in the worker; every `call_tool` in it executes inside that script. The parent sees
the per-step results only when the script returns: `_run_agent_segment_flow` reconstructs them
from `output_state` (`reconstruct_agent_step_results`, `nodus_execution_service.py:508`) and
**then** writes the `AgentStep` rows in a batch (`:604`). `_count_completed_segments`
(`agent_continuation.py:103`) advances only on whole segments, so a crash mid-segment re-runs the
segment from its first step: **every LLM call in it re-issued**, every un-mediated side effect
re-fired. Mediated effects do not double-fire (`DUR-2`), which is why this is P2.

**★ The seam that can write per step already has a session.** `nodus_worker.run_agent_tool` —
the worker's `call_tool` host function — opens `session_factory()` for `execute_tool(db=…)` on
every call. The per-step durable write the entry asks for (LangGraph's
"pending-writes-then-checkpoint", DBOS's `operation_outputs`) can happen **right there, after the
tool returns**, on a session the seam already holds. Nothing new has to cross the process
boundary: the worker writes the row, the parent reads it on continuation.

---

## 2. The row is `agent_steps`, not a new table

`AgentStep` already carries `(run_id, step_index, status, result, error_message, executed_at)`
— exactly DBOS's `(workflow_uuid, function_id, output, error)`. The change is **when** it is
written and **who reads it back**:

| Today | Proposed |
|---|---|
| batch-written by the parent at segment end | **written by the worker seam as each step completes**, own session, committed |
| never read on continuation | **read before each step on a continued run**: a `success` row for `(run_id, step_index)` is *replayed* — the recorded `result` is returned and the tool does not execute |
| parent's batch is the only writer | the parent's segment-end batch becomes an **upsert by `(run_id, step_index)`** that fills only what the worker did not write (a worker crash between the tool returning and the row committing) |

No schema: `agent_steps` gains no column. `steps_completed` keeps FR-34's meaning (successes).

---

## 3. ★ Identity is the plan's step index, not a call ordinal — because of retries

DBOS keys on a monotonic `function_id` because its steps are sequential. Ours are too — but the
compiled plan's **retry loop lives inside the guest** (`while (... is_retryable_error(__result_N))
{ __result_N = call_tool(...) }`), so the *second attempt* of step N is the *next* `call_tool` in
ordinal terms. An ordinal would key attempt 2 of step 3 as step 4. So:

- the plan compiler emits the step index as a third argument: `call_tool(tool, args, N)` — the
  compiled source already knows `N` (it names `__step_N_result`); the host function accepts it
  and ignores it when absent (a hand-written guest script keeps working, unkeyed);
- the seam records `(run_id, N)` on success; a failed attempt writes a `failed` row that a later
  attempt overwrites; **only a `success` row replays**.

`FLOW-PARALLEL-1`'s vector-clock answer is for the day branches advance independently; a plan is
a sequence, and the entry's own ordering ("the ordinal first") stands with the index in the
ordinal's place.

---

## 4. Replay, precisely

At the seam, on a run that is **continuing** (the worker request context carries
`continuation: true`, set by `continue_crashed_agent_runs` — a fresh run never replays):

```
row = agent_steps[(run_id, N)] with status == "success"
if row: return {"success": True, "result": row.result, "error": None, "replayed": True}
else:   execute; write the row; return the live result
```

- **Replayed steps are not re-metered**: no LLM call happens, so no usage — which is the cost
  the entry is about. FR-35's ledger sees nothing, correctly.
- **The effect ledger is untouched.** `DUR-2` already makes a mediated effect idempotent on a
  continued run; replay sits *before* it and simply means fewer calls reach it.
- **`replayed: True` is on the result the guest sees** so a plan (or a debugger) can tell a
  replayed step from a live one; `AgentStep` rows are not duplicated (the seam skips the write
  on replay).
- **`_count_completed_segments` is unchanged** — it still picks the segment to re-run; replay is
  what makes re-running that segment cheap. Its docstring's *"AgentStep is batch-written per
  segment, so `completed_steps` always lands on a segment boundary"* becomes false and must be
  rewritten: `completed_steps` may now land mid-segment, and that is the point.

---

## 5. What not to build

- **Not kernel deterministic replay** (`DEC-019`, `ECOGAP-1` row 2) — this records a finished
  step's *result*; it constrains no code and intercepts no non-determinism.
- **Not a new `operation_outputs` table** — `agent_steps` is that table.
- **Not replay on a fresh run** — only a continued one; a run id is never reused.
- **Not smaller segments** — the entry's rejected alternative; this decouples recovery from
  scheduling instead of moving them together.
- **Not salvage-on-terminal-failure** (the SWE-agent note) — buildable *after* this, recorded
  in the entry, still not opened.

## 6. Decisions this design asks for (`DEC-NNN` in the implementing PR)

1. The per-step durable write happens at the **worker seam**, on its own session, as each step
   completes; the parent's batch becomes an upsert.
2. The row is **`agent_steps`** keyed `(run_id, step_index)`; no new table.
3. Identity is the **plan's step index** (compiler-emitted third argument), not a call ordinal.
4. Replay only on a **continued** run and only from a **`success`** row; the replayed result
   carries `replayed: True`.

## 7. Tests

- Through the real `run_one` in-process: a two-step plan where step 1 succeeds and step 2 raises
  → one `agent_steps` row (index 0, success) committed **before** the script returns (read
  through a separate session).
- Continuation of that run: step 0 is replayed (tool spy not called, `replayed: True`), step 1
  executes; the FR-35 ledger holds one record (step 1's), not two.
- Retry inside a step: attempt 1 fails (row `failed`), attempt 2 succeeds (row overwritten to
  `success`); ordinal-keying would have produced two rows — the mutation that proves §3.
- A fresh run with a pre-existing success row for its id (manufactured) does **not** replay.
- `_count_completed_segments` on a mid-segment `completed_steps` picks the same segment; the
  docstring pin is updated with it.
