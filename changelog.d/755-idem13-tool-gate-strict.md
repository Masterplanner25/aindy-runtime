### Added — `AINDY_TOOL_IDEMPOTENCY_STRICT`: strict at-most-once on the tool seam (#755; IDEM-13)

- **Why it was wrong:** `FR-27` built the advisory lock that makes `EXACTLY_ONCE` hold under
  concurrency and wired it into the SYSCALL dispatcher only. `execute_tool` — the path a
  consumer's `EXACTLY_ONCE` tool actually runs on — never acquired it, so under contention the
  gate deduplicated nothing: a `pending` row is not a claim, every loser was counted `degraded`
  and executed. Measured on a live channel: five concurrent sends of one key delivered **five**
  messages against one ledger row. Reproduced at 8-way contention: 8 of 8 callers ran the tool.
- **What changed:** with `AINDY_TOOL_IDEMPOTENCY_STRICT=1` (**default off**, PostgreSQL only) the
  tool path takes the same lock across `reserve → tool → complete`; losers block and then replay.
  Same contention: 1 run, 7 replays, `degraded 0`. Wait ceiling
  `AINDY_TOOL_IDEMPOTENCY_STRICT_WAIT_SECONDS` (default **60s**, lower than the syscall path's 300
  on purpose); a timeout counts `degraded_lock_timeout` and proceeds rather than blocking.
- **Operators:** nothing changes unless you set the flag. If you do, watch
  `aindy_effect_gate_outcomes_total` — on PostgreSQL with the flag on, a non-zero `degraded` means
  misconfiguration rather than contention, and `degraded_lock_timeout` means a tool is slower than
  the wait. A blocked loser holds one pooled connection while it waits; size the pool for your
  duplicate fan-in.
- Across-process-crash exactly-once remains explicitly out of scope (inherited from FR-27).
