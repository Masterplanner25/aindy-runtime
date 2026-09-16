---
title: "Background Lease Fencing — Design"
api_version: "1.0"
last_verified: "2026-09-16"
status: current
owner: "platform-team"
---

# Background lease fencing — design

**`LEASE-FENCE-1`. DESIGN ONLY — nothing shipped. Proposal under `AGENT_WORKING_RULES.md` §3/§8:
it adds a column to `background_task_leases` (schema — contract bump, Alembic `0019`) and
changes what a leader-only job does when it is no longer leader.** The entry says "one integer
column and one comparison" and asks which leader-only job is least idempotent before fencing
anything. §2 answers that from source, and the answer is sharper than the entry expected: the
job whose safety argument *assumes one leader* is the one `CLAUDE.md` tells you not to guard
twice. §4 is the comparison; §6 is what not to build.

---

## 1. The finding, restated against `leadership.py` at HEAD

`try_acquire_lease` (`platform_layer/leadership.py`) is a `SELECT … FOR UPDATE` on the single
`background_runner` row: insert if absent, renew if ours, **take over if `expires_at <= now`**,
else follower. `BackgroundLeadershipElector` re-runs it every 20 s (`LEASE_HEARTBEAT_SECONDS`)
against a 60 s TTL, and a leader that loses the row learns so on its *next tick* and calls
`_stand_down_scheduler` → `scheduler_service.stop()`.

What that cannot do, stated precisely:

- **A job already running when leadership is lost runs to completion.** `stop()` stops the
  scheduler from *starting* jobs; a job mid-flight on its executor thread finishes on its own
  session with its own commits, as leader, after the row says it is not.
- **A leader that stalls (GC pause, disk stall, partition) is a leader until its next
  successful tick.** For a 60 s TTL and a 20 s heartbeat, a 45 s pause is enough for a follower
  to take over *and* for the stalled process to keep believing — and its jobs keep firing on
  that belief until the tick that fails.

Expiry bounds *how long* two leaders coexist. Nothing bounds *what the stale one writes*.

---

## 2. ★★ Which leader-only job is least idempotent — measured, and it is the one with a documented "do not guard again"

The scheduler registers thirteen runtime jobs plus app-registered ones
(`scheduler_service.py:275–413`). Read for what a *second* leader running the same job at the
same moment would do:

| Job | What it writes | Under two leaders |
|---|---|---|
| `scheduler_heartbeat_tick`, `scheduler_wait_tick` | drains the execution queue | Redis pop is atomic; in-memory queue is per-process — **no duplicate** |
| `scrape_scheduler_metrics`, `queue_backend_reconnect` | process-local | n/a |
| `cleanup_stale_logs`, `effect_record_cleanup` | status flips / deletes on filtered rows | **idempotent** |
| `queue_maintenance_process_delayed` | moves delayed → ready | atomic in the backend |
| `expire_timed_out_waits`, `expire_timed_out_wait_flows`, `recover_stuck_flow_runs` | fails / re-enqueues a run | guarded by `_claim_waiting_run`'s CAS and status filters — **a second run finds nothing** |
| `process_pending_memory_embeddings` | claims pending nodes → **paid embedding call** → upsert | a double claim costs money, not correctness; check the claim's `FOR UPDATE SKIP LOCKED` before relying on that |
| `deferred_async_job_retry` | re-dispatches a deferred `JobLog` | attempt is numbered before the lookup (`ASYNC-JOB-…-STORM-1`); a double dispatch **executes the handler twice** |
| **`recover_orphaned_approved_runs`** | **spawns a thread calling `execute_run` for every `approved` row older than 10 min** | **★ executes the agent run twice** — see below |

