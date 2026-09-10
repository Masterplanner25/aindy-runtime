---
title: "Proposal — Declaring the Guest Workflow Store (ORCHESTRATOR-SPLIT-1 store 4)"
api_version: "1.0"
last_verified: "2026-09-09"
status: current
owner: "platform-team"
---

# Proposal — declaring the guest workflow store

**Status: APPROVED 2026-09-09 — option B+C+D. Implemented in #612.**

*Kept as written, including the options not taken, because the reasoning is the record of why this posture and not another. The one thing implementation changed: the Docker image must create the state directory owned by `aindy` before the volume mounts over it — a named volume covering a path absent from the image is created `root:root`, and the non-root runtime cannot write to it. That would have surfaced on the first guest workflow rather than at boot.*

Written under `AGENT_WORKING_RULES` §8, which requires a proposal before a runtime behaviour
change. Choosing where guest run state is written, in what format, and whether an unowned
background thread runs against it is such a change; §5 additionally covers the thread.

Scope: `ORCHESTRATOR-SPLIT-1` store 4 only. This proposal **does not** attempt the split.

Measured against `nodus-lang` **5.13.0**, installed, 2026-09-09. Nodus-side defects found while
measuring are in `NODUS_HANDOFF_workflow_store_migration.md`; none of them is runtime work.

---

## 1. What is actually being decided

Not *"should we migrate the store"*. The decision is narrower and has a deadline:

> **At nodus 6.0.0 the default workflow store flips `LocalWorkflowStore` (JSON) →
> `SQLiteWorkflowStore`, and neither reads the other's records. The runtime has never expressed a
> preference, so today it inherits whatever the default is.**

**The failure at 6.0.0 is not choosing wrongly — it is having never chosen.** A declared value
that turns out to be the wrong one is a one-line change; an undeclared one changes underneath us
on a schedule we do not set.

---

## 2. Findings that shape the options

All measured, not inferred. Sources in §8.

**(a) ★★ Store 4 is write-only from the runtime's perspective.** The runtime creates records by
appending `run_workflow(<name>)` to the **guest script source**
(`nodus_execution_service.py:481`, `:1153`), so nodus's workflow framework runs *inside the guest
VM*. The host never calls `resume_workflow`, `expire_wait_timeouts`, `WorkflowFrameworkRunner` or
any store API — `AINDY/` contains **zero** references. Resume goes through the runtime's own
`PersistentFlowRunner.resume()` (`flow_engine/runner.py:262`), which is store 1.

**This is the finding that makes the decision cheap.** The 629 records have **no consumer**.
Losing all of them costs nothing today, so:

- **the migration is not needed**, and
- **the migration's truncated census — 432 of 629, silently — does not apply to us.** It is a real
  defect and a real hazard for a host that reads its store; it is not our exposure.

**(b) Growth is one record per nodus execution, and nothing reaps it.** Present census:
`agent_plan` 398, `agent_plan_seg0` 103, `build` 90, `agent_plan_seg1` 37, `agent_run` 1 = **629**.
`terminal_max_age_days` (default 30) bounds the *scan*, not the directory (`store.py:682`,
explicit). The only real ceiling is `max_terminal_runs` — **opt-in, off by default, and not
reachable through `create_workflow_store()`**; only `LocalWorkflowStore(…)` takes it, so setting it
requires constructing a store and injecting it.

**(c) The location is undeclared, and differs per environment.** The store roots at the worker
process's **CWD**, which no spawn path sets. In dev that is the repo root — where the 629 records
are, gitignored, so no review has ever seen them. In Docker it is `/home/aindy`, **which has no
compose volume**, so guest run state is container-ephemeral.

**(d) An unowned daemon thread runs, and the warm pool is what makes it real.** nodus auto-starts a
30-second sweep thread (`NODUS_WORKFLOW_AUTOSWEEP`, default on). With `AINDY_NODUS_WARM_POOL`
default-true, worker processes are long-lived, so the thread actually fires rather than dying with
a one-shot subprocess.

