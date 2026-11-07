### Fixed — identity now comes only from authentication at every step of a call (DEC-113, #900)

- **Upgrade promptly if more than one user can sign in.** A later advisory will carry the details.
- **Check your code if it calls `sys.v1.job.submit`, `sys.v1.flow.run`, `sys.v1.flow.execute_intent` or `sys.v1.nodus.execute`.** A `user_id` inside `payload`, `initial_state`, `intent_data` or `input_payload` that differs from the caller is now refused with `TENANT_VIOLATION`. Scripts are always refused. In-process callers follow `AINDY_PAYLOAD_IDENTITY_DISPATCH`, so `observe` makes them count-only.
- `call_tool` in a Nodus script now needs a capability token issued for that run and user. A token that has only expired still replays recorded steps, and `execute_tool` still refuses it before any tool runs.
- A flow node uses an `agent_run_id` from its state only if the caller owns that run. Otherwise it ignores it, with a warning.
- `require_execution_unit` no longer reuses another user's unit, and returns `None` instead. `POST /apps/memory/nodus/execute` units now have a per-user source id. Rows written before this release keep their old id, and a repeated task gets a new unit.
- A user id that isn't a valid UUID now reads nothing on owner-only memory reads, where it used to get no owner filter. Calls with no user at all are unchanged.