**The last one.** `execute_run` (`agent_runtime/execution.py:35`) guards on entry with
`if run.status not in ("approved",)` and sets `run.status = "executing"` at `:178` — a
**read-then-set on the same session, not a CAS `UPDATE … WHERE status='approved'`**. `CLAUDE.md`
records why that is correct and tells you not to add a second CAS: *"the 10-minute threshold
ensures the original thread is dead before re-dispatch fires."* **That argument holds for one
leader.** Two leaders whose five-minute jobs fire close together both select the same orphan
(the threshold is on the *row's* age, not on job spacing), both spawn a thread, and if the
second thread's entry read lands before the first thread's `executing` commit — a window of
seconds, since `_build_execution_memory_context` runs a memory recall between `:35` and
`:178` — the run executes twice: every planner and tool LLM call doubled, and tool effects
deduplicated **only** if both executions bind the same effect scope, which two fresh execution
units do not (`QUOTA-ACCRUAL-ORPHAN-1`: a unit is minted per dispatch). Narrow, and
**unobservable when it happens** — there is no row, log line or counter that says two leaders
dispatched the same run.

So the least-idempotent job is the one whose single-leader safety argument is written down.
That is exactly the shape the entry predicted — *"the failure is invisible when it happens"* —
and it is why the fence goes here first, **without touching `execute_run`**: the fence is a
guard on *leadership*, applied in the *job*, which is a different place from the CAS the
`CLAUDE.md` note forbids.

---

## 3. The mechanism

### 3a. One integer column

```python
# AINDY/db/models/background_task_lease.py (proposed; contract bump + Alembic 0019)
fence = Column(BigInteger, nullable=False, server_default="0")
```

`try_acquire_lease` sets it: **insert → 1; renew → unchanged; take over → `fence + 1`**. It
returns the hold rather than a bool:

```python
@dataclass(frozen=True)
class LeaseHold:
    owner_id: str
    fence: int
    expires_at: datetime
# try_acquire_lease(...) -> LeaseHold | None    (None = not held; truthiness preserved for callers)
```

The elector keeps the current hold; `background_leader_fence() -> int | None` exposes it
beside `background_leader_status()`. The Alembic migration adds the column under the
table-existence guard (`ALEMBIC-FRESH-DB-1`) and `downgrade()` drops it.

### 3b. One comparison, inside the writer's transaction

```python
class LeaseFenceLost(RuntimeError): ...

def assert_lease_fence(db, expected_fence: int, *, name=LEASE_NAME) -> None:
    """Call INSIDE a leader-only job's transaction, before its commit."""
    row = db.query(BackgroundTaskLease).filter(name==name).with_for_update(read=True).first()
    if row is None or row.fence != expected_fence:
        raise LeaseFenceLost(f"lease fence moved: held {expected_fence}, row {row and row.fence}")
```

**Why `FOR SHARE` (`with_for_update(read=True)`) and why inside the transaction — this is the
whole fence:**

- A takeover is a `FOR UPDATE` on the same row. While the job's transaction holds `FOR SHARE`,
  the takeover **blocks** until the job commits or rolls back — so a job that passed the check
  commits *before* anyone can become leader.
- If the takeover committed first, the fence the job reads is higher than the one it holds,
  and it rolls back — **the stale leader is refused, not asked to notice.**
- On SQLite (the unit harness) the lock clause is a no-op, exactly as `try_acquire_lease`
  already documents for itself; the comparison still runs, so the state logic is testable
  there and the lock semantics are an integration test on Postgres.

Every leader-electing profile requires Postgres, so the row lock is always real where it
matters — the same argument `LEASE-1` made for `FOR UPDATE`.

### 3c. Where it is called

```python
def _recover_orphaned_approved_runs() -> None:
    ...
    db = SessionLocal()
    orphans = db.query(AgentRun).filter(...).all()
    assert_lease_fence(db, background_leader_fence())   # ← raises → job logs, no dispatch
    ...spawn threads...
```

**What this closes and what it leaves, honestly.** The fence check proves leadership at the
moment the job *decides* to re-dispatch. A takeover between that decision and `execute_run`'s
`executing` commit on the spawned thread is still possible — the window shrinks from *"as
long as the stale leader stays stale"* (unbounded above by TTL + pause) to *"the length of one
job's dispatch"* (milliseconds). Closing the last window means a CAS in `execute_run`, which
`CLAUDE.md` declines; the fence makes that decline safe under two leaders instead of one.

### 3d. The signal

`aindy_lease_fence_refusals_total{job}` — incremented where `LeaseFenceLost` is caught. This
is the *only* evidence the fence ever fired, and the reason it is a metric rather than a log
line is green-check variant 10: a refusal happens on a scheduler thread, where `caplog`
cannot see it.

---

## 4. ★ Fence versus "check `is_leader` before each job" — why the cheaper option is not one

The obvious alternative is a job wrapper: `if not background_leader_status(): return`. It costs
no schema and reads no row. It is also **the same belief the stale leader already holds** —
`_is_leader` is the process's local boolean, updated on its next successful tick, which is the
tick a stalled process has not had. A wrapper that consults it is a second copy of the thing
that is wrong. The fence reads the *row*, under a lock that a takeover must contend for, inside
the transaction whose commit is the thing being guarded. That is the difference between "asked
to notice" and "refused", and it is the entire content of the Pi and Temporal references the
entry cites.

---

## 5. Phasing

| Phase | Ships | Note |
|---|---|---|
| 1 | `fence` column (contract bump, Alembic `0019`, baseline regen, the two `test_runtime_schema_contract.py` assertions), `LeaseHold`, `try_acquire_lease` increments on takeover, `background_leader_fence()` | schema — needs approval under §3; the `bootstrap-schema` head constant moves |
| 2 | `assert_lease_fence`, applied to `_recover_orphaned_approved_runs` and `deferred_async_job_retry`; the counter; the Postgres contention test | closes the entry — the two non-idempotent jobs are fenced |
| 3 (with `SYSEVENT_RETENTION_DESIGN.md`) | the prune job calls it before each batch | a prune by a stale leader is not harmful, but it is *audit* work — the fence makes "who deleted this" answerable |

**Store 4 (`nodus_lang_workflow`) has its own unfenced `claim` and this design does not reach
it** — it is nodus-side, filed in `docs/handoffs/NODUS_HANDOFF_workflow_store_migration.md`'s
family, and `DURABLE_STATE_OWNERSHIP_CONTRACT.md` records that its recovery is the guest's.

---

## 6. What not to build

- **Not a general fencing framework.** One row, one column, one function, two call sites. Pi
  fences a per-session writer; ours is a per-role leadership row; the analogue is a fence on
  that row and a check where leader-only writes happen — the entry's own scope note.
- **Not a fence carried on every write.** Temporal presents `RangeID` on every shard write
  because its shard *is* its store. Our leader-only writes are a handful of maintenance jobs;
  a check per job transaction is the proportionate form.
- **Not a CAS in `execute_run`** — declined in `CLAUDE.md`; this design is what makes that
  decline hold under two leaders.
- **Not fencing the idempotent jobs.** Ten of thirteen re-run harmlessly; a fence there is a
  row lock per job for nothing, and lock contention on the lease row *delays takeover*, which
  is the one thing the elector must stay fast at.

---

## 7. Decisions this design asks for (`DEC-NNN` in the implementing PR)

1. The fence lives on `background_task_leases.fence`, incremented **only on takeover** (§3a).
2. The check is `FOR SHARE` inside the job's transaction, never a wrapper on the local boolean (§3b, §4).
3. First consumers: `_recover_orphaned_approved_runs` and `deferred_async_job_retry`; the ten idempotent jobs are **not** fenced (§2, §6).
4. `execute_run` is untouched (§3c).

## 8. Tests that must exist

- **Negative control first (variant 9):** a Postgres test in which a second owner takes over
  the lease *between* the job's selection and its `assert_lease_fence` — the assertion must
  raise and the run must **not** be dispatched. Without this the fence has never been seen to
  refuse anything.
- Takeover increments the fence; renew does not; a fresh insert reads 1.
- A job holding `FOR SHARE` blocks a takeover until commit (integration, Postgres only; the
  SQLite copy pins the comparison).
- The counter moves on a refusal (read through `soak_harness.read_metric`, which raises on an
  unknown family).
- Mutation: change `!=` to `<` in the comparison and confirm the renew-after-takeover test
  goes red.