**★ Its blast radius is narrower than "a background thread we don't control" suggests, and the
precision matters.** `_auto_sweep_loop` calls `expire_wait_timeouts()` **without**
`release_schedules=True`. Per nodus's own `#733` comment it *"discards what it settles — it
dead-letters waits that ran out of patience and has no way to resume anything."* So it **can
terminally dead-letter a waiting guest record; it cannot resume or execute anything.** The full
`sweep()` that does resume requires a `vm_factory` and is never called here.

**(e) SQLite is built for the multi-process case.** `SQLiteWorkflowStore._connect`
(`store.py:1052`) sets `journal_mode=WAL`, `synchronous=NORMAL`, `timeout=5.0`, and reports
`coordination_mode: "sqlite"` against local's `"local_only"`. The obvious objection to moving —
that concurrent warm-pool workers would contend on one file — is answered by the implementation.
**Caveat: WAL is unsafe on network filesystems**, so the chosen path must be a real local volume,
not a CIFS/NFS bind mount.

---

## 3. Options

| | Option | Cost | What it fixes | What it leaves |
|---|---|---|---|---|
| **A** | Pin `NODUS_WORKFLOW_STORE_BACKEND=local` | one env var | the 6.0.0 flip | growth, location, thread |
| **B** | Pin `…=sqlite` | one env var | the flip; N files → 1 | location, thread |
| **C** | Declare `NODUS_RUN_STATE_ROOT` | one env var + a volume | location, ephemerality | flip, growth, thread |
| **D** | `NODUS_WORKFLOW_AUTOSWEEP=0` | one env var | the unowned thread | flip, growth, location |
| **E** | Implement `WorkflowStore` over Postgres | real work | all of it — collapses 4 into 1 | — |

A–D are independent and compose. **E is the durable answer and is not proposed here** (see §7).

**★ Note C uses `NODUS_RUN_STATE_ROOT`, not `NODUS_WORKFLOW_STORE_ROOT`.** The latter is what
`ORCHESTRATOR-SPLIT-1` used to recommend and it is the wrong variable: it moves only the record
half, leaving `.nodus/graphs/` behind, and nodus documents it as *"the narrower, legacy gesture"*.
It is worse than incomplete — the Floor identified runtime-owned state by a literal `.nodus` path
segment, so relocating with it moved the store **out of the Floor's reach** (nodus demonstrates a
guest `fs.write("../relocated/pwned.txt", …)` succeeding when relocated, denied at the default).
Closed upstream by `run_state_roots()`, but the variable choice still matters.

---

## 4. Recommendation

**B + C + D, as one declared posture.** Rationale, in the order the evidence supports:

1. **B over A.** Both remove the deadline for the same one-line cost, so the tiebreaker is
   everything else. SQLite is where 6.0.0 goes, so B is the position we would eventually take
   anyway and taking it now means discovering any problem on our schedule rather than on the
   upgrade's. It replaces N tiny files with one — nodus `#380` records hundreds of run files
   slowing later runs, which is the shape we already have at 629. And per §2(a), **the 629
   existing records have no consumer, so B abandons nothing of value** and needs no migration.
2. **C, with a real volume.** Declared location is the half of the problem that is purely a loss:
   state written where nobody declared and nothing preserves it. Note this makes the store
   *durable* in Docker for the first time, which is a behaviour change in its own right and the
   reason it is in a proposal rather than a bug fix.
3. **D, and this is the one worth arguing about.** The thread cannot resume — but it *can*
   terminally dead-letter a waiting guest record, and the runtime neither knows nor is told. Since
   the runtime does not read the store, nothing downstream currently depends on the outcome either
   way; disabling it removes an unowned actor from a store we are about to declare ownership of.
   **If B+C land and D does not, we would be preserving records durably while an unowned thread
   mutates them** — the worst of both.

**Do A instead of B if** the priority is strictly minimum change before 6.0.0 and the appetite for
touching guest execution is zero. A is a legitimate answer and nodus explicitly treats
`=local` as *"a host saying I know, I want the JSON store."*

---

## 5. Impact on `INVARIANTS.md`

**No invariant is engaged.** Reviewed against `docs/platform/governance/INVARIANTS.md`: the
invariants cover DB configuration (1, 2, 2.1, 17), the durable ledger (2.2), the memory graph
(4–14, 27, 28), auth and rate limiting (21–23), and the startup schema guard (29). **None
references the guest workflow store, and none can — the store is outside Postgres and outside the
runtime's own durability vocabulary.**

