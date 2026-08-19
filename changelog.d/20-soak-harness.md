### Added — a concurrency + metric-readback harness, and the first soak that uses it

**This is what eight "soak, then flip" items were waiting on, and it was never a product
consumer.** Measured 2026-08-19:

- **The integration suite was entirely sequential** — zero `ThreadPoolExecutor`, zero
  `asyncio.gather`, zero concurrent drivers under `tests/integration/`.
- **No test read a metric** — zero `get_sample_value`, zero `generate_latest`, zero `.collect()`,
  against **52** registered metrics. `PERF-BASELINE-1` is misnamed: the instrument existed,
  nothing consumed it.

Everything else was already here: live Postgres and Redis on every PR, crash simulation, and the
flags themselves. "Soak" had been standing in for an apparatus nobody built, and because the word
sounds like it needs production it got deferred to a consumer that does not exist.

#### What landed

- `tests/integration/soak_harness.py` — `drive_concurrently()` (barrier-synchronised, surfaces
  every worker exception), `metric_window()` / `read_metric()` (before/after readback that
  **raises on an unregistered name** rather than reading zero).
- `tests/integration/test_soak_idempotency_contention.py` — the first concurrent test in the
  repository. N callers race the same `(action_type, input, scope)` against the `EXACTLY_ONCE`
  gate on real Postgres, asserting the handler runs **once**, the ledger holds **one** row, and
  the pool is not exhausted.
- `tests/unit/test_soak_harness.py` — the harness guards itself, no database required.
- An **advisory** CI step running the whole integration suite with `AINDY_SYSCALL_IDEMPOTENCY`
  and `AINDY_TOOL_IDEMPOTENCY` **on**, answering the other question: does enabling them break
  anything that was passing.

#### Why the existing e2e test was not already this

`test_idempotency_gate_e2e.py` turns the gate on and dispatches the same syscall **twice,
sequentially**. Sequential dedup is the easy half — the first call has already committed its
`effect_records` row before the second one looks. Contention is the risk the flag carries, and
nothing had ever exercised it.

#### ★ Two things this does not claim

- **No metric observes the idempotency gate.** `aindy_durable_effects` and
  `aindy_effect_attribution` are ContextVars, not metrics. An operator cannot currently tell
  whether the gate is doing anything, so a counter on gate hit/replay is a prerequisite for a
  production soak — and it does not exist. The soak asserts on handler-run count and DB rows
  instead.
- **The advisory step is advisory on purpose.** A soak that red-lines unrelated PRs on its first
  flake gets disabled within a week, and a disabled check is worse than an advisory one because
  it still looks present. Promote it only after it has been green across a release window **and**
  has been made to go red deliberately.

#### Backlog corrections found while measuring

- **`AUTHORITY-VALUE-1` is not soak-gated.** `aindy-apps-monolith`'s
  `apps/automation/syscalls/syscall_handlers.py:45` calls `child_context(capabilities=[capability])`
  with the *nested* syscall's capability while the parent holds the *outer* one, so a clamp
  intersects to empty. That is a caller fix in one file, then flip — no evidence required.
- **`NODUS-WARMPOOL-1` is already soaking.** The integration job has set
  `AINDY_NODUS_WARM_POOL: "1"` for some time; it has been running flag-on against real
  infrastructure on every PR.

Harness mutation-tested **6/6**; the first two versions of its central concurrency assertion were
killed by that process — one was proving the thread pool had enough slots, the other measured a
stagger placed after the barrier.
