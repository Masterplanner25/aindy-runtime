# Changelog

## Unreleased

_Nothing yet._

## 2.7.0 — 2026-09-02

### Security — a second `nltk` advisory, accepted as not reachable (`PYSEC-2026-3740`)

- `CVE-2026-81726` / `GHSA-8mgp-746c-j5xp` was published against `nltk` 3.10.3 — the version this
  release bumps *to*, hours after the bump — and **has no fix released**. It is carried as a
  documented `--ignore-vuln` exemption alongside the four already there.
- **Not reachable, verified rather than assumed.** `import nltk` has zero hits across `AINDY/`,
  `tests/` and `scripts/`; nltk arrives solely as a transitive dependency of `textstat`; and
  `TransitionParser`, the named affected component, has zero references anywhere in `textstat`.
  Nothing here supplies a model path at all, let alone a caller-controlled one.
- **Do not resolve this by reverting the pin.** 3.10.3 remains strictly better than 3.10.0: the
  advisory it clears had a fix, and this one does not.
- While confirming that, an *existing* exemption's stated reason turned out to be imprecise and is
  corrected in place. `PYSEC-2026-597` was accepted on the grounds that *"this codebase never
  calls `nltk.data.load()/find()`"* — true of `AINDY/`, false of the dependency that pulls nltk
  in, since `textstat` calls `nltk.data.find("corpora/cmudict")`. The acceptance still holds, and
  for a better reason: that argument is a hardcoded literal, so no attacker-controlled resource
  name reaches `url2pathname()`. The accepted-on date is unchanged.

### Changed — the scheduler no longer runs drained work on its own heartbeat (`FR-15` (a), thread mode)

- **`AINDY_ASYNC_SCHEDULER_DISPATCH` now defaults to `true`.** A queued item is handed to the
  thread pool instead of being executed inside the 1-second scheduler tick, so one slow flow no
  longer blocks every other queued item — or wait expiry, or stale-wait cleanup, which share that
  tick. Set `AINDY_ASYNC_SCHEDULER_DISPATCH=0` to restore the previous behaviour.
- *Evidence:* `tests/integration/test_soak_scheduler_dispatch.py` on live PostgreSQL — the drainer
  is released while dispatched work is still running, nothing is lost or duplicated, and
  concurrent resumes each obtain their own database session.
- **★ This reaches thread-mode deployments only, and that is the honest scope rather than a
  caveat.** The setting has no effect under `EXECUTION_MODE=distributed`, which
  `docker-compose.prod.yml` sets, because the distributed transport cannot carry the scheduler's
  resume callback. **If you run the production overlay, nothing about your dispatch behaviour
  changes and the serialisation described in `FR-15` is still present.** Fixing it there requires
  reconstructing the resume from `run_id` rather than carrying a closure — a build, still open.
- `aindy_execution_dispatch_total{mode="async"}` is how you confirm which behaviour a deployment
  actually has; reading the environment variable cannot tell you, since distributed mode overrides it.

### Changed — `nodus-lang` 5.1.0 → 5.9.0 (all three pin sites)

Eight releases. Read this before upgrading a deployment that runs guest Nodus code: three of the
fixes in the gap are **security** fixes, and none of them was obvious from the version distance.

