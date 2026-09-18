---
title: "Upgrading aindy-runtime"
last_verified: "2026-09-18"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Upgrading `aindy-runtime`

**Start here if you run the runtime and are moving from one release to another.** This folder
holds one handoff per release — written the day the release was cut, against the published
wheel, for the team deploying it — and this page is the index that tells you which ones you need
to read and in what order.

`CHANGELOG.md` records *what changed*. A handoff records *what you have to do about it*: the
schema step if there is one, the exit code to expect, the consumer-visible change checked against
the app's own source, the knobs that shipped default-off, and a verification block to run
inside the container afterwards. They are point-in-time by design — a handoff for 2.5.0 describes
the world on 2026-08-19 and is not maintained after — so the table below carries the facts that
still matter across releases: **where the schema steps are.**

## How to use this page

1. Find your current version in the **Release** column of the table.
2. Every row between it and your target is a release you are crossing. **Read every row's
   *Schema step* cell** — the schema steps are cumulative, and skipping a release does not skip
   its step. A bare `aindy-runtime bootstrap-schema` exit `3` on a release that "has no schema
   change" means you owe a step from a release you jumped over.
3. Read the handoff for your **target** release in full, and the handoff for each release with a
   schema step or a breaking change between you and it.
4. Run the target handoff's verification block **in the container**, not from a source checkout
   — version numbers read from an interpreter are cwd-sensitive (`DEBT-COMPAT-1`), and the
   container is the only place the answer is unambiguous.

**The one rule that cost a live stack:** an additive runtime column makes a bare
`bootstrap-schema` exit `3`, and under `set -e` with `restart: unless-stopped` that is a crash
loop, not a warning (`FR-14`, 2.1.0). Read the exit code — `0` done, `3` re-run with
`--reconcile`, `4` stop and ask. `3` is the only one safe to automate.

## Release index

