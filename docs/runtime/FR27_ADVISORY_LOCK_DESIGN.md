---
title: "FR-27 — Advisory-locked idempotency gate (strict at-most-once under contention) — Design"
api_version: "1.0"
last_verified: "2026-09-11"
status: current
owner: "platform-team"
---

# FR-27 — strict at-most-once under contention, via an advisory lock

**`FR-27` / `IDEMPOTENCY-CONTENTION-UNVERIFIED-1` (app), the concurrency half of `IDEM-11`.
DESIGN ONLY — no runtime change ships with this document.** `AGENT_WORKING_RULES.md` §5
(concurrency) and §8 (proposal-first) both apply; this is the proposal they require. What is
asked is in §10.

---

## 1. What is already settled, and must not be re-litigated

- **The gate exists and is correct for sequential duplicates.** `IDEM-11` closed 2026-08-19:
  `AINDY_SYSCALL_IDEMPOTENCY` defaults on, the 8 `EXACTLY_ONCE` syscalls dedup on
  `(action_type, input, scope=execution_unit_id)`, a retry *within one run* replays. None of
  that changes.
- **The gate uses its own `_gate_db` session, never the handler's connection.** That is `#157`
  (txn poisoning) and is load-bearing; this design keeps it and adds nothing to the handler's
  session.
- **The idempotency key is `(action_type, input, scope)`** and the scope is only *hashed*, never
  cast to a type (`#157`-safe, IDEM-10). Unchanged.
- **A ledger failure degrades to AT_LEAST_ONCE, never blocks the syscall.** The correctness path
  is the ledger; observability is the counter; that ordering is not inverted.
- **The docstring has named this gap since MEB-1a:** *"strict at-most-once needs advisory
  locking, which has not landed."* `IDEMPOTENCY_CONTRACT.md` §167/§291 say the same. This design
  is that sentence, built.

## 2. The defect, reproduced (not taken from the report)

Measured 2026-09-11 on live Postgres (`docker-compose.test.yml`), `EXACTLY_ONCE` probe, one
scope, one payload, N callers barrier-released from separate threads each with its own
dispatcher and its own gate session — the existing `test_soak_idempotency_contention.py` driver.

**Fast handler (no sleep), three runs — handler executions:**

| callers | run 1 | run 2 | run 3 |
|---|---|---|---|
| 2  | 1 | 1 | 1 |
| 4  | 2 | 1 | 2 |
| 8  | 4 | 4 | 1 |
| 16 | 4 | 5 | 2 |

**Slow handler (200 ms) — the worst case, and the one a real effect resembles:**

| callers | degraded | replayed | handler ran |
|---|---|---|---|
| 2  | 1  | 0  | **2** |
| 4  | 3  | 0  | **4** |
| 8  | 7  | 0  | **8** |
| 16 | 15 | 0  | **16** |

**The bound is N, and it is reached whenever the handler outlives the insert race.** The fast
case only looks better because the winner's row commits before most losers' opening `SELECT`
lands, so they take `replayed` by luck. The app measured N of N on their stack (barrier-tight,
slow-ish handler); we measured the same shape. `IDEM-11`'s own "ran twice" was the fast case
with 8 callers — one sample of a variable the slow case pins at N.

**Mechanism** (`effect_ledger.py:_resolve_existing_row`, ~L147): a caller that finds a *live
pending* row (younger than `STALE_PENDING_THRESHOLD_SECONDS`) is counted `degraded` and returns
`(False, None)` → the dispatcher runs the handler. There is no wait and no lock, so **a pending
row protects nothing until it becomes `success`.** Every concurrent caller sees `pending`, not
`success`, and every one executes.

## 3. The fix, prototyped and measured

A transaction-spanning advisory lock keyed on the `action_id`, held across
`reserve → handler → complete`, so a loser **blocks** until the winner finishes and then resolves
the now-`success` (or reclaimable-`failed`) row instead of executing.

