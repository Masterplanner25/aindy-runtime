### Fixed — FR-42: a token minted for a non-agent run scope no longer trips a foreign key on its audit trail (#750; DEC-071)

- **Why it was wrong:** `mint_token` → `create_run_capability_mappings` inserted
  `agent_capability_mappings` rows FK'd to `agent_runs`; for a run scope that is not an `AgentRun`
  (a first-party consumer's session — found by `SUBSTRATE-WITNESS-1`'s first live run) the insert
  violated the FK, a broad `except` logged a WARNING, and the mint went on. The audit row was
  silently absent and reported as a failure.
- Now the run-scoped rows are written only for an `AgentRun`; any other scope gets the
  agent-type rows, an INFO line, and **`mapping_recorded: false` on the token** — informational,
  outside the HMAC, so tokens validate across runtimes with and without the key. The mint never
  fails on its audit trail. The FK stays: the row is owed per run (DEC-071).
