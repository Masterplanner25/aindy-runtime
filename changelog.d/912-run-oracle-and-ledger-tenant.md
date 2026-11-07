### Changed — someone else's agent run answers like a missing one (#912)

- `GET /coordination/runs/{id}/children` and the runtime's agent-run lookups (`get_agent_run_runtime`, `list_agent_run_steps_runtime`, `list_agent_run_events_runtime`) answered **403** for a run that exists but isn't yours and **404** for one that doesn't exist, so the difference revealed which run ids are real. Both now answer **404** with the same detail. An app route built on these, such as `GET /apps/agent/runs/{id}`, changes the same way.

### Fixed — the effect ledger checks a slot's tenant (#912)

- The ledger's key never included the tenant, so if two tenants' scopes ever collided, one would be handed the other's cached result. No such collision is known, because every scope has been owner-bound since 2.31.2. The ledger now refuses a slot recorded for another tenant, and the caller runs without the gate or refuses under `AT_MOST_ONCE`, as it does for any ledger failure. It's counted as `aindy_effect_gate_outcomes_total{outcome="tenant_mismatch"}`, which should always read 0. Rows with no recorded tenant behave as before.