- **A capability policy could be bypassed by spelling a call differently** (upstream #616).
  `agent_call` is governed by the `agent.call` capability; `agent_call_async` carried no
  capability at all, so a `DenyList("agent.call")` refused one spelling and permitted the other on
  the same agent under the same policy. Seven builtins could also be shadowed by a host
  `register_function` — including `chr` — because the "cannot override a builtin" guard read a
  hand-maintained name set that had drifted from the VM's actual dispatch table by seven entries.
- **A relocated workflow store fell outside the guest filesystem floor.** `DEFAULT_FLOOR` decided
  what counted as the runtime's own state by matching a literal `.nodus` path segment, so the
  *supported* way to move the store also moved it out of the jail — a guest write of
  `../relocated/pwned.txt` landed in the live run store while the same write to the default
  location was denied.
- **A graph response could name another request's graph** (upstream #584) — id, status and full
  task map including step return values. A cross-request leak on any server handling more than one
  caller, not merely a wrong label.
- **The bytecode cache could run a stale program.** The cache key was `sha256(abspath + mtime_ns)`,
  so any edit landing inside the platform's mtime resolution was invisible and the previous program
  ran. Entries now carry a hash of the source bytes.
- Plus closure-across-module fixes (#691, #696) that made a module-exported factory function or a
  callback passed into a step body execute against the wrong chunk — with five different symptoms
  depending on what happened to sit at that address, including silently running nothing.

**No runtime code change was needed.** Verified rather than assumed: all eight host functions this
runtime registers are still accepted (the tightened builtin-shadowing guard refuses none of them),
`NodusRuntime.__init__` still takes no `**kwargs` so a renamed confinement flag would raise rather
than silently unconfine a guest, every confinement argument it is given still exists, and the
`[mcp]` extra still resolves (`nodus-mcp` 0.1.3 requires `nodus-lang>=4.0.0` unbounded).

One upstream change is a **breaking** change that does not affect this repo: a named import of a
builtin name (`import { sleep } from "./mod.nd"`) is now refused rather than silently ignored. No
`.nd` source here uses that form.

### Changed — bump `nltk` 3.10.0 → 3.10.3 (PYSEC-2026-3726, #542)

- `nltk` is pinned to `3.10.3` at both pin sites (`pyproject.toml`, `AINDY/requirements.txt`).
  Versions before 3.10.2 carry a symlink-based arbitrary file read in
  `IPIPANCorpusReader.{channels,domains,categories,fileids}()` — those methods bypass
  `nltk.pathsec` validation entirely, so a symlink planted in the corpus root reads any file the
  process can reach (CVE-2026-62383 / GHSA-3hhw-38pf-pxj6).
- **No operator action.** Nothing in `AINDY/` imports `nltk`; it is pinned directly only to
  control the version `textstat` resolves to, and no code path in this runtime reaches the
  affected reader. A plain upgrade picks the fixed version up.
- Unlike the four nltk findings already carried as `--ignore-vuln` exemptions in
  `security-audit.yml`, this one has a fix released, so it is a bump rather than a fifth
  exemption. `3.10.3` rather than the `3.10.2` named in the advisory: same patch series,
  `textstat` requires `nltk` unbounded, and taking the tip avoids an immediate follow-up bump.
- Worth recording for the next time `pip-audit (OSV)` goes red with no diff to explain it: this
  advisory turned a required check red on an **unchanged `main`** (`a2fe25c` passed on 08-24,
  failed on 08-31), which blocked four unrelated Dependabot PRs and left a stale green on seven
  others. A dependency check reports on the state of the world, not the state of the branch.

### Changed — `pip-audit (OSV)` now runs on pushes to `main`, not only on PRs (#543)

- `security-audit.yml` gains `push: branches: [main]`. It previously ran on `pull_request` and a
  weekly `schedule` only, so the audit gated every PR *into* `main` and never gated `main` itself.
- **Why that mattered:** on 2026-08-31 `PYSEC-2026-3726` was published against a pinned `nltk` and
  turned the check red on an *unchanged* branch — `a2fe25c` passed it on 08-24 and failed it on
  08-31. It was noticed only because Dependabot PRs happened to be open and inherited the failure.
  With an empty queue, `main` would have sat red on a dependency CVE until the following Monday.
- **What a green check now means:** a passing `pip-audit` against `main` is a live statement about
  `main`, not a claim inherited from whichever PR last merged. Expect one extra job per merge.
- The trigger is deliberately **not** `paths:`-filtered — a filtered required check never reports
  on unrelated PRs and blocks them forever. `push` is scoped to `main`, so PR branches do not
  double-run.
- Also filed as variant 11 in the trusting-a-green-check catalogue in `CLAUDE.md`: the first entry
  where the check was *correct when it ran*. A dependency audit asks a question about the outside
  world, so its answer decays with no commit to mark the moment — and `gh pr checks` prints a
  duration but never a date, which makes a week-old pass indistinguishable from a fresh one.

### Changed — dependency pins refreshed

Every pin that moved this cycle had its release notes read, per the release checklist. The two
carrying security fixes have their own entries above (`nltk`, `nodus-lang`); these four are the
remainder, recorded so an operator upgrading can see the whole set rather than only the loud half.

- **`starlette` 1.3.1 → 1.6.0.** No breaking changes and no advisories. Two hardening fixes worth
  knowing if you serve files through the runtime: `FileResponse` now rejects inverted single-byte
  ranges and caps a request at 100 ranges. Also adds a `max_body_size` parameter — not adopted
  here, noted because it is the first request-size control the ASGI layer has offered us.
- **`setuptools` 83.0.0 → 84.0.0.** A major bump whose changes are confined to the distutils
  compiler surface — `Compiler.spawn` deprecated for `Compiler.call`, relocated compiler exception
  types, changed `runtime_library_dir_option` return shape. **This runtime builds a pure-Python
  wheel plus a Maturin-built Rust crate and touches none of it**; the constraint in
  `pyproject.toml` remains `>=83.0.0`, so nothing is forced on a consumer.
- **`pymongo` 4.16.0 → 4.17.0.** No breaking changes, no advisories. `bson.son.SON`'s `has_key`,
  `iterkeys` and `itervalues` are deprecated for removal in PyMongo 5.0.
- **`python-json-logger` 4.1.0 → 4.2.0.** No breaking changes, no advisories. Fixes a real
  side-effect bug: **logging a `dict` no longer mutates it** — `exc_info` and `stack_info` were
  previously added to the caller's dictionary. If you log a dict and then reuse it, it stops
  acquiring keys it never had.

### Fixed — distributed dispatch no longer silently discards work it cannot reconstruct

- `dispatch()` now raises `UndistributableWorkError` when work is routed to the distributed
  queue with no `log_id` in its context, instead of enqueueing a payload keyed on an id no
  worker can resolve. The refusal happens **before** anything is pushed.
- *Why this mattered:* `dispatch()` takes a callback. Under `EXECUTION_MODE=distributed` that
  callback is never carried — the runtime sends a job payload and the worker rebuilds the work
  by resolving `log_id` against `JobLog`. Without one, the old code manufactured an id
  (`... or eu_id or uuid4()`) precisely when there was none to use, and a worker treats an
  unresolvable job as **finished**: it logs `JobLog not found`, acknowledges the message rather
  than dead-lettering it, and reports success. The work vanished with no dead-letter entry, no
  retry, and no failed status — every observable signal said it completed.
- **No operator action, and nothing that works today is refused.** All three live job-submission
  paths pass `log_id`; that was the entire guarantee, and it was an accident rather than a check.
  The guard makes it a rule so the next caller to omit one gets an error at the call site instead
  of a silent permanent loss.
- Found while scoping `FR-15 (a)`, which would have been the first caller to hit it. Filed and
  fixed separately because it is a property of `dispatch()`, not of the scheduler.

### Added — the scheduler's async dispatch is its own switch, and it is observable (`FR-15` (a))

- **New: `AINDY_ASYNC_SCHEDULER_DISPATCH`** (default `false`) — may the scheduler hand a drained
  item to the thread pool instead of running it on the 1-second heartbeat tick? This is the half
  of `FR-15` that fixes the actual defect: `schedule()` is the only queue drainer and runs each
  item synchronously, so one slow flow starves every other queued item along with wait expiry and
  stale-wait cleanup.
- **`AINDY_ASYNC_HEAVY_EXECUTION` is now route-facing only.** It still gates `POST /agents` and
  the nodus execute route, both of which answer `202` queued instead of a result when it is on.
  It no longer influences scheduler dispatch. **Nothing changes for anyone by default** — both
  flags remain off, and with the new one off the scheduler path is byte-identical to before.
- *Why split rather than flip:* one variable meant two unrelated things, so turning it on to fix a
  scheduler defect would also have changed two HTTP response shapes without anyone asking for it.
  Same repair as `EVENTBUS-PUBLISH-LATCH-1` and FR-17's `AINDY_ASYNC_JOB_LOOP_CLOSURE`.
- **New metric `aindy_execution_dispatch_total{mode, eu_type}`** — whether an execution ran inline
  or went to the pool. Nothing observed that decision before; an operator could only read an env
  var, which cannot distinguish "the async path is on" from "it is on and nothing uses it".

### Fixed — the scheduler can no longer be pointed at a transport that would discard its work

- `AINDY_ASYNC_SCHEDULER_DISPATCH` **has no effect under `EXECUTION_MODE=distributed`**, and
  refuses it before even an explicit opt-in. Setting it on a production overlay is a documented
  no-op, not an error.
- *Why an operator should care:* in distributed mode the async path does not submit to a thread
  pool — it serialises a job payload and **drops the callback**. The scheduler's work *is* a
  callback, and there is no `JobLog` row for a worker to recover it from, so the worker would log
  `JobLog not found`, **acknowledge the message, and report success** while the resume never ran.
  The flow would sit in `waiting` forever with no dead-letter entry and no retry. That is worse
  than the starvation being fixed, which at least eventually runs. `docker-compose.prod.yml` sets
  `EXECUTION_MODE: distributed`, so this is the default production shape.

### Changed — what CI proves about dispatch

- `tests/unit/test_scheduler_async_dispatch_gate.py` (25 tests) pins the gate precedence, the
  distributed refusal, and — for the first time — `_decide_mode`'s eight type×priority
  combinations. That decision, which the whole `FR-15` diagnosis rests on, previously had no test.
- `tests/integration/test_soak_scheduler_dispatch.py` proves the drainer is released while
  dispatched work is still running, that no item is lost or duplicated, and that concurrent
  resumes each obtain their own database session on live PostgreSQL. Both suites are
  mutation-tested 5/5.


## 2.6.0 — 2026-08-22

**Six app-team feature requests (FR-17 through FR-22), two idempotency-gate corrections, and a `nodus-lang` bump. No schema change and no migration — verified against `v2.5.0..2.6.0`: nothing under `AINDY/db/models/`, `alembic/versions/` or `memory_persistence.py` moved, so this one really is a `pip install` upgrade.** The two entries below that need an operator decision rather than an install come first, as the protocol requires.

### Fixed — a liveness probe no longer persists a full health snapshot (FR-18, #517)

**Operators: read this before upgrading — the fix stops the growth, it does not reclaim what
was already written.**

Every successful `GET /health` wrote a `health.liveness.completed` SystemEvent whose payload was
the **entire health response**: 26 top-level keys, including `trusted_python_execution` (~52 kB
uncompressed), the deployment contract, the sandbox attestation and the full plugin inventory.
The write rate is set by a container healthcheck — **a timer, not traffic**. The published image
probes `/health` every 30s on its own (2,880 rows/day); a deployment whose compose adds its own
`/health` probe writes more. Measured on a real stack: **~98 MB/day, ~3 GB/month**, unbounded,
with no retention.

The app team found it when a `pg_dump` would not finish. On a dev stack with four accounts and
no real traffic: `system_events` at **3653 MB / 183,604 rows** against a 3795 MB database, of
which `health.liveness.completed` was **120,444 rows / 3317 MB — 99.6% of the database**.
`n_dead_tup` was 0, so this was not bloat and not a missing autovacuum; it was live, intended
data. `pg_dump --exclude-table-data=system_events` produced **17 MB**.

**What changed.** `/health` now records a **digest** — status, degraded domains, warnings, a
fingerprint of the posture blobs, and the byte size of the snapshot it did not store — and only
when something changed, on the first probe after boot, or once an hour. The full snapshot is
still available on demand from `GET /health/detail`. The route's own response is unchanged.

| | Before | Now |
|---|---|---|
| Payload per row | the whole ~28 kB health response | a few hundred bytes |
| Rows/day at the image's 30s probe | 2,880 | 24 + one per posture change |

Each row carries `changed_keys` — which posture keys moved — so a change is legible without
the snapshot. **Expect two or three rows immediately after a restart:** some posture providers
populate lazily, so a cold process registers real changes before it settles.

**Reclaiming the existing rows is an operator action.** Nothing prunes `system_events`, so an
upgraded deployment keeps whatever it already wrote:

```sql
DELETE FROM system_events
 WHERE type = 'health.liveness.completed'
   AND timestamp < now() - interval '7 days';
```

A plain `DELETE` leaves the TOAST pages allocated — follow with `VACUUM FULL system_events`
(takes an exclusive lock) or `pg_repack` to return the space to the filesystem.

**New environment variables**, all read per call, so none needs a restart:

- `AINDY_HEALTH_LIVENESS_EVENTS` (default `true`) — `0` makes a liveness probe a pure read.
- `AINDY_HEALTH_LIVENESS_EVENT_PAYLOAD` (default `digest`) — `full` restores the old payload.
- `AINDY_HEALTH_LIVENESS_EVENT_INTERVAL_SECONDS` (default `3600`) — heartbeat floor for an
  unchanged posture; `0` records changes only.

**New metric:** `aindy_health_liveness_events_total{outcome}` —
`persisted_boot|persisted_changed|persisted_interval|persisted_full|suppressed|disabled|failed`.
If `suppressed` stays flat while probes flow, change-detection is being defeated by a volatile
health field and the write rate is bounded only by the digest size — that is the tell.

**Consumers:** none. The event type had no reader in either repo before this change, which is
why the payload shape could move.

### Fixed — the idempotency gate had a second, uncounted path to `AT_LEAST_ONCE`

**Operators running `AINDY_SYSCALL_IDEMPOTENCY` (on by default since 2.5.0): the degradation
counter under-reported.** `aindy_effect_gate_outcomes_total{outcome="degraded"}` counted only one
of the two ways a call gets downgraded.

| Path | Meaning | Before | Now |
|---|---|---|---|
| `effect_ledger` — lost the insert race to a **live pending row** | contention; expected | `degraded` | `degraded` |
| `SyscallDispatcher` — the **gate machinery itself raised** | the gate is broken | **counted nothing** | `degraded_gate_error` |

Both drop the caller to `AT_LEAST_ONCE`, so a dashboard watching only `degraded` would have shown
a clean gate while calls were silently losing at-most-once. They stay separate labels because the
remediations differ: one says *you have contention*, the other says *investigate the gate*.

**Found in CI by the contention soak**, which asserts that a second handler run is never silent.
It failed with the handler having run twice while `degraded` stayed flat — the downgrade had come
through the dispatcher branch. The soak now asserts across **both** labels, because the property
that matters is *"a downgrade was never silent"*, not *"the contention path fired"*. Pinning it to
one label made a correct runtime look broken and would have let a real silent downgrade through
the other path.

If it ever fires again, the assertion message now says what to look for: **a third path to
`AT_LEAST_ONCE` that nothing counts.**

### Changed — `nodus-lang` 5.0.4 → 5.1.0 (#513)

`nodus-lang==5.1.0` across all three pin sites (`pyproject.toml`, `AINDY/requirements.txt`, and
the `Install MCP extra` CI step). `nodus-mcp` is unchanged at `>=0.1.3`, and it caps `nodus-lang`
only at `>=4.0.0` — unbounded above — so this is a one-repo bump, not a two-repo release train.

**The one behaviour change worth an operator's minute, and it is upstream's, not ours.** Before
5.1.0, `run_source(source, filename=...)` ran the **file** named by `filename` whenever such a
file existed — discarding the `source` the caller passed and returning `ok=True` with the other
program's output. Present since nodus v0.4.0 (upstream #521). `filename` is now purely a label; a
real path still resolves relative imports against its directory, and `run_file` is unchanged.

**This runtime was never exposed, and that is now asserted rather than read.** Every `filename`
reaching `run_source` is built by `NodusRuntimeAdapter.run_script` as `<nodus:eu:{id}>`, with
`nodus_worker` falling back to the same angle-bracket form; `<...>` names no file under any
working directory. Our own `run_file` reads the script itself and passes the *source* through the
same path. Two guards in `tests/unit/test_nodus_upgrade_contract.py` keep it that way — one pins
the upstream guarantee, one calls the adapter and fails if it ever passes a resolvable path.

*Worth recording because of the shape:* this is the same failure mode as `GUEST-CONFINE-1`'s
residual — behaviour depending on a process CWD the runtime never sets. There the worker inherited
the server's directory (`/home/aindy` in Docker, which holds `alembic/`). We escaped this one by a
formatting convention, not by a decision.

**New in the guest workflow DSL** (available to `.nd` scripts; the runtime does not consume it
yet): a step can carry a guard (`step ship after review when reached("approved")`) and declare
which dependency outcomes satisfy it (`with { on: ["failed"] }`); a `state` cell can declare how
concurrent writes merge and whether it is durable; every task now reports a terminal status
(`completed`, `failed`, `upstream_failed`, `skipped`, `omitted`, `cancelled`, `abandoned`) where
anything that never got a turn was previously just absent from the result; and a failed step
drains the run instead of tearing the scheduler down, so a timed-out step still gets its `finally`
blocks and its siblings finish.

Those first two are worked references for open runtime entries — declared per-cell merge policy is
what `FLOW-PARALLEL-1` says any fan-out fix must have (the flow layer is `state.update(patch)`,
last-write-wins, today), and the status vocabulary is `EFFECT-PARTIAL-1`'s three-outcome problem
solved one layer down. Neither entry changes here; they now have an implementation to point at.

### Fixed — `nodus_worker_pool` module docstring contradicted its own function (#513)

The module docstring still described `AINDY_NODUS_WARM_POOL` as *"Opt-in (default off)"* and
credited that default with bounding the `nodus-lang <= 5.0.2` shared-guest-memory exposure, while
`warm_pool_enabled()` ~200 lines below has said **default ON** since 2026-08-19. One file, two
answers — the `ISOLATION-DOC-STATUS-1` shape.

Not cosmetic: that docstring's standing rule is *"before enabling the pool after any dependency
bump, re-run the guest-memory isolation guard."* With the pool already enabled, re-running it is a
precondition of **every** dependency bump, not of a flag flip that has already happened. It was
re-run for this bump.

### Fixed — the effect gate had a **third** silent path to `AT_LEAST_ONCE` (#516)

**Operators running `AINDY_SYSCALL_IDEMPOTENCY` (on by default since 2.5.0): duplicate handler
runs were under-reported again, and this time by the most common route, not the rarest.**

`resolve_effect_record` opens with a `SELECT`. A caller that finds an existing row gets there by
one of two routes, and which one is decided purely by whether its `SELECT` lands before or after
the winner's `COMMIT`:

| Route | What the caller sees | Before | Now |
|---|---|---|---|
| Lost the `INSERT` race | `IntegrityError` → re-query → live `pending` | `degraded` | `degraded` |
| **Read the committed row** | the opening `SELECT` already returned `pending` | **counted nothing** | `degraded` |
| **Read a `failed` row** | the opening `SELECT` returned `failed` | **counted nothing, and did not reclaim** | `reclaimed` |

Both of the bottom two run the handler a second time. Neither moved
`aindy_effect_gate_outcomes_total`, which is the only signal an operator has that `EXACTLY_ONCE`
did not hold.

**This is the larger half of the duplicates, not an edge case.** Under contention most losing
callers do not race the insert at all — they arrive slightly later and read the committed
`pending` row. So the counter was reporting the *rarer* route and silently dropping the common
one.

**Also fixed, on the same path:** reading a `failed` row skipped the reclaim, so the row kept the
previous attempt's attribution and `created_at` while a new attempt ran against it. That left its
staleness clock running from the *first* attempt, and left the slot marked `failed` during
re-execution — so a third caller arriving in that window also fell through uncounted. It is now
reclaimed exactly as the race path already did: `pending`, clock reset, re-attributed.

**Root cause worth recording: the decision existed in only one of the two places that reach it.**
It was written for the `IntegrityError` branch and never mirrored for the direct read, which then
fell through to a bare `return False, None`. It is now one `_resolve_existing_row` helper called
from both, so the two cannot diverge again.

**How it was found, and why it took three rounds.**
`test_the_gate_degrades_to_at_least_once_under_contention` fired the exact message it was written
to carry after the *second* fix (#511): *"look for a THIRD path to `AT_LEAST_ONCE` that nothing
counts."* It fired on a docs-only PR.

★ **The soak could only ever catch this by luck, and that is the more transferable lesson.** Its
degradation assertion is guarded by `if len(runs) > 1`, so a run where the threads happen not to
collide skips the assertion and reports green — `trusting-a-green-check` **variant 9**, *green
because there was nothing to catch*. A sibling PR containing the same commit passed the same job
for exactly that reason, which is why "re-run it until it goes green" would have laundered the
finding rather than fixed it.

The regression guard is therefore **deterministic and sequential**, in
`tests/integration/test_effect_ledger_gate_accounting.py` — no concurrency is needed to
demonstrate any of this, which is itself the point. Mutation-tested: reverting the fix fails the
two bug tests and correctly leaves the liveness control and the replay test passing.

### Fixed — async jobs now record that they started and finished (FR-17, #518)

`emit_system_event` refuses any `execution.*` event emitted with neither an execution pipeline
nor the async-execution context active. Two async-job sites tripped that guard, and because the
emitter catches and logs, the rows simply never existed:

- **Submission.** `submit_async_job` emits the job's root `execution.started`. Submissions that
  come from a route have a pipeline and were fine; submissions from a scheduler tick, the
  event-bus subscriber thread or an app `bootstrap.py` have none, and were dropped with
  `WARNING [AsyncJob] … ExecutionContract violation`. Reported by the app team on a live stack.
- **The worker thread.** `_execute_job_inline` activated that context only when
  `AINDY_ASYNC_JOB_LOOP_CLOSURE` was set — **off by default** — so with the default settings
  *every* async job's `execution.completed` / `execution.failed` was discarded. Async traces
  started and never ended.

Both now declare the async-execution boundary, via a new `async_execution_scope()` context
manager. The events were not renamed to a non-`execution.*` type the way `scheduler.queued` was:
`_ensure_root_execution_event_id` and `_has_existing_execution_started` locate an async job's
trace root by `type == execution.started`, so a rename would trade a missing row for a broken
trace root.

**Changed meaning of `AINDY_ASYNC_JOB_LOOP_CLOSURE`.** It no longer decides whether an async
job's execution events are recorded — only whether each job emits a `SCORE_COMPUTED` record and
joins the Infinity loop, which is what its name says. One flag was carrying both an operator
preference and a runtime latch; that is the shape `EVENTBUS-PUBLISH-LATCH-1` was split to avoid.

**What operators will see:** more rows in `system_events` for async jobs — one `execution.started`
per submission that previously produced none, and a terminal `execution.*` per job where the
default previously produced none. Memory capture follows persistence exactly as it already did
for route-driven submissions; `RUNTIME_INTERNAL_TASK_NAMES` still refuses to capture the
runtime's own maintenance jobs, so the RT-MEMTXN-LEAK-1 cycle stays cut.

### Fixed — a route's deliberate 4xx is no longer replaced by a 500 (FR-20, #520)

A route registered under the execution contract that raised `HTTPException` **before** entering
the pipeline had its status discarded: the guard converted every endpoint exception into a
`RouteExecutionViolation`, which surfaces as a 500. A stale link that should 404 returned 500, so
the user-visible symptom of an app contract slip was a wrong status code rather than a recorded
violation — and a client cannot tell "rejected" from "the server broke" by a 500.

The runtime already disagreed with itself here: an `HTTPException` raised by a **dependency**
passed through with its status (401 stayed 401), while the same exception from the endpoint body
became a 500. The two now agree.

**The violation is still recorded — it just stopped being recorded in the status code.** That was
the part worth getting right: before this, the 500 was the *only* evidence a violation occurred,
so preserving the status without somewhere else to put it would have traded a wrong status for a
silent one. New metric:

```
aindy_route_contract_violations_total{route, outcome}
  outcome=status_preserved   # a deliberate HTTPException, now passed through intact
  outcome=converted_500      # anything else — still a RouteExecutionViolation
```

plus an ERROR log naming the route and the outcome. Only a deliberate `HTTPException` is
preserved; an unexpected exception from a managed route is still a violation and still a 500.

Both halves of the path had to change together — the route guard and the contract middleware —
because the middleware re-raises independently. Reverting either one alone puts the 500 back,
which is now pinned by a test.

### Added — responses now say whether their body is the execution envelope (FR-19, #521)

Routes that pass through `ExecutionPipeline` return `{status, data, trace_id, duration_ms}`;
every other route returns a bare body. Both share the same URL space and **nothing on the wire
told them apart**, so every consumer had to carry per-route knowledge of whether that route
happened to enter a pipeline — knowledge obtainable only by trying it.

The app team reports this as the dominant defect class of their entire live-verification phase:
five defects on five surfaces, ~40 `safeMap prevented crash` lines inside `@aindy/ui-kit`, fixed
eleven times in client code. The failure signature is why it cost so much — an envelope where a
list was expected has no `.length`, so the empty-state branch does not fire either and the
surface renders **blank, with no error at all**.

Enveloped responses now carry:

```
X-AINDY-Envelope: v1
```

**Client rule:** unwrap `data` when the header is present, use the body as-is when it is not —
one helper instead of one decision per module. The header is deliberately **absent** on error
responses, handler-built `Response` objects, and routes with a registered response adapter,
because those bodies are not the envelope; absence means "not enveloped", never "unknown".

`X-Trace-ID` cannot serve this purpose — middleware sets it on every response.

**Also fixed, and it would have made the above useless: none of the runtime's response headers
were readable by a browser client on another origin.** `allow_headers` governs the *request*
direction, and a browser exposes only the CORS safelist unless the server names the rest. The
CORS middleware now sets `expose_headers` for `X-AINDY-Envelope`, `X-Trace-ID`, `X-Request-ID`,
`X-EU-ID`, `X-API-Version` and `X-Version-Warning`. `X-Trace-ID` has been documented as a
debugging aid all along while being unreadable from the browser doing the debugging.

Additive: no body shape changes, no existing consumer breaks. Contract documented in
`SDK_CONTRACT.md` and `UI_CONTRACT.md`.

**Not closed by this:** making every `/apps/*` route enter the pipeline is app-side work, and it
is their preferred end state. This settles the half only the runtime can — that a client can find
out which shape it received.

### Added — Webhooks and Dead-Letter Queue panels in the operator console (FR-21, #522)

The runtime serves an operator SPA at `/platform/`. The app team independently grew a second
one beside it and offered it back rather than keep maintaining two — this adopts the part that
belongs here.

**The gap was two panels, not five.** They named five as "clearly runtime"; the console already
shipped four of them (flow engine, agent registry, admin users, executions). The two it did not
expose were **webhooks** and the **dead-letter queue** — and their check of our served bundle
found zero occurrences of `webhook`, `dlq`, `dead-letter` or `drain`, so these were capabilities
with no operator surface rather than duplicated implementations.

Both drive runtime-owned routes:

| Panel | Routes | Actions |
|---|---|---|
| Webhooks (`/platform/webhooks` in the SPA) | `GET/POST /platform/webhooks`, `DELETE /platform/webhooks/{id}` | list, create, delete |
| Dead-Letter Queue (`/platform/dead-letters`) | `GET /platform/queue/health`, `GET /platform/queue/dead-letters`, `POST …/drain`, `POST …/{job_id}/replay`, `DELETE …/{job_id}` | inspect, replay, delete, drain |

Every destructive action is confirm-gated in place, and both panels are admin-gated client-side
to match the server-side scope (`webhook.manage` / `platform.admin`).

**Note the DLQ ambiguity, because two runtime records share the name:** this panel is the *async
job queue's* dead-letter queue, whose jobs can be replayed because their payload was preserved.
`GET /platform/observability/dead-letter` is a different record — dead-lettered **flow runs** —
and is not what this panel shows.

The SPA's paths for these routes live in `platform/src/api/_routes.js` as `RUNTIME_ROUTES`
rather than in `@aindy/ui-kit`'s `ROUTES`, so a panel does not wait on a ui-kit release. Fold
them in on the next one. `UI_CONTRACT.md` lists them as canonical either way.

**Operators: a UI change reaches no container until a release is cut and the Dockerfile pin is
bumped** — the SPA ships as package data inside the wheel. A running container shows the last
*released* console.

### Added — the runtime publishes its HTTP route inventory, and CI keeps it current (FR-22, #524)

`AINDY/route_inventory.json` lists every `(method, path)` the runtime serves in the
`runtime-only` boot profile, with OpenAPI tags. It ships inside the wheel, so a consumer reads
the surface for the version they installed without booting anything:

```python
import json
from importlib.resources import files

inventory = json.loads(files("AINDY").joinpath("route_inventory.json").read_text())
```

**Why this exists.** The app team's API reference documents ~51 runtime-owned routes, and their
guard is scoped to `/apps/*` — so the runtime half of their file was a curated inventory nothing
checked, accurate when written and free to drift afterwards. They deliberately did not extend
their guard over our routes: an app-side mechanism policing a runtime-owned surface makes the
app responsible for something it does not own. So the runtime guards its own.

`scripts/check_route_inventory.py` regenerates the file; `--check` fails when it is stale, and
`tests/unit/test_route_inventory.py` runs that comparison in `Runtime Contracts` — in **both**
directions, because a route silently leaving the published surface matters more to a pinned
consumer than one appearing.

**★ Correction worth acting on if you consume our routes: `/apps/*` is not an app-ownership
boundary.** 35 routes under that prefix — coordination, memory, agent — are served by the
runtime with no plugins loaded. A guard treating `/apps/*` as "the app's surface" is wrong about
a third of it. Subtracting this inventory from a booted app's surface gives the genuinely
app-owned set without curating one by hand.

Two things absence means precisely: the legacy alias surface
(`AINDY_ENABLE_LEGACY_SURFACE=true`) is excluded — the inventory publishes supported routes, not
compatibility shims; and there is no version field, because the file is committed and a stamped
version would make every release bump edit it. **A removal from this file is a breaking change
for anyone pinned to it.**


## 2.5.0 — 2026-08-20

**★ Read this before upgrading. Two things need an operator decision, not just a `pip install`.**

**1. This release changes the runtime-owned schema.** `execution_units` gains three additive
nullable columns (Alembic **`0017`**, schema contract **`2026-08-19`**). Per `FR-14`, a bare
`aindy-runtime bootstrap-schema` now exits **3** — and under `set -e` with
`restart: unless-stopped` that is a **crash loop, not a warning**. Existing deployments must run
`bootstrap-schema --reconcile`, or branch on exit code 3. A fresh database needs nothing.

**2. Three execution defaults moved from off to on.** Each has an off switch, and each is a
behaviour change on upgrade:

| Flag | Now | Off switch | What changes |
|---|---|---|---|
| `AINDY_CHILD_CONTEXT_CLAMP` | **on** | `=0` | a nested syscall context can no longer widen its capability grant |
| `AINDY_SYSCALL_IDEMPOTENCY` | **on** | `=0` | 8 `EXACTLY_ONCE` syscalls dedup within a run |
| `AINDY_NODUS_WARM_POOL` | **on** | `=0` | Nodus executions reuse warm workers instead of a fresh subprocess each time |

Every widening has been logged at WARNING since 2026-08-16 — `grep` your logs for
`child_context WIDENED authority` before upgrading to see whether the clamp will affect you.

**What this release is.** Two isolation programs reached working state. `EXEC-ENV-BIND-1` gave an
execution unit a way to *declare* the environment it needs and the Nodus guest a way to *ask* for
one; `TOOL-SEAM-ISOLATION-1` closed all four of its steps, so a tool that declares an isolation
class now runs **out of process**. Alongside them, the apparatus that had been missing all along:
a concurrency + metric-readback harness, which is what finally let three long-deferred flags be
flipped on evidence rather than argument.

**No dependency pins moved this release.** No route began enforcing a new scope, so no caller
loses access.

### Changed — a nested syscall context can no longer widen its capability grant (AUTHORITY-VALUE-1)

**Operators: this is a security default moving to ON.** `AINDY_CHILD_CONTEXT_CLAMP` now defaults
**true**. `child_context()` narrows the parent's capability grant and never widens it; a widening
request is dropped and logged at WARNING. Set `AINDY_CHILD_CONTEXT_CLAMP=0` to restore the
previous behaviour, in which the widening was granted and only warned about.

**What this changes for you.** If any of your syscall handlers dispatch a nested syscall using
`child_context(context, capabilities=[...])` with a capability the *parent* context does not
hold, that nested dispatch will now be denied — an error envelope, not an exception. Every such
widening has been logged at WARNING since 2026-08-16, so `grep` your logs for
`child_context WIDENED authority` to see whether you have any before upgrading.

#### Why the default moved, and why the previous reasoning was wrong in its conclusion only

The flag shipped opt-in on one claim: clamping intersects `aindy-apps-monolith`'s
`_dispatch_owner_syscall` pattern to the **empty set**, and therefore "denies a call that works
today." **The mechanic is real and is still pinned by test.** What was never measured is what the
empty set costs.

Measured against the monolith:

| | |
|---|---|
| Functions that widen via `_dispatch_owner_syscall` | **19** |
| Registered — reachable by the dispatcher | **1** |
| Unregistered — dead code a clamp cannot break | **18** |

The one live caller widens for an **optional** cached-suggestions lookup, wrapped in
`try/except` with a full recomputation beneath it. Denied, it logs a warning and recomputes.

**Count: 1 degradation, 0 outages.** This repository's own rule is to tighten a boundary on a
count rather than an argument, and the count supports the flip.

#### The transferable part

An **executable fact** — the intersection is empty — had an **inference** layered on it — therefore
an outage — and the inference was never re-measured for three months while the fact was cited as
though it carried the conclusion. The test keeps the fact and now explicitly refuses the
inference.

Three tests added, including the one the original reasoning never checked: a starved context makes
`dispatch` return an **error envelope** rather than raising, which is the entire reason a caller's
`try/except` degrades instead of failing.

### Added — `ExecutionEnvironmentSpec`: an execution unit can declare the environment it needs (EXEC-ENV-BIND-1, phase 1)

**★ Operators: this release changes the runtime-owned schema, and that has a deployment
consequence you must handle before upgrading.**

`execution_units` gains three additive, nullable columns (`env_spec`, `env_applied`,
`env_evidence_class`; Alembic **`0017`**, schema contract **`2026-08-19`**). Per `FR-14`, an
additive runtime column makes a bare `aindy-runtime bootstrap-schema` exit **3**
(additive-reconcile-required) — and under `set -e` with `restart: unless-stopped` that is a
**crash loop**, not a warning.

**Existing deployments must run `aindy-runtime bootstrap-schema --reconcile`, or branch on exit
code 3.** A fresh database needs nothing; `create_all` produces the columns. This is the first
release since the exit-code work landed where the condition actually fires, so it is also the
first time `Upgrade Path Guard`'s main job is doing real work rather than passing trivially.

#### What it is

The runtime owned a provider abstraction — `SandboxRunner`, three implementations, a
certification ladder — and no vocabulary in which an execution unit could *request* anything from
it. `ExecutionUnit` stored `wall_time_ms` / `memory_bytes` / `syscall_count`, but those are
**measured actuals**. Nothing recorded what an execution was supposed to be allowed to do, so
*"was this the containment you asked for?"* had no answer for any individual run.

`ExecutionEnvironmentSpec` (`AINDY/core/execution_environment.py`) is the request record. Three
orthogonal axes rather than a trust level or a bag of booleans:

| Axis | Fields |
|---|---|
| **visibility** — what it may see | `filesystem {mode, roots}`, `env {mode, allow}` |
| **authority** — what it may do | `network {mode, egress_scope}`, `processes {subprocess}` |
| **resources** — how much it may use | `wall_time_ms`, `memory_bytes`, `syscalls` |
| | `min_assurance`: `insecure-dev` \| `container-grade-sandbox` \| `strong-sandbox-tier` |

Pass `env_spec=` to `require_execution_unit`. The spec is clamped to the host floor, the host's
assurance class is resolved, and the unit is **refused** if the host cannot meet the declared
minimum — raising `ExecutionEnvironmentUnsatisfiable` *and* writing a terminal `refused`
ExecutionUnit row.

#### What it does NOT do

**It confines nothing.** Phase 1 is declare / refuse / record; each seam applies its own
environment in a later phase. **A populated `env_applied` is not evidence that an execution was
confined — `env_evidence_class` is the field that says whether it was**, and on the default dev
runner it reads `insecure-dev/no-isolation-guarantee`.

Nothing changes for existing callers. `env_spec` defaults to `None`, every pre-existing row is
`NULL`, and `NULL` is defined to behave exactly as before these columns existed.

#### Two properties worth knowing

- **A spec may only ever narrow.** The effective spec is the intersection of the declared spec
  and a host floor; a caller may ask for *more* confinement and never for less, because a
  caller-supplied value is attacker-influenced in exactly the way `AUTHORITY-VALUE-1` describes.
  Every widening attempt is logged at WARNING so the exposure is countable. **Unlike that entry's
  clamp this one is not behind a flag** — no caller supplies a spec today, so there is no
  compatibility argument for shipping a security default off.
- **Refusal deliberately breaks the non-fatal contract, and only here.**
  `require_execution_unit` returns `None` on failure and its callers are documented not to block
  on that, so `ExecutionEnvironmentError` gets an explicit re-raise guard placed *before* the
  broad handler — the same shape `SyscallContractViolation` needed in `SyscallDispatcher`. A
  refusal swallowed by a broad handler is worse than no refusal, because the row says `refused`
  while the work ran.

Design and phasing: `docs/runtime/EXECUTION_ENVIRONMENT_SPEC_DESIGN.md`. 32 tests across two
suites, mutation-tested **7/7** including a liveness control that fires if refusal is disabled
entirely.

### Changed — two execution defaults are now ON: the idempotency gate and the warm Nodus worker pool

**Operators: both change runtime behaviour on upgrade. Each has a documented off switch.**

| Flag | Was | Now | Off switch |
|---|---|---|---|
| `AINDY_SYSCALL_IDEMPOTENCY` | off | **on** | `AINDY_SYSCALL_IDEMPOTENCY=0` |
| `AINDY_NODUS_WARM_POOL` | off | **on** | `AINDY_NODUS_WARM_POOL=0` |

Both accept `0`, `false`, `no`, `off` (case-insensitive), each pinned by a parametrised test —
a security or execution default that cannot be turned off is a different kind of problem.

---

#### `AINDY_SYSCALL_IDEMPOTENCY` — what it does, and precisely what it does not guarantee

The gate dedups an `EXACTLY_ONCE` syscall on `(action_type, input, scope)` where the scope is the
**execution unit id**. So a retry *within one run* replays the cached result instead of
re-executing, and **two legitimate calls in different runs are untouched.** That scoping is what
makes this safe to default on.

Eight syscalls declare `EXACTLY_ONCE` and are affected: `memory.write`, `memory.link`,
`event.emit`, `flow.run`, `flow.execute_intent`, `nodus.execute`, `job.submit`, `agent.undo`.

**★ It is NOT exactly-once under contention.** When the gate loses the insert race against a live
pending row it degrades to `AT_LEAST_ONCE` for that call and logs a warning — strict at-most-once
needs advisory locking. Measured: **8 concurrent identical calls ran the handler twice.** Watch
`aindy_effect_gate_outcomes_total{outcome="degraded"}`; a deployment where that is a meaningful
fraction of `reserved` has a weaker guarantee than the name suggests.

**★ This does not close `IDEM-12`.** `undo_run_effects` selects effects by `status == "success"`
and never consults `effect_reversals`, so a deliberate second `sys.v1.agent.undo` still
re-invokes every compensator. The gate is defence-in-depth, not the fix — and making reversal
correctness depend on an env var is the shape `IDEM-10` already paid for.

#### `AINDY_NODUS_WARM_POOL` — soaked before flipping

Reuses a bounded pool of warm worker subprocesses so plugin cold-start is paid once rather than
per execution. **Any warm-path failure falls back to a fresh subprocess**, so enabling it cannot
make execution worse than the path it replaces — asserted at the adapter, where that claim lives.

**The prior evidence was not what it looked like.** CI had set this flag for months, but the
integration suite is *sequential*: it showed the pool serves requests, not that it serves
*concurrent* ones correctly. Every pool test ran against **fake** processes, and end-to-end was
deferred to "app-side PG-tier integration" — a consumer that does not exercise it.

`tests/unit/test_soak_warm_pool_contention.py` closes that with six concurrent callers against a
pool of two **real** worker subprocesses, mutation-tested 4/4.

#### ★ What flipping found

**The warm path had never been asserted to carry DUR-2b's durable-effects signal.** That signal
must survive the process boundary because a ContextVar cannot cross it — and the warm pool is now
the *default* path. A warm path that dropped it would have silently disabled at-most-once for
every continued run while every existing DUR-2b test stayed green. It does carry it; there is now
a test saying so.

**Eight existing tests asserted on the fresh-subprocess payload and went red on the flip,
correctly** — with the warm pool on, `subprocess.run` is never called and their capture reads an
empty dict. Each now pins `AINDY_NODUS_WARM_POOL=0` explicitly, because each is about that path
specifically; the payload itself is built once and shared by both paths.

That is the expected shape of a default flip: the tests that silently depended on the old default
announce themselves. Worth noting **CI found them and repeated local sweeps did not** — the local
runs kept stopping partway and never reached those files alphabetically, so "zero failures so far"
was measuring how far the run got, not whether the suite passed.

### Changed — the Nodus guest VM now asks for its environment instead of being hardcoded (EXEC-ENV-BIND-1 phase 2)

Phase 1 gave an execution unit a way to *declare* an environment. Phase 2 is the first place a
declared spec actually changes how something runs.

**The residual this closes was a wrong comment, not a missing line.** `nodus_worker.py` said
*"the VM already confines filesystem access: `allowed_paths` defaults to the cwd"*. True of
nodus — and false here, because **nothing sets the worker's cwd**. Neither
`nodus_worker_pool.WarmNodusWorker` nor `nodus_runtime_adapter`'s `subprocess.run` passes `cwd=`,
so the guest inherited the **server's** working directory: `/home/aindy` in Docker, which holds
`alembic/` — a guest could write migrations that run on next boot — and the repo root in dev,
which holds `AINDY/.env`. `GUEST-CONFINE-1` closed the *escape* in August; the **bound** stayed an
undeclared inherited default until now.

- Every confinement argument is now derived from an `ExecutionEnvironmentSpec` clamped to
  `GUEST_FLOOR`, rather than three hardcoded `False` literals at one construction site. The guest
  path has a *stated requirement* that can be recorded and audited.
- `allowed_paths` is passed **explicitly**, bounded to a per-execution temporary scratch root that
  is created before the VM and released after it. A warm worker no longer shares scratch between
  requests.
- With no declared spec the floor applies unchanged — byte-for-byte the confinement that shipped
  in August, plus the explicit bound.

#### Two behaviour changes for operators

- **`NODUS_ALLOWED_PATHS` no longer has any effect.** nodus reads that variable only on its
  unspecified-default branch, so passing `allowed_paths` explicitly makes it inert. If you were
  using it to widen the guest's filesystem bound, that is now closed — deliberately, and it is
  the safe direction. There is no replacement env var by design: a global flag re-opens the bound
  for every run at once, which is the shape `GUEST-CONFINE-1` refused.
- **A declared spec is clamped to the floor, never merged with it.** A guest cannot widen its own
  sandbox by arriving with a permissive descriptor; it can only ask for *more* confinement.

#### What it did not close

`ORCHESTRATOR-SPLIT-1` predicted the same missing `cwd=` closed both its store-4 data loss and
this residual. **It did not.** The residual was closed by bounding the VM's `allowed_paths` —
stronger than setting a cwd, but it leaves the **process** cwd untouched, so
`nodus_lang_workflow`'s `LocalWorkflowStore` still roots wherever the worker started. That entry
has been corrected rather than left to mislead the next reader.

Also: `nodus_worker.py` gained a module logger, which it had never had. That is not an oversight
anyone should fix casually — **its stdout is the JSON protocol channel**, so a stray `print()`
corrupts the frame the adapter parses. Logging defaults to stderr, which both spawn paths handle.

11 new tests against the real VM, mutation-tested **6/6**; the existing `GUEST-CONFINE-1` suite
re-run against the real VM and green.

### Changed — tools receive a revocable DB handle, not the live session (TOOL-SEAM-ISOLATION-1 step A)

`execute_tool` resolved the tool by **name** through `TOOL_REGISTRY` — handle-shaped and correct —
and then handed it a live SQLAlchemy `Session`: a direct object reference across a trust boundary
that could not be validated, revoked mid-call, or narrowed. Every authority check the function
performs (token, granted tools, capabilities, policy, rate limit, egress, secret scope) was
advisory with respect to what the tool did with that one argument.

The tool now receives a `RevocableToolSession` (`AINDY/agents/tool_session.py`), revoked in a
`finally` when the call returns. Using it afterwards raises `ToolSessionRevoked` naming the tool.

**Measured before changing it:** across all 18 tool functions that exist — 3 runtime-owned and 15
in `aindy-apps-monolith` — **18 take `db` in their signature and 0 reference `db.<anything>`.**
Pure ambient authority with zero utility, so the narrowing breaks nothing that exists. The
parameter name is unchanged, so no tool signature moves. Same evidence `GUEST-CONFINE-1` gathered
before denying its three capabilities.

**What it buys:** a tool can no longer stash the session and use it after the call. That is a
security narrowing *and* a bug class — using a request-shared session after its request has moved
on is `RT-MEMTXN-LEAK-1`'s neighbourhood. Any access at all is logged once per tool, so the
exposure stays countable against a measured baseline of zero.

**★ What it does NOT buy, and must not be read as:** the process is not bounded. A tool holding
this handle can still `import os`, spawn a thread, or open a socket. **`TOOL-SEAM-ISOLATION-1`
remains open.** Treating step A as closing it would be exactly the "gated path that does not
actually confine" failure the scope warns against.

**Known limitation, stated rather than discovered:** the handle is not a `Session` subclass, so
`isinstance(db, Session)` is `False` inside a tool. Deliberate — subclassing would let it be
passed anywhere a real session goes and defeat the point — and safe because no tool uses the
parameter. A tool that genuinely needs data should reach through a syscall, which is what every
app tool already does.

Unflagged, because no compatibility argument exists and a security default that ships off is a
pattern this repository keeps recording as a mistake. 14 tests, mutation-tested **7/7**.

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

#### ★★ What it found on its first CI run

**`EXACTLY_ONCE` is not exactly-once under contention.** Eight concurrent identical calls ran the
handler **twice**. That is *by design* — `resolve_effect_record` degrades to `AT_LEAST_ONCE` when
it loses the insert race against a live pending row, because strict at-most-once needs advisory
locking, and it logs a warning. `IDEMPOTENCY_CONTRACT.md` documents it in its state table.

**The defect is the index, not the code.** `CLAUDE.md`'s `IDEM-11` line — the thing an
implementer reads before flipping `AINDY_SYSCALL_IDEMPOTENCY` — said *"at-most-once is built"*
with no concurrency caveat. The contract and the index disagreed, and the index is what gets read.
Corrected, and the soak now pins the documented behaviour: the gate dedups the large majority, one
`success` row per `action_id` holds, and any degradation must be accompanied by its warning so it
cannot be silent.

**Consequence for the flip:** anyone enabling this for a genuinely non-idempotent effect is buying
"exactly once unless another caller holds the slot." Until this test existed, nothing said so in a
form that could fail.

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

### Added — the idempotency gate is now observable (`aindy_effect_gate_outcomes_total`)

**Until now nothing observed the gate at all.** `aindy_durable_effects` and
`aindy_effect_attribution` are ContextVars, not metrics — so with `AINDY_SYSCALL_IDEMPOTENCY`
enabled an operator had no way to tell whether the gate was firing, replaying, or **silently
degrading**. That absence was the real blocker on a production soak: there was nothing to read.

`aindy_effect_gate_outcomes_total{outcome=…}` counts every resolution:

| `outcome` | meaning |
|---|---|
| `reserved` | this caller won the slot and will execute the effect |
| `replayed` | a completed record was returned instead of executing |
| **`degraded`** | **lost the race to a live pending row — downgraded to `AT_LEAST_ONCE` for this call** |
| `reclaimed` | took over a stale or failed slot |

**`degraded` is the label the counter exists for.** `EXACTLY_ONCE` is not exactly-once under
contention: when the gate loses the insert race to a live pending row it downgrades for that
call. That is correct and documented in `IDEMPOTENCY_CONTRACT.md` — and it was **invisible**. A
deployment where `degraded` is a meaningful fraction of `reserved` is one where the guarantee the
operator believes they enabled is not the one they have.

**Metrics failures never change execution.** `_count_gate` is best-effort and import-local: the
ledger is the correctness path and the counter is observability, and inverting that would let a
Prometheus problem become a duplicate side effect — the exact class the gate exists to prevent.

The contention soak now asserts on this counter rather than on a log line. Three instruments were
tried: `caplog` could not see a warning emitted on a worker thread, a logger spy was thread-safe
but observed the wrong signal, and the counter is both thread-safe and what production reads.
Recorded as **variant 10** in the trusting-a-green-check catalogue — *the instrument cannot see
the thing* — because it generalises to every concurrent or cross-process test.

Using the harness for real also improved it: `read_metric` now distinguishes an **unknown metric
family** (still raises) from a **label combination not yet observed** (reads 0), because
prometheus_client does not materialise label combinations until `.labels()` is first called. The
guard was right to refuse; the rule was too coarse.

### Added — the warm Nodus worker pool is soaked under contention against real workers (NODUS-WARMPOOL-1)

`NODUS-WARMPOOL-1`'s remaining work was recorded as "soak, then flip." This is the soak, and it
exists because of a gap the existing suite named in its own docstring:

> *"…against fake processes/workers (no real subprocess) … End-to-end (a real warm worker serving
> a nodus script) is **app-side PG-tier integration**."*

**That deferral is the whole problem.** It handed the only end-to-end evidence to a consumer that
does not exercise it (`SUBSTRATE-WITNESS-1`), so the pool ran against fakes here and against
nothing there. Meanwhile CI has had `AINDY_NODUS_WARM_POOL=1` on every PR — real evidence, but
**functional and sequential**: it shows the pool serves requests, not that it serves *concurrent*
ones correctly.

`tests/unit/test_soak_warm_pool_contention.py` adds seven tests against **real worker
subprocesses**, six concurrent callers against a pool of two:

- **Response correlation** — every caller gets back the marker *it* sent. The pool speaks
  length-prefixed JSON over one worker's stdin/stdout, so if `_checkout`/`_checkin` exclusion is
  ever wrong, two callers interleave frames and one receives another's result — a silent
  cross-tenant wrong answer that fakes cannot detect and sequential tests cannot reach.
- **Worker reuse under load** — with a long acquire timeout, callers queue and a worker is handed
  from one to the next, which is where a stale frame in the pipe would surface.
- **Boundedness**, **backpressure as `PoolBusy`**, and **no cross-caller state bleed**.

#### ★ The layer mattered, and the first draft got it wrong

`pool.execute()` **raises `PoolBusy`**; the **adapter** is what spills to a fresh subprocess. The
first draft asserted the *pool* spills and produced three reds that looked like a product defect
but were entirely the test's fault. The claim *"enabling the pool can never make execution worse
than the default"* is about the adapter path, and it is now asserted there.

Mutation-tested **4/4** — including that removing the size bound in `_checkout` and swapping
`PoolBusy` for a bare `RuntimeError` both go red. An earlier run scored 2/4 and **both survivors
were defective mutations**, not weak tests: one edited `prewarm()`, which the fixture disables,
and one added an unused class while `PoolBusy` was still raised. A mutation that does not change
behaviour proves nothing.

#### ★ CI caught the soak doing the thing this suite exists to prevent

The backpressure assertion originally used a 200 ms acquire timeout and asserted that some caller
was refused. It passed locally 3/3 and **failed in CI**, where the runner was fast enough that
every caller finished inside the window and backpressure never fired.

**A soak that asserts a race outcome by racing is timing-dependent evidence** — which is the exact
failure mode the harness was built to avoid, and the reason its own concurrency assertion was
rewritten twice. Fixed by setting the acquire timeout to **zero**, so `remaining <= 0`
short-circuits before any wait: the barrier releases four callers at once against a pool of one,
exactly one wins, three are refused. Deterministic rather than probable, and the assertion now
pins the exact split.

#### ★ It also found a trap in the soak harness itself

`drive_concurrently` returned results in **completion order**, so a per-caller positional
assertion paired the wrong result with the wrong caller. It failed deterministically 3/3 and read
exactly like a cross-request state bleed *in the pool*.

An unordered result list is a trap in a concurrency harness specifically: the natural way to write
a per-caller assertion is positional, and the failure it produces **accuses the product**.
`drive_concurrently` now returns results in **worker-index order**, guarded by a test that inverts
completion order, with the partial-failure caveat documented.

### Added — a tool can declare the isolation it needs (TOOL-SEAM-ISOLATION-1 step B)

`register_tool(..., isolation=...)` takes an **assurance class** — `"insecure-dev"`,
`"container-grade-sandbox"` or `"strong-sandbox-tier"` — naming the minimum the host must provide
for that tool to run. `None` (the default, and every existing tool) declares nothing and behaves
exactly as before.

A tool declaring more than the host provides is **refused**, fail-closed, before the handler runs
and before it is handed anything.

#### ★ This declares; it does not confine

A tool that is *allowed* to run still runs **in-process with the process's ambient authority**.
It can still `import os`, spawn a thread, or open a socket. **`TOOL-SEAM-ISOLATION-1` remains
open** — step C is the process boundary and is not built.

Reading a satisfied declaration as confinement would be exactly the *"gated path that does not
actually confine"* failure the scope warns against, so it is stated in the parameter docstring,
the module, and a test that asserts an allowed tool runs in the **same process id**.

#### ★ An assurance class, not a mechanism

The entry originally proposed `isolation="in_process" | "subprocess" | "container" | "strong_vm"`.
That asks a caller to state a *mechanism* the runtime cannot verify — and `in_process` and
`subprocess` are indistinguishable as **assurance**, because a bare subprocess is not a sandbox
and both report `insecure-dev`.

Declaring against `EXEC-ENV-BIND-1`'s existing assurance vocabulary reuses what is already there
instead of growing a second one beside it — the same argument that keeps `FS-SCOPE-1` a field on
that descriptor rather than a peer of `egress_scope`. The runtime owns the *request* vocabulary;
mechanism stays behind the provider boundary.

#### Three properties worth knowing

- **A misspelled class raises at registration**, not at execution and not silently. That is the
  `register_syscall` lesson from `IDEM-11`, where an unforwarded parameter left every plugin
  syscall at the weakest setting with no way to opt in. Downgrading a typo would hand a tool a
  weaker boundary than it asked for — the one direction that must never be quiet.
- **A refusal is an envelope, not an exception.** `execute_tool`'s contract is
  `{success, result, error}` and every caller reads it that way; a refusal that raised would be
  caught by the seam's own broad handler and reported as a tool *failure*, which reads as "the
  tool broke" rather than "this host cannot run it" — the status-code confusion `ROUTE-GUARD-1`
  was. The error names both the requested and the provided class, so an operator can tell a
  misconfigured host from an over-strict declaration.
- **A host-resolution failure refuses.** `_host_assurance` reports the weakest class on any error,
  so a broken provider denies a strict declaration rather than admitting it.

12 tests, mutation-tested **6/6** — including that flipping `>=` to `>`, accepting an unknown
class, or returning success on refusal all go red.

### Added — tool returns are measured against the process-boundary contract (TOOL-SEAM-ISOLATION-1 step C1)

`aindy_tool_return_contract_violations_total{reason, declared_isolation}` counts tool returns that
would not survive a process boundary: `not_a_dict`, or `not_json_serializable`.

**It measures; it does not reject.** By the time a return is inspected the handler has already run
and its effect is real — failing the call there would **discard a real effect**, which is strictly
worse than passing an awkward value through. `SyscallDispatcher` made the same judgement on the
syscall path (*"a ledger failure must never turn that into a caller-visible error"*), and the two
boundaries must not disagree.

#### Why it exists: it is the gate on step C

A tool cannot run behind a process boundary unless its return marshals — a `UUID`, a session, or
any live object crosses no pipe. Every tool that exists returns a dict and every one is typed
`-> dict`, but **nothing enforced it**, so "they all comply" was an assumption rather than a
measurement. A non-zero count is now exactly the list of tools that cannot be confined yet.

`not_json_serializable` is the case a plain `isinstance(result, dict)` check would miss, and it is
the one that actually bit: on the syscall path a `UUID` return came back as an error envelope
**after the effect had already landed**. That was fixed there and never here.

#### Two details

- **A tool that declared an isolation class is logged at ERROR and labelled separately.** It has
  opted into a boundary its return cannot cross, so that is a defect in the tool rather than an
  observation about an in-process one — and the label keeps the remediation list readable.
- **A metrics failure never affects the call.** Observability does not sit on the effect path, the
  same rule as the effect-gate counter.

7 tests, mutation-tested **6/6** — including that a mutation which *rejects* instead of measuring
goes red, because preserving a landed effect is the design and not an accident.

### Added — a tool that declares isolation now runs out of process (TOOL-SEAM-ISOLATION-1 step C2)

Steps A and B narrowed one argument and let a tool *declare* a boundary. **This is the first thing
in this entry that applies one.**

A tool registered with `register_tool(..., isolation=<assurance class>)` executes in a one-shot
worker subprocess (`python -m AINDY.agents.tool_worker`) instead of in the runtime process.
**Opt-in per tool** — a tool that declares nothing is unaffected and keeps running in-process,
because a subprocess round-trip per call is real latency and must not be imposed on everything.

`AINDY_TOOL_ISOLATION=0` reverts to declare-and-refuse only: the declaration is still validated
and still refused when the host cannot meet it, but it is not applied.

#### ★ There is no fallback, and that is the design

A worker that **crashes, times out, or cannot be spawned means the tool does not run.**

This is deliberately the opposite of the Nodus adapter, which spills a warm-pool failure to a
fresh subprocess. There, both paths give the *same* guarantee and falling back is strictly better
than failing. Here they do not: falling back would execute a tool that asked to be confined
**unconfined** — precisely the *"gated path that does not actually confine"* failure this entry
exists to prevent. Mutation-tested: making a failed worker fall back goes red.

#### Three constraints worth knowing before declaring `isolation=`

- **`db` is `None` in the worker.** A session cannot cross a process boundary. This is safe by
  measurement rather than hope — all 18 tool functions take `db` and **none uses it** (step A). A
  tool that needs data reaches through a syscall, which is what every app tool already does.
- **A worker rebuilds `TOOL_REGISTRY` from the plugin stack.** A tool registered ad hoc in the
  parent is invisible there, and the worker says so specifically rather than failing generically —
  a registry mismatch is a deployment problem and a generic error would send an operator to the
  wrong place.
- **A non-marshalling return FAILS here**, where the in-process seam only counts it (step C1).
  In-process the effect has landed and rejecting would discard it; in a worker the value cannot
  cross the pipe, so there is nothing to carry back. That is exactly why C1's counter exists:
  check `aindy_tool_return_contract_violations_total` before declaring isolation on a tool.

#### Authority is not re-evaluated in the worker

The parent's `execute_tool` checks token, granted tools, capabilities, policy, rate limit, egress
and secret scope **before** delegating; the worker resolves the function and runs it. Re-checking
inside would put the authority decision in the very process the boundary distrusts — and calling
`execute_tool` there would recurse, since it routes declared tools to a worker.

12 tests, mutation-tested **7/7**, including a real `python -m AINDY.agents.tool_worker`
round-trip that proves the module is executable and that nothing else writes to stdout to corrupt
the response frame.

### Changed — CI now enforces the CLAUDE.md registry's size rule (#493)

**This changes what a green `Runtime Contracts` means**, which is why it is here rather than
filed as a docs-only change. `tests/unit/test_debt_registry_accuracy.py` gained three assertions:
no registry entry may exceed 1150 bytes (850 under a `### Closed` heading), the cap must stay
near the data it governs, and the registry must stay under 60% of `CLAUDE.md`. A PR that adds an
over-long entry to the registry now fails CI.

The caps are the current high-water mark, written into the test as a **ratchet against regrowth**
rather than an endorsement of that length. The registry had been trimmed twice and grown back
both times; the previous attempt reported −14,936 B while the file grew 96,913 → 115,234 B,
because the delta was measured over the entries touched rather than over the file. Mutation
tested 5/5, including a liveness control that fires if the cap ever drifts far above the data.

Same PR trimmed the registry 67,986 → 55,829 B with no entry deleted, after verifying that 79 of
91 entries already have a larger record in `TECH_DEBT.md`.

### Fixed — the documented lint command now matches the one CI runs (#494)

`CLAUDE.md`'s Commands section listed `ruff check AINDY/` and `ruff format AINDY/`. CI's
`Runtime Lint` runs neither of those literally — it runs
`ruff check AINDY tests --config AINDY/ruff.toml` — and **`ruff format --check` reports 457 of
559 files would be reformatted**, so the second command had never been true of this tree.
Following it as documented produces a ~450-file diff on top of whatever the agent was asked to
do. The section now states the enforced command and warns against running `format` casually;
filed as `LINT-FORMAT-1` with the measurements and the reason not to close it with a repo-wide
sweep.


## 2.4.1 — 2026-08-19

A patch release carrying one security-relevant dependency fix and the grouped dependency
bumps. **No runtime-owned model changed**, so the schema contract stays `2026-08-15.1`, the
Alembic head stays `0016`, and `bootstrap-schema` exits 0 against an existing database with
nothing to reconcile. No route began enforcing a new scope, so no caller loses access.

`2.4.0` shipped with `nodus-lang` at the affected `5.0.1` pin — the fix landed on `main` after
that tag, which is what this release exists to close.

### Changed — `nodus-lang` pinned to 5.0.4 (was 5.0.1) (#488)

**Operators: read this if you have enabled `AINDY_NODUS_WARM_POOL`.** It is off by default and
this is latent for every deployment that left it that way.

- Bumped `nodus-lang` `5.0.1` → `5.0.4` in `pyproject.toml` and `AINDY/requirements.txt`.
- **This is a security fix, not a routine bump.** `nodus-lang <= 5.0.2` bound its
  `GLOBAL_MEMORY_STORE` at **import**, so every `NodusRuntime` constructed in one process shared a
  single guest memory dict. `memory_put`/`memory_get` are guest builtins available to any `.nd`
  script, so one script could read another's stored values. Upstream 5.0.3 gives each runtime its
  own store; sharing is now opt-in.
- **Why it reached the runtime:** `AINDY/runtime/nodus_worker_pool.py` reuses worker processes
  across requests. Its docstring claimed a reused process "never leaks state between runs" on the
  strength of `run_one` rebuilding per-request state — but `run_one` cannot reset a module global
  inside a dependency. **With `AINDY_NODUS_WARM_POOL` enabled on an affected pin, two tenants'
  scripts served by the same warm worker could read each other's guest memory.** The pool is
  opt-in and off by default, so this was latent rather than live. The docstring has been corrected.
- Regression guard added:
  `tests/unit/test_nodus_upgrade_contract.py::test_two_runtimes_in_one_process_do_not_share_guest_memory`,
  mutation-tested against 5.0.1.
- `nodus-mcp` is unchanged at `>=0.1.3` and resolves against 5.0.4, so `aindy-runtime[mcp]`
  remains installable. This retires the second instance of `MCP-SDK-2X-1`, where a
  `nodus-lang<5.0.0` cap in `nodus-mcp` had blocked a nodus major.

### Changed — dependency bumps, grouped (#485)

Nine dependabot PRs taken as one, because `strict: true` branch protection means each
individual merge forces a rebase of the other eight, and because dependabot resolves each
package independently — grouping is necessary but not sufficient, so the set was hand-aligned
and verified to resolve together.

| Package | From | To |
|---|---|---|
| `SQLAlchemy` | 2.0.51 | 2.0.52 |
| `uvicorn` | 0.52.1 | 0.52.3 |
| `Mako` | 1.3.12 | 1.4.1 |
| `regex` | 2026.6.28 | 2026.7.19 |
| `prometheus-fastapi-instrumentator` | 8.0.2 | 8.1.0 |
| `cc` (Rust build-dep) | 1.4.1 | 1.4.3 |
| `uuid` (Rust) | 1.24.0 | 1.24.1 |

Every Python pin moved in **both** `pyproject.toml` and `AINDY/requirements.txt`. CI installs
the second and then `pip install -e . --no-deps`, so a bump applied to only the first is a bump
CI never exercises — which is exactly how `nodus-lang` was tested at 4.1.0 for four months while
the wheel required 4.2.0. `test_dependency_pin_agreement.py` now fails when they disagree.

The Rust bumps are lockfile-only — `Cargo.toml` declares caret ranges — and pull
`find-msvc-tools` 0.1.10 → 0.1.11 as a transitive of `cc`.

**★ The two GitHub Actions bumps were really a consistency defect, and in a workflow added this
release.** Dependabot proposed `actions/checkout` 4 → 7 and `actions/setup-python` 5 → 7. All 34
other usages across the workflows are **SHA-pinned with a version comment**; only
`upgrade-path-guard.yml` used floating `@v4` / `@v5` tags. Rather than bump a tag, both are now
pinned to the same commit SHAs the rest of the repo already uses, so a moved tag cannot change
what runs. No floating action tag remains in any workflow.

**Verified, not assumed:** the full declared set resolves (`pip install --dry-run -r
AINDY/requirements.txt`, and again with the separately-installed MCP extra); the Rust crate
builds `cargo build --locked --release`; and the native scorer was **loaded and exercised** with
`AINDY_REQUIRE_NATIVE_BRIDGE=1` rather than left to skip — a skip reads green, which is
`NATIVE-CI-1`.

**Release notes read for every pin that moved, at release time.** The rule this satisfies was
added in #490 *because* of `nodus-lang`; this is its first application, and it changed two
readings. `Mako` 1.4.0 is a **breaking** release — it raises its Python floor to 3.10 and its
`MarkupSafe` floor to 2.0 — which the version distance (`1.3.12 → 1.4.1`) does not show;
`requires-python = ">=3.11"` and `MarkupSafe==3.0.3` satisfy both, so the bump is safe here and
would not have been on an older floor. `SQLAlchemy` 2.0.52 carries two behaviour changes,
`aliased()` on select/union constructs and `Table.to_metadata()` copying rather than reusing
default objects; neither API appears anywhere under `AINDY/`, checked rather than assumed. The
rest are benign for this runtime: `uvicorn` 0.52.2/0.52.3 are `zttp` parser updates (bodyless
request receives, parsing performance), `regex` 2026.7.19 fixes two segfaults reachable only
through crafted recursion/fuzzy patterns and is a transitive dependency we never import, and
`prometheus-fastapi-instrumentator` 8.1.0's `root_path` and nested-app label changes cannot
reach us — **it is declared in both pin files and imported nowhere in the codebase.**


## 2.4.0 — 2026-08-17

### Fixed — `llms.txt` and the Rust source were missing from the distribution

`llms.txt` (12.5 KB) and `llms-full.txt` (22 KB) lived only at the repository root. Neither sits
under `AINDY/`, and `[tool.setuptools.package-data]` cannot match outside the package, so **they
shipped in neither the wheel nor the sdist**. They exist so a model reading the *installed*
package can orient itself; at the repo root they served a reader who had already found the repo,
which is the audience that needed them least.

Both are now at `AINDY/llms.txt` and `AINDY/llms-full.txt`, declared in package-data **and**
`MANIFEST.in` — the wheel takes the first, the sdist the second, and declaring only one is half
a fix. Verified by building and inspecting both artifacts.

**The Rust source now ships in the sdist; the compiled artifact deliberately does not.** The
backend produces a pure-Python `py3-none-any` wheel. A `.pyd`/`.so`/`.dylib` inside one installs
a **broken binary** for every user not on the exact OS, architecture and CPython it was built
with — worse than the current state, in which `native_bridge.py` falls back cleanly to the
Python scorer. `Cargo.toml`, `Cargo.lock`, `build.rs` and `src/*.rs` now travel so the
accelerator can be built locally, and the README says plainly that installed users run the
Python path.

**★ And one the audit did not look for: the sdist was carrying cargo build output.**
`recursive-include AINDY *.json` is path-based, so it matched **~200 fingerprint files** under
the crate's `target/` — measured in the 2.4.0 sdist — some embedding the building machine's
absolute rustup and toolchain paths. `prune` now excludes it.

**This never reached PyPI.** The published 2.3.0 wheel was downloaded and checked: zero
`target/` files, because CI builds in a checkout where `target/` is unpopulated. It is a
local-build hazard — precisely the kind that ships the day someone cuts a release from their own
machine.

*A correction worth recording, since it changes what the fix is doing:* the wheel also showed
100 such files during testing, and a clean rebuild without the new `exclude-package-data` showed
**zero**. Package-data globs only apply to directories setuptools treats as packages, and
`target/` is not one. The wheel's copies came from a stale `build/lib` carrying them across
builds. `prune` is the fix; `exclude-package-data` is belt-and-braces against that staleness.

Before benchmarking any move to per-platform wheels: no comparison of the native scorer against
the Python one exists in this repo, and that measurement should come first.


**`CONTRIBUTORS.md` now ships too.** It records contributions present in this repository and its
own text says it travels with the package — which was not true. A repo-root file cannot reach a
wheel through package-data (that matches only inside `AINDY/`), so it is declared twice:
`include CONTRIBUTORS.md` in `MANIFEST.in` for the sdist, and in `license-files` for the wheel,
where it lands as `dist-info/licenses/CONTRIBUTORS.md`. Verified in both artifacts.

### Changed — the last identity-only routes now check authority (`HTTP-SCOPE-GAP-1`, #464)

**Operators read this before upgrading.** Eighteen more routes now require a scope: all 13 under
`/coordination/*` and the 5 user-owned agent routes under `/platform/agents`. They previously
depended on `get_current_user` alone — agent registration, heartbeats, deregistration, the
inter-agent inbox and agent CRUD were reachable by anyone who could authenticate.

| Routes | Scope |
|---|---|
| `/coordination/agents`, `/agents/status`, `/agents/register`, `/agents/{id}/heartbeat`, `DELETE /agents/{id}`, `/graph`, `/messages/inbox`, `/messages/{id}/acknowledge` | `agent.run` |
| `/coordination/runs`, `/runs/{id}/children`, `/conflict/run` | `execution.read` |
| `/coordination/memory/shared`, `/conflict/memory` | `memory.read` **or** `memory.write` |
| `GET/POST/PATCH/DELETE /platform/agents`, `/platform/agents/{slug}/restore` | `agent.run` |

`platform.admin` continues to satisfy any gate.

**Interactive users lose nothing.** All four scopes are in the ordinary derived session set, and
a test drives the real routes to prove it. As with the memory router, the callers to check are
**platform API keys** issued without these scopes.

**Three gates rather than one, deliberately.** `/coordination/memory/shared` queries
`memory_nodes` directly and `/conflict/memory` inspects a memory path — gating them on
`agent.run` because they live in the agent router would make that router a second door onto
memory. Tests assert the split in both directions: an agent-scoped caller cannot read shared
memory, and a memory-scoped caller cannot register an agent.

**No `agent.read`/`agent.manage` was invented.** The agent surface is gated as one authority
because the vocabulary has no finer grain, and adding one would oblige every consumer to grant a
scope that answers no question they ask today. If that split is wanted later it should be a
deliberate vocabulary change, not a side effect of adding gates.

**★ `/platform/agents` never inherited the `/platform` admin gate** — it is mounted on the app
directly rather than through `platform_router`, by design (FR-12b exists so an ordinary user can
own an agent). That is also why it had no authority check at all. Owner scoping is unchanged and
still does the work a scope cannot: a scope answers *"may you touch agents"*, never *"may you
touch **this** agent"*.

**Both prefixes are covered.** The coordination handlers are registered at `/coordination/*` and
at `/apps/coordination/*`; the gate is on the endpoint, so it applies to either. The app's
`smoke_autonomy.py` calls the `/apps` form with a Bearer JWT and is unaffected.

**Census after this change** — 126 registered routes: **47 scope-gated, 56 admin-gated, 21
public, 2 identity-only.** The two are `POST /auth/logout` and `POST /auth/password/change`,
which act only on the caller's own account, where a scope is a permission nobody could be
denied. A test pins that set by equality, so both adding an ungated route and gating one of
those two fail in CI.

### Fixed — an API key could mint itself a wider API key (`KEY-SCOPE-ESCALATION-1`, #463)

**Security. Operators should read this and audit existing keys before upgrading.**

`POST /platform/keys` validated only that each requested scope *exists* (membership in
`Scopes.ALL`), never that the caller was entitled to grant it. Demonstrated end to end against
real PostgreSQL, starting from an API key holding the single scope `flow.read`:

1. `POST /platform/keys {"scopes": ["platform.admin","memory.delete","event.emit"]}` → **201**,
   key issued with exactly those scopes
2. `GET /platform/admin/users` with the new key → **200**, every user's email and admin flag
3. `POST /platform/admin/users/{own_id}/promote` → **200**, `is_admin: true`

Step 3 lands in the **user row**, so revoking the minted key does not undo the escalation, and
every subsequent JWT session for that account is an admin session.

Nothing upstream would have stopped it: `require_platform_admin_access` admits **any**
authenticated API key to the whole `/platform` tree, on the stated assumption that *"scope
enforcement happens per-endpoint or per-syscall"* — which `keys_router` did not do.

**The fix is a delegation rule: you cannot grant what you do not hold.** A new
`grantable_scopes(principal)` bounds key creation by the creator's own authority — an API key's
own scopes, or a session's derived scopes. A request naming any scope outside that set is
refused with **403 `scope_not_grantable`**, listing only the scopes that were not grantable. An
unknown scope still returns **422**, because *"that is not a scope"* and *"you may not grant that
scope"* are different failures.

A holder of `platform.admin` may still grant anything. That is deliberate, not a loophole:
`platform.admin` already satisfies every scope gate and reaches user promotion, and it preserves
the documented affordance that a key *can* carry `memory.delete`/`event.emit`, which no session
inherits.

**What this does not change:** no existing key loses any access it already had — the rule only
governs what a key can *grant*. **What it does not close:** `require_platform_admin_access`
admitting any API key to 56 `/platform/*` routes is the broader hole and is tracked separately;
this removes the escalation ladder, not the reach.

**Audit advice:** any key carrying `platform.admin`, `memory.delete` or `event.emit` that you did
not deliberately issue should be revoked, and `users.is_admin` reviewed for accounts you did not
promote yourself.

**★ Why no test caught this, and the trap for whoever tests it next:** `platform_api_keys.scopes`
is a PostgreSQL `ARRAY`. On SQLite the insert fails at the driver (`type 'list' is not
supported`) **after** the authorization gate has been passed, so the harness turns a 201 into a
500 and the finding reads as an unrelated bug.

### Changed — the memory router now enforces authority, not just identity (`HTTP-SCOPE-GAP-1` D, #462)

**Operators read this before upgrading.** All 22 routes under `/memory/*` now require a scope.
Previously they depended on `get_current_user` alone — `grep -c enforce_api_key_scope` in
`AINDY/routes/memory_router.py` was **0** while that file reached memory writes, graph edits and
Nodus script execution. Anyone who could authenticate could do all of it, and an API key issued
with, say, `flow.read` only could too.

**Scopes required:**

| Routes | Scope |
|---|---|
| all reads — `GET /nodes`, `/nodes/{id}`, `/history`, `/links`, `/traverse`, `/performance`, `/agents`, `/agents/{ns}/recall`, and `POST /nodes/search`, `/nodes/expand`, `/recall`, `/recall/v3`, `/federated/recall`, `/suggest` | `memory.read` **or** `memory.write` |
| `POST /nodes`, `PUT /nodes/{id}`, `POST /links`, `POST /nodes/{id}/share`, `POST /nodes/{id}/feedback` | `memory.write` |
| `POST /nodus/execute`, `POST /execute`, `POST /execute/complete` | `flow.execute` |

`platform.admin` continues to satisfy any gate.

**Interactive users lose nothing.** An ordinary JWT session derives `memory.read`, `memory.write`
and `flow.execute` from the user row (`derive_session_scopes`), so every gate here is satisfiable
without issuing anyone a grant. A test asserts exactly that against the real routes, so if it
ever stops being true it fails in CI rather than as scattered 403s that read as a frontend bug.

**API keys are where to look.** A platform key that calls `/memory/*` over HTTP and was issued
without these scopes will now get **403**. Grant the scope, or use `POST /platform/syscall`,
which was already gated. No first-party caller is affected: the SDK's `client.memory.*` goes
through the syscall route, not these HTTP routes.

**★ The read gate accepts `memory.write` as well**, matching `_DISPATCH_CAPABILITY_SCOPES`
exactly. Without that, one key would read fine through `POST /platform/syscall` and be refused
on `GET /memory/nodes` — two answers to one authority question from one credential.
`enforce_api_key_scope` gained any-of alternatives for this; existing single-scope call sites are
unchanged.

**★ Execution is not a memory scope.** `/memory/execute` and `/memory/nodus/execute` compile and
run caller-supplied workflow code. Filing them under `memory.write` would have made "may I
remember this" and "may I run this" the same permission, so they take `flow.execute`.

**Behaviour note:** `POST /memory/execute/complete` has returned **410 Gone** since completion
moved inside `POST /memory/execute`. A caller without `flow.execute` now gets **403** there
instead — authorization is checked before the deprecation notice.

Still open on `HTTP-SCOPE-GAP-1`: the other routers. This closes the router the entry named; it
does not close the entry.

### Changed — `nodus-lang` 4.2.0 → **5.0.1**, `nodus-mcp` → **0.1.3** (`NODUS-UPGRADE-1`)

A major bump, adopted with **no behavioural change to this runtime**.

**Both packages move together, across all three declaration sites** — `pyproject.toml`,
`AINDY/requirements.txt`, and the `Install MCP extra` step in `runtime-ci.yml`. CI installs the
second and a `--no-deps` editable install means the first is never applied there; the third
installs the MCP packages directly rather than through the extra, so a constraint fixed in only
the first two is silently re-resolved by it. Both of this week's dependency failures were one of
those sites being missed.

**`nodus-mcp 0.1.3` is what unblocked this.** 0.1.2 required `nodus-lang<5.0.0`, which made
`pip install aindy-runtime[mcp]` a flat `ResolutionImpossible` against a 5.x pin — not a CI
problem but an uninstallable published extra. 0.1.3 floats the requirement to
`nodus-lang>=4.0.0`, so the cap is gone rather than merely raised, and a future nodus major will
not stall the runtime the same way.

**Two breaking changes upstream, and neither reaches us:**

**1. `NodusRuntime` now denies capabilities by default** (embedding only; `nodus run` is
unaffected). A runtime can no longer spawn subprocesses, open sockets or read the process
environment unless the embedder grants it.

We had already done this by hand. `GUEST-CONFINE-1` (#438) found the same hole nodus's own audits
found — *"the capability chokepoint was built and unused, with the door propped open by
registering subprocess and http by default"* — and fixed it caller-side by passing
`allow_subprocess=False, allow_network=False, allow_env=False`. **The runtime has exactly one
construction site** (`nodus_worker.py:343`) and it already passed all three, so deny-by-default
is now belt-and-braces rather than the mechanism. The app monolith constructs `NodusRuntime`
**nowhere**, so nothing there needs granting either.

**2. A Nodus program can no longer write into `.nodus/`** — the workflow store and graph state,
which a program could previously use to forge run records. The only `.nodus/` this repo ships is
`AINDY/nodus/stdlib/.nodus/deps.json`, which is *read* at import by the module resolver and never
written by a guest, so this is a no-op here.

**Verified against the real VM, not inferred:**

- **All 31 gated builtins are still blocked** — 7 subprocess / 18 network / 6 env, identical to
  4.1.0. The guest surface did not widen or shrink across the major bump.
- The full nodus-touching suite passes: guest confinement, the `std:sys` fail-loud guard, the
  worker, agent plan compilation.

**Three test-side adjustments, all cosmetic breakage rather than regressions** — worth
distinguishing, because four confinement tests went red and "the sandbox is broken" was the
obvious first read:

- **Denial wording changed.** 5.0.0 rephrased `... allow_subprocess=False ...` to
  `Blocked: subprocess execution is not granted; pass allow_subprocess=True to NodusRuntime to
  allow it`. The tests asserted the old sentence while the guest was fully confined. They now
  assert the **flag name** appears — the part that carries meaning, since it says *which*
  boundary refused — instead of a phrasing that is not ours to depend on.
- **★ Gated-builtin discovery no longer scrapes source at all.** It read the names out of
  `nodus.builtins.registry` with a regex, and broke on two consecutive releases: 5.0.0 moved them
  from the `if` branch into the `else:` branch's tuple, and 5.0.1 replaced that with a
  `block_group(...)` call, at which point the pattern matched nothing.

  Both breakages were **loud** — the discovery assertion and the `>= 31` floor turned an empty
  sweep into a red test rather than a vacuous pass — which is the only reason scraping was
  tolerable. **5.0.1 adds `GATED_BUILTINS` as a public mapping** (`{flag: GatedBuiltinGroup}` with
  `names`, `arity`, `capability`, `description`), so the scraping is deleted. Still *derived*
  rather than hardcoded, which is the property that matters: a builtin nodus adds to a gate shows
  up automatically instead of silently widening the guest surface.
- **The defaults assertion is inverted, not deleted.** It asserted nodus shipped permissive
  defaults, and said in its own docstring that a flip would be *"a good failure to have to
  read"*. It flipped; the test now asserts deny-by-default, so a future revert to permissive is
  a red test rather than a discovery — which is how `GUEST-CONFINE-1` was found the first time.

`tests/unit/test_nodus_upgrade_contract.py` — added days before this bump — caught the defaults
change on its first run and confirmed every other coupling (`_get_active_vm`, `call_syscall`,
builtin-override refusal, keyword-only flags, no `**kwargs` catch-all) survived intact.

### Fixed — a narrow API key could reach the whole `/platform` tree, including signing-key rotation (`KEY-SCOPE-ESCALATION-1`, `HTTP-SCOPE-GAP-1`, #465)

**Security. Operators should read this and audit issued keys before upgrading.**

`require_platform_admin_access`, the dependency on the `/platform` parent router, returns **any**
authenticated API key unconditionally — its docstring justifies that with *"scope enforcement
happens per-endpoint or per-syscall"*. For **46 of 53** routes it did not. Demonstrated from a key
holding the single scope `flow.read`, owned by a non-admin user:

| Route | Before | After |
|---|---|---|
| `GET /platform/keys`, `/nodes`, `/webhooks`, `/nodus/*`, `/queue/*`, `/observability/*`, `/flows/runs` | **200** | 403 |
| `POST /platform/queue/dead-letters/drain` | **200** — drained the queue | 403 |
| `POST /platform/ops/rotate-secret-key` | **200 — rotated the platform signing key** | 403 |

**★ The rotation is worse than destructive.** The caller supplies the new key, so afterwards they
know the signing secret and can mint tokens that verify — every user impersonable, admin
included. `KEY-SCOPE-ESCALATION-1`'s delegation rule does not touch this: that rule bounds what a
key may *grant*, not what it may *do*.

**Scopes now required**, per endpoint:

| Routes | Scope |
|---|---|
| `/platform/keys` (4), `/queue/*` (5), `/nodes` (4), `/observability/*` (11), `/flows/runs*` + `/flows/registry` (5), `POST`+`DELETE /platform/flows` (2), `/ops/rotate-secret-key` | `platform.admin` |
| `/platform/webhooks` (4) | `webhook.manage` |
| `/platform/nodus/*` — run, upload, list, schedule, flow (7) | `flow.execute` |
| `/platform/tenants/{id}/usage` | `execution.read` |

**Interactive users are unaffected — not "mostly", at all.** The parent gate already required
`is_admin` for JWT callers, and an admin session derives both `platform.admin` and
`webhook.manage`. Only API keys are newly constrained, which is the entire point.

**★ One first-party consumer is affected, and it is ours.** `aindy-runtime nodus run` and
`aindy-runtime nodus upload` (`AINDY/cli.py`) post to `/platform/nodus/run` and
`/platform/nodus/upload`. A platform key (`aindy_…`) used with the CLI now needs **`flow.execute`**;
before, any key worked. A Bearer JWT for an admin is unaffected — admin sessions derive
`flow.execute`. Nothing else in this repo, the SDK, or the app monolith sends `X-Platform-Key`:
the SDK's `client.memory.*` is `MemoryAPI(self.syscalls)`, i.e. `POST /platform/syscall`, which is
one of the two routes deliberately left ungated below.

**Audit advice:** a platform API key issued with narrow scopes could, until now, do anything on
this tree. Review `users.is_admin` for accounts you did not promote, and rotate `SECRET_KEY`
yourself if you cannot account for every key that has existed.

**Why the router gate was not simply tightened.** `POST /platform/syscall` is the SDK's entire
surface and is used with narrow scopes like `memory.read`; requiring `platform.admin` there would
break every SDK caller. The fix is the per-endpoint enforcement the docstring already assumed.

**Two routes stay ungated at the route level, deliberately:** `POST /platform/syscall` and
`GET /platform/syscalls`. Their authority is resolved **per syscall** by
`_resolve_dispatch_capabilities`, which grants only the requested syscall's own capability and
scope-checks API-key callers there. A route-level scope would either have to be one every SDK key
holds — no constraint at all — or break the SDK. A test pins that set by equality so a 47th
ungated route fails CI rather than shipping.

**The safety guard was rewritten, and strengthened.**
`test_every_enforced_scope_is_held_by_an_ordinary_session` required every gate to be satisfiable
by an *ordinary* session. That was right when every gated route was one an ordinary user should
reach, and would now have been an argument for weakening `platform.admin`. It is replaced by
`test_no_route_enforces_a_scope_nobody_can_satisfy`, which is route-derived and allows two
branches: satisfiable by an ordinary session, **or** the route is admin-gated and the scope is one
an admin session derives. A gate failing both is a permission nobody can hold — a 403 the caller
cannot fix — and that is now what fails CI.

**Interaction with `KEY-SCOPE-ESCALATION-1`'s delegation rule.** `POST /platform/keys` now requires
`platform.admin`, and a `platform.admin` holder may grant any scope — so the delegation rule added
in #463 is currently **unreachable over HTTP**: no principal both passes the gate and is bounded
by the rule. It stays anyway, and its test now says so explicitly rather than pretending to be a
route test. The two controls answer different questions — the gate asks *"may you manage keys"*,
the rule asks *"may you grant **this**"* — and if the gate is ever loosened to let a narrower key
manage its own keys, the rule is the only thing standing between that and a `flow.read` key
minting `platform.admin` again.

**The invariant now pinned in CI**, rather than a route count that will drift: the runtime has two
admin dependencies that do different things — `require_admin_principal` demands `platform.admin`
on an API key, while `require_platform_admin_access` admits any key unconditionally — and **no
route may rely on the second one alone**. That distinction is how this survived review; a test
now asserts no route depends on the permissive guard by itself, with the two SDK routes as named
exceptions.

Census across 126 registered routes: **91 scope-gated, 12 admin-gated, 21 public, 2 identity-only**
(was 47 / 56 / 21 / 2).

### Added — `SANDBOX_ESCAPE_AUDIT.md` Entry 016 for the `v2.3.0` gate run (#457)

17 / 17 PASS on the `v2.3.0` tag (`python:3.11-alpine`, native Linux containers, commit
`c911312`). The certified boundary is untouched — `git diff v2.2.0..v2.3.0` over
`sandbox_runner.py`, `plugin_host.py`, `sandbox_certification.py` and `tests/sandbox/` is empty.

**★ The entry names the one dependency change and why a green gate here does not cover it.**
`nodus-lang` 4.1.0 → 4.2.0 does not touch the Tier-2 OCI runner this suite certifies, but it
*does* touch the **guest** boundary `GUEST-CONFINE-1` closed, because confinement is expressed
as VM constructor arguments. Had one been renamed, the guest would run unconfined **while this
suite still reported 17/17** — the two boundaries are independent. That was verified against the
real VM before the bump landed, not inferred from this result.

### Fixed — capability providers ran on every tool check, and a slow one denied tool execution (`CAPABILITY-PROVIDER-TIMEOUT-1`, #466)

`_load_capability_definition_providers` is reached from `get_capability_definitions`,
`get_capability_definition`, `get_capabilities_for_tool` and `get_capabilities_for_agent`, and
therefore from `check_tool_capability` — **the tool-execution path**. Capability providers are
subprocess-isolated, so **every tool capability check spawned a process per provider** and waited
on a 30-second budget.

Under CPU contention that budget was exceeded, the exception was swallowed into a
`logger.warning`, and the capability set came back empty. The observable symptom was tool
execution refused with *"tool 'x' has no registered capability mapping"* — a message that names
the tool and nothing about the cause.

**It fails closed.** `check_tool_capability` refuses a tool whose mapping is missing, so this is
an **availability** problem, not a security one: a slow host stops tool execution rather than
letting anything through. (The guard is conditional — `if not required_capabilities and tool_name
in TOOL_REGISTRY` — so it is now pinned by a test rather than assumed.)

**Measured**, 10 `get_capabilities_for_tool` lookups on an idle machine:

| | subprocess invocations | wall time |
|---|---|---|
| before | **10** | 56.4s |
| after | **1** | 11.4s |

That is ~5.6 seconds of subprocess per tool capability check, paid on every tool call, and it
scaled linearly with the number of checks. The remaining 11.4s is the one cold call.

**Three changes:**

- Each provider's bundle is **cached**, so a provider runs once instead of per check. The bundle
  is still *applied* on every call, so clearing the definition dicts repopulates correctly.
- A **failure is never cached** — a transient timeout is retried on the next call instead of
  persisting for the life of the process.
- The failure logs at **ERROR**, naming what it costs, rather than a warning nobody reads.

**★ The cache lives on the provider object, not in a module global.** A
`_capability_providers_loaded` latch would have to be added by hand to two separate
registry-reset dictionaries, and forgetting either leaves a stale `True` that empties the
capability set permanently — this same bug, reintroduced by its own fix. The provider list is
already reset by both, so a cache attached to the objects inside it is invalidated for free.

**Not done, deliberately:** this surface was *not* added to
`_STATEFUL_IN_PROCESS_CALLBACK_SURFACES`. That set is for callbacks that read live in-process
state a subprocess cannot reconstruct; `runtime_capability_bundle` returns a literal dict and
does not qualify. Moving it there would weaken a documented isolation boundary for a performance
reason.

**Residual:** the first capability lookup in a process still spawns one subprocess per provider,
so a sufficiently contended host can still fail it once — now retried rather than permanent. If
it recurs, `AINDY_RUNTIME_CALLBACK_TIMEOUT_SECS` is the next lever.

### Fixed — `recall()` no longer issues three queries per candidate (`MEM-RECALL-N1-1`, #458)

Scoring ran `_get_model_by_id` (1 × `memory_nodes`) **plus** `get_graph_connectivity_score`
(2 × `memory_links` COUNT) **per candidate**, over up to `limit * 3` semantic *and* `limit * 3`
tag candidates.

- The re-fetch existed only to read four columns — `success_count`, `failure_count`,
  `usage_count`, `weight` — that the originating SELECT had already read and `_node_to_dict`
  then dropped. They are now carried, and the re-fetch is gone.
- Connectivity is now **two grouped queries for the whole candidate set** instead of two per
  candidate, via `get_graph_connectivity_scores()`. The per-node function is unchanged and
  still used elsewhere.

**Response shape:** `weight` is newly present on memory dicts.
`success_count` / `failure_count` / `usage_count` are **not** newly exposed — the scoring loop
already wrote them onto returned candidates — but they are now present *consistently*, including
when the old re-fetch would have missed (a row deleted between the two queries silently left the
counts unset).

Ranking is unchanged; equivalence with the per-node score is asserted, and a test counts real
SQL so a refactor that merely *looks* batched fails.

### Fixed — `ISOLATION_MODEL_PLAN.md` contradicted itself (`ISOLATION-DOC-STATUS-1`, #458)

Line 6 said *"Planning — no implementation has begun"* while line 148 of the same file said
*"Scope B1 complete"* — and the sandbox runners, plugin host, certification surface and
nine-file escape suite were all built, wired, and passing 17/17 on every release tag.

**Why it survived:** the file lives at the **repository root**, outside `docs/runtime/`, so the
`Runtime Docs Validation` frontmatter and `last_verified` checks that catch exactly this never
looked at it.

The status now says implemented, and — deliberately, so the correction does not over-reach in
the other direction — states what is **not**: Tier-2 is certified on Linux only (`C3` open), and
the provider is reachable from a single seam (`TOOL-SEAM-ISOLATION-1`, `EXEC-ENV-BIND-1`).

### Added — the nodus upgrade checklist is now executable (`NODUS-UPGRADE-1`, #467)

`nodus-lang` is pinned **exactly**, so an app cannot adopt a nodus release on its own and bumping
promptly is the runtime's obligation. What to re-verify before each bump lived as prose in
`CLAUDE.md`. It is now `tests/unit/test_nodus_upgrade_contract.py`, asserted against the
**installed nodus package** rather than a mock — the whole point being `GUEST-CONFINE-1`'s note
that *a renamed argument leaves the guest unconfined while every VM-mocking test still passes*.

What it pins:

- the three confinement flags (`allow_subprocess`, `allow_network`, `allow_env`) are still
  accepted, and still **keyword-only**;
- `NodusRuntime._get_active_vm` still exists — a *private* method `nodus_worker.py:409` depends
  on, referenced nowhere else in the repo and therefore carrying no compatibility promise;
- `nodus.services.syscall_runtime.call_syscall` is still where the `std:sys` fail-loud guard
  patches it (`NODUS-SYS-SURFACE-1`);
- the installed `nodus-lang` matches the pin in `pyproject.toml`.

**★ Two things this turned up.**

**`NodusRuntime.__init__` has no `**kwargs`.** That is good news worth pinning: a renamed
confinement flag raises `TypeError` at construction instead of being silently swallowed, so the
worker fails closed. Had there been a catch-all, `GUEST-CONFINE-1` could recur invisibly — which
is how it went unnoticed the first time. A test now asserts the absence of the catch-all, because
it is a property of someone else's code that our confinement depends on.

**"nodus forbids overriding a builtin" was only ever a docstring.** `NODUS-SYS-SURFACE-1`'s
fail-loud guard rests on that refusal — if a nodus release allowed overrides, a guest could
redefine `syscall` and bypass the guard, and every existing test would still pass because they
all *assume* the refusal rather than check it. Now asserted against the real VM for `print`,
`len` and `syscall`, so a failure distinguishes *"this builtin became overridable"* from *"the
refusal mechanism is gone"*.

### Fixed — CI had been testing `nodus-lang 4.1.0` while the wheel required 4.2.0

**★ The version check found this on its first CI run, and it is the reason the check exists.**

`Runtime Contracts` and `Integration Tests` both install with:

```
python -m pip install -r AINDY/requirements.txt
python -m pip install -e .[test] --no-deps --no-build-isolation
```

**`--no-deps` means `pyproject.toml`'s pins are never applied in CI.** The effective environment
is `AINDY/requirements.txt` — which still said `nodus-lang==4.1.0`. The pin moved to `4.2.0` in
#451 (FR-16, the app team's requested nodus upgrade) and that PR did not update the second file,
which had carried `4.1.0` since the initial repo extraction.

So since #451 every green run — **including the ones that signed off FR-16** — exercised the
version being upgraded *away from*, while the published wheel required the new one. The nodus
4.2.0 adoption was never actually tested.

`AINDY/requirements.txt` is corrected to `4.2.0`, and
`tests/unit/test_dependency_pin_agreement.py` now fails when the two sources disagree about any
shared package. Exactly one had drifted, so this was a missed edit rather than systemic rot —
which is what a guard is for, since the next bump can miss it identically.

**Adoption note for the next nodus release: bump both files.** The guard will say so if you
don't.

### Added — a pin that cannot be installed now fails at the developer's desk, naming the culprit

A pin can be written, committed and merged while being **impossible to install**, because another
package in the same environment caps it. pip then resolves *down* without complaint, and the only
symptom is that the installed version differs from the declared one — which says nothing about
who is responsible.

`test_no_installed_package_forbids_our_declared_pins` checks every exact pin in `pyproject.toml`
against the stated requirements of every installed distribution, and fails with the offender
named:

```
nodus-mcp requires nodus-lang<5.0.0,>=4.0.0 but we pin ==5.0.0
```

**Found by walking into it.** Bumping `nodus-lang` to 5.0.0 passed locally and failed CI with
`installed nodus-lang 4.2.0 != pinned 5.0.0`. The cause is `nodus-mcp 0.1.2`, which requires
`nodus-lang<5.0.0`; CI installs it *after* `requirements.txt`, so pip silently downgraded
nodus-lang to satisfy it. `pip install nodus-lang==5.0.0 nodus-mcp` is a flat
`ResolutionImpossible`.

Local had been green only because the environment was in a state pip would never produce —
`nodus-lang 5.0.0` force-installed alongside `nodus-mcp 0.1.2`. `pip check` flagged it; nothing
in the test suite did. This closes that gap.

Our own distribution is excluded from the scan: in an editable dev install its recorded metadata
is whatever it was at `pip install -e .` time and goes stale on every pin change, which would
fail for a reason that is not a conflict. `pyproject.toml` is the authority on our own
declaration, and the existing tests already compare it against `AINDY/requirements.txt`.

Same family as `MCP-SDK-2X-1`: an ecosystem package capping a dependency and blocking an upgrade
until it ships a compatible release.

### Added — `ROUTE-EFFECT-BYPASS-1` filed: four memory routes reach effects without the dispatcher (#459)

Split out of `HTTP-SCOPE-GAP-1` because the fix is different work — that entry is about scope
checks not reaching routes; this is about routes not reaching the chokepoint at all. **A scope
decorator on a route that skips the dispatcher still leaves the effect unmediated.**

Documentation only; no behaviour change.

**Measured smaller than the parent entry implied.** `HTTP-SCOPE-GAP-1` notes `memory_router.py`
has "zero `SyscallDispatcher` references", which reads as if all 22 routes bypass. **18 of 22 go
through `_mem_run_flow` → `run_flow` → the dispatcher.** Four do not: `POST /nodes` and
`POST /links` (writes), `POST /nodes/search` and `POST /recall` (reads) — and the file carries
**no scope checks either**, so those four have neither gate.

### Fixed — two memory routes now reach effects through the dispatcher (`ROUTE-EFFECT-BYPASS-1` A+B, #460)

`POST /memory/nodes` and `POST /memory/recall` called `MemoryNodeDAO` directly with the request's
own session, so the effect passed **no capability check, no tenant-isolation check, no quota
accounting and no effect ledger**. A scope decorator would not have helped — the effect never
reached the chokepoint that reads scopes.

Both now dispatch. `POST /nodes` goes through `sys.v1.memory.write`, which since the `IDEM-11`
audit declares **`EXACTLY_ONCE`**, so it gains at-most-once as well.

**★ `sys.v1.memory.write` now merges the caller's `extra` instead of replacing it.** It hard-set
`extra={"execution_unit_id": …}`, discarding anything the caller sent. The route passes
`extra=body.extra`, so rewiring without this fix would have been **silent data loss behind a
201** — not a failure. `execution_unit_id` still wins a key collision, so provenance stays
non-forgeable.

The dispatch helper hands the **request's own session** to the handler via `_db`, keeping the
write inside the caller's transaction; opening a second session per request is the shape
`RT-MEMTXN-LEAK-1` traced to pool exhaustion. A non-success envelope raises `HTTPException`
rather than returning 200 with an error body (`ROUTE-GUARD-1`).

**Two routes deliberately not rewired**, and a test pins which: `POST /links` has **no syscall
equivalent** (a build, not a rewire), and `POST /nodes/search` calls `dao.find_similar` with
`min_similarity`, which `sys.v1.memory.search` neither accepts nor uses — it calls `dao.recall`.
Rewiring that one would change search *semantics* under cover of a mediation fix.

### Added — `sys.v1.memory.link`, and `POST /memory/links` now dispatches (`ROUTE-EFFECT-BYPASS-1` C, #461)

`POST /memory/links` reached `MemoryNodeDAO.create_link` directly, so building the memory graph
passed **no capability check, no tenant-isolation check and no effect ledger**. Unlike items A+B
this was a build, not a rewire — no link syscall existed.

**★ It carries its own `memory.link` capability, which `memory.write` does not grant.** A syscall
that adds a mediation hop and no authority granularity would just relocate the same
undifferentiated power behind a longer call path. Writing a *node* and wiring the *graph between
nodes* are different powers; `memory.delete` already set the precedent of a memory capability
`memory.write` does not confer. A test drives the dispatcher with a `memory.write`-only context
and requires refusal, so the split is a boundary rather than a label.

Declared **`EXACTLY_ONCE`** (`IDEM-11`): `create_link` inserts a row, so a retry builds a *second*
edge between the same pair. Registry floor `SYSCALL_REGISTRY_MIN_COUNT` 23 → 24.

**Tenant scoping is the syscall's, not the route's.** Both endpoints resolve through a
tenant-scoped `get_by_id` before the write, and a node belonging to another tenant is reported
identically to one that does not exist — distinguishing them would make the route an existence
oracle for other tenants' ids, which is the `/auth/register` enumeration shape somewhere else.
The route keeps its status contract: **404** for an unresolvable node, **422** for a link the DAO
refuses, rather than collapsing both to 400.

**Deliberately off the `POST /platform/syscall` dispatch surface.** `memory.link` is absent from
`_DISPATCH_CAPABILITY_SCOPES`, so SDK callers get an empty grant and the dispatcher denies it;
the syscall is reachable only from the HTTP route that already had the caller. That is the
conservative order for a `stable=False` entry — publishing an experimental syscall to SDK callers
is the half that cannot be withdrawn. Two tests pin the omission as a decision. Adding it later
means a `Scopes.MEMORY_LINK` of its own; mapping it onto `MEMORY_WRITE` would undo at the scope
layer exactly the split the capability makes above.

Direct-DAO routes in `memory_router.py`: **2 → 1**. The last is `POST /nodes/search`, which calls
`dao.find_similar` with `min_similarity` — `sys.v1.memory.search` neither accepts nor uses it
(it calls `dao.recall`), so rewiring would change search *semantics* under cover of a mediation
fix. A test pins the count in both directions: a drop means the remaining work landed, a rise
means a new bypass was introduced.


## 2.3.0 — 2026-08-16

**Shape: MINOR — `2.3.0`.** No signature, route or response contract was removed or narrowed.
`recommended_runtime_requirement` derives from the major, so it stays `>=2.0,<3.0` and **no
consumer pin has to move**.

| | |
|---|---|
| Schema contract | `2026-08-15.1` — **unchanged** |
| Alembic head | `0016` — **unchanged**, no new revisions |
| Consumer pin | unchanged |

**No schema work on upgrade**, verified by diffing `AINDY/db/models/` and
`memory_persistence.py` against `v2.2.0` rather than assumed. The new `Upgrade Path Guard`
therefore passed **trivially** on this release — see its entry below for why that is stated
rather than presented as evidence.

### ★ One behaviour change to read before upgrading

### ★ Changed — a JWT session is no longer exempt from scope checks (`HTTP-SCOPE-GAP-1`, #449)

**Read before upgrading.** `enforce_api_key_scope` gated API-key callers only — its own
docstring read *"JWT users carry full trust and are never gated by this check"* — so **an
interactive browser session was strictly more privileged than any API key**. It no longer is.

- A JWT session now carries `session_scopes`, **derived from `User.is_admin` on every request**,
  not from a token claim. Nothing is baked into the token, so **no session is invalidated by this
  change** — unlike 2.0.0's `purpose` claim — and an admin grant or revocation takes effect on the
  next call rather than the next login.
- **Ordinary session:** `flow.read`, `flow.execute`, `memory.read`, `memory.write`, `agent.run`,
  `execution.read`. **Admin adds:** `webhook.manage`, `platform.admin`.
- **Neither set includes `memory.delete` or `event.emit`.** An API key can still be granted them
  explicitly; a browser session cannot inherit them by virtue of being logged in.
- Escape hatch: `AINDY_JWT_SCOPE_ENFORCEMENT=0` restores the old bypass. It is a hatch for an
  incident, not an opt-in.

**Why this ships enforcing rather than default-off**, unlike most boundary tightening here: the
blast radius is *countable*. Only **7 of 147** route decorators enforce a scope at all, and the
only three they require — `flow.read`, `flow.execute`, `memory.read` — are all in the ordinary
set. **Every signed-in user still passes every currently-enforcing route.** That enumeration is
pinned by a test that scans the source, so adding an enforcement an ordinary session cannot
satisfy fails in CI rather than as a 403 in someone's browser.

The scope surface comes from the app team's real call surface, not from our guess, and both of
their stated constraints are honoured: admin keys on the **existing user-row flag** (one source of
truth for "operator"), and nothing here tries to answer data ownership — `execution.read` still
says *may I read executions*, not *whose*.

`enforce_api_key_scope` keeps its name despite now covering both principal types: it appears at 7
call sites and in the app team's notes, and renaming a security-relevant surface for cosmetics is
churn.
### Changed — changelog entries are now files in `changelog.d/` (#452)

A PR still writes its own entry in the same PR — the protocol in `CLAUDE.md` is unchanged. It is
now a **new file** in `changelog.d/` rather than an edit to `CHANGELOG.md`'s `## Unreleased`.

Editing one shared section made every concurrent PR collide, three times in one afternoon
(#449/#450/#451). The failure mode was worse than the annoyance: the reflexive "keep mine"
resolution **silently reverted another PR's entry**, and a dropped changelog paragraph breaks no
build. A new file cannot conflict with another new file.

- Create `changelog.d/<PR>-<slug>.md`; prefix **`00-`** if an operator must read it before
  upgrading, which is how the protocol's "at the top, not buried" rule becomes mechanical.
- `python scripts/assemble_changelog.py` folds fragments in and deletes them; `--check` verifies
  none are stranded. **A release step, never a per-PR gate** — fragments are supposed to exist
  during development, so gating on their absence would invert the design.

This entry is itself a fragment.

### Fixed — scheduler jobs no longer starve each other (`SYSMAX-5`, #453)

The scheduler ran ~33 jobs (12 runtime + ~21 app-registered) against a **single pool of 10
workers**, with two unbounded holders: `scheduler_heartbeat_tick` occupies a worker for the whole
duration of an INLINE execution (~13 minutes in the `FR-15` incident), and DB-heavy jobs can
block for `DB_POOL_TIMEOUT` (60s) under connection-pool exhaustion.

The failure mode was a **maintenance brownout**: the pool saturates and the remaining jobs
silently stop running — including the recovery jobs whose purpose is cleaning up after the
condition that saturated it. Nothing raised.

**Three lanes, sized for isolation rather than capacity:**

| Lane | Workers | Holds |
|---|---|---|
| `default` | 10 | ordinary maintenance + every app-registered job |
| `recovery` | 2 | the six jobs whose value *peaks* when the scheduler is saturated |
| `waits` | 1 | time-wait firing (`FR-15` (b)) |

**★ Raising `default` would have been the wrong fix**, not merely a weaker one:
`DB_POOL_SIZE` (10) + `DB_MAX_OVERFLOW` (20) = **30 connections shared with request handling**.
Twenty concurrent scheduler jobs each holding a session would leave ten for the API — the
RT-MEMTXN-LEAK-1 shape, where a login took 42 seconds. A test asserts the lanes stay within half
the DB budget, so that trade cannot be made accidentally.

`queue_backend_reconnect` is the sharpest case: if the queue backend is down *and* the pool is
saturated, the job that would reconnect it could not run — a self-sustaining outage.

**New metric `aindy_scheduler_job_starved_total{job_id,reason}`.** APScheduler reports
saturation only as a per-job log line (*"maximum number of running instances reached"*) — which
is exactly what the `FR-15` incident printed once per starved second while nobody could see it
as a signal. `reason` separates `max_instances` (previous run still going) from `missed` (no
worker free); they have different causes and different fixes.

### Fixed — `remove_job` failures were swallowed under a misleading comment (#454)

Found by auditing the vendored `apscheduler` shim after the same gap appeared twice
(`FR-15` (b), `SYSMAX-5`).

`pytest.ini` sets `pythonpath = . AINDY`, so `import apscheduler` resolves to
`AINDY/apscheduler` — a hand-written shim — for **every test in this repo**. Anything the
runtime calls that the shim does not implement is untested by construction, and where the call
sits inside a `try/except` it fails *silently*.

- **`nodus_schedule_service._remove_from_scheduler` caught `Exception` and passed**, under a
  comment saying *"Job may already be gone"*. The shim had no `remove_job`, so under test the
  call raised `AttributeError` and was swallowed — **removal could have been a permanent no-op
  with every test green**, and a renamed scheduler API would have looked identical to a
  legitimately deleted job. Now only `JobLookupError` is silent; anything else warns.
- The shim gained `get_job`, `remove_job` and `jobstores.base.JobLookupError`, raising the same
  type production raises so a test exercises the same branch.
- **New guard, derived from source rather than a maintained list:** a test scans `AINDY/` for
  methods called on the scheduler and fails if the shim cannot express one. A hand-written list
  of expected methods would drift exactly as the shim did.

Also pinned: **`import nodus` must resolve to the installed package**, not `AINDY/nodus/`. That
directory shares the real package's name *and* `runtime/embedding.py` shares its exact module
path — the path `GUEST-CONFINE-1`'s tests import `NodusRuntime` from to assert 31 builtins are
refused. Today the collision is self-limiting (the file is a re-export, so shadowing would
self-import and fail loudly), but that depends on it staying a re-export.

### Added — CI now exercises the upgrade path against an existing database (`FR-8` / `FR-14`, #455)

The class of failure no other check could see. **Every existing job builds a fresh database**,
where `create_all` produces whatever columns the current build declares and there is nothing to
reconcile — which is why the app team's own `deploy-bootstrap-guard.yml` passed while their live
stack was crash-looping on 2.1.0. The failure only exists when a database **predates** the schema
change, which is true of every real deployment and no CI run.

`Upgrade Path Guard` builds that state deliberately: install the **previous released wheel from
PyPI**, `bootstrap-schema` against a fresh database, install **this build** over it, and
`bootstrap-schema` again. That last step is the one that took a stack down. It must either
succeed or exit **3** (`additive reconcile required` — the branchable code from `FR-14`), and
`--reconcile` must then resolve it and stay stable on re-run. It finishes by booting `serve`,
because `FR-14`'s actual symptom was a container that never reached it.

**★ Read this before treating a green run as proof.**

**This release contains no runtime schema change, so the guard passes trivially here.** There is
no drift to detect, and on such a release *a broken guard and a clean release look identical* —
which is precisely the "green because there was nothing to catch" trap this repo has catalogued
seven times.

That is why the workflow ships with a **`negative-control` job** that injects synthetic drift
(dropping `agents.updated_at`, reproducing `FR-13`'s shape) and **requires** the guard to report
exit 3. The control is the load-bearing half on any release without a schema change: if it ever
passes silently, the upgrade-path job is decorative and should not be trusted.

**Not yet a required check.** Promote it only after reading a real run — and read the
`negative-control` result, not just the overall green.

*Correction, recorded because it contradicts a note elsewhere in this repo:* **this workflow DID
run on the pull request that added it.** `CLAUDE.md`'s `NATIVE-CI-1` entry says a new workflow
file does not trigger on its own PR; that holds for `push`-triggered workflows, but a
`pull_request` trigger fires from the PR's merge ref and so does run. It failed on that first
run — for two setup reasons of its own (a missing `CREATE EXTENSION vector`, and a one-shot PyPI
lookup that hit a connection reset) — which is a better outcome than a first run that only
happens after merge.


### Changed — `nodus-lang` 4.1.0 → 4.2.0 (`FR-16`, #451)

`Requires-Dist: nodus-lang==4.1.0` is an **exact** pin, so an app cannot adopt a nodus release
on its own: `pip install nodus-lang==4.2.0` succeeds and leaves the environment inconsistent
with the runtime's declared requirement — worse than a clean refusal. Reproduced here, an
editable install of this repo **downgraded 4.2.0 back to 4.1.0**.

**The pin stays exact.** Hard-pinning a language runtime is defensible; what it creates is an
obligation to bump promptly, which this does.

Risk-probed before landing, and one check is new since the last bump:

- **`GUEST-CONFINE-1`'s confinement depends on VM constructor arguments.** `allow_subprocess`,
  `allow_network` and `allow_env` all survive with identical defaults, and **all 31 gated
  builtins are still refused under 4.2.0** — verified against the real VM, because a silently
  renamed argument would leave the guest unconfined while every test that mocks the VM passed.
- The three long-standing fragile couplings survive (`syscall_runtime.call_syscall`,
  `NodusRuntime._get_active_vm`, `register_function`), and `register_function` still refuses to
  override a builtin — which `NODUS-SYS-SURFACE-1`'s fail-loud guard depends on.
- **Its breaking change — "every error now reports the resolved absolute path" — does not affect
  this repo:** nodus errors are forwarded, never parsed. Nothing matches on error text.

No runtime code change. 4.2.0 fixes a resume path (`RESUME_TIMEOUT_MS`, store-lock scanning, and
a sweeper adopting runs it did not create) that the app team runs under
`AINDY_REASONING_NODUS_NATIVE`.

### Changed — `bootstrap-schema` exits with branchable codes (`FR-14`, #450)

Every not-ready state exited **1** — the same code as "DATABASE_URL is not set". A container
entrypoint running the command bare under `set -e` could not tell *"re-run me with --reconcile"*
from *"your config is broken"* from *"a human must migrate"*, so it exited, restarted, and left
the reason only in a log. That took a live stack down on 2.1.0.

**The refusal itself is unchanged.** No DDL is applied by default; that was never the ask.

| Exit | Meaning | Safe to automate? |
|---|---|---|
| `0` | success | — |
| `1` | configuration error (e.g. `DATABASE_URL` unset) | no — fix the environment |
| `2` | database layer could not be imported | no — packaging problem |
| **`3`** | **additive reconcile required** | **yes** — `--reconcile` adds, never drops |
| `4` | offline migration required | no — `--reconcile` will not help |
| `5` | manual repair required | no |

`1` and `2` deliberately keep their meanings: the value of 3/4/5 is that they are *not* `1`, or
an entrypoint would retry a broken environment forever. When a report indicates both, **`4` wins
over `3`** — reporting `3` there would invite an entrypoint to auto-reconcile a database that
needs a person.

`bootstrap-schema --help` now states that a bare invocation under `set -e` is a crash loop in a
container, and that the bare form is the right *interactive* shape.

**Still open, and it is the half that prevents recurrence:** the upgrade path is never exercised
against an *existing* database. CI builds a fresh one, where `create_all` produces the new columns
and there is nothing to reconcile — so no green check can see this class of failure. The same
blind spot hid `FR-8`.

### Added — `child_context` can no longer widen authority (`AUTHORITY-VALUE-1`, opt-in, #448)

`child_context()` granted whatever `capabilities=[...]` it was handed, **whether or not the
parent held them** — so it could *widen* authority, not merely inherit or narrow it.
`mint_token` already enforces the correct invariant for delegated runs (`capability_ceiling`);
this neighbouring path was left conventional.

- New `AINDY_CHILD_CONTEXT_CLAMP` (default **off**, resolved per call). When on, a child's
  capabilities are clamped to the parent's grant — narrowing always allowed, widening dropped.
- **A widening now logs a WARNING regardless of the flag.** That is the point: the real exposure
  has never been counted, and a boundary is better tightened on a measurement than an argument.

**Default-off is deliberate, and not conservatism.** Applying the clamp unconditionally **breaks
app automation syscalls today**: `aindy-apps-monolith`'s `_dispatch_owner_syscall` builds a child
granting the *nested* syscall's capability, while the parent context carries **exactly the outer
syscall's own capability** — so the intersection is empty and the nested dispatch is denied. Flip
the flag only after that caller is given a legitimate grant.

No behaviour change with the flag off, beyond the new warning.


## 2.2.0 — 2026-08-16

**Shape: MINOR — `2.2.0`.** No signature, route or response contract was removed or narrowed.
`recommended_runtime_requirement` derives from the major, so it stays `>=2.0,<3.0` and **no
consumer pin has to move**.

| | |
|---|---|
| Schema contract | `2026-08-15.1` — **unchanged** |
| Alembic head | `0016` — **unchanged**, no new revisions |
| Consumer pin | unchanged |

**No schema work on upgrade.** Nothing under `AINDY/db/models/` or `memory_persistence.py` was
touched and no migration was added, so this release does not exercise the `bootstrap-schema`
path that `FR-14` reports as broken for additive-column releases.

### ★ Two things to read before upgrading

**★ A guest Nodus script can no longer reach subprocess, network or host environment.**
This is a confinement fix, so it is a *narrowing*: any `.nd` / `.nodus`
script that called `subprocess_*`, `http_*` or `env_get` now fails with a `SandboxError`
instead of succeeding. **Measured before shipping: no first-party script in `aindy-runtime`
(8 scripts) or `aindy-apps-monolith` (2 scripts) uses any of them**, so this is expected to
break nothing — but a third-party script that relied on the old behaviour will stop working,
deliberately and loudly. Mediated egress (via `sys()` / `call_tool`) is unaffected.

**★ The scheduler now runs wait firing on its own job and its own thread**, and emits a new
`scheduler.queued` event per queued execution unit. Neither changes an API, but both change what
an operator sees: expect one additional event type in `system_events` at roughly the volume of
`execution.started`, and a second scheduler job in any dashboard that enumerates them. Turn the
event off with `AINDY_SCHEDULER_QUEUE_EVENTS=false` if the volume is unwelcome.

**Known-open and deliberately not in this release:** `FR-15` (a) — dispatch still runs INLINE by
default, so work still queues behind a single 1s tick. This release makes that wait *visible*
(`scheduler.queued`) and stops it starving timers and health checks (the wait-tick split), but
does not remove it. Flipping `AINDY_ASYNC_HEAVY_EXECUTION` is the remaining step and wants soak.

### Fixed — a slow execution no longer stops parked flows from waking (`FR-15` (b), #443)

Wait firing ran only as a prelude to dispatch, inside `schedule()`. Dispatch is INLINE by
default (see `FR-15`) and the driving APScheduler job is `max_instances=1`, so while one flow
executed the next tick was **skipped entirely and no time-based wait fired**.

**That is a correctness bug, not a latency one:** a flow parked on a timer stayed parked because
an *unrelated* flow was busy. It is also why `/health` went down for 13 minutes in the reported
incident — the same tick drove wait expiry and stale-wait cleanup.

- Wait maintenance moved to its own `scheduler_wait_tick` job (`tick_waits()` on the engine).
- **And its own executor.** Splitting the job is necessary but not sufficient: `max_instances`
  is per-job while the thread pool is shared, and this scheduler registers **16 jobs against
  APScheduler's default pool of 10**, several able to block for `DB_POOL_TIMEOUT` (60s) — the
  exact condition present during the incident. A dedicated single-thread executor makes the
  guarantee structural rather than probabilistic.
- `schedule()` gains `tick_waits: bool = True`. **The default preserves the historical
  behaviour** for any direct caller; the runtime's own tick passes `False`.

Safe by construction: `tick_time_waits` claims a due wait by removing it from `_waiting` under
the engine lock and fires it only after releasing, so the two paths cannot double-fire one wait.
Asserted with 8 concurrent tickers over 25 due waits, not assumed.

**Test-shim gap closed in passing.** `pytest.ini` sets `pythonpath = . AINDY`, so `import
apscheduler` resolves to the vendored shim in `AINDY/apscheduler` — which **silently dropped
`**kwargs`**, meaning no test could assert any job's `executor`, `max_instances` or `coalesce`,
despite `max_instances=1` being load-bearing here. The shim now records them and gained an
`executors.pool` module, without which the dedicated-executor branch would have shipped
**unexercised by any test**.

**No dispatch behaviour change.** `FR-15` (a) — flipping `AINDY_ASYNC_HEAVY_EXECUTION` — remains
open and still needs soak.

### Added — `scheduler.queued` event and a queue-wait histogram (`FR-15`, #442)

Between an item entering the scheduler queue and `execution.started` firing, **nothing was
emitted**. The app team measured a **177-second** window of that silence; inside it a queued
request and a hung process are externally indistinguishable, which is what turned a diagnosis
into a three-hour investigation.

- **`scheduler.queued`** SystemEvent at enqueue, carrying `queue_depth` — the number that
  separates *"queued behind 40 things"* from *"queued alone and the dispatcher is wedged"*.
  Those have the same external symptom and completely different causes. It lands in
  `system_events`, the table operators actually query after the fact.
- **`aindy_scheduler_queue_wait_seconds`** histogram observed at dispatch. Buckets run to
  **300s** on purpose: the observed pathological waits were 22s / 48s / 184s, so a histogram
  topping out near 10s would have put every interesting sample in `+Inf`. The depth gauge
  (`aindy_scheduler_queue_depth`) already existed and is already scraped — only the *duration*
  half was missing.
- Off switch `AINDY_SCHEDULER_QUEUE_EVENTS` (default on), resolved per call, no restart needed.

**The event is `scheduler.queued`, not the `execution.queued` that was requested, and the
difference is load-bearing.** The execution-contract gate raises for any `execution.*` event
emitted outside a pipeline, and the two hottest enqueue callers — the event-bus subscriber
thread and wait expiry — have no pipeline active. The requested name would raise in exactly the
paths that matter most. Same reason `recall.used` / `score.computed` are un-prefixed.

**Consumers reading the event stream see one new event type per enqueued execution unit** — the
same order of magnitude as the `execution.started` row that already exists per item.

**This does not change dispatch behaviour.** It makes the existing wait visible. The wait itself
is `FR-15` (b) *decouple dispatch from the 1s heartbeat* and (a) *flip
`AINDY_ASYNC_HEAVY_EXECUTION`*, both still open — see `TECH_DEBT.md` `FR-15` for why the
observability step deliberately shipped first.

### Changed — guest confinement is now verified across the whole gated surface (`GUEST-CONFINE-1`, #440)

`GUEST-CONFINE-1` shipped with three demonstrated blocks (`subprocess_shell`, `http_get`,
`env_get`). Re-verifying it against the merged code showed the guard covers **31** builtins, and
that all 31 are genuinely blocked on the real worker path — a stronger result than was claimed,
now asserted rather than assumed.

- `NODUS_DEVELOPER_GUIDE.md` §1.1 lists the real surface: **7 subprocess / 18 network / 6 env**.
  The previous table named only `env_get` and trailed off, which understated it — it omitted
  **`env_set`** and **`env_unset`** (host environment *writes*) and `subprocess_spawn`.
- Two new tests. One derives the gated names from nodus's own registry and asserts each is
  refused, so a future nodus builtin the flags fail to cover fails CI instead of silently
  widening the guest surface. The other is a floor check on the count of 31, because a doc claim
  carrying a number decays unless something asserts it (`DOCS-COVERAGE-CLAIM-1`).
- **Documented trap:** arity. A bare `http_get()` returns an *arity* error, not a `SandboxError`
  — which reads as "the guard is missing" when it is present. The tests probe arities 1–3 and
  accept only a sandbox error as proof.

Mutation-checked: removing `allow_network=False` fails 3 tests including the whole-surface one,
and the liveness control still passes.

### Fixed — the guest VM ran unconfined (`GUEST-CONFINE-1`, P0, #438)

`nodus_worker` builds the Nodus VM for **submitted script content**, not first-party code, but
passed none of the VM's confinement arguments. The VM defaults to `allow_subprocess=True,
allow_network=True, allow_env=True`, under which nodus registers the **real** `std:subprocess`
and `std:http` modules — so a guest script could reach subprocess, network and the host
environment **without touching the syscall dispatcher, capability token, effect ledger, egress
guard or tool registry**. Every authority check the runtime performs was bypassable by not
going through it.

This was **demonstrated, not inferred** (2026-08-15): driving `nodus_worker.run_one()`,
`env_get("PATH")` returned the real host PATH, `http_get` performed real DNS, and
`subprocess_shell` returned `exit_code: 0` and **created a file on the host filesystem**.

- The worker now constructs the VM with `allow_subprocess=False, allow_network=False,
  allow_env=False`. A denied call returns a structured `SandboxError` naming the flag, so a
  guest can tell *denied* from *the runtime broke*.
- **Deliberately not configurable by an env var.** A global flag would re-open the hole for
  every run at once. Per-execution variation is `EXEC-ENV-BIND-1` (the environment descriptor),
  which is the correct shape and is tracked separately.
- **Scope:** this closes the *guest boundary*. It was never an unauthenticated remote path —
  `POST /platform/nodus/run` requires `get_current_user` and is rate-limited.
- **Note on the filesystem half:** the VM already confined file access (`allowed_paths`
  defaults to the cwd). The demonstrated host-file write went through subprocess, which is not
  subject to that check at all — so `allow_subprocess=False` is what actually closed it.
- **Neither pre-execution validator could have caught this**, for two different reasons:
  `validate_nodus_source` only blocks Python-isms (`import`, `__import__`, `eval`, `exec`) plus
  a length cap. `validate_requested_operation_usage` *does* run on every authorized execution
  (via `authorize_nodus_execution`, called from `nodus_execution_service.py`) — but its
  vocabulary is `ALLOWED_OPERATION_CAPABILITIES`, which contains **8 memory operations and
  nothing else**; `subprocess_*`, `http_*` and `env_get` were never in it, so it never looked.
  Confinement was always the VM's job, and the VM was not asked.

Regression suite: `tests/unit/test_guest_confinement.py` (5 tests, `runtime_only`, so
`Runtime Contracts` selects it). It drives the real worker entry point rather than asserting on
the construction site's source text (`ROUTE-GUARD-1`), and carries an explicit **liveness
control** — every other assertion in the file is an assertion of *absence* and would pass
trivially against a VM that was simply broken (`EVENTBUS-COVERAGE-1`). **Mutation-tested: with
the three arguments removed, 4 of 5 fail and the liveness control still passes**, which is the
intended shape.

### Changed — `CLAUDE.md` trimmed from 154 KB to 66 KB (docs-only, #438)

No behaviour change; recorded because `CLAUDE.md` is the agent-instruction surface and its
*size* is load-bearing — it is loaded into every session.

- The `TECH_DEBT.md` prefix registry was **104 KB, 68% of the file**, and was a lossy duplicate
  of `TECH_DEBT.md`, whose entries are 2–5× longer. It is now a one-line-per-item index that
  says so. Verified: all 114 identifiers still resolve (96 named in `CLAUDE.md`, 18 in
  `TECH_DEBT.md`), **zero orphaned**.
- Its own header claimed "open items only" while carrying **25 fully-closed entries (32 KB)**.
  Closed entries whose *rule* still bites keep a line; inert history became a name.
- **Corrected a stale directive:** the release section still described 2.1.0 as unreleased and
  `main` as 45 commits ahead of 2.0.1, after the tag had shipped. Also dropped three completed
  directives (the cargo job, the cryptography 48→49 check, the UI major cluster).
- Removed a duplicated `admin_router.py` row; compressed justification prose while keeping every
  rule verbatim; the "trusting a green check" catalogue said "six" and listed seven — now eight,
  including `ROUTE-AST-UNWIRED-1`.

### Added — `register_syscall` can declare an execution guarantee (`IDEM-11`, #439)

`SyscallEntry` has always accepted `execution_guarantee`; **`register_syscall` never forwarded
it.** Every syscall registered through that function — i.e. **every app/plugin syscall** — got
`AT_LEAST_ONCE` with **no way to opt in**. The at-most-once gate was unreachable for plugin
syscalls *by construction*, not by configuration.

- `register_syscall(..., execution_guarantee="EXACTLY_ONCE")` now works, and is validated
  against the two accepted values. A typo'd `"EXACTLY ONCE"` **raises** rather than silently
  becoming `AT_LEAST_ONCE` — a silent downgrade is indistinguishable from never declaring it.
- No behaviour change for existing callers: the default is unchanged.

### Fixed — 6 non-idempotent syscalls were undeclared (`IDEM-11`, #439)

A per-syscall audit of all 23 built-ins. **Declared `EXACTLY_ONCE` went 1 → 7:** `event.emit`,
`flow.run`, `flow.execute_intent`, `nodus.execute`, `job.submit`, `agent.undo` join
`memory.write`. Each is a call where a retry produces a *second* effect — a duplicate event,
flow run, script execution or job.

**These declarations are inert** until `AINDY_SYSCALL_IDEMPOTENCY` is enabled (default off) or
the run is a durable continuation, so this change carries no behaviour change on its own. The
flag flip is deliberately separate and follows soak.

Reads and genuinely convergent writes were left `AT_LEAST_ONCE` on purpose — `agent.cancel`
(CAS to a terminal status), `agent.ensure_initial_run` (find-or-create), `agent.simulate`,
`memory.delete` (delete-by-id), `agent.execute` (guarded by its `approved` precondition).
Over-declaring is not free: it puts a ledger write on a hot path.

**Two filed measurements were wrong and are corrected in `TECH_DEBT.md`:** the registry holds
**23** entries, not 27; and the one pre-existing `EXACTLY_ONCE` was **`memory.write`**, not
`memory.delete`. That inverts the significance — the guarded call was the runtime's busiest
write path, not the syscall with zero callers.

### Fixed — an `EXACTLY_ONCE` result that isn't JSON-serializable no longer fails the call (`IDEM-11`, #439)

The gate caches the handler's return in a **JSONB** column. The **tool** path (MEB-0) has always
`json.dumps`-checked it and degraded to caching nothing with a warning. Its **syscall** twin
(MEB-1b) had no check and no `try`.

So a handler returning a `UUID` / `datetime` / ORM object raised inside the ledger commit,
unwound to `dispatch()`'s belt-and-suspenders handler, and came back as an **error envelope —
after the effect had already happened**. The caller is told a side-effecting syscall failed when
it succeeded. It surfaces only with the flag on, i.e. **exactly when someone flips it**, which is
why it mattered to fix before the flip rather than after.

Behaviour now matches the tool path: warn, cache nothing, let the call succeed. A ledger failure
also degrades to `AT_LEAST_ONCE` instead of failing a call whose effect is already real.

### Added — `IDEM-12`: `agent.undo` double-compensates on a second call (latent, #439)

Found while doing the audit above. `undo_run_effects` selects effects by
`status == "success"`, **never marks them reversed** and never consults `effect_reversals` — so
a second `sys.v1.agent.undo` re-invokes **every** compensator (a double refund, a second
reversing transfer) and duplicates audit rows.

**Not live: zero compensators are registered today**, so every effect reports `irreversible` and
the only present harm is duplicate audit rows. It goes live with the first compensator someone
registers. Filed, not fixed — `EXACTLY_ONCE` above is defense-in-depth only, since the gate is
default-off and keys on `(name, payload, scope)`.

### Fixed — the 2.1.0 entry's Dockerfile-pin line was false at publication (2026-08-15)

The `## 2.1.0` entry below says **"The Dockerfile builder-stage pin is still `2.0.1`."** It is
`2.1.0`, and has been since the release itself. That entry is left exactly as written — it is
the audit trail of what was believed then, not a claim to re-litigate — but the line should not
be trusted, and the *reason* it is wrong is worth more than the correction.

**It was not a line that went stale. It was false at the instant it was published.** The history:

- `42dc841` (#425) wrote it under `## Unreleased`, where it was **true and useful** — a standing
  reminder that the pin had not yet been bumped.
- `ea988d1` (#434) promoted `## Unreleased` to `## 2.1.0 — 2026-08-15` **and** bumped the pin
  `2.0.1` → `2.1.0` in the same commit, exactly as `PYPI-PUBLISH-1` requires.

So the single commit that published the sentence is the commit that falsified it. The promotion
carried it along verbatim.

**This is a structural trap between two protocols this repo already has, and it will recur on
every release.** `PYPI-PUBLISH-1` requires the pin bump and the CHANGELOG to land in one PR;
the CHANGELOG protocol requires entries to be authored while the work is in `Unreleased`. Any
*pin-status* sentence is therefore correct in `Unreleased` and self-invalidating on promotion —
the promotion PR is by definition the one that resolves it.

**Guard added:** `docs/runtime/RELEASE_CHECKLIST.md` now makes dropping unreleased-only status
notes an explicit sub-step of the promotion, rather than something the author has to remember.

A useful generalisation for anything written under `Unreleased`: state what a change *is*, not
what has *not yet been done about it*. The first survives promotion; the second cannot.

## 2.1.0 — 2026-08-15

**Shape: MINOR — `2.1.0`.** Everything below is additive. No signature, route or response
contract was removed or narrowed, and `recommended_runtime_requirement` stays `>=2.0,<3.0`, so
**no consumer pin has to move** — unlike the 2.0.0 cut.

| | |
|---|---|
| Schema contract | `2026-08-15.1` |
| Alembic head | **`0016`** (`0015_agents_metadata`, `0016_agents_owner_scoped_name`) |
| Consumer pin | unchanged |

**Two behaviour changes worth reading before upgrading**, both detailed below: `/health/deep`
now reports the event bus **degraded** while publishing is suspended, where it previously
reported it disabled; and several admin routes now return their real status codes (409/404)
where they had been returning **500**.

**The Dockerfile builder-stage pin is still `2.0.1`.** A container serves none of this until a
release is cut *and* that pin is bumped in the same PR.

---

### Added — user-owned agents (`APP-FR-*` FR-12b, #421)

- **`GET|POST /platform/agents`, `PATCH|DELETE /platform/agents/{slug}`,
  `POST /platform/agents/{slug}/restore`** — the authenticated, non-admin half of the agent
  registry. `agents.owner_user_id` has existed all along and, until now, was written by no path
  at all (`count(owner_user_id) = 0` on live deployments).

  This had been deferred as app-layer policy. It is not: ownership, per-owner name scoping and
  owner-scoped reads are properties of the *table*, so every app wanting user-owned agents would
  have rebuilt all three against a schema that fought them. What an app *does* with an agent
  remains app policy.

  Contract details callers need:

  - **`memory_namespace` is derived, not accepted** — `u:<user_id>:<slug>`. Callers supply a
    `slug` matching `^[a-z0-9][a-z0-9._-]{0,63}$`. A caller-chosen namespace would have to 409
    on a row the caller cannot see, which is a cross-tenant existence oracle; deriving makes a
    cross-user collision impossible rather than merely detected.
  - **`agent_type` is forced to `custom`** and is not caller-settable —
    `agent_capability_mappings` is keyed by it.
  - **`POST` is not idempotent** (unlike the admin route): a repeated slug is `409`. An
    idempotent update branch is exactly what silently rewrote platform rows before FR-12.
  - **Another user's agent is `404`, never `403`** — a 403 confirms someone else holds the slug.
  - A principal that does not resolve to a UUID user is `400`, never a silent
    `owner_user_id = NULL` (which would create a *shared* agent).
  - `slug`, and therefore `memory_namespace`, is immutable on `PATCH`: it is the tag already
    written onto the agent's memory nodes.

- **`POST /platform/admin/agents/{namespace}/restore`** — reactivates a deactivated agent
  without a restart, and for a reserved system namespace also restores `name` / `agent_type` /
  `description` from the platform spec.

  This closes a gap that FR-12 *created*: `POST /admin/agents/register` was the only surface
  whose update branch set `is_active = True`, so reserving the seven system namespaces
  (correctly) removed the last route back for exactly the rows that matter most.

### Added — `registry.register_agent(...)`, the agent **identity** hook (FR-12, #418)

- The registry already exposed eight `register_agent_*` hooks and every one registers
  *behaviour*; none registered the agent itself, so the roster was whatever
  `startup._bootstrap_system_agents` hardcoded.
- Declarative by design: it records a spec and touches no database, because plugin load happens
  long before a session exists. `startup._apply_registered_agents()` upserts by
  `memory_namespace` at boot, and *updates* an existing row so an app can change its display
  name or metadata between boots without a manual edit.
- The seven platform system namespaces are **reserved** and rejected, by both the hook and the
  admin route, from one shared `SYSTEM_AGENTS` set.

### Added — `agents.metadata` (JSONB) and `agents.updated_at` (FR-13, #417)

- Alembic **`0015_agents_metadata`**. An agent's durable identity is its *role*; the vendor
  client is swappable detail that previously had nowhere structured to live, so switching
  provider looked like a brand-new agent with no history.
- **The column is `metadata`; the ORM attribute is `Agent.agent_metadata`** — `metadata` is
  reserved on a SQLAlchemy declarative class (`Base.metadata`). Raw SQL sees `metadata`.
- **Purely additive, no backfill.** Both columns are nullable, so `NULL` already means "nothing
  recorded" and every pre-existing row is correct as-is — deliberately unlike FR-8, and a test
  asserts no `reconcile_backfill` marker was added so that reasoning survives an edit.

### Added — configurable runtime-callback budget (FR-11, #413)

- **`AINDY_RUNTIME_CALLBACK_TIMEOUT_SECS`**, default **30s**. `invoke_runtime_callback` had a
  hardcoded 10s budget that no call site could override and no env key exposed.
- **Resolved at call time**, so it needs no restart and stays visible to behavioural tests (the
  import-time env-read hazard). Non-positive or unparseable values warn and fall back.
- Sized on measurement: ~3.85s median cold start on the *lightest* profile is only ~2.6x
  headroom at 10s, while the sibling nodus subprocess budgets 15s for boot alone on top of a
  30s script clock.

### Added — event-bus publish recovery and visibility (#409)

- **`AINDY_EVENT_BUS_PUBLISH_RECOVERY_SECS`**, default 60.
- `get_status()` gains **`publish_suspended`**, **`publish_circuit_state`** and
  **`publish_retry_after_secs`**.

### Added — one loader for the native crate (#415)

- **`AINDY/memory/native_bridge.py`** — `load_bridge()` / `search_paths()`. The two crate
  consumers previously searched *different* directories; see the fix below.

---

### Changed — `agents.name` is unique per owner, not globally (#421)

- Alembic **`0016_agents_owner_scoped_name`**. The old global `UNIQUE (name)` meant the first
  user to register "Assistant" took that name from every other user in the deployment.
- Replaced by two **partial** unique indexes: `UNIQUE (name) WHERE owner_user_id IS NULL` and
  `UNIQUE (owner_user_id, name) WHERE owner_user_id IS NOT NULL`.
- **A plain `UNIQUE (owner_user_id, name)` would not be equivalent** — SQL treats NULLs as
  distinct, so every shared row (all system agents, every app-registered identity) would escape
  the constraint and two rows named "Runtime" would both be accepted.
- **Only widens what is accepted**, so there is nothing to backfill. `memory_namespace` is
  untouched and stays globally unique: it is the tag on every memory node the agent writes.
- Verified against real PostgreSQL on nine properties, including blank-database safety
  (`ALEMBIC-FRESH-DB-1`), idempotent re-run, and an idempotent downgrade that restores the old
  constraint.

### Changed — `/health/deep` reports the event bus **degraded** while publishing is suspended (#409)

- Previously it reported the bus *disabled* in that state, because one field meant two things.
  `_get_propagation_mode()` now returns `local-only` while the breaker is open **even if Redis
  pings OK** — events genuinely are not propagating.

### Changed — `TenantContext.capability_scope` is now a `tuple` (#411)

- The dataclass is `frozen=True` and documented "Immutable", but `frozen` does not deep-freeze:
  while the field was a `list`, `ctx.capability_scope.append("admin.everything")` succeeded and
  `assert_capability` then passed.
- Builders normalise any iterable. `in` / `len` / iteration / `has_capability` are unchanged —
  only mutation now raises `AttributeError`. No known exploit path existed; it was a weak
  invariant, not a live breach.

---

### Fixed — deliberate `HTTPException`s from unmanaged routes returned **500** (`ROUTE-GUARD-1`, #421)

- `enforce_registered_route_execution` wraps every registered route. Its **success** path always
  asked two questions — did this request enter the execution pipeline, and *was it required to*
  — while its **failure** path asked only the first. Routes registered deliberately outside the
  contract (`admin_router`, the new `agents_router`, `automation_router`, all plain DB-query
  handlers) therefore turned every `raise HTTPException` into a contract violation.

  | Call | Was | Now |
  |---|---|---|
  | `POST /platform/admin/agents/register` with a reserved namespace | **500** | `409` |
  | `DELETE /platform/admin/agents/{missing}` | **500** | `404` |

  The first is FR-12's reserved-namespace guard, which blocked the write correctly and then
  reported it as an internal error — and a client cannot tell "rejected" from "the server
  broke" by a 500.

### Fixed — reserved system namespaces on `POST /platform/admin/agents/register` (FR-12, #418)

- Registering with `memory_namespace: "runtime"` took the route's *idempotent-update* branch and
  silently rewrote the platform's own Runtime agent row — name, type and description — for
  anyone with admin, with no repair at the next boot.

### Fixed — `memory_agents_list_node` listed every active agent to every caller (#421)

- Harmless only while every row was un-owned; a cross-user leak of names, descriptions and
  metadata the moment users can own agents. Now scoped to `owner_user_id IS NULL OR = :caller`.

### Fixed — the platform agent roster is repaired at boot, not only seeded (#421)

- `_bootstrap_system_agents` claimed "idempotent upsert by memory_namespace" and was
  insert-only, so a drifted system row was never repaired. Boot now restores `name` /
  `agent_type` / `description` from the spec and logs when it does.
- **`is_active` is deliberately not repaired**: deactivating a system agent is a supported
  operator action, and silently re-enabling it on the next restart would undo that action
  without telling anyone. Boot logs a WARNING naming the restore endpoint instead.
- The roster itself is now a single `SYSTEM_AGENT_SPECS` declaration and the reserved-namespace
  set is derived from it, where the two were previously maintained separately.

### Fixed — a suspended event bus never recovered (`EVENTBUS-PUBLISH-LATCH-1`, #409)

- Three consecutive failed publishes set `self._enabled = False` and **nothing ever set it
  back**, so a *transient* Redis blip ended cross-instance WAIT/RESUME for the life of the
  process — only a restart cured it.
- Root cause was one field meaning two things: `_enabled` was both the operator kill switch and
  the runtime give-up latch. They are now separate — `_enabled` is config and is never mutated
  at runtime; a `CircuitBreaker` carries health (threshold 3, half-open single probe).
- **Suspension was kept on purpose**: dropping it would make every `notify_event()` pay a socket
  connect timeout against a dead Redis. The requirement was *suspend then recover*.

### Fixed — native and Python scorers disagreed for a negative `impact_score` (`NATIVE-PARITY-1`, #408)

- Rust clamped `(impact/5.0).clamp(0.0, 1.0)`; Python used `min(1.0, impact/5.0)` — top bound
  only. Measured +0.300 on a 0.420 score at `impact=-10`; now 0.0 across `-1e6 ... 25.0`.
- **Latent, not live**: `MemoryNodeDAO.save()` writes `max(0.0, ...)` at the universal write
  chokepoint, so no stored row could reach the divergent path. Fixed as defence in depth.

### Fixed — the two native-crate consumers searched different directories (`NATIVE-DISCOVERY-1`, #415)

- `native_scorer` looked in `target/release` + `target/debug`; `embedding_service.cosine_similarity`
  looked in **`target/debug` only** — so on any `--release` build the C++ cosine kernel was
  unreachable from the recall fallback while the scorer, in the same process, used it.
- A second defect found while reproducing it: `sys.path.insert(0, ...)` in *priority* order puts
  the lowest-priority path first, so the documented "release then debug" was inverted and a
  stale debug build silently shadowed a fresh release one.
- **Trip hazard now documented in the loader:** `cargo build` emits `libmemory_bridge_rs.so` /
  `memory_bridge_rs.dll`, and Python imports neither. CI renames; a local build needs it by hand.

### Fixed — `flatten_tree` dropped every intermediate node (`MAS-FLATTEN-1`, #416)

- The root set was "every path minus every path that is some node's parent", which removes the
  *parents*. A root is a node whose parent is not itself a node — the inverse. It now also
  guarantees every node appears exactly once. Zero callers under `AINDY/` today, but the address
  space doc presents it as usable, so it was fixed rather than deleted.

### Fixed — `AINDY/kernel/__init__.py` was a byte-identical copy of `tenant_context.py` (`KERNEL-INIT-DUPLICATE-1`, #411)

- Present since the initial extraction, so `from AINDY.kernel import TenantContext` and
  `from AINDY.kernel.tenant_context import TenantContext` returned **two different class
  objects** — `isinstance` silently `False` across them, for the class that *is* the tenant
  isolation boundary.
- Nothing had broken because nothing imported it: every call site here and in the app repo
  imports a submodule. It is now a real package init re-exporting from the single definition.
- All 337 `.py` files under `AINDY/` were hashed; no byte-identical duplicates remain.

### Fixed — two SDK-dispatched syscalls sat outside the rename guard (`SYSCALL-STABILITY-1`, #401)

- `sys.v1.memory.list` and `sys.v1.execution.get` could have been renamed with CI green,
  breaking the SDK. `SYSCALL_REFERENCE.md` also claimed `stable` for four syscalls registered
  `stable=False`.

### Fixed — a failed runtime callback said nothing about why (#423)

- A worker that died before replying wrote nothing to stdout; `json.loads(stdout or "{}")` made
  that `{}`, `{}.get("ok")` was falsy, and the handler raised its default string. So *"the
  subprocess never started"* and *"the callback returned `{ok: false}`"* produced **the same
  message** — no exit code, no stderr, no callback name.
- Empty stdout is now its own error naming the callback; every failure path reports
  `exit=<code>` plus stderr when present; the timeout path stays distinct.
- This is why `FLAKY-1` has resisted diagnosis: until now every failure of it produced the same
  contentless message regardless of cause. The fix is what will let the next occurrence say which
  cause it was. *(A traceback captured while chasing it appeared to contradict FLAKY-1's recorded
  mechanism; on further evidence — 11 consecutive clean runs — that reading was withdrawn, since
  the sample came from a machine that could not spawn processes. Corrected in `TECH_DEBT.md`
  before release rather than shipped as a wrong claim in these notes.)*

### Fixed — `AINDY_REDIS_URL` alias removed from the rate limiter (2026-08-14)

- **`AINDY/platform_layer/rate_limiter.py`**: resolved `REDIS_URL or AINDY_REDIS_URL`; now
  reads `REDIS_URL` only. This was **the last reader of that alias in the tree**.

  The alias was removed by `EVENTBUS-REDIS-URL-CONSOLIDATION-1` on 2026-06-06, but that change
  was scoped to `event_bus.py`, `config.py` and `.env.example` — this module was never in it,
  and had honoured the alias since the repo's first commit. It also never received the
  `DeprecationWarning` `event_bus.py` got in the preceding change.

  The 2026-06-06 entry's per-file list was accurate; only its summary — *"all components now
  read `REDIS_URL` exclusively"* — over-reached, by exactly one file. That entry is left as
  written: it is the audit trail of what was done then, not a claim to re-litigate now.

  **Low blast radius, and not the silent misconfiguration it first appeared to be.** An operator
  setting only `AINDY_REDIS_URL` in production or under `EXECUTION_MODE=distributed` already
  fails fast at queue init with a `RuntimeError` naming `REDIS_URL`. The narrow window was a
  non-prod thread-mode deployment, where a Redis-backed rate limiter could sit beside an
  in-memory queue, a local-only event bus and in-process concurrency counters.

- **`tests/unit/test_rate_limiter_redis_url.py`** (new): five tests pinning the resolution,
  including that the alias does **not** resolve. Verified by restoring the alias and confirming
  the guard fails. Also covers the empty-string case — Compose renders `${VAR:-}` as `""`, the
  same shape as FR-10.

### Removed — dead branch in the queue factory (2026-08-14)

- **`AINDY/core/distributed_queue.py`**: deleted an `if False:` block wrapping a bare tuple
  expression, left behind by a superseded log line (`# legacy log removed`). No behaviour change.

---

### Testing and CI

- **`CI-MARKER-1` closed (#420) — a green PR did not mean `tests/unit` passed.** `Runtime
  Contracts` runs `pytest tests -m runtime_only`, and nothing applied that marker
  automatically, so a new unit file defaulted to running in **no job**. 268 tests across 24
  files were in that state, including the regressions for FR-8/FR-9/FR-10, the defects that
  forced 2.0.1. Those files are now marked, **and `tests/unit/conftest.py` makes the marker the
  default** so it cannot recur. Collection went 1587 to 1855; coverage rose to 56.71%.
- **Coverage for four subsystems that had none (#406, #414)** — memory address space, native
  scorer, OS isolation layer and event bus, plus an end-to-end event-bus wire test against real
  Redis. Writing them found five of the defects fixed above.
- **`Runtime Docs Validation` now enforces `last_verified`** as a real date `>= 2026-05-17`
  (#400); it previously checked key *presence* only, which is how seven docs carried a
  `last_verified` earlier than the repository's first commit.
- **The deep-health wiring test no longer asserts a timing property (#422)** — it asserted the
  success-shaped keys *through* a 0.5s timeout, so it failed under load in a required check.
  Split into a wiring assertion, a direct check of the payload contract, and a forced-timeout
  test proving the endpoint degrades rather than hangs.
- All **ten** status checks are now required on `main` (was four), so a branch must also be up
  to date before merge.

### Dependencies (#404)

- `uvicorn` 0.52.1, `greenlet` 3.5.4, `pydantic-settings` 2.15.0, `alembic` 1.19.0,
  `pyo3` 0.29.2, `cc` 1.4.1, `vite` 8.2.1, `postcss` 8.5.26 — combined into one PR because
  `main` is `strict: true`.
- **`pgvector` 0.4.2 to 0.5.0 deliberately held** and filed as `MEM-EXPAND-DEAD-1`: it is green,
  but it would switch on `expand()`'s semantic-neighbour path, which returns `[]` on every call
  today, in the same code path that previously caused connection-pool exhaustion.

### Documentation

- A large verification pass over the runtime docset (#365-#403): 28 unresolvable citations
  repaired, nine unreferenced docs archived, and every doc read against source rather than
  trusted. The recurring finding was **plugin-layer routes documented as runtime-owned** —
  check `APP_ROUTERS` and the route ownership inventory, never file presence.

## 2.0.1 — 2026-08-05

**Patch. No breaking changes — this fixes the 2.0.0 upgrade path itself.**

Every defect below was found by the first team to deploy 2.0.0 to a live stack, and each one
is invisible from a source checkout *and* from CI. If you are on 2.0.0, you are exposed to
all three regardless of whether you have noticed yet.

### Correction to the 2.0.0 upgrade notes

The 2.0.0 table said *"Run migrations. Existing accounts are backfilled to verified, so nobody
is locked out."* **That was true only for a source checkout.** Alembic `0014` performs the
backfill, and the `alembic/` tree is not shipped in the wheel — so a wheel or Docker
deployment never ran it, and every pre-existing account came back **unverified**. Fixed below;
`--reconcile` now performs the backfill itself, on every install shape.

If you upgraded to 2.0.0 on a wheel install, check before enabling
`AINDY_REQUIRE_VERIFIED_LOGIN`:

```sql
SELECT is_verified, count(*) FROM users GROUP BY 1;
```

Upgrading to 2.0.1 does **not** retroactively repair an already-reconciled table — the
backfill runs when the column is first added. If those rows are already `false`, grandfather
them yourself before turning the flag on:

```sql
UPDATE users SET is_verified = true, verified_at = COALESCE(verified_at, created_at, now())
 WHERE is_verified = false AND created_at < '<the timestamp you upgraded to 2.0.0>';
```

### Fixed — the container could crash-loop on an empty environment variable (FR-10)

The idiomatic Compose default for an optional variable renders as an **empty string**, not an
absent one: `AINDY_REQUIRE_VERIFIED_LOGIN: "${AINDY_REQUIRE_VERIFIED_LOGIN:-}"`. To pydantic
that is an unparseable bool, and because `settings = Settings()` runs at **module import**,
the process died before serving — a restart loop, not a config warning. 27 restarts in the
reporting deployment.

`env_ignore_empty=True` now makes an empty value mean "unset", falling back to the field
default. **28 typed `bool` settings were exposed to this**, so it was a class of outage rather
than one unlucky variable; the regression test sweeps all of them.

### Fixed — `--reconcile` now grandfathers rows that predate a new column (FR-8)

`server_default` decides what a column holds for rows written *afterwards*; that is not always
right for rows that already exist. Columns may now declare
`info={"reconcile_backfill": "<sql expression>"}`, and `bootstrap-schema --reconcile` applies
it immediately after adding the column. `users.is_verified` and `users.verified_at` declare it,
so the grandfathering guarantee now holds on wheel installs, not just source checkouts.

### Fixed — an app `email` connector could silently swallow all auth mail (FR-9)

2.0.0 dispatched runtime transactional mail to the **`email`** connector type — the same type
apps register for user-authored automations — in a different, undocumented action shape.
Registering one silently opted an app into carrying password-reset and verification mail.
Combined with the deliberate no-SMTP-fallback rule, a shape mismatch meant `/auth/register`
returned `202` while **no account could complete signup**, with a single WARNING as the only
evidence.

Runtime mail now uses a reserved **`transactional_email`** type, so an app's `email` connector
cannot intercept it. The action shape is published in `docs/runtime/CONNECTOR_CONTRACT.md`
§5a, and a connector failure now logs at **ERROR** stating plainly that auth mail is not being
delivered.

**Action required only if you registered an `email` connector to handle runtime mail:**
re-register it as `transactional_email`. Otherwise runtime SMTP (`AINDY_SMTP_*`) carries it —
which is the FR-6 hybrid working as intended.

### Security

- **`cryptography` 49.0.0 → 50.0.0** — CVE-2026-69247 / GHSA-g6cj-pr64-35w5, a Bleichenbacher
  oracle in `pkcs7_decrypt_der/_pem/_smime`. **Not reachable in this codebase** (the only
  consumer is Ed25519 extension signing; there is no PKCS7 or S/MIME call, and JWT signing is
  HS256), but patched rather than exempted since a fix exists upstream.
- The `pip-audit` CI job could not name its own findings — a `bash -e` interaction killed the
  step before its reporting block ran, so every failure printed only `exit code 1`. Repaired.

### Platform UI

**This is the first release whose wheel ships the Tailwind 4 SPA.** vite 6→8, `@vitejs/plugin-react`
4→6 and tailwindcss 3→4 landed as one unit (plugin-react 6 peer-locks to vite ^8), plus
react-router 6→7 and `@aindy/ui-kit` 2.0.0. Verified in a browser, not only by a green build:
Tailwind 4 can compile cleanly while emitting the wrong rules.

### Other

- Node 20 → 24 across all CI workflows, plus a repo-root `.nvmrc`. **Node 20 reached
  end-of-life 2026-04-30**, so CI had been building on an unsupported runtime.
- New `Platform Lockfile` workflow resolves `platform/package-lock.json` on Linux — a
  Windows-generated lock cannot satisfy Linux `npm ci` for packages with native bindings.
- Dependency bumps: fastapi 0.141.1, uvicorn 0.52.0, pytest 9.1.1, certifi, tqdm.

### Schema

`SCHEMA_CONTRACT_VERSION` `2026-08-02` → **`2026-08-05`**. **No DDL changed** — the bump is
mechanical, because `orm_hash` is a content hash of every file under `AINDY/db/models/` and
FR-8 added a declaration to two columns. Alembic head remains `0014`. If you assert on the
contract version, update the expected value; if you diff schemas, expect no difference.

## 2.0.0 — 2026-08-02

**Major, and the breaking changes are concentrated in auth.** Every one is a deliberate
security tightening; none is a rename or a refactor. Read the upgrade notes below before
deploying.

### Upgrade notes — what breaks

| Change | Who it affects |
|---|---|
| `POST /auth/register` returns **202 with no token** | Any client that auto-logs-in from the register response. It must become a "check your email" flow. |
| Access tokens require a `purpose` claim | **All existing sessions end at upgrade.** Users log in again. |
| `POST /auth/register` enforces `MIN_PASSWORD_LENGTH` (8) | Registration flows and seeding/smoke scripts that used shorter passwords. Stored passwords are unaffected; login is unchanged. |
| `recommended_runtime_requirement` now reports `>=2.0,<3.0` | Consumers pinned `>=1.x,<2.0` will **not** pick this up. Move the pin deliberately. |
| Schema `2026-08-02`, Alembic `0014` | Run migrations. Existing accounts are backfilled to verified, so nobody is locked out. |
| `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` default `30000` → `60000` (from 1.11.0) | Only deployments that **pin** it — they keep the old value and therefore the bug. Raise above 45s. |

### Fixed — memory capture: four defects that made recall return the wrong things (FR-7)

Reported from a live 1,799-node corpus where recall returned four copies of one already-fixed
bug, two feedback counters, and two content-free labels — nothing a strategy could act on. All
four verified in source before fixing.

- **MEM-IMPACT-IGNORES-SIGNIFICANCE-1** — `get_relevant_memories` (the path feeding the
  Infinity loop) orders **purely by `impact_score DESC`**, and `impact_score` was purely
  graph-derived with no significance term, defaulting to `0.0` without a source event. A
  deliberate `decision` declared `significance: 1.0` scored 0.00 and was never recalled, while
  any captured failure started at 1.5. Impact is now **floored** by the declared policy
  significance — a floor rather than a sum, so a well-connected failure still outranks a
  declared decision but a bare one no longer does. No schema change: `significance` is not a
  column, so it is folded in at write time.
- **MEM-POLICY-KEY-1** — `validate_memory_policy` required `significance`/`base_score` while
  the engine read only `default_significance`, so a policy that *passed validation* had no
  effect and every declared significance fell back to 0.4. The engine now reads the validator's
  keys, with the old key kept as a fallback.
- **MEM-DEDUP-TRACEID-1** — dedup compared raw content, but messages embed the occurrence's
  trace id, so one recurring failure produced N rows and never deduplicated. Now compared on a
  normalised form (identifiers stripped, numbers deliberately kept) over a bounded window.
- **MEM-FORCE-UNGATED-1** — `force=True` skipped the significance gate entirely, so apps could
  not suppress the auto-captured system events at all. An **explicit** policy
  `min_significance` is now honoured for forced captures; a missing key still means force wins.


### Security — plaintext passwords were being written to the execution record ⚠️

`POST /auth/register` and `POST /auth/login` passed `body.model_dump()` as the pipeline's
`input_payload`, which is **persisted on the ExecutionUnit**. Both request bodies carry the
plaintext password, so every registration and every login wrote the user's raw password into
the execution record, where it was also exposed to anything reading trace data.

Both now pass only the non-secret fields. **Pre-existing, not introduced by this release** —
found while changing the register route for FR-6 Phase C. Operators who retain execution
records should consider them to contain plaintext credentials for any period before this
release, and purge or rotate accordingly.

### Changed — `POST /auth/register` returns 202 with no token ⚠️ breaking

FR-6 Phase C. Registration no longer authenticates the caller. It returns a neutral **202**
and sends a verification link; the access token is issued by the new
`POST /auth/verify-email` once the address is confirmed.

**This is what closes the account-enumeration oracle.** The previous 409-on-duplicate could
not be fixed while registration also returned a token, because a duplicate cannot be handed
one — some difference was unavoidable. Now a new address and an already-registered address
produce an **identical** 202: the new one gets a verification mail, the existing one gets a
*"someone tried to register with your address"* notice, and the caller cannot tell which was
sent. The duplicate path also performs equivalent work, so timing does not leak what the
response hides — including under a concurrent-registration race, which is caught and folded
into the same uniform response rather than surfacing as a 409.

**App-side change required:** any client that auto-logs-in from the register response must
switch to a "check your email" flow. There is no token to read anymore.

### Added — `POST /auth/verify-email`

Consumes an address-verification token and issues the access token. Idempotent — following
an already-used link succeeds rather than erroring. Verification tokens use their own
signing domain, distinct from both access and password-reset tokens, so none of the three is
redeemable as another.

### Added — `users.is_verified` / `users.verified_at` (schema `2026-08-02`, Alembic `0014`)

**Existing accounts are backfilled to verified.** They predate verification and were never
given a chance to confirm; leaving them unverified would retroactively mark the entire
current user base unverified and, with login gating enabled, lock all of them out.

### Added — `AINDY_REQUIRE_VERIFIED_LOGIN` (default off)

Refuses login for an unverified address. **Off by default deliberately** — the enumeration
fix does not depend on it, and enabling it is a lockout risk. The check runs *after* the
password so it cannot itself become an oracle. New settings: `AINDY_EMAIL_VERIFY_TTL_HOURS`
(48), `AINDY_EMAIL_VERIFY_URL_TEMPLATE`.

### Added — password recovery: `POST /auth/password/forgot` + `POST /auth/password/reset`

FR-6 items 2+3. A user who forgets their password now has a recovery path; previously the
only route back into an account was a direct `UPDATE users SET hashed_password` against
Postgres.

**Delivery is hybrid.** A registered `email` connector is used when one exists, otherwise
runtime-owned SMTP (`AINDY_SMTP_*`). Both go through the same `outbound.email` capability.

**`/forgot` always returns 200** for a well-formed request, whether or not the email is
registered — anything else is an account-enumeration oracle. The miss path performs
equivalent work so response *timing* does not leak the answer either.

**`/forgot` returns 503 when no email channel is configured.** That discloses a property of
the deployment, identical for every caller, and reveals nothing about any account — so the
uniform-response rule does not apply. A startup warning reports the same thing at boot.

**Rate limited 3/min per IP *and* per email.** Per-IP alone lets a distributed caller pound
one inbox; per-email alone lets one host sweep many addresses. Fails open, so a counter
outage cannot lock users out of recovery.

**Tokens are stateless and single-use by construction.** A reset token pins the user's
`token_version`; consuming it bumps that version, so a replay fails the comparison. No
table, no revocation list, no cleanup job — and any other version movement (logout, password
change, admin invalidation) burns outstanding tokens too.

**Reset tokens are signed with a domain-separated key** derived from the active signing key,
so a reset token cannot verify as an access token — it carries `sub` and `tv`, which would
otherwise make the emailed link a working session. The separation holds both directions and
survives signing-key rotation. New settings: `AINDY_PASSWORD_RESET_TTL_MINUTES` (30),
`AINDY_PASSWORD_RESET_URL_TEMPLATE`.

### Changed — access tokens now declare a `purpose`, and it is enforced ⚠️ invalidates existing sessions

`decode_access_token` previously asked exactly one question — does the signature verify
against a `KeyRing` secret — and examined nothing else. Any other token type signed with the
same key was therefore silently a **valid bearer access token**. This surfaced while scoping
FR-6: a password-reset token carrying `sub` and `tv` is everything the auth path needs, so an
emailed reset link would have *been* a session.

`create_access_token` now stamps `purpose: "access"`, and `decode_access_token` requires it.

**⚠️ Every token issued before this upgrade lacks the claim and will be rejected — all
existing sessions are invalidated and users must log in again.** Tokens expire after 24h
anyway; this brings that forward to the moment of upgrade. Nothing else about the token
format changes.

A wrong-purpose token returns the **same generic 401** as a bad signature, deliberately:
distinguishing them would confirm both that the token is genuine and which account it
belongs to.

This is defence in depth, not FR-6's primary control — non-access tokens will be signed with
a domain-separated derived key and cannot verify here at all. The claim makes "wrong token
type" an explicit failure rather than something every future token type must remember to
prevent on its own.

### Changed — `POST /auth/register` now enforces a minimum password length ⚠️

`register_user` rejects passwords under `MIN_PASSWORD_LENGTH` (8) with **400**. Previously the
floor guarded `POST /auth/password/change` only, which meant that of the paths able to set a
password, the one an *unauthenticated* caller reaches was the unguarded one. A floor applied to
one path is not a floor.

**This is a deliberate tightening on a published package, and it can break a caller.** What it
does **not** do:

- it does **not** invalidate any stored password — existing users are unaffected;
- it does **not** change `POST /auth/login` in any way.

The only affected caller is a registration flow that previously permitted passwords shorter
than 8 characters; those requests now return 400 instead of 201. If you drive registration
programmatically (seeding, fixtures, smoke tests), check the passwords those use.

The check runs before the duplicate-email lookup, so a request that is both short-password and
duplicate-email returns 400 rather than 409 — which also avoids confirming an email is
registered to a caller who supplied an invalid password.

`MIN_PASSWORD_LENGTH` is deliberately not configurable: a security floor an operator can switch
off is not a floor.

## 1.11.0 — 2026-08-01

Minor, not patch: `POST /auth/password/change` is a new public endpoint.

### Fixed — DB-NODUS-BUDGET-1: the DB idle cap now outlives the nodus execution ceiling

`DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` default **30000 → 60000**. Verified against real
PostgreSQL that the flow runner's session is held `idle in transaction` for the *entire*
duration of node execution, while a nodus run may legitimately occupy 45s (30s script +
15s boot allowance). At the old 30s default, a slow-but-in-budget nodus run had its
connection terminated mid-flight — surfacing as `server closed the connection
unexpectedly` → `PendingRollbackError`.

**Operators who pin `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` explicitly should raise it above
45s**, or above their own `AINDY_NODUS_MAX_EXECUTION_MS` + `AINDY_NODUS_BOOT_ALLOWANCE_MS`
if those are customised. A unit test now derives the ceiling from those constants and
fails if the cap stops clearing it.

The root-cause fix — memory recall no longer opening a transaction on the caller's
session — ships opt-in behind `AINDY_MEMORY_RECALL_OWN_SESSION` (default off) pending
soak.

### Fixed — MCP SDK capped at `mcp<2`

`mcp 2.0.0` removed the 1.x low-level `Server.list_tools()` decorator that `nodus-mcp`
0.1.2 is built on, so `pip install "aindy-runtime[mcp]"` resolved to an SDK that raises
`AttributeError` at server construction. The `[mcp]` extra now specifies
`mcp>=1.0.0,<2`. Lifted when a `nodus-mcp` release targets the 2.x API.

### Added — `POST /auth/password/change` (FR-6 item 1)

Self-service password rotation for an authenticated user. Until now the entire auth surface
was `register` / `login` / `logout` / `admin/invalidate-sessions` — a signed-in user could not
change their own password, and the only way to set one was a direct `UPDATE users SET
hashed_password` against Postgres.

```http
POST /auth/password/change
Authorization: Bearer <jwt>

{"current_password": "…", "new_password": "…"}
```

Bearer-JWT only (a platform API key has no password to rotate). Verifies the current password,
enforces `MIN_PASSWORD_LENGTH` (8) and new-≠-current, then writes the new hash and **bumps
`token_version`**, invalidating every session. A freshly-versioned token is returned in the
same envelope shape as `/auth/login`, so the caller stays signed in while other sessions are
cut and a client can reuse its existing token-store path.

Neither password reaches `input_payload` or the emitted `auth.password.changed` event — both
are trace-logged surfaces.

The forgot/reset half of FR-6 is not included: it needs a token-delivery channel (email), which
is FR-1 connector/egress work.

### Added — `aindy-runtime memory prune-cascade-debris`

One-time cleanup for deployments that ran a version before the RT-MEMTXN-LEAK-1 fix (v1.10.2).
Those deployments accumulated memory nodes that record nothing but the runtime's own embedding
jobs starting — each capture spawning another job and another capture. They are inert once the
cycle is cut, but they pad every recall candidate set and leave a standing embedding backlog for
the sweep to grind through on each boot. On one real stack: **1,912 of 1,970 nodes**.

Scoped by `extra.event_payload.task_name` — the same predicate the fixed capture path uses to
decide what *not* to create — so no user- or app-authored memory can match. Content strings are
never matched on.

```bash
aindy-runtime memory prune-cascade-debris            # report only (default)
aindy-runtime memory prune-cascade-debris --yes      # delete
aindy-runtime memory prune-cascade-debris --yes --batch-size 200
```

Deletes in **committed batches** so a large backlog never becomes one long-running transaction
holding a pooled connection — the failure mode this whole item exists to prevent. Child rows
(history / traces / edges / links) are removed by `ON DELETE CASCADE`, matching the
`sys.v1.memory.delete` contract. PostgreSQL and SQLite; any other dialect is refused rather than
guessed at.

## 1.10.2 — 2026-07-19

Patch. No schema-contract change (stays `2026-07-12.4`). Closes RT-MEMTXN-LEAK-1.

### Fixed — RT-MEMTXN-LEAK-1 (part 3): the capture → job → capture cascade (root cause)

1.10.0 and 1.10.1 each fixed a real transaction-hold bug, but sign-in was still ~42s. A
`py-spy` stack dump against the live container showed why: an **unbounded synchronous
recursion**, not a slow call holding a transaction.

```
submit_async_job                  ← opens its own SessionLocal()
 └ emits EXECUTION_STARTED
    └ capture_system_event_as_memory
       └ MemoryNodeDAO.save       ← commit + refresh = the held SELECT
          └ _enqueue_embedding    ← every new node needs an embedding
             └ submit_async_job   ← recurses
```

Every memory node spawns an async job whose lifecycle event becomes another memory node. Each
level holds the session it opened until the descent below returns, so depth is capped only by
the connection pool — 60 connections each holding **one** `SELECT … FROM memory_nodes WHERE
id = <uuid>` (the `save()` refresh), then a full `pool_timeout` wait for everything after.

**Fixed on three axes:**

- **Cycle cut at the origin** — the runtime's own memory-maintenance jobs
  (`memory.generate_embedding`, `memory.embedding_sweep`) are no longer captured as memory.
- **Depth bound** — new `AINDY/core/memory_capture_guard.py`; `submit_async_job` runs inside
  `async_submit_scope()` and captures are suppressed at submission depth ≥ 2. The outermost
  submission still captures (loop-closure signal preserved); `_execute_job` resets the depth at
  the thread boundary so legitimately chained jobs are unaffected.
- **Dedup repaired** — `_is_duplicate` used `WHERE user_id = :uid`, never true for `NULL`, so
  the global nodes this cascade produced were never deduplicated despite identical content.

Verified end-to-end on a live stack: **login 43.6s → 0.3s, 60 held connections → 0**, with
`/auth/register` and `/auth/login` still returning 201/200.

> **Rule:** a memory capture must never be able to enqueue work whose own lifecycle events are
> capturable. Any capture → job → capture edge is a cycle.

## 1.10.1 — 2026-07-19

Patch. No schema-contract change (stays `2026-07-12.4`).

### Fixed — RT-MEMTXN-LEAK-1 (part 2): the embedding-job connection fan-out

1.10.0 fixed the *recall* read path, which stopped leaked connections from lingering after a
request — but app-side verification showed sign-in was still ~45s. A single `POST /auth/login`
still opened **30+ concurrent** connections that each ran **exactly one** `SELECT memory_nodes
…` and then sat `idle in transaction` (`xact_age_s == idle_s`), exhausting the pool.

Traced to `embedding_jobs.process_embedding_job`: `queue_system_event(… EMBEDDING_STARTED,
required=True)` commits, which **expires** `memory_node`, so reading `memory_node.content`
triggers a **refresh `SELECT memory_nodes`** that opens a *fresh* transaction — and
`generate_embedding()` (the slow embedding API call) then ran with it open. One job is
enqueued **per captured memory**, each on its own session, so one request fanned out to dozens
of concurrently-held connections.

**Fix:** capture the node content into a local, `commit()` to return the connection to the
pool, then embed; the write re-acquires a connection for its fast execution. (The job owns its
session — not request-shared — and the `EMBEDDING_STARTED` event should be durable regardless,
so committing there is correct as well as necessary.)

> **Gotcha worth remembering:** after a commit, touching an ORM attribute silently re-opens a
> transaction (`expire_on_commit`) — never let a slow external call follow such an access.

## 1.10.0 — 2026-07-19

Additive and backward-compatible; no schema-contract change (stays `2026-07-12.4`). Closes
NODUS-WARMPOOL-1 (the durable Nodus cold-start fix) and fixes a HIGH-severity connection-pool
bug that made browser sign-in unusable.

### Fixed — RT-MEMTXN-LEAK-1: memory recall pinned a DB connection during the embedding call

A browser login took ~40s and exceeded the web client's 30s timeout, so a real user could not
sign in. `MemoryNodeDAO.recall()` ran a DB query (`_count_complete_embeddings`, which
autobegins a transaction on the request-shared session) and then made the **synchronous
embedding API call** while that transaction was open — the connection sat `idle in
transaction` for the API's duration. Under the concurrent request fan-out a browser login
triggers, ~60–85 connections piled up and exhausted the pool.

**Fix (reorder, not rollback):** `recall()` now generates the query embedding **before** any DB
query in the method, so the session holds no pooled connection across the ~seconds API call;
the fast DB queries re-acquire one only for their execution. Note we deliberately do **not**
rollback the request-shared session to free its connection — `session.dirty` cannot see
Core-level `db.execute(UPDATE …)` or an outer transaction, so that would discard in-flight
request state.

### Added — NODUS-WARMPOOL-1 Option B: warm worker pool (CLOSES the item)

Nodus executions no longer pay the plugin-stack cold-start (~12s on heavy app profiles) on
every run. All opt-in behind `AINDY_NODUS_WARM_POOL` (default off), with a fresh-subprocess
fallback on any fault — enabling it can never make execution worse than the default.

- **Phase 1 — warm worker.** `nodus_worker.py` refactored so the one-shot entry and the new
  `serve_forever()` share `run_one(payload)`, which rebuilds **every** per-request object, so a
  reused process carries no cross-run state. Length-prefixed JSON framing; new
  `nodus_worker_pool.py` with respawn-on-crash + max-requests recycle
  (`AINDY_NODUS_WARM_MAX_REQUESTS`, default 500) and a cross-platform read timeout.
- **Phase 2 — bounded pool.** Up to `AINDY_NODUS_WARM_POOL_SIZE` (default 4) workers, each
  serving one request at a time, so N executions run concurrently. Saturation waits
  `AINDY_NODUS_WARM_ACQUIRE_TIMEOUT_MS` (default 2000) then spills to a fresh subprocess
  (bounded backpressure — the warm path never blocks unboundedly).
- **Phase 3 — observability + lifecycle.** `pool.stats()` + Prometheus
  (`aindy_nodus_warm_pool_events_total{event}`, `aindy_nodus_warm_pool_workers{state}`);
  `pool.drain(timeout_s)` (stop checkouts → spill, wait for in-flight, kill);
  `pool.prewarm()` pays the plugin load ahead of traffic via a worker `{"__warmup__": true}`
  control request (tool-less scripts still skip the load), kicked in a background thread on
  first use when `AINDY_NODUS_WARM_PREWARM` is on.

### Changed — docs

- `UI_CONTRACT.md` gains an authoritative canonical `/platform/*` route table and the
  runtime-vs-app route ownership line (a UI kit carries runtime/platform routes; app-domain
  paths belong in an app-owned route map).

## 1.9.0 — 2026-07-18

Additive and backward-compatible; no schema-contract change (stays `2026-07-12.4`).
Nodus-native execution reach (app handoff FR-5) plus a Nodus cold-start correctness fix.

### Added — FR-5: native Nodus workflows can reach app logic

A `.nd` run via `run_nodus_workflow` can now invoke app callables through **both** VM
surfaces (previously neither worked from the public entry point):

- **(a) `call_tool` + capability token.** `run_nodus_workflow` gains paired
  `capability_token` + `run_id` params, threaded into flow state as
  `execution_token` / `agent_run_id` (the keys the agent path already uses) so the
  `call_tool` seam is reachable and `execute_tool` enforces the token per tool. The token
  binds to `run_id` + `user_id`, so both are required together. `initial_state`
  (previously dropped) is now merged into flow state.
- **(b) `sys()` + app syscalls.** The worker's `sys()` seam now loads the app plugin
  stack (`dispatch_worker_syscall` → `_ensure_tools_loaded()`, lazy/idempotent) before
  dispatch, so app-registered syscalls resolve instead of returning `"Unknown syscall"`.
  Enforcement is unchanged — each app syscall keeps its declared capability. (Corrected
  diagnosis: apps already register into `SYSCALL_REGISTRY`; the gap was a subprocess
  plugin-load ordering issue, not dispatcher resolution.)

Contract: `NODUS_WORKFLOW_CONTRACT.md` §§8.1–8.2.

### Changed — NODUS-WARMPOOL-1 Option A: worker cold-start off the script budget

The worker's inner `run_source(timeout_ms=)` is now the authoritative *script* clock; the
outer `subprocess.run(timeout=)` is widened to `AINDY_NODUS_MAX_EXECUTION_MS` +
**`AINDY_NODUS_BOOT_ALLOWANCE_MS`** (default 15000). A script overrun trips the inner nodus
timer first (clean "script exceeded {max}ms"); the outer kill becomes a hard safety net for
boot + a hung worker. Fixes app-profile runs that died on the ~12s plugin cold-start rather
than script work. Set `AINDY_NODUS_BOOT_ALLOWANCE_MS=0` to restore the old shared budget.
(Per-run re-boot latency is unchanged — a warm-pool fix, NODUS-WARMPOOL-1 B/C, remains
deferred.)

## 1.8.0 — 2026-07-17

Additive and backward-compatible; all new enforcement/behavior is opt-in and inert by
default. No schema-contract change (stays `2026-07-12.4`). Resolves the four app-side
runtime feature requests (`APP-FR-1..4`); two were already satisfied by prior work.

### Added — FR-1: connector registration hook + capability-enforced outbound I/O

Apps register outbound connectors through a runtime hook instead of a hardcoded `if/elif`
ladder, and every outbound call flows through the same authorization stack `execute_tool`
applies to agent tools.

- **`register_connector(connector_type, handler, *, capability=…)`** in
  `platform_layer/registry.py` (+ `get_connector` / `iter_connectors`,
  `INPROC_CAP_REGISTER_CONNECTOR`, `validate_connector_handler`), symmetric to
  `register_job`. Dispatch via `connector_service.dispatch_connector` returning a normalized
  `{success, result, error[, denied]}` envelope; `ConnectorContext.call(...)` is the
  pre-bound authorized-call helper handed to each connector.
- **`authorized_external_call(...)` + `OutboundCallDenied`** (`external_call_service.py`)
  grow the observability-only `perform_external_call` into a real chokepoint: capability
  recipient/domain allowlist → rate limit → socket-level egress guard → JIT credential
  vaulting (`resolve_secret`) → observability. Denials raise before any network I/O.
- **`outbound_http.outbound_request(...)`** — shared HTTP client with exponential-backoff
  retry and a per-service circuit breaker, routed through the authorized boundary.
- Vacuous until a `CapabilityPolicy` / secret scope / `AINDY_EGRESS_ENFORCEMENT` is
  configured — registering a connector changes routing only. Contract:
  `docs/runtime/CONNECTOR_CONTRACT.md`.

### Added — FR-3: `NEXT_ACTION_DISPATCHED` dispatch-outcome contract

Completes the Deliverable C acting loop with an app-readable record of what the runtime
*did* with a chosen `trigger_execution` (the record-first `NEXT_ACTION_CHOSEN` only said
what it *chose*).

- New un-prefixed ledger event `SystemEventTypes.NEXT_ACTION_DISPATCHED` +
  `emit_next_action_dispatched` + the `DISPATCH_DISPOSITIONS` contract in `core/next_action.py`.
  Every app-sourced `trigger_execution` candidate (once acting is enabled) emits exactly one
  outcome — decision stage (`dispatched` / `declined_no_objective` / `declined_chain_depth`
  / `declined_admission` / `declined_enqueue_error` / `declined_error`) and resolution stage
  from the follow-up job (`followup_executed` / `followup_pending_approval` /
  `followup_create_failed`) — parented to its `NEXT_ACTION_CHOSEN` via `parent_event_id`.
- No schema change (`SystemEvent` already carries `parent_event_id` + JSON `payload`).

### Changed — FR-4 / DOCS-BUCKET-A-1: docs reconciliation + `ERROR_HANDLING_POLICY.md` split

- `ERROR_HANDLING_POLICY.md` split into a **runtime-only** doc (normative repo-agnostic
  Policy Rules + `AINDY/...` implementation, including the runtime's real model-failure story
  via `llm_client` fallback chain + `CircuitBreaker`); app-domain observations pointer to
  `aindy-apps-monolith`. Closes DOCS-BUCKET-A-1 (FR-2 and FR-4 were already satisfied by
  prior work — `register_nodus_workflow` / the Bucket A migration).

### Security

- Pin `setuptools>=83.0.0` (build-system + `[security]` extra) to clear CVE-2026-59890 /
  PYSEC-2026-3447 (packaging-time `FileList` MANIFEST.in glob matching; not runtime-reachable).

### Dependencies

- `nodus-lang` 4.0.5 → 4.1.0 (risk-probed: full nodus unit surface + the version-fragile
  internal couplings verified; no code changes, no new transitive deps). `nltk` 3.9.4 →
  3.10.0. Dev/tooling: `ruff` 0.15.20 → 0.15.22, `typescript` 6.0.3 → 7.0.2, `postcss`
  8.5.16 → 8.5.19.

## 1.7.0 — 2026-07-13

### Added — RTR-4 gap (c): delegation-token-scoped private memory

Backward-compatible and additive; inert unless `AINDY_DELEGATION_PRIVATE_MEMORY` is enabled
(default off). A delegated child run's memory is private to that run.

- **Schema-contract bump `2026-07-12` → `2026-07-12.4`**: a nullable, indexed `owner_run_id`
  UUID on `memory_nodes` (no FK, so it stays additive/startup-reconcilable; `memory_nodes` is
  `create_all`-managed, not alembic-tracked). NULL = tenant-shared (every existing node), so no
  backfill and no behavior change when the flag is off.
- **One boundary, two sides.** A delegate run binds `set_owner_run_id(run.id)` around its
  execution (`mint_token` now carries `parent_run_id` so the token is identifiable). The write
  path stamps `owner_run_id` at the universal `MemoryNodeDAO.save` chokepoint — covering the
  deferred capture path *and* the syscall handler — and the read path (the centralized
  `apply_memory_owner_scope` helper) scopes reads to the same run. A run-private node is visible
  only to reads from that run; tenant-shared (NULL) nodes stay visible to everyone.
- **Fail-open-to-shared** and a `visibility=shared` escape hatch (a delegate publishing upward is
  not private). Never a cross-run or cross-tenant leak (tenant isolation via `user_id` unchanged).
- Preceded by a pure, behavior-preserving refactor that collapsed ~13 duplicated owner/visibility
  filter blocks into the single `apply_memory_owner_scope` helper.

### Changed — NODUS-SYS-SURFACE-1: fail loud on the `std:sys` syscall path

- Idiomatic `import "std:sys"` routes to nodus's in-process 4-syscall stub, **not** the AINDY
  dispatcher; only the bare `sys(...)` builtin reaches `dispatch_syscall`. The stub could not be
  aliased (the VM resolves builtins before host fns), so `nodus_worker._install_std_sys_guard()`
  monkeypatches `syscall_runtime.call_syscall` to fail loud with a "use the bare `sys(...)`
  builtin" error rather than silently no-op. Documented in NODUS_DEVELOPER_GUIDE §3.4.

### Added — ECOGAP-5a: durable timer (scheduled-job registration + downtime misfire policy)

- Per-job **misfire policy + catch-up** for scheduled jobs, so a job whose fire time was missed
  during downtime is handled deterministically instead of silently dropped. Fixed a latent
  registration gap where the real scheduler never registered nodus jobs. Schema-contract bump to
  `2026-07-12.2`, Alembic `0013`.

### Added — ECOGAP-3: provider breadth — embedding SPOF + LLM breadth

- **Embedding provider abstraction** (removes the OpenAI single-point-of-failure for embeddings)
  with a configurable embedding dimension and a `memory reembed` maintenance command; schema-contract
  bump to `2026-07-12.1`. **LLM provider registry** widened (Anthropic / Azure). Note: `Vector(N)`
  dimension is frozen at import; Claude models reject a `temperature` parameter.

### Tests — ECOGAP-6: execution-path coverage

- 26 tests closing the biggest execution-path coverage gap (`worker_loop.py`, worker processes,
  real-Postgres crash-continuation). Test-only; no runtime change.

### Added — DUR-4: FlowHistory canonicalization + fold (Durable Execution; completes ECOGAP-1 Phase 3)

Backward-compatible and additive; the resume repair is opt-in (default off).

- **Schema-contract bump `2026-07-11` → `2026-07-12`** (Alembic `0012`, runtime head `0011` →
  `0012`): a nullable monotonic `sequence_number` on `flow_history` (per `flow_run`) + index
  `ix_flow_history_run_seq`, making the node event-log deterministically ordered and fold-able.
  Populated by the runner writer (`max()+1`; a run's nodes execute sequentially and it continues
  across a resume). Blank-DB-guarded migration; `downgrade` drops both.
- **Folder** (`core/flow_history_fold.py`): `reconstruct_flow_run_state(db, run_id)` rebuilds
  `FlowRun.state` from the ordered rows — the last row's full `input_state` checkpoint with its
  `output_patch` applied only on SUCCESS (shallow merge, parity with the engine).
- **Opt-in resume repair** (`AINDY_DURABLE_FOLD_REPAIR`, default off): on continuation, rebuild a
  lost/torn snapshot from the fold before resuming (the last history row commits before the snapshot
  advance, so it is at least as fresh for the last completed node).
- Verified on real Postgres: column + index materialize, the folder reconstructs across out-of-order
  rows honoring the WAIT-no-apply rule, and Alembic `0012` adds/drops cleanly.
- **This completes ECOGAP-1 Phase 3 (Durable Execution): DUR-1 → DUR-4 all shipped.**

### Added — DUR-3: transparent crash continuation without per-flow declaration (ECOGAP-1 headline)

Backward-compatible; default off preserves current behavior. The ECOGAP-1 Phase 3 headline.

- New opt-in flag **`AINDY_DURABLE_CONTINUATION_ALL`** (default off): when on alongside
  `AINDY_DURABLE_CONTINUATION`, crash continuation covers **all** flows/agents — the per-flow /
  per-agent continuation-safe *declaration* is no longer required, because DUR-1/2/2b/2c make a
  re-run's runtime-mediated effects (memory / syscalls / tools) at-most-once.
- **Opt-out deny-list** for the residual risk: `mark_flow_continuation_unsafe` /
  `mark_agent_type_continuation_unsafe` exclude a flow/agent whose nodes have raw un-mediated side
  effects (a direct external call / a write outside the effect boundary) the runtime cannot dedup.
- Default off keeps the exact current behavior (declaration required). Staged: ship opt-in, flip
  the default after soak.

### Added — DUR-2c: gate immediate in-subprocess memory writes (Durable Execution)

Backward-compatible; only active for an (opt-in `AINDY_DURABLE_CONTINUATION`) continued run or
under `AINDY_MEMORY_IDEMPOTENCY`. Closes the last effect-reach hole before DUR-3.

- `remember()`, `record_outcome()`, `share()` are `AINDYMemoryBridge` methods that write
  **immediately, in-subprocess, via a direct DAO** — they bypass the deferred list DUR-1 gates,
  so a continuation re-run would double-write them (a duplicate memory node for `remember()`).
- `AINDYMemoryBridge` gains a `run_scope` + a `_gate()` helper that dedups `remember` and
  `record_outcome` through the shared effect ledger, keyed content-independently on
  `(run_scope, per-action ordinal)` with **cached-result replay** — a re-run's `remember()`
  returns the *original* node id instead of creating a duplicate. `share` is left ungated
  (setting an existing node to `shared` is naturally idempotent).
- The per-(run, segment) scope is threaded into the subprocess payload (`effect_scope`).
- Verified on real Postgres: a re-run's `remember()` replays the same id (1 node, not 2); without
  the signal it does not dedup.
- **All runtime-mediated effects on a continued run are now at-most-once** (deferred + immediate
  memory, syscalls, tools, parent + subprocess); only raw un-mediated node side effects remain.

### Added — DUR-2b: subprocess propagation + stable per-segment scope (Durable Execution)

Backward-compatible; only active for an (opt-in `AINDY_DURABLE_CONTINUATION`) continued run.
Makes the agent / nodus-subprocess continuation path fully at-most-once — the prerequisite for
a safe DUR-3.

- **Subprocess propagation.** The DUR-2 per-run at-most-once signal is a contextvar and cannot
  cross the nodus worker subprocess. The parent now writes `durable_effects` into the worker
  payload and the worker re-establishes `durable_effects_scope()` around `run_source`, so
  in-subprocess `sys()`/`call_tool()` effect gates dedup declaration-free.
- **Stable per-segment memory scope** (also a correctness fix): all segments of a nodus_vm agent
  run share the run's `execution_unit_id` (`correlation_id`) and run through the one constant
  `nodus.execute` node, so the DUR-1 memory-dedup scope would **collide across segments** once the
  gate is on. `_run_agent_segment_flow` now threads a per-segment `__effect_scope`
  (`agent_plan_seg<N>`) that the node handler appends, keeping each segment's scope distinct and
  re-run-stable. (Latent — agent memory gating is only reachable behind `AINDY_DURABLE_CONTINUATION`
  + a continuation-safe agent type — but fixed before DUR-3 can enable it.)
- Verified on real Postgres: two segments writing at the same ordinal under the shared run eu no
  longer collide, and a segment re-run dedups.

### Added — DUR-2: per-run at-most-once signal (Durable Execution / ECOGAP-1 Phase 3)

Backward-compatible; the signal is only set by the (opt-in `AINDY_DURABLE_CONTINUATION`)
crash-continuation drivers. Makes a continued run's effects dedup **without** any per-tool or
per-syscall `EXACTLY_ONCE` declaration.

- **`kernel/effect_ledger.durable_effects_scope()` / `durable_effects_active()`** — a contextvar
  marking the current execution context as at-most-once.
- **All three effect-boundary chokepoints honor it** (memory `_apply_deferred_memory_writes`,
  the syscall dispatcher gate, and tool `execute_tool`): inside the scope they dedup regardless of
  the per-effect `execution_guarantee` or the per-effect master flag.
- **Both continuation drivers set the scope** around the re-drive (`flow_continuation` wraps
  `runner.resume`; `agent_continuation` wraps the resume callback).
- Verified on real Postgres: with `AINDY_MEMORY_IDEMPOTENCY` off, a re-applied write inside the
  scope dedups (1 node) and outside it does not (2 nodes).
- **Honest reach:** a contextvar covers parent-side + in-process effects, not a nodus worker
  subprocess; and the agent-segment path needs a stable per-segment scope. Both are DUR-2b. The
  flow continuation path (dominant parent-side memory writes) is fully covered.

### Added — DUR-1: memory-effect idempotency boundary (Durable Execution / ECOGAP-1 Phase 3)

Backward-compatible; inert unless `AINDY_MEMORY_IDEMPOTENCY` is enabled (default off). The
keystone of the reframed ECOGAP-1 Phase 3 (`docs/runtime/DURABLE_EXECUTION_PROGRAM.md`).

- **Deferred memory writes are now dedup-guarded** through the shared MEB `EffectRecord` ledger
  at `nodus_runtime_adapter._apply_deferred_memory_writes` (both `memory.write` and `remember`
  kinds). A continuation re-run of the same node no longer persists a duplicate memory node.
- **Keyed on position identity — (run, node/segment, ordinal) — never content**, so a re-run
  dedups even when content carries a fresh uuid/timestamp, and two distinct writes never collapse.
  The per-node discriminator (`effect_scope`, the flow node name threaded via
  `execute_nodus_runtime`) is required for correctness: flow nodes share the run's
  `execution_unit_id`, so without it two siblings would collide on ordinal 0.
- A ledger failure degrades to at-least-once; a failed write leaves the slot reclaimable.
- Standalone win: dedups deferred memory writes on *any* retry, not only crash continuation.
- Verified on real Postgres (re-run dedups; sibling node at same ordinal doesn't collide;
  distinct ordinals don't collapse).

### Added — MEB-2b hardening: raw IP-literal egress + fail-closed secrets

Backward-compatible; both surfaces opt-in / inert by default.

- **Raw IP-literal connects are now covered.** `platform_layer/egress_guard.py` also wraps
  `socket.socket.connect`/`connect_ex`: under an active egress allowlist, a connect to an IP
  the caller did not obtain from an *allowed* `getaddrinfo` (tracked per-context) is denied —
  closing the raw-`socket.connect((ip, port))` bypass that skips DNS. Inert outside a scoped
  tool call.
- **`resolve_secret` fail-closed option.** `AINDY_SECRET_FAIL_CLOSED=true` denies any secret
  with no registered scope (default off preserves the dev-convenience fail-open).
- Remaining honest limit (intentionally not closed in-process): a resolve/connect on a thread
  that doesn't inherit the contextvar still escapes the scope — the sandbox `--network none`
  path is the real fix.

### Added — MEB-1: memory.write EXACTLY_ONCE + gate scope relaxation

Backward-compatible; inert unless `AINDY_SYSCALL_IDEMPOTENCY` is enabled (default off).

- **`sys.v1.memory.write` now declares `execution_guarantee="EXACTLY_ONCE"`** — the first
  syscall to opt into the idempotency gate. A retried flow step re-writing the same node in
  the same run scope replays the cached node instead of persisting a duplicate. The handler's
  return dict is JSON-safe, so the gate caches it as JSONB for replay.
- **Gate scope predicate widened** (`_is_uuid` → `_gate_scope_engaged`): the gate now fires
  for a prefixed run id whose tail is a UUID (`run_<uuid>`, `flow:<uuid>`), not only a bare
  UUID. Safe because the scope is only hashed into the `action_id`, never cast to a DB UUID
  column (the EU-PK cast that motivated the original guard was removed in MEB-1b).
- Verified on real Postgres: two identical `memory.write` dispatches persist ONE node and the
  retry replays it (`test_memory_write_exactly_once_e2e`, Integration job).

### Added — MEB-3b: EffectRecord tenant/session attribution (Mediated Effect Boundary)

Completes the MEB program. Backward-compatible and additive; attribution/audit only.

- **Schema-contract bump `2026-07-08` → `2026-07-11`** (Alembic `0011`, runtime head
  `0010` → `0011`): two nullable columns on `effect_records`, `tenant_id` and `session_id`.
  Blank-DB-guarded and idempotent; `downgrade` drops both.
- **Attribution writer** (`kernel/effect_ledger.py`): `resolve_effect_record` takes optional
  `tenant_id`/`session_id` (persisted on the row, **never folded into the `action_id` dedup
  hash**) with a per-field fallback to an ambient `set_effect_attribution` contextvar. A
  replayed action keeps the FIRST writer's attribution.
- **Populated at both effect-boundary chokepoints:** the syscall dispatcher gate and the agent
  tool path attribute the tenant (`== user_id`); the multi-tenant MCP `auth_hook` additionally
  stashes the resolved identity + session id ambiently.
- PG-verified: columns materialize via `create_all`, the writer round-trips explicit + contextvar
  attribution, replay preserves the first writer, and Alembic `0011` adds/drops cleanly.

### Added — MEB-0: tool-path effect boundary (agent tool idempotency)

Backward-compatible; inert by default. The keystone of the Mediated Effect Boundary program —
the first place agent tool calls (which bypass the syscall dispatcher entirely) get an
at-most-once guard, resolving the real half of IDEM-10.

- Agent `execute_tool` now resolves an `EffectRecord` through the shared kernel effect ledger,
  keyed on position identity `(run, tool, ordinal)`, so a retried/continued tool call replays
  the cached result instead of re-executing the effect. No schema change; no syscall declares it.

### Added — `sys.v1.memory.delete` — hard, syscall-only, tenant-scoped delete (MEM-DELETE-1)

- New syscall with a **dedicated `memory.delete` capability scope** (NOT granted by
  `memory.write`): hard, node-id, tenant-scoped, idempotent delete with DB `ON DELETE CASCADE`
  across history / traces / edges / links. Bumped `SYSCALL_REGISTRY_MIN_COUNT` and the stable-syscall
  set. Verified on real Postgres (isolation + cascade + idempotency). `client.memory.delete` is the
  SDK consumer. REST route / audit event / bulk / soft-delete deferred.

### Added — `aindy-runtime bootstrap-schema` CLI (APP-DEPLOY-1)

- Builds the runtime-owned tables and stamps the Alembic head from the packaged
  `RUNTIME_ALEMBIC_HEAD_REVISION` constant (the `alembic/` scripts dir is not shipped in the
  wheel). `memory_nodes` is runtime-owned and create_all-managed. Note: JSONB is Postgres-only and
  pgvector requires `CREATE EXTENSION`.

---

## 1.6.2 — 2026-07-09

Backward-compatible, opt-in. Closes the last open Infinity loop item — the runtime can
now *act* on a post-execution Next-Action decision (previously record-only) — plus a
verified-scope docs correction for ECOGAP-4. No schema change; no new syscall or
SystemEventType; no breaking changes to any stable surface. Satisfies
`aindy-runtime>=1.5.3,<2.0`.

### Added — Deliverable C: act on NextAction (INFINITY-RUNTIME-1)

Gap 4 shipped record-first (emit `NEXT_ACTION_CHOSEN`, take no action). This adds the
bounded acting half, **opt-in behind `AINDY_NEXT_ACTION_ACTING` (default off)**.

- **Bounded follow-up dispatch** (`core/next_action_dispatch.py`): when an **app-sourced**
  completion-hook decision is `trigger_execution` with an objective, the runtime dispatches
  ONE follow-up run via the async job `agent.next_action_followup` → `create_run` → (if
  auto-approved) `execute_run`. Wired into `_emit_agent_next_action` after the record emit (#213).
- **Rails reused:** the approval gate is structurally preserved — a `pending_approval`
  follow-up is left for a human, never force-executed; capability preflight applies; admission
  is bounded by `count_active_executions`.
- **One net-new rail:** a chain-depth cap (`parent_run_id` hops, `AINDY_NEXT_ACTION_MAX_CHAIN`,
  default 3) so a hook that always returns `trigger_execution` cannot self-perpetuate.
- **Never acts on a runtime-default decision** (`trigger_execution` is never a runtime default,
  plus an explicit non-default `source` guard). Agent runs only — async/flow completion paths
  have no NextAction seam.

### Docs

- **ECOGAP-4 verified-scope correction** (#212): recorded the source-audited built-but-inert
  state of the G4a egress/secret-broker scaffolding and G4b's out-of-tree MCP/A2A adapters,
  with reopen triggers — no code change.

### Compatibility

- No breaking changes to stable surfaces; `aindy-sdk` / `aindy-ui-kit` window unchanged.
- **App follow-up (opt-in):** to let the runtime close the Infinity loop autonomously, set
  `AINDY_NEXT_ACTION_ACTING=true` after soak and have the completion hook return a
  `trigger_execution` decision carrying `{"args": {"objective": "..."}}`.

---

## 1.6.1 — 2026-07-09

Backward-compatible. A fix that restores a silently-dead behavior, plus the first
increments of event-sourced durable execution (all opt-in, default off). No schema
change; no breaking changes to any stable surface. Satisfies `aindy-runtime>=1.5.3,<2.0`.

### Fixed

- **First-party agent-completion hooks were unusable — post-completion enforcement
  silently dead (INFINITY-COMPLETION-HOOK-BOUNDARY-1).** `run_agent_completion_hooks`
  sanitized the hook context (dropping `db`, redacting the `run` ORM) and subprocess-isolated
  the hook, so a first-party `handle_agent_run_completed` received `db=None` + a run with no
  id and no-op'd — killing the app-side Infinity loop's post-agent-completion trigger. **Not
  a 1.6.0 regression:** the sanitizer has been present since v1.0.0; INFINITY-RUNTIME-1 Gap 4
  (1.6.0) merely started *consuming* the hook's return, making it visible. Fix
  (boundary-preserving): the completion-hook context now carries `run_id` (a string that
  survives the sanitizer) and `agent_completion_hook` runs in-process — a first-party hook
  re-fetches the run by id with its own session; the runtime still never leaks a db/session/ORM
  handle across the boundary (#209).

### Added — ECOGAP-1: transparent crash continuation (Phase 1 + 2 + 2a)

All opt-in behind `AINDY_DURABLE_CONTINUATION` (default off), startup-only,
idempotent-gated. On restart a run stranded mid-execution is re-driven from its last
committed checkpoint instead of failed.

- **Phase 1 — flow-level** (`core/flow_continuation.py`): re-drive a stranded non-waiting
  `running`/`executing` FlowRun from `current_node`/`state` via `PersistentFlowRunner.resume()`;
  per-flow `mark_flow_continuation_safe`; crash-loop dead-letter (#206).
- **Phase 2 — nodus_vm agent** (`core/agent_continuation.py`): re-drive a crashed agent run
  from its last completed segment; per-agent-type `mark_agent_type_continuation_safe` (#207).
- **Phase 2a — per-step granularity** (`AINDY_DURABLE_STEP_GRANULARITY`, default off): each
  agent tool step becomes its own segment so continuation resumes at step granularity (#208).

### Compatibility

- No breaking changes to stable surfaces; `aindy-sdk` / `aindy-ui-kit` window unchanged.
- **App follow-up:** `handle_agent_run_completed` should re-fetch the run by `run_id` with its
  own `SessionLocal` (it no longer receives a usable `run`/`db` — by design).

---

## 1.6.0 — 2026-07-08

Large, **fully backward-compatible** feature release: agent-framework security
hardening, the Infinity learning-loop closure, and seven runtime-roadmap items.
No breaking changes to any stable surface — all new enum values, syscalls, events,
and config flags are additive, and every behavior change ships **opt-in / default
off**. Satisfies the apps requirement `aindy-runtime>=1.5.3,<2.0`.

### Added — Agent framework hardening (AGENT-HARDEN-1..10)

- **Operator cancel / emergency stop** — `sys.v1.agent.cancel` + terminal
  `AgentRunStatus.CANCELLED`, cooperative check at VM segment boundaries (#165).
- **Keyed capability-token integrity** — `token_hash` is now HMAC-SHA256 keyed on
  the KeyRing secret (was unkeyed SHA-256); active+previous verify (#166).
- **Compensating-undo engine** — `SyscallEntry.compensate` hook + append-only
  `effect_reversals` table + `sys.v1.agent.undo` (Alembic 0008) (#168).
- **Effect simulation / dry-run** — shadow `call_tool` seam + `sys.v1.agent.simulate`
  + virtual-tool rehearsal environment (#170, #171, #172).
- **LLM provider fallback chain** — `FallbackLLMClient` + provider-chain resolution (#167).
- **Verifier stage** — per-step post-condition check; fail → `VERIFY_FAILED` + rollback (#169).
- **Per-capability policy** — recipient/domain allowlists + rate limits (#173, #185).
- **Secrets broker** — JIT capability-scoped secret resolution (Env/File/Vault/Chain) (#187, #188).
- **Signed plugin bundles + SBOM** — Ed25519 detached signatures + trust registry
  + profile enforcement (#189, #190).
- **Contract tests** — respx recorded cassettes for the OpenAI/DeepSeek boundaries (#186).

### Added — Infinity learning-loop closure (INFINITY-RUNTIME-1, all five gaps)

- Per-run `SCORE_COMPUTED` execution record (#194); recall→planning link +
  `RECALL_USED` (#195); async jobs join the loop, opt-in `AINDY_ASYNC_JOB_LOOP_CLOSURE` (#196);
  Next-Action engine + `NEXT_ACTION_CHOSEN`, record-first (#197); tenant-scoped
  aggregate syscall `sys.v1.observability.support_metrics` (#198).

### Added — Runtime roadmap (RTR)

- **RTR-1** — `register_nodus_workflow` VM-backed agent path; dropped the dead
  `NodusTraceEvent` trace path (Alembic 0009) (#192).
- **RTR-3** — AgentRun↔FlowRun status canonicalization: `FlowRunStatus` gains
  `EXECUTING`/`SUCCESS`, `AgentRunStatus` gains `WAITING`; single-source
  classification helpers; stuck-run no-op recovery gap closed across six
  reconcilers; `ix_agent_runs_flow_run_id` (Alembic 0010) (#199).
- **RTR-4** — per-delegate capability narrowing (least-privilege, active by
  default) + opt-in delegation handshake `AINDY_DELEGATION_HANDSHAKE`
  (`AWAITING_DELEGATION`, `respond_to_delegation`) (#200).
- **RTR-6** — first-class `reasoning.signal` at the memory-capture layer (#201).
- **RTR-7** — execution-graph endpoint falls back to the runtime
  `event_trace_service` when app `rippletrace_*` symbols are absent (#202).
- **RTR-2** — production defaults to `EXECUTION_MODE=distributed` (fail-fast, via
  `config.resolve_execution_mode()`); thread-mode orphaned-job recovery at startup (#203).
- **RTR-5** — runtime-driven autonomous execute-window `run_execute_window`
  (bounded trigger→plan→execute), opt-in `AINDY_AUTONOMOUS_EXECUTE_WINDOW`;
  new `agent.autonomous_window` job + `AUTONOMY_WINDOW` event (#204).

### Added — SDK surface

- `sys.v1.execution.get` execution-introspection syscall (#164).
- **SDK-SYSCALL-GRANT-1** — `/platform/syscall` now grants the requested
  syscall's own capability (least-privilege, one cap/dispatch); `flow.run`
  grantable via `flow.execute` scope; new `event.emit` scope (#193).

### Changed

- `SYSCALL_REGISTRY_MIN_COUNT` raised to 22 (new agent/observability syscalls).
- New `SystemEventTypes` (additive): `RECALL_USED`, `SCORE_COMPUTED`,
  `NEXT_ACTION_CHOSEN`, `REASONING_SIGNAL`, `AUTONOMY_WINDOW`.
- New opt-in config flags, all **default off**: `AINDY_PLANNER_MEMORY_INJECTION`,
  `AINDY_ASYNC_JOB_LOOP_CLOSURE`, `AINDY_DELEGATION_HANDSHAKE`,
  `AINDY_AUTONOMOUS_EXECUTE_WINDOW` (+ `AINDY_AUTONOMOUS_MAX_ITERATIONS` /
  `_MAX_ACTIVE_RUNS` / `_COOLDOWN_SECONDS`).
- Schema contract version `2026-07-05` → `2026-07-08` (additive: `effect_reversals`
  table, `agent_runs.flow_run_id` index; `nodus_trace_events` dropped). Alembic
  chain extended to `0010`.
- Dependency bumps (redis, click, ruff, tzlocal, charset-normalizer, and platform
  dev deps).

### Compatibility

- No breaking changes to stable surfaces. `aindy-sdk` / `aindy-ui-kit`
  compatibility window unchanged; recommended requirement remains `>=1.0,<2.0`.

---

## 1.5.3 — 2026-07-05

### Fixed

- **Idempotency gate cast a run-scoped `execution_unit_id` to UUID and poisoned the transaction (#157).**
  The syscall dispatcher's idempotency gate looked up `ExecutionUnit.id` (a UUID column) using the
  raw `execution_unit_id`. On the `nodus_vm` resume path that id is run-scoped (`run_<uuid>`, also
  carried as the agent trace/correlation id), so PostgreSQL rejected the cast with
  `InvalidTextRepresentation`. The error was caught and logged as "lookup skipped", but the psycopg2
  transaction was already aborted — the subsequent `INSERT INTO flow_runs` then failed with
  `InFailedSqlTransaction`, the segment chain aborted, and the run never reached a terminal state
  (exposed once #152 unmasked it in 1.5.2; the poisoned session also cascaded into downstream 401s).
  The gate now (1) only issues the ExecutionUnit lookup when `execution_unit_id` parses as a bare
  UUID — a non-UUID id can never match the UUID primary key, so it short-circuits to `AT_LEAST_ONCE`
  without querying — and (2) runs the lookup inside a `SAVEPOINT` with an explicit rollback so any
  failure is contained and never leaves an aborted transaction on the pooled connection. This aligns
  the gate with the `_coerce_uuid`-guarded lookups already used throughout `execution_unit_service`.
  Regressions: `test_gate_skips_lookup_for_run_scoped_non_uuid_eu_id`,
  `test_gate_opens_lookup_for_valid_uuid_eu_id`.

---

## 1.5.2 — 2026-07-05

### Fixed

- **RTR-1 `nodus_vm` resume still tripped the ExecutionContract guard — real fix (#152, reopened).**
  The 1.5.1 change activated the async-execution context around the resume callback, which
  covers the flow runner's own `execution.started`. But when a resumed segment runs a nested
  `ExecutionPipeline` (e.g. an app tool invoked via the `call_tool` seam), the pipeline emitted
  its **own** `execution.started` *before* marking itself active (`is_pipeline_active()` was set
  one line too late), so with no ambient pipeline/async context the ExecutionContract guard
  raised. `_safe_emit_event` swallowed the error, but on PostgreSQL the failed `INSERT` had
  already aborted the transaction, cascading into `InFailedSqlTransaction` on every later query
  (masked on SQLite, which does not poison the session the same way). `ExecutionPipeline.run()`
  now calls `_safe_set_pipeline_active()` **before** emitting its own `execution.started`, making
  every pipeline self-consistent — its first event passes the guard independent of ambient state.
  Regression: `test_pipeline_active_set_before_own_execution_started`.

---

## 1.5.1 — 2026-07-04

### Fixed

- **RTR-1 `nodus_vm` mid-plan resume ran outside an execution context (#152).** The
  scheduler-driven resume callback (event notify, resume watchdog, or cross-restart
  rehydration) ran the resumed segment with no `ExecutionPipeline` wrapper, so
  `is_pipeline_active()` was `False` for the whole segment and the flow runner's
  `execution.started` (and other `execution.*` events) tripped the ExecutionContract
  guard under `ENFORCE_EXECUTION_CONTRACT=True`, stranding the run at `executing`. The
  resume callback now activates the async-execution context — the same signal the flow
  runner uses for background execution — around the resumed chain, mirroring the context
  the initial run inherits from the request pipeline. Surfaced by live-Postgres
  execute-to-completion validation in `aindy-apps-monolith`.

---

## 1.5.0 — 2026-07-04

### Added

- **RTR-1: opt-in VM-backed agent execution (`nodus_vm` backend).** Set
  `AINDY_AGENT_EXECUTION_BACKEND=nodus_vm` to run agent plans by compiling them into
  native Nodus `workflow {}` constructs executed through the VM — tools called via a
  capability-enforced `call_tool` seam — instead of the static `AGENT_FLOW` Python
  DAG. The path reproduces AGENT_FLOW's execution model (per-step **retry** with
  risk-based attempt budgets and a non-transient short-circuit; **halt-on-first-failure**)
  and adds **durable mid-plan WAIT/RESUME**: a plan may carry
  `{"wait_for": "<event.type>"}` steps that park the run (new `AgentRun.status="waiting"`)
  until the event is published. Waiting runs are **durable** — they survive a process
  restart (re-registered at startup by `rehydrate_waiting_agent_runs`) and capability-token
  TTL expiry (refreshed on resume). Release a waiting run via `resume_agent_run_runtime`
  → `publish_event`. **Opt-in and non-default** — `AGENT_FLOW` remains the default until
  broader soak completes. Validated end-to-end on PostgreSQL: backend parity, retry/halt,
  wait→resume, real scheduler-driven resume, and cross-restart rehydration.
  (Phases 2a–2e + parity/soak; `docs/runtime/NODUS_WORKFLOW_CONTRACT.md`, `TECH_DEBT.md` RTR-1.)
- **`AINDY_AGENT_WAIT_BEFORE_HIGH_RISK`** setting (default `false`) — on the `nodus_vm`
  backend, inserts a human-approval WAIT step (`agent.approval.granted`) before the first
  high-risk step so the run pauses for approval before a risky action.
- **`runtime.selftest`** diagnostic tool — verifies the agent tool-execution path end to
  end; capability-wired and executable but excluded from the planner catalog.
- `register_nodus_workflow` surface + `nodus_workflows` source table with boot rehydration
  and run-by-name (RTR-1 Phase 1).

### Changed

- Schema contract version `2026-07-04`: new nullable `agent_runs.wait_state` JSONB column
  (Alembic revision `0007`) carrying the durable WAIT descriptor.
- `execute_tool` now ensures the runtime agent defaults on load, so runtime-native tools
  (`memory.read` / `memory.write`) resolve in **every** process that executes a tool —
  including the `nodus_worker` subprocess (previously "Tool not found" there).
- **Dependency updates:** `openai` 2.44.0, `uvicorn` 0.49.0, `fastapi` 0.138.1,
  `sqlalchemy` 2.0.51, native memory scorer migrated to `pyo3` 0.29 (Bound API), platform
  `typescript` 6, `vite` 6.4.3 + `react-router-dom` 6.30.4 (security, minimal), plus
  numerous minor/patch bumps; added npm/cargo Dependabot coverage and a documented
  `nltk` pip-audit exemption.

### Docs

- Evidence-backed Runtime Roadmap (`RTR-*`) backlog and RTR-1 design/contract docs.
- `DATA_MODEL_MAP.md` relocated as a runtime-scoped doc; Bucket-A runtime docs authored/
  corrected (`INVARIANTS`, `MEMORY_BRIDGE_CONTRACT`).

---

## 1.4.3 — 2026-06-27

### Fixed

- **Agent planner is no longer broken on Linux/Docker deployments (PLANNER-SUBPROC-1).**
  First-party-app run-tool providers and planner-context providers were routed
  through an isolated subprocess (`registry._maybe_wrap_runtime_callback`). Those
  handlers read live in-process registration state (the agent `TOOL_REGISTRY`, the
  planner context) populated during app bootstrap, which a bare subprocess cannot
  reconstruct: its cwd is the read-only site-packages dir, so `load_plugins()`
  finds no app manifest and returns zero tools. The planner then failed with
  `Runtime-local planner backend requires at least one registered tool`, so
  `POST /apps/agent/run` returned 500 on Linux. It was masked in local dev because
  Windows resolves the manifest. These two registry-state-dependent surfaces now
  run **in-process** (`_STATEFUL_IN_PROCESS_CALLBACK_SURFACES` in
  `AINDY/platform_layer/registry.py`); self-contained surfaces (startup hooks,
  capability providers, trigger evaluators) keep subprocess isolation. The same
  class of bug also affected app-provided trigger evaluators (silent defer);
  those remain isolated and are tracked for a follow-up if they grow in-process
  state dependencies. Verified against a Linux container reproduction
  (`python:3.11-slim`, non-editable site-packages install) before/after.

## 1.4.2 — 2026-06-27

### Fixed

- **Memory writes no longer default to a rejected `node_type` (MEM-NODETYPE-1).**
  Every `memory.write` path defaulted `node_type="execution"`, but
  `VALID_NODE_TYPES` (`{decision, outcome, insight, relationship}`) omits it, so the
  `before_insert`/`before_update` validator raised `ValueError` on every default
  write — blocking the execute half of the `runtime_local` planner loop (which
  almost always plans a memory write first). In the script paths the rejected save
  was swallowed (`logger.warning` + `continue` / `return None`), so the script
  reported completion while the node silently vanished. All eight write-path
  defaults now use `"insight"` (the scorer's fallback, so a defaulted write ranks
  identically to an untyped one): the syscall handler (`syscall_registry.py`),
  `NodusMemoryBuiltins.write`, `DeferredMemoryBuiltins.write` + `_remember_factory`
  (`nodus_worker.py`), `_apply_deferred_memory_writes` (`nodus_runtime_adapter.py`),
  `AINDYMemoryBridge.remember` (`nodus/runtime/memory_bridge.py`), and the extension
  memory ABI (`extension_runtime_api.py`, `extension_worker.py`). A tree-wide sweep
  confirms no `"execution"` node_type default remains. Verified execute-to-completion
  against real PostgreSQL (`tests/integration/test_planner_loop_execute_to_completion.py`).
  `memory_persistence.py` is untouched, so the schema contract version is unchanged.

## 1.4.1 — 2026-06-24

### Fixed

- **Background leadership is now enforced (LEASE-1).** The `distributed-api`,
  `distributed-worker`, and `hostile-third-party` profiles advertise
  `background_leadership_mode: "lease-elected"` but the runtime self-elected
  locally — every replica ran its own scheduler. Leadership is now decided by an
  atomic lease on `background_task_leases` (`AINDY/platform_layer/leadership.py`)
  with a `BackgroundLeadershipElector` that renews on a heartbeat, fails over to a
  standby within one TTL of leader death, and stands a demoted leader down to
  prevent split-brain. `single-instance` keeps its local-boolean `in-process`
  guard. New tunables: `AINDY_BACKGROUND_LEASE_TTL_SECONDS` (default 60),
  `AINDY_BACKGROUND_LEASE_HEARTBEAT_SECONDS` (default 20). The
  `task_is_background_leader` / `task_background_lease_name` observability symbols
  are now registered and reflect real lease state.

## 1.4.0 — 2026-06-20

### Added

- **Automation logs routes** (`AINDY/routes/automation_router.py`): `GET /automation/logs` and
  `POST /automation/logs` served; closes OPER-DEFER-002. Automation tab live in Platform UI.

- **Flow strategies route** (`AINDY/routes/platform_router.py`): `GET /platform/flows/strategies`
  served; closes OPER-DEFER-001. Strategies tab live in Platform UI.

- **Agent registration API and startup seed** (`AINDY/routes/agent_router.py`, `AINDY/startup.py`):
  Agent Registry screen now populates on first boot; empty-state placeholder shown when no agents
  registered.

- **System Observability view** (Platform UI): Execution console with connected apps, domain health,
  execution pipeline stats, and registry counts.

- **Inline approve/reject in agent plan detail panel** (Platform UI): Approve or reject a pending
  agent run directly from the detail panel without navigating to a separate screen.

### Fixed

- **starlette 1.0.1 → 1.3.1**: Resolves 4 CVEs (CVE-2026-48817, CVE-2026-48818, CVE-2026-54282,
  CVE-2026-54283).

- **pydantic-settings 2.11.0 → 2.14.2**: Resolves GHSA-4xgf-cpjx-pc3j (symlink traversal in
  `NestedSecretsSettingsSource`; not exercised by this codebase).

- **nodus-lang 4.0.3 → 4.0.5**: Picks up `identity.session_id()` child-VM propagation fix (4.0.4)
  and retry trace bleed suppression (4.0.4). No code changes required.

- **FastAPI 0.137 `_IncludedRouter` compatibility** (`AINDY/core/route_execution_guard.py`):
  `include_router()` now wraps sub-routers lazily; guard updated to walk `_iter_api_routes()` and
  handle `_IncludedRouter` in route enumeration and tests.

- **`register_syscall()` docstring** (`AINDY/kernel/syscall_registry.py`): Corrected misleading
  "overwrites" claim — same-handler re-registration is a no-op; different-handler raises
  `ValueError` by design.

- **Agent console 429 and detail panel shape mismatch** (Platform UI): Retry backoff on approve;
  response shape aligned with list endpoint.

- **`require_execution_context` bypass for admin routes**: Admin router exempted from execution
  contract to fix Users tab 500.

### Dependencies bumped

- fastapi 0.135.0 → 0.137.1
- idna 3.15 → 3.18
- click 8.3.0 → 8.4.1
- tqdm 4.67.1 → 4.68.2
- redis 5.0.4 → 8.0.0
- joblib 1.5.2 → 1.5.3
- pendulum 3.1.0 → 3.2.0

---

## 1.3.1 — 2026-06-13

### Fixed

- **`sys.v1.job.submit` syscall crash** (`AINDY/kernel/syscall_registry.py`):
  `_handle_job_submit` was passing `db=external_db` to `submit_async_job()`, which does
  not accept a `db` keyword argument. The stale variable and bad kwarg are removed.
  Surfaced during the first live-stack run via the OpenClaw schedule branch.

- **OpenClaw runner — live-stack bootstrap incompatibilities** (`examples/openclaw/`):
  Six issues found and fixed during the first end-to-end pass against a live Postgres stack:
  - `ingest_memory_node` was called but never existed; replaced with `MemoryNodeDAO.save_at_path()`.
  - `node_type` values `soul`/`identity`/`context` are not valid memory node types; all mapped to `insight`.
  - `'demo-user'` string fails the `memory_nodes.user_id` FK constraint; `_ensure_live_user()` now creates or reuses a real DB user (`openclaw-demo@aindy.local`).
  - `user_id=""` in `tool_recall_memory` returned zero results; `_current_user_id` module variable is now set at bootstrap time.
  - `dispatch_syscall` inferred capability `memory.search` but `sys.v1.memory.search` requires `memory.read`; explicit `capability="memory.read"` added to the recall call.
  - `node_type='conversation'` in `openclaw_agent.nd` is invalid; changed to `insight`.

---

## 1.3.0 — 2026-06-12

### Added

- **`aindy-runtime init`** — new CLI scaffold command. Writes four files to the target
  directory from a single command, closing the operator onboarding gap found during the
  1.2.0 live walkthrough:
  - `AINDY/.env` — generated 64-char hex `SECRET_KEY` + correct `DATABASE_URL` pointing
    at the compose `postgres` service name (not `localhost`)
  - `Dockerfile` — `FROM python:3.11-slim`, `pip install aindy-runtime==<version>` from
    PyPI, `CMD ["aindy-runtime", "serve"]`
  - `docker-compose.yml` — postgres (pgvector:pg16) + api (build: .) + redis
    (`--profile full`), with correct `AINDY/.env` volume mount and `env_file` wiring
  - `docker/init-pgvector.sql` — `CREATE EXTENSION IF NOT EXISTS vector`
  - Existing files are skipped unless `--force` is given (idempotent re-runs).
  - `--dir PATH` targets a different directory (default: CWD).

### Fixed

- **Platform UI — Agent Registry crash on empty state** (`platform/src/components/platform/AgentRegistry.jsx`):
  `useState` / `useCallback` / `useEffect` were called after a conditional
  `if (!isAdmin) return` early return, violating React's Rules of Hooks. When auth state
  loads asynchronously, `isAdmin` briefly differs between renders, causing React to throw
  _"Rendered more hooks than during the previous render."_ The empty-state UI for zero
  agents was already in the component — it never rendered because the crash happened first.
  Fix: all hooks moved above the `isAdmin` guard; `loadAgents()` gated inside `useEffect`
  with `if (isAdmin)`.

- **Platform UI — crashed screen poisoned subsequent navigation** (`platform/src/PlatformApp.tsx`):
  The outer `<ErrorBoundary>` wrapping all routes stayed in `hasError=true` after catching
  a crash, blocking every in-app navigation until a full page reload. Fix: routes extracted
  into `<PlatformRoutes>` which keys the boundary on `location.pathname` — resets
  automatically on every navigation.

- **OpenClaw example — `or` fallback syntax** (`examples/openclaw/openclaw_agent.nd`):
  `x or "fallback"` is not valid Nodus 4.0.3 — `or` is treated as a variable name at
  runtime. Fixed two occurrences with explicit nil-check pattern.

- **OpenClaw runner — `sys.v1.job.submit` missing `task_name` field**
  (`examples/openclaw/openclaw_runner.py`): Added `"task_name": "openclaw.reminder"` to
  the schedule reminder dispatch payload.

- **OpenClaw runner — state readback used wrong key** (`examples/openclaw/openclaw_runner.py`):
  CLI printout read from `result["extras"]["globals"]` but `set_state` writes to the
  runner-owned `agent_state` dict. Fixed to `result.get("agent_state")`.

### Docs

- **`docs/runtime/USER_WALKTHROUGH_LOG.md`** (new): live operator onboarding issue log
  (Issues 1–9) from the first real pip-install walkthrough of 1.2.0 against a live stack.
- **`docs/runtime/QUICKSTART.md`**, **`KERNEL_CAPABILITY_AUDIT.md`**,
  **`INFINITY_LOOP_AUDIT.md`** added to the doc index.

---

## 1.2.0 — 2026-06-11

### Added — REPLAY-1: Clock abstraction for deterministic replay

- **`AINDY/kernel/clock.py`** (new): ContextVar-backed `utcnow()` + `frozen_at(t)` context
  manager. Production code calls `utcnow()` instead of `datetime.now(timezone.utc)`; tests
  freeze time with `frozen_at(fixed_dt)`. Override is async-safe and thread-safe — each
  coroutine or thread has its own ContextVar slot.
- 12 call sites updated across the execution-critical path: `SyscallDispatcher` EffectRecord
  gate (3), `CircuitBreaker._now()`, `SchedulerEngine` time-wait tick, `ExecutionUnitService._now()`,
  `SystemEventService` event timestamp + 5 cutoff queries, `flow_engine` runner completion,
  runner failure, and `_default_wait_deadline`.
- **`tests/unit/test_clock.py`** (new): 12 tests covering core clock behaviour, nested freeze,
  thread isolation, and end-to-end verification of `CircuitBreaker`, `ExecutionUnitService`,
  `_default_wait_deadline`, `_complete_effect_record`, and `emit_system_event`.

### Changed — NODUS-UPGRADE-1: nodus-lang 3.0.2 → 4.0.3

- **`pyproject.toml`** + **`AINDY/requirements.txt`**: Pin updated to `nodus-lang==4.0.3`.
- **`AINDY/runtime/nodus_worker.py`**: `_runtime_emitted_events()` updated from deprecated
  `runtime.last_vm` (removed in v4) to `runtime._get_active_vm()`.
- **`docs/runtime/NODUS_DEVELOPER_GUIDE.md`** §8: Version table + v3→v4 breaking-change notes
  added. Key changes: `last_vm` → `_get_active_vm()`; `allowed_paths` default now `[os.getcwd()]`
  (was `None`).

### Changed — CI smoke: install from PyPI wheel

- **`.github/workflows/smoke-postgres.yml`**: Install step changed from `pip install -e .[test]`
  to `pip install aindy-runtime==$AINDY_VERSION` — validates the published PyPI wheel on every
  push rather than the local editable install. Cache key simplified to hash `pyproject.toml` only.

### Added — OpenClaw Infinite Weave spike

- **`examples/openclaw/`** (new): Demonstrates the aindy-runtime complement to OpenClaw's
  `pi-agent-core` loop. `openclaw_agent.nd` — Nodus 4.0.3 agent script (persona recall, skill
  routing, pgvector turn persistence). `openclaw_runner.py` — Python bootstrap, 4 host functions,
  NodusRuntime wiring. `README.md` — 8-dimension delta table and standalone + live-stack run
  instructions.

---

## 1.1.0 — 2026-06-08

### Added — CI-SMOKE-1: PostgreSQL boot smoke workflow + Quickstart (2026-06-08)

- **`.github/workflows/smoke-postgres.yml`** (new): Boots the runtime against
  `pgvector/pgvector:pg16` + Redis 7, waits up to 30 s for `/health/deep` to reach
  `{"status":"healthy"}`, asserts the `/api/version` boot surface, and records TTFA
  (time-to-first-answer) for `/health` and `/health/deep` as a `smoke-ttfa-py3.11` JSON
  artifact retained 90 days.
- **`docs/runtime/QUICKSTART.md`** (new): Five-minute boot guide covering Docker Compose
  quickstart and bare-metal install with the correct `/health/deep` response shape documented.
- Install step uses `pip install -e ".[test]"` (editable); a comment marks the line for
  switching to `pip install aindy-runtime` when PYPI-PUBLISH-1 closes.

### Fixed — Boot smoke CI: health + registry assertion bugs (2026-06-08)

- **`AINDY/routes/health_router.py`**: Scheduler check returns `{"status": "disabled"}` when
  `AINDY_ENABLE_BACKGROUND_TASKS=false`. `"disabled"` was not in the non-degrading status set
  (`{"ok", "not_configured", "not_applicable"}`), so `/health/deep` always reported `"degraded"`
  in the smoke environment and the workflow timed out after 30 s. Added `"disabled"` to the set.
- **`.github/workflows/smoke-postgres.yml`**: The `syscall_registry` assertion read
  `data.get("syscall_registry")` from the top-level response; the key lives at
  `data["checks"]["syscall_registry"]`. Fixed to `(data.get("checks") or {}).get("syscall_registry")`.
  `docs/runtime/QUICKSTART.md` example JSON updated to match the real response shape.

### Fixed — MEMORY-1 + EVENT-1: atomic ingest + explicit emission guard (2026-06-08)

- **`AINDY/memory/memory_ingest_service.py`** (MEMORY-1): `persist_memory_ingest_payload` now
  uses a single transaction (`commit=False` on all DAO calls, single `db.commit()` at end).
  Any failure rolls back the entire write atomically — eliminates the partial-write orphan window.
- **`AINDY/core/execution_pipeline/pipeline.py`** (EVENT-1): `_safe_emit_event` now sets an
  `_emission_failed` flag on `ctx.metadata` on first failure and short-circuits on re-entrant
  calls. The loop-prevention guard is now explicit rather than relying on exception swallowing.

### Added — C3 Phase 5: macOS sandbox escape CI certification workflow (2026-06-06)

- **`.github/workflows/macos-sandbox.yml`** (new): `workflow_dispatch` job targets `macos-14`
  (Apple Silicon). Installs Colima as the Linux-backend Docker provider, runs
  `pytest -m sandbox_escape -v` against the full 17-test escape suite, and uploads
  `sandbox_escape_results.json` as a workflow artifact.

### Fixed — Auth hardening: AUTH-V1, V4, V6 (2026-06-06)

- **AUTH-V1** (`AINDY/routes/__init__.py`): Removed duplicate `health_router` re-export that
  shadowed the module with an `APIRouter` object.
- **AUTH-V4** (`@aindy/ui-kit` `src/api/auth.js`): `logoutUser()` added. The platform SPA can
  now call logout without a manual `fetch()`.
- **AUTH-V6**: `require_admin_principal` now correctly gates `/platform/admin/*` routes to
  tokens carrying the `admin` scope; API keys without it receive 403.

### Fixed — EVENTBUS-REDIS-URL-CONSOLIDATION-1: AINDY_REDIS_URL alias removed (2026-06-06)

- **`AINDY/kernel/event_bus.py`**, **`AINDY/config.py`**, **`AINDY/.env.example`**:
  `AINDY_REDIS_URL` alias fully removed — all components now read `REDIS_URL` exclusively.
  `AINDY_SKIP_MONGO_PING` alias also removed; reads `SKIP_MONGO_PING` directly.

### Fixed — EXEC-EU-1 + OBS-1: EU lifecycle safety + observability (2026-06-06)

- **`AINDY/core/execution_pipeline/pipeline.py`** (EXEC-EU-1): `_safe_finalize_eu` moved into
  a `finally` block; `ctx.metadata["eu_finalized"]` guard prevents double-finalization; `finally`
  call gated by `eu_status != "waiting"` so suspending flows are not erroneously finalized.
- **`AINDY/core/execution_pipeline/resources.py`** (OBS-1): `_safe_require_eu`,
  `_safe_finalize_eu`, and `_safe_emit_event` failures promoted from `DEBUG` to `logger.warning`.

### Fixed — OPER-EXEC-001/002: distributed mode default + ContextVar propagation (2026-06-06)

- **OPER-EXEC-001**: Worker compose environment and `AINDY/.env.example` updated to enforce
  distributed execution mode in production; thread-mode carries an explicit dev-only warning.
- **OPER-EXEC-002**: `copy_context()` added at both `ThreadPoolExecutor.submit` call sites in
  the execution pipeline so ContextVar values (trace_id, pipeline_active, etc.) propagate
  correctly into worker threads. 3 regression tests added.

### Fixed — ROUTE-EXTRACT-001: agent/memory routers extracted to aindy-apps-monolith (2026-06-06)

- **`AINDY/routes/__init__.py`**: `agent_router`, `memory_metrics_router`, and
  `memory_trace_router` removed from runtime router registration. Now registered by
  `aindy-apps-monolith` via `register_router()` at bootstrap time (PR #37).

### Fixed — Agent approve orphaned-run watchdog (2026-06-06)

- **`AINDY/platform_layer/scheduler_service.py`**: `_recover_orphaned_approved_runs` scheduler
  job added — 5-minute sweep that finds `AgentRun` rows stuck in `approved` state without an
  `executing_since` timestamp for more than 2 minutes and re-dispatches them.
- **`tests/unit/test_agent_approve_watchdog.py`** (new): 4 tests — no-op, orphan re-dispatch,
  TTL threshold, exception isolation.

### Fixed — Routes audit: ROUTES-CONSUMER-SPLIT-1, API-MODULE-DRIFT-1, AGENT-API-001 (2026-06-06)

- **ROUTES-CONSUMER-SPLIT-1**: `@aindy/ui-kit` ROUTES table restored to universal shape.
  Runtime SPA gates features via `FEATURE_FLAGS` at NavLink/route level.
  `@aindy/ui-kit@1.0.5` verified safe to publish.
- **API-MODULE-DRIFT-1**: `rippletrace.js` (×16), `analytics.js` (×19), `platform.js` (×4)
  constants restored. `/trace` route gated on `FEATURE_FLAGS.RIPPLETRACE_VIEWER`.
- **AGENT-API-001**: `getAgents`, `recallFromAgent`, `getFederatedMemory` corrected to use
  `ROUTES.MEMORY.*` constants; recover/replay endpoints added.

### Fixed — AUTH-V2/V3: API key scope enforcement wired (2026-06-07)

- **`AINDY/services/auth_service.py`**: `enforce_api_key_scope(key, required_scope)` added.
  Wired as a FastAPI dependency to flows routes, memory routes, and `dispatch_syscall`.
  API keys without the required scope now return 403.

### Fixed — AUTH-V3/V5: dead auth path + SECRET_KEY export removed (2026-06-07)

- **AUTH-V3** (`AINDY/routes/api_key_auth.py`): `get_authenticated_principal`, `require_scope`,
  and `AuthPrincipal` removed — dead parallel auth path that duplicated the real auth with
  weaker guarantees.
- **AUTH-V5** (`AINDY/services/auth_service.py`): `SECRET_KEY` module-level export removed.
  `global` assignments in `rotate_signing_key` and `_reload_key_on_sighup` also removed.

### Fixed — TIER3-8 + TIER3-9: memory drop logging + flush scope (2026-06-07)

- **TIER3-8** (`AINDY/core/distributed_queue.py`): `enqueue()` drop paths now emit
  `logger.warning` — dropped items are visible in production logs.
- **TIER3-9** (`AINDY/core/execution_pipeline/pipeline.py`): `db.flush()` replaced with
  `db.flush([event])` to scope the flush to the new event row only, preventing in-flight ORM
  changes from the handler from being committed as a side effect.

### Fixed — SYSMAX-2: autonomous scheduler queue back-pressure (2026-06-07)

- **`AINDY/agents/autonomous_controller.py`**: `submit_autonomous_async_job` raises
  `QueueSaturatedError` when the scheduler is full; `evaluate_trigger()` maps this to a 60 s
  defer rather than swallowing it silently.

### Fixed — AGENT-RESLIMIT-001: wall_time_ms rename + migration 0005 (2026-06-07)

- **`AINDY/db/models/agent_run.py`** + **`alembic/versions/0005_wall_time_ms.py`**:
  `cpu_time_ms` field renamed to `wall_time_ms`. `MAX_CPU_TIME_MS` → `MAX_WALL_TIME_MS`.
  Name now accurately reflects that the limit measures monotonic wall-clock elapsed time.
  Migration 0005 handles the column rename idempotently.

### Added — Platform: admin user management, starter templates, dashboard UX (2026-06-07)

- Admin user management panel in the platform SPA — list users, promote/demote admin,
  search by email.
- Starter flow and agent templates available on first login for new operators.
- Dashboard UX improvements across `AgentConsole`, `FlowEngineConsole`, and
  `ObservabilityDashboard`.
- `docs/runtime/DEPLOYMENT_TARGETS.md` and `docs/runtime/MONETIZATION_AUDIT.md` added.

### Added — Kernel hardening tests + REPLAY-1 debt filing (2026-06-07)

- **`tests/unit/test_kernel_hardening.py`** (new, 3 tests): `SyscallDispatcher` contract
  edge cases filed as REPLAY-1 prerequisites.
- **`TECH_DEBT.md`**: REPLAY-1 filed — `Clock` injection required at ~12 `datetime.now()`
  call sites before deterministic replay is possible; deferred post-PyPI + OpenClaw spike.

### Added — C3 Phase 2: macOS Docker Desktop Linux backend detection + policy (2026-06-06)

- **`AINDY/platform_layer/sandbox_runner.py`**: Extended `_detect_wsl2()` to handle macOS.
  New `docker_macos_backend` field: detects Docker Desktop running a Linux container backend
  via Apple Virtualization Framework (macOS 12+) or HyperKit (older). `wsl2_kernel_available`
  is now True on macOS + Docker Desktop Linux containers mode.

- **Static platform matrix** (`sandbox_platform_capability_matrix()`): Updated `PLATFORM_WINDOWS`
  and `PLATFORM_MACOS` static entries to `linux_container_backend_available=True`. Docker Desktop
  on both platforms supports Linux containers. Both now correctly show `no_new_privileges`,
  `drop_all_capabilities`, and `pids_limit` as available hardening controls.

- **`docs/runtime/MACOS_CONTAINER_POLICY.md`** (new): Policy document recording what IS and is
  NOT claimed for macOS + Docker Desktop Linux containers. Assurance tier: `container-grade-sandbox`
  (same as Windows + Docker Desktop). Seccomp/AppArmor/SELinux not claimed — not tested. Strong
  sandbox VM still requires native Linux. Escape suite certification pending.

- **2 new unit tests** in `test_sandbox_runner.py` (64 total): `test_macos_with_linux_container_backend_is_docker_macos`,
  `test_macos_without_linux_container_backend_not_detected`. `test_result_has_required_keys` updated
  to check `docker_macos_backend` field.

### Added — C3 Phase 1: WSL2/Linux backend detection for OCI sandbox runner (2026-06-06)

- **`AINDY/platform_layer/sandbox_runner.py`**: Added `_detect_wsl2(container_runtime)`.
  Detects two cases: Python process running inside WSL2 (Linux host + `/proc/version` contains
  "microsoft"); or Windows host with Docker Desktop in Linux containers mode (via `docker info`
  `OSType=linux`). Returns `{is_inside_wsl2, docker_wsl2_backend, wsl2_kernel_available, ...}`.

- **`_supports_linux_container_kernel_controls()`** now accepts a `linux_container_backend`
  keyword argument. When `True`, the function returns `True` even on non-native-Linux hosts,
  enabling `no_new_privileges`, `drop_all_capabilities`, and `pids_limit` to be treated as
  available controls for OCI containers running on Docker Desktop Linux backends.

- **`inspect_container_kernel_controls()`** has a new `linux_container_backend: bool = False`
  parameter. Basic kernel controls (cap_drop, pids_limit, no_new_privileges) are now correctly
  reported as supported/active on Windows + Docker Desktop Linux containers mode.
  Profile-based controls (seccomp, AppArmor, SELinux) remain native-Linux-host-only — they
  were not tested in Phase 0 and are not claimed.

- **`ContainerizedOciSandboxRunner`** caches `_detect_linux_container_backend()` at
  construction time (`self._linux_container_backend`) and passes it to all
  `inspect_container_kernel_controls()` calls so runner metadata is accurate.

- **`sandbox_platform_capability_matrix()`** now includes a `current_wsl2_detection` field
  in its return dict, alongside the existing `current_container_backend_detection`.

- **Platform matrix hardening controls split**: `_platform_matrix_entry()` now correctly
  separates controls. When `linux_container_backend_available=True` on Windows: basic kernel
  controls added to available list; degraded modes updated to name seccomp/AppArmor/SELinux
  (not no_new_privileges/pids_limit) as requiring native Linux.

- **21 new unit tests** in `tests/unit/test_sandbox_runner.py` across four new classes:
  `TestDetectWsl2` (6), `TestInspectContainerKernelControlsLinuxBackend` (8),
  `TestPlatformMatrixHardeningControlsWithLinuxBackend` (6), `TestSandboxPlatformCapabilityMatrixWsl2Field` (1).

### Added — C3 Phases 3+4: threat model, posture API, release gate (2026-06-05)

- **`docs/runtime/SANDBOX_ESCAPE_AUDIT.md`** (new, append-only): Formal threat model mapping
  each of the 6 escape vector categories to the specific threat it blocks, the Docker/kernel
  control that prevents it, and the failure interpretation. Includes Entry 001 — the first
  live audit run (2026-06-05, Windows + Docker Desktop, 17/17 PASS).

- **`AINDY/platform_layer/sandbox_runner.py`**: Added `sandbox_escape_test_posture()` function.
  Reads `tests/sandbox/sandbox_escape_results.json` and returns a structured posture dict:
  `posture` (`"all_pass"` / `"has_failures"` / `"not_run"`), `last_run`, `host_platform`,
  `summary`, `coverage` (list of passing vectors), `gaps` (failing vectors), `operator_note`.
  Returns `"not_run"` gracefully when the artifact is absent (production install without tests/).
  Path is configurable via `SANDBOX_ESCAPE_RESULTS_PATH` env var.

- **`docs/runtime/RELEASE_CHECKLIST.md`**: Added Step 16 — Sandbox Escape Gate. Gate condition:
  `sandbox_escape_test_posture()["posture"] == "all_pass"`. Skips acceptable; FAILs block release.
  Includes audit trail instruction: append to `SANDBOX_ESCAPE_AUDIT.md` after each pre-release run.

### Added — C3 Phase 0: adversarial sandbox escape test suite (2026-06-04)

- **`tests/sandbox/`** (8 new files, 17 tests): Adversarial escape test suite that proves
  the existing Linux container-grade sandbox claim with real Docker invocations. No mocking.
  Each test documents exactly what attack vector is tested and why it matters.

  Test categories:
  - **Filesystem** (`test_filesystem_escape.py`, 3 tests): read-only rootfs blocks writes,
    plugin bind mount is read-only, tmpfs at `/tmp` is writable while `/etc` remains frozen.
  - **Network** (`test_network_escape.py`, 3 tests): `--network none` blocks outbound TCP
    and UDP; kernel-observable proof that only loopback interface is present.
  - **Process** (`test_process_escape.py`, 2 tests, Linux-only): `--pids-limit` enforcement
    via fork-bomb attempt; cgroup kernel evidence via `/sys/fs/cgroup/pids.max`.
  - **Privilege escalation** (`test_privilege_escalation.py`, 4 tests, Linux-only):
    `--cap-drop ALL` removes `CAP_NET_RAW` (raw socket blocked) and `CAP_CHOWN`;
    `--security-opt no-new-privileges` reflected in `/proc/self/status` (`NoNewPrivs: 1`);
    combined controls verified together.
  - **Host env leak** (`test_host_env_leak.py`, 2 tests): `SECRET_KEY`, `DATABASE_URL`,
    `OPENAI_API_KEY` and other production secrets absent from container; allowed key
    (`PYTHONIOENCODING`) present (confirms `--env` was transmitted).
  - **Path boundary** (`test_allowed_path_boundary.py`, 3 tests): unmounted host directory
    with canary file is inaccessible; plugin root is accessible at `/plugin-root`; path
    traversal (`/plugin-root/../../../etc/passwd`) resolves to container's own `/etc/passwd`.

- **`tests/sandbox/sandbox_escape_results.json`** (runtime artifact): written after each
  escape test session with schema_version, tested_at, host_platform, per-test results
  (status, evidence, docker_args, cmd), and pass/fail summary.

- **`pytest.ini`** + **`pytest.integration.ini`**: registered `sandbox_escape` marker.

  To run: `pytest -m sandbox_escape -v`
  Requires: Docker with Linux containers mode, internet access to pull `python:3.11-alpine`.
  Override image: `SANDBOX_ESCAPE_IMAGE=python:3.12-alpine pytest -m sandbox_escape -v`

### Fixed — PACK-DEBT-5: FastAPI/starlette CVE PYSEC-2026-161 (2026-06-05)

- Upgraded `fastapi` 0.121.0 → 0.135.0, `starlette` 0.49.1 → 1.0.1, and
  `prometheus-fastapi-instrumentator` 7.1.0 → 8.0.0 (8.x requires starlette ≥ 1.0).
  Resolves host-header injection CVE PYSEC-2026-161. `--ignore-vuln PYSEC-2026-161` removed
  from `security-audit.yml`; accepted-findings entry removed from `SECURITY_POLICY.md`.

### Added — CLI-SANDBOX-FORMAT-1: human-readable sandbox output (2026-06-05)

- **`AINDY/runtime_only.py`**: `aindy-runtime sandbox` now renders a ~25-line human-readable
  summary by default: platform, highest assurance tier, production-safe status, container
  backend detection, active runner/certification, verification method, escape test posture,
  trusted Python extension count, and degraded modes.
- **`aindy-runtime sandbox --json`**: new flag restores the full machine-readable JSON output
  (also includes `escape_test_posture` key alongside the original five fields). 9 tests pass
  in `test_runtime_cli.py`.

### Fixed — IDEM-6: advisory lock on blank-DB bootstrap (2026-06-05)

- **`AINDY/db/schema_contract.py`**: `reconcile_runtime_schema()` acquires
  `pg_advisory_lock(_BOOTSTRAP_ADVISORY_LOCK_KEY)` before the blank-DB `create_all` path.
  A second instance that wins the wait finds the DB already bootstrapped and skips `create_all`.
  Lock released in a `finally` block. SQLite paths unaffected. Lock key: `4149443900` (stable).
  3 new unit tests in `test_runtime_schema_contract.py`.

### Added — MONITORING-GRAFANA-1: Grafana monitoring profile (2026-06-05)

- **`monitoring/grafana/`** (new): Prometheus datasource provisioning, dashboard file provider,
  and `aindy-runtime.json` starter dashboard with 8 panels: health tier, active executions,
  execution rate, DB pool pressure, AI circuit breaker state, async queue depth, duration
  p50/p95/p99 timeseries, execution total by status.
- **`docker-compose.yml`**: `grafana` service added under the `monitoring` profile
  (`grafana/grafana:11.6.1`, port 3000, `grafana_data` volume, depends on Prometheus).
- Usage: `docker compose --profile monitoring up -d` → Grafana at `http://localhost:3000`.

### Added — COMPOSE-PROD-PORTS-1 + PROMETHEUS-PIN-1: Docker hardening (2026-06-05)

- **`docker-compose.prod.yml`** (new): Compose v2 override using `!reset []` to clear host
  port bindings on `postgres`, `redis`, and `mongo`. DB services remain reachable within
  the compose network; only `api` (8000) and `worker` (8001) publish to the host.
  Requires Docker Compose v2.24+.
- **`docker-compose.yml`**: `prom/prometheus:latest` pinned to `prom/prometheus:v3.4.1`.

### Added — Env-example tooling + LOCAL-1: upgrade path documented (2026-06-05)

- **`scripts/check_env_example_coverage.py`** (new): AST-parses all `AINDY/**/*.py` for
  `os.getenv()` / `os.environ.get()` calls and `Settings` field names; reports variables not
  in `AINDY/.env.example`. Run `--strict` to exit 1 on gaps. Added as advisory CI step in
  `runtime-ci.yml`.
- Root `.env.example` forwarding stub deleted; `AINDY/.env.example` is the sole canonical
  reference (docker-compose.yml's `env_file:` already pointed there).
- **`README.md`**: `## Upgrading` section added — `pip install --upgrade`, version verification,
  `AINDY_SCHEMA_RECONCILE=true` restart sequence for schema-bumping releases, Docker Compose
  pull-and-up flow, rollback guidance.

### Fixed — AGENT-APPROVE-001b: async approve dispatch (2026-06-04)

- **`AINDY/agents/agent_runtime/approvals.py`**: `approve_run()` now fires `execute_run`
  in a daemon background thread with its own `SessionLocal` session instead of blocking
  the request thread. The approve endpoint returns immediately with `status: APPROVED`;
  clients poll `GET /apps/agent/runs/{id}` for execution progress. Eliminates the
  client-side timeout on slow or multi-step tool execution.

- **`tests/unit/test_agent_approve_idempotency.py`**: All three shapes updated to use
  `threading.Event` for deterministic background-thread coordination, preventing the
  race condition between the background execute_run and the call-count assertion.

### Added — CLI artifact validation tests (2026-06-04)

- **`tests/unit/test_runtime_packaging.py`**: Added `test_installed_cli_help` and
  `test_installed_cli_help_without_database_url`. Both invoke the `main()` entrypoint in
  a subprocess with `--help`, asserting exit 0 and presence of the program name. The
  second test strips `DATABASE_URL` from the subprocess environment, validating that the
  lazy-import guard in `runtime_only.py` (CLI-1 mitigation) prevents database engine
  creation on help invocation. Covers RELEASE_CHECKLIST.md step 5 automatically.

### Added — Phase 3 hardening: cross-repo compatibility, release discipline, core debt (2026-06-04)

- **`tests/unit/test_cross_repo_compatibility.py`** (new, 7 tests): Regression suite for
  aindy-sdk and aindy-ui-kit compatibility assumptions. SDK tests (`-k sdk`): version
  envelope shape, stable syscall names present, watcher endpoint (`/watcher/signals`)
  registered in ROOT_ROUTERS. UI tests (`-k ui`): `boot_mode` field in
  `RuntimeSurfaceResponse`, `runtime_ui_surface_state()` returns non-empty `boot_mode`,
  all expected platform route prefixes served.

- **`tests/unit/test_runtime_readiness_contract.py`** (new, 7 tests): Covers IDEM-7
  (syscall registry floor), `_check_syscall_registry_status()` ok/incomplete paths,
  `/health/deep` includes `syscall_registry` check, and SCHED-001/002/003 (scheduler
  status graceful when tasks domain absent, stuck-run-watchdog fields always present).

- **`docs/runtime/SDK_CONTRACT.md`** (new): Defines what `aindy-sdk` can rely on from
  `aindy-runtime` — version envelope shape, auth contract, watcher endpoint paths, memory
  API, stable syscall table, health/readiness HTTP semantics, and known leakage risks.

- **`docs/runtime/UI_CONTRACT.md`** (new): Defines what the platform SPA
  (`@aindy/ui-kit` + `platform/src/`) can rely on — boot mode detection path
  (`/api/version → data.system.runtime.boot_mode`), auth flow fields, ROUTES table
  invariants, SPA asset 404 discrimination, operator endpoint availability, leakage risks.

- **`docs/runtime/CROSS_REPO_COMPATIBILITY.md`** (new): Policy document listing the
  5 obligations that must hold before any release touching stable surfaces, dependency
  tables for aindy-sdk and aindy-ui-kit, and the breaking-change policy.

- **`docs/runtime/RELEASE_CHECKLIST.md`** (new): 15-step operator verification checklist
  covering schema contract, unit tests, build artifacts, installed-artifact smoke, Docker
  compose stack, health endpoints, syscall registry count, watcher endpoint, platform SPA,
  and cross-repo compatibility assertions.

- **`AINDY_RUNTIME_90_DAY_CHECKLIST.md`**: Phase 3 complete — Runtime Core Debt
  Reduction, Verification Standards, Release Discipline, and Cross-Repo Boundary Proof
  items checked off. Final score: **77.5 / 100** (target was 76-80). Category deltas,
  blockers to 80+, and blockers to 85+ recorded in the Final 90-Day Review section.

### Fixed — IDEM-7: syscall registry completeness now visible in `/health/deep` (2026-06-04)

- **`AINDY/kernel/syscall_registry.py`**: Added `SYSCALL_REGISTRY_MIN_COUNT = 17` — the
  floor for expected static built-in syscalls. Serves as a canary: if Phase 8
  `_register_domain_handlers()` crashes, the count drops and `/health/deep` reports it.
- **`AINDY/routes/health_router.py`**: Added `_check_syscall_registry_status()` and wired
  it into `_build_deep_health_payload()`. The `checks.syscall_registry` field now appears
  in every `/health/deep` response with `status`, `count`, and `minimum_expected`.

### Fixed — SCHED-001/002/003: scheduler status no longer returns 500 in platform-only profile (2026-06-04)

- **`AINDY/routes/observability_router.py`**: Replaced the flow-engine-dependent
  `observability_scheduler_status_node` flow with a direct `_build_scheduler_status_payload(db)`
  helper. The new helper checks `get_symbol("task_is_background_leader")` and returns
  `tasks_domain_available: false` (not a 500) when the tasks domain plugin is absent.
  `FEATURE_FLAGS.OPERATOR_SCHEDULER_STATUS` in `platform/src/api/_routes.js` updated to
  `true` — the scheduler status NavLink is now enabled for all deployments.

### Fixed — PERMISSION-SECRET-CLEANUP-1: vestigial `PERMISSION_SECRET` scaffolding removed (2026-06-04)

- **`tests/conftest.py`**, **`alembic/env.py`**, **`scripts/check_schema_version.py`**:
  Removed `os.environ.setdefault("PERMISSION_SECRET", ...)` from all three sites. The
  field has `default=""` in `Settings` (no validator), so it requires no env var. Removing
  these defaults has no runtime effect but eliminates confusion about whether
  `PERMISSION_SECRET` is a required secret.

### Added — Phase 2 hardening: operability contracts and security isolation (2026-06-03)

- **`tests/unit/test_operability_contracts.py`** (new, 14 tests): Operability contract
  coverage for the three stable runtime surfaces (`GET /health`, `GET /ready`,
  `GET /api/version`). Covers `derive_public_status` tier mapping (critical →
  unhealthy, degraded database → unhealthy, non-critical degraded → 200),
  `_build_health_response` HTTP 503 path, `/ready` response body shape for
  `restore_pending` and `registry_restore_incomplete` 503 cases, and `/api/version`
  stable envelope fields.

- **`tests/unit/test_security_isolation.py`** (new, 25 tests): Security isolation
  regression coverage. Covers all 11 `_BLOCKED_ROOT_KEYS` stripped from extension
  context, `AINDY.*` object redaction in extension payloads, extension tenant mismatch
  rejection via `_validate_runtime_owned_call_metadata`, quota backend fail-open in
  dev/test and fail-closed in production.

- **`docs/runtime/SECURITY_MATRIX.md`** (new): Runtime security matrix mapping five
  dimensions (trusted internal execution, extension capability boundaries, tenant
  enforcement, deployment profile differences, degraded security posture) to their
  enforcement paths, test coverage, and known limitations. Includes explicit
  safe/unsafe/unsupported table for extension execution.

- **`AINDY_RUNTIME_90_DAY_CHECKLIST.md`**: Phase 2 complete — all Operability Review
  and Security Hardening items checked off; Phase 2 Exit Criteria met.

### Fixed — `watcher_router` and `db_verify_router` were never registered (2026-06-03)

- **`AINDY/routes/__init__.py`**: `watcher_router` added to `ROOT_ROUTERS` — `POST /watcher/signals`
  and `GET /watcher/signals` were returning 404 in all deployments. `db_verify_router` added to
  `PLATFORM_ROUTERS` — `GET /platform/db/verify` (live schema inspection) was also unreachable.
  Both routers existed and were correctly implemented but were never imported or mounted.

### Changed — Watcher client process extracted to aindy-sdk (2026-06-03)

- **`AINDY/watcher/`**: Client-process files (`classifier.py`, `window_detector.py`,
  `session_tracker.py`, `signal_emitter.py`, `config.py`, `watcher.py`) moved to
  `aindy_sdk/watcher/` in the `aindy-sdk` repo. Run the watcher client with
  `python -m aindy_sdk.watcher.watcher`. The server-side signal constants
  (`constants.py`) remain in `AINDY/watcher/` — `watcher_router.py` and
  `watcher_contract.py` continue to import from `AINDY.watcher.constants` unchanged.
- **`signal_emitter.py` (SDK)**: Rewritten to use stdlib `urllib.request` in place
  of `httpx` and the runtime-internal `perform_external_call` wrapper. The SDK
  watcher module has no runtime dependency and no new external dependencies.
- **`tests/unit/test_watcher_contract.py`**: Trimmed to constants-only assertions
  (signal types, activity types, timestamp parsing). Classifier and session-tracker
  tests migrated to `aindy-sdk/tests/test_watcher.py`.

### Changed — Default resource quota raised for real agent workloads (2026-06-03)

- **`AINDY/kernel/resource_manager.py`**: Default `AINDY_QUOTA_CPU_MS` raised from
  30 000 ms to 300 000 ms (5 minutes). A realistic single agent step — one
  `memory.recall` call with three OpenAI embedding round-trips — consumes ~34 s of
  wall-clock time (trace 4cc32073; see AGENT-RESLIMIT-001). The prior 30 s default
  caused `RESOURCE_LIMIT_EXCEEDED` on the very first approve of any non-trivial agent,
  a first-user experience cliff. The new 5-minute cap accommodates multi-step runs.
  **Note:** `cpu_time_ms` measures monotonic wall-clock elapsed time (including all
  network I/O wait), not actual CPU time. This is a known misnomer documented in
  `AINDY/.env.example` (Group 12) and tracked as AGENT-RESLIMIT-001 for post-GA fix.
  Configure via `AINDY_QUOTA_CPU_MS=<ms>`.
- **`AINDY/.env.example`**: New Group 12 "Resource quotas" documents all four
  `AINDY_QUOTA_*` variables with sizing guidance. The `AINDY_QUOTA_CPU_MS` entry
  carries an explicit warning that the field measures wall-clock time.
- **`tests/unit/test_resource_quota_defaults.py`**: New test pins the 300 000 ms
  default so it cannot silently drift.

### Added — PLATFORM-AUTH-ACQUISITION-1: platform SPA login + admin bootstrap (2026-05-28)

- **`platform/src/LoginPage.tsx`** (new): Login form calling `useAuth().login()`. On success,
  stores token via `AuthContext` and navigates within the router tree.
- **`platform/src/NotAdmin.tsx`** (new): Terminal "access denied" component with logout button.
  Rendered (not navigated to) when authenticated but `is_admin=false` — prevents redirect loop.
- **`platform/src/PlatformApp.tsx`**: Rewritten — `/login` lives outside `PlatformGuard`;
  guard uses `<Navigate to="/login" replace />` (React Router, respects `basename="/platform"`);
  `VITE_APP_BASE_URL` / `window.location.href` / `redirectToApp` dependency removed entirely.
- **`AINDY_BOOTSTRAP_ADMIN_EMAIL`**: New env var. Grant-only, idempotent. Processed in
  `startup.py` Phase 5.5 (after schema guard). Never revokes admin on var removal.
- **`aindy-runtime auth promote-admin <email>`**: New CLI subcommand. Grant-only, no restart
  needed. Exits 0 if already admin, exits 1 with guidance if user not found.
- **`AINDY/routing.py` — `_SPAStaticFiles`**: Falls back to `index.html` only for paths that
  do NOT start with `assets/`; `assets/` misses correctly return 404.

### Added — PLATFORM-UI-KIT-1: Docker self-contained build (2026-05-28)

- **`Dockerfile`**: `ui-builder` stage added — runs `npm ci` + `npm run build` from the
  registry-pinned `@aindy/ui-kit`. `docker compose build --no-cache` from a clean clone is
  now fully self-contained with no prior local UI build required.
- **`.dockerignore`**: `AINDY/platform/dist/` and `platform/node_modules/` excluded to prevent
  stale local state from leaking into the Docker build context.
- `@aindy/ui-kit@1.0.1` published — `loginUser`, `registerUser`, and `bootIdentity` all call
  `.then(unwrapEnvelope)`. Fixes the silent post-login redirect misfire in `PlatformHomeRedirect`.

### Fixed — Event bus now honors REDIS_URL (2026-05-27)

- **`AINDY/kernel/event_bus.py`**: Event bus now honors `REDIS_URL` as a
  fallback when `AINDY_REDIS_URL` is unset. Previously, setting only `REDIS_URL`
  produced a silently misconfigured event bus that connected to
  `redis://localhost:6379/0` regardless of the configured URL.
  `AINDY_REDIS_URL` is still honored and takes precedence when both variables
  are set, preserving deployments that intentionally route the event bus and
  cache to different Redis instances. `AINDY_REDIS_URL` is now deprecated;
  new deployments should use `REDIS_URL` only. The resolution logic is
  extracted into `resolve_event_bus_redis_url()` for testability.
  `get_redis_client()` (auxiliary wait-registry path) receives the same fix
  but does not fall through to the localhost default — it returns `None` when
  neither variable is set.
- **`AINDY/config.py`**: `AINDY_REDIS_URL` added as a `Settings` field with
  a deprecation comment, making it discoverable via settings introspection.

### Fixed — Docker compose infrastructure: blank-DB safety, pgvector, packaging, host binding (2026-05-27)

- **ALEMBIC-FRESH-DB-1**: Migrations 0002–0004 wrapped in
  `DO $$ BEGIN IF EXISTS (pg_catalog.pg_tables WHERE tablename=...) THEN ... END IF; END $$`
  blocks. On a blank database the blocks skip and Phase 5 `_enforce_schema_guard` bootstraps
  via `create_all`. On existing deployments the blocks run normally. `IF NOT EXISTS` on the
  index name alone is not sufficient — `CREATE INDEX ... ON missing_table` still raises
  `UndefinedTable` even with it.
- **COMPOSE-PGVECTOR-1**: Switched from `postgres:16-alpine` to `pgvector/pgvector:pg16`.
  Added `docker/init-pgvector.sql` (mounted to `/docker-entrypoint-initdb.d/`) running
  `CREATE EXTENSION IF NOT EXISTS vector`. Required for `memory_nodes` `VECTOR(1536)` column.
- **PACKAGING-DEP-1**: Added `"packaging>=24.0"` as an explicit dep in `pyproject.toml` and
  forced it into the Docker `/install` prefix. The multi-stage build was not propagating it
  from the builder stage, causing `import packaging` to fail at container startup.
- **COMPOSE-HOST-1**: Added `AINDY_HOST: "0.0.0.0"` to the compose `api` service environment.
  The runtime correctly defaults to `127.0.0.1` for bare installs; this override is required
  inside Docker for the published port to be reachable from the host.

---

## 1.0.0 — 2026-05-25

### Added — CLI subcommand structure (2026-05-26)

- **`aindy-runtime serve`**: New subcommand that starts the HTTP API server.
  Use this in place of the bare `aindy-runtime` invocation.
- **`aindy-runtime sandbox`**: Existing sandbox check promoted to a named subcommand.
- **`aindy-runtime --help`** and **`aindy-runtime --version`**: Now work without any
  environment configuration. Previously crashed on import if `DATABASE_URL` was absent.

### Fixed — CLI import crash without DATABASE_URL (2026-05-26)

- **`AINDY/config.py`**: `DATABASE_URL` now defaults to `""` instead of being required
  at import time. `Settings()` no longer raises `ValidationError` when `DATABASE_URL`
  is absent; validation defers to the point of actual server startup. `aindy-runtime serve`
  checks for a missing URL and exits with a human-readable error before attempting to
  start uvicorn.

### Removed — `aindy-runtime-api` entry point (2026-05-26)

- **`pyproject.toml`**: `aindy-runtime-api` console script removed. The underlying module
  (`AINDY.main`) is unchanged and remains importable. The boot-mode distinction that
  `aindy-runtime-api` encoded (`AINDY_BOOT_MODE`) is a monolith-internal concern not
  relevant for the extracted package. For advanced boot mode control, set
  `AINDY_BOOT_MODE=runtime-only` explicitly before calling `aindy-runtime serve`.

Initial PyPI release. Covers the full runtime stack: platform layer with
sandbox runner and OCI container detection, two-tier extension execution model,
idempotency gate (NF-1 through NF-5) with EffectRecord persistence and TTL
cleanup, Alembic migration chain (0001–0004), APScheduler job framework,
nodus-lang VM integration via `AINDYMemoryBridge`, platform UI (Vite + React
SPA bundled into the wheel), health and sandbox status HTTP surfaces, and a
weekly pip-audit CVE workflow. Extracted from aindy-apps-monolith; SDK
extracted as standalone `aindy-sdk`.

### Added — Auth dependency CVE monitoring and security policy (2026-05-25)

- **`pyproject.toml`**: New `security` optional-dependencies group — `pip-audit>=2.7.0`
  plus auth-adjacent floor pins (`bcrypt>=4.0.1`, `passlib>=1.7.4`,
  `python-jose>=3.5.0`). Install with `pip install -e .[security]`.
- **`.github/workflows/security-audit.yml`**: New workflow. pip-audit (OSV-backed)
  runs on every PR and weekly (Mondays 08:00 UTC). Fails on any detected CVE; prints
  advisory detail and SLA reminder. Exemptions documented in SECURITY_POLICY.md.
- **`.github/dependabot.yml`**: New file. Enables Dependabot for `pip` and
  `github-actions` ecosystems (weekly, Mondays). Secondary CVE signal for transitive
  deps pip-audit may miss against a stale lockfile.
- **`docs/runtime/SECURITY_POLICY.md`**: New file. Defines CVE response SLA
  (Critical: 7 days, High: 14 days, Medium: next minor, Low: next major), exemption
  procedure, and accepted-findings register. Closes PACK-DEBT-2.

### Changed — Integration CI now gates on failures (2026-05-25)

- **`.github/workflows/runtime-ci.yml`**: Removed `continue-on-error: true` from the
  `integration-postgres` job. Integration failures now block CI green. Closes PACK-DEBT-4.

### Decided — mypy not adopted (2026-05-25)

- **`TECH_DEBT.md`**: Closed PACK-DEBT-3. Decision: do not pursue mypy on
  `aindy-runtime` or `aindy-sdk`. Observed bug class is cross-module/cross-repo
  contract drift, which audit-arc and contract tests address directly. Reopen triggers
  documented (second engineer joins, or signature-drift bug missed by audit-arc).

### Added — Local+cloud distribution audit (2026-05-25)

- **`docs/runtime/LOCAL_AND_CLOUD_AUDIT.md`**: Full audit pass across seven areas
  surfacing gaps the local+cloud framing makes newly visible. Areas: multi-tenancy
  readiness (TENANT-1 through TENANT-4), cross-version compatibility beyond the SDK
  (COMPAT-2, COMPAT-3), operator "where am I running" clarity (CLOUD-1, CLOUD-2),
  data residency (DATA-1, DATA-2), self-update for local installs (LOCAL-1, LOCAL-2),
  cloud control plane placeholders (CLOUD-3, CLOUD-4), and open findings (G-1, G-2).
  Findings surface only — nothing fixed.
- **`TECH_DEBT.md`**: Four new entries from the audit: `TENANT-2` (quota group
  enforcement gap), `COMPAT-2` (no ABI deprecation policy), `DATA-1` (no data
  residency mechanism), `LOCAL-1` (no production upgrade path documented).

### Added — Local+cloud architecture framing documented (2026-05-25)

- **`docs/runtime/ARCHITECTURE.md`**: New top-level architecture document
  establishing the local+cloud distribution model as the explicit framing for
  the runtime. Covers the three layers (runtime data plane, SDK universal
  interface, cloud control plane not yet built), five concrete examples of how
  the framing shapes architectural decisions, what the framing does not commit
  to, and pointers to all related docs.
- **`docs/runtime/PUBLIC_API_CONTRACT.md`**: Added SDK Bridge Role section
  naming `aindy-sdk` as the universal interface targeting both local-install
  and cloud-hosted deployment contexts. Bumped `last_verified` to 2026-05-25.
- **`TECH_DEBT.md`**: Added `DEBT-COMPAT-1` — cross-version compatibility
  story between runtime and SDK. Deferred; trigger condition is when two
  runtime versions exist simultaneously in the wild.

### Fixed — DRIFT-1 + reordering guard: first-party bootstrap allowlist ratified, list_supported_sandbox_runners ordering frozen (2026-05-25)

**DRIFT-1 (docs-only, no runtime behavior change):**

- **`AINDY/platform_layer/extension_execution_model.py`** —
  `manifest-bootstrap:first-party-app` surface entry: `execution_path`
  updated from `"...restricted runtime-owned registration allowlist"` to
  `"...runtime-owned registration capability gate"`; `notes` updated from
  `"Registration-time capability checks use a narrower allowlist than
  runtime-built-in"` to `"...use the same allowlist as runtime-built-in —
  both are Tier 1 trusted kernel code under the isolation model."` This
  ratifies what the code has always done: `registry.py` lines 235–238
  explicitly assign `_FIRST_PARTY_ALLOWED_INPROC_EXTENSION_CAPABILITIES =
  _ALL_INPROC_EXTENSION_CAPABILITIES` with a three-line comment documenting
  the intent. `test_first_party_bootstrap_allows_all_registry_capabilities`
  in `tests/unit/test_extension_ownership.py` (unchanged) is the live
  evidence for this claim.
- **`AINDY/platform_layer/public_contract.py`** —
  `trusted_in_process_python.capability_boundary.first_party_bootstrap_default`
  updated from `"restricted-allowlist"` to `"full-runtime-owned-allowlist"`,
  matching `runtime_built_in_bootstrap_default`. Both fields now report the
  same value.
- **`docs/runtime/EXTENSION_TRUST_MODEL.md`** — Tier 1 first-party execution
  model bullet updated from "smaller default allowlist for `first-party-app`
  than for `runtime-built-in`" to the correct equivalence statement. Module-prefix
  restrictions (which modules may bootstrap, e.g. `AINDY.` vs `apps.`) are
  unchanged — those are distinct from capability allowlists. `last_verified`
  bumped to 2026-05-25.
- **`tests/unit/test_runtime_public_contract.py:354`** — updated assertion from
  `"restricted-allowlist"` to `"full-runtime-owned-allowlist"`.

**Reordering guard (preventative test, no code change):**

- **`TestListSupportedSandboxRunnersOperatorNote`**
  (`tests/unit/test_sandbox_runner.py`): 3 new tests asserting that each of
  the three `list_supported_sandbox_runners()` entries carries its explicit
  per-runner `operator_note`, not the posture-derived one from the
  `**sandbox_runner_assurance_posture(runner_type)` spread. Guards against a
  future dict-literal reordering that would silently place the spread after the
  explicit key, causing the posture note to override the per-runner one.

### Added — Sandbox status surfaces: HealthDashboard, /health/sandbox, CLI subcommand, platform_layer boundary (2026-05-25)

- **`platform/src/components/platform/HealthDashboard.jsx`**: Rewritten to
  render sandbox data the backend was already sending but the frontend was
  silently discarding. Added four new sections: Sandbox Posture (runner type,
  assurance class, requirement satisfaction, trust status, cert tier,
  platform/equivalence), Verification (method, kernel_observable, ceiling),
  Trusted Python (present flag, count, owner classes), and Runtime Conditions
  (conditional, shows code/classification/detail/component). Data paths:
  `health.plugin_sandbox_posture`, `health.sandbox_verification_posture`,
  `health.trusted_python_execution`, `health.runtime_conditions`.
- **`GET /health/sandbox`** (`AINDY/routes/health_router.py`): New dedicated
  endpoint (60/minute rate limit) returning 7 fields:
  `plugin_sandbox_posture`, `plugin_sandbox_platform`,
  `sandbox_verification_posture`, `trusted_python_execution`, `plugin_hosts`,
  `plugin_sandbox_attestation`, `runtime_conditions`. Integrators no longer
  need to parse the full `/health` blob. Test:
  `tests/api/test_version_api.py::test_health_sandbox_route_returns_posture`.
- **`aindy-runtime sandbox` CLI subcommand** (`AINDY/runtime_only.py`):
  `main()` now dispatches `sys.argv[1] == "sandbox"` to `_run_sandbox_check()`,
  which prints the full sandbox posture as JSON to stdout, exits 0 when
  requirements satisfied, 1 when not, 2 on unexpected error. 8 new tests in
  `tests/unit/test_runtime_cli.py` covering dispatch routing, exit codes, JSON
  validity, payload content, and error handling.
- **`AINDY/platform_layer/__init__.py` boundary enforcement**: `PUBLIC_MODULES`
  frozenset added as the machine-readable public surface. Enforced by
  `tests/unit/test_platform_layer_boundary.py` (3 tests: `PUBLIC_MODULES`
  matches `PUBLIC_API_CONTRACT.md`, `__all__` derives from `PUBLIC_MODULES`,
  every declared module has a `.py` file on disk). Prevents the three sources
  of truth — contract doc, `__init__.py`, and filesystem — from drifting
  independently.
- **`GET /health/sandbox` added to `docs/runtime/PUBLIC_RUNTIME_SURFACES.md`**
  under Experimental HTTP Surfaces.

### Changed — Non-breaking: `operator_note` field added to all `sandbox_runner_assurance_posture` branches (2026-05-25)

- **`AINDY/platform_layer/sandbox_runner.py` — `sandbox_runner_assurance_posture()`**: Added
  `operator_note` field to all four return branches. The note clarifies the relationship
  between `assurance_ceiling` (the highest tier the runner is structurally capable of
  reaching) and `verification_method` (the evidence method actually used), which are
  semantically distinct and easy to conflate when reading `/health/sandbox` or
  `/api/version` output.
  - `RUNNER_STRONG_SANDBOX_VM` kernel-observable branch: note states that both fields
    reflect achieved kernel-observable evidence.
  - `RUNNER_STRONG_SANDBOX_VM` worker-self-report branch: note states that both fields
    reflect live authenticated-RPC probe evidence.
  - `RUNNER_CONTAINERIZED_OCI`: note clarifies that `verification_method` is `"none"`
    because only `strong_sandbox_vm` runs post-launch probes; the ceiling reflects
    structural capability, not active probing.
  - `RUNNER_INSECURE_DEV_SUBPROCESS` (fallback): note states both fields reflect the
    absence of any sandbox boundary or isolation evidence.
- **`RUNNER_CONTAINERIZED_OCI` `ceiling_note` rewritten**: Previous text read
  `"Same limitation as strong_sandbox_vm."` — replaced with an accurate description:
  `"Container runner reaches worker-self-report-verified when probed; kernel-observable
  evidence is unavailable for shared-kernel container sandboxes."` No existing
  `assurance_ceiling` or `verification_method` values changed.
- **Non-breaking**: `operator_note` is an additive new field. No existing field values
  were modified (except `ceiling_note` on `RUNNER_CONTAINERIZED_OCI` which was
  corrected). Consumers reading only `assurance_ceiling` and `verification_method`
  are unaffected.

### Added — C2/NF-2, NF-8: Contract decision recorded and trust model matrix rewritten (2026-05-24)

- **`docs/runtime/EXTENSION_TRUST_MODEL.md` — Supported Platform Sandbox Matrix**
  (NF-8): Windows and macOS entries rewritten. Both now read
  `production-safe third-party plugin sandbox support: yes, when the configured
  container runtime is in Linux-containers mode`. Previous entries read `no`. New
  `container hardening` bullet for each platform describes that Linux kernel
  hardening controls (`no_new_privileges`, `drop_all_capabilities`, `pids_limit`,
  `seccomp`, `apparmor`, `selinux_label`) run inside the container's Linux kernel
  under the host virtualization layer and are not host-introspectable. New
  `degraded mode` entry for Windows describes Windows-containers mode fail-closed
  behavior. Linux and Other entries are unchanged.
- **`docs/runtime/EXTENSION_TRUST_MODEL.md` — Important Implications** (NF-8):
  Rewritten. "documented Linux containerized guarantees" → "documented Linux
  container guarantees, detected by querying `OSType`." Removed Linux-only
  framing for production-safe container support. Added explicit statement that
  non-Linux hosts can reach container-grade certification but not strong-sandbox
  or `hostile-third-party` certification. Added statement that `containerized_oci`
  on Windows and macOS in Linux-containers mode is production-safe for
  `single-instance`, `distributed-api`, and `distributed-worker` profiles.
- **`docs/runtime/EXTENSION_TRUST_MODEL.md` — Production-Safe Third-Party Plugin
  Sandbox Semantics** (NF-2): New subsection documenting the contract decision.
  Defines "production-safe third-party plugin sandbox" as a property of the
  container backend (`OSType=linux`), not the host OS. Documents the two
  detection conditions and their evaluation via `_detect_linux_container_backend`.
  Confirms strong-sandbox guarantees remain Linux-host-bound. Cross-references
  live verification evidence: `sandbox_certification_profile` returned
  `tier_status: certified` at `container-sandbox-certified` on Windows + Docker
  Desktop with all five hardening controls accepted by the container kernel.
- **`ISOLATION_MODEL_PLAN.md` Gap 4 and C2 reopen entries**: Annotated as
  CLOSED. Gap 4 retitled "PARTIALLY CLOSED — container-grade closed (C2,
  2026-05-24); strong-sandbox remains deferred (C3)." C2 reopen entry updated
  with closure evidence and C3 forward pointer.
- **`TECH_DEBT.md`**: Added closed C2 entry with live verification evidence.
  Added open C3 entry for cross-platform strong-sandbox as the appropriate
  follow-up gap, with its own reopen condition
  (`tier_status: certified` at `strong-sandbox-certified` on a non-Linux host).
- **This closes C2.** The C2 reopen condition — "a non-Linux host platform
  produces a sandbox runner type passing the shared worker policy certification
  suite with assurance class at or above `container-grade-sandbox`" — is met.
  C3 (strong-sandbox cross-platform parity) is now the tracked follow-up.

### Added — C2/NF-5: Certification suite proves container-sandbox-certified is platform-neutral (2026-05-24)

- **`TestContainerSandboxCertificationCrossPlatform`**
  (`tests/unit/test_plugin_sandbox_certification.py`): 11 new test cases proving
  `sandbox_certification_profile` reaches `tier_status: "certified"` at
  `container-sandbox-certified` on simulated Windows and macOS hosts (positive: Linux,
  Windows + Linux backend, macOS + Linux backend), and stays uncertified when conditions
  are not met (negative: Windows-containers mode, no runtime, no pinned digest,
  wall-clock-only limits). One parametrized diagnostic case (4 sub-cases) confirms each
  of the four `launch_attestation` verified fields — `backend_identity`, `runtime_identity`,
  `mount_mode`, `resource_limit_mode` — is independently required.
- **`sandbox_certification.py` required zero changes** — confirmed by audit: the function
  contains no `platform.system()` calls and reads `platform_matrix["current_environment"]`
  which is now a dynamic runtime-resolved dict produced by `_detect_linux_container_backend`.
  Tests inject both `runner_metadata` and `platform_matrix` as synthetic dicts so no
  subprocess calls or Docker invocations are made.

### Added — C2/NF-1, NF-4, NF-7: Non-Linux hosts with Docker Desktop in Linux mode become production-safe (2026-05-24)

- **`_platform_matrix_entry`** (`AINDY/platform_layer/sandbox_runner.py`): new
  `linux_container_backend_available: bool` parameter. The
  `production_safe_third_party_plugin_execution` field is now computed as
  `(linux AND runtime_available) OR (runtime_available AND linux_container_backend_available)`,
  allowing Windows and macOS hosts running Docker Desktop or Podman in Linux-container
  mode to receive `production_safe=True`. The Linux kernel-control reporting
  (`available_hardening_controls`) remains Linux-host-only (honesty principle: those
  controls are active inside the container VM but are not host-introspectable). New
  `degraded_modes` entries distinguish "Linux containers via host virtualization" from
  "container runtime present but not a Linux-container backend". `operator_note` updated.
- **`sandbox_platform_capability_matrix`**: calls `_detect_linux_container_backend` once
  and threads `linux_container_backend_available` into the `current_environment` entry;
  static platform entries (`supported_platforms`) pass `True` only for the Linux entry
  and `False` for all others (declared support model, not detection-dependent). New
  top-level key `current_container_backend_detection` surfaces the full detection result
  dict for operator visibility (e.g., `GET /api/version`). `support_contract` gains the
  new `production_safe_third_party_supported_host_platforms` key — the dynamically resolved
  list of platforms where the running runtime can deliver production-safe third-party plugin
  execution; starts as `["linux"]` and grows to include the current non-Linux platform when
  backend detection returns `linux_container_backend: True`. Existing
  `production_safe_container_supported_host_platforms` key is unchanged.
- **NF-6 auto-resolved**: `extension_execution_model_contract()` queries
  `production_safe_third_party_supported_host_platforms` from `support_contract` to
  populate `platform_support.production_safe_host_platforms` on the
  `dynamic-plugin-node:external-third-party` surface. That field now correctly reflects the
  active backend rather than silently resolving to `[]`.
- **`PRODUCTION_SAFE_CONTAINER_SUPPORTED_HOST_PLATFORMS`** constant is **unchanged** —
  it remains `(PLATFORM_LINUX,)` as the static declared support set.
- **Note**: `deployment_contract.py` validation paths continue to read the same
  `production_safe_third_party_plugin_execution` matrix field — they will benefit from this
  change automatically. NF-5 (certification suite tests on non-Linux platforms) is the next
  step to exercise that end-to-end path.
- **Unit tests** (`tests/unit/test_sandbox_runner.py`, `TestPlatformMatrixWithLinuxContainerBackend`):
  7 cases; plus 1 NF-6/NF-7 integration case confirming `extension_execution_model_contract()`
  populates `production_safe_host_platforms` on Windows with a Linux backend. Two existing
  platform-matrix tests updated to patch `subprocess.run` (determinism — `_detect_linux_container_backend`
  now shells out on non-Linux hosts).

### Added — C2/NF-3: Linux container backend detection helper (2026-05-24)

- **`_detect_linux_container_backend(container_runtime)`**
  (`AINDY/platform_layer/sandbox_runner.py`): new module-level helper that
  determines whether the configured container runtime is currently operating
  as a Linux-containers backend. Returns a structured result dict with
  `runtime`, `runtime_available`, `linux_container_backend`, `os_type`,
  `detection_method`, `detection_error`, and `operator_note` keys.
  Detection logic: on Linux hosts the binary presence alone is sufficient
  (`detection_method: "shutil_which_only"`); on non-Linux hosts the helper
  shells out to `{runtime} info --format '{{json .}}'` and inspects the
  `OSType` field (`detection_method: "docker_info_json"`). Fails closed on
  timeout, non-zero exit, `FileNotFoundError`, or JSON parse failure
  (`linux_container_backend: False`). Not yet wired into
  `_platform_matrix_entry` — that is NF-1 / NF-4.
- **Unit tests** (`tests/unit/test_sandbox_runner.py`,
  `TestDetectLinuxContainerBackend`): 9 cases covering Linux/Windows/macOS
  host paths, timeout, invalid JSON, non-zero exit, and unavailable runtime.

### Added — IDEM-9: EffectRecord TTL cleanup (2026-05-24)

- **Cleanup job** (`AINDY/platform_layer/scheduler_service.py`):
  `_cleanup_expired_effect_records()` registered as a 24-hour interval APScheduler job.
  Deletes finalized `effect_records` rows (status ≠ `pending`, `completed_at IS NOT NULL`)
  older than 90 days in batches of 10,000 rows per commit. Pending rows are never deleted.
  Stale pending rows (older than 1 hour) trigger a `WARNING` for operator visibility.
  Constants: `EFFECT_RECORD_TTL_DAYS=90`, `EFFECT_RECORD_CLEANUP_INTERVAL_HOURS=24`,
  `EFFECT_RECORD_DELETE_BATCH_SIZE=10_000`.
- **Migration 0004** (`alembic/versions/0004_effect_records_completed_at_index.py`):
  Adds `ix_effect_records_completed_at_status` — composite partial index on
  `(completed_at, status) WHERE completed_at IS NOT NULL` — to support the cleanup query
  at production volume. Idempotent (`IF NOT EXISTS`).
- **ORM model** (`AINDY/db/models/effect_record.py`): `ix_effect_records_completed_at_status`
  added to `EffectRecord.__table_args__`. `SCHEMA_CONTRACT_VERSION` bumped to "2026-05-24.1".
  `scripts/schema_version_baseline.json` regenerated.
- **Unit tests** (`tests/unit/test_effect_record_cleanup.py`): 6 tests — no-op path,
  finalized-row deletion, multi-batch loop, single-full-batch boundary, stale-pending
  warning, exception isolation.
- **Integration test** (`tests/integration/test_effect_record_cleanup_e2e.py`): 1 test —
  verifies expired row deleted, pending row preserved, recent row preserved against real
  Postgres. IDEM-9 closed in `TECH_DEBT.md`.

### Added — Idempotency layer: NF-1 through NF-5 (2026-05-24)

- **NF-1** (`AINDY/db/models/effect_record.py`, `alembic/versions/0003_effect_records.py`):
  New `EffectRecord` ORM model and Alembic migration 0003. Table stores per-syscall
  idempotency records keyed by `action_id` (SHA-256). `SCHEMA_CONTRACT_VERSION` bumped
  to "2026-05-24".
- **NF-2** (`AINDY/core/retry_policy.py`): `RetryPolicy` dataclass gains
  `execution_guarantee: str = "AT_LEAST_ONCE"` field. `AGENT_HIGH_RISK` constant sets
  `execution_guarantee="EXACTLY_ONCE"`. `_resolve_policy_for_eu()` in `execution_gate.py`
  serialises the field into `ExecutionUnit.extra["retry_policy"]`.
- **NF-3** (`AINDY/core/execution_gate.py`): `compute_action_id(action_type, input_payload, scope)`
  returns a deterministic SHA-256 hex digest used as the idempotency key.
- **NF-4** (`AINDY/runtime/nodus_adapter.py`, `AINDY/runtime/flow_engine/runner_steps.py`):
  `is_retryable_error()` wired into agent step and flow node retry loops. Non-transient
  errors (permission denied, 404, unauthorized, etc.) skip retry immediately.
- **NF-5** (`AINDY/kernel/syscall_dispatcher.py`): Idempotency gate inserted in
  `SyscallDispatcher._dispatch()` between Step 2e (deprecation check) and Step 3
  (handler execution). For `EXACTLY_ONCE` syscalls the gate checks `effect_records`
  before calling the handler; a cache hit returns the stored result without re-executing.
  AT_LEAST_ONCE syscalls are completely unaffected. Gate EU lookup is try/except wrapped
  (graceful skip if EU unavailable); EffectRecord write is a hard invariant.
  `_resolve_effect_record` and `_complete_effect_record` use `db.commit()` (not `flush()`)
  so pending and final EffectRecord states are durable across session close.
- **NF-1 fix** (`AINDY/db/models/effect_record.py`): `server_default` for the UUID primary
  key uses `text("gen_random_uuid()")` instead of a bare string literal, preventing
  SQLAlchemy from quoting it as a literal UUID value on Postgres.
- **Schema baseline** (`scripts/schema_version_baseline.json`): updated to reflect the
  corrected `effect_record.py` model hash at version "2026-05-24".

### Added — IDEMPOTENCY_CONTRACT.md (2026-05-24)

- `docs/runtime/IDEMPOTENCY_CONTRACT.md`: canonical contract for effect-level
  idempotency. Covers three enforcement layers (DB constraints, Alembic migration
  idempotency, NF-5 effect gate), 8 required invariants, EffectRecord state machine,
  action_id derivation contract, execution guarantee labels, interaction with
  EXECUTION_CONTRACT.md and RETRY_POLICY.md, exclusion scope, enforcement/verification
  matrix, and 5 open operational questions.

### Added — Platform UI sub-project (2026-05-24)

- `platform/` — standalone Vite + React 19 SPA (`@aindy/platform-ui`) that
  consumes `@aindy/ui-kit` for all shared surfaces. Replaces the previous
  arrangement where the monolith served platform components.
- 7 platform components copied and adapted: `AgentConsole`, `FlowEngineConsole`,
  `ObservabilityDashboard`, `HealthDashboard`, `AgentApprovalInbox`,
  `AgentRegistry`, `RippleTraceViewer`. `ExecutionConsole` replaced with a
  runtime-only stub (domain analytics panels are monolith-only).
- API modules (`agent.js`, `operator.js`, `platform.js`, `analytics.js`,
  `rippletrace.js`) present locally; `_core.js` and `_routes.js` re-export
  from `@aindy/ui-kit`.
- `ErrorBoundary.jsx` simplified for runtime: no `reportClientError` call.
- `AINDY/routing.py`: `StaticFiles(html=True)` mounted at `/platform` after
  `enforce_registered_route_execution` so platform API routes keep full
  priority. Mount is skipped gracefully if `platform/dist/` is absent.
- Build: `cd platform && npm install && npm run build` (must run before serving).

### Added — Alembic migration layer + idempotency fixes (2026-05-23)

- Added Alembic to `aindy-runtime` (`alembic==1.17.0` in deps). Runtime uses
  `alembic_version_runtime` table to avoid conflicts with monolith `alembic_version`.
- Migration `0001_runtime_baseline`: empty baseline — stamps existing schema-bootstrapped
  deployments at the Alembic split point.
- Migration `0002_idempotency_constraints`: closes IDEM-2, IDEM-3, IDEM-4, IDEM-5.
  Adds partial unique indexes on webhook_subscriptions, platform_api_keys, execution_units,
  dynamic_flows, dynamic_nodes. Includes deduplication step for existing data.
- `include_object` filter in `alembic/env.py` restricts autogenerate to runtime-owned tables.
- 3 new integration tests in `test_schema_contract.py` verify Alembic version table,
  head revision, and all idempotency indexes.

### Fixed — Idempotency audit findings (2026-05-23)

- **IDEM-1** (`AINDY/kernel/syscall_registry.py`): `VersionedSyscallRegistry.__setitem__`
  now raises `ValueError` on conflicting re-registration with a different handler.
- **IDEM-8** (`AINDY/apscheduler/schedulers/background.py`): Stub `BackgroundScheduler`
  now raises `ConflictingIdError` when `add_job()` is called with a duplicate id and
  `replace_existing=False`, matching real APScheduler behavior.

### Changed

- SDK extraction complete. `AINDY/sdk/` removed from `aindy-runtime`.
  `aindy-sdk` is now a standalone package at
  https://github.com/Masterplanner25/aindy-sdk-
  with its own CI, 47 passing tests, and independent release cycle.
  `aindy-runtime` no longer ships client code.

### Gap C1 Scope B1 - Kernel-Observable Post-Launch Verification On Linux (2026-05-23)

Gap C1 Scope B1 - Kernel-observable post-launch verification on Linux. Added
`AINDY/platform_layer/kernel_proc_reader.py` implementing unprivileged `/proc`
reads for seccomp status, cgroup membership, and namespace IDs.
`_verify_post_launch_state` now layers kernel evidence on top of the existing
RPC probe on Linux hosts. `verification_method` transitions to
`kernel-observable` and `assurance_ceiling` transitions to
`kernel-observable-verified` when evidence is available. Non-Linux hosts remain
at `worker-self-report-verified`. No new processes. No privilege escalation.

### Gap C1 Scope A - Machine-Readable Sandbox Verification Posture (2026-05-23)

Gap C1 Scope A - Sandbox verification posture now machine-readable. Added
`verification_method` (`worker-self-report`) and `assurance_ceiling`
(`worker-self-report-verified`) to `/api/version` sandbox capability metadata
and `/health` `sandbox_verification_posture`. Kernel-observable verification
(Scope B) remains deferred.

### Hygiene Pass — Dev Environment, CI Hardening, Subsystem Contract Tests (2026-05-23)

Four-item hygiene pass covering dev environment reliability, supply chain
security, and contract-level test coverage for three runtime subsystems.

**Item 1 — prometheus_client missing from dev install (no file change)**

`prometheus-fastapi-instrumentator>=6.1.0` was already in `pyproject.toml`
main `dependencies`. The dev environment had been set up with
`pip install -e .[test] --no-deps` (matching CI). Fix: run
`pip install -e .[test]` without `--no-deps` to pick up transitive deps.

**Item 2 — `.env.example` and `.gitignore`**

- `.env.example` created at repo root with five documented groups: required
  boot, boot mode, schema control, optional infrastructure, local smoke test.
- `.gitignore` updated to include `.env` (was missing).

**Item 3 — GitHub Actions SHA-pinning**

All floating action tags replaced with pinned commit SHAs across both
workflow files:

- `.github/workflows/runtime-ci.yml`: `actions/checkout@…# v4` (×4),
  `actions/setup-python@…# v5` (×4), `actions/cache@…# v4` (×1).
- `.github/workflows/release-staging.yml`: `actions/checkout@…# v4`,
  `actions/setup-python@…# v5`, `actions/upload-artifact@…# v4`.

**Item 4 — Subsystem contract tests**

Three new test files under `tests/unit/`, each marked `@pytest.mark.runtime_only`:

- `test_worker_contract.py` — 7 tests for `WorkerHealthServer`: construction,
  check registration, start/stop lifecycle (ephemeral port 0), HTTP 200/503/404
  response correctness, idempotent `start()`.
- `test_watcher_contract.py` — 13 tests for classifier (`classify()` covering
  idle/work/distraction/communication/unknown paths and browser title patterns),
  `VALID_SIGNAL_TYPES`, `VALID_ACTIVITY_TYPES`, `parse_timestamp()`, and
  `SessionTracker` state machine transitions through IDLE → CONFIRMING_WORK →
  WORKING with `session_started` event emission.
- `test_nodus_runtime_contract.py` — 5 tests for `AINDYMemoryBridge`
  (constructor, `_safe_node()` from dict, from object, null-tags default) and
  `AINDYNodusRuntime` subclass assertion (skipped if nodus-lang absent).

**SDK deferred:** `AINDY/sdk/` is a self-contained `aindy-sdk 1.0.0` package
(stdlib-only, own `pyproject.toml`, own `tests/`, own `examples/`). It does
not belong in this repo long-term. Documented in `TECH_DEBT.md`. SDK test
coverage intentionally omitted from this pass.

**Verification:** 245 passed, 1 skipped (up from 220/1). Non-zero coverage on
all three targeted subsystems.

---

### Contract Clarification — Tiered Isolation Model (2026-05-23)

Adopted the Tiered Isolation Contract vocabulary throughout runtime governance
docs. No code or test changes in this pass.

**What changed:**

- `docs/runtime/EXTENSION_TRUST_MODEL.md` — Introduced explicit Tier 1 /
  Tier 2 vocabulary. Renamed "Trusted Extension Classes" to "Tier 1 Trusted
  Kernel Code." Removed all "residual exception," "privileged exception," and
  "explicit privileged exception set" language. Added Tier 1 attestation
  exclusion paragraph to the Assurance Reporting section. Updated Operational
  Guidance to describe first-party manifest bootstrap as intentional Tier 1
  kernel code rather than a transitional exception.

- `docs/runtime/EXTENSION_CAPABILITIES.md` — Added "Tier Model Scope"
  subsection explicitly distinguishing Tier 1 registration gates from Tier 2
  execution confinement. Updated the manifest-bootstrap Enforcement entry to
  name Tier 1 kernel-resident execution.

- `docs/runtime/PUBLIC_RUNTIME_SURFACES.md` — Added Tier 1 / Tier 2 tier
  labels to the Extension Registration Surfaces section. Updated the Ownership
  model subsection to name each ownership class's tier. Removed the
  "extraction-era architecture" transitional framing from the experimental
  classification reason.

**What the tiered model says:**

- Tier 1 (trusted-operator kernel-resident): `runtime-built-in` and
  `first-party-app` bootstrap code, kernel-resident callables, and
  runtime-built-in plugin nodes run in the main interpreter by design.
  They are not exceptions to a more-isolated baseline.
- Tier 2 (third-party externalized): All `external-third-party` execution
  goes through the isolated plugin-host subprocess boundary. No exceptions.

**Deferred (documented in ISOLATION_MODEL_PLAN.md):**

- Strong-sandbox live verification (plan item C1)
- Cross-platform strong sandbox (plan item C2)

---

### Code Change — Two-Tier Execution Model Enforced in Contract and Tests (2026-05-23)

Removed the `capability-confined-in-process-exception` execution model class
and reclassified all Tier 1 surfaces. The published contract now exposes exactly
two execution model classes. The public contract test suite asserts the two-tier
model.

**What changed:**

- `AINDY/platform_layer/extension_execution_model.py` — Removed
  `EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION` constant and the
  corresponding third entry from `execution_model_classes`. Reclassified
  `manifest-bootstrap:runtime-built-in` and `manifest-bootstrap:first-party-app`
  `execution_model_class` from the removed constant to `EXECUTION_MODEL_KERNEL_RESIDENT`.
  Updated `registration_boundary` for all Tier 1 surfaces
  (`manifest-bootstrap:*`, `registry-kernel-callable:*`,
  `runtime-callback-worker:*`, `dynamic-plugin-node:runtime-built-in`) from the
  removed constant to `"registration-capability-gate"`. Updated
  `attestation_scope.plugin_sandbox_attestation.notes` and `operator_note` to
  use Tier 1 / Tier 2 vocabulary.

- `tests/unit/test_runtime_public_contract.py` — Updated
  `test_runtime_public_contract_publishes_extension_execution_model_matrix` to
  assert exactly two execution model classes (`"kernel-resident"` and
  `"isolated-externalized"`), assert `"capability-confined-in-process-exception"`
  does not appear in the contract, assert `manifest-bootstrap:runtime-built-in`
  and `manifest-bootstrap:first-party-app` surfaces have
  `execution_model_class = "kernel-resident"`, and assert
  `registry-kernel-callable:first-party-app` has
  `registration_boundary = "registration-capability-gate"`.

**Verification:** 220 passed, 1 skipped across the full test suite.

---

### Production Hardening - Dependency Pins, Async Context Coverage, and Schema CI (2026-05-23)

Pinned the remaining loose observability dependencies, added import-contract
coverage for the async execution context helper, and enforced schema contract
version bumps in CI when runtime-owned ORM models change.

**What changed:**

- `pyproject.toml` and `AINDY/requirements.txt` - Replaced the six loose
  lower-bound observability constraints with exact pins matching the currently
  installed working versions: `opentelemetry-api==1.42.1`,
  `opentelemetry-sdk==1.42.1`,
  `opentelemetry-instrumentation-fastapi==0.63b1`,
  `opentelemetry-exporter-otlp-proto-grpc==1.42.1`,
  `prometheus-fastapi-instrumentator==7.1.0`, and
  `python-json-logger==4.1.0`.

- `tests/unit/test_async_execution_context.py` - Added runtime-only tests for
  `activate_async_execution_context`, `deactivate_async_execution_context`, and
  `is_async_execution_active`. The module now has explicit coverage for import,
  default inactive state, activation, and token-based restoration.

- `scripts/check_schema_version.py` and
  `scripts/schema_version_baseline.json` - Added a standalone schema contract
  checker that hashes the runtime-owned ORM model sources
  (`AINDY/db/models/*.py` plus `AINDY/memory/memory_persistence.py`), imports
  `SCHEMA_CONTRACT_VERSION`, and fails when ORM definitions change without a
  matching version bump. The initial committed baseline records the current hash
  and version.

- `.github/workflows/runtime-ci.yml` - Added
  `python scripts/check_schema_version.py` to the `runtime-contracts` job after
  dependency installation and before pytest.

**Verification:** `pip install -e .[test]` succeeded. `pytest --tb=short -q`
passed at 249 passed, 1 skipped after the new async context tests. The
runtime-only `/api/version` smoke check still reported `boot_profile =
platform-only` and `app_plugins_loaded = false`. The schema checker created its
baseline, failed with the expected contract message when a model-file hash was
temporarily changed without a version bump, and returned to a clean pass after
reverting the temporary change.