**A throwaway prototype was run end-to-end 2026-09-11** — advisory lock on a dedicated,
pinned, AUTOCOMMIT connection (see §4 for why that connection and not `_gate_db`), acquired
before `_resolve_effect_record`, released at every one of the gate's seven session-close sites.
Same driver, slow handler:

| callers | degraded | replayed | handler ran | wall |
|---|---|---|---|---|
| 2  | 0 | 1  | **1** | 0.38 s |
| 4  | 0 | 3  | **1** | 0.36 s |
| 8  | 0 | 7  | **1** | 0.53 s |
| 16 | 0 | 15 | **1** | 0.62 s |

**handler_ran = 1 at every width**, `degraded` → 0, the losers become `replayed`. The winner-fails
case (handler raises on the first attempt): **handler_ran = 2, reclaimed = 1** — one caller gets
the `error`, the next acquires the lock, sees the `failed` row, reclaims it and retries once; the
rest replay that success. Exactly the "one retry, not N" the report asked for. The full existing
PG soak (`test_soak_idempotency_contention.py`, `test_idempotency_gate_e2e.py`) and the dispatcher
/ ledger unit suites stayed green under the prototype. **The prototype was then reverted** — this
PR is the design, not the change.

## 4. The parts that are NOT one line — the reason this is proposal-first

### 4.1 The lock must span the handler, on a connection that is not `_gate_db`

Locking only the reserve/replay *decision* changes nothing: the loser re-reads a `pending` row
and is back where it started. The winner must hold the lock from before the reserve until after
`complete_effect_record`.

