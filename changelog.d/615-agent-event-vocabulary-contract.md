### Fixed — the agent-event vocabulary is now one list, and pinned (#615)

The set of valid `AgentEvent.event_type` values existed in **two** places and neither described
reality. `AINDY/db/models/agent_event.py` held 9 names and had **zero importers**;
`agent_event_service.py` held 14 and was the one consulted; **22 were actually in use**. Eight
types — `AGENT_STEP_COMPLETED`, `AGENT_STEP_FAILED`, `COLLABORATION_STARTED`, `FAILED` and the
four `DELEGATION_*` — logged `Unknown event type` on every emission and were written anyway.

- **One canonical list**, `AINDY/agents/agent_event_types.py`, with all 22 names declared.
  `agent_event_service.AGENT_EVENT_TYPES` re-exports it, so existing imports are unaffected.
- **The stale copy under `AINDY/db/models/` is removed**, and a test prevents one returning there.
  That location is the *cause*: `scripts/check_schema_version.py` content-hashes every file under
  `db/models/`, so adding one string cost a `SCHEMA_CONTRACT_VERSION` bump, a baseline
  regeneration and two test-assertion edits — for a change with **no DDL**, since `event_type` is
  a plain `String(32)`. Every commit that added a type took the cheaper path, which was the
  rational choice each time. A vocabulary that costs a schema ceremony to correct stays wrong.
- **A contract test now pins it**, mirroring `SystemEventTypes`: a SHA-256 baseline
  (`tests/baselines/agent_event_contract.json`), an ORM column guard, and — the part a hash
  cannot do — a check that every type *emitted in source* is declared.

**Behaviour is unchanged.** `emit_event` still warns on an unknown type and still writes it:
losing an audit row is worse than recording one with an undeclared name. The guard is a test, as
it is for system events; it makes a new name intentional, while the warning makes it visible.

Operators will see **eight fewer spurious `Unknown event type` warnings** per affected run.
`SCHEMA_CONTRACT_VERSION` is `2026-09-10` (no migration — the ORM change is a deleted constant).
