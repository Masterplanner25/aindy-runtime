### Removed — the boot-time EU-level WAIT rehydration; a flow's unit is finalised unconditionally (`EU-WAIT-SIGNAL-DEAD-1` follow-up, #685)

- `AINDY.core.wait_rehydration.rehydrate_waiting_eus` is gone, with its startup step. It
  re-registered every `execution_units` row in `waiting` with a scheduler callback that never
  committed, and every such unit belongs to a flow run whose own rehydration already resumes it
  (with a commit). Boot logs lose the `[rehydrate] Found N waiting EU(s)` / `WAIT rehydration
  registered N EU(s)` lines. Nothing an operator did depends on them.
- The condition code `wait_eus_rehydration_failed` is **retired**: still listed, never emitted.
  Dashboards filtering on it keep parsing; `flow_run_rehydration_failed` is the live one.
- **Fixed, latent:** a flow run that completed with no `user_id` or `workflow_type` on its row
  (a scheduler-resumed run can have neither) left its execution unit `executing` forever — the
  unit's `completed` transition lived inside a memory-capture hook that returned early. It is
  now unconditional. Route-started flows always carried both, so the app's live tables should
  show no change.