`pg_advisory_xact_lock` (transaction-scoped) is the obvious tool, but `_gate_db` **commits
between reserve and complete** (the reserve `INSERT` is committed so the row is visible to
losers), and a transaction-scoped lock releases on that commit. So the lock cannot live on
`_gate_db`. The prototype used a **separate, dedicated connection in AUTOCOMMIT** with
*session-scoped* `pg_advisory_lock` / `pg_advisory_unlock`, explicitly released. This keeps the
`#157` property (the handler's connection is never touched) and adds a second short-lived
connection per gated call **only while contention is real** — an uncontended call acquires
immediately.

**Open sub-decision for review:** session-scoped lock with explicit unlock at all seven close
sites (prototype; robust but must not miss a path — a leaked session lock outlives the request)
**vs.** a dedicated txn wrapping reserve+handler+complete on one connection with
`pg_advisory_xact_lock` (auto-released on commit/rollback/disconnect; no leak risk; but it
restructures the gate's commit sequence and holds one transaction open across the handler, which
is the shape `RT-MEMTXN-LEAK-1` warns about — acceptable *only* because it is a dedicated
connection, never the request's). **Recommendation: the prototype's explicit-unlock form**,
because it changes the commit sequence least and the leak risk is mechanical (a `finally`/context
manager covers all seven sites), but this is the sub-decision most worth a second opinion.

### 4.2 Handler wall-clock becomes the losers' wait — bounded and counted

A 300 s handler makes N−1 callers block up to 300 s. That is *correct* (they would otherwise each
run a 300 s duplicate), but it must be bounded: `wait_seconds` (prototype: 60 s) via
`pg_try_advisory_lock` in a poll loop, and a timed-out loser is **then** `degraded` —
honestly, because the machinery gave up — under a new label `degraded_lock_timeout`
(distinct from `degraded`, which after this means "lock not attempted: non-PG or flag off", and
from `degraded_gate_error`). So the worst case is restored to "bounded N under a pathologically
slow handler", never unbounded, and every such event is visible. **The 300 s syscall ceiling
(`COST-GOVERNOR-1` context) caps the handler, so `wait_seconds` should be ≥ that ceiling** or a
slow-but-valid handler's losers degrade needlessly — a number to set deliberately, not default.

### 4.3 Winner crash mid-handler — already handled, verified

The winner's handler raising leaves a `failed` row (the dispatcher's error path calls
`complete_effect_record(..., "failed", ...)`) and releases the lock. The next caller acquires,
reads `failed`, and `_resolve_existing_row` **reclaims** it (one retry). A winner *process* crash
mid-handler leaves a `pending` row and releases the session lock (Postgres drops it on
disconnect); the next caller acquires, reads `pending`-young, and — this is the residual — still
degrades until `STALE_PENDING_THRESHOLD_SECONDS`. **The app explicitly did not ask for
across-crash exactly-once, and this design does not claim it.** It is stated in the contract, not
half-built. (Closing it would mean treating a `pending` row whose lock is *not* held as
reclaimable — a `pg_advisory_lock` probe on read — which is a worthwhile phase 2 but out of
scope here.)

### 4.4 `durable_effects_active` (DUR-2) engages the gate for any syscall

The lock lands on the DUR-2 path too (a continued run's declaration-free at-most-once). That is
correct and desirable — a re-driven run's duplicate effects should serialise. **Interaction with
`FLOW-PARALLEL-1`:** two branches of one fan-out issuing the *same* effect in one superstep would
now serialise on the lock (correct — it is the same `action_id`) and each hold a gate connection
for the handler's duration. That is `SYSMAX-5`'s shape (scheduler/branch work sharing the DB
budget). It is bounded: the lock *serialises* them, so at most one handler-duration connection is
held at a time per `action_id`, plus the blocked losers' poll-loop connections (idle, cheap). Not
a new unbounded draw, but named so §10's budget analysis is honest.

### 4.5 Non-PostgreSQL

SQLite has no advisory locks; the whole unit suite runs on it. `acquire_effect_lock` returns
`unsupported` there and the gate behaves exactly as today (degrade under contention). **The soak
that proves the fix lives in `tests/integration/` (live PG) and must assert the backend is
PostgreSQL before asserting the outcome** — the standing rule about test-mode short-circuits: a
SQLite run would pass the assertion vacuously and prove nothing. A unit test asserts the
`unsupported` branch is taken on SQLite, so the degradation path stays covered.

## 5. What `degraded` means after this — a label contract

Today `degraded` conflates "a live concurrent call holds the slot" with nothing else. After this:

| label | meaning | expected under healthy contention |
|---|---|---|
| `reserved` | this caller won the insert, ran the handler | 1 per distinct `action_id` |
| `replayed` | resolved an already-terminal row (success cached, or post-lock) | N−1 |
| `degraded` | lock **not attempted** — non-PG, or the master flag off | 0 on PG with the flag on |
| `degraded_lock_timeout` | waited `wait_seconds`, gave up, ran anyway | 0 unless a handler exceeds the wait |
| `degraded_gate_error` | the gate machinery itself failed | 0 |
| `reclaimed` | took over a `failed`/stale row | 0 unless a winner failed |

**The operator signal inverts in a good way:** a non-zero `degraded` on PostgreSQL with the flag
on now means *misconfiguration*, and `degraded_lock_timeout` means *a handler is too slow for the
wait* — both actionable, where before a non-zero `degraded` was just "contention happened and we
gave up", which an operator could do nothing with.

## 6. Phasing

- **Phase 1 (this proposal, when approved):** the advisory lock as prototyped, behind a flag
  `AINDY_SYSCALL_IDEMPOTENCY_STRICT` (default **off** — the gate's behaviour is unchanged until an
  operator opts in, the same shape every `IDEM`/`DUR` step shipped with), the three new/retargeted
  counter labels, the PG soak asserting handler_ran==1 across widths, the winner-fails soak, the
  SQLite `unsupported` unit test, the contract doc update.
- **Phase 2 (separate, not asked here):** across-crash reclaim of a lock-less `pending` row (§4.3
  residual).

## 7. Not in scope / not asked

- Exactly-once across process crashes (§4.3). Stated as a non-guarantee.
- Any change to `AT_LEAST_ONCE` handlers.
- Any change to the `action_id` key, the scope, or the handler's session.
- Distributed locking across a cluster beyond what a single Postgres advisory lock gives (every
  instance shares one Postgres, so one advisory-lock namespace already covers the deployment —
  this is not a new distributed-systems surface).

## 8. Impact analysis (`AGENT_WORKING_RULES.md` §8)

**Invariants touched** (`docs/platform/governance/INVARIANTS.md`):
- **(17) per-request DB session isolation** — not violated. The lock lives on a *dedicated*
  connection, never the request's and never the handler's `_gate_db`; `#157`'s separation is
  preserved and extended.
- **Idempotency-gate invariants (IDEMPOTENCY_CONTRACT.md §59)** — strengthened, not changed: the
  key, the scope, the stale-recovery and the degrade-never-block rule all stand. Strict mode adds
  a *wait* before the degrade, nothing more.

**Schema / migration:** **none.** No column, no table, no Alembic revision. Advisory locks are
runtime state in Postgres, not schema. `SCHEMA_CONTRACT_VERSION` does not move.

**API contract:** **none.** The envelope is unchanged — a `replayed` caller gets the same
`{status: success, data: cached}` it gets today; only *more* callers reach it under contention.

**Config:** one new setting `AINDY_SYSCALL_IDEMPOTENCY_STRICT` (bool, default off) and
`AINDY_SYSCALL_IDEMPOTENCY_STRICT_WAIT_SECONDS` (int, default ≥ the 300 s handler ceiling).
Adding settings to `AINDY/config.py` is on §1's approval-required list — hence this proposal.

**Blast radius:** `AINDY/kernel/effect_ledger.py` (new lock helpers), `AINDY/kernel/syscall_dispatcher.py`
(acquire before resolve, release at the seven close sites), `AINDY/config.py` (two settings),
`AINDY/platform_layer/metrics.py` (one new label value), `docs/runtime/IDEMPOTENCY_CONTRACT.md`,
and the soak/unit tests. **No `AINDY/routes/`, no `AINDY/db/models/`, no `AINDY/db/database.py`.**

**Rollback:** the flag. Off = today's behaviour, byte-for-byte (the acquire is guarded by the
flag; `unsupported`/flag-off never takes the lock path).

## 9. The catalogue traps this must clear (so the soak is not vacuous)

- **Assert the mechanism before the outcome** (the test-mode short-circuit rule): the PG soak
  asserts `get_bind().dialect.name == "postgresql"` and that the flag is on *before* asserting
  handler_ran==1 — else a SQLite or flag-off run passes proving nothing.
- **The soak must not be stricter than the contract** (variant, `PERF-BASELINE-1`): strict mode's
  contract *is* handler_ran==1 on PG, so that exact assertion is legitimate here — unlike the
  non-strict soak, which correctly asserts only `>= 1` because degrade-to-N is allowed there. The
  two soaks assert different contracts and must stay separate.
- **Count work, not wall-clock** (variant, `PERF-BASELINE-1`): the assertions are on handler
  invocation count and counter deltas, never on the 0.3–0.6 s wall time, which is shared-CI
  noise.
- **Mutation-test the soak:** revert the acquire → handler_ran returns to N (red); drop the
  release → the next test's lock acquire times out (red); these are the two that prove the soak
  bites.

## 10. What is being asked

**Approval to implement phase 1 (§6) as prototyped (§3) and specified (§4–§5), behind the
default-off flag,** with the §8 impact analysis as the §8 record. Specifically a decision on:

1. **§4.1** — the explicit-unlock session-lock form (recommended) vs. the txn-scoped form on a
   dedicated connection. This is the one genuine design fork.
2. **§4.2** — the default for `..._STRICT_WAIT_SECONDS` (recommend ≥ 300 s, the handler ceiling).
3. That **across-crash exactly-once (§4.3) is explicitly out of scope** and documented as a
   non-guarantee rather than half-built.

No code ships until this is approved; the prototype that produced §3's numbers has been reverted.
