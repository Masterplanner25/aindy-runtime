---
title: "App Handoff — Runtime v2.23.0"
api_version: "1.0"
last_verified: "2026-09-23"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.23.0

Pin bump `constraints.txt` `==2.22.0` → `==2.23.0` (your contract test moves the floor with it).

**Required of you: the pin and a rebuild. No schema step this time** (§1). Everything in this
release came from *running* the runtime rather than reading it — a distributed topology and a live
external channel — and two of the four items change behaviour you can observe (§2, §3). One new
opt-in flag, default off, with the measurement that justifies it (§4). Two asks (§6).

> ## ★ Confirm what you are actually running — in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.23.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```
>
> A version read outside the container, or without the path beside it, has told us the wrong
> thing twice. The path is the half that catches an editable install shadowing the wheel.

---

## 1. Schema: nothing to do

No change under `AINDY/db/models/` or to `memory_persistence.py`. Alembic head stays **`0020`**,
schema contract stays **`2026-09-20`**. `bootstrap-schema` exits 0; your entrypoint's exit-3
branch will not fire.

★ **What that means for the `Upgrade Path Guard` in our CI, stated because it is easy to
misread:** with no schema change the main job passes *trivially* — it installs the previous wheel,
builds its schema, runs this build's `bootstrap-schema` over it, and finds no drift because there
is none. The half that carries meaning on a release like this is its **negative control**, which
injects synthetic drift and requires exit 3. Both were green. "The guard was green" means the
negative control this time, not the main job.

---

## 2. FR-42 — the FK warning on every token mint is gone (#750, DEC-071)

**You will notice this if you read logs.** On 2.22.0, every `mint_token` for a run scope that is
not an `AgentRun` logged:

```
[CapabilityService] create_run_capability_mappings failed: (psycopg2.errors.ForeignKeyViolation)
insert or update on table "agent_capability_mappings" violates foreign key constraint
"agent_capability_mappings_agent_run_id_fkey"
```

The mint still succeeded — the failure was swallowed by a broad `except` — but the **capability
mapping audit row was silently absent** for every non-agent consumer.

Now: the run-scoped rows are written only when the scope really is an `AgentRun` (checked with a
query, not a guess); other scopes get the agent-type rows and nothing else, and the token itself
carries **`"mapping_recorded": true|false`** — outside the HMAC, so it is a report, not a claim
anything trusts.

**If you mint tokens for anything other than an agent run** (a session, a job, an external
consumer), read `mapping_recorded` when you care whether the audit trail exists. If you only ever
mint for agent runs, this is invisible to you except that the WARNING stops.

---

## 3. FR-15 — two silent losses in distributed mode (#751, DEC-072)

**Only relevant if you run `EXECUTION_MODE=distributed`.** In thread/single-instance mode nothing
here changes.

Both were found by building a topology that actually runs an api and a worker as separate
processes and watching one resume cross between them — the first time that evidence has existed.

1. **A follower api lost every woken resume.** Under distributed mode the background lease is
   contended; an api that does not hold it runs no scheduler heartbeat, so `schedule()` is never
   called in that process. It still registered waits and still answered
   `POST /platform/flows/runs/{id}/resume` — and put the woken resume into an in-memory queue
   nothing drained. The symptom on your side: **`woken: true` in the response and a run that stays
   `waiting` forever**, with no dispatch and no queue sample to explain it. Now the resume is
   forwarded straight to the dispatcher (onto the durable queue, for a worker). New counter
   **`aindy_scheduler_resume_forwarded_total`** — non-zero simply means some api in your fleet is
   not the leader, which is normal; it is there so the path is visible rather than inferred.
2. **A worker never opened its scheduler**, so every event it received was buffered and, at 1000,
   dropped (`[Scheduler] pre-rehydration buffer full`). A worker that became leader held
   rehydrated wait registrations nothing could wake. It now rehydrates and opens at startup, as
   the api's lifespan does.

**If you have ever seen a `waiting` run that a successful resume call never moved, that is this**,
and it is worth re-checking after the upgrade rather than assuming it was a one-off.

