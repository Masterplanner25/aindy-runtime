### Added — a refused agent step can park the run for an operator's decision (`AUTHORITY-NEGOTIATION-1` phase 2, #681)

- `register_tool(..., on_denial="wait")` (default `"fail"`, today's behaviour). When a tool is
  refused for lack of authority and no declared `degraded_variant` recovers it, the agent run
  now **parks** instead of failing: the `FlowRun` waits on `agent.authority.decision`, the
  `AgentRun` is `waiting` with a durable `wait_state`, and the accumulated steps survive.
- Resume with `POST /platform/flows/runs/{flow_run_id}/resume`,
  `{"event_type": "agent.authority.decision", "payload": {"decision": "skip"|"abort", "note": "…"}}`.
  `skip` records the step as `skipped` and continues; `abort` fails the run with your reason.
  There is no `grant` — the gate cannot widen authority; an unknown decision re-parks the run
  (recorded as `last_refused_decision`); a payload without `decision` is refused with `422`.
- Behind the existing `AINDY_AUTHORITY_NEGOTIATION` flag (default off). Counter labels added:
  `waiting`, `gate_skip`, `gate_abort` on `aindy_authority_negotiation_total`.
- **Fixed on the way, on the default `agent_flow` backend:** a failure that happened *after* a
  resume never reached the `AgentRun` (it stayed `waiting`/`executing`); the run is now marked
  `failed` and its execution unit synced, whichever thread finishes it.
