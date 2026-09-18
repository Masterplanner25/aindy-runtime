### Changed — `RECOVERY-GRANULARITY-1`: an agent step is durable as it completes, and a crash continuation replays recorded steps instead of re-running the segment (#722; DEC-063..066)

- On the `nodus_vm` backend the `AgentStep` rows were written in a batch only when a segment's
  guest script returned, and crash continuation re-ran a partial segment from its first step —
  every LLM call in it re-issued. Now the `call_tool` host function writes each step's row as it
  completes (own session, committed, before the script returns), keyed on the plan's step index.
- **Replay:** on a crash continuation, a step with a recorded `success` row returns the recorded
  result with `replayed: true` and the tool does not run — no LLM call, no usage, nothing reaching
  the effect gate. A fresh run never replays; a failed step re-executes.
- **Guest surface:** `call_tool(name, args, step_index)` — an optional third argument (arity
  `(2, 3)`). Compiled agent plans pass it; hand-written scripts that call `call_tool(name, args)`
  are unchanged and unrecorded. Consumers reading `output_state["__step_N_result"]` may see a
  `replayed` key on a continued run.
- Row semantics: a retried step overwrites its own row (one row per step, never one per attempt);
  `steps_completed` keeps FR-34's meaning. No schema change. The parent's segment-end write is now
  an upsert and emits no step event for a replayed step.
- Residual by design: a crash between the tool returning and its row committing re-runs that one
  step (mediated effects still dedup under DUR-2).
