### Fixed — syscall usage accrued on units nothing reaped; the pipeline never named its unit to the dispatcher (`QUOTA-ACCRUAL-ORPHAN-1`, #632)

- **Every syscall dispatched with no `execution_unit_id` minted a fresh unit and leaked its
  `UsageSnapshot` for the life of the process.** `make_syscall_ctx_from_tool` and the
  dispatcher's root-call fill both mint a `uuid4()`; the dispatcher's step-4 accrual created the
  snapshot and nothing cleared it. Measured: 120 MCP-shaped calls → 120 snapshots retained,
  tenant `""`. (The entry as filed said the calls shared a bucket keyed `""` and locked out
  after 100 — neither happens; the quota was *vacuous* for such callers, not tripping.)
  A root dispatch now reaps the unit it minted when it returns, and counts it:
  **`aindy_syscall_unowned_unit_total{syscall}`** is the list of callers to which no
  per-execution budget applies (today: the nodus worker's `sys()` seam, agent tools,
  `extension_worker` with no run id).
- **Routes were not exempt.** `ExecutionPipeline` claims, admits and reaps an `ExecutionUnit`
  but never told `SyscallDispatcher` which — so a route's dispatches minted their own units
  (5 × `POST /platform/syscall` → 5 orphan snapshots; the request's unit read
  `syscall_count: 0`). The pipeline now binds its unit and trace into the dispatcher's
  ContextVars for the handler's duration, the same bridge the distributed worker already
  builds. **Observable consequences:** a syscall envelope returned from a route carries the
  request's `trace_id` (equal to `X-Trace-ID`) and `execution_unit_id`; usage accrues on the
  request unit, so `MAX_SYSCALLS_PER_EXECUTION` / `MAX_WALL_TIME_MS` now bound a request's
  syscalls as one unit; `memory.write` provenance names the real unit. Idempotency-gate
  engagement is unchanged (it still keys on the id the caller's context arrived with).
- **`check_quota` no longer re-decides tenant admission for a unit already admitted.** It
  called `can_execute(tenant)` with the running unit in the active count, refusing every
  syscall of the last unit admitted at exactly `MAX_CONCURRENT_PER_TENANT` (5/5). Latent only
  because no dispatch snapshot carried a tenant; the bridge above would have made it live.
- **The MCP server owns an execution unit per tool call** (`ResourceManager.owned_execution`):
  admitted against the identity's concurrency limit, started, reaped on return. A refusal is
  returned in the dispatcher's envelope shape (`status: "error"`, `RESOURCE_LIMIT_EXCEEDED`).
- **The in-memory usage store evicts unreaped snapshots after `EU_KEY_TTL_SECONDS` (1 h)** —
  parity with the Redis backend, which always had that TTL. Swept at most once a minute,
  **counted (`aindy_resource_usage_evicted_total`) and logged at WARNING**. A non-zero count is
  work that re-opened a unit after its owner returned (an async job inheriting the submitting
  request's ids, by design) or a caller that names a unit it never reaps.
