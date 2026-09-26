### Added — a plan step's argument may take an earlier step's result, behind `AINDY_PLAN_STEP_REFERENCES` (FR-46, DEC-073..075, #764)

- **Why:** a plan step's `args` were literals the planner wrote before any step ran, so "research
  X, then use it" always ran its second half blind. In the app's first real goal, `memory.write`
  stored the planner's pre-research sentence as its "findings", and every step said `success`.
  The next runs compounded it: recall ranked those placeholder notes first, and a planner invented
  a `strategy_id` because the recall's result could not reach the steps that needed it.
- **The form:** an argument value, at any depth, may be exactly
  `{"$from_step": N, "path": "a.b"}`. It is replaced whole by tool step N's result, or a field in
  it (dot path; list items by number, `results.0.id`). N counts tool steps only and must be
  earlier than the current step. A plan that breaks this is refused at plan time (on replay too).
- **Where:** resolved before `execute_tool` on both backends. On agent_flow that happens in the
  node; on nodus_vm it happens in the worker seam, from the `agent_steps` rows. That covers
  earlier segments and WAITs, which the guest's own state forgets. So `args_schema` (FR-33)
  validates the **value**, including under `enforce`; the idempotency key covers the value; and
  `agent_steps.tool_args` records what the tool was actually called with.
- **Failure:** a reference to a step that failed, was skipped at the authority gate, has no
  result, or lacks the path fails the step with `failure_class: "invalid"` (not retried). The
  tool is never called with the placeholder.
- **Default off.** With the flag on, the runtime's tool catalog tells the planner the form in one
  line, so an app's planner prompt does not need its own. **The runtime truncates no step result.**
  A reference carries exactly what the tool returned (the app's 2 KB research cut is its own
  `apps/search/syscalls.py`).
- The verifier's dot-path resolver moved to `AINDY/core/result_path.py` and is shared (no
  behaviour change).
