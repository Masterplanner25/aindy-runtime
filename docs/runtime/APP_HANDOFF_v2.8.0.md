---
title: "App Handoff — Runtime v2.8.0"
api_version: "1.0"
last_verified: "2026-09-03"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.8.0

> ## ★★ This one is not a plain `pip install`. Read §1 before you upgrade.
>
> **2.8.0 changes the schema.** 2.7.0 did not, and the two before it did — so please do not
> pattern-match off the last release. An existing deployment needs one extra step, and skipping
> it produces a **crash loop**, not a warning.

---

## 1. ★★ The schema step, and what happens if you skip it

One additive, nullable column: `flow_runs.graph_signature` (Alembic `0018`). Nothing to backfill,
no data to prepare, no downtime beyond the usual restart.

**But an additive runtime column makes a bare `bootstrap-schema` exit `3`.** Under `set -e` with
`restart: unless-stopped` that is a container that fails, restarts, fails again — a crash loop
that looks like a broken image rather than a missing flag. This is `FR-14`, and it took a live
stack down on 2.1.0 after a handoff said *"nothing to backfill and no data to prepare"* — which
was true **about data** and read to a deployer as "nothing to do".

So, explicitly:

```bash
aindy-runtime bootstrap-schema --reconcile
```

Or branch on the exit code, which is what an automated deploy should do:

| exit | meaning | safe to automate? |
|---|---|---|
| `0` | schema is current | — |
| **`3`** | **additive drift — this release** | **yes, re-run with `--reconcile`** |
| `4` | offline migration required | no — human |
| `5` | manual repair required | no — human |

**`3` is the only one safe to automate.** Do not blanket-retry with `--reconcile` on any non-zero
exit; 4 and 5 mean something a reconcile cannot fix.

---

## 2. What the schema change buys: a run can no longer resume into a changed flow

A suspended `FlowRun` was restored against whatever flow definition the process happened to hold
**at that moment**. Nothing recorded what the run was planned against, so a node renamed or an
edge rerouted between suspend and resume executed against a definition the run was never planned
for — **silently, and reported as success**.

A run now records a fingerprint of its flow's *shape* when it starts. On resume, a positive
mismatch **quarantines** the run — `status="dead_letter"` with a reason — rather than executing
it.

**What you will see if it fires:** a `FlowRun` in `dead_letter` whose `dead_letter_reason` begins
*"flow topology changed while run was suspended"*. That is the guard working. The run is
recoverable; the shape it was planned against is not coming back on its own.

**What it covers:** node identities and edge topology — the start node, terminal nodes, edge
sources, and each source's targets in order.

**What it deliberately does not cover:** node bodies, node configuration, and branch predicates.
A changed *predicate* that reroutes control flow is **not** caught. This detects a moved graph,
not a changed decision — the narrow version is the one that stays switched on, and a fingerprint
that moved on every deploy would quarantine every in-flight run and be disabled within a week.

**Nothing is quarantined by the upgrade itself.** Runs that predate the column have no
fingerprint, and an absent one means "cannot tell" and proceeds exactly as before.

---

## 3. Distributed deployments can opt in to async scheduler dispatch

`AINDY_ASYNC_SCHEDULER_DISPATCH` is no longer refused under `EXECUTION_MODE=distributed` — the
setting your production overlay uses. **It is opt-in there and does not take the default**, so
nothing changes unless you set it.

The refusal existed because a scheduler resume was a callback the distributed transport could not
carry, so enqueueing one produced a message no worker could resolve — and a worker treats an
unresolvable message as *completed*. A resume now crosses as two identifiers and is rebuilt at
the far end.

**If you want to try it,** set it on one deployment and watch two things:

```
aindy_execution_dispatch_total{mode="async"}   # should start moving
```

…and your dead-letter queue, which should stay flat. **A resume that cannot be rebuilt is now
dead-lettered by design** — so a DLQ entry there is a signal to read, not a silent loss.

**Why we are not defaulting it on:** a separate worker *process* has not been exercised. Every
defect found on this path lived at a process boundary, including the last one — a worker whose
flow registry was empty, which would have acknowledged every flow resume as complete. The
evidence covers the transport thoroughly and the process boundary not at all.

If you leave it alone, the `FR-15` serialisation you reported is still present in distributed
mode. We would rather say that than imply it is fixed.

---

## 4. Smaller things

- **`EffectRecord.status` gains `partial` and `unknown`.** No migration — the column already
  allowed them. `partial` means some units of a batched effect applied and some did not;
  `unknown` means dispatched with the outcome genuinely unobserved. **Nothing emits them yet**,
  so no record you read today will hold either value; the vocabulary landed first on purpose.
  The syscall envelope is unchanged and still `success | error`.
- **Thread-mode deployments** are unaffected by §3 — that half shipped in 2.7.0 and its default
  has not moved.
- **No dependency pins moved** this cycle, and no route began enforcing a new scope.

---

## 5. Verification

Full CI green on the release commit, including `Integration Tests (PostgreSQL + Redis)`,
`Platform UI Build`, `Runtime Package Build` and `Install Smoke Test`.

**`Upgrade Path Guard`: meaningful this time, and that is worth stating.** The guard installs the
previous released wheel, builds its schema, then runs this build's `bootstrap-schema` over that
database. On a release with no schema change it passes trivially — there is no drift to find, and
a broken guard is indistinguishable from a clean release. **2.8.0 contains real additive drift**,
so the guard exercised the condition it exists for. (For contrast, the 2.7.0 handoff said the
opposite, correctly.)

Sandbox escape gate: see the entry appended to `SANDBOX_ESCAPE_AUDIT.md` for this tag.
