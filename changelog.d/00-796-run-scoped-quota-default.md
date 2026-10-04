### Changed — a run is the subject of the syscall cap by default (#796)

**Operators: read before upgrading.** `AINDY_RUN_SCOPED_QUOTA` now defaults ON. Only
`0/false/no/off` turn it off.

- **What changes.** A guest's `sys()` calls and every dispatch made during an agent run now count
  against that run's `AINDY_QUOTA_MAX_SYSCALLS` (default 100). This holds on both agent backends.
  Once a run is past the cap, its next dispatch is refused with `RESOURCE_LIMIT_EXCEEDED`.
- **Why.** Before, each of those dispatches created a one-call unit of its own, so the cap never
  applied to them (QUOTA-ACCRUAL-ORPHAN-1).
- **What to do.** If a legitimate run makes more than 100 syscalls, raise
  `AINDY_QUOTA_MAX_SYSCALLS`. To restore the old per-dispatch accounting, set
  `AINDY_RUN_SCOPED_QUOTA=0`.
- **Wall time is not summed per run (DEC-096).** The 300 s `AINDY_QUOTA_CPU_MS` cap is a sum of
  syscall durations, so applying it per run would refuse long LLM-heavy runs partway through. A run
  counts calls only.

### Fixed — a Nodus worker's syscalls now reach the run's budget (#796)

- On `nodus_vm`, an agent run's tool calls run in a worker process. That worker charged the Nodus
  execution's own unit and counted in its own memory, so none of the calls reached the run. A
  3-step run read 1 syscall on the run. Any guest `sys()` made in a worker had the same problem.
- Now the parent hands the worker the unit it is charging and its count so far. The worker checks
  against that running total, and the reply reports what it added (`quota_usage`), which the parent
  records. With a Redis backend the count is already shared, so the worker binds the same unit and
  reports nothing.
