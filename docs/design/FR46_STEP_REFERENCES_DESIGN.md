---
title: "FR-46 — Plan Step References — Design"
api_version: "1.0"
last_verified: "2026-09-25"
status: current
owner: "platform-team"
---

# FR-46: a plan step that takes an earlier step's result

**Status: BUILT 2026-09-25 (#764), default OFF.** Phase 1 of §7 shipped. DEC-073..075 were
accepted by the owner on 2026-09-25. Phase 2 (the app re-runs its goal with
`AINDY_PLAN_STEP_REFERENCES=1`) and phase 3 (the flip) remain. As built: the resolver is
`agents/step_references.py`, the path grammar is `core/result_path.py` (now shared with the
verifier), and the seams are `nodus_adapter.agent_execute_step` and
`nodus_worker.run_agent_tool` (plus its simulate branch).

---

## 1. The finding (app-filed, verified at source)

A plan step is `{"tool", "args", "risk_level", "description"}`, and `args` is a literal that the
planner writes before any step has run. Nothing reads a result back into a later step's arguments:

- `agent_flow`: `agent_execute_step` passes `step.get("args", {})` to `execute_tool` as written
  (`runtime/nodus_adapter.py:326`, `:524-533`).
- `nodus_vm`: `compile_agent_segment` bakes the args into the workflow input
  (`runtime/agent_plan_compiler.py:213`). The guest calls
  `call_tool(input_payload["__step_N_tool"], input_payload["__step_N_args"], N)` (`:147`).
- Neither prompt the runtime owns (`platform_layer/runtime_agent_defaults.py::_DEFAULT_PLANNER_PROMPT`,
  `agents/agent_runtime/planning.py::PLANNER_SYSTEM_PROMPT`) offers any way to express it.
- There is no templating anywhere in `AINDY/`: no `{{`, `$ref`, `from_state` or input mapping.

**Observed (app, run `02e9e214…`, Claude planner, `nodus_vm`, `completed 6/6`).** Step 0's
research came back real. Step 2's `memory.write` stored the sentence the planner wrote before
step 0 ran, and steps 3–5 created tasks from the planner's own priors. Every step returned
`success`, so nothing recorded a failure. "Research X, then use it" is the most natural goal a
person gives an agent, and its second half is always planned blind.

## 2. What exists that this must fit

| Question | Answer at HEAD |
|---|---|
| Plan validation after the planner | Only `steps` + `overall_risk` present (`planning.py:368-370`), then risk reconciliation and `apply_wait_policy` (`:372-388`). Nothing checks `args`. Replay (`creation.py::_create_run_from_plan`) skips `generate_plan` entirely. |
| Step identity | The **tool-step ordinal**. WAIT steps take no index (`agent_plan_compiler.py:265-267`), and the index is global across segments (`base_index`, `:289-307`). The verifier uses the same ordinal (`core/verifier.py:23-25`). |
| One seam both backends pass through | **`execute_tool`** (`agents/tool_registry.py:1063`). But its signature `(tool_name, args, user_id, db, run_id, execution_token)` has no step index and no earlier results. Its two callers do: `agent_execute_step` (agent_flow, holds `state["step_results"]`) and `nodus_worker.run_agent_tool` (nodus_vm, **in the worker subprocess**, holds `run_id` + `step_index` + a session factory). |
| Where earlier results live, readable at that moment | agent_flow: `state["step_results"]` (`{step_index, tool, status, result, error}`, checkpointed with the FlowRun), plus an `agent_steps` row per step. nodus_vm: guest `state` holds `__step_N_result` **for the current segment only** (each segment starts empty; under `AINDY_DURABLE_STEP_GRANULARITY` every step is its own segment). The `agent_steps` row, upserted and committed per step by `_record_step` (`nodus_worker.py:286-329`), covers every earlier segment, including across a WAIT. `_read_recorded_step` (`:257-283`) already reads one by `(run_id, step_index)`. |
| `args_schema` (FR-33) | Validated **inside** `execute_tool` (`tool_registry.py:1099-1116`), before the idempotency key and dispatch. On nodus_vm it runs in the worker; the tally rides the reply (DEC-067). |
| Idempotency key | `compute_action_id(action_type=tool_name, input_payload=args, scope=run_id)` inside `execute_tool` (`:1124-1137`), so it is computed from whatever `args` arrives. |
| Continuation replay | A `nodus_vm` re-drive returns a recorded `success` row **before** `execute_tool` (`nodus_worker.py:172-181`, DEC-066). |
| A path expression vocabulary already in use | The verifier's `expects`: `{"field": "<dot.path>", …}`, resolved by `_resolve_path` (dot-split, dict keys, integer list indices, `_MISSING` sentinel; `core/verifier.py:63-75`). |

## 3. The proposal

### 3.1 One reference form (DEC-073)

An argument **value**, at any depth inside `args`, may be exactly:

```json
{"$from_step": 0, "path": "raw_result"}
```

- `$from_step` is the tool-step ordinal (the same index as `agent_steps.step_index`,
  `__step_N_result` and the verifier). It must be **strictly less** than the referring step's
  own index.
- `path` is optional. It is a dot path into that step's `result` (the tool's return value, the
  `result` field of the step entry), resolved by the verifier's `_resolve_path`. It is lifted
  out to one shared helper, never copied. Omitted, it means the whole result.