That absence is itself worth recording: **a durability layer the invariants cannot describe is
exactly what `ORCHESTRATOR-SPLIT-1` is about.** This proposal does not close that; it declares the
layer's configuration so a future ownership contract has something stable to describe.

The nearest adjacent invariant is **(2.2) required `SystemEvent` emission fails closed**. Store 4
writes emit no `SystemEvent` and are not part of the ledger; nothing here changes that.

---

## 6. Migration and API contract implications

**Migration: none, and this is the substantive claim to check.** Because store 4 has no host-side
consumer (§2(a)), moving to SQLite abandons 629 records that nothing reads. **We should
deliberately NOT run `nodus workflow migrate-store`** — it would carry 432 of 629 while reporting
success, and buy us records we do not use. Declining to migrate is a decision, and should be
recorded as one rather than looking like an oversight.

**API contract: none.** No route, syscall, response shape, envelope field or schema changes. No
Alembic migration; no `SCHEMA_CONTRACT_VERSION` bump — nothing under `AINDY/db/models/` or
`memory_persistence.py` is touched.

**Changelog: yes.** Env vars and behaviour both change under the CHANGELOG protocol, and C makes
guest run state durable in Docker where it was ephemeral — an operator must read that before
upgrading, so it belongs at the top of `Unreleased` (a `00-` prefixed `changelog.d/` file).

**Deployment:** C requires a compose volume. Values must be set where the **worker process** reads
them, not only the API container.

---

## 7. What is explicitly NOT proposed

- **Option E (a `WorkflowStore` over Postgres).** It is the durable answer, it is bounded work
  against a small abstract surface (`runner.py:492` takes an injected store), and it would collapse
  store 4 into store 1 — removing a durability layer rather than configuring one. It wants its own
  proposal, and `ORCHESTRATOR-SPLIT-1`'s ordering still says **(b) publish the ownership contract
  first**. Nothing here pre-empts that; declaring the configuration makes the contract easier to
  write, not unnecessary.
- **Any change to how guests execute.** No change to the warm pool, `nodus_worker.py`,
  confinement, or `allowed_paths`.
- **Deleting the 629 local records.** Out of scope, and they are one of the two pieces of evidence
  this entry has.

---

## 8. Verification before and after

**Before**, to confirm the premise rather than assume it:

```bash
grep -rn "nodus_lang_workflow\|WorkflowStore\|resume_workflow" AINDY/   # expect: zero
```

**After**, and this is the part that must not be skipped — a declared value that nothing reads is
`G4a`'s shape, and this repo already has one of those:

1. **Assert the mechanism, not the setting.** Run a guest workflow and confirm `store_info()`
   reports `backend: "sqlite"` and the declared path — do not infer it from the env var being set.
   This is the `TEST_MODE`-above-the-decision lesson: prove the branch you think you configured is
   the one running.
2. **Confirm the records land in the declared root**, and that `.nodus/` no longer appears at the
   worker's CWD.
3. **Confirm the sweep thread is absent** with D — by enumerating threads in a warm-pool worker,
   not by reading the env var back.
4. **Verify in a container**, not only on this box. The CWD differs (`/home/aindy` vs the repo
   root), which is the whole reason (c) exists.

### Sources

| Claim | Where |
|---|---|
| guest-side invocation | `AINDY/runtime/nodus_execution_service.py:481`, `:1153` |
| host resume path (store 1) | `AINDY/runtime/flow_engine/runner.py:262` |
| store injection point | `nodus_lang_workflow/runner.py:492` — **not `:437`, which is the line `TECH_DEBT.md` cites from nodus v5.0.4-2; it moved by 5.13.0** |
| scan bounds the scan, not the directory | `nodus_lang_workflow/store.py:682` |
| auto-sweep cannot resume | `nodus_lang_workflow/runner.py:54`, comment `#733` |
| SQLite WAL / concurrency | `nodus_lang_workflow/store.py:1052` |
| root resolution, legacy variable | `nodus/runtime/state_paths.py` |
