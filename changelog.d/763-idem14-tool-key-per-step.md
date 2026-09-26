### Changed — the tool seam's idempotency key is per plan STEP, not per run (IDEM-14, DEC-076, #763)

- **Why it was wrong:** `execute_tool` keyed an effect on `(tool, args, run_id)`. A plan whose
  steps 1 and 4 call the same `EXACTLY_ONCE` tool with identical args ran step 1 and **replayed**
  it as step 4. The second effect (a second reminder, the same message after a WAIT) never
  happened, and the run reported `success`.
- Both agent backends now pass the plan's step index, and the key's scope is
  `"<run_id>#step:<N>"`. A retry of one step is still one effect and still replays. Syscalls,
  MCP, extensions and a hand-written `call_tool(name, args)` have no step and keep the run scope.
  The strict lock (`AINDY_TOOL_IDEMPOTENCY_STRICT`) uses the same key and narrows with it.
- `execute_tool` gains an optional `step_index` keyword. An app that stubs `execute_tool` in its
  tests with a fixed signature must accept it.
- **Upgrade:** a step whose effect completed under the old key and that re-drives across the
  upgrade computes the new key and does not find that row. A `nodus_vm` continuation replays from
  `agent_steps` first, so only an effect whose step row was never recorded is exposed.