- A dict with `$from_step` and any key other than `path` is not a reference; plan validation
  refuses it (§3.2).
- The resolved value **replaces** the placeholder whole, with whatever JSON type it has. There is
  no string interpolation (`"Summary: {{step0}}"`), no expressions, and no reference to a WAIT
  step or to a later step. The app asked for exactly this and ruled out free-form expressions.

Why this shape: a `$`-prefixed key cannot collide with a tool's argument names (none use one).
The dict form survives JSON round-trips through the planner, the plan column, the compiled
workflow input and the worker payload unchanged. Reusing the verifier's path vocabulary means
the runtime has one way to address inside a step result.

### 3.2 Validated at plan time

This is a new check at the runtime-owned post-processing point (`planning.py:372-388`) **and** in
`_create_run_from_plan`, so a replayed plan is checked too. For every reference in a step's args:

- the target index exists, is a tool step, and is strictly earlier;
- `path` is a string of non-empty dot segments.

A plan that fails is refused the way a malformed plan already is: `generate_plan` returns `None`
with a reason. It never runs with the placeholder in place.

### 3.3 Resolved at run time, before `execute_tool` (DEC-074)

One pure function, `resolve_step_references(args, lookup) -> (resolved_args, errors)`, lives in
`agents/`. It walks `args` and resolves each reference through `lookup(step_index)`, which
returns the step entry `{status, result}` or `None`. **Two callers, one resolver, two lookups**:

- **agent_flow**, in `agent_execute_step`, before its retry loop. The lookup reads
  `state["step_results"]`, which the node already holds.
- **nodus_vm**, in `nodus_worker.run_agent_tool`, before `execute_tool`. The lookup reads the
  `agent_steps` row by `(run_id, step_index)`, with the same query `_read_recorded_step` uses. The
  guest state cannot serve here because it forgets every earlier segment. The row is committed by
  `_record_step` before the next step runs, so it covers WAIT boundaries, durable step
  granularity and a continuation re-drive.

Resolving **before** `execute_tool` settles the app's ask 3 and the idempotency question
together:

- **`args_schema` validates the resolved value** (ask 3). The placeholder never reaches
  `validate_tool_args`, so `AINDY_TOOL_ARGS_VALIDATION=enforce` does not refuse every step that
  uses one.
- **The idempotency key is computed over the resolved args.** A step that writes step 0's
  findings has a different `action_id` when the findings differ. A key over the placeholder would
  make two runs' different findings dedupe against each other.
- **`agent_steps.tool_args` records the resolved args**, because the worker records what reaches
  the seam. The audit trail says what the tool was actually called with. The parent's gap-fill
  (`nodus_execution_service.py:639`) writes the compile-time args, so it must record the
  placeholder **and** mark the row, never pass it off as the call.
- **A continuation replay is unchanged.** A replayed `success` row returns before resolution, and
  its recorded `tool_args` are the args it originally resolved to.

### 3.4 An unresolvable reference fails the step (DEC-075)

At run time a reference fails when the target step:
- has no row or entry;
- has a status other than `success` (`failed`, or `skipped` at the authority gate: DEC-069's
  skipped step has no result to give);
- or has a result where `path` resolves to `_MISSING`.

The step then fails with `failure_class: "invalid"` and an error naming the reference. It is
**never** called with the literal placeholder and never with a partial resolution.
`failure_class: "invalid"` is not retried (DEC-025: only `transient` retries); a retry cannot
make an earlier step's result appear.

