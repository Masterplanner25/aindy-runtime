---
title: "App Handoff — aindy-runtime v2.2.0"
api_version: "1.0"
last_verified: "2026-08-16"
status: current
owner: "platform-team"
---
# App Handoff — `aindy-runtime` v2.2.0

**Published to PyPI 2026-08-16 as `aindy-runtime==2.2.0`** (verified against the package index,
and the published wheel installed into a clean venv and inspected — not just "CI was green").
Release commit `c09b01f`, tag `v2.2.0`, full pipeline green, Linux sandbox-escape gate 17/17 PASS
(`SANDBOX_ESCAPE_AUDIT.md` Entry 015).

**Minor, and it arrives passively like 2.1.0 — but unlike 2.1.0 it *removes* something.**
Supersedes `APP_HANDOFF_v2.1.0.md`.

---

## 1. The one thing that can break you

**A Nodus guest script can no longer reach subprocess, network, or the host environment.**

`GUEST-CONFINE-1` — the P0 we flagged in `APP_HANDOFF_v2.1.0.md` §7 as *open and relevant to you
because you run Nodus scripts* — is **closed in this release**. The guest VM was being built with
none of the confinement arguments it accepts, so a script reached `subprocess`, network and host
env **without passing the dispatcher, capability token, effect ledger, egress guard or tool
registry**. It was demonstrated, not inferred: a guest script created a file on the host.

The fix denies all three. **31 builtins are now refused**, each with a `SandboxError` naming the
flag:

| Denied | Count | Notable |
|---|---|---|
| Subprocess | 7 | `subprocess_run`, `subprocess_shell`, `subprocess_spawn` |
| Network | 18 | all `http_*` verbs and their `_async` variants |
| Host environment | 6 | `env_get`, and the writes **`env_set`** / **`env_unset`** |

**We measured your exposure before shipping: zero.** No `.nd`/`.nodus` script in
`aindy-apps-monolith` (2 scripts) or in this repo (8) calls any of them. So this is expected to
break nothing — **but that measurement is of your scripts as committed.** If anything generates
Nodus source at runtime, or if a script arrived after we looked, it will now fail loudly rather
than silently escaping. Loud is the intended failure mode.