---

## 4. `AINDY_TOOL_IDEMPOTENCY_STRICT` — new, **default off** (#755, `IDEM-13`)

**Read this if any of your tools is registered `execution_guarantee="EXACTLY_ONCE"`.**

What was true before, and still is with the flag off: the tool-path effect gate deduplicates
**sequential** duplicates perfectly (a retry replays the first result and does not re-run the
tool), and under **concurrency** it deduplicates *nothing*. A `pending` row is not a claim, so
every concurrent caller of the same action is counted `degraded` and executes.

That is the documented contract, not a regression — but it had never been priced. Measured on a
live external channel: **five concurrent sends of one key delivered five real messages** against
one ledger row. Reproduced at 8-way contention: 8 of 8 callers ran the tool.

With `AINDY_TOOL_IDEMPOTENCY_STRICT=1` (PostgreSQL only), the same 8-way contention runs the tool
**once**, with 7 replays and `degraded 0`: losers block on an advisory lock keyed on the action,
then replay the winner's result.

- Wait ceiling `AINDY_TOOL_IDEMPOTENCY_STRICT_WAIT_SECONDS`, **default 60s**. A loser that waits
  the full ceiling degrades honestly and is counted `degraded_lock_timeout` — a *different*
  signal from contention: it means a tool is slower than the wait.
- A blocked loser holds one pooled connection while it waits. If you turn this on, size
  `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` for your duplicate fan-in.
- Across-process-crash exactly-once is **explicitly out of scope** (inherited from `FR-27`): a
  winner whose process dies leaves a `pending` row, and the next caller degrades past it until the
  staleness threshold. Stated rather than half-built.

**It ships off deliberately.** The mechanism is proven; which default is right needs a soak on
real traffic. If your workload has concurrent duplicates of an `EXACTLY_ONCE` tool, you are the
traffic that would settle it — see the ask in §6.

---

## 5. Dependency bumps (#749)

Seven dependabot PRs taken as one group: `anyio` 4.15.1, `joblib` 1.6.0, `pymongo` 4.18.1,
`urllib3` 2.8.0, `uvicorn` 0.53.0, the `cc` crate, `react-router-dom` 7.18.4. **`joblib` 1.6.0
adds a transitive `cloudpickle`**, now pinned at 3.1.2 in both pin files. `uvicorn` 0.53.0 is the
only one with server surface; the health / version / boot / routing / middleware / worker-pool
suites ran green under it.

★ **Still owed from 2.22.0 and now due:** declare `nltk` + `textstat` in your own `pyproject.toml`
(`PACK-DEBT-6`). Your search service imports both **undeclared**, relying on the runtime's pins.
The runtime drops them in the release after **2026-10-01**; four audit ignores and a dismissed
dependabot pair exist only for them.

---

## 6. Asks

1. **Re-run the manufactured denial on `nodus_vm`** (`AUTHORITY_NEGOTIATION_DESIGN.md` §9.6,
   step 5). Third release running. Half the evidence for the phase-3 default flip is in — your
   `leadgen.act` declares `on_denial="wait"` — and the first denial is still unobserved, so the
   flip stays unjudgeable.
2. **If you have concurrent duplicates of an `EXACTLY_ONCE` tool: run a window with
   `AINDY_TOOL_IDEMPOTENCY_STRICT=1`** and send back `aindy_effect_gate_outcomes_total` by label.
   On PostgreSQL with the flag on, a non-zero `degraded` means misconfiguration rather than
   contention, and that inversion is the reading we need. Zero `replayed` over the window means
   your traffic never contended and the window proved nothing — say so, that is a useful answer
   too.

---

## 7. What did not change

No syscall added or removed; no route contract change; no capability vocabulary change; the SPA
is unchanged. `AINDY_SYSCALL_IDEMPOTENCY_STRICT` (the syscall-path flag from `FR-27`) is
untouched — §4's flag is a **separate** one for the tool path, and neither affects the other.