### 3.5 Default off, and the planner is told only when it is on (DEC-075)

`AINDY_PLAN_STEP_REFERENCES`, default **off** ("capabilities ship default-off until evidence").

- **Off:** no validation, no resolution and no prompt line; the behaviour is exactly today's.
- **On:** the runtime appends one line to the tool catalog it already renders
  (`planning.py::_build_planner_prompt`). That is the one place every planner sees, whichever
  system prompt the app supplies:

  > *A step's argument value may be `{"$from_step": N, "path": "a.b"}`: the result of tool step N
  > (0-based, counting tool steps only, earlier than this one), or a field inside it. Use it when
  > a step needs what an earlier step found.*

  The app said it would add its own line to `PLANNER_SYSTEM_PROMPT`. It no longer needs to,
  because the catalog carries the line.

## 4. Interactions checked

- **Authority gate (FR-38).** The gate parks the chain **inside** `call_tool`, after resolution,
  so a gated step's `tool_args` are already resolved when the operator sees them. A `skip`
  decision leaves a skipped step, and a later reference to it fails (§3.4). That is the correct
  answer, not a gap: the skipped tool produced nothing.
- **Simulate mode** (`nodus_worker._call_tool` → `simulate_agent_tool`) never reaches
  `execute_tool`. It resolves against simulated results the same way. If a simulated result lacks
  the path, simulation reports the reference failure, which is exactly what a dry run is for.
- **A crash re-drive on agent_flow** marks the run failed (`stuck_run_service.py:62-122`); nothing
  to resolve. An authority-gate resume restores FlowRun state including `step_results`.
- **Found while reading, not this design's to fix:** `compute_action_id` is scoped to the
  **run**, not the step. Two steps of one run calling the same `EXACTLY_ONCE` tool with
  identical args share an `action_id`, so the second replays the first. This design makes that
  rarer (resolved args differ), not impossible. It is filed separately.

## 5. The app's ask 4: size

**The runtime truncates no step result.** `AgentStep.result` and `AgentRun.result` are unbounded
JSONB. The worker reply is length-prefixed with no cap. `_json_safe` converts types without
cutting anything. **The 2 KB cut is the app's:** `apps/search/syscalls.py:133` returns
`{"raw_result": raw[:2000]}`, a hard slice with no marker (and again at
`apps/search/services/search_service.py:446`). A reference sees the full recorded result, so it
carries exactly what the tool returned, including that cut. Lifting it, or cutting at a word
boundary with a marker, is the app's change.

## 6. Not building

- Re-planning after each step. It is a different and larger feature, and the app did not ask.
- Expressions, string templates, or references to anything but an earlier tool step's result
  (not run inputs, not events, not memory).
- A new column or table. The `agent_steps` row and the flow state already hold every result.
- Resolution inside `execute_tool`. Its signature carries no step context, and threading it
  there would put plan semantics into the one seam that direct tool calls, MCP and syscalls
  share.

## 7. Phases

1. **Build (one PR, flag off):**
   - the resolver and the shared path helper;
   - plan-time validation (both entry points);
   - both call sites;
   - the gap-fill marker and the catalog line.

   Tests:
   - the resolver matrix;
   - both backends end to end: step 1 receives step 0's result, across a WAIT on nodus_vm and
     under durable step granularity;
   - `enforce` mode accepts a resolved value;
   - the key differs for different resolved values;
   - an unresolved reference fails with `invalid` and the tool is not called;
   - flag off gives today's behaviour.

   Mutation-check each.
2. **Evidence:** the app re-runs the owner's goal with the flag on. Step 2's `memory.write`
   should hold step 0's findings.
3. **Flip** the default on that evidence.

## 8. Decisions (provisional)

- **DEC-073:** one reference form, `{"$from_step": N, "path"?}`, a whole-value replacement,
  validated at plan time. The path uses the verifier's vocabulary.
- **DEC-074:** resolved in the two callers of `execute_tool`, before it, by one resolver: from
  flow state on agent_flow, from `agent_steps` on nodus_vm. The key, `args_schema` and the step
  record all see the resolved args.
- **DEC-075:** an unresolvable reference fails the step with `invalid` and never passes the
  literal. The feature ships behind `AINDY_PLAN_STEP_REFERENCES` (off), and the runtime's catalog
  carries the planner line when it is on.