**What to do instead**, if you find you need one of these: reach it through the mediated seams —
`call_tool(...)` for outbound effects (enforces the run's scoped capability token) or the bare
`sys(...)` builtin for runtime capabilities. Configuration a script needs should be passed in via
flow state or `input_payload` rather than read from host env. See
`NODUS_DEVELOPER_GUIDE.md` §1.1.

**There is no env var to turn this off, deliberately.** A global switch would re-open the
boundary for every run at once. Per-execution declaration is `EXEC-ENV-BIND-1`, still open.

---

## 2. `FR-15` — your defect. Two of three parts shipped

You filed this on 2026-08-16 after a Genesis session sat **177 seconds** with no events emitted.
You inferred a single-slot serialisation and explicitly declined to claim the mechanism.

**There is one, we found it, and it is default-on.**

`_scheduler_heartbeat_tick` is the only thing that drains the scheduler queue. It runs on a
**1-second APScheduler job with `max_instances=1`**, and it dispatched each item
**synchronously** — because `_decide_mode()` returns `INLINE` for everything. Rule 2 in that
function short-circuits Rules 4 and 5: `AINDY_ASYNC_HEAVY_EXECUTION` **defaults to false**. So
the entire async path — including *"high-priority work should never block a request thread"* and
the routing of `{flow, agent, nodus, job}` to threads — was **unreachable by default**.

Demonstrated across all eight type × priority combinations: every one returns `INLINE` unset,
`ASYNC` set.

Your `maximum number of running instances reached (1)` log was not a side-symptom. It was the
queue being blocked, printing once per starved second.

**Your three asks:**

**(1) Is there a single-slot serialisation?** Yes — above. **Predates 2.1.0**; your caution about
misattribution was right, and stronger than you claimed.

**(2) Emit something while queued — SHIPPED.** A `scheduler.queued` SystemEvent now fires at
enqueue, carrying `queue_depth`. It lands in `system_events`, the table you were querying.
There is also a new `aindy_scheduler_queue_wait_seconds` histogram, bucketed to **300s** because
your observed waits were 22s / 48s / 184s and a default histogram would have put all of them in
`+Inf`. The depth gauge `aindy_scheduler_queue_depth` already existed.

> **It is `scheduler.queued`, not the `execution.queued` you asked for, and that is not
> bikeshedding.** The execution-contract gate raises for any `execution.*` event emitted outside
> a pipeline, and the two hottest enqueue callers — the event-bus subscriber thread and wait
> expiry — have no pipeline active. Your requested name would have raised in exactly the paths
> that matter. Off switch: `AINDY_SCHEDULER_QUEUE_EVENTS=false`.

**(3) Consider a bound — PARTIALLY.** We did not bound the wait, but we removed its worst
consequence. Wait firing now runs on **its own job and its own thread**. Previously
`tick_time_waits()` lived inside `schedule()`, so a slow execution skipped the next tick and
**no time-based wait fired** — a flow parked on a timer stayed parked because an *unrelated*
flow was busy. That is a correctness bug, and it is also why `/health` died at 2.7 cores for 13
minutes: the same tick drove wait expiry and stale-wait cleanup.

### What is left, and it is yours to trial

**`FR-15` (a): dispatch still runs INLINE by default.** Work still queues behind a single 1s
tick. This release makes that wait *visible* and stops it starving timers and health — it does
not remove it.

The remaining step is flipping **`AINDY_ASYNC_HEAVY_EXECUTION=1`**, which you already control. It
is one env var, it makes Rules 4/5 live as designed, and it changes `flow`/`agent`/`nodus`/`job`
from inline to threaded. **Per the standing split, soak happens in your repo, not ours** — so
this is a deliberate handoff, not an omission. With (c) shipped, the first occurrence after you
flip it is diagnosable.

Your own amplifier — the synchronous `sys.v1.analytics.execute_infinity` per Genesis chat message
— is still worth removing. It lengthens each queued item. But it is an amplifier: a 177s queue
for one user's second message is not explained by one extra syscall.

> **One correction while we were checking it.** Your write-up cites
> `apps/automation/flows/flow_definitions.py:254`. In the current tree that syscall appears at
> **lines 258, 375 and 554** — *three* call sites, not one. We have not traced which are on the
> Genesis hot path, so we are not claiming the amplifier is three times worse; only that if you
> remove one by line number you may leave two. Worth a look before you conclude it is gone.

---

## 3. `IDEM-11` — the effect-gate audit is done

`APP_HANDOFF_v2.1.0.md` §7 told you *"only one of the registered syscalls declares its execution
guarantee. Duplicate-effect exposure in default configuration is real."* That audit is complete.

**Declared `EXACTLY_ONCE` went 1 → 7.** Added: `event.emit`, `flow.run`, `flow.execute_intent`,
`nodus.execute`, `job.submit`, `agent.undo` — each a call where a retry produces a *second*
effect.

Two corrections to what we told you before:

- The registry holds **23** syscalls, not 27.
- The one pre-existing declaration was **`memory.write`**, not `memory.delete`. That inverts the
  significance: the guarded call was the busiest write path in the runtime, not the syscall with
  no callers.

**Also fixed: `register_syscall` had no `execution_guarantee` parameter at all.** `SyscallEntry`
accepted it; the function never forwarded it. So **every syscall your plugins register was
`AT_LEAST_ONCE` with no way to opt in** — the gate was unreachable for app syscalls by
construction. It now works: `register_syscall(..., execution_guarantee="EXACTLY_ONCE")`, and a
typo raises rather than silently downgrading.

**The gate is still default-off** (`AINDY_SYSCALL_IDEMPOTENCY`). Declarations are inert until it
is enabled or a run is a durable continuation, so nothing changed in your behaviour this release.
Flipping it is the remaining step and also wants soak.

---

## 4. New settings and surfaces

| Name | Default | What it does |
|---|---|---|
| `AINDY_SCHEDULER_QUEUE_EVENTS` | `true` | Emit `scheduler.queued`. Set `false` if the volume is unwelcome. Resolved per call — no restart. |

**New event type: `scheduler.queued`** — one row per enqueued execution unit, roughly the volume
of the `execution.started` row that already exists per item. If you enumerate event types
anywhere, it will appear.

**New scheduler job: `scheduler_wait_tick`** — if any dashboard or assertion enumerates
APScheduler jobs, expect a second one alongside `scheduler_heartbeat_tick`.

**New metric:** `aindy_scheduler_queue_wait_seconds{priority}`.

**New API parameter:** `register_syscall(..., execution_guarantee=...)`.

---

## 5. Your scope answer — received, recorded, and it settled the design

You answered §6 of the last handoff with your real call surface rather than a preference. It is
recorded against `HTTP-SCOPE-GAP-1`.

**The finding that decided it:** your UI is *two privilege classes sharing one JWT*, and the
client already draws that line itself (`useAuth().isAdmin`, `<AdminAccessRequired />`) —
**frontend-only today**. So deriving authority from the user row does not impose a new model on
you; it makes the server enforce a boundary your UI already draws. That is the strongest argument
for the approach, and it is yours, not ours.

Both your constraints are accepted: admin scopes tie to the **existing user-row admin flag** (no
second source of truth for "is this an operator"), and we have recorded that **`execution.read`
conflates scope with data ownership** — a scope cannot answer *"may I read someone else's"*, and
that is the same distinction `memory_agents_list` owner-scoping just hit.

**Not in 2.2.0.** When it ships, the release handoff will **name the scopes being enforced**, per
your request — a narrowing that lands without a note reads as scattered 403s and looks like a
frontend bug.

---

## 6. `FR-14` — read this before concluding it is fixed

**Your 2.2.0 upgrade will not crash-loop. `FR-14` is not fixed.**

This release contains **no schema change** — nothing under `AINDY/db/models/` was touched, no
migration was added, schema contract stays `2026-08-15.1` and Alembic head stays `0016`. So
`bootstrap-schema` has no additive drift to refuse, and the bare entrypoint works.

**That is a property of this release, not a repair.** The next release that adds a runtime column
will reproduce exactly what you hit on 2.1.0. Both gates — the CLI and `serve`'s
`_enforce_schema_guard` — still default off, and the README still recommends the bare form.

We are telling you this explicitly because "the upgrade worked" is the observation most likely to
be mistaken for "the defect is gone."

---

## 7. Also open on our side

- **`IDEM-12`** (new) — `agent.undo` re-invokes **every** compensator if called twice; it never
  marks effects reversed. **Latent: zero compensators are registered today**, so the only present
  harm is duplicate audit rows. It goes live the moment anyone registers the first one.
- **`SYSMAX-5`** (new) — the scheduler thread pool is smaller than the job count: **~33 jobs on
  10 workers** in a realistic deployment (12 runtime + your 21 `register_scheduled_job` sites,
  all on the shared pool). Not implicated in any incident; filed because it is latent by
  construction. `FR-15` (b) gave the wait tick its own thread; the ratio itself is untouched.
- **`TOOL-SEAM-ISOLATION-1`**, **`EXEC-ENV-BIND-1`**, **`HTTP-SCOPE-GAP-1`** — unchanged.

**Next available FR number: `FR-16`.**
