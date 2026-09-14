### Fixed — a waiting flow run no longer holds a tenant concurrency slot (`ACTIVE-COUNT-WAIT-LEAK-1`) (#656)

`PersistentFlowRunner` took a tenant slot (`ResourceManager.mark_started`) when a run started and
returned it on success or failure — and **never on WAIT**. A run parked as a durable row kept one
of the tenant's `MAX_CONCURRENT_PER_TENANT` (default 5) slots for as long as it waited. Observed
live: after four parked waits, a read-only `GET /platform/flows/runs/{id}` returned 429. Under a
`hostile-third-party` profile this was a five-request self-DoS available to any tenant that can
start a flow.

- **A run now holds a slot exactly while it is executing.** The slot is acquired at node entry —
  `can_execute` then `mark_started`, once, for a fresh start and a resume alike — released by
  every WAIT (new `ResourceManager.mark_waiting`: same as `mark_completed`, including the
  `resource_available` capacity event, but the usage snapshot is kept because the run comes
  back), and released by completion/failure.
- **Admission is no longer re-decided per node with the run's own slot in the count.** At exactly
  the cap, the last-admitted run used to park itself on `resource_available` while still holding
  the slot it was being refused for. A refused run now parks holding nothing, and its node does
  not execute.
- The success-path release used to live inside the flow-completion memory-capture hook, which
  returns early when `user_id` or `workflow_type` is unset — a run with no workflow type took a
  slot and never returned it. The release is now unconditional on a terminal outcome.
- On WAIT the run's own ExecutionUnit is moved to `waiting` with its wait condition (previously it
  stayed `executing` while parked); a resumed runner recovers its EU and tenant from the run and
  moves a `waiting` EU through `resume_execution_unit` if the scheduler callback has not.
- **Behaviour change for operators reading `aindy:quota:concurrent:*` / `get_tenant_active`:** a
  parked run no longer counts. `AINDY_QUOTA_MAX_CONCURRENT` is unchanged — this is not a cap
  raise.
- Unit tests drive the real runner against a fresh `ResourceManager` with `is_testing` patched off
  (`can_execute` short-circuits under it); mutation-checked 4/4. Not yet re-run live.
