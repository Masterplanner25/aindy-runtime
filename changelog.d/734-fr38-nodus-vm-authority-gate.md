### Added — FR-38: the authority gate reaches the `nodus_vm` backend (#734; DEC-068..070)

- **Why it was wrong:** `AUTHORITY-NEGOTIATION-1`'s census missed a fifth denial site —
  `execute_tool`'s own `check_tool_capability`, which is where a `nodus_vm` step is refused, in the
  pool worker, before any adapter code. Phases 1–2 (the declared variant, the WAIT gate) were wired
  on `agent_flow` only; on the backend the app runs a denied step failed the run with
  `capability.denied` ×3 and `on_denial="wait"` was never consulted.
- **Now, under `AINDY_AUTHORITY_NEGOTIATION` (still default off):** in the worker a denial negotiates
  one downgrade to a declared variant the token grants; failing that, a tool declaring
  `on_denial="wait"` halts the guest at the call and the segment chain parks the AgentRun
  (`waiting`, `wait_state.authority_gate`) mid-segment. The operator decides through the agent
  resume route, which now takes a body: `POST /api/agent/runs/{id}/resume`
  `{"decision": "skip" | "abort", "note": "…"}`. `skip` records the step `skipped` and re-drives
  the segment as a continuation (finished steps replay, the skip replays, the rest run); `abort`
  fails the run with the reason. A resume of a gate-parked run **without** a decision is refused
  (409) and the run stays parked; an unknown decision is refused (422) and recorded on the gate.
- `AUTHORITY_NEGOTIATED` is recorded from the worker; `aindy_authority_negotiation_total` counts
  worker resolutions in the api (they ride the reply, like `llm_usage`).
- **Consumer-visible:** a `skipped` step now replays on a continued run (it used to execute
  again); `agent_finalize_run`'s `COMPLETED` payload `steps_completed` counts successes only
  (a skipped step read as `1/1`) and gains `steps_total`. **App side:** its live resume surface
  should pass the decision body through to `resume_agent_run_runtime(payload=)`.
