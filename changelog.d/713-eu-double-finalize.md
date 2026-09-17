### Fixed — an agent run's execution unit is finalised once, and a verify-failed run's unit now finalises at all (`EU-DOUBLE-FINALIZE-1`, #713)

- Three sites mirrored a terminal `AgentRun` status onto its execution unit and only one guarded
  on the unit already being terminal, so every completed run on the `nodus_vm` backend logged
  `[EU] invalid transition completed→completed` at finalize. Cosmetic — the second write was
  refused.
- **Not cosmetic:** the chain passed the *run* status `verify_failed` straight through, which
  is not a unit status; that transition was refused every time, and a verify-failed run's unit
  stayed `executing` forever. `ExecutionUnitService.finalize_for_run_status` is now the one rule
  (run vocabulary → unit vocabulary; `verify_failed` / `cancelled` / `refused` → `failed`; silent
  when already terminal), called by all three sites. **Operators:** units of past verify-failed
  agent runs are still `executing` in the table; this release does not backfill them.
