---
title: "App Handoff — aindy-runtime v2.3.0"
api_version: "1.0"
last_verified: "2026-08-16"
status: current
owner: "platform-team"
---
# App Handoff — `aindy-runtime` v2.3.0

Release commit and PyPI publication confirmed in §7. **Minor, arrives passively — but it
contains one behaviour change that lands on every signed-in session.** Supersedes
`APP_HANDOFF_v2.2.0.md`.

**The short version: `nodus-lang==4.2.0` is now reachable (your `FR-16`), and §6 is the list you
asked for — every runtime flag that is waiting on a soak, with an honest readiness call on each.**

---

## 1. The one behaviour change

**A JWT session is no longer exempt from scope checks** (`HTTP-SCOPE-GAP-1`).

`enforce_api_key_scope` gated API-key callers only — its docstring said *"JWT users carry full
trust and are never gated"* — so **an interactive browser session was strictly more privileged
than any API key**. Built from the scope surface you supplied, so this should be a no-op for you:

| Class | Scopes |
|---|---|
| Ordinary session | `flow.read`, `flow.execute`, `memory.read`, `memory.write`, `agent.run`, `execution.read` |
| Admin session | the above **+** `webhook.manage`, `platform.admin` |

**Nothing is encoded in the token — authority is derived from `User.is_admin` per request.** So
no session is invalidated by this upgrade (unlike 2.0.0's `purpose` claim), and a promotion or
demotion takes effect on the next call rather than the next login.

**Why we shipped it enforcing rather than default-off:** only **7 of 147** route decorators
enforce a scope at all, and the only three they require are in the ordinary set — so every
signed-in user still passes every enforcing route. A test scans our source and fails if anyone
adds an enforcement an ordinary session cannot satisfy, so you should never meet this as
scattered 403s.

Escape hatch if we got it wrong: `AINDY_JWT_SCOPE_ENFORCEMENT=0`.

Both your constraints are honoured: admin keys on the **existing user-row flag**, and nothing
here pretends to answer data ownership — `execution.read` still means *may I read executions*,
not *whose*. That distinction remains open.

---

## 2. `FR-16` — nodus-lang 4.2.0, the thing you were blocked on

**Shipped.** `Requires-Dist: nodus-lang==4.2.0`.

The pin stays **exact** — hard-pinning a language runtime is defensible — which is exactly why
bumping promptly is our obligation rather than your problem. We reproduced your block: an
editable install of the runtime *downgraded* 4.2.0 back to 4.1.0.

Risk-probed before landing, and one check was new since the last bump: **`GUEST-CONFINE-1` makes
guest confinement depend on VM constructor arguments**, so a silently renamed argument would
leave the guest unconfined while every VM-mocking test still passed. Verified against the real
VM — all three flags present with identical defaults, all 31 gated builtins still refused.

Your assessment of 4.2.0's breaking change was right for our side too: nodus errors are
forwarded, never parsed.

**Your `#376` question stands unanswered and we did not answer it.** You said the signatures
match `run_reasoning_apply` returning `{'data': {}}` but did not claim causation. We have not
re-run that path; worth doing now that 4.2.0 is reachable.

---

## 3. `FR-14` — partially closed, and the honest half

**`bootstrap-schema` now exits with branchable codes**, so an entrypoint can act instead of
crash-looping:

| Exit | Meaning | Safe to automate? |
|---|---|---|
| `0` | success | — |
| `1` | configuration error | no — fix the environment |
| `2` | database layer import failure | no |
| **`3`** | **additive reconcile required** | **yes** — `--reconcile` adds, never drops |
| `4` | offline migration required | no |
| `5` | manual repair required | no |

`1` and `2` deliberately keep their meanings — the value of 3/4/5 is that they are *not* `1`.
When a report indicates both, **`4` wins over `3`**, so an entrypoint never auto-reconciles a
database that needs a person. `--help` now says a bare call under `set -e` is a crash loop in a
container.

**The half that prevents recurrence also shipped:** a CI job installs the **previous released
wheel**, builds its schema, installs the new build over that database, and requires
`bootstrap-schema` to succeed or exit 3. That is the state no other job reached — the one your
own `deploy-bootstrap-guard.yml` could never see, for the reason you identified.

**★ It passed trivially on this release**, because 2.3.0 has no schema change. On such a release
a broken guard and a clean release are indistinguishable, so the workflow ships with a
**negative-control job** that injects synthetic drift and requires detection. Verified from the
logs, not the green tick: *"guard detected the injected drift as exit 3."* **The control is the
half that carried meaning here.**

---

## 4. Also in this release

- **`SYSMAX-5`** — the scheduler ran ~33 jobs against one pool of 10 workers. Recovery jobs now
  have their own lane, so a saturated scheduler can still reconnect a dead queue backend and
  recover stuck runs. New metric `aindy_scheduler_job_starved_total{job_id,reason}`; saturation
  was previously a log line only.
- **`bootstrap-schema` exit codes** (§3) and the **upgrade-path guard**.
- **`AUTHORITY-VALUE-1`** — `child_context` could *widen* authority. Clamp shipped **opt-in**;
  see §6, where it is the one flag you must **not** turn on yet.
- Internal: changelog entries moved to `changelog.d/` fragments; a vendored-test-shim audit that
  found real `remove_job` faults being swallowed.

---

## 5. New settings

| Name | Default | What it does |
|---|---|---|
| `AINDY_JWT_SCOPE_ENFORCEMENT` | **on** | §1. Set `0` to restore the old bypass — a hatch, not an opt-in. |
| `AINDY_SCHEDULER_QUEUE_EVENTS` | **on** | Emit `scheduler.queued`. Set `false` if the volume is unwelcome. |
| `AINDY_CHILD_CONTEXT_CLAMP` | off | §6 — **do not enable yet.** |

---

## 6. ★ Runtime flags waiting on a soak — the list you asked for

**None of this is urgent.** It is here so you can pick up whichever are cheap on your side and
start moving our soaks along. Every one of these is a capability the runtime built, shipped
default-off, and cannot advance without real traffic — the standing split puts that traffic in
your repo, not ours.

Readiness is our honest call, not a request.

### Ready now — low risk, degrade safely

| Flag | Turns on | Why it is safe |
|---|---|---|
| `AINDY_NODUS_WARM_POOL` | Warm Nodus worker pool (`NODUS-WARMPOOL-1`) | Falls back to a fresh subprocess on **any** fault, so worst case is today's behaviour. Biggest latency win available to you — cold start is ~12s on a 17-app profile. |
| `AINDY_NODUS_WARM_PREWARM` | Pays plugin load before traffic | Only meaningful with the pool on. Same fallback. |
| `AINDY_MEMORY_RECALL_OWN_SESSION` | Recall uses its own short-lived DB session (`DB-NODUS-BUDGET-1`) | Falls back to the caller's session on failure. Removes a real connection-hold on the node path. |
| `AINDY_PLANNER_MEMORY_INJECTION` | Recall feeds planning, emits `RECALL_USED` (`INFINITY` Gap 1) | Additive signal; no execution-path change. |
| `AINDY_ASYNC_JOB_LOOP_CLOSURE` | Async jobs emit loop-closure signal (`INFINITY` Gap 5) | Additive events only. |
| `AINDY_DELEGATION_HANDSHAKE` | Delegate accept/reject (`RTR-4b`) | Only affects delegated runs; inert if you do not delegate. |
| `AINDY_DELEGATION_PRIVATE_MEMORY` | Token-scoped delegate memory (`RTR-4c`) | Same scope. **Gotcha:** delegate writes take the *deferred* capture path, so `MemoryNodeDAO.save` is the chokepoint, not the syscall. |

### Ready, but changes execution shape — worth a deliberate window

| Flag | Turns on | What to watch |
|---|---|---|
| **`AINDY_ASYNC_HEAVY_EXECUTION`** | **`FR-15` (a)** — threaded dispatch for `flow`/`agent`/`nodus`/`job` | **The one that fixes your 177s queue.** Makes the async path live; without it dispatch is INLINE behind a 1s tick. `scheduler.queued` (shipped 2.2.0) makes the before/after measurable, which is why we shipped observability first. |
| `AINDY_DURABLE_CONTINUATION` | Re-drive crashed flows/agents at startup (`ECOGAP-1`) | Per-flow and per-agent-type opt-in *inside* the flag, so exposure is yours to widen. |
| `AINDY_DURABLE_STEP_GRANULARITY` | Continuation resumes per step, not per segment | Only meaningful with continuation on. |
| `AINDY_AGENT_EXECUTION_BACKEND=nodus_vm` | VM-backed agent execution (`RTR-1`) | Our largest untested-in-anger path. Pending exactly this soak. |

### Ready, but they change *effect* semantics — read first

| Flag | Turns on | Why it needs thought |
|---|---|---|
| `AINDY_SYSCALL_IDEMPOTENCY` | At-most-once for `EXACTLY_ONCE` syscalls (`IDEM-11`) | **Now meaningful in a way it was not before 2.3.0:** the per-syscall audit took declarations from 1 to 7 (`event.emit`, `flow.run`, `flow.execute_intent`, `nodus.execute`, `job.submit`, `agent.undo`, `memory.write`). Flipping this makes retries of those *replay* rather than re-execute. |
| `AINDY_TOOL_IDEMPOTENCY` | At-most-once at the tool seam (`MEB-0`) | Same shape, tool path. Pairs naturally with the above. |
| `AINDY_NEXT_ACTION_ACTING` | Runtime acts on an app-sourced `trigger_execution` (Deliverable C) | Bounded (chain-depth cap, approval gate reused), but it *starts runs*. Turn on last. |
| `AINDY_AUTONOMOUS_EXECUTE_WINDOW` | Autonomous execute window (`RTR-5`) | Same class — bounded, but autonomous. |

### Do NOT enable yet

| Flag | Why not |
|---|---|
| **`AINDY_CHILD_CONTEXT_CLAMP`** | **It will break your automation syscalls.** `_dispatch_owner_syscall` (`apps/automation/syscalls/syscall_handlers.py:45`) builds a child granting the *nested* syscall's capability, while the parent context holds **only the outer** syscall's capability. Clamping intersects to the empty set and denies a call that works today. This is ours to solve — the caller needs a legitimate grant first. A widening currently logs a WARNING either way, so **your logs will tell us how often this actually happens**, which is the measurement we are missing. |
| `AINDY_EGRESS_ENFORCEMENT` | Vacuous until a `CapabilityPolicy` is registered, and its own docstring names two bypasses. Not worth soaking yet. |
| `AINDY_REQUIRE_SIGNED_PLUGINS` | Requires signed bundles you do not produce. |

**If you only do one:** `AINDY_ASYNC_HEAVY_EXECUTION`. It is the remaining half of your own
`FR-15`, you already control it, and 2.2.0 shipped the observability that makes the result
legible.

**If you want the cheapest win:** `AINDY_NODUS_WARM_POOL` — pure latency, fails back to current
behaviour.

---

## 7. Release facts

| | |
|---|---|
| Version | `aindy-runtime==2.3.0` |
| Schema contract | `2026-08-15.1` — unchanged |
| Alembic head | `0016` — unchanged, no new revisions |
| Consumer pin | unchanged (`>=2.0,<3.0`) |
| `nodus-lang` | **4.2.0** |

**No schema work on upgrade**, verified by diffing `AINDY/db/models/` and `memory_persistence.py`
against `v2.2.0` rather than assumed. `bootstrap-schema` has no additive drift to refuse on this
release — but that is a property of *this* release, not a repair of `FR-14`'s remaining half.

---

## 8. Still open on our side

- **`IDEM-12`** — `agent.undo` re-invokes **every** compensator if called twice. Latent: zero
  compensators are registered, so today the only harm is duplicate audit rows. It goes live the
  moment anyone registers the first one.
- **`AUTHORITY-VALUE-1`** — beyond the clamp: `SyscallContext.capabilities` is still a
  caller-constructible list, and absent identity still *skips* the boundary rather than denying.
- **`HTTP-SCOPE-GAP-1` remainder — the larger half.** 140 of 147 routes still enforce nothing,
  and `memory_router.py` still reaches effects with zero dispatcher references. When we widen
  enforcement, that release's handoff will **name the scopes**, per your request.
- **`TOOL-SEAM-ISOLATION-1`**, **`EXEC-ENV-BIND-1`**, **`FR-6` items 2+3**, **`FR-14`'s
  entrypoint-pattern half.**

**Next available FR number: `FR-17`.**