| Release | Date | Handoff | Schema step | Alembic head | Schema contract | Consumer pin moves? | What an upgrader must know |
|---|---|---|---|---|---|---|---|
| **1.11.0** | 2026-08-01 | [`APP_HANDOFF_v1.11.0.md`](./APP_HANDOFF_v1.11.0.md) | none | `0013` | — | — | Last 1.x. Adds `POST /auth/password/change`. |
| **2.0.0** | 2026-08-02 | [`APP_HANDOFF_v2.0.0.md`](./APP_HANDOFF_v2.0.0.md) | **yes** — `users.is_verified`, `users.verified_at` | `0014` | `2026-08-02` | **yes** — `>=2.0,<3.0`; a `<2.0` pin will not resolve | **Major.** Auth rework: 202-no-token on register, verification, recovery routes. **Read 2.0.1 too — the backfill this handoff promised did not run on wheel installs.** |
| **2.0.1** | 2026-08-05 | *(no handoff — see `CHANGELOG.md` §2.0.1)* | corrects 2.0.0's | `0014` | `2026-08-02` | no | Patch that fixes the 2.0.0 upgrade path itself: `--reconcile` now performs the verified-backfill on every install shape. If you are on 2.0.0 you are exposed. |
| **2.1.0** | 2026-08-15 | [`APP_HANDOFF_v2.1.0.md`](./APP_HANDOFF_v2.1.0.md) | **yes** — agents metadata + owner-scoped name | `0016` (`0015`, `0016`) | `2026-08-15.1` | no | **`bootstrap-schema --reconcile` required.** This is the release where a bare `bootstrap-schema` crash-looped a live stack (`FR-14`); the handoff was corrected after. |
| 2.2.0 | 2026-08-16 | [`APP_HANDOFF_v2.2.0.md`](./APP_HANDOFF_v2.2.0.md) | none | `0016` | `2026-08-15.1` | no | New `scheduler.queued` event. Scope enforcement explicitly **not** in this release (its closing section says so). |
| 2.3.0 | 2026-08-16 | [`APP_HANDOFF_v2.3.0.md`](./APP_HANDOFF_v2.3.0.md) | none | `0016` | `2026-08-15.1` | no | **`bootstrap-schema` gains branchable exit codes** (`0`/`3`/`4`); the `Upgrade Path Guard` ships. |
| 2.4.0 | 2026-08-17 | [`APP_HANDOFF_v2.4.0.md`](./APP_HANDOFF_v2.4.0.md) | none | `0016` | `2026-08-15.1` | no | Shipped with a `nodus-lang` pin already fixed on `main` — which is why 2.4.1 exists. |
| 2.4.1 | 2026-08-19 | [`APP_HANDOFF_v2.4.1.md`](./APP_HANDOFF_v2.4.1.md) | none | `0016` | `2026-08-15.1` | no | Dependency bumps incl. **`Mako` 1.4 (Python floor 3.10)**. Plain `pip install`. |
| **2.5.0** | 2026-08-19 | [`APP_HANDOFF_v2.5.0.md`](./APP_HANDOFF_v2.5.0.md) | **yes** — `execution_units` environment spec columns | `0017` | `2026-08-19` | no | **`bootstrap-schema --reconcile` required.** Three execution defaults flipped on (warm pool, idempotency gate, child-context clamp). |
| 2.6.0 | 2026-08-22 | [`APP_HANDOFF_v2.6.0.md`](./APP_HANDOFF_v2.6.0.md) | none | `0017` | `2026-08-19` | no | Plain `pip install`. |
| 2.7.0 | 2026-09-02 | [`APP_HANDOFF_v2.7.0.md`](./APP_HANDOFF_v2.7.0.md) | none | `0017` | `2026-08-19` | no | Async scheduler dispatch on by default (`AINDY_ASYNC_SCHEDULER_DISPATCH=0` to revert); three nodus-lang security fixes. |
| **2.8.0** | 2026-09-02 | [`APP_HANDOFF_v2.8.0.md`](./APP_HANDOFF_v2.8.0.md) | **yes** — `flow_runs.graph_signature` | `0018` | `2026-09-02.1` | no | **`bootstrap-schema --reconcile` required.** Suspended runs are quarantined on graph-shape mismatch (`FLOW-GRAPH-SIGNATURE-1`). |
| 2.9.0 | 2026-09-03 | [`APP_HANDOFF_v2.9.0.md`](./APP_HANDOFF_v2.9.0.md) | none *(but 2.8.0's if skipped)* | `0018` | `2026-09-02.1` | no | Syscall envelope gains `partial` / `unknown` outcomes (`EFFECT-PARTIAL-1`); a consumer branching on `== "error"` should read §2. |
| 2.10.0 | 2026-09-09 | *(no handoff — see `CHANGELOG.md` §2.10.0)* | none | `0018` | `2026-09-02.1` | no | `pydantic-core` no longer pinned by the package; DeepSeek client metered. Plain `pip install`. |
| 2.11.0 | 2026-09-10 | [`APP_HANDOFF_v2.11.0.md`](./APP_HANDOFF_v2.11.0.md) | none *(routes you by origin version)* | `0018` | `2026-09-10` — **bumped with no DDL** (a vocabulary edit under `db/models/`, `AGENT-EVENT-VOCAB-1`); `bootstrap-schema` still exits `0` | app floor `>=2.9.0` | **Read §0 if you are below 2.9.0** — it routes you through the steps you skipped. Records the cwd version trap that bit both repos. |
| 2.12.0 | 2026-09-12 | [`APP_HANDOFF_v2.12.0.md`](./APP_HANDOFF_v2.12.0.md) | none | `0018` | `2026-09-10` | app floor `>=2.11.0` | FR-23/25/26/27/28 intake; strict at-most-once opt-in (`AINDY_SYSCALL_IDEMPOTENCY_STRICT`). Pin bump + rebuild. |
| 2.13.0 | 2026-09-13 | [`APP_HANDOFF_v2.13.0.md`](./APP_HANDOFF_v2.13.0.md) | none | `0018` | `2026-09-10` | app floor `>=2.12.0` | `sys.v1.flow.run` can return `partial` for lenient-join fan-out (opt-in, the app declares none). Token governor and run-scoped quota knobs, all default-off. |
| 2.14.0 | 2026-09-14 | [`APP_HANDOFF_v2.14.0.md`](./APP_HANDOFF_v2.14.0.md) | none | `0018` | `2026-09-10` | app floor `>=2.13.0` | Four unflagged WAIT/resume + job-retry fixes from the live tutorial run (#654–#657): guest scripts receive resume payloads; per-run resume no longer fans out (event-bus message gains `run_id`); parked runs hold no concurrency slot; unregistered job handlers fail once. Pin bump + rebuild; run Tutorial 2 live as the verification. |
| 2.15.0 | 2026-09-14 | [`APP_HANDOFF_v2.15.0.md`](./APP_HANDOFF_v2.15.0.md) | none | `0018` | `2026-09-10` | no | Closes the app's FR-29 (`WAIT-DETECT-SHAPE-1`, #670): a request that reads or starts a waiting run no longer parks its own execution unit — `nodus/run`'s envelope says `success` + `data.status: WAITING`; an optional one-off `UPDATE execution_units …` retires the rows every prior release leaked. Grouped dependency bumps (#669). Pin bump + rebuild. |
| **2.20.0** | 2026-09-17 | [`APP_HANDOFF_v2.20.0.md`](./APP_HANDOFF_v2.20.0.md) | **yes** — `background_task_leases.fence` | **`0019`** | **`2026-09-16`** | no | **`bootstrap-schema --reconcile` required outside compose** (the app's entrypoint branches on exit 3 — this is the release that exercises it). All five app FRs against 2.19.0 shipped (FR-32 option 2, FR-33 `args_schema`, FR-34 `steps_completed` counts successes, FR-35 guest LLM usage reaches the parent, FR-36 hook `user_id` as `str`). **Deprecation with a date: `user.id` on `syscall.*` spans → `enduser.id`, removed next release.** `RETRY-CLASSIFY-1` (a cancelled/refused step attempted once, `failure_class` on every refusal), `SYSEVENT-RETENTION-1` (unset), `LEASE-FENCE-1`, `OTEL-GENAI-SEMCONV-1`, `EU-DOUBLE-FINALIZE-1` (past verify-failed units not backfilled). `Upgrade Path Guard` required. |
| 2.21.0 | 2026-09-18 | [`APP_HANDOFF_v2.21.0.md`](./APP_HANDOFF_v2.21.0.md) | none | `0019` | `2026-09-16` | no | Pin bump + rebuild. **★ Behaviour change: a pipeline route handler that raises is now ROLLED BACK before `execution.failed` (#721) — a write-then-raise-without-commit route used to keep the write; shared-session test suites must test 4xx routes on a private engine.** **`user.id` removed from `syscall.*` spans (dated in 2.20.0) → `enduser.id`.** `call_tool` gains an optional third argument; `replayed: true` on continued runs (#722). `EGRESS-INPROC-1` (flag-gated), `AUTHORITY-LIFETIME-1` (stale tokens refused `permission`), `AUDIT-CORRELATION-1` (additive event keys). DEC-046/047 accepted. |
| 2.19.0 | 2026-09-16 | [`APP_HANDOFF_v2.19.0.md`](./APP_HANDOFF_v2.19.0.md) | none | `0018` | `2026-09-10` | no | Fixes the runtime half of the app's `SYSCALL-SILENT-ERRORS-1` (#692): the system-state snapshot now counts agent runs — expect `active_runs` to rise. **Removes `sys.v1.agent.list_recent_durations`** (#693, DEC-022; app never called it). New `AINDY_NODUS_MAX_MEMORY_MB`, unset by default (#697). Plugin-host post-launch kill on every path (#694). Re-entrant `agent.undo` (#696). DEC-021: no `waiting → completed` edge — the app's paused-task hook keeps working; two truthful shapes offered. Pin bump + rebuild. |
| 2.18.0 | 2026-09-16 | [`APP_HANDOFF_v2.18.0.md`](./APP_HANDOFF_v2.18.0.md) | none | `0018` | `2026-09-10` | no | Closes the app's **FR-31** (`RESUME-FLOW-UNREGISTERED-1`, #689): a run parked across a restart resumes on the first wake of a fresh process; `agent_execution` resolvable for resume only (DEC-020). Cross-instance resume actually resumes the run (#686). **`resumed` on the resume route now means woken; `woken` added.** `await_event()` is the guest wait; `nodus_builtins` removed (#688). Pin bump + rebuild. |
| 2.17.0 | 2026-09-16 | [`APP_HANDOFF_v2.17.0.md`](./APP_HANDOFF_v2.17.0.md) | none | `0018` | `2026-09-10` | no | Six runtime items (#677–#682). **Removed** `ExecutionWaitSignal` (unused; a request EU can never `wait`). Three unflagged behaviour changes on live paths: the resume route no longer self-vetoes on a payload `correlation_id` key; it can 422 for a typed wait; cancelled runs stop sooner (dispatcher chokepoint; isolated worker killed). Opt-ins: named predicates (migrating an edge moves the digest once — drain first), typed wait payloads, the authority WAIT gate. Two asks of the app. Pin bump + rebuild. |
| 2.16.0 | 2026-09-15 | [`APP_HANDOFF_v2.16.0.md`](./APP_HANDOFF_v2.16.0.md) | none | `0018` | `2026-09-10` | no | Closes the app's FR-30 (`EU-FINALIZE-UNCOMMITTED-1`, #673): a request's `execution_units` row now actually reaches `completed`/`failed` — it never had, on any release; optional one-off `UPDATE` retires the `executing` route rows. FR-29 addendum: the boot-time `waiting_flow_runs` seed FK noise stops. Pin bump + rebuild. |

Bold rows carry a schema step. **Between any two releases, count the bold rows you cross —
that is how many times you owe `bootstrap-schema --reconcile`** (once is enough; it applies all
pending drift). Two releases (2.0.1, 2.10.0) shipped without a handoff; their `CHANGELOG.md`
sections are the record, and both were plain installs.

"Consumer pin moves?" is `recommended_runtime_requirement` as published on `/api/version`. It
has said `>=2.0,<3.0` since 2.0.0 — the *app's own* declared floor moved at 2.11.0 and after,
which is a different number and is the app's decision.

## Reading a handoff

Every handoff since 2.11.0 opens with **§0 — Which path are you on?**, a table keyed by the
version you are coming *from*. Earlier ones assume you are on the immediately preceding release;
if you are not, use this index to find the steps in between.

A handoff's verification block is written to be pasted into `docker exec`. The checks that
matter most, in every one of them:

```bash
# You are on the version you think, and it is the installed package, not a checkout
docker exec <api> python -c "import AINDY, importlib.metadata as m; print(m.version('aindy-runtime'), AINDY.__path__)"

# No schema drift on an existing database (exit 0). 3 = additive reconcile owed; 4 = stop.
docker exec <api> aindy-runtime bootstrap-schema; echo "exit $?"
```

## Writing a handoff — for the person cutting a release

One file per release, `APP_HANDOFF_v<major>.<minor>.<patch>.md`, in this folder, with the
five-key frontmatter (`Runtime Docs Validation` checks this folder too). Then **add the row
above** — the index is hand-maintained, and a release without a row is the same failure as a
release without a handoff. `docs/governance/RELEASE_CHECKLIST.md` §Release Notes Verification
carries the checklist: if runtime-owned schema changed, the handoff *says so and names the
step*; if a route started enforcing a scope, the handoff *names the scopes*; and say which
`Upgrade Path Guard` case applied — on a release with no schema change it passes trivially, so
"the guard was green" means different things.

Two handoffs were written and later found wrong in the field: 2.0.0 (the backfill did not run on
wheel installs — corrected by 2.0.1) and 2.1.0 (said *"nothing to backfill"* on the release that
crash-looped a live stack — corrected in place). **Neither was rewritten to hide the error.**
Correct a handoff with a dated note, or with the next release's handoff; the audit trail of what
was believed at release time is part of what these files are for.

## What is not here

- **[`docs/handoffs/`](../handoffs/README.md)** goes the other direction — requests *to* the
  Nodus repository, not upgrade guides for this one.
- **The app repository's own copies** (`RUNTIME_<version>_UPGRADE.md` in `aindy-apps-monolith`)
  are the app team's working versions of these; where a handoff here cites one, that is the
  app-side name for the same release.
- **Schema mechanics** — what `bootstrap-schema` does, `--reconcile`, the runtime's own Alembic
  version table — are `docs/runtime/SCHEMA_LIFECYCLE.md`. Cross-repo version policy is
  `docs/governance/CROSS_REPO_COMPATIBILITY.md`.
