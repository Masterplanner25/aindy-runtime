# Technical Debt

## DECISIONS-2026-08-01 — open questions answered by the owner

Seven questions had accumulated across the FR-6 build, the v1.11.0 release, and the dependabot
triage. All answered 2026-08-01. Recorded here because they were made in conversation and would
otherwise be lost; each links to the entry that owns the work.

**Current phase context (matters for reading everything below):** the runtime is in a
**testing** phase. Things get connected to it in order to exercise it, and that is where the
app-side feature requests keep originating — they are a *symptom of the testing method*, not
scope creep. Consequence: **flag soak happens in the apps-monolith, not here.** The runtime
ships capabilities default-off; the app repo is where they get turned on and lived with.

| # | Question | Decision | Owner entry |
|---|---|---|---|
| 1 | Semver for the breaking register change | **Follow semver — it gates a major.** Release not imminent. | this entry |
| 2 | FR-6 email channel ownership | **Option 3 — hybrid** | `APP-FR-*` → FR-6 |
| 3 | `/auth/register` 409 enumeration oracle | **Fix it** | `APP-FR-*` → FR-6 |
| 4 | Cargo build job in CI | **Add it** | `NATIVE-CI-1` |
| 5 | cryptography 48→49 (#302) | **Verify before merging** — done; now at **50.0.0** for CVE-2026-69247 | `PACK-DEBT-2` |
| 6 | UI major cluster + `@aindy/ui-kit` peer range | **Deferred** — resolved 2026-08-03: ui-kit 2.0.0 widened the peer, cluster landed (#345, #349) | `DEP-UPGRADE-DEFERRED-1` |
| 7 | Soak-and-flip the default-off flags | **Handled app-side** | see phase note above |

### 1 — Semver: the register password floor gates a major

`register_user` rejecting short passwords is a behavioural break to a public endpoint, so by
semver it belongs in a **major**, not a minor. **Decision: follow semver as always.**

This is load-bearing beyond convention. `runtime_compatibility.py:11` `_major_series()` computes
`recommended_runtime_requirement` as `>={major}.0,<{major+1}.0`, so the runtime **actively tells
consumers that anything inside the current major is safe**. Shipping the register change as
1.12.0 would make that self-reported claim untrue.

**Practical state:** the change is merged to `main` and sits under `## Unreleased`. **The next
release must therefore be `2.0.0`** — or the change must be pulled before any 1.x release. No
release is imminent, so there is time for other breaking work to ride along.

**Cross-repo consequence to remember:** when 2.0.0 ships, `recommended_runtime_requirement` flips
to `>=2.0,<3.0`, and the apps-monolith floor (`aindy-runtime>=1.11.0,<2.0`) will *exclude* it.
The app team has to move their pin deliberately — this will not upgrade itself.

### 2 — FR-6 email: hybrid

Dispatch a registered `email` connector when one exists; otherwise fall back to runtime-owned
SMTP config. Satisfies "the runtime sends it" without making a runtime auth flow depend on an
app registering something, so password reset still works in a `platform-only` deployment.

### 3 — `/auth/register` enumeration oracle: fix

Guarding `/forgot` against enumeration while leaking the same fact on register is incoherent.
Accepted that this changes a long-standing public response contract and can break clients that
branch on 409.

**⚠️ Scoping finding (2026-08-01, before any code): this CANNOT be fixed standalone. It is a
dependent of decision 2 (FR-6 hybrid email), not independent work.**

**Why.** `POST /auth/register` returns an **access token** on success. A duplicate email cannot
be given a token — that would be account takeover — so the two responses *must* differ. No
choice of status code or message closes the oracle while registration also authenticates the
caller in the same request. Uniform-response hardening is impossible here, unlike `/forgot`,
where "always 200" works precisely *because* `/forgot` returns nothing of value.

**What an actual fix requires** — the standard non-enumerable registration shape:

1. `/auth/register` always returns a neutral `202` ("check your email"), issuing **no** token;
2. the token is issued only after the emailed verification link is followed;
3. a duplicate email gets a *"someone tried to register with your address"* mail instead — same
   neutral 202 to the caller.

That is an **email-verification flow**, which needs the email channel decision 2 settles
(hybrid) and does not yet exist. Verified 2026-08-01: the `User` model has no `is_verified`,
`email_verified`, or `verification_token` — there is no verification concept in the runtime at
all.

**Rate limiting does not mitigate it.** Register is `10/minute`, but targeted enumeration
("is `alice@corp.com` registered?") is a *single* request. Throttling only raises the cost of
bulk sweeps, not the attack that matters.

**Implementation note for whoever builds it — there is a second channel.** The duplicate-email
path returns **before** `hash_password`, so it skips bcrypt and answers measurably faster. A
status-code-only fix would leave that timing oracle intact. The fix must also equalise work on
both paths (hash regardless, or defer the existence check until after hashing).

**Sequencing consequence:** build decision 2 first and fold this into it. Attempting #3 alone
can only produce cosmetic changes that leave the oracle open while appearing to close it —
which is worse than the current honest 409.

### 4 — Cargo CI job: add

`NATIVE-CI-1` is the binding constraint on three standing PRs (#292 uuid, #296 serde, #306 cc)
that re-accumulate monthly. A build job converts them from "needs a local MSVC build" into
ordinary merges.

### 5 — cryptography 48→49: verify first — *superseded 2026-08-04, now at 50.0.0*

Green CI including `pip-audit` is the weakest form of evidence for a crypto major under an auth
stack. Verify `python-jose` / `passlib` / `bcrypt` interop before merging #302.

**Update:** 49.0.0 landed, and then **CVE-2026-69247 / GHSA-g6cj-pr64-35w5** was disclosed
against it — a Bleichenbacher oracle in `pkcs7_decrypt_der/pem/smime`, introduced in 44.0.0,
fixed in **50.0.0**. Bumped to 50.0.0 rather than exempted: an exemption is for findings with
no fix released (which is what all four existing `--ignore-vuln` entries are), not for one
patched upstream. **Not reachable here** — the only `cryptography` consumer in the tree is
`platform_layer/extension_signing.py`, which uses Ed25519 signing and `serialization`; there
is no PKCS7 or S/MIME call anywhere under `AINDY/`, and JWT signing is HS256. The interop
condition above still applied and was checked the same way: `pip install --dry-run` with
`python-jose[cryptography]` and `passlib[bcrypt]` resolves with cryptography 50.0.0 as the
only change, and the auth suites in `Runtime Contracts` + `Integration Tests` exercise the
actual sign/verify paths.

### 6 — UI cluster: deferred, decided from the other repo

`@aindy/ui-kit` pins react-router 6, so its peer range must widen and publish before #312/#324
can land. Same owner, different repo (`C:\dev\aindy-ui-kit`) — to be worked from there. Note
**#324 supersedes #312** (7.18.2 vs 7.0.0); one should close when the cluster is taken up.

## RT-MEMTXN-LEAK-1 — memory reads held DB connections across the embedding API call

**Status:** **FIXED in three parts.** Accepts app handoff `RT-MEMTXN-LEAK-1` (apps-monolith,
HIGH — a browser login took ~40s and exceeded the web client's 30s timeout, so a real user
could not sign in). **Part 1 (recall read path) shipped in v1.10.0** — app-verified as a
*partial* fix: leaked connections now drain at request end (`idle in transaction` falls back
to ~2 after login) instead of lingering to the 120s reaper. **Part 2 (embedding-job
commit-before-embed) shipped in v1.10.1** — also insufficient on its own. **Part 3 (the
capture→job→capture cascade) fixed 2026-07-19 — this is the actual root cause.**
Locally reproduced and verified end-to-end: **login 43.6s → 0.3s, 60 held connections → 0.**

**Part 3 — the cascade (root cause).** Parts 1 and 2 were both real transaction-hold bugs, but
they were treating symptoms: the *reason* dozens of connections existed at all is an **unbounded
synchronous recursion**. Diagnosed with a `py-spy` stack dump against the live container (the
first artifact that showed the Python side; `pg_stat_activity` alone could not distinguish
"held across a slow call" from "held by a stack frame that never returns"):

```
submit_async_job                     (async_job_service.py:522)   ← opens its own SessionLocal()
 └ _emit_async_system_event                  EXECUTION_STARTED
    └ emit_system_event
       └ capture_system_event_as_memory      (EXECUTION_* is auto-captured)
          └ MemoryNodeDAO.save               commit + refresh  ← the held SELECT
             └ _enqueue_embedding            every new node needs an embedding
                └ dispatch_job → submit_async_job   ← RECURSES, one level deeper
```

Every memory node spawns an async job; that job's lifecycle event becomes another memory node.
The recursion is **synchronous**, and each level holds the session it opened until the descent
below it returns — so depth is capped only by the connection pool. The observed fingerprint
falls straight out of this: 60 connections (= pool ceiling), each with **exactly one** statement
(`SELECT … FROM memory_nodes WHERE id = <uuid>` — the `save()` refresh at that level), 60
**distinct** ids, `xact_age_s == idle_s`, all `embedding_status='pending'` (they never got
embedded — the stack was still descending). Once drained, every further checkout waits the full
`pool_timeout` → ~42s login. It also explains the corpus: 1239 of 1246 nodes were global rows
reading `"execution.started from async"` — pure cascade debris, 60–120 per minute.

**Three compounding defects, each fixed:**

1. **The cycle existed.** `capture_system_event_as_memory` now drops events whose
   `payload["task_name"]` is in `RUNTIME_INTERNAL_TASK_NAMES` (`memory.generate_embedding`,
   `memory.embedding_sweep`). A "the embedding job started" memory has no recall value, and
   capturing it is precisely what closed the loop. This cuts the cycle at its origin. (It also
   covers the second entry point: `feedback.abandonment_detected` is auto-captured *and* carried
   the same `task_name`.)
2. **Nothing bounded the nesting.** New `AINDY/core/memory_capture_guard.py`:
   `submit_async_job` runs inside `async_submit_scope()`, and captures are suppressed at
   submission depth ≥ 2. The outermost submission still captures, so loop-closure signal
   (INFINITY-RUNTIME-1) is preserved; only the nested submission a capture itself spawned is
   dropped. `_execute_job` resets the depth via `fresh_async_submit_depth()` — the worker thread
   inherits the submitter's context through `copy_context()`, and a thread hand-off ends the
   synchronous chain, so an executing job that legitimately chains further work is unaffected.
3. **Dedup could never fire.** `_is_duplicate` used `WHERE user_id = :uid`, which is never true
   when `uid IS NULL` — so the *global* nodes this cascade produced, all with byte-identical
   content, were never deduplicated. Now branches to `user_id IS NULL` (branch rather than
   `IS NOT DISTINCT FROM`: unsupported on SQLite, and it would leave the NULL bind untyped on
   PostgreSQL). Working dedup would independently have capped the cascade at one node.

Tests: `tests/unit/test_memory_capture_cascade.py` (11 — origin cut, depth semantics incl.
scope restoration on exception, the thread-boundary reset, and both dedup branches).

> **Rule: a memory capture must never be able to enqueue work whose own lifecycle events are
> capturable.** Any capture → job → capture edge is a cycle; the runtime's own maintenance jobs
> must stay invisible to capture, and nesting must be depth-bounded regardless.

**Debris cleanup.** Deployments that ran an affected version carry a body of memory nodes that
record nothing but the cascade (on one real stack, 1,912 of 1,970). They are inert once the cycle
is cut, but they pad every recall candidate set and leave a standing embedding backlog for the
sweep. `aindy-runtime memory prune-cascade-debris` (`AINDY/memory/cascade_cleanup.py`) removes
them, scoped by `extra.event_payload.task_name` — the same predicate the fixed capture path uses
to decide what *not* to create, so no user- or app-authored memory can match and no content
string is ever matched on. Reports unless `--yes`; deletes in committed batches (one long
transaction holding a pooled connection is the exact failure mode this item exists to prevent);
child rows go via `ON DELETE CASCADE`. Tests: `tests/unit/test_cascade_cleanup.py` (10).

> **Diagnostic note:** `pg_stat_activity` shows the *last* statement, not the caller. When
> `xact_age_s == idle_s` on many connections, the transaction has exactly one statement — that is
> equally consistent with "held across a slow call" (parts 1–2) and "held by a frame that never
> returned" (part 3). Only a stack dump separates them:
> `docker exec --privileged -u root <api> py-spy dump --pid 1` (needs `--privileged`; the
> container does not carry `CAP_SYS_PTRACE`).

**Part 2 — the embedding-job fan-out (the app's follow-up report).** App-side
`pg_stat_activity` on 1.10.0 showed 30+ **concurrent** connections, each running **exactly
one** `SELECT memory_nodes …` then sitting `idle in transaction` with **`xact_age_s ==
idle_s`** — i.e. each opened a transaction, did one read, and held it for the transaction's
whole life. Traced to `embedding_jobs.process_embedding_job`: `queue_system_event(...
EMBEDDING_STARTED, required=True)` commits, which **expires** `memory_node`, so reading
`memory_node.content` triggers a **refresh `SELECT memory_nodes`** that opens a *fresh*
transaction — and `generate_embedding()` (the slow LLM/embedding API call) then ran with it
open. One job is enqueued **per captured memory**, each on its **own** session, so a single
request fanned out to dozens of concurrently-held connections → pool exhaustion → ~45s login.
**Fix:** capture `node_content` into a local, `db.commit()` (guarded) to return the connection
to the pool, *then* embed; the write below re-acquires a connection for its fast execution.
The job owns its session (not request-shared) and the `EMBEDDING_STARTED` event should be
durable anyway, so committing there is correct as well as necessary. Test:
`test_memory_txn_leak.py::test_embedding_job_releases_connection_before_embedding` (pins the
commit-before-embed order).

**Root cause (traced, not speculated).** `MemoryNodeDAO.recall()` ran
`_count_complete_embeddings()` (a DB query → autobegins a transaction on the request-shared
session), then called `generate_query_embedding()` — a **synchronous OpenAI/Anthropic
embedding-API call (~seconds)** — while that transaction was still open. The DB connection
sat `idle in transaction` (`wait_event_type=Client`) for the whole API call. Every
semantic recall runs through this path (the pipeline's per-request `_safe_recall_memory_count`
→ `MemoryOrchestrator.get_context` → `dao.recall`), so under the concurrent request fan-out
a browser login triggers, ~60–85 connections piled up idle-in-transaction and exhausted the
SQLAlchemy pool; the rest of the request then waited the full `pool_timeout`. The app-side
`pg_stat_activity` snapshot (~61 `idle in transaction` on `SELECT memory_nodes …`) matched
exactly. Note the recall *code* was not itself leaking sessions — it correctly used the
caller's `db`; the leak was **holding that one connection across a slow external call**.

**Fix (reorder, not rollback).** `MemoryNodeDAO.recall` now generates the query embedding
**before** any DB query in the method (`_count_complete_embeddings` moved *after* it). After
the request's prior work commits (auth handler, then the pipeline's memory capture), the
session holds no pooled connection when recall runs; embedding-first keeps it that way, so the
~seconds API call happens connection-free, and the fast DB queries below re-acquire a
connection only for their execution. The `/memory/nodes/search` route and the
`memory_nodes_search_similar` flow node already embed-first (comments added). Tests:
`test_memory_txn_leak.py` (2 — embed-before-DB ordering + no-rollback). End-to-end (no
`idle in transaction` under a real login, sign-in under 30s) is app-side `pg_stat_activity`
verification.

> **Rejected approach — do NOT rollback the request-shared session to release its connection.**
> A first cut added `release_read_transaction(db)` (a guarded `db.rollback()` before the
> embedding). It broke `test_agent_approve_idempotency` (a shared session in flight): the
> `session.new/dirty/deleted` guard only catches ORM-tracked changes, **not** Core-level
> `db.execute(UPDATE …)` or a test/pipeline outer transaction — so the rollback discarded
> in-flight request state. **Rolling back a request-shared session mid-request is unsafe;**
> the safe remedy is to not open the transaction until after the slow external call (the
> reorder).

**Possible follow-up (not required):** `get_context`'s multi-`node_type` loop re-embeds the
same query per type — embed once at the orchestrator and pass the vector down to avoid
redundant API calls (a latency, not a leak, concern).

## INFINITY-RUNTIME-1 — Runtime Infinity loop-closure gaps (accepts app handoff INFINITY-RUNTIME-HANDOFF-1)

**Status:** **CLOSED 2026-07-08.** All five structural loop-closure gaps + the item-3
app-facing aggregate syscall are shipped (PRs #194–#198; see Advance log below).
Runtime-owned counterpart to the app-side handoff
`aindy-apps-monolith/TECH_DEBT.md` → **INFINITY-RUNTIME-HANDOFF-1** (Phase 2 unblocked —
Gap 4 was the gate; the support-metrics aggregate is now available for the app's
`dependency_adapter`). **Deliverable C (autonomous acting on NextAction) shipped opt-in
2026-07-09** — the record-first Gap 4 now has a bounded acting half: `core/next_action_dispatch.py`
`maybe_act_on_next_action` (wired into `_emit_agent_next_action` after the `NEXT_ACTION_CHOSEN`
emit) dispatches ONE follow-up run for an **app-sourced** `trigger_execution` decision carrying an
objective. It goes through the async job `agent.next_action_followup` → `create_run` → (if
auto-approved) `execute_run`, reusing the existing rails — the approval gate is structurally
preserved (a `pending_approval` follow-up is left for a human, never force-executed), capability
preflight applies, and admission is bounded by `count_active_executions`. One net-new rail: a
**chain-depth cap** (`parent_run_id` hops, `AINDY_NEXT_ACTION_MAX_CHAIN`, default 3) so a hook that
always returns `trigger_execution` cannot self-perpetuate — the window's max-iterations bounds a
single window, not a NextAction chain. Gated `AINDY_NEXT_ACTION_ACTING` (default off). The runtime
never acts on its own runtime-default decision (`trigger_execution` is never a runtime default,
plus an explicit non-default `source` guard). Agent runs only (async/flow completion paths have no
NextAction seam). No syscall/schema, no new SystemEventType. Tests:
`tests/unit/test_next_action_acting.py` (16 — gating, chain-depth walk/cap, admission cap, dispatch
payload, approval-respecting follow-up job). Deferred, non-blocking follow-ups only: flip the three
opt-in flags after app soak (`AINDY_PLANNER_MEMORY_INJECTION`, `AINDY_ASYNC_JOB_LOOP_CLOSURE`,
`AINDY_NEXT_ACTION_ACTING`); broaden acting verbs (`retry`/`schedule_follow_up`) if a need arises.

**Context:** The Infinity scoring/orchestrator/loop is app-owned
(`aindy-apps-monolith/apps/analytics/services/{scoring,orchestration}/`). This repo owns
the execution substrate and the loop-closure primitives the app-side "force execution
through Infinity" phases depend on. The runtime-side audit is
`docs/runtime/INFINITY_LOOP_AUDIT.md`; it now cross-links the app docset (was
one-directional — the app docs pointed here, this repo did not point back; fixed
2026-07-05).

**The five structural loop-closure gaps** (from `INFINITY_LOOP_AUDIT.md` §"The five
structural gaps") — described in that audit but previously untracked in this repo's debt
register:

1. **Gap 1 — Recall → Planning link broken.** Memory is recalled into execution context
   but not into the planning prompt; the planner context provider must query and inject
   memory into the system prompt.
2. **Gap 2 — Event ledger missing three entries.** `RecallUsed`, `ScoreComputed`,
   `NextActionChosen` are not emitted as `SystemEventTypes`; the learning loop improves but
   cannot explain itself.
3. **Gap 3 — No execution-level score record.** `MemoryLearningEngine` scores memory nodes,
   not the execution as a whole; no single `{run_id, score, dimensions}` record is written
   after each run (`ANALYTICS_SCORE_UPDATED` exists but is not consistently emitted).
4. **Gap 4 — No Next-Action engine primitive.** Post-run "what should happen next" is
   decided by the flow graph or app-registered completion hooks, not a runtime-owned
   decision. `_run_completion_hooks()` is the right attachment point but needs a contract
   return type the runtime acts on. **This gap gates the app-side Infinity Phase 2**
   (pre-dispatch control).
5. **Gap 5 — Async jobs outside the loop.** Jobs via `sys.v1.job.submit` do not
   automatically produce memory, trigger recall, or get scored; job completion should emit a
   memory write + score event like the agent path.

**Runtime-gated support inputs** (app handoff item 3; `INFINITY_ALGORITHM_SUPPORT_SYSTEM.md`
Steps 3 & 4): observability aggregates (`AINDY/routes/observability_router.py`) and
agent/async execution metrics (`AINDY/agents/agent_event_service.py`,
`AINDY/platform_layer/async_job_service.py`) have no app-facing aggregate syscall/job. The
app lever is a `dependency_adapter` fetch once the runtime exposes the aggregate — i.e. a
runtime feature request, not an app edit.

**Close/advance trigger:** any of the five gaps closed, or an aggregate observability/
execution syscall exposed. Notify the app-side `INFINITY-RUNTIME-HANDOFF-1` reopen trigger
on each advance.

**Advance log:**
- **2026-07-08 — Gap 2 (partial) + Gap 3 CLOSED.** Added the three loop-closure event
  constants (`RECALL_USED`, `SCORE_COMPUTED`, `NEXT_ACTION_CHOSEN`) to
  `core/system_event_types.py` — deliberately un-prefixed by `execution.` so they emit
  outside the pipeline/async contract gate. Wired **Gap 3** end-to-end: new canonical
  `core/execution_score.py` (`compute_execution_score` scalar 0–1 scorer flooring failure
  statuses to 0.0 and holding a 0.6 success floor via `evaluate_result`; `emit_execution_score`
  best-effort emitter) emits one `SCORE_COMPUTED` SystemEvent per finished execution carrying
  the durable `{run_id, score, status, dimensions[, duration_ms]}` record — the event row IS
  the record, **no schema table / no Alembic / no contract bump**. Emitted at the agent-run
  terminal path (`agent_runtime/execution.py` `_emit_agent_run_score`, both `completed` and
  `failed`, covers AGENT_FLOW + nodus_vm backends) and the generic `ExecutionLoop`
  (`runtime/memory_loop.py`, trace-correlated, `source="execution_loop"`). Fills the
  never-emitted `ANALYTICS_SCORE_UPDATED` slot the audit flagged. `SCORE_COMPUTED` is the
  only fully-wired event of the three; `RECALL_USED` (Gap 1) and `NEXT_ACTION_CHOSEN` (Gap 4)
  are constants-only pending their PRs. Tests: `test_infinity_score_event.py`. Docs:
  `INFINITY_LOOP_AUDIT.md` §9/Gap 3. **Remaining: Gaps 1, 4, 5 + item-3 aggregate syscall.**
  Notify app-side `INFINITY-RUNTIME-HANDOFF-1`.
- **2026-07-08 — Gap 1 (recall→planning) CLOSED + Gap 2's `RECALL_USED` wired.** `generate_plan`
  (`agents/agent_runtime/planning.py`) now recalls objective-keyed memory pre-plan via a new
  runtime-owned `_recall_planner_memory` (`MemoryOrchestrator.get_context`, **not** the
  app-registered planner-context provider — whose runtime-default `context_block` is empty) and
  injects it into the planner prompt through a new `memory_block` param on `_build_planner_prompt`
  (symmetric to `context_block`). Injection is gated by config flag `AINDY_PLANNER_MEMORY_INJECTION`
  (default **off**; opt-in, flip after app soak so plan quality doesn't shift silently — mirrors
  the nodus_vm discipline). New canonical `core/execution_recall.py` `emit_recall_used` emits one
  `RECALL_USED` event (`{query, node_ids, count, operation_type}`, no-op on empty recall) at **both**
  recall sites — planning (`agent_planning`) and execution (`_build_execution_memory_context`,
  `agent_execution`) — so the ledger's `RecallUsed` entry is no longer silent. No schema change.
  Tests: `test_infinity_recall_event.py` (11). Docs: audit §4/§6/Gap 1/§16.
  **Remaining: Gaps 4, 5 + item-3.** Notify app-side `INFINITY-RUNTIME-HANDOFF-1`.
- **2026-07-08 — Gap 5 (async jobs in the loop) CLOSED (opt-in).** Root cause found: the async
  path already emits `EXECUTION_COMPLETED`, but the `execution.*` contract gate
  (`system_event_service.py:448`, `ENFORCE_EXECUTION_CONTRACT`) raises it and
  `_emit_async_system_event` swallows the error → never persists → no auto-capture. Fix
  (`platform_layer/async_job_service.py`): gated by new flag `AINDY_ASYNC_JOB_LOOP_CLOSURE`
  (default **off**), `_execute_job_inline` activates the async-execution context (mirroring
  `flow_engine/runner.py`) so `EXECUTION_STARTED/COMPLETED/FAILED` persist and drive
  auto-capture (jobs produce memory), plus `_emit_async_job_score` emits a per-job
  `SCORE_COMPUTED` via the Gap-3 helper (`source="async_job"`). Recall-into-jobs deliberately
  NOT wired (infra job bodies → weak recall relevance; `SCORE_COMPUTED` needs no recalled ids).
  Default-off because it makes ALL job workers write memory + score (embedding/metric infra
  jobs included) — opt in after soak. No schema change. Tests: `test_infinity_async_job_loop.py`
  (6, incl. a mechanism test proving async-context activation lets `EXECUTION_COMPLETED` past
  the gate). Docs: audit Gap 5/§16, `.env.example`. **Remaining: Gap 4 + item-3 aggregate
  syscall.** Notify app-side `INFINITY-RUNTIME-HANDOFF-1`.
- **2026-07-08 — Gap 4 (Next-Action engine) CLOSED (record-first). ALL FIVE STRUCTURAL GAPS
  NOW CLOSED.** New `core/next_action.py` defines the NextAction contract
  (`done`/`retry`/`ask_user`/`escalate`/`schedule_follow_up`/`create_memory`/`recommend`/
  `trigger_execution`) + `coerce_next_action` (string/dict/obj → normalized) + `default_next_action`
  (runtime heuristic: done on success, retry/escalate on failure) + `emit_next_action_chosen`.
  `agents/agent_runtime/execution.py` now **captures the `_run_completion_hooks` return**
  (previously discarded at :218), coerces it, falls back to the runtime default, and emits
  `NEXT_ACTION_CHOSEN` for **completed + failed** runs. **Record-first:** runtime records the
  decision, takes NO autonomous action — app orchestrator consumes it. **This lifts the
  app-side Infinity Phase 2 gate** (the gap that gated it). Hook-contract-only — no new syscall,
  no schema. Runtime *acting* on decisions (auto-retry/schedule) is a deferred follow-up. Tests:
  `test_infinity_next_action.py` (21). Docs: audit §6/§11/Gap 4/Verdict rewritten.
  **All 5 structural gaps (1–5) closed; only item-3 (app-facing aggregate observability/
  execution syscall) remains — a separate feature request.** Notify app-side
  `INFINITY-RUNTIME-HANDOFF-1` (Phase 2 unblocked).
- **2026-07-08 — Item 3 (app-facing aggregate syscall) CLOSED. INFINITY-RUNTIME-1 fully
  closed.** New syscall **`sys.v1.observability.support_metrics`** (capability `execution.read`)
  + `platform_layer/support_metrics_service.py` `build_support_metrics`: a tenant-scoped,
  read-only rollup of request metrics + platform health (Step 3) and agent-run / async-job /
  Infinity-loop-event distributions (Step 4), over `window_hours` (default 24, max 168). No new
  persistence — reuses existing tables via per-tenant group-bys. Chose `execution.read` because
  it is already on the SDK-SYSCALL-GRANT-1 `/platform/syscall` dispatch surface
  (`_DISPATCH_CAPABILITY_SCOPES`), so the app fetches it via `dependency_adapter` with the
  existing `execution.read` scope — **no router/grant changes**. `SYSCALL_REGISTRY_MIN_COUNT`
  21→22; added to `_STABLE_SYSCALLS` (cross-repo contract). Tests:
  `test_infinity_support_metrics.py` (7, real-session tenant-scoping + window + event counts).
  Docs: `SYSCALL_REFERENCE.md` (new domain `observability` + scope table),
  `SDK_CONTRACT.md`. Notify app-side `INFINITY-RUNTIME-HANDOFF-1` — the aggregate is live.

## APP-FR-* — App-side runtime feature requests (handoff 2026-07-17)

**Source:** `aindy-apps-monolith` handoff doc "Runtime Feature Requests — handoff to
aindy-runtime" (last_verified 2026-07-17). Four runtime-owned items surfaced during the
apps build. Verified against runtime source on receipt (2026-07-17): **two of the four
are already shipped here** — the handoff was written without visibility into the runtime's
current state. IDs mirror the app doc (FR-1..FR-4). App-side priority was
FR-3 > FR-1 > FR-2 > FR-4; corrected runtime picture: the only fully net-new item is FR-1.
**FR-1/FR-3/FR-4 shipped in v1.8.0; FR-2 pre-existing.** A fifth item (**FR-5**) surfaced
2026-07-18 (native workflows couldn't reach app callables) — verified real; see below.
A sixth (**FR-6**, self-service password management) surfaced 2026-07-31 — verified real;
item 1 (change-password) shipped 2026-07-31, items 2+3 (forgot/reset) are the open remainder,
blocked on a token-delivery channel (FR-1). **FR-7** (memory recall defects) shipped in
v2.0.0. **FR-8, FR-9 and FR-10 arrived 2026-08-03 and shipped 2026-08-05 — see below; all
three are 2.0.0 upgrade-path defects, so they gate a 2.0.1.** **FR-11/12/13 filed 2026-08-06** (callback timeout budget; no agent-registration surface; `agents` has no metadata column) — all verified against source, none built. **FR-14/15/16 filed 2026-08-15/16** (their own sections below; 16 closed in 2.3.0, 15 (b)+(c) shipped, 14 half closed). **FR-17** (async-job `execution.*` eaten by the contract gate, #518) and **FR-18** (a full health snapshot persisted per liveness probe — 99.6% of one database, #517) arrived 2026-08-22 and were fixed the same day; both have their own sections. **FR-19/20/21 arrived 2026-08-22**; 20 fixed, 19 and 21 open, each with its own section. Next available: **FR-22**.

### FR-8/9/10 — the 2.0.0 upgrade trio (SHIPPED 2026-08-05)

Filed 2026-08-03 by the app team while upgrading a live deployment to `aindy-runtime==2.0.0`
— i.e. found *because* they were the first to run the release we had just cut, on the install
shape we do not exercise ourselves. **All three verified against source before building; all
three premises held.** They sat untracked for two days, which is the argument for this entry
existing at all.

The common thread is worth more than the individual fixes: **each one is invisible from a
source checkout and from CI.** A wheel install has no `alembic/` tree; a Compose file writes
empty strings where a shell writes nothing; an app registering a connector type is a shape we
never see in our own tests. Our green board did not and could not have caught any of them.

| | Defect | Fix | PR |
|---|---|---|---|
| **FR-10** | `AINDY_REQUIRE_VERIFIED_LOGIN: "${VAR:-}"` renders as `""`; pydantic rejects it as a bool and `settings = Settings()` runs at **module import**, so the container restart-loops before serving (27 restarts). | `env_ignore_empty=True` on `model_config` — empty means unset. | #360 |
| **FR-8** | Alembic `0014` grandfathers pre-existing accounts to verified; `alembic/` is **not in the wheel**, and `reconcile_runtime_schema` is purely structural, so wheel installs left every account unverified. Latent lockout the moment an operator believes our upgrade notes and enables the flag. | Columns declare `info={"reconcile_backfill": "<sql>"}`; reconcile issues the `UPDATE` right after the `ADD COLUMN`. | #361 |
| **FR-9** | Runtime transactional mail dispatched to the `email` connector type — the same type apps register for automations — in a different, undocumented action shape. Combined with the (correct) no-fallback rule: `/auth/register` returns 202 and **no account can complete signup**. | Reserved `transactional_email` type; shape published in `CONNECTOR_CONTRACT.md` §5a; failure logs at ERROR. | #362 |

**Three findings from the work, each of which would otherwise be rediscovered painfully:**

- **SQLAlchemy renders `server_default="false"` as `DEFAULT 'false'`** — a *quoted string
  literal*. Postgres casts it to boolean false on a BOOLEAN column; **sqlite stores the four
  characters and reads back truthy**. A sqlite test asserting boolean semantics here is
  testing type affinity, not your code. FR-8's end-to-end test uses a text column for exactly
  this reason.
- **Alembic `0014`'s re-run guard does not do what its comment claims.**
  `WHERE ... created_at < now()` evaluates `now()` at *execution* time, so on a re-run it
  matches every row rather than only those predating the first run. Low practical risk
  (alembic will not re-run an applied revision) but the comment overstates the protection.
  Not edited — changing a shipped migration is its own risk.
- **`send_email`'s "never raises" guarantee is inherited, not enforced.** It holds only
  because `dispatch_connector` normalises handler exceptions. A test that mocks the
  dispatcher therefore proves nothing about it; FR-9's test registers a genuinely broken
  handler and drives the real path.

**Not taken, deliberately:** FR-8 asks 2–4 (ship the alembic tree in the wheel; document the
wheel-vs-source difference; refuse a security-relevant NOT NULL column on a populated table)
— all are unnecessary once the guarantee simply holds everywhere, which is ask 1. FR-9 ask 4
(dry-run probe at registration) — separating the types removes the failure it would catch,
and dispatching a synthetic action into a handler at boot has its own side-effect risk.
FR-10 asks 2–3 (better error text; document which settings are typed fields) — ask 1 makes
the process survive, which was the outage; the rest is polish if the message still confuses.

**Release:** all three are upgrade-path defects for the *current* published version, so they
want to ship together as **2.0.1** rather than wait for the next feature release. Held
pending owner go-ahead.

### FR-11/12/13 — filed 2026-08-06, verified against source, not yet built

Received in the app-team handoff dated 2026-08-06. **All three premises verified before
filing**; corrections noted where the reported mechanism differs from the code.

---

#### FR-11 — `invoke_runtime_callback` has a 10s non-configurable budget — **SHIPPED 2026-08-15**

**Status: SHIPPED (2026-08-15).** `AINDY_RUNTIME_CALLBACK_TIMEOUT_SECS` (default **30s**, up
from a hardcoded 10s), resolved **at call time** so it takes effect without a restart. The
explicit `timeout_seconds=` parameter still wins when a caller passes one.

**The default was sized on measurement, not taste:**

| Evidence | Value |
|---|---|
| Measured cold start + work, runtime-only profile, idle host | **~3.85s median** (3 runs) |
| Old budget | 10.0s — **~2.6× headroom on the lightest possible profile** |
| Sibling subprocess boot allowance (`nodus_runtime_adapter._DEFAULT_BOOT_ALLOWANCE_MS`) | **15s for boot alone**, on top of a 30s script clock |
| New default | 30s — ~8× the measured idle cost, still bounded |

The second row is the sharp one: this callback's *entire* budget was smaller than what a
comparable subprocess in the same repo is given merely to start.

**★ Read at call time, deliberately.** A module-level constant would have been simpler and
wrong: CLAUDE.md records import-time env reads as a recurring hazard here (FR-10's container
crash-loop, `ResourceManager._get_backend()`, the rate limiter's Redis alias) precisely because
they are invisible to behavioural tests. One `os.getenv` against a process spawn costs nothing,
and `test_env_is_read_at_call_time_not_import_time` fails if it ever regresses — without needing
a reload trick.

**Also:** unparseable and non-positive values log a warning and fall back rather than raising
(this runs on scheduled-job paths), and the timeout message now names the elapsed budget and the
env key instead of a bare *"runtime callback command timed out"*.

**Expected to mitigate `FLAKY-1`** — the ~50% failure in a required check whose leading
hypothesis is exactly this budget. Not claimed as closed: see that entry.

**Original filing follows.** The app team filed this explicitly as *not a defect*: it self-resolved, does not reproduce
warm (0 timeouts in the following 6 minutes, 19,370 autonomy decisions recorded), and was a
cold-start artifact on a container that took ~285s to become responsive. They note they nearly
shipped circuit breakers across three apps before checking whether it reproduced. Worth
preserving that restraint in the record.

**Verified true:**
- `runtime_callback_host.py:43` — `timeout_seconds: float = 10.0`, a parameter default.
- Neither call site in `registry.py` (lines 433, 442) passes a timeout.
- No env or settings override exists — `grep` for a timeout key in `config.py` finds nothing.
  A deployment on a slow host has no lever.
- It is invoked from scheduled jobs, so a failure repeats on an interval, and repeats hardest
  exactly when the host is slowest. Self-amplifying.

**One reported mechanism is inaccurate.** The handoff says the payload carries
`bootstrap_register`, so *"the subprocess re-runs app bootstrap — 16 apps here."* In source,
`registry.py:410` sets `bootstrap_register` only when
`module_name == "AINDY.platform_layer.runtime_agent_defaults"`, and the worker uses it to call
that one module's `register()`. It is not a 16-app bootstrap. The real per-call cost is a fresh
Python subprocess doing `importlib.import_module(module_name)` on an app module, which pulls
that module's transitive import graph — expensive for the same reason, by a different route.
The ask is unaffected; the reasoning should be stated correctly.

**Ask (their framing, all small, none urgent):** make the timeout configurable (precedent:
`AINDY_NODUS_MAX_EXECUTION_MS` / `AINDY_NODUS_BOOT_ALLOWANCE_MS` exist for exactly this class
of problem); avoid re-importing per call where the callback does not need the full graph; back
off after N consecutive failures so a cold start cannot be amplified by the scheduler; log the
first occurrence at WARNING with elapsed time rather than a traceback per tick.

Related: `_maybe_wrap_runtime_callback` subprocess hazards are already documented in CLAUDE.md,
and `PLANNER-SUBPROC-1` is the closed case of the same subprocess being unable to see live
in-process state.

---

#### FR-12 — no way to register an agent; the roster is hardcoded — **SHIPPED 2026-08-15**

**Status: SHIPPED (2026-08-15).** `registry.register_agent(...)` — the identity hook — plus
owner-scoped reads, reserved system namespaces, and a startup upsert. No schema change (FR-13
already added the `metadata` column this uses).

**★ Correction: the filed premise was wrong.** This entry said *"the only ways to add a row are
a runtime code change or a raw `INSERT`"*. Checked before building on it:
**`POST /platform/admin/agents/register` already existed** in `admin_router.py`, is mounted at
`/platform`, is runtime-owned (confirmed against `APP_ROUTERS`, not by file location — the
`DOCS-STALE-1` lesson), admin-authenticated, and idempotent on `memory_namespace`. What was
genuinely missing is narrower and sharper than "no way to register":

| Filed as | Actually |
|---|---|
| no way to add a row | an admin route existed; **no *platform hook*** for an app to declare an identity at plugin-load |
| `count(owner_user_id) = 0` unexplained | **no path ever wrote `owner_user_id`** — the admin route does not accept it, so the column could not be populated |
| reads unscoped | true |
| — | **system namespaces unreserved**, so the admin route's idempotent-*update* branch silently rewrote platform rows |

**That last one is a defect this work found and fixed.** `POST …/agents/register` with
`memory_namespace: "runtime"` took the *update* branch and rewrote the platform's own Runtime
agent — name, type, description — for anyone with admin. The next boot would **not** repair it,
because `_bootstrap_system_agents` only `INSERT`s when the row is absent. Both the hook and the
route now reject the seven reserved namespaces from one shared `SYSTEM_AGENTS` set (a test pins
that they share it, since two guards reading two lists would drift).

**★ A shipping bug caught by its own test.** `INPROC_CAP_REGISTER_AGENT` was declared but not
added to `_ALL_INPROC_EXTENSION_CAPABILITIES`. Because
`_require_in_process_extension_capability` raises `PermissionError` for any capability outside
the caller's allowed set — and that set is derived from `_ALL_` — the hook would have been
**denied in exactly the plugin-load context it exists for**. Fixed; the test that caught it
stays.

**Design note:** registration is *declarative*. `register_agent` records a spec and touches no
database, because plugin load happens long before a session exists;
`startup._apply_registered_agents()` then upserts by `memory_namespace` — the durable identity
and the tag on every memory node the agent writes. Unlike the system seed it also *updates* an
existing row, so an app changing its display name or metadata between boots needs no manual DB
edit, and nothing about the agent's memory history moves.

**★ The deferred half SHIPPED 2026-08-15** — see `FR-12b` below. The deferral reasoning
("app-layer policy") did not survive contact: ownership, per-owner name scoping and
owner-scoped reads are properties of the *table*, so every app would have rebuilt all three
against a schema that fought them.

**★ The flagged `DELETE` gap is now recoverable (2026-08-15), and the policy is still open** —
also `FR-12b`. `_bootstrap_system_agents` gained a repair path and
`POST /admin/agents/{namespace}/restore` exists. Whether an admin *should* be able to
deactivate a system agent remains undecided, deliberately.

**Original filing follows. Verified true, and the live data confirms the sharper half:**
- `AINDY/startup.py:937` — `_SYSTEM_AGENTS` is a hardcoded list of exactly 7 specs (ARM,
  Genesis, Nodus, SYLVA, Platform, Runtime, Memory), upserted by `memory_namespace`.
- There is **no `register_agent`**. The registry exposes 8 `register_agent_*` hooks —
  `register_agent_tool`, `_planner_backend`, `_planner_context`, `_run_tools`,
  `_completion_hook`, `_event`, `_ranking_strategy`, `_capabilities` — every one registers
  behaviour attached to an agent, none registers an *identity*.
- Live on the app stack: `agents` holds 7 rows, all `agent_type='system'`, and
  **`count(owner_user_id) = 0`**. The per-user half of the schema has never been exercised, as
  reported.

So the table is shaped for a general registry (`owner_user_id`, `memory_namespace`,
`agent_capability_mappings` keyed by `agent_type`) and the only ways to add a row are a runtime
code change or a raw `INSERT`.

**Ask:** a `register_agent(...)` platform hook (or an authenticated route/syscall for
user-owned agents); honour `owner_user_id` on read so one user cannot enumerate another's;
keep the idempotent-upsert semantics `_bootstrap_system_agents` already uses; reserve the seven
system namespaces against app registration.

**Their related observation, also verified:** `AGENT_USER = "user"` exists in
`AINDY/db/models/agent.py` and is excluded from `_SYSTEM_AGENTS`, so no row is ever created for
the user's own agent.

---

#### FR-12b — user-owned agents, and a repair path for system agents — **SHIPPED 2026-08-15**

**Status: SHIPPED (2026-08-15).** The two halves FR-12 left open, plus the schema constraint
that made the first of them incoherent and a route-guard defect that made its error codes
wrong. Alembic `0016_agents_owner_scoped_name`, schema contract `2026-08-15.1`,
`RUNTIME_ALEMBIC_HEAD_REVISION` → `0016`.

**★ The deferral reasoning was wrong, and it is worth recording why.** FR-12 deferred the
user-facing surface as app-layer policy, on the "runtime owns the mechanism, app owns policy"
split. But *ownership* is not policy — it is three properties of the table:
`owner_user_id` semantics, per-owner name scoping, and owner-scoped reads. Every app wanting
user-owned agents would have had to build all three, against a schema actively working against
them, and each app would have got the enumeration boundary slightly differently. What an app
*does* with an agent is still app policy; that half of the split holds.

**`agents.name` is unique per owner, not globally.** The old global `UNIQUE` was inherited
from a table that in practice held seven platform rows. It means the first user to register
"Assistant" takes that name from every other user in the deployment, and the 409 telling them
so reports on a row they cannot see. Replaced by two **partial** unique indexes:

| Index | Predicate |
|---|---|
| `uq_agents_name_shared` | `UNIQUE (name) WHERE owner_user_id IS NULL` |
| `uq_agents_owner_name` | `UNIQUE (owner_user_id, name) WHERE owner_user_id IS NOT NULL` |

**★ A plain `UNIQUE (owner_user_id, name)` is NOT equivalent, and the difference is the whole
point.** SQL treats NULLs as distinct inside a unique constraint, so every shared row — all
seven system agents and every app-registered identity, all `owner_user_id IS NULL` — would
escape it entirely, and two rows named "Runtime" would both be accepted. The partial pair keeps
the old global guarantee exactly where it still applies. Verified on real PostgreSQL as
property P6, which fails loudly under the naive constraint.

`memory_namespace` is deliberately untouched and stays globally unique: it is
`MemoryNodeModel.source_agent`, the tag on every memory node the agent writes, so one namespace
must mean one agent process-wide.

**Verified against real PostgreSQL** (throwaway database, nine properties), not just
unit-tested:

| Property | Result |
|---|---|
| Blank DB, no `agents` table (ALEMBIC-FRESH-DB-1) | skips cleanly |
| Pre-migration, two owners share a name | rejected — i.e. the defect reproduces |
| `upgrade()` on a populated table | rows preserved, both indexes present, `agents_name_key` gone |
| Post-migration, two owners share a name | accepted |
| One owner reuses their own name | rejected |
| Two un-owned rows share a name | rejected (the NULL trap) |
| `upgrade()` re-run | idempotent |
| `downgrade()` | indexes dropped, constraint restored |
| `downgrade()` re-run | idempotent |

**The user-facing surface.** `/platform/agents` — `GET` (list), `POST` (create), `PATCH`
(name/description/metadata), `DELETE` (soft), `POST …/restore`. Registered like `admin_router`,
outside the execution contract, because these are plain DB-query handlers.

- **The namespace is derived, not accepted** — `u:<user_id>:<slug>`. If users chose it
  directly, a taken namespace would have to 409, and that 409 reports on a row the caller
  cannot see: the same cross-tenant existence oracle tracked for `/auth/register`. Deriving
  makes a cross-user collision **impossible by construction** rather than merely detected, so
  every conflict a user can observe is with their own agent.
- **`agent_type` is forced to `custom`.** `agent_capability_mappings` is keyed by it. Nothing
  in the runtime grants capability from that column today, and a user-facing create route is
  not where you want to discover that changed.
- **Create is deliberately not idempotent**, unlike the admin route. An idempotent update
  branch is precisely what silently rewrote platform rows before FR-12 reserved them.
- **A foreign agent is 404, never 403.** A 403 confirms that someone else holds the slug.
- **A principal with no resolvable user is refused (400)**, rather than falling through to
  `owner_user_id = NULL` — which would create a *shared* agent, the one outcome an ownership
  route must never produce by accident.
- `slug` and therefore `memory_namespace` are immutable on PATCH: the namespace is already
  written onto this agent's memory nodes, and changing it orphans exactly the history FR-13's
  metadata bag exists to preserve.

**The repair path.** `_bootstrap_system_agents` claimed "idempotent upsert by
memory_namespace" in its docstring and was insert-only in its body, so a drifted platform row
was never repaired. **Closing the FR-12 hole made this sharper, not milder:**
`POST /admin/agents/register` was the only surface whose update branch set `is_active = True`,
so reserving the seven system namespaces (correctly) removed the last accidental route back for
exactly the rows that matter most. Split on purpose:

- **Identity** (`name` / `agent_type` / `description`) is platform-owned, so boot restores it
  from the spec and logs a WARNING naming the fields.
- **`is_active` is NOT repaired at boot.** Silently re-enabling an agent an operator
  deactivated trades a missing repair path for an unpredictable one. Boot warns and names the
  remedy; `POST /platform/admin/agents/{namespace}/restore` is the repair and needs no restart,
  and for a reserved namespace it repairs the identity fields in the same call.

**★ Policy DECIDED 2026-08-15 (owner): an admin MAY deactivate a platform system agent.** It is
a supported operator action, not an anomaly to be prevented, so no reserved-namespace guard is
added to `DELETE` — and the reservation on `POST …/agents/register` is *not* precedent for one,
because that guard exists to stop an idempotent-update branch silently rewriting platform rows,
which is a different thing from an explicit, visible, reversible operator action.

**The decision makes the boot behaviour more clearly right, not less.** Leaving `is_active`
alone at boot was chosen when the policy was open, as the conservative option; now that
deactivation is *sanctioned*, silently reversing it on the next restart would be actively wrong
— a supported decision the platform undoes behind the operator's back. The boot WARNING stays,
reworded to say the state is supported rather than anomalous, because it is still consequential
and not otherwise visible (`flow_definitions_memory` filters `is_active`). `DELETE` returns a
`warning` naming the restore endpoint.

**Two defects found while building, both fixed here:**

1. **`memory_agents_list_node` listed every active agent to every caller.** Harmless while all
   seven rows were un-owned — which is exactly why it survived — and a cross-user leak of
   names, descriptions and metadata the moment users own agents. Now `owner_user_id IS NULL OR
   = :caller`.
2. **Two lists described one roster.** `SYSTEM_AGENTS` (what an app may not register) and
   `startup._SYSTEM_AGENTS` (what the platform does register) were maintained separately with
   nothing making them agree. Now one declaration, `SYSTEM_AGENT_SPECS` in
   `AINDY/db/models/agent.py`, with the set derived from it and a test pinning the derivation.

**Test-patching gotcha, the inverse of the scheduler-job rule:** `startup.py` binds
`SessionLocal` at **module level** (line 56), so a test must patch `AINDY.startup.SessionLocal`.
Patching `AINDY.db.database.SessionLocal` — correct for scheduler jobs, which import inside the
function body — silently does nothing here: the seed runs against the real engine, logs
`no such table: agents` as a *non-fatal* warning, and the test fails on its assertion rather
than on the patch.

**Also relaxed:** FR-13's `test_head_constant_matches` asserted `== "0015"`, which is really
the claim "0015 is the newest migration that will ever exist" and turns red on any unrelated
migration. Now `>= "0015"`. The constant-vs-scripts-dir check is authoritative and CI-enforced
in `tests/unit/test_runtime_alembic_head.py`; this one only needs to know `0015` is reachable.

---

## LINT-FORMAT-1 — a documented lint command the repository does not satisfy

**Status: Open — P3 (cosmetic). Filed 2026-08-19.**

**What it is.** `CLAUDE.md`'s Commands section listed two lint commands side by side:

```bash
ruff check AINDY/
ruff format AINDY/
```

The first is real. The second has never been true of this tree.

**Measured 2026-08-19**, with the same config CI uses (`AINDY/ruff.toml`):

| Scope | Would reformat | Already formatted |
|---|---:|---:|
| `AINDY/` | 259 | — |
| `tests/` | 198 | — |
| both | **457** | 102 |

**Why it matters more than a cosmetic issue normally would.** `CLAUDE.md` is the authoritative
agent-instruction surface. An agent that reads the Commands section, runs `ruff format AINDY/`
as documented, and commits the result produces a **~450-file diff** on top of whatever it was
actually asked to do. The command is not wrong in isolation — it is wrong *as an instruction*,
because following it has a blast radius nothing on the page warns about.

It is also the shape this repo catalogues repeatedly: **a claim that is stated, unenforced, and
untrue** — `DOCS-COVERAGE-CLAIM-1` (docs citing test files that did not exist),
`ROUTE-AST-UNWIRED-1` (a verification with no call site), `DEBT-COMPAT-1` (a compatibility policy
served over HTTP that nothing reads). Nothing about the running system differs whether the
command is honoured, which is why it survived.

**What CI actually runs** (`runtime-ci.yml`, `Runtime Lint`):

```bash
python -m ruff check AINDY tests --config AINDY/ruff.toml
```

`check`, not `format`, and over `tests` as well as `AINDY` — so the documented command was also
narrower than the enforced one. Both halves of the line were misleading.

**Not a `check`/`format` conflict.** `line-length = 120` in `AINDY/ruff.toml`, and the formatted
output of `test_debt_registry_accuracy.py` has a longest line of 117 and passes `ruff check`. The
two tools agree; the tree simply predates ever running one of them.

**Action taken now:** the Commands section states what CI runs, and warns against running
`format` casually. `tests/unit/test_debt_registry_accuracy.py` — the one file this cycle added
code to — was formatted, so newly written code is not left knowingly unformatted.

**★ Do NOT close this by formatting the repository in one sweep.** It rewrites almost every file,
destroys `git blame` across all of it, and buys nothing any check verifies. If it is ever wanted,
the only version worth doing is: format **and** add `ruff format --check` to `Runtime Lint` **in
the same PR**. Formatting without enforcing resets the clock and nothing more — the same reason
`NATIVE-CI-1` was not closed by building the crate once by hand.

**Open question if it is ever taken up:** whether to use `.git-blame-ignore-revs` so the sweep
commit is skippable in blame. That file does not exist in this repo today, and adding one is a
prerequisite, not an afterthought.

---

## ROUTE-GUARD-1 — an HTTPException from an unmanaged route reported as a 500

**Status: FIXED (2026-08-15).** Found while building FR-12b, when a new route's deliberate
`422` came back as `{"error": "internal_error"}`.

`enforce_registered_route_execution` (`AINDY/core/route_execution_guard.py`) wraps **every**
registered route. Its success path, `_assert_execution_context_entered`, always asked two
questions: did this request enter the execution pipeline, and *was it required to*. Its failure
path asked only the first:

```python
if request is not None and not hasattr(request.state, "execution_context"):
    raise RouteExecutionViolation(...)
```

So any exception from a route registered deliberately **outside** the contract became a
`RouteExecutionViolation` → 500. Three routers are registered that way on purpose —
`admin_router`, the new `agents_router`, and `automation_router` — because they are plain
DB-query handlers, and `routing.py` says so at each call site.

**Measured, on `main` before the fix:**

| Call | Intended | Actual |
|---|---|---|
| `POST /platform/admin/agents/register` with `memory_namespace: "runtime"` | 409 | **500** |
| `DELETE /platform/admin/agents/does-not-exist` | 404 | **500** |

**★ The first row is FR-12's reserved-namespace guard, shipped the day before this was found.**
It refused the write correctly and then reported it as an internal error. The guard worked;
only its answer was wrong — and a caller cannot tell "your request was rejected" from "the
server broke" by a 500, which is the difference between retrying and not.

**Why nothing caught it:** the FR-12 tests assert on `admin_router.py`'s **source text**
(`assert "SYSTEM_AGENTS" in source`) rather than calling the route. Source assertions confirm
the code was written; they cannot see what it returns. Same family as `DOCS-COVERAGE-CLAIM-1`
and the absence-assertion in `EVENTBUS-COVERAGE-1`.

**Fix:** the failure path now uses the same two-part question as the success path, extracted as
`_is_pipeline_bypass_on_error`.

**Regression coverage** (`tests/unit/test_route_guard_unmanaged_routes.py`) calls the real
routes over HTTP, and carries a **liveness control in the opposite direction** —
`TestManagedRoutesStillViolate` — because a "fix" that simply stopped raising would satisfy
every other assertion in the file. It pins two routes of identical shape whose only difference
is the `require_execution_context` dependency: managed still violates, unmanaged returns 418.
The pre-existing `test_route_execution_guard.py` suite is unchanged and green.

**Flagged, not fixed — `ADMIN-PROMOTE-UUID-1`:** `POST /platform/admin/users/{user_id}/promote`
also 500s for a missing user, but for an unrelated reason. It passes the raw path string into
`User.id == user_id`, and the SQLite UUID binding raises
`AttributeError: 'str' object has no attribute 'hex'` before the 404 branch is reached. That is
a genuine 500 — the route really did fail — and it is **confined to the SQLite test harness**,
since psycopg2 casts the string on PostgreSQL. Fix is `normalize_uuid` with a 404 on a
malformed id; deliberately not folded into a route-guard change.

---

#### FR-13 — `agents` has no metadata field — **SHIPPED 2026-08-15**

**Status: SHIPPED (2026-08-15).** `agents` gains a JSONB `metadata` column and an
`updated_at` stamp. Alembic `0015_agents_metadata`, schema contract `2026-08-15`,
`RUNTIME_ALEMBIC_HEAD_REVISION` → `0015`.

**Column vs attribute.** The **column** is `metadata` — what the app asked for and what raw SQL
sees. The **ORM attribute** is `Agent.agent_metadata`, because `metadata` is reserved on a
SQLAlchemy declarative class (it is `Base.metadata`). Both halves are pinned by tests so neither
drifts, and a test asserts *why* the rename exists rather than leaving it folklore.

**Purely additive, so no backfill — deliberately unlike FR-8.** Both columns are nullable.
"No metadata recorded" is exactly what `NULL` means, so every pre-existing row is already
correctly represented and reading code must treat absent metadata as empty regardless of row
age. That is why there is no `UPDATE` in the migration and no `info={"reconcile_backfill": …}`
on the columns — the FR-8 mechanism exists for columns whose meaning *depends* on a backfill,
and a test asserts none was added so that reasoning survives a future edit.

**Verified against real Postgres**, not just unit-tested — five properties, each run against a
throwaway `postgres:16-alpine`:

| Property | Result |
|---|---|
| Blank DB, no `agents` table (ALEMBIC-FRESH-DB-1) | skips cleanly, no error |
| Existing table with a row | both columns added, row preserved, `metadata` NULL |
| Re-run of `upgrade()` | idempotent no-op (`NOTICE: … already exists, skipping`) |
| JSONB genuinely queryable | `WHERE metadata->>'workspace'='w1'` returns the row |
| `downgrade()` then re-run | drops both, and is itself idempotent |

The blank-DB guard is the one that matters: `ADD COLUMN IF NOT EXISTS` alone is **not**
sufficient, since `ALTER TABLE missing_table` still raises `UndefinedTable`, and in compose
`alembic upgrade head` runs before the ORM `create_all` guard.

**Original filing follows. Verified true.** `AINDY/db/models/agent.py` declares exactly eight columns: `id`, `name`,
`agent_type`, `description`, `owner_user_id`, `is_active`, `memory_namespace`, `created_at`.
No JSONB, and no `updated_at`.

The motivating shape is that the identity should be the **role**
(`development.main-runtime`) with the vendor client as swappable metadata, so switching
provider does not look like a brand-new agent with no history. The durable half already works
— `id` and `memory_namespace` are provider-independent — but the swappable half has nowhere
structured to live, and encoding `provider=codex;workspace=...` into `description` is the kind
of thing that works until something needs to query it.

**Ask:** add `metadata JSONB` (nullable) and `updated_at`; expose both on whatever FR-12's
registration surface becomes, so re-registering with a new provider updates rather than
duplicating.

**Cost note for whoever builds it:** this touches `AINDY/db/models/`, so it triggers the
schema-contract protocol (bump `SCHEMA_CONTRACT_VERSION`, regenerate the baseline, update the
two frozen assertions) **and** wants an Alembic revision plus a
`RUNTIME_ALEMBIC_HEAD_REVISION` bump. Additive and nullable, so no backfill and no
`reconcile_backfill` declaration needed — unlike FR-8.

---

#### Also from this handoff: FR-8, FR-9, FR-10 confirmed closed by the app team

All three verified app-side on 2.0.1 and marked ✅ in their doc. FR-8 specifically: their
database needed no repair, **0** pre-existing accounts unverified, because the 12 grandfathered
by hand in their PR #190 are all `true`.

**Their FR-7 status is stale — flag on next contact.** The handoff still lists FR-7 as 🔴
net-new, but all four defects were fixed in 2.0.0 and are present in source:
`_policy_base_significance` (MEM-POLICY-KEY-1), `normalize_for_dedup` (MEM-DEDUP-TRACEID-1),
`_forced_capture_suppressed` (MEM-FORCE-UNGATED-1), and `blend_impact_with_significance` /
`SIGNIFICANCE_IMPACT_WEIGHT` (MEM-IMPACT-IGNORES-SIGNIFICANCE-1). They are running 2.0.1, so
the fixes are in their deployment; only the doc is behind.

Next available: **FR-22**.

---

### FR-1 — Connector registration hook + capability-enforced outbound I/O

**App ref:** `MASTERPLAN-CONNECTOR-RUNTIME-1` · **Status: SHIPPED 2026-07-17.** All three
parts built, opt-in/vacuous-by-default, no schema. Contract:
`docs/runtime/CONNECTOR_CONTRACT.md`.

**What shipped:**
1. **`register_connector(connector_type, handler, *, capability=None, description=None,
   overwrite=False)`** in `AINDY/platform_layer/registry.py` (+ `get_connector` /
   `iter_connectors`, `INPROC_CAP_REGISTER_CONNECTOR`, `validate_connector_handler` in
   `registry_contracts.py`). Symmetric to `register_job`; handler shape `handler(action,
   ctx)`; capability defaults to `outbound.<type>`. Dispatch via
   **`dispatch_connector(connector_type, action, …)`** in `connector_service.py` returning a
   normalized `{success,result,error[,denied]}` envelope; `ConnectorContext.call(...)` is
   the pre-bound authorized-call helper handed to the handler.
2. **`authorized_external_call(...)` + `OutboundCallDenied`** in `external_call_service.py`
   — grows the (observability-only) `perform_external_call` into a real chokepoint by
   composing the SAME stack `execute_tool` applies to agent tools:
   `enforce_capability_policy` (recipient/domain allowlist) → `enforce_capability_rate`
   (AGENT-HARDEN-8) → socket-level `egress_guard` scope → `capability_scope` for
   `resolve_secret` JIT vaulting (AGENT-HARDEN-9) → `perform_external_call` observability.
   Denials raise before any network I/O. `dispatch_connector` also wraps the whole handler
   in the egress + capability scope, so a connector using raw `urllib`/`smtplib` is still
   guarded.
3. **`outbound_http.outbound_request(...)`** — shared HTTP client with exponential-backoff
   retry (transport errors + 408/429/5xx) and a per-service `CircuitBreaker`, routed
   through `authorized_external_call` (authorization enforced once, outside the retry loop).

Enforcement is vacuous until a `CapabilityPolicy` (`AINDY_CAPABILITY_POLICIES`) / secret
scope (`AINDY_SECRET_SCOPES`) / `AINDY_EGRESS_ENFORCEMENT` is configured — registering a
connector changes routing only. Tests: `tests/unit/test_connector_registry.py` (16) +
`tests/unit/test_outbound_http.py` (4). Strong-form egress still converges with the MEB
program / IDEM-10. **App adoption target (unchanged):** delete the `if/elif` ladder in
`apps/automation/services/automation_execution_service.py::execute_automation_action`,
register each connector via the hook; delivery behavior unchanged, enforcement added.

### FR-2 — `register_nodus_workflow` hook for app-defined `.nd` workflows

**App ref:** `APP-DEBT-MIGRATED-1` · **Status: ALREADY SHIPPED (RTR-1, closed 2026-07-07).**
The exact hook exists: `register_nodus_workflow(name, source, kind=, version=,
capabilities=, owner_class=, provenance=, overwrite=)` —
`AINDY/runtime/nodus_workflow_registry.py`, called from the manifest/extension load path
in `registry.py:1711`. Symmetric to `register_flow`, reachable from the intent-execution
path. Supporting surface: DB model `nodus_workflow.py`, migration `0006`, router
`nodus_flow_router.py`, contract `docs/runtime/NODUS_WORKFLOW_CONTRACT.md`, tests
`test_nodus_workflow_registry.py`. **Action: none in runtime — notify app team it exists;
app adopts behind its `register_flow_strategy("reasoning", …)` seam per the contract doc.**

### FR-3 — Next-Action engine: record-first → autonomous pre-dispatch

**App ref:** `INFINITY-RUNTIME-1` Gap 4 · **Status: CORE SHIPPED (Deliverable C, #213,
v1.6.2, 2026-07-09) — narrow delta remains.** The handoff describes the runtime as still
record-first-only; it is not. `core/next_action_dispatch.py` `maybe_act_on_next_action`
already provides the bounded, opt-in autonomous-acting half (flag `AINDY_NEXT_ACTION_ACTING`
default off; chain-depth cap; approval gate + admission reuse; app-sourced
`trigger_execution` only). See the INFINITY-RUNTIME-1 entry above for the full description.

**Remaining delta vs. the ask:**
- (a) Broaden acting verbs beyond `trigger_execution` (`retry`/`schedule_follow_up`) —
  still a deferred INFINITY-RUNTIME-1 follow-up (touches RetryPolicy / scheduler semantics).
- (b) **App-consumable dispatch-outcome contract — SHIPPED 2026-07-17.** New un-prefixed
  ledger event **`SystemEventTypes.NEXT_ACTION_DISPATCHED`** (`next_action.dispatched`) +
  `core/next_action.py::emit_next_action_dispatched` + the `DISPATCH_DISPOSITIONS` contract.
  Every app-sourced `trigger_execution` candidate (once acting is enabled) emits exactly one
  outcome event, parented to its `NEXT_ACTION_CHOSEN` via `parent_event_id`, with a canonical
  `disposition`: decision stage (`dispatched` / `declined_no_objective` /
  `declined_chain_depth` / `declined_admission` / `declined_enqueue_error` / `declined_error`)
  and resolution stage from the follow-up job (`followup_executed` / `followup_pending_approval`
  / `followup_create_failed`, carrying `followup_run_id` + `followup_status`). Pre-candidate
  no-ops emit nothing. `execution.py` threads the chosen event id as the dispatch parent. The
  app reads the `NEXT_ACTION_CHOSEN → NEXT_ACTION_DISPATCHED` chain from the ledger. Frozen-hash
  baseline regenerated (`tests/baselines/system_event_contract.json`, `3389c3b6…`). No schema
  change (SystemEvent already carries `parent_event_id` + JSON `payload`). Tests:
  `tests/unit/test_next_action_acting.py` (+8 outcome cases). Contract doc:
  `docs/runtime/INFINITY_LOOP_AUDIT.md` (Gap 4).
- (c) Flip `AINDY_NEXT_ACTION_ACTING` after app soak (unchanged).

### FR-4 — Docs relocation: Bucket A + runtime half of `INVARIANTS.md`

**App ref:** `DOCS-MIGRATION-2` · **Status: ALREADY SATISFIED — no work required.** Verified
2026-07-17: FR-4 was completed by this repo's **DOCS-BUCKET-A-1** migration on
2026-06-27/28, ~3 weeks *before* the handoff was written (2026-07-17); the handoff was
authored without visibility into it (same stale premise as FR-2 / FR-3). Every FR-4 item is
present and git-tracked with frontmatter:

- **Bucket A relocate-as-is:** `docs/architecture/{DATA_MODEL_MAP,MODEL_OWNERSHIP_POLICY}.md`
  (DATA_MODEL_MAP surgically runtime-scoped, DOCS-BUCKET-A-1 residual 1),
  `docs/platform/governance/{AGENT_WORKING_RULES,ERROR_HANDLING_POLICY,CHANGELOG}.md`,
  `docs/tutorials/{index,01-memory-driven-workflow,02-event-driven-automation,03-scheduled-execution}.md`
  (the "four tutorials" = 3 + index; the pre-split archive itself has no 4th/Nodus tutorial —
  WAIT/RESUME is covered as the Nodus `event.wait()` builtin in tutorial 2).
- **Runtime half of `INVARIANTS.md`:** authored at `docs/platform/governance/INVARIANTS.md`
  ("runtime-owned half" — PostgreSQL/UTC/session-isolation/memory-graph/embedding/schema-guard
  invariants, enforcement sites re-verified), cross-linked to the app-owned half in
  `aindy-apps-monolith` (DOCS-BUCKET-A-1 residual 4).

**No relocation to perform** — and DOCS-BUCKET-A-1's last deferred residual (the optional
runtime/app editorial split of `ERROR_HANDLING_POLICY.md`) was also completed 2026-07-17, so
**DOCS-BUCKET-A-1 is now CLOSED**. App-side adoption (per the handoff) is non-functional:
update the reciprocal cross-links + author the app-side error-handling companion, both of
which the app repo owns. Tracked in full under **DOCS-BUCKET-A-1** below.

### FR-5 — `run_nodus_workflow` cannot invoke app callables (NEW 2026-07-18)

**App ref:** `APP-DEBT-MIGRATED-1` (Nodus-native reasoning). **Verified real** (unlike the
original handoff's stale premises): a `.nd` launched via the public `run_nodus_workflow`
could not reach app logic through *either* VM surface —
- `call_tool("<app tool>", …)` → fail-closed `"tool execution requires a capability token"`
  (`nodus_worker.run_agent_tool`), and `run_nodus_workflow` exposed no token param; and
- `sys("sys.v1.<app syscall>", …)` → `"Unknown syscall"` because the kernel `dispatch_syscall`
  resolves only `SYSCALL_REGISTRY`, never app-registered (`register_syscall`) syscalls.

The app asked for either fix; decision **2026-07-18: build both** — (a) now, (b) as a follow-up.

**(a) — capability-token threading. ✅ SHIPPED 2026-07-18.** `run_nodus_workflow` gains paired
`capability_token` + `run_id` params. When present they thread into flow state as
`execution_token` / `agent_run_id` — the **same proven keys the agent path uses**
(`nodus_execution_service.py:496`, read at `nodus_adapter.py:145,684`) — so the `nodus.execute`
node hands them to the `call_tool` seam and `execute_tool`/`check_tool_capability` enforce the
token per tool (unchanged; fail-closed preserved). The token binds to `run_id` + `user_id`, so
both are required together (guarded with a `ValueError`). Also folds in the previously-dropped
`initial_state`. Pure additive threading — no kernel change, no new enforcement surface.
Contract: `NODUS_WORKFLOW_CONTRACT.md` §8.1. Tests: `test_nodus_workflow_registry.py` (+5).

**(b) — app syscalls reachable from the VM `sys()`. ✅ SHIPPED 2026-07-18 — corrected
diagnosis.** The handoff framed this as "make `dispatch_syscall` resolve app-registered
syscalls (they're in a separate registry)". **Verify-first found that wrong:** apps register
syscalls via the **kernel** `register_syscall` (`syscall_registry.py:1813`) with full metadata
(`capability="analytics.read"`, `input_schema`) **into `SYSCALL_REGISTRY`** — which
`dispatch` already consults. The real cause is a **subprocess plugin-load gap** (sibling to
PLANNER-SUBPROC-1): the worker's `sys()` seam went straight to `dispatch_syscall` with **no
plugin-load entry point**, while `execute_tool` (the `call_tool` seam) lazily calls
`_ensure_tools_loaded()` → `load_plugins()` → each app's `bootstrap()` (which registers its
syscalls). So a `sys()`-only workflow dispatched against an unpopulated registry →
`"Unknown syscall"`. **Fix:** the new module-level `nodus_worker.dispatch_worker_syscall`
runs `_ensure_tools_loaded()` (idempotent, lazy — only when `sys()` is used, so no tax on
tool-only/pure-script runs) before dispatching. Enforcement is **unchanged and real** — the
app syscall keeps its declared capability, which the worker's `dispatch_syscall` grants via
`_infer_dispatch_capability` (e.g. `get_kpi_snapshot` → `analytics.read`) and the dispatcher
enforces; no kernel change, no new backdoor. Tests: `test_nodus_sys_dispatch.py` (4, incl. the
load-before-dispatch ordering). End-to-end app-syscall resolution is app-side PG-tier
integration (`test_nodus_vm.py`). Key files: `AINDY/runtime/nodus_worker.py`
(`dispatch_worker_syscall` / `_sys_dispatch`), `AINDY/agents/tool_registry.py`
(`_ensure_tools_loaded`).

**App adoption (once both land):** rewrite `reasoning_apply_v1.nd` to call
`reasoning.evaluate` (under a) or a new app syscall (under b), behind
`AINDY_REASONING_NODUS_NATIVE` (default off), normalizing `nodus_output_state` to the existing
recommendation envelope; PG-tier integration test.

### FR-6 — Self-service password management (NEW 2026-07-31)

**App ref:** surfaced in the app-side KPI-dashboard walk (2026-07-31), sibling of that
walk-log's first-admin-bootstrap finding. **Verified real** against the live OpenAPI on
`aindy-runtime==1.10.2` and against source: the entire auth surface was four routes
(`register`, `login`, `logout`, `admin/invalidate-sessions/{user_id}`). No forgot-password,
no reset-token, and — the sharper gap — **no change-password**, so even a signed-in user
could not rotate their own credential. The only way to set a password was a direct
`UPDATE users SET hashed_password` against Postgres, which is what the app team had to do to
restore admin access. Runtime-owned by construction: `/auth/*` is in the app's
`RUNTIME_OWNED_PREFIXES`, and there is no `register_*` hook that lets an app add an auth route.

The app filed this as three sliceable items. Item 1 has no delivery dependency and shipped
alone; items 2+3 are one unit and are the open remainder.

**Item 1 — `POST /auth/password/change`. ✅ SHIPPED 2026-07-31.** Authenticated, Bearer-JWT
only (a platform API key has no password to rotate — the same guard `logout` applies).
`change_user_password` in `AINDY/services/auth_service.py` verifies the current password,
enforces `MIN_PASSWORD_LENGTH` (8) and new-≠-current, writes the new hash, and bumps
`token_version` so every session is invalidated. Returns a freshly-versioned token in the
**same shape as `/auth/login`** (inside the canonical envelope the ui-kit unwraps), so the
caller stays signed in while other sessions are cut and a client reuses its existing
token-store path. Neither password reaches `input_payload` or the emitted
`auth.password.changed` event — both are trace-logged surfaces; there is a test asserting it.
Extracted `bump_token_version()` (the `% 32767` SMALLINT wrap was duplicated at two existing
call sites). Tests: `tests/unit/test_auth_password_change.py` (14 — service rejection matrix
+ real-HTTP route contract), route added to the must-stay-served list in
`test_cross_repo_compatibility.py`. No schema change.

**Note on scope:** `MIN_PASSWORD_LENGTH` is enforced on *change* only. `register_user` has
never applied a password policy; adding one there would start rejecting existing callers, so
it stays a separate decision — see the open item below.

**Items 2+3 — `POST /auth/password/forgot` + `POST /auth/password/reset`. OPEN — delivery
decision answered 2026-08-01, one structural question left (see below).**
Issue a time-boxed, single-use reset token for an email; consume it, set the new password,
invalidate sessions. Both reuse `hash_password` / `verify_password` and the `token_version`
bump item 1 already established, so the auth logic is the small half.

**Why deferred rather than built with item 1 — the blocker is delivery, not auth.** A reset
token is worthless unless it reaches the user, which needs an email channel. That is
**FR-1** territory (`register_connector` + `authorized_external_call`), and it forces a
design decision the runtime should not make unilaterally:
- **(a)** the runtime sends the mail itself through an `email` connector — needs a
  registered connector, a `CapabilityPolicy`, and secret-broker credentials, all of which are
  currently vacuous-by-default; or
- **(b)** the runtime returns the token and the **app** delivers it — smaller runtime
  surface, but it puts a live credential-reset token in an HTTP response body, so it is only
  safe behind an admin/service-authenticated caller, not the public forgot endpoint.

### ✅ App team answered 2026-08-01 — **(a), the runtime sends it**

The delivery question is settled. Their reasoning, recorded so it is not re-litigated:

- **(b) would not actually deliver FR-6.** The gap FR-6 exists to close is *a user who forgot
  their password has no recovery path*. An endpoint a locked-out user cannot call does not close
  it — we would ship (b), still lack the feature, and be left permanently guarding a
  token-minting route.
- **The email channel is wanted regardless** (order/payment notifications have the same
  dependency), so under (a) the cost is paid once by the layer that owns egress policy.
- **(b) moves the security boundary to the weaker side** — the token would cross a process
  boundary into a layer that does not own auth.

**Their positions on the sub-questions** (offered as defaults, explicitly ours to overrule):

| Question | App position | Assessment |
|---|---|---|
| Token storage | Stateless signed token carrying `user_id` + `token_version` | **Agree.** Single-use falls out by construction — the reset bumps `token_version`, so a consumed token no longer verifies. No table, no migration, no cleanup job. |
| Single-use | Falls out of the above | **Agree** — replay fails without a revocation list. |
| TTL | 30–60 minutes | **Agree.** |
| Unknown email on `/forgot` | Always 200 | **Agree** — matches our own read; otherwise it is an enumeration oracle. |
| Rate limit | 3/minute per IP **and** per email | **Agree.** Stricter than `/change`'s 5/minute because `/forgot` is unauthenticated and is the cheapest endpoint to abuse for mail-bombing. |

### ⚠️ Unresolved by that answer — who owns the email channel?

**(a) is under-specified, and the gap is structural.** `register_connector` is a hook for *apps*
to register into; **the runtime ships no `email` connector** (verified 2026-08-01: no connector is
registered anywhere under `AINDY/`). So "the runtime sends it" currently has nothing to send with.

Three shapes, and this is a runtime call:

1. **Runtime ships its own minimal SMTP sender** (config-driven `AINDY_SMTP_*`), routed through
   `authorized_external_call` so egress policy and secret-brokering still apply. Auth stays
   self-contained; a `platform-only` deployment can reset passwords. Cost: the runtime owns a
   mail channel it did not previously have.
2. **Runtime dispatches an app-registered `email` connector**, with `/forgot` returning
   503/disabled when none is registered. Cheapest, but **inverts the split** — a runtime-owned
   auth flow would depend on an app registering something, and password reset would be
   unavailable in any runtime-only deployment. That conflicts with the "runtime boots clean
   without plugins" contract.
3. **Hybrid** — dispatch a registered `email` connector if present, else fall back to built-in
   SMTP config.

**Recommend 1 or 3.** The app team's own argument for (a) — the token never leaves the runtime,
and the layer owning egress pays the cost once — argues for the runtime owning the channel.
Option 2 satisfies the letter of (a) while reintroducing the dependency they chose (a) to avoid.

### ✅ Sub-item CLOSED 2026-08-01 — password policy applied to `register_user`

`register_user` now rejects passwords under `MIN_PASSWORD_LENGTH` (8) with 400. Both paths that
set a password share the one constant, and a test asserts `register_user` references it rather
than a literal, so they cannot silently diverge into a strong path and a weak one.

**Decision record.** The app team asked for it, arguing zero migration cost because their
deployment has no production users. That argument does not generalise — this is a published PyPI
package, so the change reaches every consumer — and the objection was raised. **Owner overruled
it deliberately:** a security floor deferred indefinitely is not a floor, and downstream callers
adjusting is an accepted cost. Shipped unflagged on that basis.

**Blast radius, narrower than "breaking" suggests.** No stored password is invalidated and login
is untouched; only *new* registrations under the length are rejected. The realistic casualty is a
seeding/fixture/smoke script that drives `POST /auth/register` programmatically.

**Not configurable, by design** — a floor an operator can switch off is not a floor.

**Ordering note:** the length check runs *before* the duplicate-email lookup. It needs no DB
round-trip, and it means a short-password request against a taken email returns 400 rather than
409, so an invalid-password caller is not told whether the email exists.

**Adjacent finding, NOT addressed:** `POST /auth/register` still returns **409 "Email already
registered"** for a valid-password duplicate — an account-enumeration oracle on the registration
path, the same class of issue both sides agreed `/forgot` must avoid by always returning 200.
Fixing it changes a long-standing public response contract, so it needs its own decision.

**Trigger to build:** resolve the email-channel ownership question above (1/2/3), then build.
The auth half is small and fully specified now; delivery is the whole remaining risk. **App-side adoption for item 1 (available now):** an in-app
"Change password" control calling the endpoint — and it **must** store the returned token,
since the change invalidates the caller's existing one (recorded in `UI_CONTRACT.md` /
`SDK_CONTRACT.md`).

## AGENT-HARDEN-* — Agent-framework safety/resilience hardening

**Source (2026-07-05):** a skeptical self-assessment of the runtime + apps against an
external "Real Agent Framework" spec. The systems backbone (capability-gated syscall
kernel, durable WAIT/RESUME + rehydration, EffectRecord idempotency, tiered sandbox,
pgvector memory, KeyRing rotation, cross-repo contract freeze) graded strong; the gaps
below are the safety/security/resilience surfaces that separate "a runtime that runs
agents" from "a runtime you'd let an agent run **unattended** in production." Each rides
primitives that already exist — these are wiring/layering tasks, not from-scratch builds.
All five are **runtime-owned** (none is app-layer). Ordered by value ÷ effort.

**Not tracked here (already scoped elsewhere):** MCP is `ECOGAP-4` — G4a (capability-gated
egress + secret-broker, the runtime-owned trusted half, P1) and G4b (concrete MCP/A2A wire
adapters, plugin-layer, P2). The `call_tool` seam is the boundary G4a would formalize.
Negotiation schemas / typed planner-executor-verifier-supervisor roles are frontier
multi-agent work (RTR-4 delegation core exists; formal protocol deferred until a product
need, not a checkbox).

### AGENT-HARDEN-1 — Emergency stop / cooperative cancel for in-flight agent runs

**Status:** CLOSED (2026-07-05). Operator kill switch shipped.

`sys.v1.agent.cancel` (capability `agent.cancel`, `kernel/syscall_registry.py`
`_handle_agent_cancel`) flips a non-terminal `AgentRun` to the new terminal
`cancelled` state (`AgentRunStatus.CANCELLED`, `kernel/condition_codes.py`) via an
atomic CAS from `{pending_approval, approved, executing, waiting, delegated}`,
commits it, clears `wait_state`, and emits an `AgentEvent`/SystemEvent `CANCELLED`
(added to `AGENT_EVENT_TYPES`). Already-terminal runs are an idempotent no-op;
cross-tenant cancels are denied. The VM-backed segment chain
(`runtime/nodus_execution_service.py` `_execute_agent_segment_chain`) checks for
the cancel at **two segment boundaries** — before a segment runs (halt before the
next tool) and after it returns (a cancel that landed mid-segment is honored, not
clobbered by the completed/failed/waiting write). A cancel on a parked (`waiting`)
run is enforced by the existing resume claim (`WHERE status='waiting'`), so no
later event revives it. `"cancelled"` is now frozen in `_STABLE_AGENT_RUN_STATUSES`
(cross-repo contract). `SYSCALL_REGISTRY_MIN_COUNT` 18→19.

**Remaining gap:** cooperative cancel is wired into the opt-in **nodus_vm** backend
(`AINDY_AGENT_EXECUTION_BACKEND=nodus_vm`). The cancel syscall still transitions the
terminal state for the default AGENT_FLOW DAG backend (and blocks a parked run's
resume), but mid-flight interruption of the AGENT_FLOW Python DAG at node
granularity is not wired — deferred until that backend is retired in favor of the
VM path (RTR-1). Tests: `test_syscall_agent_cancel.py` (handler) +
`test_agent_vm_execution.py::test_vm_cancel_*` (boundary halt + resume-prevention
close trigger). Docs: `SYSCALL_REFERENCE.md`.

### AGENT-HARDEN-2 — Cryptographic capability-token integrity (replace unkeyed SHA-256)

**Status:** CLOSED (2026-07-05). The forge hole is closed.

`capability_service.py` `_token_hash` is now **HMAC-SHA256 keyed on the auth `KeyRing`
secret** (via new `auth_service.signing_key()` / `verification_keys()` accessors), replacing
the unkeyed SHA-256 that anyone able to recompute a hash over the public fields could forge.
Minting uses the active key; `validate_token` verifies against active + previous key within the
rotation grace window (`hmac.compare_digest`, constant-time), mirroring the JWT multi-key
verify. `_token_hash` keeps its signature (mint/refresh call sites unchanged); a new
`_token_hash_matches` drives verify. Token dict shape is unchanged (`token_hash` is still a hex
string), so no cross-repo/SDK contract impact. Falls back to the process `SECRET_KEY` if the
KeyRing can't be imported (keeps mint/verify symmetric in embedded contexts).

**Migration caveat:** tokens minted under the old unkeyed scheme fail HMAC verification after
deploy — in-flight `approved`/`waiting` runs carrying a pre-deploy token (TTL 24h) must be
drained or re-approved across the upgrade. Deliberate: accepting the legacy hash during a grace
window would leave the forge hole open, defeating the fix.

**Remaining (optional) upgrade:** symmetric HMAC proves integrity, not *identity* — any holder
of the runtime secret can mint. Ed25519 asymmetric mint/verify for a true signed agent identity
is deferred (no consumer needs it yet). Tests: `test_capability_token_integrity.py` (keyed MAC,
legacy-hash rejection, tamper detection, rotation grace, KeyRing-down fallback);
`test_capability_token_refresh.py` still green (real-KeyRing round trip). Docs: `SECURITY_MATRIX.md`.

### AGENT-HARDEN-3 — Reversible actions / compensating-undo log

**Status:** CLOSED (2026-07-05). Compensating-undo engine + audit log shipped.

`SyscallEntry` gains an optional `compensate` hook (+ `reversible` property);
`register_syscall(..., compensate=...)` passes it through. New append-only
`effect_reversals` table (`db/models/effect_reversal.py`) is the audit log. The engine
`core/effect_compensation.py` `undo_run_effects(run_id, db, context)` resolves the run's
`ExecutionUnit`, walks its **successful** `EffectRecord`s newest-first, and for each invokes
the owning syscall's compensator — recording one `effect_reversals` row per effect as
`reversed` / `irreversible` (no compensator declared → surfaced, not skipped) / `failed`
(compensator raised; other effects still processed). Exposed as `sys.v1.agent.undo`
(capability `agent.undo`, tenant-scoped). `SYSCALL_REGISTRY_MIN_COUNT` 19→20. The compensator
receives the effect's recorded outcome (`result_payload`/`external_receipt`/ids) — the original
input is not retained by design, so compensators key off the result.

**Schema:** new model → `SCHEMA_CONTRACT_VERSION` 2026-07-04→2026-07-05, baseline regenerated,
two assertions in `test_runtime_schema_contract.py` bumped; migration `0008_effect_reversals.py`
(guarded per ALEMBIC-FRESH-DB-1: CREATE skipped on blank DB where FK parents are absent →
create_all bootstraps it). No built-in syscall ships a compensator yet, so all existing effects
report **irreversible** (honest + surfaced); attaching a real compensator (e.g. `memory.write`
→ delete) is incremental. **This is the rollback mechanism AGENT-HARDEN-6 (Verifier) invokes on
post-condition failure.** Tests: `test_effect_compensation.py` (reverse-order, irreversible
surfacing, compensator-failure isolation, success-only, tenant scope). Docs: `SYSCALL_REFERENCE.md`.

### AGENT-HARDEN-4 — Effect-simulation / true dry-run (shadow `call_tool` seam)

**Status:** CLOSED (2026-07-05) — **-4** (PR1+PR2) and **-4b** (PR3, virtual tool
environment) both landed.

Plan-preview is real — a plan is generated, risk-scored, and persisted as `pending_approval`,
shown in the apps `AgentApprovalInbox` with per-step + overall risk before any tool runs
(`agent_runtime/creation.py`, `planning.py`); `memory_ingest_service.py` has a genuine
`dry_run`. What is missing is **effect simulation**: previewing what each tool would *output /
change*, not just its name + args.

**PR1 (done) — the shadow `call_tool` seam.** `runtime/tool_simulation.py`
`simulate_agent_tool` mirrors `run_agent_tool`'s fail-closed capability contract but **never
executes the tool**: it runs the read-only `check_tool_capability` gate, returns a predicted
`{success, result, error}` (success=True when permitted so the plan keeps flowing; success=False
+ error when capability-denied), and emits a structured `would_write` intent
(`{tool, args, risk_level, capability_ok, capability_error, predicted_result, executed:False}`).
A `simulate` flag threads `NodusExecutionContext → subprocess payload → nodus_worker._call_tool`;
in simulate mode the worker collects intents into `simulated_effects`, returned via the worker
result → `NodusExecutionResult.simulated_effects` → `build_nodus_execution_summary`. v1
prediction is deterministic; a predictor model is the documented upgrade path (same seam). Tests:
`test_tool_simulation.py` (zero-execution proof via `execute_tool` guard, capability
ok/denied/error, adapter flag threading + effect parsing); verified end-to-end through the real
subprocess (a `call_tool` shadowed with `executed:false`, no side effect).

**PR2 (done) — `mode="simulate"` end-to-end + report persisted.** A `simulate` flag threads
the flow node (`nodus_adapter.py` `nodus.execute` reads `state["simulate"]`) →
`execute_nodus_runtime(simulate=…)` → `NodusExecutionContext.simulate` → subprocess. New
`simulate_agent_run` (`nodus_execution_service.py`) splits the plan and runs every tool segment
shadowed (WAIT boundaries ignored — the whole plan is previewed), collects `simulated_effects`
via `_extract_simulated_effects`, and persists `{simulated, steps, simulated_effects,
steps_total, effects_total}` under `run.result["simulation"]` **without changing run status**.
Exposed as `sys.v1.agent.simulate` (capability `agent.simulate`, tenant-scoped); reuses the run's
`capability_token` or mints a preview token so the report reflects real grants.
`SYSCALL_REGISTRY_MIN_COUNT` 20→21. No schema change (`run.result` is existing JSONB). Verified
end-to-end through the real subprocess (`execute_nodus_runtime(simulate=True)` → `call_tool`
shadowed, `executed:false`, no side effect). Tests: `test_agent_simulate.py` (report+persistence,
no status change, flag threading, token reuse/mint, tenant scope). Docs: `SYSCALL_REFERENCE.md`.
**Close trigger met.**

**PR3 (done) — AGENT-HARDEN-4b (virtual tool environment).** The shadow seam now accepts
`virtual_tools` = **fake tool implementations** (`{tool_name: {"result", "success"?, "error"?}}`)
so a rehearsal runs against a *simulated world*: a tool with a fake impl returns its scripted
output (downstream steps see realistic data; each effect is tagged `source:"virtual"`), others
get the deterministic placeholder (`source:"placeholder"`). Threads `virtual_tools`
`NodusExecutionContext → subprocess → nodus_worker._call_tool → simulate_agent_tool`, and up the
agent path via `simulate_agent_run(virtual_tools=…)` / `sys.v1.agent.simulate` payload
`virtual_tools`. Capability is still enforced first (a denied tool ignores its fake impl).
**Network isolation:** simulation executes **zero real tools** (invariant `executed:False`), so
no tool network egress is possible by construction; container-grade `--network none` remains the
existing guarantee on the Docker-sandboxed **extension** execution path (C2/C3) — running the
agent nodus_worker itself inside that container is a separate, larger integration, not required
for the zero-side-effect rehearsal. Tests: `test_tool_simulation.py` (virtual result/failure,
placeholder fallback, capability-gated) + `test_agent_simulate.py` (virtual_tools threading +
handler passthrough); verified through the real subprocess. **Relates to:** the predicted-effect
report is the same surface AGENT-HARDEN-6's post-condition checker consumes.

### AGENT-HARDEN-5 — LLM provider fallback chain

**Status:** CLOSED (2026-07-05). Cross-provider failover available via config.

`platform_layer/llm_client.py` gains `FallbackLLMClient` (tries an ordered chain of
already-breaker-wrapped provider clients; on `LLMCallError` — which subsumes
`LLMCircuitOpenError`, so an open primary breaker fails over rather than surfacing — it advances
to the next provider; a success short-circuits; if all fail the last error propagates) plus
`resolve_provider_chain()` / `get_llm_client_chain()`. The chain is config-driven via new
`settings.LLM_PROVIDER` (primary) + `LLM_FALLBACK_PROVIDERS` (comma-separated secondaries);
unknown providers are dropped, order preserved, de-duped. A single-provider chain returns the
provider client directly (unchanged behavior); providers that fail to construct are skipped so a
broken secondary never blocks a healthy primary. `get_llm_client(provider)` is unchanged.

**Adoption follow-up (not blocking close):** the fallback is available as a factory but call
sites still resolve a single provider — agent planning/`shared.py` and `embedding_service.py`
call `get_openai_client()` directly, and `planner_backends.py` backends are single-named. Wiring
those to `get_llm_client_chain()` is a separate, opt-in change (the spec scoped planner fallback
as optional); deferred to avoid destabilizing the planner path. Tests:
`test_llm_provider_fallback.py` (failover on error, open-primary-breaker → secondary used [close
trigger], chain resolution, factory wiring). Docs: `.env.example`.

**Suggested sequence:** AGENT-HARDEN-1 → 2 → 5 (three small, high-value PRs closing the
scariest safety/security/resilience blanks), then the 3 → 6 → 4/4b arc (undo + verify +
simulation), with 7–10 folded in opportunistically.

---

**AGENT-HARDEN-6..10 (added 2026-07-05)** came from grading the runtime against a second,
more architectural v1 blueprint (the "Plan → Dry-Run → Approve → Execute → Verify" plan).
The blueprint's 8-layer diagram maps ~1:1 onto the `AINDY/` layer model (validation of the
architecture); these five are the specific components that plan names which 1–5 did not.
The highest-leverage one is **-6 (the Verifier)** — it is the missing letter in the core
loop and the natural trigger for the -3 undo work.

### AGENT-HARDEN-6 — Verifier stage (post-condition check + verify→rollback)

**Status:** CLOSED (2026-07-05). The core loop now closes on Verify.

`core/verifier.py` is a rules-based post-condition checker: `extract_post_conditions(plan)`
maps **tool-step ordinal** → per-step `expects` (skipping WAIT steps, matching
`AgentStep.step_index`); `verify_post_conditions(post_conditions, step_results)` evaluates each
condition and returns `{ok, checked, failures}`. Condition forms: `{"status": "success"}` or
`{"field": "<dot.path>", "op": "<op>", "value": …}` over the step's `result` (ops: exists /
not_exists / eq / ne / contains / not_contains / gt / gte / lt / lte / truthy / falsy; unknown
op / type-incompatible / missing field or step **fail closed**).

Wired into the terminal-success block of `_execute_agent_segment_chain`
(`runtime/nodus_execution_service.py`): on a fully-run plan it verifies before marking
complete. Pass → `completed` + an `AgentEvent` `VERIFIED` (only when `checked > 0`). Fail →
terminal `AgentRunStatus.VERIFY_FAILED` (frozen in `_STABLE_AGENT_RUN_STATUSES`), `result` keeps
the `{steps, verify}` verdict, an `AgentEvent` `VERIFY_FAILED` is emitted, and the
**AGENT-HARDEN-3** compensators (`undo_run_effects`) roll back the run's reversible effects
(best-effort; irreversible ones are surfaced by undo). Runs on both the initial and the resumed
completion path (both read the persisted `run.plan`). No new model → no schema bump.

**No behavior change until authors opt in:** plans with no `expects` verify vacuously
(`checked == 0`), so existing runs complete exactly as before. Upgrade path: swap the rules
checker for a small verifier model. **Depends on AGENT-HARDEN-3 (#168)** for the rollback half —
shipped as a stacked PR. Tests: `test_verifier.py` (extract/ops/fail-closed) +
`test_agent_vm_execution.py::test_vm_run_verify_*` (pass→completed+VERIFIED,
fail→verify_failed+undo, no-expects→vacuous).

### AGENT-HARDEN-7 — Contract / record-playback integration tests

**Status:** PR1 done (2026-07-06) — LLM/embedding boundaries under recorded-cassette
contract tests; `respx` adopted. Remaining HTTP tools can be added incrementally.

The blueprint's "three rings" wants **contract tests for each integration** (VCR-style
record/playback fixtures). (Note: the original audit said `respx` was already a dep — it was
not actually declared; PR1 adds `respx==0.23.1` to `AINDY/requirements.txt` + pyproject `[test]`,
compatible with the pinned `httpx==0.28.1`.)

**PR1 — respx contract tests for the primary external boundary.** VCR-style cassettes under
`tests/fixtures/cassettes/` (recorded response shapes) replayed via `respx` (which intercepts
the openai SDK's httpx calls). `test_contract_llm_openai.py` freezes OpenAI **chat** and
**embedding** — asserting the request wire shape (URL, `Authorization`, model/messages/params
body) **and** response handling (assistant-text / embedding-vector extraction), plus a 500 →
`LLMCallError` path. `test_contract_llm_deepseek.py` freezes DeepSeek chat. **The DeepSeek
contract test surfaced a real bug:** `DeepSeekLLMClient` built the OpenAI SDK with no
`base_url`, so DeepSeek calls were sent to `api.openai.com`; fixed by setting `base_url` from
new `settings.DEEPSEEK_BASE_URL` (default `https://api.deepseek.com/v1`), and the test now guards
the endpoint. **Close trigger met** for the LLM/embedding integrations.

**Remaining (opportunistic):** the same respx cassette pattern for any first-party HTTP *tools*
as they land (`memory/embedding_service.py` rides the same OpenAI boundary already covered).

### AGENT-HARDEN-8 — Declarative per-capability policy (rate limits + allowlists)

**Status:** CLOSED (2026-07-06). Recipient + domain allowlists (PR1) and per-capability
rate limits (PR2) both enforced.

The blueprint's CAP descriptor carries `limits.rate` (e.g. `30/minute`) and
`recipients.allowlist` / domain allowlists **per capability**. Today enforcement is coarser:
per-verb capability checks (`syscall_dispatcher.py`) + per-EU/tenant quotas
(`kernel/resource_manager.py`) + a per-user tool auto-grant allowlist
(`capability_service.py`) + a coarse `egress_scope` label (`internal`/`external`) on tool
contracts. There is **no per-capability rate limit, recipient allowlist, or domain egress
allowlist**.

**PR1 (done 2026-07-06) — recipient + domain-egress allowlists.** `agents/capability_policy.py`
adds a `CapabilityPolicy(recipients, domains, rate)` descriptor + a process-wide registry
(`register_capability_policy` / `get_capability_policy`) and `enforce_capability_policy(caps,
args)` → `{allowed, violations}`. Recipient (email) and domain (URL host) targets are extracted
generically from the call args (no per-tool arg schema needed); a recipient/domain outside a
policy-bound capability's allowlist is denied. Allowlist matching: recipients by exact address or
`@domain`; domains by exact or subdomain suffix. Enforced in `agents/tool_registry.py`
`execute_tool` **after** the capability check, **before** the tool runs — emitting a
`capability.policy_denied` system event on violation. **Vacuous until a policy is registered**
(`has_capability_policies()` gate → zero behavior change / no overhead when unused). This
upgrades the coarse `egress_scope` label toward an enforced list and complements the SSRF
`validate_outbound_extension_url` blocklist. Tests: `test_capability_policy.py` (extraction,
allow/deny incl `@domain`/subdomain, vacuous, `execute_tool` integration). **Close trigger met**
for recipient/domain bounds.

**PR2 (done 2026-07-06) — per-capability rate limits.** `ResourceManager.rate_limit_hit(key,
limit, window_secs)` adds a generic fixed-window counter (shared Redis when available → enforced
across instances; thread-safe in-memory fallback otherwise; fail-open on backend error).
`capability_policy.parse_rate` parses `"N/period"` (s/min/hour/day) and `enforce_capability_rate(
caps, scope)` records one hit per policy-bound capability keyed by `cap × scope` (the tenant/user)
and denies once the window count passes the limit. Enforced in `execute_tool` **after** the
recipient/domain check (rate increments the counter, so only otherwise-permitted calls count);
denial emits `capability.policy_denied` (`kind:"rate"`). Tests: `test_capability_policy.py`
(parse_rate, deterministic fixed-window via `now=`, allow→deny per scope, execute_tool rate
integration). **Relates to:** AGENT-HARDEN-2 (token integrity). **Close trigger fully met** — a
capability can be granted with a declarative rate/recipient/domain bound the runtime enforces.

### AGENT-HARDEN-9 — Secrets broker (just-in-time retrieval)

**Status:** CLOSED (2026-07-06). Broker abstraction + backends + capability-scoped JIT
resolution wired at the tool seam — close trigger met.

The blueprint wants secrets pulled **just-in-time** from an OS keychain / Vault, never sitting
in a database. Secrets were process env vars; the plugin sandbox's `secret_injection: "none"`
is a posture, not a broker.

**PR1 — the broker seam.** `platform_layer/secret_broker.py`: a `SecretBroker` ABC +
`EnvSecretBroker` default that reads a **controlled `AINDY_SECRET_<NAME>` namespace** (not
arbitrary env vars, so it's a deliberate secret surface a prod backend swaps transparently via
`set_secret_broker`). `resolve_secret(name, *, capabilities, required_capability?)` is the JIT
entry point: it's **fail-closed** on a missing gating capability, missing secret, or backend
error, and the value is fetched at call time and returned to the caller **only — never
persisted** (not in the DB, not on the token, not in a result). Scoping via
`register_secret_scope(name, capability)` (or an explicit `required_capability`); ungated
secrets stay open in dev. **Deliberately NOT a syscall** — the dispatch envelope is trace-logged,
so a secret value must never transit it; resolution is an in-process call at the tool seam. Tests:
`test_secret_broker.py` (namespace isolation, capability gate allow/deny, fail-closed paths,
pluggability, `SecretRef` holds no value).

**PR2 (done 2026-07-06) — real backends + tool-seam scoping.** Backends: `FileSecretBroker`
(Docker/K8s mounted `<root>/<name>`, default `/run/secrets`, path-traversal-blocked),
`VaultSecretBroker` (HashiCorp Vault **KV v2** over `httpx` — no `hvac` dep, respx-contract-tested),
and `ChainSecretBroker` (ordered fallback, e.g. env→file→vault). **Close trigger met via the tool
seam:** `agents/tool_registry.py` `execute_tool` now wraps the tool invocation in
`capability_scope(<token allowed_capabilities>)`; a tool calls `resolve_secret(name)` (no caps
arg → ambient scope) and is gated by the run's grants — the secret is consumed inside the tool
and never returned to the script. A tool whose token lacks the gating capability is denied
(fail-closed). OS-keychain (`keyring`) is a trivial further backend given the ABC. Tests:
`test_secret_broker.py` (File incl. traversal block, Vault KV v2 via respx, Chain fallback,
ambient scope, and the `execute_tool` allow/deny close-trigger demonstration).
**Relates to:** AGENT-HARDEN-2, -8.

### AGENT-HARDEN-10 — Signed plugin bundles + SBOM

**Status:** CLOSED (2026-07-06). Real signing + trust registry + SBOM primitives (PR1) +
provenance wiring / profile enforcement (PR2). Close trigger met.

The blueprint wants signed plugin bundles (sigstore/cosign) + SBOM. Integrity was SHA-256
byte-comparison and `extension_provenance.py` hardcodes `"signing": {"status": "unsupported"}` —
no keys, no trust registry, no SBOM.

**PR1 — the signing foundation.** `platform_layer/extension_signing.py` implements real
**Ed25519** detached signatures (via `cryptography`, already a dep): `generate_keypair`,
`sign_digest`/`verify_digest` over a bundle's SHA-256 digest, `key_fingerprint`
(`sha256:<hex>` key id). A **trust registry** (`register_trusted_key`/`is_trusted`/
`trusted_public_key`) holds the public keys the host will accept. `verify_bundle_signature`
(fail-closed: unsigned / untrusted-key / bad-sig all denied) + `enforce_bundle_signature(profile,
…)` encode the policy: **production profiles refuse** an unsigned/untrusted bundle; dev/
single-instance allows unsigned but reports `verified:False`. `generate_sbom` emits a
CycloneDX-lite SBOM (component digests). Tests: `test_extension_signing.py` (sign/verify,
tamper + wrong-key rejection, trust registry, profile gate allow/deny, SBOM shape).

**PR2 (done 2026-07-06) — provenance wiring + profile enforcement.** A plugin bundle's declared
provenance may carry a typed `signature: {algorithm, value, key_id}` (`ExtensionSignatureDeclaration`).
`derive_plugin_artifact_provenance` now verifies it against the trust registry
(`_describe_and_enforce_signature`) and records a `signing` block (`verified` / `unverified` /
`unsigned`) in the provenance result. **Enforcement:** scoped to signature-required surfaces
(external-third-party) and gated by the operator opt-in `AINDY_REQUIRE_SIGNED_PLUGINS` — when set
on a **production** deployment profile, an unsigned/untrusted/invalid bundle **raises** (refused,
mirroring the existing integrity-mismatch refusal that already blocks load); default OFF so
existing first-party/dev plugins keep loading. `extension_provenance_policy()` `signing.status`
flipped `unsupported` → **`supported`** (`ed25519`) — a **public version-API contract** change,
so the two frozen assertions (`test_version_api.py`, `test_runtime_public_contract.py`) were
updated in lockstep with the actual capability so the advertised status stays honest. **Close
trigger met:** a bundle can be signed and the host refuses an unsigned/untrusted one in a
production profile. Tests: `test_extension_signing.py` (verified/unverified/unsigned recording,
production refuses unsigned + untrusted when enforced, dev/opt-out allow, valid-signed passes).

**Not tracked (still out of scope):** Slack/Teams chat approval surface (UI convenience — the
web `AgentApprovalInbox` + CLI/HTTP already cover approvals; add if a chat-ops need arises);
blue/green + canary skill deploys (infra/release-process, not a runtime primitive);
multi-agent principal roles and Intent DSL (frontier, deferred with RTR-4). MCP remains
`ECOGAP-4`.

## CLI-1 — Lazy settings getter deferred (post-1.0)

Status: Deferred — Low Priority

Settings() is called at module level in `AINDY/config.py` (line 316) and is load-bearing
for log initialization on the lines immediately below it. The 1.0.0 fix gave `DATABASE_URL`
a default of `""` so that import succeeds without configuration, but the module-level
instantiation remains. A proper fix (Option 1 from the CLI audit) would introduce a
`get_settings()` lazy getter and defer instantiation until first use, eliminating the
270+ module-level `settings.` call sites as a migration.

Why deferred: 279 usages across 36 files — not scope-appropriate for the 1.0.0 CLI fix.
The `DATABASE_URL = ""` default achieves the user-visible goal (--help works without env)
at zero consumer-side cost.

This pattern already required two workarounds in the 1.0.0 CLI fix:
1. `AINDY/runtime_only.py` uses module-level `__getattr__` to defer `from AINDY.main import app`
   so it doesn't pull in the database engine layer on `--help`.
2. `sandbox_verification_posture()` (in `health_service.py`) is guarded with try/except because
   `health_service` imports `AINDY.db` at module level.

Reopen triggers (any one is sufficient):
- A third "I had to add a try/except guard because a platform module imports settings
  transitively" instance surfaces. Two workarounds is a pattern; three is a signal the
  root cause needs addressing.
- CLI startup time becomes measurably slow — `Settings()` + log initialization run on
  every `--help` invocation including in CI hot loops.
- Multi-tenant or per-request config support requires settings isolation beyond a single
  module-level instance.

Resolution path: introduce `get_settings() -> Settings` that caches on first call; replace
all `settings.` call sites with `get_settings().`; gate log initialization inside a
`configure_logging()` function called from app startup, not module load.

---

## EFFECT-OUTCOME-UNKNOWN-1 — the runtime has no word for "dispatched, outcome unobserved"

**Status:** Open — P2. Filed 2026-08-22.

**Provenance — two design notes, neither of them this repo's:**
`OneDrive/…/Designs/NOTE_browser_automation_feasibility.md` (written against the `C:\codev`
sweep of 2026-08-19/20, aimed here) and
`C:\dev\Coding Language\docs\design\v5\03-outcome-ambiguity.md` (nodus-lang's answer to it).
**Read the second before acting on this entry** — its §5.3 phase ladder and §7 impossibility
proof are the reasoning, and neither is reproduced here. Answered from this side in that
document's §14.

### The finding, in this repo's terms

Both notes converge on the same claim: for a counterparty that is not transactional, the
achievable guarantee is **at-most-once dispatch with a recorded outcome**, where the outcome
may legitimately be *unknown*. **The runtime can express neither half.** Two closed
vocabularies, both binary:

| vocabulary | where | values | what is missing |
|---|---|---|---|
| execution guarantee | `syscall_registry.py:1923` | `frozenset({"AT_LEAST_ONCE", "EXACTLY_ONCE"})` | **`AT_MOST_ONCE` — zero occurrences repo-wide** |
| effect outcome | `db/models/effect_record.py:70-71` | `pending` / `success` / `failed` | **no `unknown`/`unobserved` — zero in the effect layer** |

**So the nodus note's fix cannot be applied as written.** It correctly catches that the browser
note contradicts itself — its §3 says at-most-once, its §6.1 says declare `EXACTLY_ONCE` — and
says to correct §6.1 to match §3. But `register_syscall` validates against a frozenset of two,
and **neither value is the right one**: one over-claims, the other under-claims, and the honest
label is unregisterable. *The vocabulary gap is one level below where either note placed it.*

★ **`EXACTLY_ONCE` would be a lie twice over, not once.** The nodus note argues the label is
"success-shaped" because a website never agreed to it. Measured here (`IDEM-11`, closed
2026-08-19): under contention **8 concurrent identical calls ran the handler twice**, degrading
to `AT_LEAST_ONCE` with a warning (`_count_gate("degraded")`). The label would misdescribe the
counterparty *and* our own gate.

### ★ `pending` cannot be borrowed for this

The obvious shortcut is to park an ambiguous effect at `pending` and call that unknown. Code
that already exists closes it: `_cleanup_expired_effect_records` warns on **any** pending row
older than one hour — *"may indicate stuck handlers; investigate action_ids"*
(`scheduler_service.py:563-576`). A correctly-recorded ambiguity would be indistinguishable
from a malfunction, and would page someone.

That is `EVENTBUS-PUBLISH-LATCH-1`'s exact shape — **one field meaning two things**, there the
operator kill switch and the give-up latch. Do not repeat it. (Pending rows are never deleted,
so nothing is *lost*; the failure is misclassification, not data loss.)

### Why the fix is cheaper than it reads

`EffectRecord.status` is `String(32)` with **no CHECK and no Enum**, and
`complete_effect_record(db, action_id, status, result_payload)` assigns the string
**unvalidated** (`effect_ledger.py:204-213`). **A fourth value needs no migration.**

That is the same structural fact `EFFECT-PARTIAL-1` already banked for its three-outcome
problem. **Two entries, one change** — settle the status vocabulary once, for partial *and*
unobserved, or they will diverge.

### What is live today, stated precisely

The runtime's authorized outbound boundary collapses the phase ladder. `outbound_request`
(`platform_layer/outbound_http.py:88-101`) catches **`httpx.HTTPError`** — the base class,
covering `ConnectError` (**knowably not dispatched**) and `ReadTimeout` (**the one true
ambiguity**) identically — wraps both in `TransientHTTPError`, and retries: `max_retries=2` by
default, **no method guard**. `_RETRYABLE_STATUS` also retries 500/502/503/504, which a POST
may have committed before returning. That is nodus §8.1's *"retry blindly, which is assuming
world A while behaving as though they had confirmed it."*

**Exposure, measured rather than assumed:** `outbound_request` has **no caller in `AINDY/` —
only `tests/unit/test_outbound_http.py`**. Email (`email_channel.py:193`) and registered
connectors (`connector_service.py:62`) call `authorized_external_call` **directly**, and that
function does **not** retry. So the blind retry is **shipped in a documented FR-1 client and
unused in-tree** — latent here, live for any consumer that adopted it.

**Promotion to P1 on any one of:** a consumer using `outbound_request` for a non-idempotent
method; a retry loop added to `authorized_external_call`; or the first syscall whose
counterparty is not transactional (a browser action being the motivating case).

### Cross-links — this is a corner of a shape, not a standalone item

| entry | relation |
|---|---|
| `EFFECT-PARTIAL-1` | **same column, one change.** Its three-outcome envelope and this fourth status are one vocabulary decision. Do not settle separately. |
| `CANCEL-REACH-1` | **blocks the phase the browser note calls most important.** Its §5 table names `release_on_cancel` *"the path most implementations skip — and the one that matters most here"*; cancellation is durable but never reaches an in-flight effect. The four-phase pattern is three phases here. |
| `EFFECT-PRECONDITION-1` | **a browser syscall un-defers it.** That entry is deferred because *"it needs an external mutable resource the runtime actually mutates, and there is no filesystem syscall and no `sys.v1.repo.*`"* — a browser is exactly that resource. ★ Its recorded answer (*"the version identity is whatever the external system's own mechanism produces — record it, carry it, refuse on mismatch, NEVER reimplement it"*) **is** nodus §7.4's pre-arranged trace. |
| `IDEM-11` | `EXACTLY_ONCE` already degrades under contention — see above. |
| `AUTHORITY-NEGOTIATION-1` / `approve_run` | **the browser note's *"you already have an approval inbox"* is half true.** `pending_approval` is on `AgentRun`: **pre-dispatch, run-level, whole-plan**. Reconciling an unknown outcome is **post-dispatch, effect-level**. Different surface, different time, different granularity. |

★ **Three independent derivations, not two.** The nodus note treats it as notable that
reserve → call → reconcile was reached twice (LiteLLM's spend governor, and its own domain
statement §4.1). It was reached a **third** time in this repo, from Aider's Git discipline, and
filed as `EFFECT-PRECONDITION-1` months earlier. Money, distributed-systems theory and version
control converged on *plant an attributable trace before you act*.

### Constraint on the planted trace, which neither note states

`compute_action_id(action_type, input_payload, scope)` (`core/execution_gate.py:70-77`) is a
SHA-256 of the **request**. A nonce planted to make an action attributable must therefore live
*inside* the payload, and so **changes the key**. Consequence: it must be minted **once**,
before the first dispatch, and reused across every retry — mint it per attempt and dedup breaks
silently. Consistent with the notes' intent-record-first design, but it rules out the obvious
implementation.

### Do not

- **Do not add `unknown` to the status set alone.** Without `AT_MOST_ONCE` in the guarantee
  set a syscall still cannot *declare* that unknown is a legitimate terminal outcome, and the
  status becomes a state nothing is permitted to reach.
- **Do not add `AT_MOST_ONCE` alone** — the mirror of the same error.
- **Do not overload `pending`.** See above.
- **Do not "fix" the outbound retry by widening the except clause.** The fix is *narrowing*:
  distinguish `ConnectError` from `ReadTimeout`, which httpx already does and which the current
  base-class catch throws away. **The phase distinction exists in the library and is destroyed
  at our boundary** — nodus §5.3 makes the same point about Playwright's exception types, which
  makes this two instances of one cause.
- **Do not build a browser driver to motivate this.** Three of the four blockers are runtime
  gaps that exist now; a driver would meet all three on its first mutating call. Order: status
  vocabulary (no migration) → guarantee vocabulary → `CANCEL-REACH-1` → an effect-level
  reconciliation surface. The driver is genuinely last, and genuinely a library.

---

## QUOTA-ACCRUAL-ORPHAN-1 — the dispatcher accrues resource usage that only the pipeline reaps

**Status:** Open — P2, but a **live functional break**, not a latent risk. Found 2026-08-22
while scoping `CLI-EXEC-SURFACE-1`; split out because the mechanism is not CLI- or MCP-specific.

### The mechanism

Resource accounting is split across two components that were never required to appear
together:

- **`SyscallDispatcher` accrues.** Step 4 of every dispatch calls
  `record_usage(context.execution_unit_id, {"syscall_count": 1, "wall_time_ms": …})`
  (`syscall_dispatcher.py:766-771`). `record_usage` → `record_cpu`/`record_syscall`, each of
  which **creates the `UsageSnapshot` when absent** (`resource_manager.py:862, 871, 882`).
- **`ExecutionPipeline` reaps.** `mark_completed` is called from
  `core/execution_pipeline/resources.py:107,121` and the flow engine's completion/failure
  paths (`runner_completion.py:180`, `runner_failure.py:39`) — and **nowhere else**, grepped
  across `AINDY/`.

**A caller that uses `dispatch_syscall` without `ExecutionPipeline` therefore accrues usage
that nothing ever clears.** Route handlers are fine: they run inside the pipeline. The gap is
every other dispatch path.

### The one caller that hits it today

`AINDY/platform_layer/mcp_server.py` has **zero** references to `ExecutionPipeline`, and its
handler calls `dispatch_syscall(name, args, user_id=...)` with no `execution_unit_id` and no
`trace_id` (`:188-196`). `dispatch_syscall` then builds the context with `run_id=""`
(`syscall_dispatcher.py:904-910`), so every call checks *and accrues* against the key `""`,
in the process-level singleton (`get_resource_manager()`, `resource_manager.py:984`).

### Executed, with a liveness control

`is_testing` is a pydantic **property** — patch it on the class or `check_quota`
short-circuits to `(True, None)` at `resource_manager.py:639` and proves nothing:

```
call 1: check_quota('') -> (True, None)      # no snapshot yet — the one free call
call 2: check_quota('') -> (True, None)
bucket after 3 calls: {'eu_id': '', 'tenant_id': '', 'wall_time_ms': 15, 'syscall_count': 3}
...after 108 accrued syscalls:
check_quota('') -> (False, "RESOURCE_LIMIT_EXCEEDED: eu '' exceeded syscall_count limit (108 > 100)")
control (eu-1, over cap) -> (False, "... eu 'eu-1' exceeded syscall_count limit (105 > 100)")
```

The control matters: without it, `(True, None)` twice is equally consistent with
*"`check_quota` is a no-op"*, which would be a different (and smaller) finding.

Three stages, in order:

1. **The first id-less call escapes the quota** — no snapshot to exceed.
2. **Every later id-less call shares ONE global bucket keyed `""`**, accumulating across
   callers, sessions and tools. A per-*execution* budget silently became a per-*process* one,
   with `tenant_id=""` — so `can_execute("")` is also being asked about a tenant that is not
   one.
3. **Past `MAX_SYSCALLS_PER_EXECUTION` (100) it trips and never recovers.** Every subsequent
   id-less call is refused with `RESOURCE_LIMIT_EXCEEDED: eu '' exceeded syscall_count limit`.
   `MAX_WALL_TIME_MS` (300 000) accrues identically — a second, slower path to the same
   lockout.

### Why it matters

`aindy-runtime mcp-server --transport stdio` is a **long-lived** process. A session exceeding
100 tool calls hits a hard stop, and the message cites an execution unit that does not exist —
indistinguishable from a real quota breach, and not obviously connected to uptime.

**Redis makes it worse, not better.** `_backend_get_syscalls("")` is a shared key, so the
bucket spans every instance in the deployment rather than one process — the lockout becomes
deployment-wide and outlives any single restart.

**P2 only because exposure is bounded by adoption** — the MCP server is opt-in, behind the
`[mcp]` extra, read-only by default, and spawned deliberately. The mechanism is not weak.
**Promotion triggers (any one):** MCP server use becomes routine; a second non-pipeline
`dispatch_syscall` caller appears (a CLI is the obvious candidate — see
`CLI-EXEC-SURFACE-1`); or the deployment runs Redis, which widens the blast radius from one
process to all of them.

### The fix, and the shape to avoid

**The rule: a caller that uses `dispatch_syscall` must own an `ExecutionUnit` lifecycle —
claim it and reap it.** Not for metrics; so the quota has a subject that is *its own* and that
someone eventually clears.

- **Do not "fix" it by making `check_quota("")` return early.** That restores the *first*
  stage (a caller with no budget at all) and deletes the evidence that the accrual is
  unreaped. The empty key is a symptom.
- **Do not give the dispatcher its own reaper.** Accrual and reaping would then live in two
  places with no shared transaction, which is `ORCHESTRATOR-SPLIT-1`'s failure mode.
- **Consider making the dispatcher refuse an empty `execution_unit_id` outright** — it is
  never legitimate, and failing loudly at the seam beats accruing into a bucket named `""`.
  Check first what else dispatches without one; `dispatch_syscall`'s signature makes
  `execution_unit_id` optional, so the answer may not be only MCP.

★ **Method note, worth more than the bug.** This was filed wrong first: read from source,
labelled "measured", and stated as *"the quota is vacuous for an id-less caller"* — the exact
opposite of what happens. It survived into a draft of
`docs/runtime/CLI_EXECUTION_SURFACE_SCOPE.md`, a document that cites the
`trusting-a-green-check` catalogue, and it is catalogue **variant 7** (asserting the source,
not the behaviour). Running it took four minutes. The wrong version is preserved in that
doc's §3 on purpose.

---

## CLI-EXEC-SURFACE-1 — the runtime can be administered from a terminal, never asked to do anything

**Status:** Open — P2. Filed 2026-08-22. Scope doc:
`docs/runtime/CLI_EXECUTION_SURFACE_SCOPE.md`.

**This is a lens, not a defect.** Nothing is broken. What is filed is that a whole surface was
never made the subject of a question, and three existing entries are facets of it that were
filed separately because the lens was missing.

### The finding

Every one of the eight `aindy-runtime` subcommands administers the server — `init`, `serve`,
`sandbox`, `bootstrap-schema`, `mcp-server`, `memory reembed`, `memory prune-cascade-debris`,
`auth promote-admin`. **None executes anything.** No `run`, no `agent run`, no `flow run`, no
`syscall`. Verified against the `add_parser` table and the `args.command ==` dispatch block in
`AINDY/runtime_only.py`.

The direction is the point. `nodus` — *below* the runtime — ships ~27 commands including
`run`, `repl`, `workflow run|list|resume`, `goal-run`, `snapshot`/`restore`. `claw` — *above*
it — ships a daemon lifecycle plus `agents`, `workspace`, `weave`. **The runtime in the middle
is the only level that cannot be asked to do work.** So a person at a prompt either stands up
HTTP or drops to `nodus run`, which reaches the interpreter without passing the dispatcher,
the capability token, the effect ledger, the egress guard or the quota. **The terminal path
routes around the runtime** — `FLOW-PARALLEL-1`'s shape ("apps needing parallelism route
around the flow engine"), and `GUEST-CONFINE-1`'s.

### ★ Why nine audits missed it, and the number that shows it

Nine comparative audits examined systems that are *all* terminal-driven (Codex, Claude Code,
Aider, SWE-agent, OpenClaw/Pi, GPT Engineer, …). `COMPARATIVE_RESEARCH_INDEX.md` has **zero**
occurrences of "CLI" in 482 lines. The split across the audit documents is the mechanism:

| Document class | "CLI" mentions |
|---|---|
| *Architecture* audits — describing **them** | 36, 13, 12, 11, 7, 4 |
| `*_ON_AINDY_RUNTIME_*` / `*_AINDY_LENS_*` — turning it back on **us** | 1, 0, 0, 3, 1, 2 |

The CLI was on both sides of every comparison and was the subject of neither. The cause is a
mismatch nobody named: those systems are **app-level** tools whose CLI *is* the execution
surface (`codex exec`, `aider`, `claude -p`), and it was being compared against our
**runtime-level administration** CLI. Two different questions wearing the same word.

*(Method caveat, stated because the conclusion rests on it: the counts above are keyword hits
in the top-level audit `.md` files plus spot-reading, not a full read of nine folders. The
zero in the index and the empty subcommand table are hard facts; "never asked of us" is a
strong inference from a proxy.)*

### ★★ Scoping this found a live bug — split out as `QUOTA-ACCRUAL-ORPHAN-1`

**Read that entry, not this paragraph.** In one line: `SyscallDispatcher` **accrues** resource
usage and only `ExecutionPipeline` **reaps** it, so any caller that dispatches outside the
pipeline accrues forever. The one caller doing that today is `mcp-server`, which dispatches
with no `execution_unit_id`; it gets one free call, then a single process-wide bucket keyed
`""`, then a permanent `RESOURCE_LIMIT_EXCEEDED` past 100 syscalls.

**Why it belongs in this entry's story at all:** it is the hole a CLI built the obvious way
inherits on day one, and it is the reason the scope doc's answer to *"pipeline or beside it?"*
is not a judgement call. **A CLI that executes must claim and reap an `ExecutionUnit`.**

It also carries the method lesson this entry earned: the bug was filed **wrong first** — read
from source, labelled "measured", and stated as the opposite of what happens — inside a
document that cites the `trusting-a-green-check` catalogue. Variant 7, committed while writing
about variant 7.

### What this subsumes

Three open entries are facets of "the runtime has no terminal-shaped consumer":

- **EMBEDDED-FLOOR-1** — the *deployment* half ("a consumer shaped like a library in a
  terminal is out of contract by declaration").
- **PROGRESS-CHANNEL-1** — the *streaming* half. Its own text says it was "surfaced only by an
  **interactive** comparator" — that is because we have no interactive surface to feel it
  from.
- **SUBSTRATE-WITNESS-1** — the *witness* half. A CLI would be the cheapest first-party
  consumer that cannot route around the chokepoints.

**Do not merge those into this one.** Each is actionable alone and two have their own
dependents; this entry records that they share a root, which is the thing that was missing.

### What would close it

Either a decision that the runtime deliberately has no execution CLI — **recorded**, the way
the declined kernel-replay decision is — or the tiered build in the scope doc. The scope doc's
recommendation is neither: **settle the `ExecutionPipeline` question and fix the vacuous quota
first**, because that is true either way.

---

## CLI-SANDBOX-FORMAT-1: aindy-runtime sandbox returns raw JSON to terminal

**Status:** CLOSED (2026-06-05)

**Implemented:**
- `_format_sandbox_summary(payload)` in `AINDY/runtime_only.py` — renders the full
  payload as a ~25-line human-readable summary: platform, highest tier, production-safe
  status, container backend detection + operator note, active runner/assurance/certification,
  requirements met, sandbox verification method, escape test posture (from
  `sandbox_escape_test_posture()`), trusted Python extension count, degraded modes list.
- Default `aindy-runtime sandbox` output is now human-readable.
- `aindy-runtime sandbox --json` restores the full machine-readable JSON (now also
  includes `escape_test_posture` key alongside the original five).
- `_run_sandbox_check(output_json=False)` — new parameter; `--json` flag wired through
  argparse `dest="output_json"`.
- Tests updated in `test_runtime_cli.py` (9 total pass): JSON tests updated to pass
  `output_json=True`; new `test_sandbox_check_default_produces_human_readable_summary`
  verifies the human-readable format; patch list extended with `sandbox_escape_test_posture`.

---

## IDEM-6 — Multi-Instance Bootstrap Race

Status: CLOSED (2026-06-05)

Source: `docs/runtime/IDEMPOTENCY_CONTRACT.md` Open Question #1.

Implemented: `pg_advisory_lock(_BOOTSTRAP_ADVISORY_LOCK_KEY)` wraps the blank-DB
bootstrap path in `reconcile_runtime_schema()` (`AINDY/db/schema_contract.py`).
The lock is acquired with a blocking call (waits rather than fails), the schema state
is re-inspected under the lock (TOCTOU guard — a second instance that wins the wait
finds the DB already bootstrapped and skips `create_all`), and the lock is explicitly
released in a `finally` block so it is freed even when `create_all` raises.

Lock key: `_BOOTSTRAP_ADVISORY_LOCK_KEY = 4149443900` (stable bigint, must not change).
SQLite paths are not affected (advisory lock is PostgreSQL-only; the check gates on
`not url.startswith("sqlite")`).

Regression coverage: 3 new unit tests in `tests/unit/test_runtime_schema_contract.py`
(`test_reconcile_blank_db_acquires_advisory_lock_for_postgres`,
`test_reconcile_blank_db_skips_create_all_when_another_instance_bootstrapped`,
`test_reconcile_blank_db_advisory_unlock_called_even_on_create_all_failure`).

---

## IDEM-7 — Syscall Registry Not-Ready Window

Status: CLOSED (2026-06-04)

Implemented: `SYSCALL_REGISTRY_MIN_COUNT = 17` added to `AINDY/kernel/syscall_registry.py`.
`_check_syscall_registry_status()` added to `AINDY/routes/health_router.py` and wired into
`/health/deep` — the response now includes `syscall_registry: {status, count, minimum_expected}`.

The timing-window risk (HTTP traffic before Phase 8) is already covered by the `startup_complete`
check in the readiness report (`get_readiness_report` in `health_service.py:800`) — the ready
endpoint returns 503 `startup_incomplete` until Phase 8 finishes and `publish_api_runtime_state`
sets `startup_complete=True`. The `/health/deep` addition makes the registry count visible to
operators and surfaces an `incomplete` status if a future registration is lost.

Regression coverage: `tests/unit/test_runtime_readiness_contract.py`.

---

## IDEM-9 — EffectRecord Table Growth

Status: CLOSED (2026-05-24)

Note: IDEM-8 is already taken (APScheduler stub fix, closed 2026-05-23 — see IDEMPOTENCY_AUDIT.md).

Implemented: `_cleanup_expired_effect_records()` in `AINDY/platform_layer/scheduler_service.py`.
Runs every 24 hours. Deletes finalized rows (status ≠ `pending`, `completed_at IS NOT NULL`)
older than 90 days in batches of 10,000 rows per commit. Pending rows are never deleted.
Supporting index: `ix_effect_records_completed_at_status` (migration 0004).
`SCHEMA_CONTRACT_VERSION` bumped to "2026-05-24.1".

Remaining operational gap: row-count monitoring must still be set up manually. No automated
alert exists. Add a dashboard panel or startup log line that surfaces `effect_records` total
row count so unbounded growth is detected without polling.

---

## IDEM-10 — The EXACTLY_ONCE idempotency gate is dead in production; agent tool calls bypass it entirely

**PLAN:** consolidated into the Mediated Effect Boundary program —
`docs/runtime/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md`. IDEM-10 is delivered by **MEB-0**
(tool-path effect boundary — gives agent tool calls idempotency, the part that actually
matters) + **MEB-1** (repair the dispatcher gate to key on a stable scope, not the
unaddressable EU PK). The finding below is the verified source of that plan.

**MEB-0 SHIPPED 2026-07-11:** side-effecting agent tools now have idempotency — opt-in via
`AINDY_TOOL_IDEMPOTENCY` + per-tool `execution_guarantee="EXACTLY_ONCE"`, deduped per
(run_id, tool, args) through `kernel/effect_ledger.py`. PG-verified (tool runs once across
two identical calls). This closes "the part that actually matters" (tool calls had NO
idempotency at any layer). **MEB-1a SHIPPED 2026-07-11:** the dispatcher's duplicated
effect-record copies + STALE constant were removed; it now imports them from
`kernel/effect_ledger.py` (behavior-preserving — gate still dead). **MEB-1b SHIPPED 2026-07-11:**
the gate now fires from a per-syscall `SyscallEntry.execution_guarantee` (new additive field,
default AT_LEAST_ONCE) + `AINDY_SYSCALL_IDEMPOTENCY` flag (default off), scoped to
`execution_unit_id`; the dead EU-PK lookup is removed. Kept the separate `_gate_db` + `_is_uuid`
#157 guards; ledger failure degrades to AT_LEAST_ONCE. Verified: rewritten gate unit suite + a
real-PG dedup e2e (`test_idempotency_gate_e2e::test_syscall_idempotency_dedup_e2e`, CI Integration
job). **IDEM-10 is CLOSED at the mechanism level** (tool path MEB-0, syscall path MEB-1b). No
syscall declares EXACTLY_ONCE yet. **Follow-ups (not IDEM-10-blocking):** adopt EXACTLY_ONCE on
chosen syscalls (e.g. memory.write); populate `execution_id` on the writer (EU-by-source lookup,
compensation-ledger link); optionally relax `_is_uuid` for `run_<uuid>` coverage.

**Status:** Open — verified 2026-07-09 (during ECOGAP-1 Phase 3a scoping). High:
the documented, unit-tested "EXACTLY_ONCE" idempotency contract has **never deduplicated a
single syscall in a real run**, and the side-effecting agent tool calls never reach the gate.

**Finding (source-verified).** The gate in `kernel/syscall_dispatcher.py:504-579` resolves
`execution_guarantee` from `ExecutionUnit.extra.retry_policy.execution_guarantee`, looked up
by `ExecutionUnit.id == context.execution_unit_id`, and only enters the EXACTLY_ONCE branch
(`:553`) when that yields `"EXACTLY_ONCE"`. In production it **never does**, for two
independent reasons:

1. **The guarantee is never persisted to any EU.** The only resolver that stamps
   `EXACTLY_ONCE` — `require_execution_unit` → `_resolve_policy_for_eu`
   (`core/execution_gate.py:174-241`) — is never called with `eu_type="agent"` (no
   `_route_prefixes` entry maps to `agent`; `registry.py:125`), and `gate_and_dispatch`
   (`execution_gate.py:364`) is **dead code with no callers**. The agent-run EU is created
   directly via `ExecutionUnitService.create` (`agents/agent_runtime/creation.py:76-84`)
   with **no `retry_policy`** — and it stores `"overall_risk"`, which `_resolve_policy_for_eu`
   ignores (it reads `"risk_level"`, `execution_gate.py:193`). So the gate's guarantee read
   (`syscall_dispatcher.py:528-533`) always returns `"AT_LEAST_ONCE"`.
2. **Even if persisted, the lookup can't match.** `ExecutionUnit.id` is always a standalone
   `uuid4` (`execution_unit_service.py:69`), never equal to the `execution_unit_id` a syscall
   carries (FlowRun.id, a fresh per-`sys()` random uuid via `make_syscall_ctx_from_tool:900`,
   or a trace_id). The EU is linked to a run via `source_id`/`flow_run_id`, not its PK.

**And the part that actually matters:** agent **tool** calls (the side-effecting operations —
`send_email`, etc.) bypass the dispatcher entirely — `call_tool` → `run_agent_tool` →
`execute_tool` (`nodus_worker.py:242-262`, `tool_registry.py`) never touches the gate. So the
operations that most need at-most-once protection have **none, at any layer**.

**Why it wasn't caught:** `tests/unit/test_idempotency_gate.py` stubs an EU with a matching
bare-UUID PK and `extra={"retry_policy":{"execution_guarantee":"EXACTLY_ONCE"}}` — a state
production never constructs — so the gate's logic is verified in isolation while its
production wiring is absent.

**The real fix (two layers, the actual shape of ECOGAP-1 Phase 3a — was mis-scoped as a
nodus_vm "gate skip" edge):**
- **Resurrect the gate:** persist the guarantee on agent-run EUs (`risk_level`, not
  `overall_risk`), and thread a **stable** action_id scope + an **explicit** guarantee to the
  gate — `AgentRun.id` (a stable bare UUID) is already present in the nodus worker context as
  `ctx["run_id"]`/`tool_run_id` (`nodus_worker.py:187`) but not forwarded into the
  SyscallContext. `compute_action_id(scope=...)` accepts any string, so the scope need not be
  the EU PK. Preserve the #157 protections: the `_is_uuid` guard (`:516`), the `begin_nested`
  SAVEPOINT (`:524`), the no-bare-`run_<uuid>`-cast invariant.
- **Route tool calls through idempotency:** `run_agent_tool` writes an `EffectRecord` keyed on
  the stable run scope + tool + args (write-ahead in the worker subprocess) — this is what
  actually makes agent crash-continuation safe for **non-idempotent** tools. Converges with
  ECOGAP-1 Phase 2a/3b (the per-step WAL).

**Relationship to ECOGAP-1:** ECOGAP-1 Phases 1/2/2a ship crash continuation gated to
*idempotent-declared* flows/agents precisely because this layer doesn't exist. IDEM-10 is the
prerequisite for **declaration-free** continuation (the ECOGAP-1 Phase 3 payoff). Scope the
two layers as a dedicated effort — this is the kernel's most correctness-sensitive path
(#157 history) and larger than a single contained PR.

---

## C2 — Cross-Platform Container-Grade Sandbox

Status: CLOSED (2026-05-24)

Source: `ISOLATION_MODEL_PLAN.md` Gap 4 / `C2_SANDBOX_AUDIT.md`.

Reopen condition was: a non-Linux host platform produces a sandbox runner type passing
the shared worker policy certification suite with assurance class at or above
`container-grade-sandbox`.

Implemented: NF-1 through NF-7 in `AINDY/platform_layer/sandbox_runner.py` —
`_detect_linux_container_backend` helper, `linux_container_backend_available`
parameter in `_platform_matrix_entry`, and dynamic
`production_safe_third_party_supported_host_platforms` key in `support_contract`.
On Windows + Docker Desktop in Linux-containers mode,
`sandbox_platform_capability_matrix()` reports
`production_safe_third_party_plugin_execution: True` and
`_detect_linux_container_backend` returns
`linux_container_backend: True, detection_method: docker_info_json`.

Live verification (2026-05-24, Windows + Docker Desktop): `sandbox_certification_profile`
returned `tier_status: certified` at tier `container-sandbox-certified` with all four
attestation fields launch-verified (backend identity, runtime identity, mount mode,
resource limit mode). `docker run` argv included `--cap-drop ALL`,
`--security-opt no-new-privileges`, `--read-only`, `--network none`, and
`--pids-limit`, all accepted by the container kernel.

Documentation: `docs/runtime/EXTENSION_TRUST_MODEL.md` Supported Platform Sandbox
Matrix rewritten (NF-8). NF-2 contract decision documented in the new
"Production-Safe Third-Party Plugin Sandbox Semantics" subsection.

---

## C3 — Cross-Platform Strong Sandbox

Status: PHASES 0-5 COMPLETE (2026-06-04 → 06) — one deferred capability remains:
a native strong-sandbox VM runner on non-Linux hosts (trigger-on-demand, not scheduled).

All scoped phase work shipped and is CI-gated (17/17 escape tests pass, real Docker):
Phase 0 escape suite, Phase 1 WSL2/Windows backend detection, Phase 2 macOS backend
detection + policy, Phase 3 threat model + `sandbox_escape_test_posture()`, Phase 4
release gate, Phase 5 macOS CI certification workflow. See the per-phase bodies below.

**Only remaining gap (deliberately deferred, large net-new build):** strong-sandbox and
`hostile-third-party` profiles are Linux-only —
`STRONG_SANDBOX_SUPPORTED_HOST_PLATFORMS = (PLATFORM_LINUX,)` and
`HOSTILE_THIRD_PARTY_SUPPORTED_HOST_PLATFORMS = (PLATFORM_LINUX,)` are unchanged. Non-Linux
hosts reach `container-sandbox-certified` (C2 — closed) but not `strong-sandbox-certified`.
Closing C3 fully needs a platform-native strong-VM runner. **Preparation plan scoped in
`docs/runtime/C3_NON_LINUX_STRONG_SANDBOX_PLAN.md`** (Windows-native + macOS tracks) so
either track can start the day a trigger lands.

Source: `C2_SANDBOX_AUDIT.md` "What This Audit Does NOT Cover" / `ISOLATION_MODEL_PLAN.md` Gap 4 (C3 remainder).

**Phase 0 (2026-06-04) — Adversarial escape test suite: COMPLETE**

Created `tests/sandbox/` with 17 adversarial escape tests across 6 categories,
gated under `pytest -m sandbox_escape`. Tests prove the existing Linux
container-grade sandbox claim with real Docker invocations (no mocking). Each test
documents exactly what was tested and why the specific vector matters.

Categories and test counts:
- Filesystem escape (3): read-only rootfs, read-only bind mount, tmpfs isolation
- Network escape (3): TCP outbound, UDP outbound, loopback-only kernel evidence
- Process escape (2): pids limit enforcement, cgroup kernel evidence (Linux-only)
- Privilege escalation (4): CAP_NET_RAW, CAP_CHOWN, no-new-privileges /proc evidence, combined (Linux-only)
- Host env leak (2): sensitive keys absent, allowlist verification
- Path boundary (3): unmounted dir inaccessible, plugin root accessible, path traversal stays in container

Result artifact: `tests/sandbox/sandbox_escape_results.json` — written at session end.
Marker: `sandbox_escape`. Image: `python:3.11-alpine` (configurable via `SANDBOX_ESCAPE_IMAGE`).
Platform note: all tests run on any platform with Docker Linux containers; Linux-only kernel
control tests (privilege escalation, process/pids) skip on non-Linux backends.

**Phase 1 (2026-06-06) — WSL2/Windows Linux backend detection: COMPLETE**
Implemented `_detect_wsl2()` in `sandbox_runner.py`. Detects two cases: (1) Python
process running inside WSL2 (Linux host, `/proc/version` contains "microsoft"); (2) Windows
host with Docker Desktop Linux container backend (WSL2 or Hyper-V, from `docker info`).
Updated `_supports_linux_container_kernel_controls()` to accept `linux_container_backend`
parameter. Updated `inspect_container_kernel_controls()` to pass it through, enabling
`no_new_privileges`, `drop_all_capabilities`, and `pids_limit` to be reported as supported
and active on Windows + Docker Desktop Linux containers mode. `seccomp_profile`,
`apparmor_profile`, and `selinux_label` remain native-Linux-host-only (not tested in Phase 0).
`ContainerizedOciSandboxRunner` caches backend detection at construction time.
`sandbox_platform_capability_matrix()` now includes `current_wsl2_detection` field.
Platform matrix hardening controls split: basic kernel controls available when
`linux_container_backend_available=True`; profile controls Linux-host-only.
21 new unit tests in `tests/unit/test_sandbox_runner.py`.

Gap remaining: strong sandbox VM (`RUNNER_STRONG_SANDBOX_VM`) still requires native Linux
or WSL2-native Python (when `platform.system() == "Linux"`). A Windows-native path to the
strong sandbox tier requires a Windows `aindy-sandbox-vm` binary that bridges to WSL2 —
out of scope until the launcher exists.

**Phase 2 (2026-06-06) — macOS Docker Desktop Linux backend detection + policy: COMPLETE**
Extended `_detect_wsl2()` to handle macOS: new `docker_macos_backend` field detects
Docker Desktop with Linux container backend (Apple Virtualization Framework or HyperKit)
via `docker info`. `wsl2_kernel_available` is now True on macOS + Docker Desktop Linux mode.
Updated static platform matrix entries for Windows and macOS: both now show
`linux_container_backend_available=True` (Docker Desktop on both platforms supports Linux
containers). Static matrix now correctly reports `no_new_privileges`, `drop_all_capabilities`,
`pids_limit` as available hardening controls for both platforms.
Policy document created: `docs/runtime/MACOS_CONTAINER_POLICY.md`. Records what IS and is
NOT claimed (seccomp/AppArmor/SELinux not claimed — not tested), assurance tier
(container-grade, not strong-sandbox-vm).
2 new unit tests in `tests/unit/test_sandbox_runner.py` (64 total).

**Phase 5 (2026-06-06) — macOS CI certification workflow: COMPLETE**
`.github/workflows/macos-sandbox.yml` added (PR merged 2026-06-06). `workflow_dispatch`
job targets `macos-14` (Apple Silicon), installs Colima as the Linux-backend Docker
provider, and runs `pytest -m sandbox_escape -v` against the full 17-test escape suite.
Uploads `sandbox_escape_results.json` as a workflow artifact. macOS escape suite
certification is now gated through CI — run the workflow before each macOS deployment.

**Phase 3 (2026-06-05) — Formal threat model + sandbox_escape_test_posture(): COMPLETE**
Created `docs/runtime/SANDBOX_ESCAPE_AUDIT.md` (append-only log, Entry 001 committed).
Each escape vector maps to a threat model entry documenting threat, control, and failure
interpretation. `sandbox_escape_test_posture()` added to `sandbox_runner.py` — reads
`tests/sandbox/sandbox_escape_results.json`, returns structured posture dict (posture,
last_run, host_platform, coverage, gaps, operator_note). Returns `"not_run"` gracefully
when artifact is absent (production install without tests/).

**Phase 4 (2026-06-05) — Release gate: COMPLETE**
Step 16 added to `docs/runtime/RELEASE_CHECKLIST.md`. Gate condition:
`sandbox_escape_test_posture()["posture"] == "all_pass"`. Skips acceptable; FAILs block.
Audit trail instruction added: append to SANDBOX_ESCAPE_AUDIT.md after each pre-release run.

Trigger: when there is a platform-specific sandbox runtime delivering strong-sandbox-tier
assurance on a non-Linux host.

Condition to close C3 fully: A non-Linux host platform gains a supported sandbox runner type
with assurance class `strong-sandbox-tier`, verified through the escape test suite and the
shared worker policy certification suite (`tier_status: certified` at `strong-sandbox-certified`).

---

## PACK-DEBT-1 — Nodus Pin Staleness

Status: CLOSED (2026-05-25)

**Resolution:** Pin bumped to `nodus-lang==3.0.2` in `pyproject.toml` and
`AINDY/requirements.txt`. `AINDYNodusRuntime` updated to match the 3.0.2 base class API:
`initial_globals` now forwarded to `load_module_from_source` / `load_module_from_path`
(was silently dropped — caused "Undefined variable" for `state`, `user_id`, etc. in
worker scripts); error handling now returns `Result.failure()` dict instead of raising,
matching the base class contract and preserving captured stdout on script error;
`HostFunctionError` unwrapped before the generic error handler.

The class is retained for AINDY-specific extensions that are not in the base class:
`register_function` stdlib aliases (`recall_from`, `recall_all`, `share`); auto
`project_root` fallback to the bundled stdlib directory; bare `import memory` rewriting.

**Investigation findings (2026-05-25):**

Nodus is at `3.0.2`. The gap spans
v1.1.2, v2.0.0, v2.0.1, v2.1.0, v2.1.1, v3.0.0, v3.0.1 — two full major versions.

**Audit completed 2026-05-25.** Import surface in `AINDY/` is entirely in the
embedding/VM layer, concentrated in `AINDY/nodus/runtime/aindy_runtime.py`:
`NodusRuntime`, `ModuleLoader`, `VM`, `coerce_error`, `BuiltinInfo`, `Result`,
`normalize_filename`, `capture_output`, `configure_vm_limits`.
Additional probe-only imports in `health_router.py` and `runtime/__init__.py`
(hasattr checks only — not affected by any breaking change).

**Breaking changes that require action before bumping the pin:**

1. **v2.1.1 CRITICAL — `allowed_paths` sandbox bypass (SECURITY).**
   Stdlib wrappers (`std:fs`) were not forwarding `allowed_paths` from the calling
   VM, allowing sandboxed scripts to read arbitrary paths via stdlib calls.
   `aindy_runtime.py` constructs `VM(..., allowed_paths=self.allowed_paths)` — the
   sandboxing intent is present but the fix is only in v2.1.1+. Any use of
   `allowed_paths` for security isolation is currently ineffective at the stdlib
   boundary. **Must be resolved before any deployment relying on path sandboxing.**

2. **v2.1.0 BUG-005 — `NodusRuntime.run_source` raises vs. returns divergence.**
   v2.1.0 changed `NodusRuntime.run_source` to return `{"ok": false, "error": ...}`
   on script error instead of raising. `nodus_flow_compiler.py:255` checks
   `result.get("ok")` — written for the post-v2.1.0 contract. On v1.1.0, script
   errors raise before the check is reached; the caller at `nodus_adapter.py:882`
   catches `(ValueError, RuntimeError)`, but Nodus v1.1.0 exception types may not
   match. `AINDYNodusRuntime.run_source()` is unaffected — it overrides the method
   completely and still raises `coerce_error(...)`, which is the correct shape for
   its callers (`nodus_worker.py` catches `Exception`).

3. **v3.0.0 — err.kind taxonomy changed.**
   `coerce_error` in `aindy_runtime.py:155` coerces Python exceptions to Nodus
   errors. The kind taxonomy changed: `"runtime"` splits into `"io_error"`,
   `"parse_error"`, `"runtime_error"`, etc. No code in aindy-runtime currently
   inspects `.kind` on the raised error (confirmed by grep — all `.kind` uses are
   Python `inspect.Parameter.kind` or manifest fields). Low callsite impact; the
   error message strings seen at the HTTP layer will change.

4. **v3.0.0 — Integer type introduced.**
   Nodus scripts that check `type(x) == "number"` will break — integers are now a
   distinct type. This is a script-level concern; the Python embedding API is
   unaffected. User-authored `.nodus` scripts must be audited.

5. **v3.0.1 BUG-E04 — `HostFunctionError` sentinel for host function exceptions.**
   Python exceptions raised by host-registered functions (registered via
   `register_function`) now propagate as `HostFunctionError` (from
   `nodus.runtime.diagnostics`) rather than propagating directly. The `except
   Exception as err` handler in `aindy_runtime.py:154` catches it. `coerce_error`
   on a `HostFunctionError` may produce different error detail than before.
   Verify error messages surfaced to users remain meaningful.

**Cleanup opportunity:** COMPLETED — see OVERRIDE-DRIFT-1 below.

**Resolution path:**
1. Bump `nodus-lang==1.1.0` → `nodus-lang==3.0.1` in `pyproject.toml`.
2. Delete `AINDYNodusRuntime` and update all import sites to `NodusRuntime`.
3. Verify `nodus_flow_compiler.py` error path: test that a bad flow script surfaces a
   `ValueError` with a readable message (not a raw Nodus exception).
4. Audit user-authored `.nodus` scripts for `type(x) == "number"` — rename to
   `type(x) == "integer"` or `type(x) == "float"` as appropriate.
5. Run the full test suite and the Nodus-specific integration tests.
6. Manually verify that `allowed_paths` sandboxing is effective after the bump
   (create a test script that attempts `std:fs` access outside allowed paths).

Trigger: must be resolved before tagging 1.0.0.

---

## OVERRIDE-DRIFT-1 — AINDYNodusRuntime override class deleted

Status: CLOSED (2026-05-25)

Derived from PACK-DEBT-1 cleanup. `AINDYNodusRuntime` in
`AINDY/nodus/runtime/aindy_runtime.py` was a `NodusRuntime` subclass written to patch
BUG-E03 (`host_globals` not forwarded to `ModuleLoader` in nodus-lang 1.1.0). With the
pin bumped to 3.0.2 (PACK-DEBT-1), the subclass provided no upstream-bug-patch value and
was the source of three documented divergences:

1. **initial_globals dropped** — `AINDYNodusRuntime.run_source` constructed the VM with
   `initial_globals` but the value was overwritten by `vm.reset_program` in
   `_execute_module`. Fixed inline 2026-05-25 before this deletion, confirmed working.
2. **Raise vs. return semantics** — `AINDYNodusRuntime.run_source` returned a failure
   dict on error, but the override's error handling had diverged from the base class
   contract. Aligned to base class behavior 2026-05-25; base class now owns the contract.
3. **HostFunctionError double-wrap** — `AINDYNodusRuntime.run_source` included an
   explicit `except HostFunctionError as wrapped: raise wrapped.cause` guard, which
   could have produced inconsistent exception wrapping if not perfectly aligned with the
   base class's own guard. Resolved automatically by this deletion — the base class
   handles it correctly.

**What was inlined into `nodus_worker.py` (AINDY/runtime/nodus_worker.py):**
- `project_root` defaulting to `_STDLIB_DIR` (bundled stdlib) — now passed explicitly
  at the `NodusRuntime(project_root=...)` instantiation site.
- `register_function` stdlib aliases (`recall_from` → `__memory_stdlib_recall_from`,
  `recall_all` → `__memory_stdlib_recall_all`, `share` → `__memory_stdlib_share`) —
  now registered as three explicit `register_function` calls in the worker.
  These aliases are load-bearing: `AINDY/nodus/stdlib/memory.nd` calls the `__*` names
  directly.
- Bare `import memory` → `import "memory" as memory` rewriting — now applied to
  `script` before calling `runtime.run_source`.

**Additional change:** `_runtime_emitted_events()` in the worker now reads from
`runtime.last_vm.event_bus.events()` (base class exposes `last_vm`). The override had
populated `runtime.last_emitted_events` as a list of dicts; the base class never set
that attribute, so we switched to the standard event bus path.

**Files changed:**
- `AINDY/runtime/nodus_worker.py` — import + instantiation + aliases + rewriting + event collection
- `AINDY/nodus/runtime/embedding.py` — AINDYNodusRuntime removed from re-export shim
- `AINDY/nodus/runtime/aindy_runtime.py` — class body replaced with deprecation doc comment
- `tests/unit/test_nodus_runtime_contract.py` — `test_aindy_nodus_runtime_subclasses_nodus_runtime` removed (tested class existence, not behavior)

---

## PACK-DEBT-2 — Auth Dependency CVE Policy

Status: CLOSED (2026-05-25)

Implemented:
- `security` optional-dependencies group added to `pyproject.toml` — declares
  `pip-audit>=2.7.0` plus floor pins for `bcrypt>=4.0.1`, `passlib>=1.7.4`,
  `python-jose>=3.5.0`.
- `.github/workflows/security-audit.yml` — pip-audit (OSV-backed) runs on every
  PR and on a weekly cron schedule (Mondays 08:00 UTC). Fails CI on any detected CVE.
  Produces an `audit-results.json` artifact. Exemptions via `--ignore-vuln <GHSA-ID>`
  with mandatory comment documentation.
- `.github/dependabot.yml` — enabled for `pip` and `github-actions` ecosystems,
  weekly cadence. Secondary signal for transitive deps and stale SHA pins.
- `docs/runtime/SECURITY_POLICY.md` — new file. Documents SLA (Critical: 7 days,
  High: 14 days, Medium: next minor, Low: next major), exemption process, and
  accepted-findings register.

### Scoping note — what actually consumes `cryptography` (added 2026-08-02)

Recorded so the next `cryptography` major is a ten-minute question rather than an
open-ended worry. Established while verifying 48→49 (#302), which was held deliberately
because green CI is the weakest form of evidence for a crypto major under an auth stack.

**There is exactly one direct consumer:** `AINDY/platform_layer/extension_signing.py` —
Ed25519 signed plugin bundles (AGENT-HARDEN-10). That is the whole surface.

Two things that look like consumers and are not:

- **JWT does not touch it.** `auth_service.ALGORITHM = "HS256"` — HMAC via `hashlib`.
  python-jose's HS256 path never reaches `cryptography`. So "auth-adjacent major"
  overstates the risk: the exposure is plugin-bundle signing, not login.
- **passlib/bcrypt is a separate package** (`bcrypt==4.0.1`), not a `cryptography`
  consumer.

**Method that settled it** (reusable): an isolated venv at the candidate version with
`python-jose` / `passlib` / `bcrypt`, exercising the Ed25519 path **call-for-call as
`extension_signing.py` uses it** — including the raw-bytes serialization round-trip, which
is the part a major release could plausibly move. Then the **negative** paths: tampered
digest rejected, untrusted key rejected, wrong JWT secret rejected. A green round-trip alone
proves nothing — a `verify` that silently accepts anything would pass it.

Note also that a bump touching `AINDY/requirements.txt` is installed by `Runtime Contracts`,
so the full unit suite already runs against the candidate version. The targeted checks exist
to cover the negative paths that suite does not assert.

**If `extension_signing.py` gains a second `cryptography` import, or JWT moves off HS256 to
an asymmetric algorithm, this scoping is stale and must be re-derived.**

---

## PACK-DEBT-3 — No mypy Baseline

Status: CLOSED (2026-05-25) — Decision: do not pursue mypy at this time.

The dominant bug class observed across this codebase is contract drift between
modules, repos, and layers — registry implementation vs execution-model docs,
frontend vs backend sandbox fields, SDK vs runtime surfaces. The audit arc and
contract test suite address this class directly. mypy's primary value is signature
drift within a module, which has not been the observed failure mode. Adopting mypy
now would impose ongoing annotation maintenance cost (plugin-host dynamic dispatch
friction, capability registry typing) for marginal coverage of the bugs actually
being shipped.

Reopen triggers:
- A second engineer joins the project, OR
- A contributor PR introduces a signature-drift bug that audit-arc misses and a
  type-checker would have caught.

On reopen: start with `aindy-sdk` (smaller surface, cleaner boundaries) before
`aindy-runtime`. Use `--strict` on new code only; document a phased adoption plan.

---

## PACK-DEBT-4 — Integration Tier Uses `continue-on-error: true`

Status: CLOSED (2026-05-25)

`continue-on-error: true` removed from the `integration-postgres` job in
`runtime-ci.yml`. Integration failures now block CI green.

Rationale: advisory-only integration tests provide weak signal. If integration
coverage is worth running, it is worth gating on. If flakes materialize, they are
investigated as real signals rather than silenced by restoring the bypass.

Followup posture: if a flake appears within the first two weeks, investigate root
cause (test isolation, container startup race, fixture cleanup) rather than restoring
`continue-on-error`. If genuinely environmental and unfixable, open a new TECH_DEBT
entry rather than re-disabling the gate.

---

## PACK-DEBT-5 — starlette 0.49.1 / FastAPI 0.121.0: PYSEC-2026-161 host-header CVE deferred

**Status:** CLOSED (2026-06-05)

**Implemented:** Upgraded `fastapi` 0.121.0 → 0.135.0, `starlette` 0.49.1 → 1.0.1, and
`prometheus-fastapi-instrumentator` 7.1.0 → 8.0.0 (7.x required `starlette<1.0.0`; 8.0.0
requires `starlette>=1.0.0,<2.0.0`). Pins updated in both `AINDY/requirements.txt` and
`pyproject.toml`. `--ignore-vuln PYSEC-2026-161` removed from `security-audit.yml`.
PYSEC-2026-161 Accepted Findings entry removed from `docs/runtime/SECURITY_POLICY.md`.
Unit tests pass; no API-level breakage detected (direct starlette usage in the codebase
is limited to `starlette.exceptions.HTTPException` — a stable import).

---

## DEBT-COMPAT-1 — Cross-version compatibility story between runtime and SDK

**★★ REOPENED 2026-08-18 — P2. The trigger fired, in the only consumer, and nothing surfaced it.**

Provenance: measured against `C:\dev\claw` while checking `C:\codev\openclaw_research\`.

**The deferral rationale below — *"Only one version of each exists today"* — is false.** The
first-party flagship carries `aindy_runtime-1.4.0.dist-info` in its venv while this repo is at
**2.4.0**: a full major behind, and **below the floor the runtime itself advertises**
(`runtime_compatibility.py` computes `recommended_runtime_requirement` as `>=2.0,<3.0`).

**★ Why nobody noticed, and it is the actionable part: `aindy-runtime` is not declared as a
dependency anywhere in that consumer.** `grep -n aindy` over its `pyproject.toml` returns nothing —
only `nodus-*` packages are declared. **No pin exists, so no pin can go stale visibly:** nothing to
bump, nothing in a lockfile, no install step that could fail. The version survives only in a README
table, which is exactly where it rotted.

**★ And the runtime half is the same shape as `ROUTE-AST-UNWIRED-1` — a declaration published with
no consumer.** `platform_layer/runtime_compatibility.py` emits, in its own payload:

> *"The apps repo **must declare a normal Python dependency range on `aindy-runtime` with an
> explicit upper bound** before the next MAJOR runtime release."*

Its only reader is `routes/version_router.py`, which serves it on `/api/version`. **Nothing
consumes it. No consumer is ever told it is out of contract.** The policy sentence is violated by
the only consumer there is, and the mechanism that states the policy cannot observe the violation.

**Resolution path, revised — smallest first:**

1. **Consumer-side (one line, not ours to make but the root cause):** declare
   `aindy-runtime>=2.0,<3.0`. Every subsequent drift becomes visible to ordinary tooling.
2. **Runtime-side, cheap:** have the SDK — or any client that already calls `/api/version` — compare
   its installed `aindy-runtime` against `recommended_runtime_requirement` and **warn**. The
   declaration already exists and is already served; what is missing is one comparison at one call
   site. Warn, do not refuse: a hard gate on a version check is how a working deployment dies on a
   patch bump.
3. **Then the original path below** — a compatibility-window policy and cross-version testing.

**★ Do not close this with a policy document.** The gap is not that the policy is unwritten; it is
written, and served over HTTP, and unread. **A compatibility contract with no consumer is a
compatibility contract that cannot fail** — which is why a consumer sat a major behind, under the
advertised floor, without anything anywhere raising a word.

**Related:** `SUBSTRATE-WITNESS-1` (same consumer, the integration-depth half of the same picture),
`ROUTE-AST-UNWIRED-1` and `DOCS-COVERAGE-CLAIM-1` (the published-and-unconsumed family),
`PYPI-PUBLISH-1` (release protocol, which does pin the Dockerfile — the one place a version *is*
checked).

---

**Status:** Deferred — Low Priority *(superseded by the reopen above)*
**Trigger condition:** When two runtime versions exist in the wild
simultaneously (e.g., a 1.0 cloud runtime serving users whose local
SDKs are still on a 0.x version, or vice versa). **★ FIRED — see above.**

**Context:** Today, the runtime and SDK ship at matching versions and
the compatibility contract is implicit. Under the local + cloud
distribution model (see ARCHITECTURE.md), this implicit contract
becomes load-bearing: a cloud runtime at v1.1 may serve users whose
local SDKs are v1.0, and the runtime's declared HTTP surface
(`/health/sandbox`, `/flow/run`, etc.) must remain compatible across
those versions.

**Resolution path when reopened:** Define a compatibility window
policy (e.g., "the SDK at version N is supported against runtimes
at versions N through N+2"). Add automated cross-version testing
that exercises older SDK versions against newer runtime versions.
Document the policy in PUBLIC_API_CONTRACT.md.

**Why deferred:** Only one version of each exists today. The
infrastructure to test cross-version compatibility is non-trivial,
and the policy needs to be informed by actual release cadence and
deprecation philosophy, neither of which is settled.

---

## TENANT-2 — Per-tenant quota limits not configurable; `quota_group` has no enforcement

Status: Deferred — Low Priority

Source: `docs/runtime/LOCAL_AND_CLOUD_AUDIT.md` Area A, finding TENANT-2.

`MAX_CONCURRENT_PER_TENANT = 5` is a process-wide constant overridable only via
`AINDY_QUOTA_MAX_CONCURRENT` env var, not per-billing-tenant. The `quota_group`
column on `execution_unit` accepts policy tags ("premium", "batch") but nothing
reads this field to adjust quota behavior. In a cloud multi-tenant context,
different tenants need independently configured concurrency ceilings.

Resolution path:
- Build enforcement for `quota_group` as a policy lookup key, OR
- Add a per-tenant concurrency limit table driven by control-plane configuration.

Trigger: when cloud onboarding begins.

---

## COMPAT-2 — No deprecation or forward-compatibility policy for extension ABI

**Status:** CLOSED (2026-06-15)

**Resolution:** Added "Deprecation and Forward-Compatibility Policy" section to
`docs/runtime/EXTENSION_ABI.md`. Stable ABI versions get a minimum two-minor-release
support window after a newer stable version ships, with the deprecated version flagged
in `GET /api/version` under `public_contract.extensions.abi.deprecated_versions`.
Experimental ABI versions (`v1alpha*`) explicitly carry no support window and may be
removed in any release. Policy triggers on first stable ABI promotion or experimental
surface promotion to stable.

---

## DATA-1 — No data residency mechanism

Status: Deferred — Low Priority

Source: `docs/runtime/LOCAL_AND_CLOUD_AUDIT.md` Area D, finding DATA-1.

No `AINDY_DATA_REGION` env var or equivalent exists. Cloud operators in regulated
industries (GDPR, HIPAA, SOC 2 Type II) need to declare which region data is stored
in and enforce that writes stay within that boundary.

Resolution path:
- Define an `AINDY_DATA_REGION` env var and expose it in the deployment contract.
- Actual region-routing enforcement requires control-plane work outside this repo.

Trigger: when cloud onboarding begins or when a regulated operator requires it.

---

## LOCAL-1 — No documented production upgrade path for local installs

Status: CLOSED (2026-06-05)

Added `## Upgrading` section to `README.md` covering: `pip install --upgrade`,
version verification via `aindy-runtime --version` / `/api/version`, the
`AINDY_SCHEMA_RECONCILE=true` restart sequence for schema-bumping releases,
Docker Compose pull-and-up flow, and rollback guidance (reinstall previous
version; note that rolling back across a schema change requires a DB restore).

---

## DEPLOY-TARGET-1 — Cloud deployment manifests not authored

**Status:** Deferred — pre-cloud-launch

The shortest path to a single-operator hosted deployment is translating
`docker-compose.prod.yml` into a platform-specific deployment manifest. The compose
file is effectively already the spec; the work is translation and cloud-Postgres
integration testing, not architecture.

Candidate platforms (in order of fit):
- **Railway** — `railway.json` / `railway.toml`; native Postgres with pgvector plugin
- **Render** — `render.yaml`; managed Postgres add-on; docker-compose import supported
- **Fly.io** — `fly.toml`; more regional control; pgvector via Supabase or Fly Postgres
- **Digital Ocean App Platform** — YAML spec; managed Postgres; no nginx needed (TLS built-in)

Required env vars at deploy time: `DATABASE_URL`, `SECRET_KEY`, `OPENAI_API_KEY`,
`AINDY_BOOTSTRAP_ADMIN_EMAIL`, optionally `AINDY_REDIS_URL`.

Source: `docs/runtime/DEPLOYMENT_TARGETS.md`.

**Reopen trigger:** When first cloud deployment is planned.

---

## DEPLOY-TARGET-2 — Multi-tenant SaaS readiness gate

**Status:** Deferred — trigger when first multi-tenant customer onboards

When the deployment target shifts from "hosted for a single operator" to "multiple
paying operators sharing one runtime deployment," the following TENANT-* findings
from `LOCAL_AND_CLOUD_AUDIT.md` become load-bearing and must be resolved in sequence:

1. **TENANT-1** — `tenant_id == str(user_id)` by convention; must be rebased onto a
   control-plane-issued `billing_account_id` before billing isolation is meaningful.
2. **TENANT-2** — `quota_group` on `execution_units` has no enforcement path; per-tenant
   concurrency limits and feature gates require this to be built.
3. **TENANT-3** — event bus is a single shared Redis channel; WAIT/RESUME events for
   tenant A must not broadcast to tenant B's processes.
4. **TENANT-4** — OCI container resource limits are global env vars; must become
   per-tenant to prevent noisy-neighbor problems.

None of these are blocked by architectural debt — the hooks are seeded. This is
deliberate work that begins only when the first multi-tenant customer is ready.

Source: `docs/runtime/DEPLOYMENT_TARGETS.md`. Related: `BILLING-1` (billing identity).

**Reopen trigger:** When first multi-tenant operator onboards.

---

## BILLING-1 — Billing identity: tenant_id not decoupled from user_id

**Status:** Deferred — trigger when first multi-seat customer onboards

`tenant_id` on `ExecutionUnit` is set by convention: `str(user_id)`. A commercial
billing model requires a billing account identity that is independent of individual
users — one paying account may contain multiple users (team seats). The `User`
model has no `billing_account_id`, `plan_tier`, or external billing reference field.

Resolution direction: introduce a `billing_account_id` field on `User` (or a
`BillingAccount` model) issued by the control plane at registration. Rebase
`tenant_id` onto this identifier. This unblocks BILLING-3 (plan enforcement) and
DEPLOY-TARGET-2 (multi-tenant SaaS).

Source: `docs/runtime/MONETIZATION_AUDIT.md` Area A, finding BILLING-1.

**Reopen trigger:** When first multi-seat team plan or control-plane integration begins.

---

## BILLING-2 — Metering model not chosen

**Status:** Deferred — decision required before billing infrastructure is built

Three viable billing models exist (per-seat, per-agent-run, usage-based compute),
each with different data sources and customer-facing complexity. The `AgentRun`
table is the clearest natural unit; the recommendation in the monetization audit
is per-agent-run with a seat-based floor for team plans. This decision must be
made before any billing backend is integrated.

Source: `docs/runtime/MONETIZATION_AUDIT.md` Area B, finding BILLING-2.

**Reopen trigger:** Before billing infrastructure or Stripe integration begins.

---

## BILLING-3 — No plan-tier enforcement path

**Status:** Deferred — trigger when first paid plan is defined

Even when a billing model is chosen and a control plane issues plan tiers, the
runtime has no enforcement mechanism. Every operator has identical access regardless
of plan. The enforcement path requires:

1. A `plan_tier` field on the user (populated from the control plane)
2. A `require_plan(tier)` FastAPI dependency factory (analogous to `require_admin_principal`)
3. A quota policy lookup that translates `quota_group` into concrete per-tenant limits

The `quota_group` field on `execution_units` is the right enforcement hook (seeded
but unread). `TENANT-2` in `TECH_DEBT.md` tracks the enforcement-path gap at the
infrastructure level; BILLING-3 extends it into the commercial billing context.

Source: `docs/runtime/MONETIZATION_AUDIT.md` Area C, finding BILLING-3.

**Reopen trigger:** When the first paid plan tier is defined.

---

## BILLING-4 — No self-service acquisition funnel

**Status:** Deferred — trigger before first paid customer onboards

Current onboarding requires direct operator involvement (register → manual admin
promotion). A commercial funnel requires: register → plan selection → Stripe payment
→ control plane webhook → auto-promotion with plan tier set. Steps 1 and the final
SPA redirect already work; steps 2-4 require a separate control plane service (not
in this repo) that calls internal runtime admin APIs.

The runtime's side of this contract: a `set-plan-tier` internal admin endpoint
(analogous to `auth promote-admin`) callable by the control plane via internal API
key. The commercial logic (Stripe, webhooks, pricing pages) lives outside this repo
to preserve self-hostability.

Source: `docs/runtime/MONETIZATION_AUDIT.md` Area D, finding BILLING-4.

**Reopen trigger:** Before first paid customer onboards.

---

## BILLING-5 — No usage reporting surface

**Status:** Deferred — trigger when first plan with usage limits ships

Customers on any metered or capped plan need a usage view before they are surprised
by an overage or renewal invoice. The data is available (`AgentRun` count,
`ExecutionUnit.wall_time_ms` aggregate, `memory_nodes` count), but no
`GET /platform/billing/usage` endpoint or billing-period concept exists.

Minimum viable: a read-only admin endpoint returning current-period agent run count,
compute wall time, and memory record count relative to plan limits. Requires a
billing period start date on the billing account model (BILLING-1 dependency).

Source: `docs/runtime/MONETIZATION_AUDIT.md` Area E, finding BILLING-5.

**Reopen trigger:** When first metered plan with usage limits ships.

---

## LINT-VERSION-GAP-1: eslint major version asymmetry across ui-kit and apps-monolith

**Status:** Tracked, accepted. Soft commitment to align on next maintenance pass.

**Context:** `@aindy/ui-kit` is on `eslint@^10.4.0`. `aindy-apps-monolith` (the primary consumer) is on `eslint@^9.36.0`. Both use flat config and share the `eslint-plugin-react-hooks` plugin (ui-kit on `^7.1.1`, apps-monolith on `^5.2.0` — independent version tracks).

**Posture:** Library leads consumer by one major version. This is the structurally correct direction (library lagging consumer is the bad shape — it would block consumer upgrades). The asymmetry is currently cosmetic; no rules in ui-kit's eslint 10 config are unavailable in eslint 9, and no plugin in the shared set has a peer-deps conflict.

**Cross-ref:** Same finding tracked in `aindy-apps-monolith/TECH_DEBT.md` as LINT-VERSION-GAP-1 (apps-monolith side).

**Commitment:** ui-kit will not adopt a lint rule that fails to express under eslint 9 until apps-monolith is aligned. If a desired rule is eslint-10-only, that triggers the apps-monolith upgrade rather than a divergent ui-kit config.

**Reopen trigger:** (a) apps-monolith next maintenance pass — bump to eslint 10 as a side-task, OR (b) ui-kit wants an eslint-10-only rule, OR (c) `eslint-plugin-react-hooks` 7.x backports a rule that apps-monolith wants and requires the eslint major bump.

**Estimated effort on apps-monolith bump:** ~30 minutes (verified: react-hooks 5.x supports eslint 9 and 10; no forced plugin bumps; `eslint-plugin-react-refresh@^0.4.22` is the main compatibility verification needed).

---

## EVENTBUS-REDIS-URL-CONSOLIDATION-1 — Deprecate AINDY_REDIS_URL alias

**Status:** CLOSED (2026-06-06)

Removed `AINDY_REDIS_URL` from `event_bus.py` (function simplified, `import warnings`
dropped), `config.py` (field removed), and `.env.example`. `get_redis_client()` now
reads `REDIS_URL` only. In the same pass: `AINDY_SKIP_MONGO_PING` alias removed from
`config.py` `ensure_mongo_url` validator (now reads `SKIP_MONGO_PING` directly);
`tests/conftest.py` setdefault cleaned up; `.env.example` updated to `SKIP_MONGO_PING`.
Test file reduced from 9 to 5 tests — AINDY_REDIS_URL-specific cases removed.

---

## PERMISSION-SECRET-CLEANUP-1 — Remove vestigial PERMISSION_SECRET scaffolding

**Status:** CLOSED (2026-06-04)

**Discovered:** 2026-05-27 during `.env.example` drift audit.

**Context:** `PERMISSION_SECRET` was originally a required HMAC field. It has since been
deprecated (`AINDY/config.py:36`: "HMAC removed; kept for backward compat"). The field now
has `default=""` and no validator. Three scaffolding call sites remain:

- `tests/conftest.py:65` — `os.environ.setdefault("PERMISSION_SECRET", "test-...")`
- `alembic/env.py:24` — `os.environ.setdefault("PERMISSION_SECRET", "alembic-...")`
- `scripts/check_schema_version.py:24` — dict literal with dummy value

All three `setdefault` calls are vestigial — they satisfied the old required-field constraint
but are no-ops now that the field defaults to `""`. Removed from `.env.example` in 1.0.0.

**Cleanup path (1.x hygiene pass):**
1. Remove the three `setdefault` / dict-literal call sites.
2. Remove the `PERMISSION_SECRET` field from `Settings` in `config.py`.
3. Verify `model_config` uses `extra="ignore"` (confirmed: line 251) so existing `.env` files
   with `PERMISSION_SECRET=` set do not break after the field is removed.
4. No migration needed — pydantic ignores unknown fields; operators with stale `.env` files
   are unaffected.

**Verified during investigation (2026-05-25):**
- ui-kit `tsconfig.json` has `"strict": true` — TypeScript null-safety guardrails are active.
- The `safeMap()` invariant in apps-monolith addresses a problem ui-kit's strict-mode TypeScript already prevents at compile time. No need to port the lint rule to ui-kit.
- `eslint-plugin-react-refresh` correctly absent from ui-kit (Vite HMR dev-server guard, not relevant for a published library).

---

## ENV-EXAMPLE-CONSOLIDATION-1 — Remove root .env.example forwarding stub

**Status:** CLOSED (2026-06-05)

**Implemented:** Deleted root `.env.example`. The unblock condition was already met:
`docker-compose.yml` uses `env_file: AINDY/.env`, making `AINDY/.env.example` the
self-evident canonical reference. The forwarding stub was no longer earning its keep.

---

## CONFIG-ENV-EXAMPLE-DRIFT-1 — No automated check for .env.example / Settings drift

**Status:** CLOSED (2026-06-05)

**Implemented:**
- `scripts/check_env_example_coverage.py` — AST-parses all `AINDY/**/*.py` for
  `os.getenv()` / `os.environ.get()` calls and `Settings` field names; parses
  `AINDY/.env.example` for all variable names (commented-out and uncommented);
  reports uncovered gaps. Exclusion list covers test-only, OS/system, deprecated
  aliases, Docker Compose infra, and computed/internal vars.
- `python scripts/check_env_example_coverage.py --verbose` for full counts.
- `python scripts/check_env_example_coverage.py --strict` exits 1 on any gap
  (for future enforcement).
- Added as advisory CI step in `.github/workflows/runtime-ci.yml` ("Check
  env-example coverage (advisory)") — runs, reports, exits 0 until gap list is
  resolved. Comment in CI step explains how to promote to `--strict`.

**First-run result (2026-06-05):** 68 gaps found — mostly `AINDY_PLUGIN_CONTAINER_*`,
`AINDY_PLUGIN_STRONG_SANDBOX_*`, `OPENAI_*` timeout/retry tuning, and `MONGO_*`
connection pool tuning fields not yet in `.env.example`. Gaps are advisory; each
should be reviewed and either added to `.env.example` or to the EXCLUSIONS list in
the script with a reason comment.

---

## STRIPE-SETTINGS-CLEANUP-1 — Stripe Settings fields with no readers

**Status:** CLOSED (2026-06-15)

**Discovered:** 2026-05-27 during `.env.example` drift audit.

**Resolution:** State 2 confirmed — fields are intentional placeholders for the
planned Stripe integration, not vestigial. `STRIPE_SECRET_KEY` and
`STRIPE_WEBHOOK_SECRET` added to `AINDY/.env.example` Group 18 (Payments) with
a forward-pointer to PAYMENTS-ARCHITECTURE-1. Fields remain in `config.py`.

---

## PAYMENTS-ARCHITECTURE-1 — No payments implementation behind Stripe Settings fields

**Status:** Deferred — Low Priority

**Discovered:** 2026-05-27, derived from STRIPE-SETTINGS-CLEANUP-1.

**Context:** `Settings` declares `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`
but no code reads them (confirmed by grep). If payments are part of the product
roadmap, the architecture question needs an answer before implementation begins:

1. **In this repo vs. separate service.** The runtime is the execution engine.
   Billing and subscription management are typically a separate concern (separate
   service, separate datastore, separate audit trail). Embedding Stripe logic in
   `aindy-runtime` couples billing failures to runtime availability.

2. **Which layer owns the webhook handler.** Stripe webhooks require idempotent
   handling (Stripe retries on non-200). The runtime already has an idempotency
   gate (EffectRecord / NF-1–NF-5). A webhook handler that routes through
   `SyscallDispatcher` gets idempotency for free; a standalone FastAPI route does
   not.

3. **Multi-tenant billing identity.** Who is the billing subject — the operator
   deploying the runtime, or end-users of the operator's product? Determines
   whether the Stripe customer ID lives on the operator config or on a User row.

**Resolution:** Answer the three questions above before writing any Stripe
integration code. If the answer to question 1 is "separate service," remove the
Settings fields from this repo immediately (see STRIPE-SETTINGS-CLEANUP-1).

---

## MEMORY-EMBEDDING-PROVIDER-1 — OpenAI is the sole embedding provider; no abstraction layer

**Status:** RESOLVED at mechanism level 2026-07-12 (in working tree, uncommitted) as ECOGAP-3
Phase 1 — see `docs/runtime/PROVIDER_BREADTH_PROGRAM.md` + §ECOGAP-3 above. `EmbeddingProvider`
abstraction (`embedding_providers.py`, OpenAI default + local sentence-transformers), configurable
column dimension (`AINDY_EMBEDDING_DIMENSIONS`), and a re-embed migration
(`aindy-runtime memory reembed`, real-PG verified) make a local/offline embedding backend usable
end-to-end. The original (now-historical) analysis follows; note its "pgvector is planned" framing
was stale — pgvector had already shipped, so the real work was the dimensionality/migration story.

**Discovered:** 2026-05-27 during `.env.example` drift audit (OpenAI timeout /
retry settings surfaced as the only tunable LLM parameters).

**Context:** `config.py` declares OpenAI-specific embedding and LLM settings
(`OPENAI_CHAT_TIMEOUT_SECONDS`, `OPENAI_EMBEDDING_TIMEOUT_SECONDS`,
`OPENAI_MAX_RETRIES`, `OPENAI_RETRY_BACKOFF_BASE_SECONDS`) with no equivalent
for other providers. `DEEPSEEK_API_KEY` is present but without corresponding
timeout/retry controls, suggesting DeepSeek was added as a key-only credential
without a full client integration.

The memory and embedding subsystem (`AINDY/memory/`) appears to be hardwired to
OpenAI embeddings. There is no provider-abstraction interface (e.g.,
`EmbeddingProvider` protocol) that would allow swapping to a local model, another
API provider, or a self-hosted embedding server.

The runtime already has `llm_client.py` as the provider-dispatch facade for
chat-completion calls. An `EmbeddingProvider` protocol with the same dispatch
shape (and `AINDY_EMBEDDING_PROVIDER` env var alongside the existing
`AINDY_AGENT_PLANNER_BACKEND`) keeps the architecture symmetric rather than
introducing a second dispatch pattern.

**Impact:** Operators running in air-gapped environments, cost-sensitive
deployments, or regulated environments that prohibit external API calls for memory
content cannot use the memory subsystem without code changes.

**Resolution path (when prioritized):**
1. Audit `AINDY/memory/` and `AINDY/memory/embedding_jobs.py` to confirm the
   hardwiring (grep for `openai` import and embedding API calls).
2. Define an `EmbeddingProvider` protocol with `embed(texts: list[str]) ->
   list[list[float]]`.
3. Implement `OpenAIEmbeddingProvider` as the default. Add a
   `LocalEmbeddingProvider` stub (sentence-transformers or similar) as the
   offline alternative.
4. Add `AINDY_EMBEDDING_PROVIDER: str = "openai"` to `Settings` and
   `AINDY/.env.example` Group 11 (Agent planner, or a new Embedding group).
5. Wire `AINDY_AGENT_PLANNER_BACKEND` and `AINDY_AGENT_PLANNER_MODEL` to the
   same abstraction if the planner and embedding backends should be independently
   configurable.

**Trigger:** First operator request for a non-OpenAI embedding backend, or when
the offline / air-gapped deployment profile is formally supported.

**Scoped 2026-07-12 as Phase 1 of the Provider Breadth Program —
`docs/runtime/PROVIDER_BREADTH_PROGRAM.md`.** Verified-against-code update to the sketch below:
(a) the embedding funnel is exactly two functions (`generate_embedding` /
`generate_query_embedding`) so the seam is a clean insertion point; (b) the resolution sketch's
"pgvector is planned / upstream unlock" framing is **stale — pgvector already shipped**
(`Vector(1536)` live), so the real un-addressed problem is **dimensionality + existing-vector
migration** (the `1536` literal is baked into the ORM column, the service constants, AND the DAO
similarity cast — a schema-contract change). See the program doc §3.2 for the migration options.

**Upstream unlock:** Resolving this entry also unblocks the planned pgvector
semantic similarity work. At pgvector integration time, the deployment needs to
choose an embedding provider per-deployment (OpenAI `text-embedding-3-small`,
a local sentence-transformers model, etc.) to match the vectors stored in
the index. Without a provider abstraction, pgvector support locks every
deployment to whatever embedding model is hardwired at that moment.

---

## CI-SMOKE-1 — Boot smoke workflow uses editable install; switch to PyPI wheel post-publish

**Status:** CLOSED (2026-06-15)

The workflow already installs from PyPI (`pip install "aindy-runtime==$AINDY_VERSION"`, reading
the version from `AINDY/_version.py` in the checkout). `install_mode: "pypi"` is recorded in
the TTFA artifact. The editable-install step was replaced when the workflow was authored
(2026-06-08); PYPI-PUBLISH-1 was the remaining blocker and is now closed (2026-06-14).

---

## PYPI-PUBLISH-1 — Dockerfile uses local wheel build pending PyPI publish

**Status:** CLOSED (2026-06-14)

`aindy-runtime` published to PyPI at v1.3.1. Dockerfile updated: the
ui-builder (SPA compile) and local `python -m build` stages removed;
Stage 1 now runs `pip install --prefix=/install "aindy-runtime==1.3.1"`.
`build-essential` and `libpq-dev` retained — psycopg2 still compiles from
source. The published wheel includes the Platform SPA dist via package-data.
To update the pinned version after a new release, bump the version string in
the builder stage `pip install` line.

---

## NODUS-UPGRADE-1 — nodus-lang pinned at 3.0.2; v4.0.0 available

**★ 4.1.0 → 4.2.0 bumped 2026-08-16 (#451), filed as `FR-16` by the app team.**

**The exact pin is the reason this needed a runtime release.** `Requires-Dist:
nodus-lang==4.1.0` means an app cannot adopt a nodus release on its own — `pip install
nodus-lang==4.2.0` succeeds and leaves the environment inconsistent with the runtime's declared
requirement, which is worse than a clean refusal. Reproduced: an editable install of this repo
**downgraded 4.2.0 back to 4.1.0**. The pin stays exact deliberately — hard-pinning a language
runtime is defensible — but that choice makes prompt bumping the runtime's obligation rather
than the app's problem.

**Probe checklist, and one item is NEW since the 4.1.0 bump.** `GUEST-CONFINE-1` (shipped
2026-08-15) makes guest confinement depend on the VM constructor accepting `allow_subprocess`,
`allow_network` and `allow_env`. **A silently renamed or removed argument would leave the guest
unconfined while every test that mocks the VM still passed** — so this must be verified against
the real VM, not inferred. Verified for 4.2.0: all three present with identical defaults, and
all **31** gated builtins still refused.

Re-verified alongside it: the three long-standing fragile couplings
(`nodus.services.syscall_runtime.call_syscall`, `NodusRuntime._get_active_vm`,
`register_function`), and that `register_function` **still refuses to override a builtin** —
which is the premise `NODUS-SYS-SURFACE-1`'s fail-loud guard rests on.

**4.2.0's breaking change does not reach us.** *"Every error now reports the resolved absolute
path"* is inert here: nodus errors are forwarded, never parsed — `nodus_adapter.py` and
`nodus_execution_service.py` only ever pass `result.get("error")` through, and nothing matches
on error text or location.

**Why the app team wanted it:** 4.2.0 fixes four causes behind an intermittent resume failure on
a path they run (`AINDY_REASONING_NODUS_NATIVE`) — a sweeper adopting runs it never created, an
unlocked store scan that loses records on Windows, and a 200ms resume budget sized for running a
script rather than recompiling one (`RESUME_TIMEOUT_MS`, now 30s). **They explicitly did NOT
claim it explains the runtime's own `run_reasoning_apply` returning `{'data': {}}`** — only that
the signatures match and both are load-dependent. Worth re-running the nodus tests before
assuming the 45s-limit note is the whole story.

No runtime code change was required. 167 nodus/worker/flow/simulation tests pass against 4.2.0.


**Status:** CLOSED (2026-06-11); pin last updated 2026-07-17 (4.0.5 → 4.1.0)

**Implemented:** Bumped `pyproject.toml` + `AINDY/requirements.txt` pin from `nodus-lang==3.0.2`
to `nodus-lang==4.0.3` (latest). One embedding API fix required: `nodus_worker.py` accessed
`runtime.last_vm` (removed in v4) — updated to `runtime._get_active_vm()`. No Nodus script
changes needed. `NODUS_DEVELOPER_GUIDE.md` §6 heading and §8 upgrade notes updated to reflect v4.

**2026-06-19:** Bumped to `nodus-lang==4.0.5`. No code changes required — 4.0.4 fixed
`identity.session_id()` propagation to child VMs and retry trace bleed; 4.0.5 is stability
graduations and companion tooling only. All 504 unit tests green.

**2026-07-17:** Bumped to `nodus-lang==4.1.0` (skipping 4.0.6–4.0.8). No code changes
required. Risk-probed before landing (local install of 4.1.0): the full nodus unit surface —
`test_nodus_{execution_budget,flow_compiler,runtime_contract,schedule_misfire,std_sys_guard,
tool_seam,workflow_registry}` — passes identically to 4.0.5 (61 tests), and the three
version-fragile internal couplings the runtime depends on all survived the bump:
`nodus.services.syscall_runtime.call_syscall` (the NODUS-SYS-SURFACE-1 fail-loud monkeypatch
target), `nodus.runtime.embedding.NodusRuntime._get_active_vm()`, and `register_function`
builtin registration. The install pulled no new/changed transitive deps (pip-audit
unaffected). Does NOT address NODUS-WARMPOOL-1 (cold-start wall-clock timeout — a runtime
architecture issue, not a nodus-lang concern).

---

### Index detail moved here 2026-08-18

`CLAUDE.md`'s registry line for this item had grown to 4,409 bytes — **larger than this entry**,
which inverts the arrangement: the index is status, hook and pointer; the detail belongs here.
Preserved verbatim so the trim loses nothing:

> - **NODUS-UPGRADE-1** — pin now **`nodus-lang==5.0.1`** + **`nodus-mcp>=0.1.3`** (2026-08-17; was 4.2.0 in #451/FR-16). **★ Bump BOTH packages across ALL THREE sites** (`pyproject.toml`, `AINDY/requirements.txt`, the `Install MCP extra` step in `runtime-ci.yml` — the third installs directly, not via the extra, so it silently re-resolves a constraint fixed only in the first two). **nodus-mcp 0.1.3 floated its `nodus-lang<5.0.0` cap**, which is what unblocked this; 0.1.2 made `aindy-runtime[mcp]` a flat `ResolutionImpossible` against a 5.x pin. **★ Gated-builtin discovery no longer scrapes registry source** — it broke on 5.0.0 AND 5.0.1 (loudly, via the discovery assert + `>=31` floor); **5.0.1 exposes `GATED_BUILTINS`** (`{flag: group}` with `names`/`arity`/`capability`), so use that. **★ 5.0.0 is a MAJOR bump adopted with ZERO behavioural change here**, because `GUEST-CONFINE-1` (#438) had already done its headline breaking change by hand: 5.0.0 makes `NodusRuntime` **deny-by-default**, and our one construction site (`nodus_worker.py:343`) already passed all three flags. nodus's own audits reached the same finding we did — *"the capability chokepoint was built and unused, the door propped open by registering subprocess and http by default"*. Second breaking change (a program can no longer write `.nodus/`, which could forge run records) doesn't reach us — our only `.nodus/` is `stdlib/.nodus/deps.json`, read at import, never written by a guest. The monolith constructs `NodusRuntime` **nowhere**. **Verified against the real VM: all 31 gated builtins still blocked (7/18/6, unchanged).** **★ Four confinement tests went red and NONE was a regression** — 5.0.0 rephrased denials (`allow_subprocess=False` → `Blocked: … pass allow_subprocess=True …`) so assertions now match the **flag name** not the sentence; and gated-name discovery moved to the `else:` branch's `for _name in (...)` tuple, where the old regex was also capturing the flag name out of `_denied_reason()` as 3 phantom builtins. **Distinguish cosmetic from real before touching the sandbox.** **★ The pin is EXACT, so an app cannot adopt a nodus release on its own** — `pip install nodus-lang==X` succeeds and leaves the env inconsistent with our declared requirement, and an editable install *downgrades* it back. Staying exact is deliberate; the cost is that bumping promptly is the runtime's obligation. **★ The probe checklist is now EXECUTABLE: `tests/unit/test_nodus_upgrade_contract.py`** (added #467) — it asserts the nodus surface against the **installed package**, so bump the pin and run it. Two facts it pinned: **`NodusRuntime.__init__` has NO `**kwargs`**, so a renamed confinement flag raises `TypeError` rather than silently unconfining the guest (that absence is now a test, since our confinement depends on someone else's signature); and **"nodus forbids overriding a builtin" had been a DOCSTRING ONLY** — `NODUS-SYS-SURFACE-1`'s guard rests on it, so a nodus that allowed overrides would let a guest redefine `syscall` past the guard with every test still green. **★★ It found a REAL one on its first CI run: CI had been testing `nodus-lang 4.1.0` while the wheel required 4.2.0.** `Runtime Contracts` + `Integration Tests` install `-r AINDY/requirements.txt` then `pip install -e . --no-deps` — **`--no-deps` means pyproject's pins are NEVER applied in CI**; the effective env is `requirements.txt`, which still said 4.1.0 because #451 (FR-16) bumped only `pyproject.toml`. **So every green run since #451, including the ones that signed off FR-16, exercised the version being upgraded AWAY FROM — the 4.2.0 adoption was never tested.** Fixed + guarded by `tests/unit/test_dependency_pin_agreement.py` (the two sources must agree on every shared package). **★ Bump BOTH files on any dependency change.** Still verify behaviour too: **`GUEST-CONFINE-1` depends on the VM accepting `allow_subprocess`/`allow_network`/`allow_env`, so verify all 31 gated builtins still refuse AGAINST THE REAL VM** — a renamed argument leaves the guest unconfined while every VM-mocking test still passes. Also re-check the three fragile couplings (`syscall_runtime.call_syscall`, `NodusRuntime._get_active_vm`, `register_function` still refusing builtin overrides — `NODUS-SYS-SURFACE-1` depends on that). 4.2.0's breaking change (errors carry absolute paths) doesn't reach us: nodus errors are forwarded, never parsed.


## MONITORING-GRAFANA-1 — Grafana excluded from compose monitoring profile

**Status:** CLOSED (2026-06-05)

**Implemented:**
- `monitoring/grafana/provisioning/datasources/prometheus.yml` — auto-registers the compose
  Prometheus instance as the default Grafana datasource (proxy mode, `http://prometheus:9090`,
  15 s scrape interval).
- `monitoring/grafana/provisioning/dashboards/aindy.yml` — file-provider provisioning config,
  reads dashboards from `/etc/grafana/dashboards` every 30 s.
- `monitoring/grafana/dashboards/aindy-runtime.json` — starter dashboard with 8 panels:
  System Health Tier (stat, threshold-colored), Active Executions (stat), Execution Rate 5m
  (stat, reqps), DB Pool Pressure (gauge, 0–1 with yellow at 0.7 / red at 0.9), AI Circuit
  Breaker State (stat per provider), Async Queue Depth (stat), Execution Duration p50/p95/p99
  (timeseries, seconds), Execution Total by Status (timeseries, reqps).
- `grafana` service added to `docker-compose.yml` monitoring profile: `grafana/grafana:11.6.1`,
  `GF_SECURITY_ADMIN_USER/PASSWORD` from env (default `admin/admin`), `GF_USERS_ALLOW_SIGN_UP=false`,
  provisioning + dashboards bind-mounted read-only, `grafana_data` volume, depends on Prometheus, port 3000.
- `grafana_data` volume added to compose volumes block.
- Compose header comment updated to mention Grafana.

**Usage:** `docker compose --profile monitoring up -d` → Grafana at `http://localhost:3000`.

---

## COMPOSE-PROD-PORTS-1 — Database ports published for dev convenience

**Status:** CLOSED (2026-06-05)

**Implemented:** `docker-compose.prod.yml` — Compose v2 override file that uses the
`!reset []` merge tag to clear the host port bindings on `postgres`, `redis`, and `mongo`.
All three DB services remain reachable within the compose network; only the `api` service
(8000) and `worker` service (8001) remain published to the host.

**Usage:**
```
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile full up -d
```

Requires Docker Compose v2.24+ (`!reset` merge tag). Version noted in the file header.

---

## PROMETHEUS-PIN-1 — prom/prometheus uses :latest tag

**Status:** CLOSED (2026-06-05)

Pinned `prom/prometheus:latest` → `prom/prometheus:v3.4.1` in `docker-compose.yml`
(current stable at close time). Consistent with pin-everything discipline elsewhere.

---

## MCP-BEHAVIOR-1 — MCP tool errors return isError result, not Python exceptions

**Status:** Tracked — Protocol fact. No code change needed; required knowledge for any MCP integration work.

**Discovered:** 2026-05-30 during `nodus-mcp` library implementation (`C:\dev\nodus-mcp`).

**Behavior:** When a tool handler raises a Python exception inside an MCP server, the `mcp` SDK (v1.x) catches it and returns a `CallToolResult(isError=True, content=[...])` to the client. It does **not** propagate a Python exception on the client side. `ClientSession.call_tool()` always returns successfully; callers must check `result.isError` to detect failures.

**Implication for tests:** Any test asserting that `call_tool()` raises on handler failure must instead assert `result.isError is True`:
```python
# WRONG — call_tool never raises on tool errors
with pytest.raises(Exception):
    await session.call_tool("bad_tool", {})

# CORRECT
result = await session.call_tool("bad_tool", {})
assert result.isError is True
```

**Implication for production callers:** Any AINDY route or service that calls an MCP server must explicitly check `result.isError` and handle the error content — it cannot rely on exception propagation.

**Scope:** Applies to both `NodusServer` (nodus-mcp) and any external MCP server called via `MCPClientAdapter`. Confirmed against mcp SDK 1.x on 2026-05-30.

---

## SDK Extraction

Status: COMPLETE (2026-05-23)

`aindy-sdk` extracted to standalone repo:
https://github.com/Masterplanner25/aindy-sdk-

First green CI run:
https://github.com/Masterplanner25/aindy-sdk-/actions/runs/26343161733

`AINDY/sdk/` removed from `aindy-runtime` in this commit.

47 SDK tests pass in the standalone repo.

`aindy-runtime` packaging config confirmed - no explicit sdk include
required removal. `pyproject.toml` already used `include = ["AINDY*"]`,
so removing the directory was sufficient.

---

## ALEMBIC-FRESH-DB-1 — Alembic migrations assume tables exist (non-idempotent on blank database)

Status: CLOSED (2026-05-27)

**Root cause:** Migrations 0001–0004 were written assuming the schema had already been
bootstrapped by `schema_contract.py`'s `create_all` path (the original deployment model).
On a fresh Docker deployment where `alembic upgrade head` runs before the server creates
any tables, migrations 0002–0004 failed with `UndefinedTable` because they referenced
tables (`platform_api_keys`, `execution_units`, `effect_records`, `webhook_subscriptions`,
`dynamic_flows`, `dynamic_nodes`) that didn't exist yet.

**Fix applied (2026-05-27):** Wrapped all DML (UPDATE, DELETE) and DDL (CREATE TABLE,
CREATE INDEX) statements in 0002–0004 in `DO $$ BEGIN IF EXISTS (pg_catalog.pg_tables
WHERE tablename=...) THEN ... END IF; END $$` blocks. On a blank database, the blocks
skip silently; the server's Phase 5 `_enforce_schema_guard` then calls `create_all`
which creates all runtime-owned tables with the current ORM-defined constraints.

On existing deployments the blocks run normally: dedup DML cleans up duplicate active
rows before the unique indexes are created, and effect_records is created if the migration
was authored after the original deployment.

**Remaining gap:** The hybrid `create_all` + alembic approach means a fresh deployment's
alembic revision history (stamped at `0004`) doesn't reflect that alembic actually ran
the migrations — the tables were created by `create_all`. This is operationally correct
but conceptually impure. A proper fix would be to write `0001` as a full `CREATE TABLE`
migration for all 32 runtime-owned tables, making alembic the single source of truth.
Deferred: the monolith alembic history and the runtime history use separate version
tables, so there's no collision risk; the hybrid approach is sustainable for 1.x.

---

## COMPOSE-PGVECTOR-1 — postgres image must be pgvector/pgvector:pg16

Status: CLOSED (2026-05-27)

The compose file originally used `postgres:16-alpine`. The runtime's `memory_nodes` table
has an `embedding VECTOR(1536)` column (from `pgvector.sqlalchemy.Vector`), which requires
the PostgreSQL `pgvector` extension. The stock image does not ship it; schema bootstrap
fails with `type "vector" does not exist`.

**Fix applied (2026-05-27):**
- Switched to `pgvector/pgvector:pg16` in `docker-compose.yml`.
- Added `docker/init-pgvector.sql` (mounted to `/docker-entrypoint-initdb.d/`) to run
  `CREATE EXTENSION IF NOT EXISTS vector` on first database initialization.
- Added a Quickstart note in `README.md` explaining the requirement for operators who
  bring their own PostgreSQL instance.

---

## PACKAGING-DEP-1 — `packaging` not propagated to Docker runtime stage

Status: CLOSED (2026-05-27)

`limits` (a transitive dependency via `slowapi`) declares `packaging` as a runtime
requirement. In the multi-stage Dockerfile, `pip install --prefix=/install` skips
`packaging` because it is already satisfied at the builder-stage system level (installed
as a build tool peer of `pip`/`setuptools`). The runtime stage only copies `/install`,
so `packaging` was absent and `import packaging` failed at server startup.

**Fix applied (2026-05-27):**
- Added `"packaging>=24.0"` and `"limits==5.8.0"` as explicit dependencies in
  `pyproject.toml` (pinning `limits` prevents a silent upgrade to a future version that
  may change `packaging` requirements).
- Added `pip install --prefix=/install --ignore-installed "packaging>=24.0"` after the
  wheel install in the Dockerfile builder stage to force `packaging` into the `/install`
  prefix regardless of system-level satisfaction.

---

## COMPOSE-HOST-1 — aindy-runtime serve defaults to 127.0.0.1; breaks Docker port mapping

Status: CLOSED (2026-05-27)

`runtime_only.py`'s `_serve()` defaults `AINDY_HOST=127.0.0.1`. Inside a Docker container
this means the server only accepts connections from within the container, making the
published port (`0.0.0.0:8000 → 8000/tcp`) unreachable from the host.

**Fix applied (2026-05-27):** Added `AINDY_HOST: "0.0.0.0"` to the compose `api` service
environment block. This is compose-only: bare `aindy-runtime serve` outside compose
correctly defaults to localhost for security.

---

## PLATFORM-UI-ENV-1 — VITE_API_BASE_URL bakes localhost into the production bundle

**Status:** CLOSED (2026-06-05)

**Discovered:** 2026-05-28 during PLATFORM-AUTH-ACQUISITION-1 implementation.

**Resolution:** Changed the fallback in `@aindy/ui-kit` `src/api/_core.js` from
`"http://localhost:8000"` to `""`. When `VITE_API_BASE_URL` is unset, `API_BASE`
is now an empty string. `buildApiUrl()` already had a falsy guard (`API_BASE ?
... : path`) so all API calls become relative paths (e.g. `/auth/login`) that the
browser resolves against the current origin — correct since the SPA and API are
always co-served.

Local dev gap (Vite on port 5173, API on 8000) is closed by `server.proxy` entries
added to `platform/vite.config.ts` — no `VITE_API_BASE_URL` env var required for
local dev. `VITE_API_BASE_URL` still works as an explicit override for non-standard
host configurations.

Bundle verified: `grep localhost:8000` returns no matches in the rebuilt
`AINDY/platform/dist/assets/*.js`.

---

## PLATFORM-AUTH-ACQUISITION-1 — Platform SPA login + admin bootstrap

**Status:** CLOSED (2026-05-28)

**What was implemented:**

*Frontend (`platform/src`):*
- `LoginPage.tsx` — form calling `useAuth().login()` against `VITE_API_BASE_URL/auth/login`.
  On success, stores token via `AuthContext` and navigates to `/` within the router tree.
- `NotAdmin.tsx` — terminal "access denied" view with logout button. Rendered (not navigated
  to) when authenticated but `is_admin=false`. No redirect loop possible.
- `PlatformApp.tsx` rewritten — `/login` route lives outside `PlatformGuard`; guard uses
  `<Navigate to="/login" replace />` for unauthenticated (React Router, respects
  `basename="/platform"`); authenticated-but-not-admin renders `<NotAdmin />` in-place.
  `redirectToApp` / `window.location.href` / `VITE_APP_BASE_URL` dependency removed.

*Backend:*
- `AINDY_BOOTSTRAP_ADMIN_EMAIL` env var (config.py + .env.example) — grant-only, idempotent,
  no-op if user not yet registered, logged at INFO if absent.
- `startup.py` Phase 5.5 — `_bootstrap_admin_email()` runs after schema guard, before dev
  key bootstrap.
- `aindy-runtime auth promote-admin <email>` CLI subcommand — grant-only, exits 0 if already
  admin, exits 1 with guidance if user not found, requires DATABASE_URL.

*Routing (`routing.py`):*
- `_SPAStaticFiles.get_response()` now discriminates route misses from asset misses:
  paths under `assets/` return 404 (not index.html); all other unmatched paths fall back
  to index.html.

**Verified end-to-end (2026-05-28):**
- `GET /platform/` → 200 index.html
- `GET /platform/login` → 200 index.html (SPA handles the route)
- `GET /platform/assets/does-not-exist.js` → 404 (not HTML fallback)
- `GET /platform/assets/index-BGunogPh.js` → 200 application/javascript
- `POST /auth/register` → 201 with JWT (`is_admin: false`)
- `AINDY_BOOTSTRAP_ADMIN_EMAIL=admin@aindy.local` + restart → `granted is_admin=True` in logs
- Second restart → `already admin, no-op` (idempotency confirmed)
- `aindy-runtime auth promote-admin ops@aindy.local` → `ok: granted` (no restart needed)
- Second run → `ok: already admin. No change made.`
- Unknown email → exit 1, clear guidance
- `POST /auth/login` after promotion → JWT with `is_admin: true`

PLATFORM-UI-ENV-1 (localhost baked into bundle) closed 2026-06-05 — relative-URL fix.

---

## PLATFORM-UI-KIT-1 — @aindy/ui-kit npm publish gap

**Status:** CLOSED (2026-05-28)

**What was implemented:**

- `src/api/auth.js` in `aindy-ui-kit`: added `.then(unwrapEnvelope)` to `loginUser`,
  `registerUser`, and `bootIdentity`. All three were returning the raw
  `{ data: {...} }` envelope; callers expecting unwrapped payloads silently received
  the wrong shape. Second-order effect: `bootIdentity` now correctly surfaces
  `system.runtime.boot_mode`, fixing the silent post-login redirect misfire in
  `PlatformHomeRedirect`.
- Published `@aindy/ui-kit@1.0.1` to npm. `platform/package.json` bumped to
  `^1.0.1` — `npm install` now pulls the corrected version from the registry.
- Dockerfile `ui-builder` stage added: `npm ci` + `npm run build` runs inside the
  image build from the registry-pinned ui-kit. `docker compose build --no-cache`
  from a fresh clone is now self-contained — no prior local UI build required.
- `.dockerignore` updated: `AINDY/platform/dist/` and `platform/node_modules/`
  excluded from build context to prevent stale local state from leaking in.

**Verification gate:** fresh clone → `docker compose build --no-cache` →
`docker exec aindy-runtime-api-1 ls .../AINDY/platform/dist/` shows non-empty dist →
`curl -I http://localhost:8000/platform/` returns 200.

---

## RIPPLE-ROUTES-001 — RippleTraceViewer load-trace issues bare monolith-era path; no served runtime route

**Status:** Open — deferred until runtime serves a per-trace load route.

**Discovered:** 2026-06-03 during RippleTraceViewer walk (ROUTES audit follow-on).

**Symptom — Bare path, no prefix:**
`RippleTraceViewer`'s "Load Trace" button calls `getRippleTraceGraph(traceId)` in
`platform/src/api/rippletrace.js`. That function correctly reads
`ROUTES.RIPPLETRACE.TRACE_GRAPH(traceId)` from the route table. However, `TRACE_GRAPH`
was defined in the monolith-era RIPPLETRACE group with `BASE = ""`, so it resolves to
`GET /rippletrace/${traceId}` — a bare top-level path with no `/platform` or `/apps` prefix.
The route is unserved by the runtime (returns 404). The route-table abstraction is honoured;
the problem is that the constant itself pointed at a monolith path that was never ported to
the runtime.

**Disposition:** Flag-off. `FEATURE_FLAGS.RIPPLETRACE_VIEWER = false` hides the RippleTrace
sidebar NavLink. The `/trace` route in `PlatformApp.tsx` remains mounted; only the NavLink is
hidden. No runtime fix is possible because there is no runtime route to repoint at.

**Two-condition unblock:**
1. The runtime serves a per-trace load route (e.g., `/platform/observability/rippletrace/{id}`)
   visible in the runtime OpenAPI.
2. A served ROUTES constant (e.g., `ROUTES.OPERATOR.RIPPLETRACE_TRACE`) is added for that path
   and `rippletrace.js:getRippleTraceGraph` is updated to use it.

The component already reads from ROUTES correctly — no architectural rewire needed, only a
new served constant and a one-line update in `rippletrace.js`.

---

## OPER-DEFER-001 — `/platform/flows/strategies` not served by runtime

**Status:** CLOSED (2026-06-15)

`GET /platform/flows/strategies` implemented in `AINDY/routes/platform/flows_router.py`.
Returns registered flow strategies from the plugin registry plus scheduling metadata
(priority tiers, max per cycle, dispatch model) and all retry policy definitions.
`get_all_flow_strategies()` added to `AINDY/platform_layer/registry.py`.
`FEATURE_FLAGS.OPERATOR_FLOW_STRATEGIES` flipped to `true` in `platform/src/api/_routes.js` —
the "Strategies" tab in `FlowEngineConsole` is now live.
6 unit tests in `tests/unit/test_flow_strategies_endpoint.py`.

---

## OPER-DEFER-002 — `/automation/logs` group not served by runtime

**Status:** CLOSED (2026-06-15)

Three routes implemented in `AINDY/routes/automation_router.py` and registered directly in
`AINDY/routing.py` (bypassing `require_execution_context`, auth via `require_admin_principal`):
- `GET /automation/logs` — list with status/source/limit filters; response `{ logs, count }`
- `GET /automation/logs/{log_id}` — detail; 404 on unknown id
- `POST /automation/logs/{log_id}/replay` — calls `replay_task()`; 404/409 on failure

`JobLog` model (`AINDY/db/models/job_log.py`) was already present in the runtime.
`FEATURE_FLAGS.OPERATOR_AUTOMATION_LOGS` flipped to `true` in `platform/src/api/_routes.js` —
the "Automation" tab in `FlowEngineConsole` is now live.
10 unit tests in `tests/unit/test_automation_logs_endpoint.py`.

---

## AGENT-EVAL-001 — Swallowed trigger-evaluator exception + SUCCESS-on-defer envelope contract

**Status:** CLOSED (2026-06-03)

**Location:** `AINDY/agents/autonomous_controller.py` — `evaluate_trigger()`.

**What was implemented:**

Removed the `try/except Exception` block in `evaluate_trigger()` (lines 33-37 pre-fix). The
evaluator call is now bare — any exception propagates through `evaluate_live_trigger` →
`_decision_or_defer_response` → `create_agent_run_runtime` → `ExecutionPipeline.run()`, which
catches it at its generic handler and returns `ExecutionResult(success=False, status_code=500)`.
`_execute_agent` raises `HTTPException(500, detail=str(exc))`, which the runtime exception
handler formats as `{"error": "http_error", "message": "<exception message>", "details": null}`.

Legitimate `"defer"` decisions (evaluator returning `{"decision": "defer", ...}`) are unaffected:
`_decision_or_defer_response` processes them as before → 202 DEFERRED with the evaluator's
actual reason. The no-evaluator path (`evaluator is None → _decision("defer", ..., "no trigger
evaluator registered")`) is also unaffected.

**Evidence:** `tests/unit/test_agent_eval_contract.py`:
- `test_evaluator_crash_surfaces_as_500` — regression test; injected exploding evaluator → 500
  with exception message; zero AgentRun rows written.
- `test_evaluator_genuine_defer_returns_202` — legitimate defer path preserved; 202 DEFERRED
  with evaluator's reason.
- `test_happy_path_evaluator_execute_calls_create_run` — approve path flows correctly → 200.

**Remaining gap (not in scope):** The `execution_envelope.status = SUCCESS` on the 202 DEFERRED
path is a separate envelope-contract issue shared with SCHED-001/002/003 (same swallow family —
this fix is the pattern; apply to SCHED-* in a future pass).

---

## AGENT-APPROVE-001a — Approve idempotency: concurrent race guard (CAS)

**Status:** CLOSED (2026-06-03)

**Discovered:** 2026-06-03 during AGENT-APPROVE-001 idempotency audit.

**Problem:** `approve_run()` (`AINDY/agents/agent_runtime/approvals.py`) used a non-atomic
read-then-act pattern to guard the `pending_approval → approved` transition. Under PostgreSQL
READ COMMITTED, two concurrent sessions could both read `status = "pending_approval"` before
either committed, both pass the Python check, and both call `execute_run` — doubling execution.

**Fix:** Replaced the Python-level check with an atomic `UPDATE ... WHERE status =
'pending_approval'` CAS. Only one concurrent caller gets `rowcount = 1`; all others see
`rowcount = 0` and return early without calling `execute_run`. The DB row lock ensures
atomicity under PostgreSQL READ COMMITTED.

**Tests:** `tests/unit/test_agent_approve_idempotency.py` — three shapes:
- `test_sequential_double_approve_executes_once` — second approve returns run state, no re-execute
- `test_repro_cancel_retry_executes_once` — second approve sees "executing" status, CAS rowcount=0
- `test_concurrent_race_repro_cas_rowcount` — proves CAS returns rowcount=0 after first commit

**Remaining gap:** The async refactor (return 202 immediately, dispatch execution to background)
is tracked separately in AGENT-APPROVE-001b.

---

## AGENT-APPROVE-001b — Approve endpoint blocks on synchronous execution; exceeds client timeout on slow tools

**Status:** CLOSED (2026-06-04)

**Implemented:** `approve_run()` (`approvals.py`) now fires `execute_run` in a daemon
background thread with its own `SessionLocal` session, returning `_run_to_dict(run)`
immediately. The HTTP approve request returns with `status: APPROVED` in milliseconds;
clients poll `GET /apps/agent/runs/{id}` for status transitions. Tests updated to use
`threading.Event` for deterministic background-thread coordination.

**Watchdog implemented (2026-06-06):** `_recover_orphaned_approved_runs()` in
`scheduler_service.py` runs every 5 minutes. It queries `AgentRun` rows where
`status='approved'` and `approved_at < now - 10 min` (cap 50 per sweep), then
re-dispatches `execute_run` in a fresh daemon thread for each. `execute_run` guards on
`status == 'approved'` at entry so re-dispatch is safe if the original thread recovered
late. Tests: `tests/unit/test_agent_approve_watchdog.py` (4 shapes). All gaps closed.

**Discovered:** 2026-06-03 during agent walkthrough (Phase 2).

**Symptom (repro-specific):** Approving a `pending_approval` run with a `memory.recall` step
held the HTTP request open through the full tool execution. The execution exceeded the browser's
default 30-second fetch timeout — the approve request was cancelled client-side
(`(cancelled)`, 30.02 s in the network panel) while the server completed the approval and
execution successfully. The UI showed a false failure / "needs retry"; the retry immediately
succeeded because server state was already correct (run already `COMPLETED`). The
response-vs-reality mismatch: the client believes the operation failed; the server knows it
succeeded.

**Root cause:** `approve_agent_run_runtime` calls `_decision_or_defer_response` (trigger
evaluation, synchronous subprocess), then immediately calls `approve_run` → `execute_run` —
running the full tool-execution loop on the request thread. "Approve to start execution
immediately" is implemented as a synchronous call, so request duration scales linearly with
tool runtime. One slow tool (or a multi-step plan) pushes the request past any client timeout.

**Severity:** No data loss observed; AGENT-APPROVE-001a CAS fix ensures retries are safe.
But UX is broken: a client-cancelled request leaves the user uncertain whether approval
landed. The gap widens with slower tools and multi-step plans — a long plan would always
false-timeout regardless of client configuration.

**Fix direction:** Ack-then-execute-async. `POST /apps/agent/run/{id}/approve` returns
promptly (`202 Accepted`, `"approved; execution started"`) and dispatches execution to a
background thread or task queue. The UI polls `GET /apps/agent/runs/{id}` (or subscribes to
an event stream) for status changes. Decouples request duration from tool runtime entirely;
client always gets a definitive success/failure for the approve action itself within
milliseconds.

**Liveness gap — orphaned `approved` state:** The CAS fix (001a) only fires from
`pending_approval`. If the winning caller's execution dies mid-flight (process crash, OOM,
SIGKILL — any unhandled termination, not a caught failure), the run is stranded in `approved`
forever: `execute_run` never ran to completion, but no subsequent caller can re-trigger it
because `status != "pending_approval"`. No retry, no recovery path. The async design **must**
include a watchdog/reaper that detects runs stuck in `approved` beyond a deadline and either
re-enqueues them for execution or marks them `failed` with a recoverable reason. This is a
liveness gap, not a correctness gap — but it means the CAS fix alone is not a complete
solution in the presence of process crashes.

**Family:** Same response-vs-reality mismatch class as AGENT-EVAL-001 (client receives
wrong status relative to actual server outcome). Cross-reference AGENT-EVAL-001 and any
EXEC-CONTRACT entry when fixing — all three share the "envelope status diverges from actual
server outcome" root shape.

---

## AGENT-RESLIMIT-001 — cpu_time_ms accounting semantics: field measures wall-clock, not CPU time

**Status:** CLOSED (2026-06-05) — field renamed to `wall_time_ms` across all layers; schema migration 0005 added; `SCHEMA_CONTRACT_VERSION` bumped to "2026-06-05"; `MAX_CPU_TIME_MS` → `MAX_WALL_TIME_MS` (env var `AINDY_QUOTA_CPU_MS` unchanged for operator compatibility).

**Discovered:** 2026-06-03 during AGENT-APPROVE-001a live smoke test.

**Symptom:** A single-step agent run (`memory.recall` with OpenAI embedding API calls)
hit `RESOURCE_LIMIT_EXCEEDED: eu exceeded cpu_time_ms limit (34021 > 30000)`. The run was
marked `failed`; the step itself completed successfully. Total request duration: 65s — the
execution thread blocked the approve request for 65s before returning a `FAILED` result.
The 65s duration is also another data point for AGENT-APPROVE-001b's priority.

**Root cause:** `cpu_time_ms` measures monotonic wall-clock elapsed time (not CPU time).
Every timing path — `runner_steps.py:112–143`, `execution_pipeline/resources.py:120`,
`syscall_dispatcher.py:666` — uses `time.monotonic()`. Network I/O wait (OpenAI embedding
calls, database round-trips) is counted in full. A realistic single agent step with three
embedding round-trips is ~34 s wall-clock time. The field name is a misnomer.

**Scope:** Per-run, accumulated across all steps. Each node's elapsed wall-clock time is
added to `UsageSnapshot.cpu_time_ms` via `+=`. `check_quota` compares the accumulated
total before each step.

**Mitigation applied (v1.0.0):** Default raised from 30 000 ms to 300 000 ms (5 minutes)
via `AINDY_QUOTA_CPU_MS`. Documented in `AINDY/.env.example` (Group 12) with a clear
warning that the field measures wall-clock time. Default pinned by
`tests/unit/test_resource_quota_defaults.py`. Note: raising the cap makes synchronous
approve (AGENT-APPROVE-001b) more likely to exceed client timeouts on slow workloads —
that is the correct trade-off until 001b lands.

**Remaining fix:** Accounting semantics — either:
1. Exclude network I/O wait from the timer (measure actual CPU time, e.g. via
   `os.times()` or by wrapping only CPU-bound segments).
2. Rename the field to `wall_time_ms` (or split into `wall_time_ms` + `cpu_time_ms`)
   so the name matches what is measured.

This requires changes to `ResourceManager.record_cpu`, all three timing call sites,
the `UsageSnapshot` dataclass, the DB column in `ExecutionUnit`, and the API envelope.
Schema change → SCHEMA_CONTRACT_VERSION bump required.

**Coupling:** `AINDY/runtime/nodus_worker.py:209` and `nodus_runtime_adapter.py` have
a parallel per-script subprocess timeout (also defaulting to 30 000 ms via
`max_execution_ms`). That is a separate Nodus VM execution limit, not the ResourceManager
quota. Operators who need individual Nodus script steps > 30 s must also configure that
timeout — it is not controlled by `AINDY_QUOTA_CPU_MS`.

---

## ROUTES-CONSUMER-SPLIT-1 — Shared `@aindy/ui-kit` ROUTES table serves both monolith and runtime; quarantine as committed breaks monolith on next publish

**Status:** CLOSED (2026-06-03) — resolved: Option B, shared table universal, policy consumer-local, annotations carry the audit map.

**Discovered:** 2026-06-03 during blast-radius check following `_routes.js` quarantine audit.

**Root cause:** `@aindy/ui-kit/src/api/_routes.js` is the single ROUTES source of truth for
**both** consumers. Both shims are identical:

```js
// platform/src/api/_routes.js (aindy-runtime)
export { ROUTES, FEATURE_FLAGS } from "@aindy/ui-kit";

// client/src/api/_routes.js (aindy-apps-monolith)
export { ROUTES } from "@aindy/ui-kit";
```

The quarantine commits (`002de1e`, `77d9956`) removed ANALYTICS, SOCIAL, TASKS, RIPPLETRACE,
ARM, MASTERPLAN, FREELANCE, IDENTITY, and SEARCH from `ROUTES` in ui-kit source. The monolith
has **94 callsites** across these groups that will `TypeError: Cannot read properties of
undefined` at call-time the moment the monolith upgrades to a version of `@aindy/ui-kit`
that includes the quarantine:

| Group | Callsites |
|---|---|
| RIPPLETRACE | 27 |
| ANALYTICS | 21 |
| MASTERPLAN | 13 |
| ARM | 8 |
| SEARCH | 8 |
| SOCIAL | 6 |
| IDENTITY | 4 |
| TASKS | 4 |
| FREELANCE | 3 |
| **Total** | **94** |

**Current safety window:** The monolith is pinned to `@aindy/ui-kit@^1.0.0`, installed at
`1.0.0`. Quarantine commits are post-`1.0.4`. As long as `1.0.5+` (or any version including
the quarantine) is not published to npm, the monolith is unaffected. Publishing triggers the
break.

**Two architectural options:**

1. **Per-consumer route overlay** — Keep all routes in the shared table (un-quarantine in
   ui-kit source). Each consumer applies its own filter. The runtime platform SPA applies
   the "only served routes" filter locally; the monolith keeps all groups. The quarantine
   comment block currently in ui-kit source moves to `platform/src/api/_routes.js` as a
   runtime-side filter — not applicable because the platform SPA's shim is a one-liner
   re-export; it would need to become an explicit re-export with the monolith-group keys
   omitted.

2. **Un-quarantine from shared + gate runtime-side** — Restore the commented-out route
   groups in `@aindy/ui-kit/src/api/_routes.js` (making the quarantine transparent to both
   consumers), and gate runtime access at the platform SPA level only (FEATURE_FLAGS,
   NavLink hiding, API module guards). The monolith retains its routes; the runtime SPA
   never renders NavLinks for unserved groups; API module functions in the runtime SPA are
   guarded individually. More code in the platform SPA; zero monolith breakage.

**What API-MODULE-DRIFT-1 depends on:** The fix shape for platform SPA API modules
(`rippletrace.js`, `analytics.js`, `platform.js`) referencing quarantined ROUTES groups
is determined by which option is chosen here.

~~**Do not publish `@aindy/ui-kit@1.0.5+`**~~ — restriction lifted 2026-06-03. Option B is
implemented: all route groups are in the shared table; publish is safe. Monolith compatibility
verified 2026-06-06 against full import surface (ROUTES + 20 other symbols). See closure note.

---

## API-MODULE-DRIFT-1 — Quarantined route groups left platform SPA API modules reading `undefined` → `TypeError`

**Status:** CLOSED (2026-06-03) — dissolved by Option B: all quarantined groups restored to the shared table; all 64 module ROUTES.* references now resolve; graceful-404 behavior restored.

**Discovered:** 2026-06-03 during `_routes.js` audit follow-on.

**Root cause:** When route groups are quarantined (commented out) in
`@aindy/ui-kit/src/api/_routes.js`, any API-module function in the platform SPA that
reads `ROUTES.<QUARANTINED_GROUP>.*` receives `undefined` instead of a path string.
Calling `undefined(args)` or using `undefined` as a URL in `authRequest()` throws
`TypeError` at call-time — a regression from the pre-audit behavior of silently returning
a graceful 404 (wrong but non-crashing).

**Affected modules and callsite counts:**

| File | Quarantined group | Function count |
|---|---|---|
| `platform/src/api/rippletrace.js` | `ROUTES.RIPPLETRACE` | 16 |
| `platform/src/api/analytics.js` | `ROUTES.ANALYTICS` | 19 |
| `platform/src/api/platform.js` (unserved subset) | `ROUTES.PLATFORM.*` (quarantined constants) | 4 |

**Disposition principle:** API module functions follow their route group. When a route group
is quarantined, its API module must be either (a) quarantined alongside it (module functions
guarded or removed), or (b) the route group must be restored (un-quarantine).

**Why not implemented now:** The correct fix shape depends on the ROUTES-CONSUMER-SPLIT-1
architectural decision:
- If Option 1 (per-consumer overlay): route groups remain in the shared table; platform SPA
  API modules continue reading them; no TypeError.
- If Option 2 (un-quarantine shared + gate runtime-side): same — modules keep their routes.
- If quarantine stays in the shared table: each affected module must be guarded (either
  deleted, `if (ROUTES.RIPPLETRACE)` guarded, or the consuming component gated via
  FEATURE_FLAGS before calling the module).

**Interim risk:** Any platform SPA component that calls a function from `rippletrace.js`,
`analytics.js`, or the unserved-subset functions in `platform.js` will throw `TypeError` at
call-time, not at import time. Components that are never navigated to are safe; components
reachable via the router but whose NavLink is hidden (e.g., RIPPLETRACE_VIEWER gated) are
safe as long as the user does not navigate directly to the route. The quarantine does not
affect the runtime's primary flows.

---

## AGENT-API-001 — `getAgents` / `recallFromAgent` / `getFederatedMemory` in platform SPA reference never-existed ROUTES constants

**Status:** CLOSED (2026-06-03) — fixed in `platform/src/api/agent.js`; all three functions now use correct `ROUTES.MEMORY.*` constants. Consumer `AgentRegistry.jsx` (lines 4–6, 58/267/455) unaffected — no component changes needed.

**Discovered:** 2026-06-03 during `_routes.js` audit, agent.js review pass.

**Location:** `platform/src/api/agent.js`

**Bug:** Three exported functions reference `ROUTES.AGENT.*` constants that were never
defined in any version of `@aindy/ui-kit`:

| Function | Uses | Should use |
|---|---|---|
| `getAgents()` | `ROUTES.AGENT.LIST` | `ROUTES.MEMORY.AGENTS` |
| `recallFromAgent(agentId, query)` | `ROUTES.AGENT.RECALL(agentId)` | `ROUTES.MEMORY.AGENT_RECALL(agentId)` |
| `getFederatedMemory(query)` | `ROUTES.AGENT.FEDERATED_MEMORY` | `ROUTES.MEMORY.FEDERATED_RECALL` |

`ROUTES.AGENT.LIST`, `ROUTES.AGENT.RECALL`, and `ROUTES.AGENT.FEDERATED_MEMORY` do not exist
— not in the audited 1.0.0–1.0.4 builds, not in any reconcile state. All three were always
`undefined`. All three calls throw `TypeError` at call-time.

The correct constants (`ROUTES.MEMORY.AGENTS`, `ROUTES.MEMORY.AGENT_RECALL`,
`ROUTES.MEMORY.FEDERATED_RECALL`) are served, correctly defined, and used correctly in the
monolith's `client/src/api/agent.js`.

**Consumer:** `platform/src/components/platform/AgentRegistry.jsx`
- Import: lines 4–6 (`import { getAgents, recallFromAgent, getFederatedMemory }`)
- Call sites: line 58 (`getAgents()`), line 267 (`recallFromAgent(...)`), line 455 (`getFederatedMemory(...)`)

**Fix:** Update the three function bodies in `platform/src/api/agent.js` to use the correct
`ROUTES.MEMORY.*` constants. This is a one-file fix independent of the ROUTES-CONSUMER-SPLIT-1
architectural decision (the target constants are in the served MEMORY group, unaffected by
quarantine). No ui-kit change needed.

**Follow-on (2026-06-06):** `ROUTES.AGENT.RECOVER` and `ROUTES.AGENT.REPLAY` constants added
to ui-kit; `recoverAgentRun()` and `replayAgentRun()` added to `platform/src/api/agent.js`.
No SPA component consumes recover/replay yet — first component that needs orphan recovery or
run replay will drive the UI work.

---

## SCHED-001/002/003 — `/platform/observability/scheduler/status` returns 500 in platform-only profile

**Status:** CLOSED (2026-06-04)

**Root cause:** The endpoint called `_run_flow_observability("observability_scheduler_status", ...)` which
invoked the `observability_scheduler_status` flow. That flow node checks for `task_is_background_leader`
via the plugin registry and for `BackgroundTaskLease` rows. In the platform-only profile, neither the tasks
domain nor the `background_task_lease` table is available, so the node returned `{"status": "FAILURE"}`,
propagating as HTTP 500.

**Fix (2026-06-04):** Replaced the flow engine call with `_build_scheduler_status_payload(db)` in
`AINDY/routes/observability_router.py`. The new helper:
- Reads `scheduler_running` directly from `scheduler_service.get_scheduler()`
- Looks up `task_is_background_leader` from the plugin registry; sets `is_leader=null` and
  `tasks_domain_available=false` when the tasks domain is absent (platform-only profile)
- Populates `stuck_run_watchdog` directly from `get_last_scan_result()`
- Never calls the flow engine — zero flow dependency

`FEATURE_FLAGS.OPERATOR_SCHEDULER_STATUS` flipped to `true` in `platform/src/api/_routes.js`.

---

## ROUTE-REG-001 — `watcher_router` and `db_verify_router` are never registered; their endpoints return 404

**Status:** CLOSED (2026-06-03)

**Discovered:** 2026-06-03 during `PUBLIC_RUNTIME_SURFACES.md` review.

**Location:**
- `AINDY/routes/watcher_router.py` — `APIRouter(prefix="/watcher", ...)`
- `AINDY/routes/db_verify_router.py`

**Bug:** Both router files exist and define endpoints, but neither is included in
`ROOT_ROUTERS`, `PLATFORM_ROUTERS`, `APP_ROUTERS`, or any other group in
`AINDY/routes/__init__.py`. Neither is imported or registered anywhere in
`AINDY/routing.py`, `AINDY/startup.py`, or `AINDY/main.py`. All defined endpoints
return 404 in production.

**Impact:**
- `POST /watcher/signals` and `GET /watcher/signals` — used by the watcher client
  (`aindy_sdk/watcher/signal_emitter.py`). The watcher client cannot deliver signals
  until this router is registered.
- `db_verify_router` endpoints — unknown; the file's purpose needs investigation
  before registration.

**Fix for watcher_router:** Add `watcher_router` to `ROOT_ROUTERS` in
`AINDY/routes/__init__.py`. The router uses `dependencies=[Depends(verify_api_key)]`
(API key auth, correct for a headless client process) and its prefix `/watcher` gives
the final paths `/watcher/signals`. Mounting in ROOT_ROUTERS (no `/apps` prefix)
matches the URL the watcher client already targets.

```python
# AINDY/routes/__init__.py
from AINDY.routes.watcher_router import router as watcher_router

ROOT_ROUTERS = [
    health_router,
    auth_router,
    watcher_router,   # add here
]
```

**Fix for db_verify_router:** Investigate intended audience and prefix before mounting.

---

## OPER-EXEC-001 — Thread-mode async is not durable; distributed mode not wired as production default

**Status:** CLOSED (2026-06-06)

**Problem:** `EXECUTION_MODE=thread` (the default) uses a `ThreadPoolExecutor` with a 100-job in-memory queue. Any job in-flight or queued when the API process restarts is permanently lost — no DLQ, no recovery. The distributed mode (Redis queue + separate worker process, `--profile full`) is fully implemented and handles restarts correctly via `requeue_stale_jobs()`, but operators could spin up the worker without the API ever routing to it if `EXECUTION_MODE=thread` remained in `.env`.

**Root cause:** The `docker-compose.yml` worker service did not set `EXECUTION_MODE=distributed`, so the compose `--profile full` command brought Redis and the worker online while the API continued dispatching to the in-process thread pool. Worker was idle; jobs remained ephemeral.

**Fix applied:**
- `docker-compose.yml` worker service: added `EXECUTION_MODE: distributed` to the `environment:` block — overrides `.env` so the worker is never silently idle.
- `docker-compose.yml` header: updated the "Production-shaped" comment to explicitly state `EXECUTION_MODE=distributed` must also be set in `.env` for the API.
- `AINDY/.env.example`: added a WARNING under the `EXECUTION_MODE=thread` line documenting that thread mode has no durability and directing operators to `distributed` + `--profile full` for production.

**No code change required.** The distributed queue backend, worker process, DLQ, stale-job recovery, and retry backoff were already production-grade; the gap was purely an operational default.

---

## OPER-EXEC-002 — ContextVar state not propagated to ThreadPoolExecutor worker threads

**Status:** CLOSED (2026-06-06)

**Problem:** `ThreadPoolExecutor.submit(fn)` runs `fn` in a fresh context where all `ContextVar` values revert to their defaults. The trace context (`trace_id`, `parent_event_id`, `pipeline_active` in `platform_layer/trace_context.py`) and syscall context (`syscall_trace_id`, `syscall_eu_id` in `kernel/syscall_dispatcher.py`) were lost at every async thread boundary. Events and logs emitted from worker threads had no trace_id / eu_id — cross-thread correlation was impossible. Distributed mode already restored context from `QueueJobPayload.context` on the worker; thread mode had no equivalent.

**Fix applied:**
- `AINDY/core/execution_dispatcher.py:453` — `copy_context()` snapshot captured before submit; `_ctx.run` passed as the callable so the worker thread inherits the full context.
- `AINDY/platform_layer/async_job_service.py:620` — same pattern for the `submit_async_job()` thread-pool path.
- `tests/unit/test_contextvar_thread_propagation.py` — 3 shapes verifying `trace_id`, `eu_id`, and `pipeline_active` each propagate correctly.

**`copy_context()` is Python stdlib (3.7+), zero new dependencies.**

---

## SYSMAX-1 — Thread-mode queue hard cap is still the .env.example default

**Status:** Partially mitigated (2026-06-07)

**Problem:** `EXECUTION_MODE=thread` defaults a 10-worker `ThreadPoolExecutor` + 100-job in-memory queue (hard cap). At ~15s/job this sustains 0.67 jobs/second. Any burst beyond 100 queued jobs returns `QueueSaturatedError` (503). Jobs are dropped outright — no overflow, no DLQ, no retry. An automated trigger scheduler hitting this ceiling gets 503 permanently (back-pressure gap also mitigated below).

**Mitigation applied (2026-06-07):**
- `docker-compose.prod.yml` now sets `EXECUTION_MODE: distributed` on the `api` service, so anyone running the production overlay gets distributed mode without needing to edit `.env`.
- `AINDY/.env.example` already carries a `WARNING: Do NOT use in production deployments where uptime matters` comment under `EXECUTION_MODE=thread` (OPER-EXEC-001, 2026-06-06).
- The worker service in `docker-compose.yml` already sets `EXECUTION_MODE: distributed` (OPER-EXEC-001, 2026-06-06).

**Additional mitigation (2026-06-15):** `startup.py:_log_async_job_capacity_advisory()` now emits `logger.error` when `ENV=production` and `EXECUTION_MODE=thread`, firing unconditionally regardless of `AINDY_JOB_WARN_CAPACITY`. The prod escalation returns early so the normal advisory path is skipped. This surfaces the misconfiguration prominently in production logs and monitoring.

**Remaining gap:** `AINDY/.env.example` still ships `EXECUTION_MODE=thread` as the literal default value — a developer who copies `.env.example` directly to `.env` and doesn't run the prod overlay still gets thread mode. Changing the default to `distributed` breaks local dev without Redis. Resolution direction: separate dev and prod `.env` templates, or a first-run wizard that detects the deployment context. Deferred until DEPLOY-TARGET-1 is addressed.

**CLOSED by RTR-2 (2026-07-08).** `config.resolve_execution_mode()` makes the
*runtime* default deployment-aware: when `EXECUTION_MODE` is unset, production
resolves to `distributed` (dev/test stay `thread`), so a prod deploy no longer
depends on remembering to set it — and thread mode now re-dispatches jobs
stranded by a restart at next startup (`job_recovery.recover_orphaned_thread_jobs`),
removing the "dropped outright, no recovery" edge for single-server deployments.
`.env.example` comments the assignment out and documents the deployment-aware
default. See **RTR-2** advance for detail.

---

## SYSMAX-5 — the scheduler thread pool is smaller than the job count, and never sized deliberately

**Status: CLOSED (2026-08-16, #453).** Filed 2026-08-16, found while fixing `FR-15` (b).

**Fixed by isolation, not capacity — and that distinction was the whole finding.** Three lanes:
`default` (10) for ordinary maintenance and every app-registered job, `recovery` (2) for the six
jobs whose value peaks when the scheduler is saturated, `waits` (1) for time-wait firing.

**★ The entry warned "do not close this by raising the number alone", and building it produced
the concrete reason.** `DB_POOL_SIZE` (10) + `DB_MAX_OVERFLOW` (20) = **30 connections, shared
with request handling**. Every scheduler thread can hold a session, so raising `default` to
exceed the job count would leave request handling starved instead — the RT-MEMTXN-LEAK-1 shape,
where a login took 42s. A bigger pool moves the threshold; dedicated lanes remove the coupling,
and cost +3 threads. `test_total_scheduler_threads_leave_db_headroom` asserts the lanes stay
within half the DB budget, so that trade cannot be made accidentally later.

`queue_backend_reconnect` is the sharpest protected case: if the queue backend is down *and* the
pool is saturated, the job that would reconnect it cannot run — self-sustaining rather than
merely delayed.

**Item 3 (emit saturation) shipped too:** `aindy_scheduler_job_starved_total{job_id,reason}`,
driven by an APScheduler listener on `EVENT_JOB_MAX_INSTANCES` / `EVENT_JOB_MISSED`. Those were
previously a per-job log line only — which is what the `FR-15` incident printed once per starved
second while nobody could see it as a signal. The `reason` label separates "previous run still
going" from "no worker was free"; they have different causes.

**Test-shim gap closed in passing, the second of its kind.** `pytest.ini` sets
`pythonpath = . AINDY`, so `import apscheduler` resolves to the vendored shim — which had no
`events` module and no `add_listener`, so the listener silently took its `except ImportError`
path and **would have shipped unexercised by any test**. The shim grew to match the guard rather
than the guard being weakened to match the shim (same call as the `executors.pool` addition in
`FR-15` (b)).

**Original finding follows.** Not implicated in any
known incident on its own; it is the same shape as the defect that *was*, one level up.

**Measured.** `scheduler_service.start()` builds `BackgroundScheduler` and — before `FR-15` (b)
— passed no `executors=`, so the pool was APScheduler's default `ThreadPoolExecutor()`, i.e.
**10 workers**. Against that:

| | Jobs |
|---|---|
| Runtime, `platform-only` profile (the floor) | **12** |
| App-registered via `get_scheduled_jobs()` in `aindy-apps-monolith` | **21** `register_scheduled_job` call sites |
| Realistic deployment total | **~33 jobs on 10 workers** |

App jobs are added at `scheduler_service.py:348` with **no `executor=`**, so every one lands on
`default`.

**Why the ratio matters here specifically.** Two of those jobs can hold a worker for a long time,
and neither is bounded:

- `scheduler_heartbeat_tick` holds a worker for the **entire duration of one INLINE execution**.
  Dispatch is INLINE by default (`FR-15`), and the reported incident had it held for ~13 minutes.
- Several maintenance jobs (`recover_stuck_flow_runs`, `process_pending_memory_embeddings`,
  `cleanup_stale_logs`, `effect_record_cleanup`, …) open DB sessions and can block up to
  `DB_POOL_TIMEOUT` — **60s** since `DB-NODUS-BUDGET-1` raised it — when the connection pool is
  exhausted. Pool exhaustion is not hypothetical: it is `RT-MEMTXN-LEAK-1`, and it was present
  during the `FR-15` incident.

So the failure mode is a **maintenance brownout**: enough slow jobs coincide, the shared pool is
saturated, and every remaining job silently stops running — including recovery jobs whose entire
purpose is to clean up after the condition that saturated the pool. Nothing raises; APScheduler
logs `maximum number of running instances reached` per starved job and keeps going.

**What `FR-15` (b) already fixed, and what it deliberately did not.** That change gave
`scheduler_wait_tick` its own single-thread executor, because time-wait firing is a correctness
guarantee and must not be probabilistic. **It protected one job by name.** Everything else still
shares the same 10 workers, so the general ratio problem is untouched — recording it here rather
than widening that PR's scope.

**Not a live incident.** No observed failure is attributed to this, and the ratio has presumably
been this way since the scheduler was written. It is filed because it is *latent by
construction*: the pool size was never chosen, it is an upstream default that our own job count
already exceeds, and the gap widens with every app that registers a job.

**Proposed fix, in order of value.**

1. **Size the default executor deliberately and say why.** It is now an explicit argument
   (`FR-15` (b) added `executors=`), so this is a one-line change plus a comment — but pick the
   number from the job count and the blocking profile, not from taste.
2. **Consider a second dedicated executor for the recovery jobs.** They are the ones whose value
   is highest exactly when the pool is saturated, which is when they currently cannot run.
3. **Emit pool saturation.** APScheduler's `maximum number of running instances reached` is a
   per-job log line, not a metric; there is no signal for *"the scheduler pool is full"*. This is
   the same observability gap `FR-15` (c) closed for the dispatch queue, and it should be closed
   the same way rather than rediscovered from logs during the next incident.

**Do NOT close this by raising the number alone.** A bigger pool moves the threshold; it does not
bound either of the two unbounded holders. The durable fix for the worst holder is `FR-15` (a) —
taking dispatch off the inline path entirely — and this entry should be re-read after that lands,
because it may reduce to items 2 and 3.

## SYSMAX-2 — Autonomous trigger scheduler has no queue back-pressure

**Status:** CLOSED (2026-06-07)

**Problem:** `submit_autonomous_async_job()` in `async_job_service.py` called `submit_async_job()` bare — any `QueueSaturatedError` propagated up to the route handler as 503. The trigger scheduler had no mechanism to slow down on saturation: it received 503 and could keep retrying, hammering the queue rather than backing off.

**Fix applied:** Added a `try/except QueueSaturatedError` block around the `submit_async_job()` call in `submit_autonomous_async_job()`. On saturation the submission is converted to a 60-second deferred job via `defer_async_job()` — the same path as a trigger-evaluator `"defer"` decision. The caller receives `status: DEFERRED` with `reason: "Execution queue saturated — automatically deferred for retry."` and a `defer_seconds: 60` signal. A `logger.warning` fires so operators see the saturation event.

**Effect:** Autonomous triggers that hit a full queue now self-regulate at 60s intervals instead of producing a stream of 503s. The deferred job re-enters `process_deferred_jobs()` after the cooldown, where `evaluate_live_trigger()` is called again before re-submission.

---

## SYSMAX-3 — Memory bytes not enforced per execution unit

**Status:** Deferred — requires OS integration

**Problem:** `check_quota()` in the syscall dispatcher tracks memory bytes consumed but does not enforce a hard cap. The comment in the source reads "requires OS integration." A memory-heavy node (large embedding batch, large LLM context) can OOM the API process with no prior warning or quota enforcement.

**Gap:** No `/proc/{pid}/status` or `resource.getrusage()` integration exists. The value tracked is the syscall-reported estimate, not actual process RSS.

**Resolution direction:** When per-EU memory limits become production-critical (multi-tenant SaaS, hostile-third-party profile with untrusted extensions), wire `resource.getrusage(RUSAGE_SELF).ru_maxrss` into the quota check and enforce `MAX_MEMORY_BYTES_PER_EXECUTION`. On Linux, `ru_maxrss` is kilobytes; on macOS, bytes — the platform difference must be normalized.

**Reopen trigger:** First OOM incident in a production deployment, or when `hostile-third-party` deployment profile becomes the active default.

---

## SYSMAX-4 — Per-EU syscall cap (100) and wall-time cap (5min) may be tight for LLM-heavy flows

**Status:** Tracked — advisory

**Context:** `MAX_SYSCALLS_PER_EXECUTION = 100` (hard, mid-execution termination on breach) and `MAX_WALL_TIME_MS = 300_000` (5 minutes) are the per-execution-unit caps. A single flow node calling an LLM 3 times, doing 5 memory reads, and writing back results across multiple iterations can approach 100 syscalls non-trivially. A slow model with multiple round trips can exceed 5 minutes.

**Not a bug:** The caps are correct safety defaults for single-process deployments. A multi-node DAG flow bypasses per-EU caps by design (each WAIT/RESUME creates a new EU). The risk is a developer building a complex single-node flow who hits a mid-execution `RESOURCE_LIMIT_EXCEEDED` with no retry path.

**Resolution direction:** Both caps are tunable via env vars (`AINDY_MAX_SYSCALLS_PER_EXECUTION`, `AINDY_MAX_WALL_TIME_MS`). Document the advisory in `NODUS_DEVELOPER_GUIDE.md` §3 ("Design complex flows as multi-node DAGs rather than single nodes with many syscalls"). Raise caps only when a real workload requires it — do not raise speculatively.

**Reopen trigger:** First production `RESOURCE_LIMIT_EXCEEDED` from a legitimate (non-runaway) flow.

---

## AUTH-V1 — AINDY/auth/__init__.py was a verbatim duplicate of api_key_auth.py

**Status:** CLOSED (2026-06-06)

**Problem:** `AINDY/auth/__init__.py` and `AINDY/auth/api_key_auth.py` were byte-for-byte identical (211 lines each). Any change to one had to be mirrored to the other or behavior would silently diverge. The `__init__.py` was not re-exporting — it was fully re-implementing.

**Fix applied:** Replaced `AINDY/auth/__init__.py` with a 7-line re-export shim. Canonical implementation lives exclusively in `api_key_auth.py`.

---

## AUTH-V4 — Frontend logout() never called POST /auth/logout

**Status:** CLOSED (2026-06-06)

**Problem:** `AuthContext.jsx:logout()` called `clearStoredToken()` and `setToken(null)` only. `POST /auth/logout` increments `User.token_version`, invalidating the JWT on all subsequent requests. Without the backend call, a "logged-out" user's token remained valid on the server for up to 24 hours — enough for replay or session-fixation if the token was captured.

**Fix applied:**
- Added `ROUTES.AUTH.LOGOUT` to `@aindy/ui-kit` `_routes.js`.
- Added `logoutUser()` function to `auth.js` (best-effort: `.catch(() => null)` so network failure never blocks local state clear).
- Updated `AuthContext.jsx:logout()` to call `logoutUser()` before clearing local state.
- Rebuilt `@aindy/ui-kit` and platform SPA dist.

---

## AUTH-V6 — require_platform_admin_access() passed ALL API keys regardless of scope

**Status:** CLOSED (2026-06-06)

**Problem:** `auth_service.py:require_platform_admin_access()` checked `is_admin` for JWT users but returned immediately for any `auth_type == "api_key"` user with no scope check. An API key with `flow.read` scope could call any admin route (flow management, session invalidation) guarded by this dependency. `admin_invalidate_sessions` in `auth_router.py` had a manual in-handler copy of the correct logic instead of using the shared dependency — the two drifted.

**Fix applied:**
- `AINDY/services/auth_service.py`: `require_platform_admin_access()` now checks `"platform.admin" in api_key_scopes` for API key callers, 403 if absent.
- `AINDY/routes/auth_router.py`: `admin_invalidate_sessions` dependency changed from `Depends(get_current_user)` to `Depends(require_platform_admin_access)`; manual in-handler guard removed.
- `tests/unit/test_auth_wiring.py`: 11 tests covering V1 re-exports (5 shapes) and V6 guard (6 shapes).

---

## TIER3-8 — MemoryIngestQueue.enqueue() dropped writes were silent at the queue level

**Status:** CLOSED (2026-06-07)

**Problem:** `MemoryIngestQueue.enqueue()` incremented Prometheus metrics on queue-full and not-accepting drops, but emitted no log. The service wrapper (`memory_ingest_service.py`) warned on drops, but direct callers had no visibility.

**Fix applied:** Added `logger.warning` in both drop paths inside `enqueue()` — queue-full (with depth/capacity) and not-accepting — so all drop paths produce a WARNING log regardless of call site.

---

## TIER3-9 — db.flush() in event emission committed pending handler ORM changes

**Status:** CLOSED (2026-06-07)

**Problem:** `_persist_system_event()` called bare `db.flush()` after `db.add(event)`. SQLAlchemy's `session.flush()` pushes ALL pending identity-map changes to the DB — not just the event. Any ORM object a route handler had staged with `db.add()` but not yet committed would be flushed as a side effect of event emission and committed by the subsequent `db.commit()`. Data and event writes were not atomic, and handler data could be committed by a different code path than the handler itself.

**Fix applied:** Changed `db.flush()` to `db.flush([event])` — SQLAlchemy supports flushing a specific object list. The event gets its DB-assigned `id` for use in `link_events()` while all other pending session changes stay unflushed until the handler's own commit.

---

## AUTH-V5 — SECRET_KEY module-level string exported from auth_service.py

**Status:** CLOSED (2026-06-07)

**Fix:** Removed `SECRET_KEY: str = settings.SECRET_KEY` from line 94. Removed `global SECRET_KEY` + assignment in `rotate_signing_key()` and `_reload_key_on_sighup()`. JWT encode already used `_get_signing_key()`; decode already used `_key_ring.verify_keys()`. Zero external consumers confirmed by grep before deletion.

---

## REPLAY-1 — Deterministic replay harness requires Clock injection refactor

**Status:** CLOSED (2026-06-11)

Added `AINDY/kernel/clock.py`: ContextVar-backed `utcnow()` + `frozen_at(t)` context manager. No signature changes required — each call site imports `utcnow` directly and the ContextVar override is async-safe and thread-safe.

Sites updated (12): `kernel/syscall_dispatcher.py` (EffectRecord gate — 3 sites), `kernel/circuit_breaker.py` (`_now()` body), `kernel/scheduler/waits.py` (time-wait tick), `core/execution_unit_service.py` (`_now()` body), `core/system_event_service.py` (event timestamp + 5 cutoff queries), `runtime/flow_engine/runner_completion.py`, `runtime/flow_engine/runner_failure.py`, `runtime/flow_engine/shared.py` (`_default_wait_deadline`).

12 tests in `tests/unit/test_clock.py` — all green. ORM model `default=lambda:` columns intentionally excluded (SQLAlchemy concerns, not business logic).

---

## TIER3-V2V3 — require_scope() / enforce_api_key_scope() wired to platform routes

**Status:** CLOSED (2026-06-07)

**Problem:** `require_scope()` and `AuthPrincipal` in `AINDY/auth/api_key_auth.py` were fully implemented but wired to zero routes. Any API key with any scope (or no scope) could call flows, memory, and syscall routes as if it had full access — the stored scope list was consulted only at key creation for validation, never at request time.

**Fix applied:**
- Added `enforce_api_key_scope(scope)` to `auth_service.py` — a FastAPI dependency factory using the already-resolved `current_user` dict (no second DB lookup). JWT users always pass; API keys must have the required scope or `platform.admin`.
- `flows_router.py`: `list_flows`/`get_flow` → `flow.read`; `run_flow_endpoint` → `flow.execute`.
- `platform_ops_router.py`: `list_memory_path`/`memory_tree`/`memory_trace` → `memory.read`.
- `platform_ops_router.py:dispatch_syscall`: inline domain-level scope enforcement for API key callers — maps syscall name prefix to required scope (`sys.v1.memory.*` → `memory.write`, `sys.v1.flow.*` → `flow.execute`, `sys.v1.agent.*` → `agent.run`, `sys.v1.webhook.*` → `webhook.manage`); `platform.admin` bypasses all.
- 13 new unit tests in `tests/unit/test_tier3_structural.py`.

**V3 — CLOSED 2026-06-07:** Removed the dead parallel auth path (`get_authenticated_principal`, `require_scope`, `AuthPrincipal`, header extractors) from `api_key_auth.py`. File now contains only `Scopes`. `__init__.py` re-exports `Scopes` only. Three dead export-check tests removed from `test_auth_wiring.py`.

---

## LAYER-1 — execution_dispatcher.py opens its own SessionLocal() for event emission

**Status:** Deferred — Known architectural gap

**Problem:** `AINDY/core/execution_dispatcher.py:_enqueue_distributed()` opens a `SessionLocal()` directly at lines 305–307 and 368–370 to emit a `job_enqueued` observability event. The execution dispatcher layer is directly managing DB sessions — a responsibility that belongs to the service or event layer. This violates the "one session per request" convention and places session lifecycle management in the wrong layer.

**Why deferred:** The dispatcher runs outside the request context in the distributed path; no request-scoped session is available. Fixing this properly requires routing the event emission through an injected event service or background event queue rather than opening a raw session. That refactor touches the dispatcher/event boundary across multiple call sites and is a non-trivial scope change.

---

## LAYER-2 — auth_router.py routes auth primitives through execute_with_pipeline_sync

**Status:** Deferred — Known architectural gap

**Problem:** `AINDY/routes/auth_router.py` sends all four handlers (login, register, logout, admin_invalidate_sessions) through `execute_with_pipeline_sync`. Auth requests create an `ExecutionUnit`, emit `execution.started`/`execution.completed` events, and go through quota checks. Every login and register is an "execution" with full resource-tracking overhead. The pipeline was not designed for auth primitives — this creates event noise and DB writes on every unauthenticated request.

**Why deferred:** Removing auth routes from the pipeline requires a lighter-weight route wrapper that still provides tracing and error normalization without ExecutionUnit creation. That wrapper doesn't exist yet. The overhead is real but not a correctness issue at current load.

---

## LAYER-3 — exception_handlers.py falls back to decode_access_token for user attribution

**Status:** CLOSED (2026-06-15)

**Problem:** `AINDY/exception_handlers.py:_extract_user_id_from_request()` called `decode_access_token` as a fallback — full signature verification + key ring walk — for logging attribution only. Cross-layer dependency: exception handler doing auth work.

**Resolution:** Replaced `decode_access_token` fallback with the same unverified sub-claim extraction pattern used by the rate-limiter (`jose.jwt.decode` with `verify_signature/aud/exp: False`). Attribution is for logging only — no access-control decision is made — so unverified decode is correct. The `request.state.user_id` fast path (set by the pipeline for all authenticated requests) remains the primary path; the unverified decode fires only for requests that failed before the pipeline set state (e.g. 401s on unauthenticated routes).

---

## LAYER-4 — memory_ingest_service.py opens SessionLocal() outside request context

**Status:** Deferred — Known architectural gap, intentional by design

**Problem:** `AINDY/memory/memory_ingest_service.py` imports and opens `SessionLocal()` at construction time (line 40), outside any request context. This creates a second concurrent session to the same tables as the request session. It breaks the "one session per request" convention and creates independent transaction boundaries.

**Why deferred:** Memory ingestion is intentionally decoupled from the request session — writes are queued and flushed after the script finishes, not within the request transaction. The independent session is architecturally correct for this use case (deferred background writes shouldn't be rolled back if the request session rolls back). The violation is a convention mismatch, not a correctness bug. Resolving it would require a formal background-session pattern or session factory abstraction.

---

## LAYER-5 — execute_with_pipeline_sync uses asyncio.run(); coordination_router calls it on every endpoint

**Status:** Deferred — Known performance gap

**Problem:** `execute_with_pipeline_sync()` (`AINDY/core/execution_helper.py:69`) bridges synchronous routes into the async pipeline via `asyncio.run()`. `coordination_router.py` calls this on 9+ endpoints — each call creates and tears down a new event loop. No technical correctness issue in FastAPI's threadpool model, but it introduces non-trivial async machinery overhead on every coordination route invocation.

**Why deferred:** Fixing this requires either making the coordination routes fully async (straightforward but high-churn across all endpoints) or providing a sync-native pipeline path that doesn't use `asyncio.run()`. The coordination domain is a candidate for extraction (see ROUTE-EXTRACT-001 remaining candidates), so the refactor may be moot if those routes move to the monolith.

---

## EXEC-EU-1 — _safe_finalize_eu not called from the finally block in ExecutionPipeline.run()

**Status:** CLOSED (2026-06-07)

**Implementation:** `_safe_finalize_eu` (resources.py) now has an idempotency guard: `ctx.metadata["eu_finalized"]` is checked at entry and set to `True` before the DB write attempt — preventing double-finalization on normal paths. The `finally` block in `ExecutionPipeline.run()` (pipeline.py) now calls `self._safe_finalize_eu(ctx, "failed")` gated by `ctx.metadata.get("eu_status") != "waiting"`, which is a no-op on every normal path (guard fires) and closes the EU as `failed` on any `BaseException` escape path. Waiting EUs are excluded by the `eu_status` guard.

---

## EVENT-1 — Emission error loop prevention is implicit, not explicit

**Status:** CLOSED (2026-06-08)

**Implementation:** Added explicit `_emission_failed` flag to `ctx.metadata` in `_safe_emit_event` (`pipeline.py`). On any emission exception the flag is set to `True`. On every subsequent call to `_safe_emit_event` for the same context, the guard short-circuits and returns `None` immediately — before touching the DB or calling `emit_system_event`. This makes loop prevention a first-class invariant rather than a side effect of the broad `except` catch.

- The guard fires only on the second+ call in a failure sequence; it does not affect the first failed call.
- A successful emission never sets the flag.
- 4 new regression tests in `tests/unit/test_memory1_event1_fixes.py` (flag-set-on-failure, skip-on-flag, no-flag-on-success, loop-terminates-after-one-real-call).

---

## MEMORY-1 — persist_memory_ingest_payload can produce orphaned nodes on partial write failure

**Status:** CLOSED (2026-06-08)

**Implementation:**
- Added `commit: bool = True` parameter to `MemoryTraceDAO.create_trace()`, `MemoryTraceDAO.append_node()`, and `MemoryNodeDAO.save()`. When `commit=False` each method uses `db.flush()` instead of `db.commit()`, leaving the changes pending in the caller's transaction.
- `persist_memory_ingest_payload()` now passes `commit=False` to all three DAO calls and issues a single `db.commit()` on success. On any exception it calls `db.rollback()` and returns `IngestResult(status="failed")` rather than silently continuing with partial state.
- 4 new regression tests in `tests/unit/test_memory1_event1_fixes.py` (success path commits once, append failure rolls back, create failure rolls back, session always closed).

---

## MEM-DELETE-1 — memory.delete shipped hard/syscall-only; four opt-in upgrades deferred

**Status:** Core SHIPPED (2026-07-11); upgrades deferred by explicit scope decision.

`sys.v1.memory.delete` (capability + dedicated `memory.delete` scope) ships as a **hard,
syscall-only, node-id** delete: tenant-scoped, idempotent, DB `ON DELETE CASCADE` to the
node's history / trace memberships / causal edges / links. Irreversible. No REST route, no
audit event, no bulk/path delete. Verified against real Postgres (isolation + cascade +
idempotency). Key files: `db/dao/memory_node_dao.py::delete_by_id`,
`kernel/syscall_registry.py::_handle_memory_delete`, scope in `auth/api_key_auth.py`,
dispatch map in `routes/platform/platform_ops_router.py`.

Four upgrades were consciously deferred (each independently addable, no rework of the core):

- **G1 — REST `DELETE /platform/nodes/{node_id}` route** (`memory_router.py`) that dispatches
  the syscall. **Reopen trigger:** a non-SDK HTTP client needs delete, or production asks for it.
- **G2 — `MEMORY_DELETED` audit event.** Adds a `SystemEventTypes` value → **trips the
  frozen-hash baseline** (update in lockstep). Skipped for v1; the dispatch envelope + OTel span
  already record the call. **Reopen trigger:** deletion needs a first-class audit signal.
- **G3 — Bulk / MAS-path delete** (e.g. wipe a namespace subtree). Bigger feature; the SDK
  signature is node-id-only. **Reopen trigger:** subtree/bulk deletion is requested.
- **G4 — Soft delete** (add `is_deleted`/`deleted_at` to `MemoryNodeModel`). Preserves audit +
  reversible (can hook AGENT-HARDEN-3 undo), but needs a schema-contract bump + migration +
  threading `is_deleted == False` through ~8 read sites. **Reopen trigger:** audit-preservation
  or undoable delete becomes a requirement (would likely supersede the hard delete).

## MEM-NODETYPE-1 — Memory write defaults to a node_type the validator rejects

**Status:** CLOSED (2026-06-27)

**Problem:** Two `memory.write` paths defaulted `node_type="execution"`, but
`VALID_NODE_TYPES` in `AINDY/memory/memory_persistence.py` (`{decision, outcome, insight,
relationship}`) omits "execution". The `before_insert`/`before_update` validator
(`validate_node_type`) therefore raised `ValueError` on every default write — and since
`memory_type` falls back to `node_type` (line 122), it failed `VALID_MEMORY_TYPES` too.
This blocked the execute half of the `runtime_local` planner loop, which almost always
plans a memory tool first. Surfaced during live-stack verification from the monolith
(`LIVE_VERIFICATION_SCOPE.md`). The syscall docstring even documented `default "execution"`,
so the runtime advertised a default its own model rejected.

**Why it was an outlier, not a missing type:** every *other* write path already defaulted
to a valid type — `memory_ingest_service.py` → "insight", `nodus_memory_bridge.py` →
"outcome". Only the syscall handler and the Nodus builtin defaulted to "execution". The
scorer (`memory_scoring_service.py`) also falls back to "insight" when type is unspecified,
so "execution" nodes silently floored at the 0.8 default weight.

**Fix applied (two passes):** Changed every write-path default to "insight" (matches the
scorer fallback, so a defaulted write ranks identically to an untyped one).

Pass 1 (PR #98) — the two sites in the original report:
- `AINDY/kernel/syscall_registry.py` — `_handle_memory_write` default + docstring.
- `AINDY/runtime/nodus_builtins.py` — `NodusMemoryBuiltins.write` signature + docstring.
- 3 regression tests in `tests/unit/test_mem_nodetype_default.py`.

Pass 2 — **execute-to-completion verification on the Postgres stack revealed PR #98 was
incomplete**: the *deferred* path the flow engine actually runs still defaulted to
"execution", as did the extension ABI. In the script paths the rejected save is swallowed
(`logger.warning` + `continue` / `return None`), so the script reported completion while the
node silently vanished. Six more sites, all → "insight":
- `AINDY/runtime/nodus_worker.py` — `DeferredMemoryBuiltins.write` + `_remember_factory`.
- `AINDY/runtime/nodus_runtime_adapter.py` — `_apply_deferred_memory_writes` dao.save.
- `AINDY/nodus/runtime/memory_bridge.py` — `AINDYMemoryBridge.remember` (the VM's `remember`
  builtin; persists in-subprocess on its own session).
- `AINDY/platform_layer/extension_runtime_api.py` + `extension_worker.py` — extension memory ABI.
- `tests/integration/test_planner_loop_execute_to_completion.py` — 4 integration tests driving
  each real write path (dispatcher syscall, adapter deferred persist, `remember` builtin, full
  subprocess VM run) with a default node_type against real PostgreSQL, asserting the node
  persists as "insight". All green. A clean tree-wide sweep confirms no `"execution"` node_type
  default remains.

No `VALID_NODE_TYPES` change → `memory_persistence.py` untouched → schema contract protocol
not triggered.

**Distinct from `ECOGAP-1` (event-sourced durable execution / replay):** that is a
kernel/flow-engine durability gap (append-only event log for crash continuation), a
different subsystem from the memory-node taxonomy. An episodic "execution"/"action" memory
type could be introduced later *if* ECOGAP-1 mirrors execution events into the memory graph —
but that is deferred and out of scope here.

---

## INFINITY-COMPLETION-HOOK-BOUNDARY-1 — First-party completion hooks got `db=None` + a redacted run (post-completion enforcement silently dead)

**Status:** CLOSED (2026-07-08). Runtime counterpart to the app handoff
`aindy-apps-monolith` PR #64.

**Problem:** every agent-completion hook is invoked via
`registry.run_agent_completion_hooks` → `_sanitized_extension_input(context)`, and the
extension-boundary sanitizer (`platform_layer/extension_boundary.py`) **drops `db`**
(blocked root key) and **redacts the `run` ORM** to `{"_redacted_type": "AgentRun"}`.
`agent_completion_hook` was also **not** in `_STATEFUL_IN_PROCESS_CALLBACK_SURFACES`, so a
module-resolvable first-party hook was subprocess-isolated on top. So the app's
`handle_agent_run_completed` received `db=None` + a run with no id and hit its
`if run is None or db is None: return None` guard — the post-agent-completion Infinity loop
enforcement was **silently dead**. **Not a 1.6.0 regression:** the sanitize landed
`2026-05-20` (commit `93d9c84`, in v1.0.0+). It only became *visible* in 1.6.0 because
INFINITY-RUNTIME-1 **Gap 4** made `execution.py:220` start *consuming* the hook's return
(NextAction) — before Gap 4 the return was discarded, so `db=None` went unnoticed. The
sibling stateful surfaces (`run_tool_provider`/`planner_context`) also sanitize but survive
because they read live *registry* state, not the DB.

**Fix applied (boundary-preserving, Option A):** (1) the completion-hook context at
`execution.py:220` now carries `"run_id": str(run.id)` — a string, so it **survives** the
sanitizer; a first-party hook re-fetches the run by id with its own session. (2)
`agent_completion_hook` added to `_STATEFUL_IN_PROCESS_CALLBACK_SURFACES` so it runs
in-process (can open a session / reach live app state) instead of subprocess-isolated. The
sanitizer is unchanged — the runtime still **never** leaks a `db`/session/ORM handle across
the boundary (only a string id crosses). Registration stays capability-gated to trusted
extensions. Tests: `test_completion_hook_boundary.py` (run_id survives / db+run stripped;
surface runs in-process; end-to-end through `run_agent_completion_hooks` with return
propagation — the sanitized path no prior test exercised). **App follow-up:** update
`handle_agent_run_completed` to re-fetch by `run_id` with its own `SessionLocal` (the parked
`feat/infinity-next-action-ledger` branch); ships in the next runtime release (main ahead of v1.6.0).

---

## PLANNER-SUBPROC-1 — Agent planner broken on Linux/Docker (run-tool provider isolated into a stateless subprocess)

**Status:** CLOSED (2026-06-27)

**Problem:** `POST /apps/agent/run` → `generate_plan` → `get_tools_for_run` resolves the
registered run-tool provider, which `registry._maybe_wrap_runtime_callback` routed through
an isolated subprocess (`runtime_callback_worker.py`). First-party-app providers (and the
planner-context provider) read **live in-process registration state** — the agent
`TOOL_REGISTRY` and planner context populated during app bootstrap. A bare subprocess can't
reconstruct that: its `cwd` is the read-only site-packages dir (`runtime_callback_host.py:62`),
so the provider's `load_plugins()` finds no app manifest and returns zero tools → planner
raises `requires at least one registered tool` → **500**. Masked in local dev because Windows
resolves the manifest; only surfaced on Linux (CI + a `python:3.11-slim` non-editable repro).
Same class of bug also affects app-provided trigger evaluators (the documented silent-defer in
`_maybe_wrap_runtime_callback`).

**Fix applied:** Registry-state-dependent surfaces now run **in-process**. Added
`_STATEFUL_IN_PROCESS_CALLBACK_SURFACES = {"run_tool_provider", "planner_context"}` in
`AINDY/platform_layer/registry.py`; `_runtime_callback_spec` returns `None` (in-process) for
those. Self-contained surfaces (startup hooks, capability providers, trigger evaluators) keep
subprocess isolation. Context is still sanitized at the registry boundary
(`get_planner_context` / `get_tools_for_run`), so no extra state crosses any boundary. Updated
`tests/unit/test_extension_ownership.py` (planner_context now in-process, not recorded as an
isolated invocation; startup_hook stays isolated). Shipped in 1.4.3.

**Remaining gap:** app-provided **trigger evaluators** still run isolated; if a deployment
relies on app-state-dependent trigger evaluators they will silently defer on Linux. Add
`trigger_evaluator` to the in-process set when that becomes a real workload.

---

## OBS-1 — Pipeline _safe_* failures log at DEBUG, invisible in production

**Status:** CLOSED (2026-06-07)

**Implementation:** Promoted all three failure-path logs from `logger.debug` to `logger.warning`:
- `resources.py` — `_safe_require_eu` exception handler (was line 53)
- `resources.py` — `_safe_finalize_eu` exception handler (was line 160, now shifted by EXEC-EU-1 guard)
- `pipeline.py` — `_safe_emit_event` exception handler (was line 347)

Success-path debug logs (`[Pipeline] EU registered`, `[Pipeline] EU finalised`) remain at DEBUG — not failures.

---

## LEASE-1 — `lease-elected` background leadership was advertised but not enforced

**Status:** CLOSED (2026-06-24)

**Source:** Audit finding — *"advertising a guarantee the code doesn't implement."*

**The gap (what the audit found):** The deployment contract advertises
`background_leadership_mode: "lease-elected"` for the `distributed-api`,
`distributed-worker`, and `hostile-third-party` profiles, and
`DEPLOYMENT_PROFILES.md` stated *"Lease-elected means exactly one participating
runtime process becomes leader at a time."* The code did not implement it.
Leadership was decided locally in `_start_background_services` (and the worker
entrypoints) by:

```python
is_leader = enable_background and all(result is not False for result in startup_results)
```

— a per-process boolean with no cross-instance coordination. Every API/worker
replica whose local `system.startup` hooks succeeded self-elected, so N replicas
ran N schedulers (duplicate stuck-run watchdog, EffectRecord TTL cleanup,
orphaned-approved recovery, db-pool metrics). The `background_task_leases` table
existed in the ORM model and was *read* by two observability endpoints (which
therefore always saw `None`) but the runtime never wrote/acquired any row. (The second half of the same
audit — "silent durable→in-memory queue degradation on Redis loss" — was checked
and **refuted**: that path fails fast in prod/distributed/`AINDY_REQUIRE_REDIS`
and otherwise degrades loudly with a metric + warning + `system.queue.backend_degraded`
event + `UNSAFE_DEGRADED` runtime condition. No code change needed there.)

**Implemented:**
- `AINDY/platform_layer/leadership.py` — atomic lease claim/renew/takeover/release
  on `background_task_leases` (`SELECT … FOR UPDATE` serialises contenders on
  PostgreSQL; `UNIQUE(name)` resolves the fresh-insert race), plus a
  `BackgroundLeadershipElector` daemon thread that runs on every lease-electing
  process: the leader renews each tick; a follower takes over once the leader's
  lease lapses (TTL 60s, heartbeat 20s); a leader that loses the lease stands
  down via `on_lose` to prevent split-brain.
- `AINDY/startup.py` `_start_background_services` — for `lease-elected` profiles,
  `is_leader` is now gated on winning the lease; scheduler start/stop are wired
  to the elector's acquire/lose callbacks. The `in-process` (single-instance)
  profile keeps the local-boolean guard — that profile never promised cross-process
  exclusion. Lease is released on shutdown so a standby takes over promptly rather
  than waiting the full TTL.
- `AINDY/worker/__init__.py` and `AINDY/worker/__main__.py` — both worker
  entrypoints route leadership through the same elector.
- Tests: `tests/unit/test_background_leadership.py` (10) — claim/renew/takeover/
  release semantics + elector acquire/lose/disabled/exception transitions.

**Layering — runtime lease vs tasks-domain symbols (deliberate non-goal):** the
runtime claims its own lease row named `background_runner`. This is *distinct*
from the apps-monolith `tasks` domain, which owns the `task_is_background_leader`
/ `task_background_lease_name` registry symbols and a separate lease row named
`task_background_runner` (`apps/tasks/bootstrap.py`). The two coexist as different
rows in the same table. The runtime deliberately does **not** register those
symbols (doing so would collide with the tasks domain and corrupt its
observability), so the `/platform/observability` scheduler-status `is_leader`
field stays app-domain-owned. Surfacing the runtime's own lease state in
observability/health is a separate future enhancement (`leadership.background_leader_status()`
is the ready accessor), not part of this fix.

**Clock assumption (documented, not a gap):** lease expiry is evaluated against
each process's kernel clock (`utcnow`), not the DB clock. The 60s TTL vs 20s
heartbeat margin tolerates the skew expected between co-deployed instances. If a
future deployment spans hosts with unbounded skew, switch the expiry comparison
to a DB-side `now()` predicate.

**No schema-contract bump:** `background_task_leases` was already in the ORM
metadata (created by `create_all` / the Phase 5 schema guard); no model file
changed, so `SCHEMA_CONTRACT_VERSION` is untouched.

---

## NODUS-SYS-SURFACE-1 — Idiomatic `std:sys` bypasses the AINDY SyscallDispatcher

**Status:** CLOSED 2026-07-12 (fail-loud guard + doc; in working tree, uncommitted).

**Resolution.** A guard in `nodus_worker.py` (`_install_std_sys_guard`, installed in `main()`
after the host-function registrations) converts the silent wrong-backend into an immediate,
clear error: a `.nd` script that reaches nodus's native `syscall` builtin (directly or via
`import "std:sys"`) now fails with *"std:sys is not routed to the AINDY syscall dispatcher …
use the bare `sys("<name>", <payload>)` builtin"* instead of silently using the ephemeral
in-process stub. Verified end-to-end (real `NodusRuntime.run_source` → `ok:false` + the message)
+ 3 unit tests (`test_nodus_std_sys_guard.py`). Documented in `NODUS_DEVELOPER_GUIDE.md` §3.4.
Surface B (the bare `sys(...)` builtin → `dispatch_syscall`) is a different function and is
unaffected.

**Corrected analysis (the original fix option below was infeasible).** The entry proposed
"register a `syscall` host builtin that forwards to `_sys_dispatch`" — but `register_function`
**raises `ValueError: Cannot override built-in function`** for any name in `BUILTIN_NAMES`, and
`syscall` is a builtin; the VM also resolves native builtins before host functions. So the
idiomatic path cannot be aliased via the public API. The one interceptable seam is
`nodus.services.syscall_runtime.call_syscall` (`builtin_syscall` re-imports it per call, so a
module-attribute swap takes effect) — which the guard uses to fail loud. A *transparent* alias
(forward `call_syscall` → AINDY `dispatch_syscall`) was rejected: it couples to nodus internals,
must thread `user_id`, and `std:sys`'s memory-KV contract differs from AINDY's `sys.v1.memory.*`
syscalls, so aliasing would quietly change semantics rather than "just work". Fail-loud is the
honest fix; a host-overridable syscall hook is an upstream nodus-lang ask if transparent routing
is ever wanted.

---
_Historical (pre-close) analysis:_

**Status (historical):** Open — deferred (latent footgun, no current incident)

A `.nd` script has **two name-disjoint ways to issue a syscall**, and only one of
them reaches AINDY. They look interchangeable but route to entirely different
backends:

**Surface A — nodus-lang native `std:sys` (the idiomatic path):**
```
import "std:sys"
sys.call("sys.v1.memory.put", { ... })
```
resolves to `site-packages/nodus/stdlib/sys.nd`, whose `call()` invokes the native
VM builtin `syscall(name, payload)` (`nodus/vm/vm.py:262`, `builtin_syscall` at
`vm.py:1389`) → `nodus.services.syscall_runtime.call_syscall`. That runtime has its
**own hardcoded registry of exactly four syscalls** — `sys.v1.memory.{get,put,delete}`
and `sys.v1.memory.recall_from` — backed by `nodus.services.memory_runtime`, an
**in-process, ephemeral key/value store**. It never touches AINDY's
`SyscallDispatcher`, capability enforcement, quota, idempotency, kernel, or Postgres.

**Surface B — the AINDY-injected `sys` builtin (the path AINDY actually wires):**
```
sys("sys.v1.memory.put", { ... })        # bare builtin, NOT the std:sys module
```
`AINDY/runtime/nodus_worker.py:167` registers a host function literally named `sys`
(`register_function("sys", _sys_dispatch, arity=2)`) whose body
(`nodus_worker.py:136-162`) calls `AINDY.kernel.syscall_dispatcher.dispatch_syscall`
→ the real dispatcher → kernel + Postgres, scoped to `user_id`.

**The gap:** the builtins are named differently (`syscall` vs `sys`), so there is no
shadowing in either direction — but also **no guard**. aindy-runtime's worker does
**not** register `syscall` or `syscall_list`, so it cannot override Surface A. A
developer who writes the conventional `import "std:sys"; sys.call(...)` silently gets
nodus's four-syscall in-process stub with throwaway memory instead of AINDY's
capability-enforced, durable dispatcher — no error, no warning, wrong backend.

This reconciles two prior audit claims that appeared to conflict: "Nodus `std:sys`
routes to local in-process handlers, not AINDY syscalls" (true of **Surface A**) and
"Nodus `sys()` reaches the AINDY SyscallDispatcher" (true of **Surface B**). Both are
correct; they describe different builtins. The integration is real and live, but it
does **not** work by intercepting the idiomatic stdlib entry point.

**Options (not yet chosen):**
- **Guard/alias** — register a `syscall` (and `syscall_list`) host builtin in
  `nodus_worker.py` that forwards to `_sys_dispatch`, so `std:sys` also lands on
  AINDY. Risk: must match the native envelope shape and arity exactly, and verify it
  overrides the VM builtin rather than colliding.
- **Fail-loud** — register `syscall` to raise a clear "use the `sys()` builtin under
  AINDY" error, so the wrong path is caught at runtime instead of silently stubbed.
- **Doc-only** — document in `NODUS_DEVELOPER_GUIDE.md` that under aindy-runtime,
  scripts must call the bare `sys(...)` builtin and must not `import "std:sys"`.

Key files: `AINDY/runtime/nodus_worker.py` (`_sys_dispatch`, `register_function`),
`site-packages/nodus/stdlib/sys.nd`, `nodus/vm/vm.py:262` (`builtin_syscall`),
`nodus/services/syscall_runtime.py`, `nodus/services/memory_runtime.py`.

**Reopen/resolve trigger:** before any `.nd` script or agent objective is authored
that relies on `import "std:sys"`, or before exposing Nodus authoring to external
users.

---

## NODUS-WARMPOOL-1 — Nodus worker cold-start is billed against the script budget

**Status:** **CLOSED 2026-07-19.** Option A (clock split, 2026-07-18) + Option B — Phase 1
(single warm worker), Phase 2 (bounded worker pool), Phase 3 (metrics / graceful drain /
eager pre-warm), all 2026-07-19. The durable fix is complete: plugin-stack import cost is
amortized across a reused pool, up to N executions run concurrently, and pre-warm removes
the first-request cold-start. All opt-in (`AINDY_NODUS_WARM_POOL`, default off) with
fresh-subprocess fallback on any fault. Deferred (not required): an active health-monitor
heartbeat (reactive crash-reaping + max-requests recycle cover it) and the sibling
`runtime_callback_host.py` 10s callback subprocess (same tax, separate surface). See the
A/B/C plan below.

**CI now runs warm (2026-07-31).** Because the pool is opt-in, CI was still exercising the
cold path this entry replaced. The Integration Tests job (the only tier that really spawns
nodus workers) now sets `AINDY_NODUS_WARM_POOL=1` + `AINDY_NODUS_WARM_PREWARM=1`, so the
shipped fix is what gets tested. The cold path is not abandoned — it remains the pool's
fault fallback and is covered by `tests/unit/test_nodus_worker_pool.py`. **Standing
gotcha:** pre-warm is explicitly non-blocking (background thread off the first
`get_pool()`), so the *first* execution still races the one-time plugin load. Where a test
holds a DB session open across that load, the 10s test-mode
`idle_in_transaction_session_timeout` terminates the backend mid-run and surfaces as
`server closed the connection unexpectedly` → `PendingRollbackError` (this reddened
`test_agent_vm_parity.py` for weeks). That job therefore also sets
`DB_IDLE_IN_TRANSACTION_TIMEOUT_MS=60000`; the knob only works because the test-mode branch
in `database.py` now honors an explicitly-set value instead of hardcoding 10s.

**Symptom (verified 2026-07-09, live Linux serve, app-profile).** A real agent run
reached `executing` then failed with `"Nodus script exceeded 30000ms wall-clock
timeout"` at 0/3 steps. The plan, planner, and app were all fine — the run died on
the runtime's own execution budget, not on script work.

**Root cause — architecture, not a nodus-lang bug.** Every Nodus execution spawns a
**fresh worker subprocess** (`NodusRuntimeAdapter._execute` →
`subprocess.run([sys.executable, "nodus_worker.py"], timeout=timeout_s)`,
`nodus_runtime_adapter.py`). Under an app profile, that subprocess cold-starts the
**entire plugin stack** (`load_plugins()` over ~17 apps, ~12s) *before the script
runs a single step*. That boot time is billed against the same wall-clock budget the
script gets, so a heavy profile can burn most/all of a 30s budget on import cost
alone. The kill is the runtime's outer `subprocess.run(timeout=)` (pure AINDY code) —
nodus-lang's inner `run_source(timeout_ms=)` never gets the deciding vote. A nodus-lang
upgrade cannot fix this.

Second, smaller instance of the same shape: `runtime_callback_worker` spawns a
subprocess with a ~10s timeout (`runtime_callback_host.py`), which similarly pays a
cold-start tax on the app profile.

**Quick mitigation (shipped, this entry's PR).** `AINDY_NODUS_MAX_EXECUTION_MS`
(default 30000) now sets the budget for **both** the outer subprocess timeout and the
inner `run_source(timeout_ms=)` in one value; a per-run
`NodusExecutionContext.max_execution_ms` still overrides. Operators can raise it (e.g.
180000) to get past the cold-start wall. This makes the profile *runnable* but does not
remove the tax — every run still re-boots the stack, so p50 latency stays ~cold-start
+ script.

**Durable-fix plan — keep cold-start out of the script budget (A/B/C).** In rough
order of effort. Sized against the actual IPC contract (adapter
`subprocess.run(input=json, timeout=)` ↔ worker `stdin.read()` → register ~15 builtins
→ `run_source(timeout_ms=)` → `stdout.write(json)` → exit; the plugin load fires inside
`main()` after the stdin read, not at import; no phase marker exists today).

- **Option A — Separate the two clocks. ✅ SHIPPED 2026-07-18 (this entry's second PR).**
  The worker's inner `run_source(timeout_ms=max_execution_ms)` is the authoritative
  *script* clock; the adapter's outer `subprocess.run(timeout=)` is widened to
  `max_execution_ms + AINDY_NODUS_BOOT_ALLOWANCE_MS` (default 15000) so it is a hard
  safety net for boot + a hung worker, not the script budget. A script that overruns now
  hits the inner nodus timer first → the worker returns `status:"timeout"` → the adapter's
  existing clean "Nodus script exceeded {max}ms" path (budget = script time). The rare
  outer kill now reports a distinct boot+script message. Pure adapter-side change (no
  worker/protocol change, subprocess isolation fully preserved). **Removes the "boot billed
  to script" bug; does NOT remove per-run re-boot latency** (that's B/C). Knob:
  `AINDY_NODUS_BOOT_ALLOWANCE_MS` (0 = old shared-budget behavior). Sibling
  `runtime_callback_host.py` still shares one budget — apply the same split there if it
  becomes a concern.
- **Option C — Lazy/narrowed plugin load (deferred).** Load only the apps a given script
  needs instead of all ~17. Cuts the tax rather than moving it; reduces latency too.
  Effort: medium — needs a correct "needed apps" computation and selective `load_plugins()`;
  risk: under-loading can miss a dynamically-called tool.
- **Option B — Warm worker / worker pool (the true durable fix).**
  - **Phase 1 — single warm worker. ✅ SHIPPED 2026-07-19.** One long-lived worker loads the
    plugin stack once and serves executions over a length-prefixed JSON framing; import cost
    is amortized instead of paid per run. `nodus_worker.py` refactored so `main()` (one-shot,
    unchanged default) and the new `serve_forever()` share `run_one(payload)` — which rebuilds
    **every** per-request object (VM, `AINDYMemoryBridge`, builtins, `state`, tokens; the
    `std_sys` guard is idempotent), so a reused process carries **no** cross-run state
    (verified: 2 requests to one worker returned x=0 then x=1). New `nodus_worker_pool.py`
    keeps the worker alive with **respawn-on-crash** + **max-requests recycle**
    (`AINDY_NODUS_WARM_MAX_REQUESTS`, default 500) + a cross-platform reader-thread timeout.
    Gated `AINDY_NODUS_WARM_POOL` (default off); the adapter routes through the pool when on
    and **falls back to a fresh subprocess on any warm-path fault**, so it can only help,
    never regress. Serial (one request at a time under a lock). Subprocess isolation preserved
    (still a separate process); read-only-cwd constraint unaffected. Tests:
    `test_nodus_worker_pool.py` (17 — framing, timeout, crash, recycle/respawn/drop, adapter
    warm+fallback) + a real `--serve` IPC smoke.
  - **Phase 2 — bounded worker pool. ✅ SHIPPED 2026-07-19.** `NodusWorkerPool` grew from a
    single serial worker to up to `AINDY_NODUS_WARM_POOL_SIZE` (default 4) workers, each
    serving one request at a time (checked out under a `Condition`, returned to an idle set),
    so up to N executions run concurrently. When all N are busy a caller waits up to
    `AINDY_NODUS_WARM_ACQUIRE_TIMEOUT_MS` (default 2000) for a free worker, then raises
    `PoolBusy` and the adapter **spills to a fresh subprocess** (bounded backpressure — the
    warm path never blocks unboundedly). Faulted workers are dropped (not returned);
    over-`_max_requests` workers recycled on return. Adapter unchanged (still
    `get_pool().execute`; `PoolBusy`→fallback). Verified with a real 2-worker concurrent smoke
    (isolated state per run). Tests: +3 (concurrency, saturation/`PoolBusy`, pool-size env).
  - **Phase 3 — metrics / graceful drain / eager pre-warm. ✅ SHIPPED 2026-07-19.**
    **Metrics:** internal counters (`spawned`/`recycled`/`crashed`/`spilled`/`served`) +
    worker gauges via `pool.stats()` and best-effort Prometheus
    (`aindy_nodus_warm_pool_events_total{event}`, `aindy_nodus_warm_pool_workers{state}`).
    **Graceful drain:** `pool.drain(timeout_s)` stops new checkouts (they raise `PoolBusy` →
    spill), waits for in-flight to finish, then kills all workers. **Eager pre-warm:**
    `pool.prewarm(count)` spawns workers and pays their plugin-stack load ahead of traffic via
    a new worker `{"__warmup__": true}` control request (calls `_ensure_tools_loaded`, runs no
    script — so tool-less scripts still skip the load); kicked in a background daemon thread on
    first `get_pool()` when `AINDY_NODUS_WARM_PREWARM` is on (never blocks the caller).
    Verified with a real prewarm→hot-execute→drain smoke. Tests: +5. **Deferred (over-
    engineering):** an active health-monitor heartbeat thread — reactive crash-reaping at
    checkout + max-requests recycle already keep the pool healthy.
  Must preserve (still true): read-only cwd in a wheel, and subprocess isolation as
  load-bearing for the sandbox `--network none` extension path + the stateful-in-process
  callback carve-outs. Reopen Phase 2 when concurrency/p50 becomes a product SLA.

**Constraints to respect.** The worker's `cwd` is `parents[2]` (in a wheel:
read-only site-packages — see the `_maybe_wrap_runtime_callback` CWD hazard in
CLAUDE.md), so a warm worker must not assume a writable cwd. Subprocess isolation is
also load-bearing for the sandbox posture (`--network none` on the extension path) and
for the stateful-in-process callback carve-outs (`_STATEFUL_IN_PROCESS_CALLBACK_SURFACES`);
a pool must preserve per-run state isolation (fresh `state`/`memory_context` per
request, no cross-run leakage).

**Reopen/resolve trigger:** when app-profile Nodus execution latency or the
cold-start tax becomes a product concern (heavier profiles, tighter SLAs, or the
literal-completed CI run needing a green under a realistic budget). Until then the env
knob is the supported workaround.

Key files: `AINDY/runtime/nodus_runtime_adapter.py` (`_execute`, subprocess spawn +
timeout, `_resolve_default_max_execution_ms`), `AINDY/runtime/nodus_worker.py`
(one-shot worker entry, `load_plugins()` cold-start), `AINDY/platform_layer/
runtime_callback_host.py` (the sibling 10s callback subprocess).

---

## DEP-UPGRADE-DEFERRED-1 — Deferred deliberate dependency upgrades (OTel group, UI major unit)

**Status:** Open — **OTel half resolved 2026-08-01** (bumped to 1.44.0); the UI major unit
remains, now blocked on `LOCKFILE-PLATFORM-1`. Dependency maintenance; each is a deliberate
upgrade, not a drop-in bump. Surfaced during the 2026-07-18 dependabot triage.

Two dependabot upgrades that cannot be taken as individual auto-bumps:

- **OpenTelemetry 1.42.1 → 1.44.0 (grouped).** The otel packages are version-locked —
  `AINDY/requirements.txt` pins `opentelemetry-sdk==1.42.1`, which hard-requires
  `opentelemetry-api==1.42.1` — so bumping a single otel package yields `ResolutionImpossible`
  in CI. Dependabot's per-package PRs (**#251** api, **#254** exporter-otlp-proto-grpc) were
  **closed 2026-07-18** for this reason. Do it as ONE PR bumping the whole set together
  (`opentelemetry-api`, `-sdk`, `-instrumentation`, `-instrumentation-asgi`,
  `-instrumentation-fastapi`, `-exporter-otlp-proto-common`, `-exporter-otlp-proto-grpc`,
  `-semantic-conventions`, `-proto`, `-util-http`) to the same 1.44.x line, then run
  Integration Tests (the otel spans exercise the FastAPI/gRPC instrumentation).
  **Grouping shipped 2026-08-01:** `.github/dependabot.yml` now has an `opentelemetry`
  group (`opentelemetry-*`) on the pip ecosystem, so future otel bumps arrive as **one**
  PR — the only shape in which they can resolve. A third single-package PR (**#307**) was
  closed 2026-08-01 after being rebased onto fixed `main` and still failing with
  `ResolutionImpossible`, confirming it was the pin conflict and not that week's
  mcp/nodus CI breakage. The pattern intentionally covers the instrumentation packages,
  which run a separate version line (`-instrumentation-fastapi==0.63b1` against a
  `1.42.1` core) but the same release train.

  **OTel half RESOLVED 2026-08-01 — bumped to 1.44.0.** And a lesson worth keeping:
  **grouping was necessary but not sufficient.** The first grouped PR dependabot produced
  (**#325**, "bump the opentelemetry group with 4 updates") did put all four packages and
  both files in one commit — and still failed, because dependabot resolved each package
  independently and chose an internally inconsistent set:

  ```
  aindy-runtime 1.11.0 depends on opentelemetry-api==1.43.0
  opentelemetry-sdk 1.44.0 depends on opentelemetry-api==1.44.0
  ERROR: ResolutionImpossible
  ```

  It moved `sdk` to 1.44.0 but `api` only to 1.43.0. `opentelemetry-api` 1.44.0 *is*
  published, so this was not an upstream gap — grouping controls **which PR** the bumps
  arrive in, not **which versions** dependabot picks. Expect to hand-align the set.

  The merged set is `api`/`sdk`/`exporter-otlp-proto-grpc` at **1.44.0** with
  `instrumentation-fastapi` at **0.65b0** (the paired instrumentation release). The six
  remaining otel packages are unpinned and resolve transitively. Verified by
  `pip install --dry-run` before pushing — all ten resolve cleanly — then by Integration
  Tests, which is the check that matters since the otel spans exercise the FastAPI/gRPC
  instrumentation.
- **The UI major unit — CLOSED 2026-08-03 (#349):** vite 6→8, `@vitejs/plugin-react` 4→6,
  tailwind 3→4, landed as one commit; #298 / #308 / #310 auto-closed. It was filed as "a
  two-major jump with breaking changes" and **that diagnosis was wrong** — the blocker was
  `LOCKFILE-PLATFORM-1`, and once the resolver existed the code side went green first try.
  The three could not be split: `@vitejs/plugin-react@6` peers on `vite: ^8.0.0`.

  Tailwind 4 specifics are in the commit, but two are worth having here: `@config
  "../tailwind.config.js"` is load-bearing (v4 is CSS-first and otherwise ignores
  `tailwind.config.js` entirely — theme colours, `darkMode: ["class"]`, and the content
  glob scanning `@aindy/ui-kit/dist`, without which every kit-only class is purged), and
  **no border-colour shim was needed** despite v4 changing the default from `gray-200` to
  `currentColor` against 237 bare `border` uses, because `platform.css` already carries the
  shadcn `* { @apply border-border }` base rule.

  react-router 6→7 was originally lumped in with them and **landed alone** (#345) — no
  native bindings. Its actual blocker was `@aindy/ui-kit`'s `react-router-dom: ^6.0.0` peer
  pin, fixed in ui-kit 2.0.0.

- **react-router 7→8 — deferred, and the security alert on it is dismissed.** Dependabot
  alert **#17** (high, `react-router >= 7.12.0, < 8.3.0`, *"RSC Mode CSRF Bypass Allows
  Action Execution Before 400 Response"*) fired on `main` when #345 landed. **Dismissed
  2026-08-03 as `not_used`** — the advisory says outright that it *"only affects your
  application if you are using the unstable RSC APIs"*, and the platform is a client-side
  SPA: `BrowserRouter`, `Routes`, `Outlet`, `Navigate`, `NavLink`, `useNavigate`,
  `useLocation`, no SSR, no server handler. Grepping `platform/src` **and** the
  `@aindy/ui-kit` source for `unstable_`, `react-router/rsc`, `createCallServer` and
  `RSCHydratedRouter` returns zero hits on either side.

  The reason this is deferred rather than patched: the fix is **react-router 8**, another
  major, and `@aindy/ui-kit@2.0.0` peers on `^6.0.0 || ^7.0.0` — so taking it needs a
  ui-kit release first. That is the same cross-repo peer trap #345 spent two PRs escaping,
  and it would be paid for a vulnerability we do not have. **Re-assess** when react-router 8
  is scheduled on its own merits, or immediately if the SPA ever adopts RSC or SSR.

**Reopen/resolve:** when the OTel line is bumped as a group. The UI unit is done.

---

## LOCKFILE-PLATFORM-1 — a Windows-generated lockfile cannot satisfy Linux `npm ci`

**Status:** Open — the workflow ships and the UI unit it blocked has landed (#349, closing
#298 / #308 / #310). Stays open because the underlying npm behaviour is permanent: every
future rolldown/oxide bump needs the same treatment. Found 2026-08-02.

**The failure.** `Platform UI Build` runs `npm ci`, which rejected the branch with:

```
npm error `npm ci` can only install packages when your package.json and
npm error package-lock.json are in sync.
npm error Missing: @emnapi/runtime@1.11.3 from lock file
```

while local `npm install` reported "up to date" and `npm run build` passed.

**Cause** — *corrected 2026-08-03 after reading the resolved lock; the first write-up said
"transitive deps that npm prunes", which is the right shape but the wrong mechanism.*

vite 8 replaces esbuild/rollup with **rolldown**; tailwind 4 introduces the **oxide**
engine. Both ship platform-specific native bindings. The missing `@emnapi/*` packages are
**`bundleDependencies` of `@tailwindcss/oxide-wasm32-wasi`** — itself `optional` and
`cpu: ["wasm32"]`:

```json
"node_modules/@tailwindcss/oxide-wasm32-wasi": {
  "cpu": ["wasm32"], "optional": true,
  "bundleDependencies": [
    "@napi-rs/wasm-runtime", "@emnapi/core", "@emnapi/runtime",
    "@tybys/wasm-util", "@emnapi/wasi-threads", "tslib"
  ],
  "dependencies": { "@emnapi/runtime": "^1.11.1", ... }
}
```

So they are bundled inside that tarball rather than resolved as ordinary transitive deps.
A machine that never installs the `wasm32-wasi` variant never walks into that subtree, and
the lock it writes omits entries the resolving platform demands. The observable failure and
the fix are unchanged — **only the reason is different**, and it matters because the fix
follows from it: this is not something a flag or a cleaner regenerate can reach.

**Not fixable from a Windows machine.** All of these produce the same incomplete tree:

- `npm install`
- `npm install --package-lock-only`
- deleting `package-lock.json` and regenerating from scratch
- `npm install --os=linux --cpu=x64 --libc=glibc` (npm 11 platform targeting)

Running a Linux container **over the mounted working directory** is actively worse — npm
reconciles against the Windows `node_modules` present in the mount and produces a lock with
*only* win32 bindings. Any container attempt must copy `package.json` to a clean directory
inside the container.

**Why esbuild does not have this problem** — the useful diagnostic: esbuild declares its 26
platform variants as **explicit optional dependencies**, so npm records every one, including
`@esbuild/linux-x64`. rolldown and oxide hide theirs as *transitive* deps of platform
packages, which is exactly what gets pruned. So "does the lock contain the other platforms'
packages?" is not sufficient — the question is whether their *transitive* deps are there too.

**Fix — SHIPPED 2026-08-02: `.github/workflows/platform-lockfile.yml`** (`Platform
Lockfile` → job `Resolve Platform Lockfile (Linux)`). Resolves the lock on `ubuntu-latest`,
which is the only way to get a lock resolved on the platform `npm ci` actually runs on. It
will be needed again for every future rolldown/oxide bump, so it is a standing tool, not a
one-off.

**How to use it** (Actions → *Platform Lockfile* → Run workflow):

- `ref` — the branch to resolve on, e.g. a dependabot branch. Blank = the dispatch ref.
- `push` — commit the lock back to that branch. Default **off**: it uploads a
  `platform-package-lock` artifact for you to commit yourself.

**Design notes worth not relitigating:**

- **`workflow_dispatch` only.** It is a tool, not a gate — `npm ci` in `Platform UI Build`
  is already the gate, and running `npm install` on every PR would burn minutes to
  regenerate a file that is almost always correct. Consequence: a dispatch-only workflow
  must be **on the default branch** before it can be run against any other ref, so this
  had to merge first and could not be exercised on its own PR.
- **Incremental (`npm install`), not from-scratch.** Keeping the existing lock means npm
  adds only the entries Windows pruned and leaves every other transitive pin alone, so the
  diff stays reviewable. Deleting the lock first re-resolves the whole tree and buries the
  actual fix in unrelated version churn.
- **No `cache: npm`.** setup-node derives the cache key from the lockfile — the very file
  being regenerated — so a hit would restore a tree resolved against the lock we are
  replacing.
- **Verification is the point:** after resolving, it does `rm -rf node_modules && npm ci`
  and then `npm run build`. Without the clean-tree `npm ci` the job would reproduce the
  exact mistake it exists to prevent (see the process rule above).
- **Linux is authoritative.** A Linux-resolved lock could in principle omit a win32-only
  transitive dep and break `npm ci` on Windows. Accepted: local dev runs `npm install`,
  which self-repairs; CI runs `npm ci`, which does not. **Measured 2026-08-03 and the
  caveat does not bite for these packages:** `npm ci` on Windows against the
  Linux-resolved lock succeeds (112 packages, exit 0, lockfile untouched). rolldown and
  oxide declare all their platform bindings as *explicit optional* deps the way esbuild
  does, so the Linux resolution records the win32 variants too. The asymmetry is real in
  principle but unobserved here; local dev is unaffected.

**First real use, 2026-08-03 — the UI major unit (#349).** Dispatched at
`deps/ui-toolchain-major` with `push: true` (run `30817776715`): resolved, verified with a
clean-tree `npm ci`, built, and committed the lock back. It added **35 packages a
Windows-resolved lock never records** — the whole `@rolldown/binding-*` set (15 platforms),
the whole `@tailwindcss/oxide-*` set (12), plus `@tailwindcss/node`, `@tailwindcss/postcss`,
`enhanced-resolve`, `detect-libc`, `@oxc-project/types`, `@standard-schema/*`. Net
`+937 / −2181` lines. The PR then passed `Platform UI Build` on the first attempt, which is
what the whole exercise was for.

**Smoke-verified on `main` 2026-08-03** (run `30815998362`, dispatched right after the
workflow merged, since dispatch-only workflows cannot run on their own PR): green end to
end — resolve → clean-tree `npm ci` → `npm run build` → dist verify → artifact.

**Expect a benign `"peer": true` diff — do not chase it.** That run reported
`LOCK_CHANGED=true` on an untouched `main`, and the entire diff was **16 lines, all of them
`"peer": true` flags** (plus the paired `license` lines that gain/lose a trailing comma).
Zero packages added or removed, zero version changes. The cause is an npm-major skew, not
the platform:

| | node | npm | writes `"peer": true` |
|---|---|---|---|
| dev machine | 24.13.0 | 11.6.2 | yes |
| all four workflows *(until 2026-08-05)* | 20 | 10.8.2 | no |

So any lock written locally showed this churn when resolved in CI. **The resolver was on the
right side of it** — it pinned the same node as `Platform UI Build`, the job whose `npm ci`
actually gates merges. Consequence, still true whenever the two ends differ: `push: true`
will commit a metadata-only change when nothing real moved, so *"the lockfile changed"* is
not by itself evidence that anything meaningful did. Read the *"Packages added"* output,
which exists for exactly this reason and correctly printed
`(none — versions changed but no packages were added)`.

**Skew CLOSED 2026-08-05: all four workflows moved to node 24, plus a repo-root `.nvmrc`.**
Both ends now run node 24 / npm 11, so local and CI resolve identically and the peer-flag
churn stops. The bump was overdue for a second and sharper reason found while scoping it —
**node 20 reached end-of-life on 2026-04-30** (per `nodejs/Release/schedule.json`), so CI had
been building on an unsupported runtime for three months:

```
v20: EOL 2026-04-30      v22: EOL 2027-04-30      v24: EOL 2028-04-30
```

24 over 22 deliberately: 22 is the conservative pick but leaves the dev-vs-CI mismatch in
place, which was the actual problem. `.nvmrc` is the guard — it keeps a future `nvm use`
from silently re-opening the gap. Note `platform/package.json` still declares no `engines`
field; adding one would make the floor enforceable at install time rather than advisory.

**Process rule this exposed:** verify a lockfile change with **`npm ci`**, never `npm
install` + `npm run build`. `npm install` silently repairs a mismatch; `npm ci` fails on it.
On a machine whose platform differs from CI's, only the second proves anything — and a build
will keep passing off a populated `node_modules` long after the lockfile has gone bad.

---

## MCP-SDK-2X-1 — `[mcp]` extra capped at `mcp<2`; nodus-mcp still targets the 1.x server API

**Status:** Open — pinned workaround shipped 2026-07-31, upstream unblock pending.

**★ SECOND INSTANCE 2026-08-17, in the other direction — `nodus-mcp` now blocks a *nodus* upgrade.**
`nodus-lang 5.0.0` was published and #468 bumped both pin sites. CI failed with
`installed nodus-lang 4.2.0 != pinned 5.0.0`, because **`nodus-mcp 0.1.2` requires
`nodus-lang<5.0.0,>=4.0.0`** and CI installs it *after* `requirements.txt`, so pip resolved back
down.

**This is not a CI problem and must not be fixed in CI.** Verified:

```
$ pip install --dry-run "nodus-lang==5.0.0" "nodus-mcp>=0.1.2"
ERROR: ResolutionImpossible
```

So pinning `nodus-lang==5.0.0` makes **`pip install aindy-runtime[mcp]` uninstallable for a
user**. Isolating the MCP tests into their own job — the obvious "fix" — would let CI go green
while shipping an extra nobody can install. The guard added in #469 is reporting a real
constraint, not an inconvenience.

**Ordering protocol for adopting a new nodus major.** There is no deadlock here, only a sequence,
and it is worth writing down because the failure looks like one:

1. `nodus-lang X.0.0` publishes. *(Nothing downstream can be done before this; the version has to
   exist to be depended on — the same shape as `Boot Smoke` being unable to validate a version
   before its tag exists, `PYPI-PUBLISH-1`.)*
2. **`nodus-mcp` releases a version accepting `nodus-lang>=X`.** It can do this immediately once
   step 1 lands; nothing blocks it.
3. The runtime bumps `nodus-lang` **and** `nodus-mcp` **in one PR**, in **all three** places:
   `pyproject.toml`, `AINDY/requirements.txt`, and the `Install MCP extra` step in
   `runtime-ci.yml` — which installs the packages directly rather than via the extra, so a
   constraint fixed in only the first two is silently re-resolved by the third.

**★ The upstream change worth making, since both packages are first-party:** a hard
`nodus-lang<5.0.0` upper bound on a fast-moving first-party dependency **guarantees** this stall
on every major release. A cap earns its place when a break is known; a prophylactic one converts
every nodus major into a two-repo release train. Either release the two in lockstep, or let
`nodus-mcp` float (`>=4.0.0`) and rely on its own tests to catch a real break.

**Until then the runtime stays on `nodus-lang==4.2.0`.** #468 holds the completed adoption work
(31 gated builtins re-verified, discovery retargeted, defaults assertion inverted) as a draft,
rebased and ready to merge the day a compatible `nodus-mcp` exists. Surfaced
when `mcp 2.0.0` was published and turned every CI run red.

**What broke.** Both install sites specified `mcp>=1.0.0` with **no upper bound** — the
`[mcp]` extra in `pyproject.toml` and, separately, the "Install MCP extra" step in
`runtime-ci.yml` (which installs the two packages directly rather than via the extra, so the
constraint had to be fixed in both places or CI would keep resolving past the cap). The day
`mcp 2.0.0` released, both resolved to it and `Runtime Contracts` failed on
`tests/unit/test_mcp_client_live.py::test_live_mcp_round_trip`:

```
AttributeError: 'Server' object has no attribute 'list_tools'
  nodus_mcp_aindy/server.py:139  in NodusServer._setup_handlers
```

**Not a test bug.** `nodus-mcp 0.1.2` — still the latest release — is built against the 1.x
low-level server API and registers handlers via the `@server.list_tools()` decorator, which
mcp 2.0.0 removed. The failure is raised from `NodusServer.__init__`, so with mcp 2.0.0
installed the extra is broken at server-construction time for real callers, not only under
pytest. **Do not "fix" this by skipping the live test** — that would report green on a
genuinely broken `pip install aindy-runtime[mcp]`.

**Blast radius is confined to nodus-mcp.** `AINDY/platform_layer/mcp_client.py` and
`mcp_server.py` import only `nodus_mcp_aindy` (`MCPClientAdapter`, `discover_tools`,
`ToolRegistry`, `NodusServer`, `syscall_entry_to_tool`) and never touch the `mcp` SDK
directly, so no runtime code needs porting — only the dependency needs to catch up.

**Fix applied:** cap both sites at `"mcp>=1.0.0,<2"`. No code change.

**To resolve:** when a `nodus-mcp` release targets the mcp 2.x server API, lift the cap in
**both** `pyproject.toml` and `.github/workflows/runtime-ci.yml`, bump the `nodus-mcp` floor,
and re-run the live round-trip test — it exercises the real wire end to end, so it is the
check that proves the upgrade. `nodus-mcp` is out-of-tree (PyPI: 0.1.0/0.1.1/0.1.2), so this
is an upstream dependency, not work in this repo.

**Watch for:** dependabot re-proposing `mcp` 2.x. It should stay closed with a pointer here
until the upstream release lands.

---

## DB-NODUS-BUDGET-1 — nodus wall-clock budget (45s) outlives the DB idle cap (30s)

**Status:** **Both fixes shipped 2026-08-01** — cheap guard active by default, root-cause
fix opt-in pending soak. Surfaced 2026-07-31 while diagnosing the `test_agent_vm_parity` CI
failures; verified against real PostgreSQL 2026-08-01 (see below). Both halves rest on
measurement, not inference.

**Fix 1 — the ordering guard (active).** `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` default
**30000 → 60000**, which clears the 45s nodus ceiling with 15s of headroom.
`tests/unit/test_db_nodus_budget_ordering.py` derives the ceiling from the adapter's own
constants and fails if the cap stops clearing it — so raising either nodus budget without
raising the cap breaks CI instead of production. This raises the ceiling; it does **not**
stop the transaction being held.

**Fix 2 — the root cause (opt-in, `AINDY_MEMORY_RECALL_OWN_SESSION`, default off).** The
transaction is opened by a read-only `memory_nodes` SELECT running on the *caller's*
session. `MemoryOrchestrator.get_context` now resolves a dedicated short-lived read session
(`_resolve_read_session`) and closes it in `finally`, so no transaction is ever started on
the caller's session and the connection returns immediately.

**Why not just roll the caller's transaction back:** RT-MEMTXN-LEAK-1 already tried exactly
that (`release_read_transaction`) and it broke `test_agent_approve_idempotency` — Session
`.dirty` cannot see Core `db.execute(UPDATE)` or outer transactions, so rolling back a
request-shared session mid-request discards in-flight state. Not starting a transaction is
the only safe direction. Any failure to obtain a session falls back to the caller's, so
recall can never become unavailable. Opt-in because a caller relying on seeing its own
uncommitted writes through recall would change behaviour — **remaining work is soak, then
flip the flag.**

### Verified by reading the defaults — the two budgets are mis-ordered

| Setting | Default | Source |
|---|---|---|
| Nodus script budget | 30s | `nodus_runtime_adapter.py:29` `_DEFAULT_MAX_EXECUTION_MS` |
| Boot allowance (added on top) | 15s | `nodus_runtime_adapter.py:30` `_DEFAULT_BOOT_ALLOWANCE_MS` |
| **Outer `subprocess.run(timeout=)`** | **45s** | script + boot (NODUS-WARMPOOL-1 Option A) |
| **`idle_in_transaction_session_timeout`** (prod) | **30s** | `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS`, `config.py:283` |

The runtime permits a nodus execution to occupy **45 seconds** of wall clock while Postgres
terminates a connection sitting idle-in-transaction at **30**. A fully in-budget, entirely
legal nodus run therefore has a 15-second window in which the DB can kill its connection
out from under the flow engine. Whatever the outcome of the open question below, these two
defaults should not be ordered this way.

Compounding factor: `SessionLocal` (`database.py:77`) is constructed **without**
`expire_on_commit=False`, so it defaults to `True` — touching any ORM attribute after a
commit silently re-opens a transaction. That is the exact RT-MEMTXN-LEAK-1 Part 2 gotcha
already recorded in `CLAUDE.md`, and it makes "a transaction is open when the subprocess
blocks" the easy accidental state rather than an unlikely one.

### Open question — RESOLVED 2026-08-01: **yes, the transaction is held**

The previously-open half is now **verified against real PostgreSQL**. A transaction IS open
and idle on the flow runner's own session for the entire duration of node execution.

**Method.** A one-node flow registered through `PersistentFlowRunner`, whose node body
sleeps — the faithful analogue of `nodus.execute`, because the runner is blocked inside
`execute_node` either way and the session's transaction state does not depend on what the
node body does. `pg_stat_activity` was sampled every 4s from a **separate** connection
(never perturbing the session under test), filtered by `application_name` so only this
engine's backends were visible. Production timeout settings, not test mode.

**Result** — one backend, held for the whole 20s node:

```
mid-node t+4s    pid=291  idle in transaction  xact_age_s=4.12   idle_s=4.07
mid-node t+8s    pid=291  idle in transaction  xact_age_s=8.19   idle_s=8.14
mid-node t+12s   pid=291  idle in transaction  xact_age_s=12.52  idle_s=12.47
mid-node t+16s   pid=291  idle in transaction  xact_age_s=16.57  idle_s=16.52
mid-node t+21s   pid=291  idle in transaction  xact_age_s=20.60  idle_s=20.55
    last_query: SELECT memory_nodes.id AS memory_nodes_id, memory_nodes.content AS mem…
```

`session.in_transaction()` was `True` at `execute_node` entry, and `xact_age_s == idle_s` on
every sample — one statement, then held. The transaction is opened by a **`memory_nodes`
SELECT** (the memory read on the node path), not by a `run.*` attribute touch as originally
hypothesised; `expire_on_commit` is a compounding factor, not the trigger.

**The xact age tracks the node duration exactly** (4.12 → 20.60 over a 20s sleep), which is
what rules out the alternative explanation. An earlier 6s run showed three backends and was
ambiguous — the two extra sessions were embedding jobs retrying against a deliberately
invalid API key. Lengthening the node to 20s separated the two: a fixed ~4s retry artifact
cannot track a 20s sleep.

**Self-verifying detail:** the transaction survived **20.6s** idle. Had the probe been
running under `settings.is_testing`, the 10s cap would have killed it. Surviving past 10s
proves the 30s production cap was the one in force.

**Therefore the ordering is live, not theoretical.** With 45s of permitted execution against
a 30s idle cap, a nodus run that is slow but entirely in-budget has its connection
terminated at 30s — surfacing as `server closed the connection unexpectedly` →
`PendingRollbackError`, exactly the shape seen in CI under the 10s test cap.

Probe: `scratchpad/dbnodus_probe.py` (kept out of the repo; re-runnable against
`docker-compose.test.yml`'s `postgres-test`).

**Not covered by this verification:** the probe drove `PersistentFlowRunner` directly with a
sleeping node, not a real `nodus.execute` subprocess, and used a one-node flow. The step from
"any node" to "the nodus node specifically" is small — transaction state is independent of
the node body — but it is an inference, not a measurement.

### Why it has not bitten in practice

Warm pool (NODUS-WARMPOOL-1, closed) makes typical executions far shorter than 30s, so this
needs a genuinely slow script or a slow tool call to reach the cap. The CI symptom that
exposed the arithmetic ran under the **10s** test cap, not 30s — see the CI notes in
NODUS-WARMPOOL-1.

**Measured 2026-08-01:** with the warm pool enabled and the cap raised to 60s (#315), the
Integration Tests job goes **green** — so warm execution plus 60s of headroom clears the
one-time plugin load on a real runner. That is a measurement of the *test* configuration
only; it says nothing about the 45s-vs-30s ordering in production, which remains open.

**Candidate fixes** (confirmed — now a matter of choosing, not investigating):

1. **Order the defaults** so the DB idle cap exceeds the maximum permitted execution
   (`30s` script + `15s` boot = `45s`, so the cap must clear 45s). Smallest change, removes
   the mis-ordering outright, but leaves a transaction open across the subprocess — it
   raises the ceiling rather than removing the hold.
2. **Commit-then-detach before `execute_node`** so no transaction spans node execution.
   Addresses the cause. This is the RT-MEMTXN-LEAK-1 rule applied to the runner's own
   session, and given the trigger is a `memory_nodes` SELECT, the fix likely belongs on the
   memory-read path rather than in the runner.
3. **`expire_on_commit=False` on `SessionLocal`** — removes the silent re-open on
   post-commit attribute access. Compounding factor only; does not by itself close this,
   since the observed trigger was an explicit SELECT.

(1) and (2) are complementary: (1) is the cheap guard, (2) is the real fix. Note (2) touches
a shared session, and RT-MEMTXN-LEAK-1 records that rolling back a request-shared session
mid-request breaks in-flight state — so it needs care, not a reflexive `rollback()`.

---

## NATIVE-CI-1 — Rust native scorer crate excluded from CI (green-but-unverified bumps)

**Status:** **CLOSED 2026-08-02** — a `Native Crate Build (Rust)` job now compiles the crate on
every PR. Surfaced during the 2026-07-18 dependabot triage; the gap below is the historical
record.

**What shipped.** A `native-crate` job in `runtime-ci.yml` runs
`cargo build --locked --release` in the crate directory on `ubuntu-latest`. Decisions worth
keeping:

- **`--locked`** is the point for a dependency bump: it fails if `Cargo.lock` would need
  changing, proving the lockfile committed in the PR is the one that actually builds, rather
  than one cargo would silently repair.
- **Build only, no `cargo test`.** The crate has no `#[test]`s, and pyo3's `extension-module`
  feature omits libpython, so a test harness would fail to *link* rather than report anything
  about the bump. Adding tests later means either dropping that feature for the test profile or
  running them through maturin.
- **Not path-filtered, on purpose.** If this is ever promoted to a required check, a `paths:`
  filter would make it never report on PRs that don't touch the crate — and those PRs could
  then never merge. Caching keeps the unconditional run cheap instead.
- **Added to `runtime-ci.yml` rather than a new workflow file**, because a new workflow file
  does not trigger on the PR that adds it, so it could not have been verified in the same PR.
- **No toolchain action needed** — Rust and a C++ toolchain are preinstalled on
  `ubuntu-latest`, so there is no extra pinned third-party SHA to maintain.
- The job covers the **C++ half too**: `build.rs` compiles `memory_cpp/semantic.cpp` via the
  `cc` crate, and `cc` is itself one of the packages dependabot bumps.

**Remaining gap (deliberate):** this builds on **Linux**, not MSVC. The original entry framed
the need as an MSVC build because the Windows dev box is where the crate is normally compiled.
A Linux build catches API-breaking dependency changes — which is what cargo bumps risk — but
would not catch an MSVC-only compilation problem. Adding a Windows matrix leg is the follow-up
if that ever bites; `build.rs` already carries `/std:c++17` and `/O2` flags for MSVC.

**Not yet a required status check.** Branch protection still requires only Runtime Lint,
Runtime Docs Validation, and Runtime Contracts. Promoting this one is a separate call.

**Unblocks:** #292 `uuid`, #296 `serde`, #306 `cc` — held open pending a manual local build, now
gateable on CI.

---

**Historical record (the gap this closed):**

The optional Rust pyo3 memory scorer (`AINDY/memory/native/memory_bridge_rs`, built via
Maturin) is **not compiled or tested in CI** — no MSVC/cargo build job exists. So cargo
dependency bumps to that crate pass all CI checks **green-but-unverified**: nothing in CI
actually builds the crate. Two such dependabot PRs are **held open** pending a local build
rather than merged on a misleading green:

- **#252** `uuid` 1.23.4 → 1.23.5
- **#250** `cc` 1.2.66 → 1.2.67

Both are patch bumps (low risk), but "CI green" is not evidence for the native path. To
verify: local MSVC toolchain build (`maturin build` / `cargo build` in the crate dir) + the
scorer's own tests, then merge. Durable fix: add a native-crate build/test job to CI so cargo
bumps are actually gated. The runtime falls back to the Python scorer when the native crate is
absent, so this is a performance-path gap, not a correctness one.

**Reopen/resolve:** when a native-crate CI build job is added, or when the held bumps are
locally verified and merged.

---

## MEM-RECALL-N1-1 — `recall()` scoring loop issues 3 queries per candidate

**Status:** Open — performance-only. Surfaced 2026-07-19 while verifying RT-MEMTXN-LEAK-1, and
**explicitly not** the cause of that incident (recorded here so the two are not conflated).

`MemoryNodeDAO.recall()` scores each candidate in a Python loop, and each iteration issues
**three** queries (`AINDY/db/dao/memory_node_dao.py`):

| Call | Site | Queries |
|---|---|---|
| `get_graph_connectivity_score(c["id"])` | `:680` | 2 — outbound + inbound `COUNT` on `memory_links` |
| `_get_model_by_id(c["id"])` | `:705` | 1 — full-row `SELECT` on `memory_nodes` |

Candidates are up to `limit * 3` from the semantic path plus `limit * 3` from the tag path, so a
default `limit=5` recall can issue **45–90 queries**. (`get_success_rate` and
`get_usage_frequency_score` are free — they read the already-loaded object.)

**The re-fetch is pure waste.** `_get_model_by_id` re-reads a row the candidate query *already*
selected in full, solely to reach `success_count`, `failure_count`, `usage_count`, and `weight` —
four columns that `_node_to_dict` (`:52`) does not carry into the candidate dict. Adding them to
that dict removes the per-candidate `memory_nodes` SELECT entirely, with no behavior change. The
link counts can then be batched into one `GROUP BY` over the candidate ids.

**Why it is not urgent.** Unlike RT-MEMTXN-LEAK-1, this runs on a *single* session with indexed
lookups and no external call inside the loop — it lengthens one request, it does not drain the
pool. App-side measurement after the cascade fix: a direct `recall()` scanned `memory_nodes` 18
times while holding **1** connection (the `memory_links` counts do not appear in a `memory_nodes`
scan counter, so the true query count is roughly triple that).

**Reopen/resolve:** when recall latency matters, or when a candidate set grows past the current
`limit * 3` bound.

---

## MEM-EXPAND-DEAD-1 — `expand()`'s semantic half is a silent no-op; pgvector 0.5.0 would switch it on

**Status:** Open — behavioural, latent. Found 2026-08-14 while reviewing dependabot #390
(`pgvector 0.4.2 → 0.5.0`). The bump is **held** on this entry; #390 closed, not merged.

**A type mismatch, not a logic error.** pgvector 0.4.2's SQLAlchemy column returns
`numpy.ndarray`, and the guard every consumer passes through tests for `list`:

```python
# pgvector 0.4.2 — AINDY/memory/... via pgvector.Vector._from_db
return cls.from_text(value).to_numpy().astype(np.float32)   # -> numpy.ndarray

# AINDY/db/dao/memory_node_dao.py:126
def _embedding_is_usable(embedding: list | None) -> bool:
    if not isinstance(embedding, list) or not embedding:   # ndarray -> False
        return False
```

Verified by execution, not inference:

| Read type | `_embedding_is_usable` | Effect |
|---|---|---|
| `ndarray` (pgvector 0.4.2, current) | `False` | `find_similar` returns `[]` |
| `list` (pgvector 0.5.0) | `True` | search actually runs |

**Consequence today.** `expand()` (`:1395`) reads a stored embedding back and feeds it to
`find_similar`, which fails the guard at `:417` and returns `[]`. So the **semantic-neighbour
half of `expand()` returns nothing on every call and always has.** It is invisible because the
skip logs at `debug` and the caller just receives an empty list. `include_similar` defaults to
`True` in all four places it appears — `expand()` itself (`:1359`), `memory_router.py:117`,
and `flow_definitions_memory.py:176` and `:247`.

**Second, narrower bug on the same mismatch.** The python fallback at `:483` does
`list(getattr(node, "embedding", None) or [])`; `ndarray or []` raises
`ValueError: The truth value of an array with more than one element is ambiguous`. Only
reachable when the pgvector branch throws, which is why it has not surfaced.

**Why the bump is held rather than merged.** pgvector 0.5.0 fixes both by returning `list` —
but that flips semantic expansion from "silently off" to "on, by default, everywhere", and
`expand()` calls `find_similar` **per node**. Stacked on the existing per-candidate N+1 in
`MEM-RECALL-N1-1`, that adds real query load to precisely the path that produced the
connection-pool exhaustion in `RT-MEMTXN-LEAK-1`. Turning a dead feature on is a decision to
take deliberately with the load understood, not a side effect of a dependency bump.

**No test caught it** in either direction — consistent with `DOCS-COVERAGE-CLAIM-1` (the memory
subsystem has no dedicated behavioural suite). Any fix should land with one.

**Reopen/resolve:** when `MEM-RECALL-N1-1` is addressed, or when semantic expansion is wanted.
Three routes, in the order they were weighed: (1) take the bump behind a default-off flag and
soak, matching the repo's opt-in pattern; (2) widen `_embedding_is_usable` to accept any
sequence — note this activates the path on 0.4.2 too, so it is not the *safe* option it looks
like; (3) take the bump as-is once the load profile is known.

**Unrelated observation from the same pass:** `numpy==2.4.6` is pinned directly in both
`pyproject.toml` and `AINDY/requirements.txt`, but there is **no numpy usage anywhere under
`AINDY/`** (zero hits for `import numpy`, `np.`, `.tolist()`, `.astype(`, `ndarray`). The pin
appears vestigial — it is what made pgvector's "removed dependency on NumPy" a non-issue.

---

## ECOGAP-* — Ecosystem capability gaps (corrected lens)

Derived from the 12-project ecosystem re-audit, re-judged against source-verified
aindy-runtime/Nodus facts. These are **capability/roadmap gaps**, not classic debt
(a shortcut in existing code) — except `ECOGAP-6` (and the narrow `ECOGAP-5a`), which
are debt-shaped. Full analysis: `docs/runtime/ECOSYSTEM_CAPABILITY_GAPS.md`. Several
map onto existing entries (noted per item); do not double-track.

### ECOGAP-1 — Event-sourced durable execution / transparent crash continuation

**Status:** ★ **Phase 3 (Durable Execution) COMPLETE 2026-07-12** — DUR-1→DUR-4 all shipped (transparent crash continuation without per-flow declaration, at-most-once runtime-mediated effects, event-sourced fold for torn-snapshot recovery; one additive schema bump; all opt-in/default-off; remaining is soak-then-flip-defaults). Phases 1 + 2 + 2a shipped (2026-07-08, opt-in); **Phase 3 scoped + reframed 2026-07-12 → `docs/runtime/DURABLE_EXECUTION_PROGRAM.md` (DUR-1..4); DUR-1 + DUR-2 + DUR-2b + DUR-2c SHIPPED 2026-07-12** (DUR-1 opt-in `AINDY_MEMORY_IDEMPOTENCY` position-keyed memory-write dedup; DUR-2 per-run `durable_effects_scope()` engages all 3 chokepoints declaration-free, set by the continuation drivers; DUR-2b threads the signal into the nodus subprocess payload + per-segment memory-scope discriminator; DUR-2c gates the immediate in-subprocess bridge writes (remember/record_outcome, cached-id replay; share is idempotent) — so ALL runtime-mediated effects on a continued run are now at-most-once (only raw un-mediated node side effects remain); DUR-3 flips continuation default-safe via opt-in `AINDY_DURABLE_CONTINUATION_ALL` (all flows/agents except an opt-out deny-list `mark_flow/agent_type_continuation_unsafe`) — **the ECOGAP-1 headline (transparent crash continuation without per-flow declaration) is DELIVERED, opt-in**; all PG/unit-verified. Remaining: DUR-4 optional FlowHistory fold (the only schema bump); flip the default after soak) — roadmap (P0)

**Phase 3 reframe (2026-07-12, source-audited).** A four-front audit reframed Phase 3 away from
"event-sourced deterministic replay in the kernel." Findings: (1) continuation resumes *forward*
and re-runs **exactly one** node/segment — only *its* effects are unsafe; (2) the memory-write
idioms that dominate the hot path (`remember()`, deferred `memory.write()`) **bypass every
EffectRecord chokepoint** (direct `MemoryNodeDAO.save()`), so they double-write on replay; (3)
kernel deterministic replay is **out of scope** — wrong layer (determinism is a VM concern per
`ECOSYSTEM_CAPABILITY_GAPS.md:109`), unnecessary (forward-resume ≠ code re-execution), and huge
(~20 raw `uuid4` sites + an unreachable subprocess boundary). Reframed program (`DUR-1..4`):
**DUR-1** memory-effect boundary (3rd EffectRecord chokepoint, keyed on run+step identity —
keystone/standalone win, no schema); **DUR-2** per-run at-most-once signal (declaration-free);
**DUR-3** flip continuation default-safe (drop the continuation-safe declaration gate); **DUR-4**
(optional) FlowHistory canonicalization + fold (the only schema bump). Core DUR-1→3 needs no
schema change. Full plan + file:line evidence: `docs/runtime/DURABLE_EXECUTION_PROGRAM.md`.

aindy-runtime marks non-waiting `running` flows FAILED on restart; there is no replay log.
WAIT/RESUME + `flow_run_rehydration` + ResumeWatchdog already cover *suspended* flows — the
gap is specifically mid-run, non-waiting work. Field bar: Temporal (event-sourced replay);
LangGraph (pending-writes-then-checkpoint, partial); ADK/OpenHands/Open Interpreter ship
event logs. Absorb targets: ADK append-event fold, LangGraph `versions_seen` vector clock,
Temporal at-least-once idempotent-start. **Do not import weaker JSON-snapshot models.**

### ★ "Replay" means three different things — which one we declined, and what it actually is

**Added 2026-08-18, provenance `ADK-LENS-2026-08-18`.** The Phase 3 reframe above says
*"kernel deterministic replay is out of scope"* without saying what that is, and three unrelated
mechanisms in this space are all called "replay." Six comparative audits now cite one or another of
them at us. Separating them once:

| # | Mechanism | What is stored | What re-executes | Who has it |
|---|---|---|---|---|
| 1 | **Event-sourced state fold** | state *deltas* per step | **nothing** — state is rebuilt by folding the log | ADK `append_event`; **us, as DUR-4** |
| 2 | **Deterministic code replay** | every non-deterministic *result* (clock, uuid, RNG, activity return) | **the code, from the beginning** — with recorded values injected so it takes the same branches | Temporal |
| 3 | **Ordering replay** | the *sequence* concurrent results landed in | nothing; results are re-ordered on merge | ADK `ReplaySequenceBarrier`; us, as `FLOW-PARALLEL-1`'s "merge in declaration order" |
| 4 | **Pending-writes / completed-unit result durability** | a finished unit's *output*, persisted **before** the consolidating checkpoint | nothing — recovery **applies** the recorded result instead of redoing the unit | LangGraph `pending-writes-then-checkpoint`; MAF per-superstep chain (executor state flushed *before* the checkpoint). **Us: flow layer YES (`runner.py:347-359`), agent layer NO** → `RECOVERY-GRANULARITY-1`. *(★ Corrected 2026-08-19: OpenHands was listed here and does **not** belong — its immutable per-event blob log (`filesystem_event_service.py:35-36`) is **row 1**, which we already ship as DUR-4. Two arrivals, not three.)* |

**★ Row 4 was added 2026-08-18 and it matters for how the declined decision is read.** #2 was
declined because determinism is a VM concern, because forward-resume never re-executes code, and
because it constrains every line of workflow code. **None of those reasons apply to #4** — it
persists a *result*, imposes no constraint on the code, and intercepts no non-determinism. So
*"we declined replay"* must **not** be read as covering #4. Three peers arrived at #4
independently (LangGraph, OpenHands, MAF), which makes it the strongest convergence signal in the
comparative corpus.

**#2 is the one that was declined, and it is worth understanding rather than dismissing.** The idea
is that you do not store state at all — you store *inputs*. On recovery you re-run the original
code from the top, and every time it reaches something non-deterministic, instead of *doing* it
again the runtime *looks up what happened last time* and hands back the recorded value. The code
runs again; **the world does not.** Every branch it takes is the branch it took before, because
every input it sees is the input it saw before. When the log runs out — the point where the crash
happened — execution becomes real again and continues forward.

That is why Temporal workflow code must be deterministic: no `datetime.now()`, no `uuid4()`, no
direct I/O. Those are replaced by SDK-mediated equivalents that record on the first pass and replay
on the second, and side-effecting *activities* are not re-invoked at all — their recorded results
are returned. The payoff is real: nothing is re-executed on recovery, and *"what did this run know
at step 40"* is answerable exactly.

**Why it is the wrong shape here, restating the three reasons concretely:**

1. **Wrong layer.** Determinism is a property of whatever runs the code. Here that is the Nodus VM,
   not the kernel — the kernel dispatches, it does not own an opcode loop. Building replay in the
   kernel would put the constraint on the wrong side of the seam.
2. **Unnecessary.** Replay exists to make *re-execution* safe. We do not re-execute: continuation
   resumes **forward** from the last committed node, so only the single in-flight node re-runs, and
   `DUR-1`/`DUR-2` made that node's runtime-mediated effects at-most-once. We do not have the
   problem replay solves.
3. **Huge, and the boundary is in the wrong place.** ~20 raw `uuid4` sites would each need routing
   through a recording shim, and the nodus worker runs in a **subprocess** — so the recording
   boundary does not even sit inside one process.

**★ The cost side is the part that usually goes unsaid: deterministic replay is not a feature you
add, it is a constraint you impose on every line of workflow code anyone ever writes.** That is a
reasonable trade for Temporal, whose product *is* the workflow language. It is a poor trade for a
substrate whose value proposition is that ordinary code can run under it.

**And ADK does not have #2 either** — which is why its own audit calls this *"a design-pattern
edge, not a runtime-durability win."* ADK has #1 and #3, and its workflow loop state is explicitly
non-durable (`_workflow.py:261` `# TODO`). **We have #1 shipped and #3 specified.** The honest
statement of the residual is not *"we lack replay"* — it is: **the single re-run node's
un-mediated side effects are the only thing recovery cannot make safe**, which is recorded above
and is a much smaller claim.

**Phase 1 — flow-level continue-from-last-node (2026-07-08, opt-in default off).**
Key realization: the substrate already existed — `FlowRun.state` is a full post-node
snapshot and `current_node` already points at the next, not-yet-run node, and
`PersistentFlowRunner.resume()` drives the loop from there whenever status != `waiting`.
`core/flow_continuation.py` `try_continue_flow_run` re-claims a stranded
`running`/`executing` FlowRun (atomic `UPDATE … WHERE status IN (running,executing)`) and
re-drives `resume()` on a bg thread — mirroring the WAIT-rehydration path, minus the wait.
Wired into `stuck_run_service.scan_and_recover_stuck_runs` behind a new `continue_stranded`
param that **only the startup caller sets** — continuation is startup-only (no live runners;
continuing a hung-but-alive run would double-drive it — same principle as the RTR-2 job
recovery). The periodic watchdog + async `recovery_jobs` still fail stranded runs. Safety:
the one node that re-runs on continuation must be idempotent, so it only applies to flows
explicitly declared **continuation-safe** (`registry.mark_flow_continuation_safe`; empty
set by default). A durable attempt counter (`state["__continuation_attempts"]`, no schema
change) dead-letters a crash-looping run after `AINDY_DURABLE_CONTINUATION_MAX_ATTEMPTS`
(3). Master flag `AINDY_DURABLE_CONTINUATION` (default off). Tests:
`test_flow_continuation.py`.

**Phase 2 — nodus_vm agent-run crash continuation, segment-boundary (2026-07-08, opt-in).**
`core/agent_continuation.py` `continue_crashed_agent_runs` (startup-only) re-drives a
crashed `executing` **nodus_vm** AgentRun from its last completed segment, reusing the
WAIT-resume machinery — `_build_agent_resume_callback` gained a `claim_status` param so it
claims from `executing` (crash) as well as `waiting` (WAIT). Detection: startup-time
`executing` = orphaned (no live runner); nodus_vm runs identified by the linked
`nodus_agent_execution` FlowRun (AGENT_FLOW runs are left to the flow-side path). Resume
point + `accumulated` derived from `run.result["steps"]` via `_count_completed_segments`.
Gated to **continuation-safe agent types** (`mark_agent_type_continuation_safe`) because the
crashed segment re-runs from its first step (AgentStep is a post-segment batch write, so
mid-segment progress isn't durable — double-fire risk, so idempotent-only). Crash-loop bound
in `result["__continuation_attempts"]` (resets on progress; no schema change). Reuses
`AINDY_DURABLE_CONTINUATION`. Tests: `test_agent_continuation.py`.

**Phase 2a — per-step segment granularity (2026-07-08, opt-in).** Chose the
one-VM-run-per-step route over a cross-process WAL. `split_agent_plan` now expands each
multi-step segment into one segment per tool step behind `AINDY_DURABLE_STEP_GRANULARITY`
(default off) — `_expand_to_step_segments`, base_index kept contiguous, a segment's trailing
WAIT attached to its last step. Safe because `compile_agent_segment` builds `input_payload`
from each segment's own steps and args are static (no VM-level inter-step data flow), so a
1-step segment is self-contained. Reuses everything: the existing per-segment `AgentStep`
write + Phase 2 continuation now resume at **step** granularity (completed steps skip; a crash
re-runs only the in-flight step). Cost: one subprocess VM run per step (hence opt-in). No
worker/compiler/schema change. Tests: `test_agent_step_granularity.py`. **Still deferred:**
the full pending→success **write-ahead log** written from the worker subprocess (at-most-once
for the in-flight step → safe for *non-idempotent* agents) — needs `(run_id, step_index)`
uniqueness + threading `step_index` through `call_tool`; folded into Phase 3 since
non-idempotent safety fundamentally needs the EffectRecord broadening. **Phase 3** — fold
`FlowHistory` as the canonical state source +
thread the REPLAY-1 clock through the execution hot paths for deterministic event-sourced
replay; broaden `EffectRecord`/`execution_guarantee` beyond `AGENT_HIGH_RISK` so continuation
is safe for non-idempotent flows/agents without the per-declaration.
**Phase 3a re-scoped 2026-07-09 → tracked as IDEM-10:** the investigation found the
EXACTLY_ONCE gate is dead in production (guarantee never persisted / EU-PK lookup can't match)
and agent tool calls bypass the dispatcher entirely, so "extend the gate for the nodus_vm
edge" was a false premise — the real work is resurrecting the gate + routing tool calls
through idempotency (IDEM-10), the prerequisite for declaration-free continuation.

**Reopen trigger:** when Phase 2/3 (agent-run continuation, event-sourced replay) is scheduled.

### ECOGAP-2 — Hostile-safe sandboxing (strong-VM tier on non-Linux) — SEE C2/C3

**Status:** Owned by existing entries — **C2 (CLOSED 2026-05-24)** and **C3 (open, Phases 1–4)**.

The ecosystem audit flagged sandboxing as a leading P0 gap; that **overstates** the real state.
Container-grade isolation is closed, certified cross-platform (Linux/Windows/macOS reach
`container-grade-sandbox`), and adversarially escape-tested (17 tests, real Docker, all PASS;
`tests/sandbox/`, `SANDBOX_ESCAPE_AUDIT.md`). Auto-selection is environment-aware:
distributed/production profiles default to `containerized_oci` (the certified tier); only dev
falls back to `insecure_dev_subprocess`. The genuine residual — `strong_sandbox_vm`
(dedicated-VM, hostile-third-party tier) being Linux-only — is **already tracked as C3**. No new
debt. Reconcile the external v2 aggregate + OpenHands/OI/SWE per-project audits against C2/C3.

### ECOGAP-3 — Provider breadth + embedding SPOF — extends MEMORY-EMBEDDING-PROVIDER-1

**Status:** RESOLVED at mechanism level 2026-07-12 (in working tree, uncommitted) — both phases
built. See §MEMORY-EMBEDDING-PROVIDER-1 above (Phase 1 embedding abstraction + reembed migration,
real-PG verified) and its Phase 2 note (LLM registry + Anthropic/Azure providers, real-SDK
verified), and `docs/runtime/PROVIDER_BREADTH_PROGRAM.md`. Remaining: additional concrete providers
(Gemini/Bedrock) on demand; soak. Original roadmap note follows.

Only OpenAI + DeepSeek concretely in tree; OpenAI hard-required for embeddings. The embedding
half is **MEMORY-EMBEDDING-PROVIDER-1**; this entry adds LLM-client breadth (Azure/Anthropic/
Gemini/Bedrock/local) behind `CircuitBreakerLLMClient`. Absorb: CrewAI native multi-SDK +
cross-loop cache-breakpoint, Devika 7-backend registry, litellm reach (Aider/SWE/ADK). Most
broadly cited concrete weakness (9/12 projects). Mechanically straightforward behind the
existing client seam.

**Scoped 2026-07-12 — `docs/runtime/PROVIDER_BREADTH_PROGRAM.md`.** Two phases, sequenced by
owner: **Phase 1 = embedding SPOF** (the `MEMORY-EMBEDDING-PROVIDER-1` half — no seam today,
harder: dimensionality de-hardcode + existing-vector migration is the crux), **Phase 2 = LLM
hosted breadth** (seam already exists behind `FallbackLLMClient`/`resolve_provider_chain`).

**Phase 1 BUILT in working tree 2026-07-12 (uncommitted).** Increment 1 (the seam):
`embedding_providers.py` (EmbeddingProvider protocol + OpenAI default + local + fail-closed dim
validation) + `embedding_service.py` dispatch refactor + `AINDY_EMBEDDING_*` settings +
`[embeddings-local]` extra; zero behavior change on OpenAI default. Increment 2 (dimension +
migration): `AINDY_EMBEDDING_DIMENSIONS`-configurable pgvector column
(`resolve_embedding_column_dimensions()` in `memory_persistence.py` → **schema-contract bump
2026-07-12 → 2026-07-12.1**, baseline regenerated, 2 assertions updated) + `embedding_migration.py`
`reembed_all_memory_nodes()` + `aindy-runtime memory reembed` (ALTER column + re-embed,
fail-closed, PG-only). **Real-PG verified** (vector(1536)→reembed→vector(8), rows re-embedded).
15 unit tests green across `test_embedding_providers.py` + `test_embedding_migration.py`.
**MEMORY-EMBEDDING-PROVIDER-1 is now resolved at the mechanism level** (local embeddings usable
end-to-end).

**Phase 2 (LLM hosted-provider breadth) BUILT in working tree 2026-07-12 (uncommitted).**
`llm_client.py` provider dispatch → extensible registry (`_PROVIDER_FACTORIES` +
`register_llm_provider` + `registered_provider_names`); two concrete providers behind the existing
`FallbackLLMClient` seam: **Anthropic** (`anthropic_client.py`, official `anthropic` SDK/Messages
API, optional `[anthropic]` extra) + **Azure OpenAI** (`azure_openai_client.py`, reuses `openai`
SDK). Config `LLM_PROVIDER`/`LLM_FALLBACK_PROVIDERS` + `ANTHROPIC_*`/`AZURE_OPENAI_*`. 10 tests
green; real-SDK verified. ★ Correctness catch (claude-api skill): Anthropic client does NOT forward
`temperature` (400 on Opus 4.8/Sonnet 5), maps system→`system=`, defaults required `max_tokens`,
default model `claude-opus-4-8`. **ECOGAP-3 now resolved at mechanism level (both phases).**
Remaining: more concrete providers (Gemini/Bedrock) on demand; soak/flip. No schema change.

**Reopen trigger:** when a non-OpenAI provider or local-model path is scheduled.

### ECOGAP-4 — MCP/A2A: gated-egress boundary (runtime) + wire adapters (plugin)

**Status:** G4b **client-side + server-side(stdio + SSE, incl. MEB-3a per-session multi-tenant)
SHIPPED** 2026-07-11 (opt-in). MEB-3b attribution schema bump + G4a strong-egress deferred.

**Update 2026-07-11 — G4b server-side (stdio) shipped (opt-in).** AINDY can run as an MCP
*server* exposing syscalls as tools to external clients (Claude Desktop). `aindy-runtime
mcp-server --transport stdio` (`AINDY/platform_layer/mcp_server.py` + CLI in `runtime_only.py`):
builds a `nodus_mcp_aindy` ToolRegistry from an allowlist of syscalls, each MCP tool handler
`dispatch_syscall(name, args, user_id=<configured>)` (least-privilege cap per syscall,
SDK-SYSCALL-GRANT-1). **Decisions:** single configured identity `AINDY_MCP_SERVER_USER_ID`
(per-session/multi-tenant auth deferred = G4a), read-only default (`AINDY_MCP_SERVER_ALLOW_WRITES`
opts in writes, `AINDY_MCP_SERVER_TOOLS` overrides). Verified write→read-back on real Postgres.
**Deferred:** SSE transport (nodus-mcp #7 — `run_sse_app` omits `/messages/` mount) + multi-tenant
per-session auth. The multi-tenant work is **MEB-3** in the Mediated Effect Boundary program
(`docs/runtime/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md`): per-session identity via
`NodusServer.auth_hook` → `mint_token` + tenant/session columns on EffectRecord, on top of the
MEB-0/1 effect boundary. Doc: `docs/runtime/MCP_INTEGRATION.md`.

**Update 2026-07-11 — SSE transport + MEB-3a per-session multi-tenant SHIPPED (opt-in).** Both
upstream blockers fixed in **nodus-mcp 0.1.2** (#7 `/messages/` mount; #8 `auth_hook` receives real
per-call context with `headers`), pin bumped `nodus-mcp>=0.1.2`. `aindy-runtime mcp-server
--transport sse --host --port` now serves over HTTP. Under `AINDY_MCP_SERVER_MULTI_TENANT=true` the
`auth_hook` resolves each session's `Authorization: Bearer <jwt>` / `X-Platform-Key` header to a
real user via the existing auth surface (`decode_access_token` / `_resolve_platform_key_as_user` —
no new mechanism) and dispatches each call as that identity (threaded via a `_SESSION_IDENTITY`
contextvar), fail-closed (no identity → denied). Multi-tenant rejected over stdio (no per-request
headers). Opt-in, default off; stdio single-identity unchanged. Verified: real 0.1.2 SSE app builds
with `/sse` + `/messages/` and the auth_hook attached. **This is MEB-3a (no schema).** **MEB-3b**
(tenant/session attribution columns on EffectRecord — the program's only schema-contract bump) +
optional per-session capability-ceiling token remain deferred (attribution/audit, not required by
3a).


**Update 2026-07-11 — G4b client-side interop shipped (opt-in).** AINDY agents can now call
external MCP servers' tools. `AINDY/platform_layer/mcp_client.py` (`bootstrap()` on the default
runtime manifest, no-op unless `AINDY_MCP_CLIENT_ENABLED` + `AINDY_MCP_SERVERS`) connects to each
server, discovers its tools via `nodus_mcp_aindy.discover_tools`, and registers each via
`register_tool` → `TOOL_REGISTRY` with capability `outbound.mcp`, risk high. `pip install
aindy-runtime[mcp]`. Verified with a live SSE round-trip. Doc: `docs/runtime/MCP_INTEGRATION.md`.

**Three corrections to the 2026-07-09 scope (verify-first, during the build):**
1. **A2A is out.** `nodus-a2a` is NOT a wire protocol — it's an in-process coordinator
   (registry + delegation *decisions*), zero transport/HTTP/agent-cards. External A2A interop
   is not deliverable from it; it would be a from-scratch build. G4b is MCP-only.

   **★★ CORRECTED 2026-08-19 — the observation was right, the conclusion was not.** That pass
   examined `C:\dev
odus-a2a`, which really is the coordinator. **A SECOND package, also named
   `nodus-a2a`, also at `0.1.0`, holds the wire** — `C:\codev\a2a-wire-pub`, an A2A 1.0.0 (Linux
   Foundation) HTTP+JSON adapter with agent cards, codec and transport in ~1,071 LOC. So "it would
   be a from-scratch build" is **false**: the wire exists and is well factored for host reuse
   (`A2AHttpServer` takes `invoke` as a plain callable, and `handle_request` is a pure function, so
   a host can mount the protocol without adopting the transport).

   **What is actually blocking A2A, in order:** (1) the two packages collide on a **live** PyPI
   name at the same version — the wire cannot ship until that is resolved; (2) the wire caps
   `nodus-lang<5.0.0` while we pin `5.0.4`, the **third** instance of `MCP-SDK-2X-1`; (3) and the
   real one on our side, `INITIATOR-IDENTITY-1` — `token_validator` returns a bool, which
   authenticates the connection and not the peer, so every remote caller collapses to one identity.
   **The wire turned out to be the cheap part.** All Nodus-side items are handed off in
   `docs/runtime/NODUS_HANDOFF_a2a_mcp_packaging.md`.
2. **The executable registration is `register_tool` → `TOOL_REGISTRY`, not `register_agent_tool`.**
   `register_agent_tool` writes to `_agent_tools`, read only by observability/listing — never by
   `execute_tool`. (Latent ABI gap: the "official" agent-tool plugin surface is discovery-only;
   a small runtime fix could bridge `_agent_tools` → execution to make it real.)
3. **`nodus-mcp` packaging gap** (adapters excluded from the wheel) — handed off as nodus-mcp
   issue #5, **RESOLVED in nodus-mcp 0.1.1** (adapters now shipped). A second upstream bug found:
   `nodus_mcp_aindy.NodusServer.run_sse_app()` omits the `/messages/` POST mount (server-side,
   blocks the deferred server direction) — hand off when server-side is scheduled.

**Deferred (unchanged):** G4b **server-side** (expose AINDY tools/syscalls as MCP to Claude
Desktop) — needs an inbound identity/tenant model, which pulls in G4a; and both forms of **G4a**
(thin activation + strong mediated-egress, the latter converging with IDEM-10). Original scope below.

**Original status:** Deferred — roadmap (P1 for the runtime half)

Two altitudes, split deliberately. **G4a (runtime):** a capability-gated egress boundary +
secret-broker so executed/sandboxed code never holds keys (OpenHands' control-plane pattern) —
trusted/enforced at the syscall boundary, a real runtime concern. **G4b (plugin):** the concrete
MCP/A2A wire clients (JSON-RPC envelopes, handshake, SSE framing) are hosted adapters registered
via the plugin ABI — *not* kernel primitives (the kernel owns the socket + the gate, not the
protocol client). `nodus-mcp`/`nodus-a2a` graduate from out-of-tree to registered plugins.

**Verified state (2026-07-09, source-audited — the honest built-vs-inert picture).**
Grounded against source so the reopen scope is real, not aspirational:

- **G4a is scaffolded but inert — the seams are cut, nothing is live.** The enforcement
  chokepoint already exists at `execute_tool` (`agents/tool_registry.py:160-221`) — the correct
  place, precisely because agent tool calls bypass the dispatcher (`call_tool` → `run_agent_tool`
  → `execute_tool`; see IDEM-10). But every guard is dormant:
  - `enforce_capability_policy` (recipient/domain allowlist, `capability_policy.py`) is wired at
    `tool_registry.py:179` yet **gated on `has_capability_policies()`** — no policy is registered
    outside tests, so it is vacuously allow-all in prod.
  - `enforce_capability_rate` + `ResourceManager.rate_limit_hit` (real Redis fixed-window,
    `resource_manager.py:293`) wired at `tool_registry.py:195` — vacuous for the same reason.
  - `capability_scope(_scoped_caps)` wraps the tool call at `tool_registry.py:220`, but its only
    intended reader — `resolve_secret` (`platform_layer/secret_broker.py:231`, with Env/File/
    Vault/Chain backends) — **has zero production callers.** The secret broker is fully built and
    orphaned.
- **Two design gaps beyond "flip it on":** (1) **no true egress chokepoint** — the domain/
  recipient check is *static string inspection of the tool's call args* (`extract_domains`/
  `extract_recipients`), so a tool that builds a URL internally or reads a domain from config is
  unchecked; the only real network guard, `validate_outbound_extension_url` (SSRF blocklist,
  `extension_policy.py:224`), fires at *webhook/callback registration*, not tool egress. (2)
  **secret gating is fail-open on ungated names** — a secret with no registered scope in
  `SECRET_SCOPES` resolves for any caller.
- **G4b has zero runtime code; the plug-in point is ready.** *(★ SUPERSEDED 2026-07-11 — this
  bullet is the preserved original diagnosis and is **no longer true**. See the status header at
  the top of this entry: both halves of G4b shipped. `AINDY/platform_layer/mcp_client.py` (#222)
  and `AINDY/platform_layer/mcp_server.py` (#223) are in tree. **Marked 2026-08-17 because an
  external analysis read this bullet, missed the header ~90 lines above it, and published
  "[Observed] the runtime has no MCP client" against a pin a month after the client shipped** —
  see `MAF-REFERENCE-2026-08-17`. A2A remains genuinely absent: zero matches under `AINDY/`.)*
  No MCP/A2A/JSON-RPC wire code lives
  in this repo (the `json-rpc` string hits are sandbox exec-boundary labels, unrelated). Real
  implementations exist out-of-tree at `C:\dev\nodus-mcp` (client+server, `protocol/jsonrpc.py`;
  `nodus_mcp_aindy/adapters/syscall.py` already duck-types `SyscallEntry`/`TOOL_REGISTRY` → MCP
  `ToolDefinition`), `C:\dev\nodus-mcp-server`, `C:\dev\nodus-a2a` — none are dependencies, none
  registered. The plugin ABI is real and ready: `SURFACE_AGENT_TOOL` (`extension_abi.py:18`),
  `register_agent_tool` (`registry.py:733`), `load_plugins` (`registry.py:1750`). G4b =
  graduate those repos to plugins through this ABI.

**PLAN (G4a):** consolidated into the Mediated Effect Boundary program —
`docs/runtime/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md`. G4a is **MEB-2**: *2a thin activation*
(register a real `CapabilityPolicy` + secret scopes + a real `resolve_secret` path) and *2b
strong* (a true socket/httpx egress chokepoint — the static `extract_domains`/`extract_recipients`
scan is insufficient). The multi-tenant MCP identity work is **MEB-3**. Both hang off **MEB-0**
(the `execute_tool` effect boundary). Detail below.

**MEB-2a SHIPPED 2026-07-11:** config-driven activation — `AINDY_CAPABILITY_POLICIES` /
`AINDY_SECRET_SCOPES` (JSON) load via `_ensure_tools_loaded` (memoized, every process) and
register real `CapabilityPolicy`s + secret scopes, flipping the dormant `execute_tool`
recipient/domain/rate + `resolve_secret` gates live. Opt-in (empty config = no-op). Verified with
a real `execute_tool` out-of-allowlist-domain denial.

**MEB-2b SHIPPED 2026-07-11:** true egress chokepoint — `platform_layer/egress_guard.py` wraps
`socket.getaddrinfo` and raises `EgressDenied` for any hostname resolution outside the active
capability-policy domain allowlist, catching **runtime-built URLs** that MEB-2a's static
arg-string scan cannot see. Installed once process-wide but inert unless an `egress_scope`
allowlist is set; `execute_tool` scopes it for the tool `fn` call only, only when the tool's
capability carries a `domains` policy and `AINDY_EGRESS_ENFORCEMENT` is on (opt-in, default off).
Honest limits (documented in-module): IP-literal connects do no `getaddrinfo` and are uncovered;
a resolution on a non-context-inheriting thread escapes; only resolution is guarded, not connect.
The non-bypassable form is still sandbox `--network none` + mediated proxy — MEB-2b is the
in-process strong-form for the non-sandboxed tool path. **Remaining G4a:** IP-literal /
thread-escape hardening + fail-open-on-ungated-secret hardening.

**Two forms of G4a, pick at reopen:** *thin activation* (register a real `CapabilityPolicy` +
secret scopes + one proving tool that calls `resolve_secret`, behind a default-off flag) ships
the arg-inspection level of assurance cheaply but leaves egress bypassable. *Strong form* — a
**mediated egress point** (tools lose raw outbound network; a broker/syscall injects the secret
and enforces the allowlist at the socket) — is non-bypassable but kernel-adjacent, and it
**converges with IDEM-10**: both are the same "route side-effecting tool calls through a real
boundary instead of trusting `execute_tool` to see everything" work — now the MEB program. Do
the strong form as a dedicated effort alongside/after MEB-0/1, not as a bolt-on.

**Reopen trigger:** when first external MCP/A2A interop is scheduled (G4b), or when a sandbox
needs mediated egress without holding credentials (G4a). If only the *story* is needed before
then, thin-activation G4a is a self-contained opt-in slice; the strong form waits for the
IDEM-10 boundary work.

### ECOGAP-5 — Durable timer (5a) + workflow-as-data (5b)

**Status:** 5a — SHIPPED 2026-07-12 (in working tree, uncommitted). 5b — largely already
DELIVERED (RTR-1); tracking was stale.

**5a — SHIPPED (with a latent-bug fix found by verify-first).** Two parts:
1. **Correctness fix (load-bearing):** `nodus_schedule_service._parse_cron` imported the *vendored*
   `AINDY.apscheduler` CronTrigger and handed it to the real scheduler, which rejects a foreign
   trigger instance (`TypeError: Expected a trigger instance or string`) — so restored Nodus jobs
   silently failed to register in production. Fixed to import the top-level `apscheduler` name (the
   same one `scheduler_service` uses; real in prod, vendored stub under the test pythonpath shadow),
   pinned to UTC. Real-apscheduler verified: the scheduler now accepts the trigger and computes a
   next-run. This means the durable timer actually *fires* now, which the misfire work presupposes.
2. **Downtime-misfire policy (the scoped work):** per-job `misfire_policy` column on
   `NodusScheduledJob` (`skip` default = prior behavior | `run_once`) + `_has_missed_fire` detection
   (via real CronTrigger.get_next_fire_time) + a coalesced one-shot catch-up scheduled in
   `restore_nodus_scheduled_jobs()` when a `run_once` job's fire was due during downtime. Exposed on
   `POST /platform/nodus/schedule` (`misfire_policy`). Schema-contract bump 2026-07-12.1 →
   2026-07-12.2, Alembic 0013 (blank-DB-guarded), head → 0013. 12 unit tests.
   The "unifying durable FireTime primitive" remains a deferred larger ambition (no second use yet).

**5b — largely DELIVERED via RTR-1; the entry was stale.** The claim ("`FLOW_REGISTRY` is in-process
Python; fix = a loadable `.nodus/graphs/<id>.json`") no longer holds: workflow-as-data ships as the
`NodusWorkflow` table (versioned `.nd` **source** as the durable, content-hashed artifact; Alembic
0006) + `register_nodus_workflow` (persist) + `rehydrate_nodus_workflows` (recompile every active row
into the registry on boot, `startup.py`) + `run_nodus_workflow` (execute by name via
`PersistentFlowRunner`). `FLOW_REGISTRY` holds only runtime **kernel** flows (nodus_execute, memory)
— legitimately runtime-owned, not business creep; app/business workflows are already data-defined and
DB-persisted. The loadable-artifact-the-runtime-interprets mechanism exists and is live (`.nd` source
rather than a lossy JSON graph, which is arguably better). Residual: an optional JSON graph
export/import is speculative — defer until a concrete non-`.nd` artifact need appears.

**Reopen trigger:** 5a — the FireTime primitive, if a second durable-timer use appears. 5b — a
concrete need for a non-`.nd` graph artifact format.

### ECOGAP-6 — Execution-path test coverage

**Status:** Largely CLOSED 2026-07-12 (in working tree, uncommitted). The original framing
was partly stale — see the corrected map below.

**What the code actually shows (corrected):** Surface-B (the live Nodus subprocess path) is
*not* uncovered — `tests/integration/test_agent_vm_parity.py` + `test_planner_loop_execute_to_completion.py`
drive the **real subprocess, real flow engine, WAIT→RESUME, tool-failure/retry on real Postgres**,
and CI's `integration-postgres` job runs the whole `tests/integration/` tier against real PG+Redis
(with a coverage upload). So "Surface-B low/zero" and "integration tier mocked in CI" were both
overstated. The genuine gap was **`AINDY/worker/worker_loop.py` (897 L, the prod-default distributed
job executor) at zero coverage** — it is never loaded on the inline/TESTING path — plus the small
worker modules, and the ECOGAP-1 continuation *resume* being unit-only (mocked `_dispatch_resume`).

**Shipped 2026-07-12 (test-only, no product changes):**
- `tests/unit/test_worker_loop.py` — 20 tests: claim/fetch, `process_one_job` (idle / happy /
  already-claimed / missing / failure→DLQ / shutdown-requeue), dead-letter drain, stale-recovery,
  failure-rate alert window, health/heartbeat, semaphore, signal draining, single-thread loop.
- `tests/unit/test_worker_processes.py` — 4 tests: `metric_writer_worker` + `memory_ingest_worker`
  lifecycle + `worker/__main__` orchestration (happy path + schema-gate).
- `tests/integration/test_ecogap6_flow_continuation.py` — 2 real-PG tests: `PersistentFlowRunner.resume()`
  drives a stranded 1-node continuation-safe flow to `success`, and the full `try_continue_flow_run`
  claim→increment→resume→`success` chain (the piece the unit suite mocked). **Real-PG verified.**

**Remaining (small):** `worker/__init__.py` helper coverage; broader multi-node continuation shapes.
Pairs with `ECOGAP-1`. **Reopen trigger:** before relying on new `worker/` behavior in a release claim.

---

## DOCS-BUCKET-A-1 — Runtime docset relocation (Bucket A) residuals

**Status:** **CLOSED 2026-07-17.** Relocation landed 2026-06-27; both close-trigger
residuals now resolved — residual 1 (`DATA_MODEL_MAP.md` surgery, 2026-06-28) and
residual 2 (`ERROR_HANDLING_POLICY.md` runtime/app editorial split, 2026-07-17). Also
resolves app handoff **FR-4** (`APP-FR-*` above). Remaining items 6/7 are annotations/
by-design non-Bucket-A pointers, not open work.

The Bucket A migration relocated runtime-owned docs that were left behind in the
pre-split monolith archive (`C:\dev\masterplan-infiniteweave-monday-node-2025-0411\docs`)
into this repo, mirroring the archive's category dirs:

- `docs/architecture/MODEL_OWNERSHIP_POLICY.md`
- `docs/platform/governance/{AGENT_WORKING_RULES,ERROR_HANDLING_POLICY,CHANGELOG}.md`
- `docs/tutorials/{index,01-memory-driven-workflow,02-event-driven-automation,03-scheduled-execution}.md`

File-path tokens were verified against `AINDY/**` and `aindy-apps-monolith/apps/**`
and rewritten to canonical post-split locations (runtime-moved paths repointed
within `AINDY/`; app-owned modules repointed to `aindy-apps-monolith` with notes).
`RUNTIME_DOC_INDEX.md` gained a "Sibling Docsets" section.

**Residuals / deferred work:**

1. **`DATA_MODEL_MAP.md` Tier-2 surgery — DONE 2026-06-28.** Landed at
   `docs/architecture/DATA_MODEL_MAP.md`, runtime-scoped ("surgery only,
   faithful"). The archive's ~902-line **combined** pre-split schema was
   collapsed: app-domain tables (`freelance`, `masterplan`, `task`, `social`,
   `author`, `leadgen`, `research`, `arm`, `rippletrace`, analytics/`metrics_*`,
   `network_bridge`) reduced to a single ownership-pointer table → `aindy-apps-monolith`
   (canonical list: `DB_OWNERSHIP_CONTRACT.md`). The runtime tables it documented
   (agent, background_task_lease, memory_metrics, memory_trace, memory_trace_node,
   request_metric, system_health_log, user, user_identity + Memory Bridge
   `memory_nodes`/`memory_links`/`memory_node_history`) were **re-verified
   against current source** — corrected several stale/copy-paste claims
   (`Agent.owner_user_id` is a UUID FK not a plain String; bogus `user_id->users.id`
   FK lines on `background_task_leases`/`system_health_logs`/`users` removed;
   `RequestMetric.trace_id`, `User.is_admin`/`token_version`/`api_keys` added;
   `memory_nodes` brought up to date — `visibility`, `source_event_id`/`root_event_id`,
   `causal_depth`, `impact_score`, `memory_type`+`VALID_MEMORY_TYPES`,
   `embedding_pending`/`embedding_status`). Paths repointed
   (`memory_persistence.py` → `AINDY/memory/`; `memory_ingest_service.py` → `AINDY/memory/`).
   §3 Alembic rewritten to the runtime's own tree (`alembic_version_runtime`,
   `0001`–`0005`) with the combined-monolith history pointed to the app repo; §4
   MongoDB collapsed to app-owned. A **Coverage** note enumerates the ~18
   runtime models not individually detailed (kept faithful to the archive's
   table set rather than expanding to the full current model set — deliberate
   scope choice). Deferral references in `AGENT_WORKING_RULES.md` and
   `RUNTIME_DOC_INDEX.md` updated to live links. **Residual:** the doc is
   accurate-but-not-exhaustive — the ~18 Coverage-listed runtime models
   (`effect_record`, `execution_unit`, `flow_run`, `event_edge`, `agent_run`,
   `capability`, `dynamic_*`, `system_event`, `system_state_snapshot`,
   `waiting_flow_run`, `webhook_subscription`, `api_key`, …) are pointered to
   `DB_OWNERSHIP_CONTRACT.md` + source, not field-mapped here. Expand only if a
   full current data-model reference is needed.

2. **`ERROR_HANDLING_POLICY.md` runtime/app editorial split — DONE 2026-07-17.** The
   combined-monolith audit is now a **runtime-only** doc: each section keeps its normative,
   repo-agnostic **Policy Rules** (unchanged) and a rewritten **Runtime Implementation**
   observing `AINDY/...` only (syscall-dispatcher envelope, `llm_client` fallback chain +
   `CircuitBreaker`, `get_db`/`memory_persistence` rollback, scheduler-job pattern,
   `_build_log_handler` OSError guard — all re-verified against source). The ~90% app-owned
   "Current Implementation" observations (genesis, ARM, social, bridge, dashboard,
   authorship, network_bridge, rippletrace, search/seo, tasks) were split out to an
   **App-owned implementation** pointer section directing to `aindy-apps-monolith`
   (`DOCS-MIGRATION-2`); the full pre-split observations remain in git history + the
   pre-split archive. Also dropped one stale gap (the `/tools/seo/*` health-check reference
   no longer exists in `AINDY/routes/health_router.py`). App companion authoring is
   app-team-owned follow-up. Note: the app repo (`aindy-apps-monolith`) has no
   error-handling doc yet and was on an active WIP branch at split time — untouched.

3. **Unverified path tokens — RESOLVED 2026-06-28.** The lone dangling token,
   `deepseek_arm_service.py`, was an **app-owned** ARM concern (not a runtime
   concern). The pre-split file was refactored into the
   `apps/arm/services/deepseek/` package in `aindy-apps-monolith` (analyzer:
   `deepseek_code_analyzer.py`; config/file/security siblings), wired via
   `apps/arm/bootstrap.py` + `apps/arm/syscalls.py`. `ERROR_HANDLING_POLICY.md`
   §2 repointed to that package; the "path unverified" annotation is removed. No
   remaining unverified tokens in the migrated docs.

4. **Pre-split governance docs.** `INVARIANTS.md` has been **split and authored**:
   the runtime-owned half is now `docs/platform/governance/INVARIANTS.md` (this
   repo; PostgreSQL/UTC/memory-graph/auth/startup invariants, enforcement sites
   re-verified against the current tree), companion to the app-owned half in
   `aindy-apps-monolith`. References that previously annotated it as "not migrated"
   were repointed. `SYSTEM_SPEC.md` and `GOVERNANCE_INDEX.md` remain absent in both
   split repos; references retained as historical pointers. Not part of Bucket A.

5. **CHANGELOG relocated verbatim.** The pre-split monolith `CHANGELOG.md` is an
   audit trail; its hundreds of historical path references were intentionally
   **not** rewritten (rewriting would falsify the record). A scope banner marks it
   as pre-split history; current runtime history lives in
   `docs/runtime/DOCSET_CHANGELOG.md`.

6. **Tutorial surface drift** (validated against the live runtime, annotated with
   **Runtime note** callouts, examples left intact so worked outputs stay
   coherent): `sys.v1.event.wait` is not a registered syscall — WAIT/RESUME is the
   Nodus `event.wait()` builtin; `sys.v1.flow.run` field is `initial_state` not
   `input`; trace endpoint param is `{trace_id}`; delete-schedule param is
   `{job_id}`; `extra` is SDK-only (not in the v1 `memory.write` schema). The
   `AINDY.sdk.aindy_sdk` client and the `docs/sdk/` docset are not in this repo
   (published separately as **aindy-sdk**).

7. **`RUNTIME_DOCSET_BOUNDARY.md` relative links** to `../architecture/`,
   `../platform/`, `../apps/` now have the parent dirs present, but several
   *specific* targets it lists (`BOOT_PROFILES.md`, `ARCHITECTURE_MAP.md`,
   `PLUGIN_REGISTRY_PATTERN.md`, `platform/interfaces/API_CONTRACTS.md`,
   `apps/*`) are **not** Bucket A docs and remain unresolved by design.

**Close trigger:** ~~when `DATA_MODEL_MAP.md` surgery lands (residual 1) and the
`ERROR_HANDLING_POLICY.md` runtime/app split (residual 2) is decided.~~ **MET 2026-07-17 —
both residuals resolved (see Status above).**

---

## SYSCALL-STABILITY-1 — two SDK-called syscalls were outside the rename guard

**Status: FIXED 2026-08-13** (guard holes + doc contradictions). One app-side finding filed
below is **open and belongs to `aindy-apps-monolith`**.

**Origin.** A docs pass flagged that `SyscallEntry.stable` and `_STABLE_SYSCALLS` "disagreed on
6 of 17 syscalls". Checking what had actually been *promised* showed the premise was wrong:
**they measure different things and may legitimately differ.**

- `_STABLE_SYSCALLS` (`tests/unit/test_cross_repo_compatibility.py`) is a **rename/removal
  guard** — its own comment says *"SDK depends on these syscall names not being renamed"*.
- `SyscallEntry.stable` is **advertised maturity**, flowing to `GET /platform/syscalls` and into
  `aindy-apps-monolith`'s published `docs/api/API_REFERENCE.md`.

"Experimental shape, but we will not rename it out from under you" is coherent. `memory.list`,
`memory.tree` and `memory.trace` are exactly that.

### What was actually wrong

**1. The rename guard had two holes (FIXED).** The shipped SDK dispatches nine syscalls; two
were unguarded — `sys.v1.memory.list` (`client.memory.list()`) and `sys.v1.execution.get`
(`client.execution.get()`). Renaming either would have passed CI and broken the SDK. Sharper:
**`execution.get` has already failed live once for a neighbouring reason** — the SDK dispatched
it before the runtime registered a handler, and the SDK's own unit tests stayed green *because
they mock the dispatcher* (SDK CHANGELOG, 2026-07-05). Mocked-dispatcher tests structurally
cannot catch a name break; this list is the only thing that can.

**2. `SYSCALL_REFERENCE.md` contradicted the registry on four syscalls (FIXED).** It claimed
`Stability: stable` for `memory.list`, `memory.tree`, `memory.trace` and `sys.v2.memory.read`,
all registered `stable=False`. Three sources, and the runtime's own reference was the **only**
one that disagreed — the monolith's published API reference already said "experimental".

**3. `MEM-DELETE-1` named a consumer that does not exist (FIXED).** It claimed
"SDK `client.memory.delete` is the consumer". `MemoryAPI` has no `delete`; `client.delete()` is
a generic HTTP helper. The syscall stays guarded — it shipped as a public capability with its
own scope — but nothing calls it.

**4. The duplicate-registration guard had no test (FIXED).** Two tests added. See the retraction
below for why that gap mattered.

### Two claims from the audit, retracted

Recording these because both were confidently wrong and the correction is the useful part.

**"`register_syscall`'s docstring promises a guard that does not exist."** It does exist.
`SYSCALL_REGISTRY` is a **custom mapping, not a dict** — the check is in
`SyscallRegistry.__setitem__` (`syscall_registry.py:274`), which raises on a differing handler.
Reading `register_syscall`'s body alone shows no guard, and that is where the audit stopped.
**The error surfaced only because a test written to assert the wrong behaviour failed.** The
guard had zero coverage, which is what let the misreading stand; it now has two tests.

**"`stable=True` was inherited by default, never decided."** For three of the four named, it was
decided and pinned: `test_syscall_execution_get.py` (the test is *named*
`test_registered_stable_with_execution_read_capability`), `test_syscall_agent_cancel.py:84` and
`test_agent_simulate.py:192` all assert `entry.stable is True`. Only `agent.undo` is unpinned,
and it is a sibling of the other two. No change warranted.

### Open, and app-owned — for `aindy-apps-monolith`

`apps/automation/syscalls/syscall_handlers.py` defines **`register_all_domain_handlers` twice**
(lines 824 and 864). Python binds the second, so the first — 11 registrations — is **dead code**.
Three of those dead entries re-register `sys.v1.memory.list` / `.tree` / `.trace` with *different
handlers and different capabilities* (`memory.list` / `memory.tree` / `memory.trace` instead of
the runtime's `memory.read`).

The live function is explicitly narrower (*"Register only automation-owned syscall handlers"*,
9 entries), so the narrowing looks deliberate — but the superseded body was left in place rather
than deleted. **Reviving or reordering it would raise `ValueError` at plugin load**, because the
runtime registers those three names first and the `__setitem__` guard is fatal. Verified against
the runtime they actually install (`aindy_runtime==2.0.1` in their venv), which registers all
three.

Not fixable from this repo. Report on next contact; the fix is deleting the shadowed function.

---

## DOCS-STALE-1 — seven docs carry a `last_verified` that predates the repository

**Status: CLOSED 2026-08-13.** All seven read against source. Filed and closed the same day
from a correctness audit of all 80 files in `docs/runtime/`.

**Finding.** Seven documents declare a `last_verified` date **earlier than this repo's first
commit** (`0d5d382 Initial runtime repo extraction`, 2026-05-17). The frontmatter asserts
verification against a codebase that did not yet exist. Four of them have exactly **one**
commit in this repo — the extraction itself — so nothing has been checked against runtime
source since they arrived from the monolith.

| Document | `last_verified` | Commits here | Lines | State |
|---|---|---|---|---|
| `RETRY_POLICY.md` | ~~2026-04-18~~ **2026-08-13** | 2 | 226 | **✔ verified** |
| `EXECUTION_CONTRACT.md` | ~~2026-05-02~~ **2026-08-13** | 2 | 626 | **✔ verified** |
| `MEMORY_ADDRESS_SPACE.md` | ~~2026-04-19~~ **2026-08-13** | 1 | 248 | **✔ verified** |
| `MEMORY_BRIDGE.md` | ~~2026-04-19~~ **2026-08-13** | 1 | 460 | **✔ verified** |
| `OS_ISOLATION_LAYER.md` | ~~2026-04-22~~ **2026-08-13** | 1 | 297 | **✔ verified** |
| `NATIVE_MEMORY_BRIDGE.md` | ~~2026-04-25~~ **2026-08-13** | 1 | 405 | **✔ verified** |
| `RUNTIME_DOCSET_BOUNDARY.md` | ~~2026-05-10~~ **2026-08-13** | 1 | 95 | **✔ verified — `status: complete`** |

**CLOSED 2026-08-13 — 7 of 7 read against source.** All but one were materially wrong, in different
ways, and none was wrong in the way the staleness signal predicted:

- **`RETRY_POLICY.md`** — structure held; two things did not. The `RetryPolicy` dataclass gained
  a fifth field, `execution_guarantee`, which is load-bearing (the same name appears on
  `SyscallEntry` and on tool-registry entries, and `syscall_dispatcher.py:470` reads it for the
  EXACTLY_ONCE gate). And its **Backoff** section reached a true conclusion from an inverted
  premise: it said all six constants have `backoff_ms=0`, so nothing sleeps. Four of six carry
  200–500ms with exponential backoff and jitter — *and nothing sleeps anyway*, because
  `_sleep_before_retry` has zero callers outside the module and its only consumers,
  `execute_with_retry` / `_execute_with_retry`, are themselves uncalled. **`backoff_ms` and
  `exponential_backoff` are declared, persisted into `ExecutionUnit.extra`, and never applied.**
  The old doc's advice — "update the relevant policy constant" to add backoff — was a no-op.
  *Side finding, code not doc:* `is_retryable_error`'s docstring says "Current system does not
  use this"; three call sites do. Flagged, not edited.

- **`EXECUTION_CONTRACT.md`** — a pre-split *design target*, not a description of the runtime.
  `ExecutionRequest` / `ExecutionRunner` / `ExecutionRecord` / `ExecutionOrchestrator` do not
  exist under any name; three of its five canonical events are not in `SystemEventTypes`; the
  Task/Genesis/ARM subsystems it contracts are app-owned (`AINDY/domain/`, `AINDY/modules/` are
  gone); and the enforcement it claims — `tools/execution_contract_linter.py`,
  `.github/workflows/lint.yml`, `.pre-commit-config.yaml` — has **none** of those three files
  here. Two claims were actively inverted: `/apps/agent/*` is plugin-owned, not runtime-owned
  (same error `PUBLIC_RUNTIME_SURFACES.md` fixed on 2026-08-06 — `AINDY/routes/agent_router.py`
  is a deprecated reference file whose docstring says so), and "register returns a usable JWT
  immediately" is the exact behaviour **2.0.0 removed** (`status_code=202`, no token).
  Annotated in place rather than rewritten — the aspiration is worth keeping as a record; a
  banner now says which parts are real.

- **`MEMORY_ADDRESS_SPACE.md`** — the split is clean and instructive: **the API *inventory* was
  perfect** (3 constants, 16 path functions, 6 DAO methods, 4 DB columns, all present with the
  documented names and defaults), while **everything describing *behaviour* had drifted**. The
  endpoints moved file (`routes/platform_router.py` → `routes/platform/platform_ops_router.py`);
  `/memory/tree` returns `{tree, node_count, path}` where the doc promised
  `{tree, flat, count, root}` — 3 of 4 keys wrong, and there is no `flat` because `flatten_tree`
  has **zero callers**; `/memory/trace` returns `{chain, depth, path}` not
  `{chain, count, root_path}`, caps depth at 20 not 10, and 404s on an empty chain. Two default
  limits wrong (50 not 20, 200 not 100). The syscall table mis-stated 3 of 5 handlers —
  `memory.write` calls `dao.save` **not** `save_at_path`, `memory.list` calls `query_path`
  **not** `list_path`, and `memory.read` silently falls back to `dao.recall` when no path is
  given — and omitted `memory.search` and `memory.delete` entirely. **Sharpest single error:**
  §8 said an un-namespaced write lands in the `_legacy` namespace. It lands in `general`;
  `_legacy` is read-side only, synthesised by `derive_legacy_path` for pre-MAS rows. Anyone
  auditing `/memory/{tenant}/_legacy/**` for recent writes would have found an empty tree and
  drawn the wrong conclusion.

  **Also confirms one half of the stable-flag conflict with hard evidence:**
  `sys.v1.memory.tree` and `sys.v1.memory.trace` are registered `stable=False`
  (`syscall_registry.py:1487`, `:1501`) yet both sit in `_STABLE_SYSCALLS`, where
  rename/removal is a MAJOR bump. `sys.v1.memory.list` is consistent (`stable=False`, absent from
  the contract), which rules out "the whole list is stale" as an explanation.

- **`OS_ISOLATION_LAYER.md`** — architecture intact, *almost everything copyable* wrong. The
  layering, non-fatal pattern, distributed broadcast and FlowRun atomic claim all hold. But:
  **there is no `cpu_time_ms` column** — it is `wall_time_ms`, and the ceiling is
  `MAX_WALL_TIME_MS` fed by an env var still named `AINDY_QUOTA_CPU_MS` "for operator
  compatibility", so an I/O-blocked syscall burns quota as fast as a CPU-bound one.
  `TenantContext` had 3 of 4 fields wrong, omitting `capability_scope`/`namespace` — the two
  that make it an isolation primitive rather than a quota tag. `priority` is a `String(16)`, not
  "1–10"; §§3/5 said integer while §4's table said `"high"|"normal"|"low"`, so **the document
  contradicted itself** and §4 was right. §7's endpoint was in the wrong file, claimed a
  `memory.read` scope it does not use, omitted that it is **self-only (403 on mismatch)**, and
  showed six response keys of which **none** is real.

  **Two failures are silent-into-a-plausible-default, the expensive kind.** §9's config table
  named **`AINDY_REDIS_URL`**; nothing reads it — the variable is `REDIS_URL`, so an operator
  setting the documented name gets the `redis://localhost:6379/0` fallback with no error. And §4
  said WAIT is `sys("sys.v1.event.wait", ...)`; **no such syscall is registered** (only
  `sys.v1.event.emit`) — WAIT is the Nodus `event.wait()` builtin raising `WorkerWaitSignal`.
  That is the *same* error DOCS-BUCKET-A-1 residual 6 already fixed in
  `docs/tutorials/02-event-driven-automation.md`; it survived here because the residual was
  scoped to tutorials. **A corrected claim should be grepped across the whole docset, not fixed
  only where it was found.**

  A third silent failure sits in §3's own code sample: `rm.record_usage(eu, {"cpu_time_ms": 42})`.
  `record_usage` reads `usage.get("wall_time_ms", 0)`, so the unrecognised key is **dropped and
  zero recorded** — no error, a quota counter that simply never moves.

  **Acting on that lesson immediately found two more sites**, both fixed in the same change:
  `DEPLOYMENT_TARGETS.md` listed `AINDY_REDIS_URL` in its *required env vars* block, and
  `FOUNDATIONAL_PATTERN.md` named the `cpu_time_ms` field (while, to its credit, correctly
  explaining the wall-clock semantics that `OS_ISOLATION_LAYER.md` had wrong). The tutorials were
  already correct.

  **One code/CHANGELOG discrepancy surfaced en route — flagged, not edited.** The CHANGELOG entry
  for `EVENTBUS-REDIS-URL-CONSOLIDATION-1` (2026-06-06) states the `AINDY_REDIS_URL` alias was
  *"fully removed — all components now read `REDIS_URL` exclusively."* One component still reads
  it: `AINDY/platform_layer/rate_limiter.py:67` does
  `os.environ.get("REDIS_URL") or os.environ.get("AINDY_REDIS_URL")`. That is what makes the
  DEPLOYMENT_TARGETS error *partial* rather than total.

  **RESOLVED 2026-08-14 — the alias is gone**, and investigating it corrected two claims made
  above.

  *Why it existed:* not a deliberate compatibility shim. `git log -L 67,67:` shows the line
  **untouched since `0d5d382`, the extraction commit** — it predates the 2026-06-06
  consolidation, whose diff touched only `event_bus.py`, `config.py`, `.env.example` plus
  docs/tests. `rate_limiter.py` was never in scope, so it also never received the
  `DeprecationWarning` `event_bus.py` got in the commit before. The CHANGELOG's **per-file list
  was accurate**; only its summary clause — *"all components"* — over-reached, by exactly one
  file.

  *How bad:* **less than stated above.** "Set only the old name and the rate limiter finds Redis
  while the rest falls back" is true only in a non-prod thread-mode deployment. In production or
  under `EXECUTION_MODE=distributed`, a missing `REDIS_URL` **raises at queue init**
  (`distributed_queue.py`, "Production requires RedisQueueBackend… Set REDIS_URL before
  startup" / "EXECUTION_MODE=distributed requires REDIS_URL"), naming the right variable. The
  dangerous case fails fast; it was never silent where it mattered.

  Nothing depended on the alias — no test, compose file, or workflow — so removal was one line,
  guarded by five new tests (`tests/unit/test_rate_limiter_redis_url.py`), verified by restoring
  the alias and confirming the guard fails.

  **Method note worth keeping:** the alias was invisible to every behavioural test because
  `_redis_url` is resolved at *module import*. Nothing about the running limiter differs when it
  is honoured, so only reading the source — or a reload-based test — can see it. That is how a
  scoped cleanup leaves a reader behind and still looks complete.

  **One correction went the other way — the doc was pessimistic.** Its "Cross-Instance
  Limitation" said `can_execute()`/`mark_started()`/`mark_completed()` *"require Redis-backed
  atomic counters"* and to treat `MAX_CONCURRENT_PER_TENANT` as per-instance.
  `RedisResourceBackend` now provides exactly that, switched on by the presence of `REDIS_URL`
  (no separate flag), with Lua scripts for floor-at-zero decrement and set-if-greater peak
  memory, plus key TTLs so a crashed instance cannot leak a slot. An operator following the old
  text would over-provision against a limit that is already global. **Stale docs mis-state risk
  in both directions.**

- **`NATIVE_MEMORY_BRIDGE.md`** — **the one that largely held up**, and worth recording as the
  counter-example. The three-layer FFI architecture, the entire Python interface (`MemoryNode`,
  `MemoryTrace`, both C++-backed functions), **every scoring coefficient** including the
  `usage > 5.0` success-weight switch and `log1p(x)/log(101)` normalisation, the
  `target/release`→`target/debug` module-discovery order, `rebuild_native.ps1`, and all six
  failure modes with their exact log strings and fallback dict — all checked line by line, all
  correct.

  Three defects, all in the build-and-deploy half: **`pyo3` is `0.29`, not `0.19`** (ten minor
  versions, several breaking API generations — CHANGELOG records the Bound-API migration, this
  doc never got the memo); **"Rust 1.70+" is unsourced** (`Cargo.toml` declares no
  `rust-version` and CI pins no toolchain, so the number came from nowhere and is almost
  certainly too low for pyo3 0.29 — left unstated rather than swapping one guess for another,
  with adding `rust-version` named as the fix); and the Deployment section still asserted **CI
  does not build the crate**, naming `.github/workflows/ci.yml`, which does not exist. That
  claim went obsolete when NATIVE-CI-1 closed 2026-08-02, and it **contradicted the note added
  to this same file in #394** three paragraphs earlier.

  The nuance kept: CI *compiles* the crate but never `maturin develop`s it, so the old
  conclusion — Python tests run on the fallback — is still true, for a narrower reason. **A
  build regression is now caught; a scoring regression still is not.**

  **Grepping the docset again paid off**: `RTR.md` still described NATIVE-CI-1 as open
  ("excluded from CI … need a local MSVC build") and still carried the *wrong* diagnosis for
  DEP-UPGRADE-DEFERRED-1 ("vite 6→8 is a breaking UI major" — the real blocker was
  `LOCKFILE-PLATFORM-1`, and the UI unit landed 2026-08-03). Both corrected; RTR's own
  last-verified bumped. It self-describes as a digest that "goes stale fast", which is accurate
  but not a licence to leave closed items marked open.

- **`MEMORY_BRIDGE.md`** — the **model** survived; the **map** did not. Lifecycle, all five
  storage tables, layering and phase framing all check out, and §7's "Partial" on Phase v1 is
  *still honestly Partial*: both `MemoryNodeDAO` classes really do coexist
  (`db/dao/memory_node_dao.py:32`, `memory/memory_persistence.py:239`) and the dead
  `save_memory_node` is still at `:260`. A stale doc being **right about unfinished work** is
  itself worth noting — the reflex to assume everything old is wrong is not safe either.

  **Every `services/…` path in the file was wrong** — nine of them. `AINDY/services/` contains
  exactly one module, `auth_service.py`. Seven relocate (`memory/`, `core/`, package dirs),
  `memory_engine.py` was never built (the abstraction landed as `runtime/memory/native_scorer.py`
  + `scorer.py` fallback), and `infinity_orchestrator.py` is app-owned
  (`aindy-apps-monolith:apps/analytics/services/`).

  **One open debt is actually closed, and the doc's own Next Step with it.** "Embedding generation
  is synchronous on write path (latency risk)" is false — `MemoryNodeDAO.save` sets
  `embedding_pending=True` and calls `_enqueue_embedding`, drained by `ingest_queue.py` →
  `embedding_jobs.py`. Step 4 is done. Cross-referenced `RT-MEMTXN-LEAK-1`, because the async path
  it moved to then developed its own failure mode (the capture → job → capture cascade) and anyone
  reading "we made it async" should meet that immediately.

  **`/memory/metrics*` is plugin-layer, and the prefix was wrong too** — it is
  `GET /apps/memory/metrics`, owner `apps/memory/routes/memory_metrics_router.py`, extracted
  2026-06-06. `ROUTE_OWNERSHIP_INVENTORY.md` **already recorded this**; MEMORY_BRIDGE simply never
  learned it. Third instance of the same pattern after `/apps/agent/*` and `memory_metrics_router`
  — a router file left in `AINDY/routes/` but absent from `APP_ROUTERS` reads as runtime-owned
  until you boot the runtime alone.

  **"RippleTrace" retired as a component name** (RTR-7 dissolved it into `EventEdge`), but the
  grep-the-docset pass found **nothing to fix elsewhere**: `INVARIANTS.md` correctly scopes it as
  an app-domain invariant, and `SYSCALL_SYSTEM.md` uses it for the observability *view*, which is
  how the name legitimately survives (`GET /observability/rippletrace/status`). Recorded because a
  clean grep is a result too — the discipline is a check, not an edit quota.

- **`RUNTIME_DOCSET_BOUNDARY.md`** — not stale, **finished**. A one-time migration plan written
  2026-05-10 and executed by the extraction commit on 2026-05-17, still carrying
  `status: current` and still written in future tense ("Move To …"), which reads as a queue of
  pending work. All three sections verified done: 10/10 runtime docs present, app docs present
  only in the monolith, and — the part that surprised — **"Shared Or Split Later" resolved by a
  different mechanism than planned.** Instead of splitting each shared monolith doc, the runtime
  grew its own companions under different names: `DEPLOYMENT_PROFILES` + `PROFILE_SUPPORT_MATRIX`
  for `BOOT_PROFILES`; `ARCHITECTURE` + `RUNTIME_MODULE_MAP` for `ARCHITECTURE_MAP`; the four
  `EXTENSION_*` docs for `PLUGIN_REGISTRY_PATTERN`; `PUBLIC_API_CONTRACT` +
  `PUBLIC_RUNTIME_SURFACES` + `ROUTE_OWNERSHIP_INVENTORY` for `API_CONTRACTS`. Intent satisfied,
  mechanism different — worth checking for before filing "still outstanding" work anywhere.

  Now `status: complete`, a **new frontmatter value defined in `RUNTIME_DOCSET_GOVERNANCE.md`**
  along with the other two (`current`, `outdated`), because neither fitted: the work is not
  ongoing and the reasoning is not wrong. Note `Runtime Docs Validation` checks only that the key
  is *present*, so the value is unvalidated and a typo would be silent — hence writing the
  vocabulary down.

  Its Current Boundary Notes were also wrong, in the sweep's signature way.

---

### The pattern this audit actually surfaced

**Four separate documents mis-stated plugin-layer routes as runtime-owned**:
`EXECUTION_CONTRACT.md` (`/apps/agent/*`), `MEMORY_BRIDGE.md` (`/apps/memory/metrics*`),
`RUNTIME_DOCSET_BOUNDARY.md` (agent *and* watcher), and `PUBLIC_RUNTIME_SURFACES.md` (corrected
2026-08-06, before this audit). That is not four coincidences — it is one mechanism:

> **A router file left in `AINDY/routes/` but absent from `APP_ROUTERS` reads as runtime-owned
> until you boot the runtime with no plugins.** `agent_router.py`, `memory_metrics_router.py` and
> `memory_trace_router.py` are all still in the tree, unregistered. On any plugin-loaded
> deployment the routes answer, so the mistake is invisible in normal use.

`ROUTE_OWNERSHIP_INVENTORY.md` had the right answer the whole time. Docs that assert route
ownership should be checked against `APP_ROUTERS` and that inventory, not against the presence of
a file.

**Lesson from the seven.** Staleness did not predict the failure mode; seven reads produced
seven different ones: `RETRY_POLICY` was structurally sound with a load-bearing
inversion buried in one section; `EXECUTION_CONTRACT` was wholesale aspirational;
`MEMORY_ADDRESS_SPACE` had a perfect inventory wrapped around drifted behaviour;
`OS_ISOLATION_LAYER` had sound architecture wrapped around a wrong data model, and was
*pessimistic* in one section — the gap it warned about had been closed; and
`NATIVE_MEMORY_BRIDGE` was **substantially correct**, wrong only about its own build inputs
and about what CI does; and `MEMORY_BRIDGE` kept a correct *model* wrapped in a
file-map where every `services/…` path was wrong; and `RUNTIME_DOCSET_BOUNDARY` was not stale at all
but *finished*, mislabelled as ongoing work. Nothing about
the frontmatter date distinguishes those cases. Reading is the only way to tell, which is why
this was filed rather than bulk-dated.

**Pattern worth carrying forward:** in all three, the *names* of things survived and the
*contracts* did not — response keys, defaults, caps, which function a handler actually calls.
Check behaviour before inventory, and check claims about *CI, tooling and route ownership*
hardest of all — those move without anyone editing the doc. Three docs have now mis-stated
plugin-layer routes as runtime-owned; that is a pattern, not a coincidence.

**Why this is filed rather than fixed.** The audit could repair every citation mechanically
(done — see the docset changelog), but it cannot certify 2,357 lines of behavioural prose.
Bumping `last_verified` without reading the code would make the field *worse* than leaving it
visibly stale: a wrong date that looks current is unfalsifiable, a wrong date that predates the
repo is self-evidently a flag.

**The frontmatter check does not catch this.** `Runtime Docs Validation` asserts the five keys
are *present*. It does not assert `last_verified` is a plausible date, and nothing compares it
to the file's own git history. A one-line check — `last_verified >= 2026-05-17` — would have
caught all seven at the commit that introduced them.

**What the audit already proved is right.** Spot-checking cleared the most suspicious claim:
`MEMORY_ADDRESS_SPACE.md`'s four path columns (`path`, `namespace`, `addr_type`, `parent_path`)
**are** present on `MemoryNodeModel` exactly as documented. So these are not fiction — they are
unverified, and at least one is materially accurate. Treat the list as a reading queue, not a
deletion queue.

**Close trigger:** ~~each document read against source once, `last_verified` bumped to that
date.~~ **MET 2026-08-13 — all seven done** (#395, #396, #397, #398, #399, and the boundary
closure). The follow-on prevention — a `last_verified >= 2026-05-17` assertion in
`Runtime Docs Validation` — is **still not implemented**, and is now cheap to add: no document
violates it any more, so the check would go green on the commit that adds it. That was the
blocker when this was filed.

---

## DOCS-COVERAGE-CLAIM-1 — docs claimed test suites that never existed

**Status:** Corrected in the docs 2026-08-13; **the coverage gap it exposed is open.**

**Finding.** Six documents cited **eight** test files by path, several with precise counts
(*"61 tests (Groups A–K)"*, *"26 tests"*, *"64 versioning/ABI tests (Groups A–J)"*). Checked
against the complete history of both `aindy-runtime` and `aindy-apps-monolith`: **none of the
eight has ever existed.** Not moved, not renamed — never created.

| Cited path | Named in |
|---|---|
| `tests/unit/test_memory_address_space.py` | `MEMORY_ADDRESS_SPACE.md` |
| `tests/system/test_memory_loop_e2e.py` | `MEMORY_BRIDGE.md` |
| `tests/integration/test_memory_bridge.py` | `NATIVE_MEMORY_BRIDGE.md` |
| `tests/integration/test_memory_native_scorer.py` | `NATIVE_MEMORY_BRIDGE.md` |
| `tests/unit/test_os_layer.py` | `OS_ISOLATION_LAYER.md` |
| `tests/unit/test_event_bus.py` | `OS_ISOLATION_LAYER.md` |
| `tests/unit/test_syscall_dispatcher.py` | `SYSCALL_SYSTEM.md` |
| `tests/unit/test_syscall_versioning.py` | `SYSCALL_SYSTEM.md` |

`tests/system/` is not even a directory in this repo.

**The sharpest one.** `NATIVE_MEMORY_BRIDGE.md` introduced two of them under the heading
*"Focused tests that exist in this repository"*, and reproduced a CI job running both. No file
under `tests/` references `memory_bridge_rs` at all. This agrees with **NATIVE-CI-1**, which
records the crate has no Rust tests either — so the native scorer has **no behavioural
coverage in either language**, while the doc asserted a working pytest suite.

**Docs corrected, not quietly deleted.** Each claim was replaced with a dated note naming what
it used to say. A silent deletion would erase the evidence that the coverage was believed to
exist — which is the part worth remembering.

**Real remaining gap (this is the open half):** MAS path addressing, the OS isolation layer,
the distributed event bus, and the native scorer each have no dedicated suite. Overlaps
**ECOGAP-6** (execution-path coverage) — do not double-track the execution paths; the four
areas above are the increment.

**★ COVERAGE HALF CLOSED 2026-08-14 — all four areas now have suites**, written at the exact
paths the docs had cited. Writing them surfaced **five** defects the missing coverage had been
hiding (`NATIVE-PARITY-1`, `NATIVE-DISCOVERY-1`, `MAS-FLATTEN-1`, `EVENTBUS-PUBLISH-LATCH-1`,
`TENANT-FROZEN-SHALLOW-1`, all below):

| Area | Suite | Tests |
|---|---|---|
| MAS path addressing | `tests/unit/test_memory_address_space.py` | 84 |
| Native scorer | `tests/unit/test_memory_native_scorer.py` | 75 (33 need the compiled extension; CI builds it — see below) |
| OS isolation layer | `tests/unit/test_os_layer.py` | 46 |
| Distributed event bus | `tests/unit/test_event_bus.py` | 44 |

**247 tests.** All four are marked `runtime_only` — without which CI collects none of them.
That marker rule is `CI-MARKER-1`, filed separately; it is *why* this gap stayed invisible, but
it is a distinct problem — `CI-MARKER-1` is tests that exist and never run, this entry was tests
never written. Fixing one does not fix the other.

**★ The suites are made to actually run — a fourth variant of the same trap, closed.** 33 of the
native tests (the kernel contract and the parity assertions that pin `NATIVE-PARITY-1`) need the
compiled extension, and a skip reads as green. `Native Crate Build (Rust)` compiles the crate but
never imports it, so on the first cut those 33 skipped in CI — coverage that existed, was
collected, and still tested nothing. Fixed in two halves:

1. `Runtime Contracts` now builds the crate itself and renames the artifact
   (`cargo build --locked --release`, then `libmemory_bridge_rs.so` → `memory_bridge_rs.so`;
   cargo emits a `lib`-prefixed cdylib that Python will not import — the Linux twin of the
   `.dll`/`.pyd` mismatch in `NATIVE-DISCOVERY-1`). It shares the Rust job's cache key.
2. The job sets `AINDY_REQUIRE_NATIVE_BRIDGE=1`, under which
   `test_native_bridge_is_importable_when_ci_says_it_must_be` **fails** instead of skipping if
   the extension is missing, plus a companion test that calls into it so an
   importable-but-broken extension is caught too. Verified in all three states: unset → skips
   (local dev unaffected); set + present → passes; set + artifact removed → two explicit
   failures.

Without (2), a future break in the build or the rename would silently delete the parity
coverage while the job stayed green — which is the exact failure this whole entry is about.

**Scope note, so this is not read as more than it is.** `test_os_layer.py` covers TenantContext
(the isolation boundary) and ResourceManager (quota/concurrency); the SchedulerEngine
WAIT/RESUME half is covered through `test_event_bus.py` and existing ECOGAP-6 work, not
re-tested here. Still open under ECOGAP-6, not this entry.

**Gotcha found while writing it:** `ResourceManager.can_execute` returns `(True, None)`
unconditionally when `settings.is_testing`, so quota enforcement is **vacuous in the test
environment**. Any naive quota test passes without exercising a single counter. The suite
patches `is_testing` off — and note it is a pydantic *property*, so it must be patched on the
class; `patch.object(settings, "is_testing", False)` raises `AttributeError`.

---

## CI-MARKER-1 — 268 unit tests run in no CI job; a green PR does not mean `tests/unit` passed

**Status: CLOSED (2026-08-15).** Found 2026-08-14 while confirming whether two local test
failures were caused by the dependabot sweep (#404). They were not; the investigation surfaced
this instead.

**What shipped — both halves, because marking the 24 files alone leaves the footgun armed:**

1. The 24 files carry `pytestmark = pytest.mark.runtime_only` explicitly. **Four of them never
   imported `pytest` at all** — `test_background_leadership`, `test_capability_token_refresh`,
   `test_nodus_tool_seam`, `test_reconcile_backfill` — which is its own evidence that nothing
   had ever required them to behave like pytest files.
2. **`tests/unit/conftest.py` makes the default safe.** A `pytest_collection_modifyitems` hook
   applies `runtime_only` to every item collected from `tests/unit/` unless it already carries
   it, or carries a marker handing it to a different job (`FOREIGN_JOB_MARKERS`:
   `integration`, `sandbox_escape`, `redis`, `mongo`, `multi_instance`). Opting a unit test out
   is still possible; it now takes a deliberate marker rather than an omission. The hook filters
   by path, because a directory conftest's `modifyitems` receives the **whole session's** items.

**Measured before and after:**

| | Collected | Deselected |
|---|---|---|
| Before | 1587 / 1943 | 356 |
| After | 1855 / 1943 | 88 |

**+268, exactly the number this entry predicted.** The remaining 88 are `tests/integration` and
`tests/sandbox`, which have their own jobs. Coverage went **up**, to 56.71% against the
`--cov-fail-under=35` gate — so the re-baselining this entry warned about was not needed, but
the number should still be raised deliberately at some point rather than left at 35.

**★ The guard is mutation-checked 3/3, and it had to be, because the obvious way to test this
is vacuous.** A test that imported `tests/unit/conftest.py` and called the hook with a fake item
would keep passing after pytest stopped loading the hook at all — the "covers, asserts nothing"
family. `tests/unit/test_ci_marker_default.py` instead spawns a **real pytest subprocess**
against probe files generated on the spot:

| Mutation | Fails |
|---|---|
| `item.add_marker(...)` removed | `test_unmarked_unit_file_is_collected` |
| `FOREIGN_JOB_MARKERS` branch removed | `test_foreign_job_marker_opts_out` |
| hook disabled **and** one file unmarked | `test_no_unit_test_escapes_the_marker` |

The third asserts the complement — `pytest tests/unit -m "not runtime_only"` must collect
nothing — which is the invariant this entry actually cares about.

**★ Turning them on found exactly the failure predicted below**, and it is worth keeping as the
shape to expect: `test_infinity_async_job_loop.py::test_async_context_lets_execution_events_past_contract_gate`
asserts that an `execution.*` event raises when emitted **outside** a pipeline — and it
*assumed* that precondition rather than arranging it. `pipeline_active` is a ContextVar an
earlier test can leave set, so in a full-suite run the gate did not fire and it failed with
`DID NOT RAISE`, while passing in isolation. Fixed by calling `set_pipeline_active(False)` up
front, asserting the precondition, and resetting the token in a `finally` so the test no longer
leaks the state it was itself a victim of. Reproduced deterministically with a throwaway probe
file that sets `pipeline_active(True)` and never resets — with the fix the pair passes, without
it the ordering failure appears on demand.

**Still failing locally and expected to:** `test_runtime_packaging.py::test_runtime_build_artifacts_include_runtime_owned_assets`
fails on `python -m build --no-isolation` locally and passes in CI. It *is* marked and always
was; unrelated to this change.

**Gotchas worth keeping:**

- **`--collect-only -q` prints `<path>: <count>`, not node ids.** A first draft of the guard
  asserted on a node id, found the probe collected correctly, and still failed.
- **Exit code 5 is `EXIT_NOTESTSCOLLECTED`, not an error.** The complement test's collection
  legitimately returns 5, so a naive `returncode == 0` assertion inverts it.

---

**Original filing follows.**

**Mechanism — one missing marker makes a test file invisible.** `Runtime Contracts` is the only
job in `runtime-ci.yml` that runs unit tests, and it runs:

```yaml
python -m pytest tests -m runtime_only -q --cov=AINDY --cov-fail-under=35
```

The marker is applied per-file as `pytestmark = pytest.mark.runtime_only`. **Nothing applies it
automatically** — `pytest.ini` only *declares* the marker in `markers =`, and `tests/conftest.py`
has no `pytest_collection_modifyitems` hook. So a new unit test file defaults to **not run**,
and nothing about the PR looks different.

The Integration job does not compensate: `pytest.integration.ini` sets
`testpaths = tests/integration`, so it never reaches `tests/unit/` at all.

**Measured, not estimated:**

| | |
|---|---|
| `pytest tests -m runtime_only` (what CI runs) | **1230 collected, 349 deselected** of 1579 |
| `tests/unit/` files carrying the marker | 122 of 146 |
| **Unit tests in no CI job** | **268, across 24 files** |

(The 349 − 268 remainder is `tests/integration` and `tests/sandbox`, which *do* have their own
jobs — `pytest -c pytest.integration.ini` and `sandbox-escape-linux.yml`. The 268 are the
genuinely uncovered set.)

**The 24 files, by test count:**

```
23 test_nodus_workflow_registry     10 test_runtime_degraded_modes
23 test_agent_vm_execution          10 test_background_leadership
22 test_agent_plan_compiler          8 test_nodus_flow_compiler
21 test_infinity_next_action         8 test_agent_runtime_guardrails
21 test_deployment_profiles          7 test_nodus_tool_seam
16 test_infinity_score_event         6 test_infinity_async_job_loop
15 test_syscall_contract             6 test_empty_env_typed_settings
15 test_agent_wait_policy            6 test_effect_record_cleanup
12 test_clock                        5 test_transactional_email_isolation
11 test_infinity_recall_event        5 test_reconcile_backfill
                                     5 test_event_bus_redis_url
                                     5 test_capability_token_refresh
                                     4 test_infinity_support_metrics
                                     4 test_aindy_env_file
```

**Why this is worse than a count suggests.** These are not peripheral files:

- `test_syscall_contract.py` is the file **CLAUDE.md's own Commands section uses as its worked
  example** (`pytest tests/unit/test_syscall_contract.py::test_name -v`).
- `test_reconcile_backfill.py`, `test_empty_env_typed_settings.py`,
  `test_transactional_email_isolation.py` are the regression tests for **FR-8 / FR-10 / FR-9** —
  the three upgrade-path defects that forced the 2.0.1 release. The tests written so those
  never recur are not executed.
- `test_clock.py` (REPLAY-1), `test_background_leadership.py` (LEASE-1), all five
  `test_infinity_*` (INFINITY-RUNTIME-1), `test_agent_vm_execution` / `test_nodus_*` (RTR-1),
  `test_runtime_degraded_modes.py` (the DEGRADED_MODE matrix work).

Each of those fixes can be reverted and CI stays green.

**Fix.** Add `pytestmark = pytest.mark.runtime_only` to the 24 files, then make the default
safe so this cannot recur — the durable half. Two options, in preference order:

1. A `pytest_collection_modifyitems` hook in `tests/conftest.py` that auto-applies
   `runtime_only` to everything under `tests/unit/` lacking an explicit marker. Removes the
   footgun entirely; a new file is covered on creation.
2. A CI assertion that `pytest tests/unit --collect-only -q` and
   `pytest tests/unit -m runtime_only --collect-only -q` return equal counts — cheap, and fails
   loudly on the next unmarked file.

**Expect new failures when they start running.** Two are already known and are *pre-existing*,
verified against a clean `main` worktree baseline, not caused by #404:
`test_infinity_async_job_loop.py::test_async_context_lets_execution_events_past_contract_gate`
passes in isolation but fails in a full-suite run (test-order pollution — compare the
`pipeline_active` ContextVar leak noted under RTR-3/4/6/7), and
`test_runtime_packaging.py::test_runtime_build_artifacts_include_runtime_owned_assets` fails
locally on `python -m build --no-isolation` but passes in CI (it *is* marked). Marking the 24
should therefore be done as its own PR, so any newly-surfaced red is attributable.

**Note the `--cov-fail-under=35` interaction:** adding 268 tests changes measured coverage.
Expect the number to move and re-baseline deliberately rather than lowering the gate.
---


## NATIVE-PARITY-1 — the native and Python scorers disagree for a negative `impact_score`

**Status: CLOSED (2026-08-15).** `scorer.py` now clamps both ends
(`min(1.0, max(0.0, impact / 5.0))`), matching the Rust engine. Verified against the real
extension across `-1e6 … 25.0`: **delta 0.0 at every point**, where it had been +0.300 at
`impact=-10`. Found 2026-08-14 writing the DOCS-COVERAGE-CLAIM-1 native suite; verified against
a locally-built crate, not inferred.

**★ Correction to this entry's original severity claim.** It closed with *"Reachable whenever a
stored `impact_score` is negative"*. That was asserted without checking whether a negative can
be stored — and it cannot, through the runtime's own path. `MemoryNodeDAO.save()` writes
`impact_score=max(0.0, float(impact_score or 0.0))` (`memory_node_dao.py:209`, mirrored at
`:725`), and `save()` is the universal write chokepoint. The producers are non-negative by
construction too: `calculate_impact_score` sums counts and a positive bonus;
`blend_impact_with_significance` floors its input at 0 then takes a `max` with a floor. So the
divergence was **latent, not live** — real, but not reachable through any runtime write path.

That does not make the fix unnecessary — two engines silently disagreeing is a hazard whichever
inputs are currently possible, the column carries **no `CheckConstraint`**, and
`_prepare_node` will score any dict handed to it (an app-supplied candidate need not have passed
through `save()`). But the honest severity was *defense in depth*, not *live mis-ranking*, and
the original wording overstated it.

`AINDY/runtime/memory/scorer.py` runs whichever engine is available, and the two implement the
same formula — except for the impact term:

```rust
// lib.rs:181  (Rust)
let impact_bonus = (impact_scores[idx] / 5.0).clamp(0.0, 1.0) * 0.15;
```
```python
# scorer.py:120  (Python fallback)
impact_bonus = min(1.0, prepared["impact_score"] / 5.0) * 0.15
```

`clamp(0.0, 1.0)` bounds **both** ends; `min(1.0, …)` bounds only the top. For a negative
`impact_score` the Python term goes negative while Rust floors it at zero. Measured against the
real extension:

| `impact_score` | native | python | delta |
|---|---|---|---|
| −10.0 | 0.420038 | 0.120038 | **+0.300** |
| −1.0 | 0.420038 | 0.390038 | +0.030 |
| ≥ 0 | — | — | 0.000 |

A **0.30 delta on a 0.42 score is 71%** — easily enough to reorder a recall result set.

**Why this is more than a formula nit.** Which engine runs is not a decision anyone makes:
`_load_bridge()` returns the extension if it happens to be importable and `None` otherwise. So
the *same* recall on the *same* data ranks differently depending on whether the crate was
built — and `USE_NATIVE_SCORER` silently changes ranking, not just speed.

**What shipped (2026-08-15).** Python clamped rather than dropping the Rust lower clamp — the
term is a *bonus*, so flooring at zero is the coherent reading, and `_normalize_usage` in the
same function already floors its input the same way.

- `runtime/memory/scorer.py:120` — `min(1.0, max(0.0, impact / 5.0))`.
- `memory/memory_scoring_service.py:31` — **the same unclamped shape, one file over**, found
  while fixing this. It has no native counterpart so there was no parity to break, but a
  negative would still have turned a bonus into a penalty. Floored for consistency; behaviour
  changes only for inputs that cannot currently occur.

**Test changes.** The `xfail(strict=True)` parity test became a plain passing assertion
(`test_engines_agree_on_negative_impact`), widened with `-1e6` since the error grew with
`|impact|` — a regression shows first at the extreme. The old "divergence is exactly the
un-clamped term" test was replaced by
`test_negative_impact_contributes_exactly_zero_bonus`, which is strictly stronger: agreement
alone could be agreement on a *wrong* value, so it asserts a negative impact scores identically
to a zero impact — no bonus and no penalty.

**★ Guarded where the native suite cannot reach.** The parity tests need the compiled extension
and skip without it, so the clamp is *also* pinned by two native-independent tests in
`TestPythonFormula` — `test_impact_bonus_floors_at_zero` and
`test_impact_bonus_is_monotonic_across_the_sign_boundary`. Without those, the regression guard
would exist only where the crate happens to be built, which is the `DOCS-COVERAGE-CLAIM-1`
mistake in miniature.

**No schema, migration or contract impact** — pure scoring arithmetic; `scripts/check_schema_version.py`
leaves the baseline untouched.

---

## NATIVE-DISCOVERY-1 — the two crate consumers search different directories

**Status: CLOSED (2026-08-15).** Both consumers now delegate to a single loader,
`AINDY/memory/native_bridge.py`. Found 2026-08-14 alongside NATIVE-PARITY-1.

Two modules load the same extension and disagree about where it lives:

| Consumer | Searches | Result with a `--release` build |
|---|---|---|
| `runtime/memory/native_scorer.py:127-153` | `target/release` **then** `target/debug` | finds it |
| `memory/embedding_service.py:191-198` | `target/debug` **only** | never finds it |

`Native Crate Build (Rust)` runs `cargo build --locked --release`, and any real deployment would
build release too — so `cosine_similarity` silently uses the pure-Python fallback while
`native_scorer`, in the *same process*, uses the C++ kernel. Verified locally: with only
`target/release/memory_bridge_rs.pyd` present, `cosine_similarity` returned without ever
importing the module, while `_load_bridge()` loaded it from the release path.

**Two smaller things in the same function**, both pinned by tests:

- `except (ImportError, AttributeError, Exception)` is just `except Exception`. It also swallows
  the extension's `ValueError` on ragged input, so a genuine length-mismatch bug returns `0.0` —
  indistinguishable from "not similar".
- The `sys.path.insert(0, …)` runs on **every call**, not once at import.

**What shipped (2026-08-15).** `AINDY/memory/native_bridge.py` owns the search paths, the
profile order and a once-per-process cache; `native_scorer._load_bridge()` and
`embedding_service.cosine_similarity()` both delegate to it. It lives under `AINDY/memory/`
because the crate does, and because `runtime/ → memory/` is the established import direction
(four existing examples), not the reverse.

**★ A second defect, found by reproducing it.** The plan said `native_scorer` "already has the
right shape (both profiles, cached)" — so the new loader copied that shape, and the extension
then resolved from `target/debug` **with a release build present**. The cause is in the original:

```python
for path in (release_path, debug_path):   # priority order
    sys.path.insert(0, path)              # ...but each insert goes to the FRONT
```

`insert(0, …)` puts each entry ahead of the previous one, so iterating in priority order leaves
the **lowest**-priority path first. `native_scorer`'s documented "release, then debug" was
inverted in practice: a stale debug build silently shadowed a fresh release one. The shared
loader iterates `reversed(search_paths())`, verified by loading from `release` with both
profiles built, and pinned by `test_the_shared_loader_prefers_release_over_debug`.

**The two smaller things are fixed too.** `except (ImportError, AttributeError, Exception)` — a
plain `except Exception` in a costume — is now an explicit `except ValueError` for ragged input
(deferring to `cosine_similarity_python`'s 0.0, which is *correct* here: the sole caller is the
recall fallback, where a node re-embedded at a different dimension is genuinely incomparable,
not a programming error) plus a logged catch-all for anything unexpected. And the
`sys.path.insert(0, …)` no longer runs on every call — the loader caches.

**Artifact naming is documented in the loader**, since it is the recurring trip hazard:
`cargo build` emits `libmemory_bridge_rs.so` / `memory_bridge_rs.dll`, neither of which Python
imports. `Runtime Contracts` renames in CI; a local build needs it done by hand.

---

## EVENTBUS-PUBLISH-LATCH-1 — three failed publishes disable cross-instance events permanently

**Status: CLOSED (2026-08-15).** Suspension is now a `CircuitBreaker` that re-probes once after
`AINDY_EVENT_BUS_PUBLISH_RECOVERY_SECS` (default 60) and closes on success. Found 2026-08-14
writing the DOCS-COVERAGE-CLAIM-1 event-bus suite; verified by driving `EventBus.publish`
through a failing then a healthy client.

**The root cause was narrower than "no recovery": one field meant two things.** `self._enabled`
was both the operator's kill switch (`AINDY_EVENT_BUS_ENABLED`) *and* the runtime give-up latch.
Writing transient health into a config flag is what made the outage permanent (nothing owns
un-setting an operator's choice) **and** invisible (`get_status()` reported a deliberately
enabled bus as `enabled: false`, indistinguishable from someone having switched it off). The fix
separates them: `_enabled` is config, never mutated at runtime; `_publish_breaker` is health.

`EventBus.publish` counts consecutive failures and, on the third, sets `self._enabled = False`
(`event_bus.py:192-198`). **Nothing ever sets it back.** When Redis returns, `publish` hits the
`if not self._enabled: return False` guard at the top and returns without attempting a
connection:

```
publish 1: False  enabled=True   failures=1
publish 2: False  enabled=True   failures=2
publish 3: False  enabled=False  failures=3
Redis recovered → publish: False   (client.publish never called)
```

**Why it matters.** The bus exists so a flow that entered WAIT on instance A can be resumed by
an event arriving on instance B. Once the publisher latches off, that propagation stops for the
life of the process — so in a multi-instance deployment a *transient* Redis blip of three
publishes silently produces exactly the failure the module was built to prevent: flows waiting
on other instances are never resumed. The only cure is a restart, and nothing surfaces the
state except one WARNING at the moment it latches.

**The module docstring is misleading here** — "Subscriber thread crash: caught by the outer
reconnect loop; reconnects with exponential back-off (1 s → 30 s cap)" describes the
*subscriber*. The publisher has no recovery path at all.

**What shipped (2026-08-15).** The kernel already had the right primitive —
`AINDY/kernel/circuit_breaker.py` with CLOSED/OPEN/HALF_OPEN, a recovery timeout and single-probe
half-open — so `publish()` now wraps its one fallible operation in a
`CircuitBreaker(name="event_bus_publish", failure_threshold=3, recovery_timeout_secs=60)` rather
than hand-rolling a cooldown. It reads the clock through `kernel.clock.utcnow` (REPLAY-1), so the
recovery window is testable with `frozen_at` instead of `sleep`.

**Suspension was kept deliberately, not removed.** Dropping the latch outright was the third
option considered and is wrong: with a dead Redis, every `notify_event()` would pay a socket
connect timeout. The requirement was *suspend, then recover* — which is exactly a circuit
breaker. `CircuitOpenError` short-circuits before any connection attempt, so the latency
benefit is unchanged.

**The state is now visible.** `get_status()` gained `publish_suspended`,
`publish_circuit_state` and `publish_retry_after_secs`, and `_get_propagation_mode()` returns
`local-only` while the circuit is open **even if Redis answers a ping** — outbound events are
genuinely not reaching other instances, and reporting `cross-instance` there was part of what
made the old state invisible. Both health consumers (`health_service.check_event_bus`,
`health_router`) read via `.get()`, so the added keys are backward-compatible; `check_event_bus`
passes the whole dict as `metadata`, so `/health/deep` now shows the suspension and its retry
window. **Behaviour change worth knowing:** health reports the bus degraded during suspension
rather than `ok` — correct, since it is not propagating, but it is a change in health output.

**The docstring was fixed too**, since it was part of the trap: its "reconnects with exponential
back-off" bullet describes the *subscriber*, and the publisher's absence of any recovery path
was never stated. It now says so explicitly.

**Tests:** `TestPublisherFailureLatch` → `TestPublisherSuspendAndRecover`. The two tests that
pinned the *bug* (`test_third_consecutive_failure_disables_the_bus`,
`test_recovery_of_redis_does_not_re_enable_the_publisher`) were rewritten, since they asserted
the behaviour being fixed. New coverage: recovery after the window with a healthy Redis; re-open
if still broken at the probe; no recovery before the window; the config flag is never touched;
`publish` still never raises while suspended; plus `TestStatusSurfacesSuspension` for the
operator-visible half.

---

## EVENTBUS-COVERAGE-1 — the pub/sub wire has never been exercised end to end

**Status: CLOSED (2026-08-15).** `tests/integration/test_event_bus_wire.py` — 7 tests driving
`publish() → real Redis pub/sub → a live _subscriber_loop thread → _handle_message →
notify_event`. Found 2026-08-15 while fixing `EVENTBUS-PUBLISH-LATCH-1`, when the
`Integration Tests (PostgreSQL + Redis)` check was about to be read as evidence for a change to
`EventBus.publish()`. It was not evidence: it never ran that code.

**★ The suite was mutation-tested, because a passing test proves nothing on its own.** With
`_publish_payload` mutated to silently drop every message, **5 of the 7 fail**. The first draft
scored 4 — the own-instance-filter test asserts an *absence*, so a broken wire satisfied it
trivially. It now runs a **liveness control** first (a different instance's event must be
delivered to the same subscriber) so the absence assertion can only pass when the channel is
demonstrably working. The two that still pass under mutation are the publisher-contract tests,
which assert `publish()` reports success rather than that delivery occurred — correct, and
deliberately so.

**Placement and marker, per the pitfalls this entry recorded:**

| Decision | Why |
|---|---|
| `tests/integration/` | needs a live Redis; the Integration job provisions `redis:7-alpine` |
| marked `redis`, **not** `integration` | it needs Redis, not Postgres — `pytest.mark.integration` triggers the conftest skip guard when `DATABASE_URL` is not live PG |
| skips only when `REDIS_URL` is **unset** | if the URL *is* configured (always, in CI) a connection failure is a **failure**, not a skip — otherwise the suite could silently stop covering the wire and still look green |

Verified: 7 collected by `pytest -c pytest.integration.ini`, **0** collected by
`pytest tests -m runtime_only` (so it does not slow the unit job), 7 clean skips with no
`REDIS_URL`.

**The two documented race pitfalls were both real and both handled.** Buses in one process share
an `_instance_id` (hostname-derived), so each test sets an explicit distinct id or the
own-instance filter eats every message; and Redis pub/sub has no persistence with no exposed
readiness signal, so a message published before `SUBSCRIBE` lands is simply gone. Rather than
sleep "long enough" first — the fixed-sleep pattern behind `FLAKY-1` — the helper **republishes
inside the polling loop** until the effect is observed or a 10s deadline expires.

**Measured on `main`:**

| | |
|---|---|
| Integration tests calling `EventBus.publish()` or `get_event_bus()` | **0** |
| `notify_event(...)` calls in `tests/integration/` | 4, **all** `broadcast=False` |
| Calls with `broadcast=True` (the production default, `waits.py:79`) | **0** |
| Tests that call `start_subscriber()` anywhere in the repo | **0** |

So three things are untested:

1. **`publish()` against a real Redis.** The unit suite drives it through a `MagicMock` client.
2. **`_subscriber_loop`** — the daemon thread. Nothing in the repo ever starts one; the unit
   suite deliberately drives `_handle_message` directly, which is the same entry point *per
   message* but skips the thread, the `pubsub.listen()` blocking read, and the reconnect
   back-off.
3. **The loop that is the module's entire reason to exist:** publish on instance A → Redis
   pub/sub → subscriber on instance B → `_handle_message` → local `notify_event`.

**What the multi-instance tests actually cover — worth not mis-reading.**
`tests/integration/test_multi_instance_resume.py` *does* test genuine cross-instance resume, but
through **`RedisWaitRegistry`** (shared Redis *state*), not the bus. It patches
`event_bus.get_redis_client` and then calls `notify_event(..., broadcast=False)`, i.e. it
simulates "instance B received the event" rather than propagating one. `broadcast=False` is a
reasonable choice there — it keeps those tests deterministic by avoiding real pub/sub timing —
but the side effect is that the publisher and subscriber have no end-to-end coverage anywhere.

**Why it matters.** `EVENTBUS-PUBLISH-LATCH-1` — a publisher that permanently disabled
cross-instance propagation after three transient failures — survived in `main` until it was
found by *reading* the code. No test could have caught it, because no test runs the publisher.
This is the same family as `DOCS-COVERAGE-CLAIM-1` (suites that never existed) and `CI-MARKER-1`
(suites that never ran): a check whose name implies coverage it does not provide, and which now
**gates every merge** since all ten checks became required.

**The infrastructure already exists, so this is cheap.** The Integration job provisions a live
`redis:7-alpine` service with `REDIS_URL=redis://localhost:6379`, and installs `fakeredis` for
the `multi_instance` marker. A test needs only: two `EventBus` instances with distinct
`_instance_id`s, `start_subscriber()` on B, `publish()` on A, and a bounded wait for B's
`notify_event` to fire.

**Write it carefully** — the pitfalls are known and each has bitten this repo before:

- **Assert on an observable effect, not a sleep.** Poll for the effect with a deadline; a fixed
  `sleep` is the classic flaky-test recipe, and `FLAKY-1` is already one required-check coin
  flip too many.
- **The own-instance filter will silently eat the message** if both buses derive the same
  `_instance_id` — they will, since `_get_instance_id()` reads hostname/pid and
  `TestInstanceIdentity::test_two_buses_in_one_process_share_an_identity` pins exactly that. Set
  distinct ids explicitly or the test passes for the wrong reason.
- **Rehydration gating.** `_handle_message` buffers instead of dispatching when
  `engine.is_rehydrated()` is false, so a test that does not mark rehydration complete will see
  zero dispatches and look like a propagation failure.
- **Do not mark it `pytest.mark.integration`** unless it truly needs Postgres — that marker
  triggers the conftest skip guard when `DATABASE_URL` is not live PG (see the marker hazard in
  CLAUDE.md). This needs Redis, not a database.

Overlaps `ECOGAP-6` (execution-path coverage) and the event-bus half of
`DOCS-COVERAGE-CLAIM-1`, which closed the *unit* half — do not double-track; the increment here
is specifically the wire.

---

## TENANT-FROZEN-SHALLOW-1 — `TenantContext` is frozen but its capability list is not

**Status: CLOSED (2026-08-15).** `capability_scope` is now `tuple[str, ...]`; in-place mutation
raises `AttributeError`. Found 2026-08-14 writing the DOCS-COVERAGE-CLAIM-1 OS-layer suite.

`TenantContext` is a `@dataclass(frozen=True)` documented as an *"Immutable tenant isolation
context"*. Rebinding an attribute raises `FrozenInstanceError` as expected — but
`capability_scope` is a `list`, and `frozen` does not deep-freeze:

```python
ctx = build_tenant_context("t1", ["memory.read"])
ctx.has_capability("admin.everything")      # False
ctx.capability_scope.append("admin.everything")
ctx.has_capability("admin.everything")      # True
ctx.assert_capability("admin.everything")   # passes
```

So a capability can be added to a live security context that the type claims cannot change.
`build_tenant_context` does copy the caller's list (`list(capability_scope or [])`), so this is
not aliasing from the caller — the exposure is any code holding the context afterwards.

**No known exploit path today** — nothing in `AINDY/` mutates `capability_scope` — which is why
this is recorded rather than treated as an incident. It is a weak invariant, not a live breach.

**What shipped (2026-08-15).** The field is `tuple[str, ...] = field(default_factory=tuple)`;
`build_tenant_context` and `tenant_context_from_syscall_context` store `tuple(...)`. Reading is
unchanged — `in`, `len()`, iteration, `has_capability`/`assert_capability` all behave as before;
only mutation differs. Callers may still pass a list (or any iterable): the builders normalise.

`TestFrozenIsShallow` became `TestImmutabilityIsDeep`: the test that asserted the mutation
*succeeded* now asserts it raises **and** that `assert_capability` still refuses the capability
afterwards, plus a direct-construction test covering the dataclass default rather than only the
builder path.

**★ The fix surfaced a second, worse defect — see `KERNEL-INIT-DUPLICATE-1` below.** Editing one
copy of the class would have left another untouched.

**Adjacent inconsistency, still open** (pinned by a test, not a bug per se): `TenantContext`'s
`validate_memory_path` requires the trailing slash and so *rejects* the exact tenant root
`/memory/t1`, while `memory_address_space.validate_tenant_path` *accepts* it. Two tenant guards
give different answers for the same string. Unchanged by this fix.

---

## KERNEL-INIT-DUPLICATE-1 — `AINDY/kernel/__init__.py` was a second copy of `tenant_context.py`

**Status: CLOSED (2026-08-15).** Found while fixing `TENANT-FROZEN-SHALLOW-1`, by grepping for
every consumer of `capability_scope` before changing its type — the results showed the same
code at the same line numbers in two files.

`AINDY/kernel/__init__.py` was a **byte-identical 171-line copy** of
`AINDY/kernel/tenant_context.py`. `git log` shows one commit: `0d5d382 Initial runtime repo
extraction`. It had been that way since the repo began and was never touched.

**Why it is worse than dead weight.** A package `__init__` that *defines* a class means
`from AINDY.kernel import TenantContext` and `from AINDY.kernel.tenant_context import
TenantContext` return **two different class objects**:

- `isinstance(ctx, TenantContext)` silently returns `False` across the two import paths — for a
  class whose whole job is the tenant isolation boundary.
- A fix applied to one does not reach the other. That is not hypothetical: the
  `TENANT-FROZEN-SHALLOW-1` change would have left a second, still-mutable `capability_scope`
  behind, in the copy a package-root import resolves to.

**Why nothing had broken yet.** Nothing imported it. Every `from AINDY.kernel import X` in this
repo *and* in `aindy-apps-monolith` imports a **submodule** (`syscall_registry`, `effect_ledger`,
`event_bus`, `resource_manager`), which resolves the same either way. The duplicate class was
simply never referenced — so this sat latent for the life of the repo.

**Fix:** `__init__.py` is now a real package init that re-exports the five public names from the
single definition, so the package-root path keeps working and resolves to the *same* object.
Emptying the file was the alternative; re-exporting was chosen because it cannot break an
unknown external caller.

**Checked, not assumed:** re-exporting is safe from the `AINDY.routes` shadowing hazard recorded
in CLAUDE.md — that bites when an exported *name* collides with a *submodule* name (`from
AINDY.routes import health_router` yielding an `APIRouter` instead of the module). None of the
five exported names collides with a submodule, and a test asserts `from AINDY.kernel import
tenant_context` still yields the module.

**Pinned by `TestKernelPackageExportsOneClass`:** the two import paths are the same object, an
instance is recognised through both, the package root contains no `class TenantContext` of its
own, and submodule access is not shadowed.

**Swept, and it was the only one.** Since this was found by accident, all 337 `.py` files under
`AINDY/` were hashed and compared: after the fix there are **no byte-identical non-empty
duplicates** anywhere in the package. So the extraction produced exactly one such artefact, and
the concern is closed rather than left as a suspicion.

---

## MAS-FLATTEN-1 — `flatten_tree` drops any node that is a parent of another node

**Status: CLOSED (2026-08-15).** Found 2026-08-14 writing the MAS suite.

`flatten_tree` computes its roots as every path *minus* every path that is some other node's
parent. An intermediate node is therefore never walked as a root, and because it is not a child
of anything in the map either, it vanishes:

```
in : ['/memory/t1/entities/updated', '/memory/t1/entities/updated/n1']
out: ['/memory/t1/entities/updated/n1']        # the parent node is gone
```

**Was not reachable:** `flatten_tree` has **zero callers** under `AINDY/` — re-verified
2026-08-15, and none in `aindy-apps-monolith` either. That is why it was recorded rather than
treated as urgent.

**Fixed rather than deleted**, because `docs/runtime/MEMORY_ADDRESS_SPACE.md` §7 documents it as
usable with a worked example: deleting a documented, exported helper is a larger call than
correcting it, and an out-of-tree caller cannot be ruled out from here.

**The fix:** a root is a node whose *parent is not itself a node* — the inverse of what was
written. `flatten_tree` now also guarantees **every node appears exactly once**: the walk is
visited-guarded (no duplicates), and anything unreachable from a root is appended rather than
dropped. That totality guard matters because `build_tree` only records a `children` entry when
the parent path is itself a node, so a hand-built or partially-populated tree can legitimately
hold unreachable nodes — and silently dropping them is the very failure this entry is about.

The `xfail(strict=True)` test flipped to `XPASS(strict)` on the fix — i.e. it failed the build
until it was converted, which is what strict xfail is for. Replaced with plain assertions plus
coverage for depth-first ordering across three levels, exactly-once, unreachable nodes, multiple
independent roots, and **`len(flatten_tree(tree)) == len(tree)`** — the one-line invariant that
would have caught the original bug.

**Related, and this one *is* live:** `build_tree` — which backs `sys.v1.memory.tree` — never
nests for canonical MAS data. MAS nodes are always 5-segment leaves, so a node's parent path is
never itself a node, the `parent in by_path` branch never fires, and every entry comes back with
`children: []`. The "tree" endpoint returns a flat map by construction. Asserted in
`TestBuildTree::test_real_mas_data_produces_no_nesting` so a change to real nesting is a
visible, deliberate break rather than a surprise.

---

## FR-14 — the blessed deploy primitive breaks on every additive runtime schema release

**Status: PARTIALLY CLOSED (2026-08-16, #450)** — filed by the app team 2026-08-15 from the
2.1.0 upgrade, verified here, and one part of their write-up corrected in the runtime's favour
and against it.

**★ Shipped: two of their three asks.** The refusal itself is unchanged — they explicitly did
not ask for auto-DDL, and none was added.

- **Distinct exit codes**, published in `--help` and pinned by tests as a public contract:
  **3** additive reconcile required, **4** offline migration required, **5** manual repair
  required. `1` stays "fix your environment", `2` stays "import failure" — load-bearing,
  because the whole value of 3/4/5 is that they are *not* 1; sharing a code with a config
  error would make an entrypoint retry a broken environment forever. **Only 3 is safe to
  automate**; 4 and 5 must page someone, which is why they are three codes and not one
  "schema not ready". Precedence puts **4 above 3** when a report says both — reporting 3
  there would invite an entrypoint to auto-reconcile a database that needs a person.
- **The entrypoint pattern documented where they actually looked** — `bootstrap-schema --help`
  now states that a bare invocation under `set -e` is a crash loop in a container, and that
  the bare form is the right *interactive* shape. Their entrypoint was modelled on the `init`
  scaffold and inherited the bare form, so the help text is the right surface for this.
- **Their first ask — "say it in the handoff" — is now a release-checklist step**, gated on a
  `git diff` over `AINDY/db/models/` rather than on someone remembering.

**★ The report always carried the distinction.** `SchemaReport` has `reconcile_supported`,
`offline_migration_required`, `state` and `operator_action`; only the exit surface collapsed
them. Same shape as `IDEM-11`'s `register_syscall`: the information existed, the surface did
not expose it.

**★ CLOSED 2026-08-16 (#455) — the upgrade-path guard is built.** `Upgrade Path Guard` installs
the previous released wheel from PyPI, builds its schema, installs this build over that database,
and requires `bootstrap-schema` to either succeed or exit 3, with `--reconcile` resolving it and
staying stable; it then boots `serve`, since `FR-14`'s symptom was a container that never reached
it. **A `negative-control` job injects synthetic drift and requires the guard to see it** —
without that, a release with no schema change (like the one shipping this) produces a green run
that proves nothing, because a broken guard and a clean release look identical. Not yet a
required check: a new workflow file does not trigger on the PR that adds it, so promote it after
observing a real run. The original analysis follows.

**Their**
own analysis names it: *the upgrade path is never exercised against an existing database.* CI
builds a fresh one, where `create_all` produces the new columns and there is nothing to
reconcile — so **no green check can see this class of failure**; their own
`deploy-bootstrap-guard.yml` passed while the live stack was crash-looping. The missing guard
boots the **previous** runtime against a fresh DB, then the **new** one against that
now-existing DB. That shape would have caught both `FR-8` and `FR-14` before either reached a
running stack. Not built here.

**What happened.** Their entrypoint runs `aindy-runtime bootstrap-schema` bare, under `set -e`.
On 2.1.0 against an existing database it exits non-zero, because FR-13's additive
`agents.metadata` / `agents.updated_at` are absent:

```
error: runtime-owned schema is not ready: Runtime-owned schema requires an explicit additive
reconcile: Runtime table 'agents' is missing required column 'metadata'.
```

`set -e` turns that into an exit and `restart: unless-stopped` turns the exit into a **crash
loop**. Their stack was down until reconciled by hand.

**The refusal is correct.** `bootstrap-schema` deliberately will not alter tables without an
explicit opt-in, which is the right default for a command that may be pointed at production. The
finding is not "the guard is wrong."

**The finding is that the runtime recommends the form that breaks.** `README.md:530` calls
`aindy-runtime bootstrap-schema` the **"Blessed deploy primitive"** and shows it bare in the
deploy snippet; `--reconcile` appears afterwards as an aside — *"Pass `--reconcile` to also apply
additive column/index fixes if the runtime schema is out of date."* So the documented deploy
entrypoint is guaranteed to fail on any release that adds a runtime column, which is exactly what
a MINOR release is allowed to do.

**★ Correction to their write-up, which makes the finding bigger rather than smaller.** Their
option-B rationale reads: *"the runtime already self-migrates its own schema during `serve` — the
real inconsistency is that `bootstrap-schema` is stricter than the command running immediately
after it."* **Verified false.** `startup._enforce_schema_guard` reads
`AINDY_SCHEMA_RECONCILE` (default `false`) and raises `RuntimeError` on
`SCHEMA_STATE_UPGRADE_REQUIRED` exactly as the CLI does. There is no asymmetry to exploit —
removing `bootstrap-schema` from an entrypoint **moves** the failure to `serve`, it does not
remove it.

So the real shape is worse than either write-up stated: **one behaviour, two gates, two different
opt-in mechanisms** — a CLI flag (`--reconcile`) and an environment variable
(`AINDY_SCHEMA_RECONCILE`) — and neither is on by default, while the README recommends the bare
form.

**Also worth recording: this is the second time an "additive, nothing to prepare" claim has been
true about data and false about deployment.** FR-8 was the first — Alembic `0014` grandfathered
pre-existing users, the `alembic/` tree is not in the wheel, so wheel installs never ran it. The
recurring error is treating *data safety* as *deploy safety*. `APP_HANDOFF_v2.1.0.md` made
exactly that conflation and has been corrected.

**Options, none yet chosen (runtime side):**

1. **Document the deploy form honestly** — make `--reconcile` the recommended deploy invocation
   in the README, with the bare form named as the strict/audit variant. Cheapest, and it fixes
   the specific trap.
2. **Unify the two opt-ins** so the CLI flag and `AINDY_SCHEMA_RECONCILE` are one decision rather
   than two, and a deployment cannot satisfy one and be refused by the other.
3. **Emit the remedy in the error**, which the app team already did on their side — the message
   names `--reconcile` but the crash-loop context scrolls it away.

**What the app team decided on their side (recorded so we do not duplicate it):** an opt-in
`AINDY_BOOTSTRAP_RECONCILE` env var on their entrypoint, default off, wired through compose —
convenient behaviour available and non-default. They deliberately kept `restart: unless-stopped`,
on the grounds that unattended reboot recovery is worth more than a faster failure signal.

---

## GUEST-CONFINE-1 — the guest VM runs unconfined; effects on the primary execution path are not mediated

**★ RESIDUAL OPEN 2026-08-17 — the fourth argument was never passed. Read this before citing this
entry as closed.** The demonstrated escape is closed and stays closed; what follows is a
*different* bound that this entry's own fix recommendation named and did not deliver.

Step 1 of the sequencing below reads: *"pass `allow_subprocess=False`, `allow_network=False`,
`allow_env=False` **(and an explicit `allowed_paths`)**"*. Three landed
(`nodus_worker.py:345-347`). The fourth did not, and the comment at `nodus_worker.py:340` records
why it was judged unnecessary — *"the VM already confines filesystem access: `allowed_paths`
defaults to the cwd."*

**That reasoning does not hold, because nothing sets the cwd.** Neither spawn path passes `cwd=`:
`nodus_worker_pool.py:108` (`subprocess.Popen`, warm pool) and `nodus_runtime_adapter.py:278`
(`subprocess.run`, cold path) both **inherit the server process's working directory**. So the
guest's only filesystem bound is an inherited process default the runtime never declares, never
asserts, and no test pins:

| Deployment | Inherited cwd | What the guest can reach |
|---|---|---|
| Docker (`Dockerfile:71`) | `/home/aindy` | `alembic.ini` and `alembic/` — **a guest can rewrite migration scripts that execute on the next boot.** `.env` is mounted at `/etc/aindy/.env:ro`, outside cwd and read-only, so it is *not* exposed here |
| Dev — which is how the runtime is exercised in the current phase | the repo root | the entire source tree, including `AINDY/.env` |

**Severity, stated honestly.** Lower than the original finding: reaching the filesystem at all now
requires the VM's own file builtins rather than `subprocess`, and the Docker blast radius is one
directory. It is filed because the *claim* in the comment is wrong, and because a confinement
whose bound is "whatever directory the operator happened to launch from" is not a confinement
anyone can review.

**Fix.** One kwarg plus a test that pins the bound. It is also the smallest true instance of
`FS-SCOPE-1` — it converts an inherited process default into a declared scope. **Do not close
`FS-SCOPE-1` with it:** one call site is not a vocabulary.

**★★ The same missing `cwd=` governs a second, unrelated thing — and this one loses data
(added 2026-08-18, provenance `CREWAI-NODUS-2026-08-18`).** Setting `cwd=` is now owed twice over,
because the guest's working directory also decides **where its durable run state is written**:

- Nodus's `nodus_lang_workflow` builds `LocalWorkflowStore(root=default_store_root())`, and
  `default_store_root()` (`nodus_lang_workflow/runner.py:994-1007`) returns
  `.nodus/workflow_framework` **relative to the process CWD** unless `NODUS_WORKFLOW_STORE_ROOT`
  overrides it.
- This runtime has **zero references** to `nodus_lang_workflow`, `WorkflowStore`,
  `NODUS_WORKFLOW_STORE_ROOT` or `configure_default_workflow_runner`. Nothing overrides it.
- So the store lands under the inherited CWD — **`/home/aindy` in Docker** (the `Dockerfile:71`
  WORKDIR), the repo root in dev.
- **`/home/aindy` has no volume in `docker-compose.yml`.** The api service mounts only
  `./AINDY/.env:/etc/aindy/.env:ro`. So a guest workflow's **claims, waits, retry schedules and
  rehydration records are container-ephemeral** — durable by construction, discarded on container
  replacement.
- `get_default_workflow_runner()` also **auto-starts a daemon sweep thread** bound to that root
  (`runner.py:1022-1027`), which this runtime does not know exists and never stops.

**★ Nodus's own docstring names the failure this already caused upstream**, which is why it is not
speculative: *"every process that runs a workflow writes into `.nodus/workflow_framework` under
whatever its working directory happens to be — which is how the test suite came to accumulate
hundreds of run files inside the repo and slow its own later runs (#380)."*

**Consequence for sequencing:** one line — `cwd=` on `nodus_worker_pool.py:108` and
`nodus_runtime_adapter.py:278` — closes the filesystem bound above *and* pins the store location.
Do that first, then declare the root explicitly (`NODUS_WORKFLOW_STORE_ROOT` or
`configure_default_workflow_runner(root=…)`). The durable fix is in `ORCHESTRATOR-SPLIT-1`: inject
a runtime-supplied `WorkflowStore` so the state stops living on a container filesystem at all.

---

**Status: CLOSED (2026-08-15)** — for the demonstrated escape; see the residual above. Filed 2026-08-15 from the substrate-boundary audit (F-1),
**independently verified, and upgraded from the audit's own `[Strong inference]` to
demonstrated.** Closed the same day by the three-kwarg fix. The original diagnosis is preserved
below unchanged — it is the audit trail, and the demonstration method is worth keeping.

**What was implemented.** `AINDY/runtime/nodus_worker.py` now constructs the guest VM with
`allow_subprocess=False, allow_network=False, allow_env=False`. nodus substitutes
`_make_blocked_stub` for each denied module, so a guest call returns a structured `SandboxError`
naming the flag rather than failing silently — a guest can distinguish *denied* from *the runtime
broke*. Verified against the real worker entry point (`run_one`), reproducing the original
demonstration: all three operations now refuse and **no host file is created**.

**Deliberately not env-configurable.** A global flag would re-open the boundary for every run at
once, which is the wrong shape; per-execution declaration is `EXEC-ENV-BIND-1`. This was a defect
fix, not a new primitive — the VM already accepted the arguments.

**Blast radius, measured before shipping:** zero first-party `.nd`/`.nodus` scripts in
`aindy-runtime` (8 scripts) or `aindy-apps-monolith` (2 scripts) call `subprocess_*`, `http_*` or
`env_get`. Deny-by-default broke nothing in either repo. A third-party script relying on the old
behaviour will now fail loudly — called out at the top of `CHANGELOG.md` `## Unreleased`.

**Correction to the filed entry — the filesystem half was already confined.** The entry implied
the VM was open on all axes. It was not: `allowed_paths` defaults to `[os.getcwd()]`, so the io
builtins were already path-confined. The demonstrated host-file write went through **subprocess**,
which is not subject to that check at all. So `allow_subprocess=False` is what actually closed the
demonstrated escape, and the filesystem guard was never the gap.

**Regression guard:** `tests/unit/test_guest_confinement.py` — 5 tests, marked `runtime_only`
(verified selected by `Runtime Contracts`; the complement collects nothing). It drives the real
worker rather than asserting on the construction site's source text (`ROUTE-GUARD-1`), and carries
an explicit **liveness control**, because every other assertion in the file asserts an *absence*
and would pass trivially against a VM that was simply broken (`EVENTBUS-COVERAGE-1`).
**Mutation-tested: removing the three arguments fails 4 of 5, and the liveness control still
passes** — the intended shape, since the control is not testing the fix.

**Two findings from writing the tests, kept because they cost time:** `run_one` **stringifies**
the VM's structured error (`nodus_worker.py:463` wraps it in `str(...)`), so the `kind`
discriminator survives only as text and assertions must substring-match rather than read a dict;
and `print()` output does **not** land in `stdout_log` on this path, so it is not a usable liveness
signal — `set_state` + `output_state` is.

**★ Second correction — "zero callers" was true but misleading, and it mis-classified the
failure.** The original finding below says `validate_requested_operation_usage()` "has **zero
callers outside its own module**", which reads as dead code. **Verified 2026-08-15: it runs on
every authorized execution.** Its call site is `nodus_security.py:145`, inside
`authorize_nodus_execution`, which `nodus_execution_service.py:1361` calls. The literal claim is
true only because the direct caller happens to live in the same file. The finding's *conclusion*
stands — it could never have blocked subprocess or http — but for the other reason it gave:
`ALLOWED_OPERATION_CAPABILITIES` contains **exactly 8 entries, all memory operations** (`recall`,
`recall_all`, `recall_from`, `recall_tool`, `record_outcome`, `remember`, `share`, `suggest`) and
nothing for subprocess, network or env. **So this was never "verification that exists but does not
run" (variant 8) — it is "gates, doesn't cover" (variant 5).** Worth getting right, because the
two have different fixes: the first needs wiring, the second needs vocabulary. `validate_nodus_source`'s
docstring — which asserted the VM "enforces its own sandbox (no imports, no filesystem, no
network)" — was corrected in the same change; that false reassurance is the belief that let the
hole survive.

**Remaining gap:** none for the guest boundary. The adjacent, still-open work is the *provider*
re-homing this converges with — `EXEC-ENV-BIND-1`, `TOOL-SEAM-ISOLATION-1`, `EGRESS-INPROC-1` —
where `create_sandbox_runner` reachable only from `plugin_host.py` is the shared root.

---

**Original finding, as filed (retained):**

**The gap.** Every agent run executes as a compiled Nodus workflow inside a `nodus_worker`
subprocess. That subprocess builds the guest VM with
`NodusRuntime(project_root=…)` (`AINDY/runtime/nodus_worker.py:327`) and passes **none** of the
confinement arguments the VM accepts. The VM's defaults are permissive —
`allow_subprocess=True`, `allow_network=True`, `allow_env=True`
(`nodus/runtime/embedding.py`) — and `nodus/builtins/registry.py` registers the real
`std:subprocess` / `std:http` modules **iff** the flag is truthy, substituting
`_make_blocked_stub` otherwise. So a guest script reaches subprocess, network and the host
environment **without passing the syscall dispatcher, the capability token, the effect ledger,
the egress guard or the tool registry.**

**★ Demonstrated, not inferred.** The audit stated it could not attempt this. Driving the
runtime's own `nodus_worker.run_one()`:

| Probe | Result |
|---|---|
| `env_get("PATH")` | returned the **real host PATH** |
| `http_get("http://example.invalid/")` | real DNS resolution (`getaddrinfo failed`) — the live module, not a blocked stub |
| `subprocess_shell("echo escaped > <path>")` | `exit_code: 0`, and **created a file on the host filesystem** |

**Scope of that demonstration, stated precisely:** the worker was driven in-process, so this
establishes that the *guest boundary* is open. It is **not** a demonstration of an
unauthenticated remote path — `POST /platform/nodus/run`
(`routes/platform/nodus_router.py:64`) requires `get_current_user` and is rate-limited
`30/minute`, and it accepts an inline script body validated only by `_validate_nodus_source`.

**Why the source validator does not help — and it is worse than the audit said.**
`validate_nodus_source` (`AINDY/runtime/nodus_security.py`) blocks only Python-isms
(`import X`, `from X import`, `__import__(`, `eval(`, `exec(`). The audit noted that
`RESTRICTED_OPERATION_SUMMARY` — the dict naming `subprocess`, `socket`, `http://` — is never
read by any check; confirmed, it appears only inside a returned summary dict. **Additionally,
and not in the audit:** `validate_requested_operation_usage()`, the function that *does* gate
operations against an allowlist, has **zero callers outside its own module**, and its allowlist
`ALLOWED_OPERATION_CAPABILITIES` contains only memory operations (`recall`, `remember`,
`share`, …) — so it could not have blocked subprocess or http even if it were wired.

**★ This is NOT a finding against the sandbox-escape gate, and the two must not be read against
each other.** That suite certifies the **Tier-2 extension sandbox** — the
`ContainerizedOciSandboxRunner` path reached through `plugin_host.py` — and it passes 17/17 on
every release tag. The Nodus guest VM has never been inside its scope. So *"container-grade certified"* and
*"the guest runs unconfined"* are both true simultaneously; the gap is that one provider is bound to one seam and
not to the others. `SANDBOX_ESCAPE_AUDIT.md` Entry 014 carries the same table from the other
direction, so a reader arriving from either document reaches the same conclusion.

**★ Migration risk is far lower than the audit estimated — measured.** The audit says *"Any
existing `.nd` script that uses `std:http` or `std:subprocess` breaks"* and recommends shipping
default-off and log-only for one release. Searched both repositories: **no first-party Nodus
script uses `subprocess_*`, `http_*`, `env_get` or imports `std:subprocess` / `std:http`.** The
only matches anywhere are nodus's own stdlib module definitions inside a venv. Deny-by-default
breaks nothing that exists today.

**Recommended sequencing — deliberately different from the audit's.** The audit proposes a
per-execution-unit *confinement descriptor* as the primitive. Split it:

1. **Now, as a defect fix, not a new primitive:** pass `allow_subprocess=False`,
   `allow_network=False`, `allow_env=False` (and an explicit `allowed_paths`) at
   `nodus_worker.py:327`. The VM already accepts these; the runtime simply never passed them.
   Three keyword arguments close the demonstrated hole, and the measurement above says nothing
   breaks. An interim single config escape hatch is cheaper than a descriptor if one is needed.
2. **Then the descriptor** (`EXEC-ENV-BIND-1`) — still second, because the three kwargs close a
   demonstrated hole today and need no design, but **not** "when variation earns it". *That
   reasoning was wrong and is corrected in `EXEC-ENV-BIND-1`: the descriptor's value is
   accountability, not variation, and accountability does not wait for a second guest.*

The architectural answer for a script that genuinely needs network is `call_tool`, which is
mediated, not raw `http_get` from a guest. That is the whole point of the boundary.

**Related, same class, not fixed by the above:** the native `action tool "x"` construct lowers
to nodus's built-in `__action_tool` stub with no capability enforcement and, per the runtime's
own comment, "cannot be overridden" (`nodus_worker.py:113–117`). The sibling `std:sys` surface
*was* closed this way (NODUS-SYS-SURFACE-1, by monkeypatching `call_syscall` to raise), so the
precedent for closing this class of gap already exists in the same file.

---

## AUTHORITY-VALUE-1 — CLOSED 2026-08-19, clamp on by default

**The flip, and why the blocker turned out not to exist.**

`AINDY_CHILD_CONTEXT_CLAMP` now defaults **on**: a child context narrows the parent's capability
grant and never widens it. `AINDY_CHILD_CONTEXT_CLAMP=0` restores the permissive behaviour.

The flag shipped opt-in because of a single, well-recorded claim: clamping intersects
`aindy-apps-monolith`'s `_dispatch_owner_syscall` pattern to the **empty set** and therefore
"denies a call that works today." The mechanic is real — `test_the_app_pattern_is_what_makes_this_opt_in`
encoded it as an executable fact, which was exactly the right instinct.

**What was never measured was what the empty set costs.** Measured 2026-08-19 against the
monolith at `feat/adopt-runtime-2.4.1`:

| | |
|---|---|
| Functions calling `_dispatch_owner_syscall` | **19** |
| Of those, **registered** (reachable by the dispatcher) | **1** |
| Unregistered — dead code a clamp cannot break | **18** |

The one live caller is `_handle_agent_suggest_tools` (`sys.v1.agent.suggest_tools`). It widens to
`analytics.read` for an **optional** persisted-suggestions lookup, and the whole nested dispatch
sits inside `try/except Exception` with a `logger.warning` and a **full KPI-based fallback**
beneath it. Denied, it warns and recomputes.

**So "denies a call that works today" described one optional optimisation with a fallback, not a
working feature.** Count: **1 degradation, 0 outages.** The repo's own rule — tighten a boundary
on a count, not an argument — is what moved the default, and it moved it in the direction the
count supports.

**★ The transferable lesson is about the shape of the error, not the flag.** An executable fact
(the intersection is empty) had an inference layered on it (therefore an outage), and the
inference was never re-measured for three months while the fact was cited as though it carried
the conclusion. The test now keeps the fact and explicitly refuses the inference.

**Evidence added rather than argued:** `tests/unit/test_child_context_clamp.py` gains a test that
an operator configuring nothing gets the clamp, a parametrised test that every plausible spelling
of "off" reaches the permissive path, and — the one the original reasoning never checked — that a
starved context makes `dispatch` return an **error envelope** rather than raising, which is the
whole reason the app's `try/except` degrades instead of failing.

**Original entry follows.**

## AUTHORITY-VALUE-1 — the syscall capability check reads a value the calling frame supplied

**★ PARTIAL — `child_context` clamp shipped opt-in 2026-08-16 (#448), and the estimate that
preceded it was wrong.**

`child_context()` fell back to `list(parent.capabilities)` and, when handed an explicit
`capabilities=[...]`, granted it **whatever the parent held**. So it could *widen* authority, not
merely inherit it. `mint_token` already enforces the correct invariant for delegated runs
(`capability_ceiling` — *"a delegate never receives more than the intersection of the parent's
grant and its own registered capabilities"*); this neighbouring path was left conventional.

**It was scoped as "two lines — `mint_token` proves the invariant computable." That was wrong,
and the reason is worth keeping.** A repo-wide grep found no `child_context` caller under
`AINDY/` or `tests/` — only its own docstring — which reads as a zero-caller change. **The app
repo calls it:** `aindy-apps-monolith/apps/automation/syscalls/syscall_handlers.py:45`
(`_dispatch_owner_syscall`) builds a child granting the **nested** syscall's capability, while
`_resolve_dispatch_capabilities` grants the parent **exactly the outer syscall's own
capability** (SDK-SYSCALL-GRANT-1, least privilege). Clamping therefore intersects to the
**empty set** and denies a call that works today. Applying the clamp unconditionally breaks the
app's automation syscalls.

That is the same self-granting shape this entry already documents at `entrypoints.py:83` and
`nodus_execution_service.py:226` — the app is granting itself the capability it needs for a
nested call, because nothing hands it one.

**Shipped as:** clamp behind `AINDY_CHILD_CONTEXT_CLAMP` (default **off**, resolved per call), and
**a WARNING on every widening regardless of the flag**. The warning is the point: the real
exposure has never been counted, and this repo's own history says a boundary should be tightened
on a measurement rather than an argument. Flip the flag after the app-side caller is given a
legitimate grant — not before.

**Guard:** `tests/unit/test_child_context_clamp.py` (10 tests). It pins the default-off choice
*and* encodes the app-caller reason as an executable test, so a future reader who flips the
default gets a failing assertion pointing at the cause rather than an app-side outage.
Mutation-checked: removing the clamp fails 3; defaulting the flag on fails 2.

**Still open in this entry:** everything else — `SyscallContext.capabilities` is still a
caller-constructible list, `_infer_dispatch_capability` still derives a grant from the syscall
name, and the `if not user_id:` paths still skip the boundary rather than deny.


**Status: OPEN — P1.** Filed 2026-08-15 from the substrate-boundary audit (F-2), verified.

**Not a vulnerability, and should not be reported as one.** Every entry point has its own
authorisation — HTTP auth and tenant isolation for routes, an explicit allowlist for MCP,
`_require_runtime_capability` for extensions. The finding is architectural: **the syscall
chokepoint is not an independent second gate**, because the value it checks is supplied by the
frame being checked.

**Verified:**

- `SyscallContext.capabilities` is a `list[str]` on a dataclass any caller may construct
  (`kernel/syscall_registry.py`).
- The check is `if entry.capability not in context.capabilities`
  (`syscall_dispatcher.py:385`) — faithful, but it cannot distinguish a claim derived from a
  signed token from one the calling frame wrote a line earlier.
- Self-granting paths: `capabilities=[capability or _infer_dispatch_capability(name)]`
  (`syscall_dispatcher.py:794`), which derives `{domain}.read|write` from the syscall *name*;
  `capabilities=["flow.run", "flow.execute"]` (`flow_engine/entrypoints.py:83`, literal);
  `capabilities=["nodus.execute", "flow.run"]` (`nodus_execution_service.py:226`, literal).
- `child_context()` falls back to `list(parent.capabilities)` — **inherits, never narrows.**
- The tool chokepoint honours two authority models: `tool_registry.execute_tool` runs
  `check_tool_capability` only `if execution_token is not None`, and
  `extension_worker.py:344` calls it with `run_id=None, execution_token=None` — so the
  extension path performs **no per-tool capability check**, gated only by the coarse
  `CAP_TOOL_INVOKE`.

**Identity-absent branches skip the boundary rather than denying.** `execute_intent`,
`run_flow` and `run_nodus_script_via_flow` each contain `if not user_id:` → call a
`_*_direct()` variant (`entrypoints.py:63`, `:128`; `nodus_execution_service.py:202`). They log
the fact at **debug**, which is what makes the failure mode quiet rather than loud.

**Proposed:** an `ExecutionAuthority` carried on `SyscallContext` in place of the bare list,
verified by MAC rather than read as a field; `child_context` narrowing by default; non-agent
callers issued a runtime-minted context authority at the entry point that already authorises
them. The cryptography already exists in `capability_service`.

**★ A second audit (Hermes, 2026-08-15) found the `child_context` half independently, framed it
better, and it is worth separating from the rest of this entry: the runtime has ALREADY PROVED
the invariant is computable — in the adjacent path.** `mint_token` computes a monotone-decreasing
grant (`capability_service.py:481`: `allowed_capabilities = [c for c in allowed_capabilities if
c in ceiling]`, and it drops tools whose capability falls outside the ceiling too). `child_context`
does not. So this is not "we have not built attenuation" — it is *"we built it, then left the
neighbouring path conventional"*, which is a much cheaper thing to fix and a much weaker thing to
defend. Verified: both sides of the asymmetry are exactly as described.

The fix is two lines and can ship independently of the rest of this entry:

```python
requested = set(capabilities) if capabilities is not None else set(parent.capabilities)
capabilities = sorted(requested & set(parent.capabilities))
```

plus a test asserting no path can widen. **Take this first** — it converts the strongest security
claim from "true in the main path" to "true by construction", and it does not wait on the larger
`ExecutionAuthority` refactor.

**Assessment on sequencing:** agree with the direction, not with doing it first. It is a
moderate refactor (roughly ten `SyscallContext` construction sites, three self-granting paths,
one inference function) with no demonstrated exploit, whereas GUEST-CONFINE-1 has one. Do the
three identity-absent branches as denials at the same time — a missing identity should fail the
call, not skip the boundary.

---

## CANCEL-REACH-1 — cancellation is durable but never reaches an in-flight effect

**Status: OPEN — P1.** Filed 2026-08-15 from the substrate-boundary audit (F-3), verified.

`sys.v1.agent.cancel` (`kernel/syscall_registry.py:1005`) flips a non-terminal run to
`cancelled` via an atomic CAS in a separate session, and the Nodus execution chain observes it
**between segments** (`nodus_execution_service.py:769–782`, whose own comment says "before this
segment's tools run … halts the chain between steps"). A tool already inside `entry["fn"](…)`
— an HTTP call, a long query, a subprocess — runs to completion.

**Verified:** `SyscallContext` carries exactly six fields — `execution_unit_id`, `user_id`,
`capabilities`, `trace_id`, `memory_context`, `metadata`. There is **no cancellation object**,
no signal threaded to `execute_tool`, and no timeout the effect itself can observe.

**Proposed:** a cancellation observation point — `ctx.should_stop()` — checked at the two effect
chokepoints immediately before the handler runs: `execute_tool` before `entry["fn"]`, and
`_dispatch` before `entry.handler`. The same two lines the effect ledger already brackets.
Cooperative, not preemptive, but at *effect* granularity rather than *segment* granularity.

**★ Implementation constraint this repository has already paid for twice, and which the audit
does not state.** `should_stop()` must **not** perform a DB round-trip per effect on the
request-shared session. `RT-MEMTXN-LEAK-1` exhausted the connection pool by holding a
transaction across a slow call on a shared session, and `MEM-RECALL-N1-1` is an N+1 in the same
family. The predicate needs a cached value with a short TTL, refreshed at segment boundaries or
via its own short-lived session — never an unbounded per-effect query, and never a `rollback()`
on a shared session.

**★ A third audit (Codex, 2026-08-15) found this independently and added three things worth
keeping:**

1. **Quota has the same shape.** `check_quota` is consulted at dispatcher entry and at each flow
   node, so a step that blows its wall-time budget mid-call is *reported afterwards*, never
   preempted. A substrate that cannot reclaim an execution slot cannot enforce its own
   concurrency limits — `MAX_CONCURRENT_PER_TENANT` is an accounting convention rather than a
   guarantee unless a slot can be forcibly freed.
2. **The hard-kill primitive already exists one layer over, and is unreachable from a tool call.**
   Verified: `nodus_runtime_adapter.py:283` does `subprocess.run(timeout=…)`, and
   `sandbox_runner.py:963/967` has a `terminate()` → `kill()` ladder. The runtime can hard-kill a
   Nodus worker and a sandboxed plugin; it cannot hard-kill a tool it invoked in-process.
3. **The consequence is concrete:** a hung tool holds a tenant concurrency slot *and* a DB session
   indefinitely. `stuck_run_service` marks the **row** failed after a threshold; the **thread**
   keeps running.

**Its framing is better than a bare predicate:** an `ExecutionSlot` owning a cancellation token
and a deadline, held by the runtime rather than by the executing code, whose `terminate()`
*strength is a function of the isolation class* — in-process degrades to a cooperative flag and
says so; subprocess/container/VM hard-kill. That makes this one design with
`TOOL-SEAM-ISOLATION-1`, and two deliverables. The cooperative `should_stop()` below is the
in-process half; it should be built knowing the other half is coming.

**Assessment: agree, and this is the cheaper of the two proposed primitives.** The runtime ships
a compensating-undo engine precisely because effects are hard to take back; a pre-effect check
is strictly cheaper than compensation. Additive, no migration risk, and it only ever narrows
what a cancelled run does.

---

## DISPATCH-ADMISSION-1 — no pre-effect interception seam at the dispatcher

**Status: OPEN — P2, and deliberately not urgent.** Filed 2026-08-15 from the audit (F-4),
verified: there is no hook, interception or callback anywhere in the body of
`SyscallDispatcher.dispatch`, and `registry.register_event_handler` appends a handler and
returns it — its return value is never consulted, so it cannot veto.

Policy can be *declared* in advance (`CapabilityPolicy`: recipients, domains, rate) and events
can be *observed* afterwards, but an operator cannot interpose a decision at the moment of
dispatch.

**Do not build a general hook system.** An interception seam is a place to run someone else's
code inside the kernel process, and the Tiered Isolation Contract reserves that for Tier 1. A
generic hook surface would quietly widen Tier 1 — which is why the audit itself rates this P2
and says the absorption test only half-passes.

**If built:** exactly one seam — a dispatch admission callback registered like a planner
backend, one per deployment, Tier 1 only, returning allow / deny-with-reason, invoked after the
capability check and before the effect-ledger claim, with denial producing the existing error
envelope and a `capability.policy_denied` event so nothing new appears in the observability
model.

**Assessment: defer.** Risk if omitted is low — operators needing conditional policy today fork
or wrap a tool. The cost is friction, not correctness. Revisit when a deployment actually asks.

---

## ISOLATION-DOC-STATUS-1 — `ISOLATION_MODEL_PLAN.md` contradicts itself about its own status

**Status: OPEN — trivial, doc-only.** Filed 2026-08-15, verified.

`ISOLATION_MODEL_PLAN.md` (repository **root**, not `docs/runtime/`) declares at line 6:

> **Status:** Planning — no implementation has begun

while line 148 of the same file says *"Scope B1 complete: unprivileged kernel-observable
evidence is now collected via `/proc/<pid>/status` …"*, and `sandbox_runner.py`,
`plugin_host.py`, `sandbox_certification.py` and the nine-file escape suite all exist and are
wired. `C2_SANDBOX_AUDIT.md` describes the same code as built.

Source wins; the status line is stale. Note the file is at the repo root, so it is **not**
covered by the `docs/runtime/` frontmatter and `last_verified` checks that `Runtime Docs
Validation` enforces — which is why nothing caught it.

---

## FR-15 — dispatch into the execution pipeline is serialised through a 1s single-instance job

**Status: OPEN — P0 (defect), diagnosed 2026-08-15.** Filed by the app team after a Genesis
session showed a **177-second** gap with zero events; their writeup is
`aindy-apps-monolith/docs/handoffs/DEFECT_GENESIS_MESSAGE_LATENCY.md`. They inferred a
single-slot serialisation from the queueing behaviour and **explicitly declined to claim the
mechanism**. There is one. This entry names it.

**Answer to their question 1: yes — and it is default-on.**

The chain, verified against source and demonstrated:

1. `_scheduler_heartbeat_tick()` (`scheduler_service.py:355`) is the **only** thing that drains
   the scheduler queue. It calls `SchedulerEngine.schedule()`.
2. It is registered as an APScheduler job on `IntervalTrigger(seconds=1)` with
   **`max_instances=1`** (`scheduler_service.py:192-199`).
3. `schedule()` (`kernel/scheduler/dispatch.py:14`) loops up to `MAX_PER_SCHEDULE_CYCLE = 10`
   items and calls `execution_dispatcher.dispatch(...)` for each.
4. `dispatch()` runs `handler_fn()` **synchronously** when the mode is `INLINE`.
5. `_decide_mode()` returns `INLINE` for everything, because **Rule 2 short-circuits Rules 4 and
   5**: `async_heavy_execution_enabled()` reads `AINDY_ASYNC_HEAVY_EXECUTION`, which
   **defaults to false** (`execution_dispatcher.py:94`), is commented out in both `.env` and
   `.env.example`, and is pinned `"false"` in CI.

So **the flow executes inside the 1-second heartbeat tick**, and while it runs no other queued
work can be dispatched at all — `schedule()` is the sole dispatcher and only one instance may
run. That is the single slot. Their `maximum number of running instances reached (1)` log is not
a side-symptom; it is the queue being blocked, printing once per starved second.

**★ The code written to prevent exactly this is unreachable by default.** Rule 4 is
*"high-priority work should never block a request thread"* and Rule 5 routes the heavy types
`{flow, agent, nodus, job}` to ASYNC. Both sit **below** the env-flag check. Demonstrated across
all eight combinations of those four types × `{high, normal}` priority: **every one returns
`INLINE` by default and `ASYNC` with the flag on.** `priority="high"` blocks the thread the
docstring promises it never will.

This is the repo's own recurring shape — **built, and not wired.** Compare `ROUTE-AST-UNWIRED-1`
(a boot-time proof with no call site) and `IDEM-11` (a gate whose declarations were unreachable
from `register_syscall`). Here an entire correctly-designed async path is gated behind a flag
nobody sets.

**Their measurements are consistent with this and nothing else needs to be invoked to explain
them:** wait time = queue depth × per-item duration, so 5.4s / 18.2s / 48.8s / 22.3s / 184.1s is
unbounded and non-monotonic by construction. The app's synchronous
`sys.v1.analytics.execute_infinity` per chat message (which they have already owned) lengthens
each item and therefore amplifies the queue — but is not required for the queue to form.

**Answer to their question 2 — the observability gap is real and separable.**
`execution.started` is emitted by `ExecutionPipeline` once a unit is actually claimed, so
everything before that — the entire time an item sits in `self._queues` — emits nothing. A
queued request and a hung process are externally identical. This is fixable independently of the
dispatch decision and is the cheapest of the three asks.

**Answer to their question 3 — a bound.** There is a per-cycle cap (`MAX_PER_SCHEDULE_CYCLE = 10`)
but **no cap on the duration of any single inline item**, and no queue-depth limit. One slow flow
starves the scheduler for as long as it runs, which is why `/health` timed out at 2.7 cores for
13 minutes: the heartbeat that also drives wait-expiry and stale-wait cleanup never got a turn.

**Not attributable to 2.1.0 — agreed, and stronger than that: this predates it.** The env-flag
short-circuit is not new in 2.1.0. Their caution about load-dependent misattribution is correct
and the mechanism is version-independent.

**Fix options (owner decision, deliberately not taken here).**

- **(a) Flip `AINDY_ASYNC_HEAVY_EXECUTION` on by default.** One line; makes Rules 4/5 live as
  designed. But it changes execution for `flow`/`agent`/`nodus`/`job` from inline to threaded in
  every deployment at once, which is a real behaviour change and wants soak — the standing
  decision puts soak in `aindy-apps-monolith`.
- **(b) Decouple dispatch from the heartbeat.** Give `schedule()` its own worker rather than
  borrowing a `max_instances=1` maintenance job, so a slow item cannot starve wait-expiry and
  cleanup even under INLINE.
- **(c) Emit `execution.queued` + a queue-depth gauge.** Independent of (a)/(b), and the thing
  that turns the next occurrence into a one-line answer instead of a three-hour investigation.

**★ (b) SHIPPED 2026-08-16 (#443).** Wait firing moved to its own `scheduler_wait_tick` job **and its own APScheduler executor** — the job split alone is probabilistic, because `max_instances` is per-job while the pool is shared (16 jobs, default pool of 10, several able to block 60s on `DB_POOL_TIMEOUT`). `schedule()` gained `tick_waits: bool = True`, default preserving historical behaviour. **The real severity was higher than filed: this was a correctness bug** — a flow parked on a timer stayed parked because an unrelated flow was executing, since `tick_time_waits` lived inside `schedule()`. Concurrency is safe by construction (claim-under-lock, fire-after-release), asserted with 8 concurrent tickers over 25 due waits.

**★ (c) SHIPPED 2026-08-15 (#442).** `scheduler.queued` SystemEvent at enqueue (carrying
`queue_depth`) + `aindy_scheduler_queue_wait_seconds` histogram at dispatch. Named
**`scheduler.`, not the requested `execution.`** — the contract gate raises for `execution.*`
outside a pipeline and the hottest enqueue callers have none, so the requested name would have
raised in exactly the paths that matter. Off switch `AINDY_SCHEDULER_QUEUE_EVENTS`. Does **not**
change dispatch behaviour; (b) and (a) remain open.

**Recommended order: (c), then (b), then (a).** (c) is safe and immediately useful; (b) removes
the starvation coupling regardless of which dispatch mode is chosen; (a) is the real fix but is
the one that needs soak, and doing it last means the first occurrence after the flip is
diagnosable because (c) already shipped.

## FR-17 — the async-job path emits `execution.*` from outside a pipeline, so the gate ate it

**Status: FIXED 2026-08-22 (this session).** Filed by the app team 2026-08-16 while verifying
2.3.0 on a live stack, as 🟢 observability: `AINDY/platform_layer/async_job_service.py`'s
submit-time `execution.started` was refused by the execution-contract gate, logged as a
warning and dropped. They were right about the mechanism, right that it is the same
constraint `APP_HANDOFF_v2.2.0.md` §2 used to explain why the new event is `scheduler.queued`,
and right that the cost is a trace timeline with a silent gap where the work started.

**Verified against source before fixing.** `emit_system_event` (`system_event_service.py:449`)
raises for any `execution.*` event when `is_pipeline_active()` is False **and**
`is_async_execution_active()` is False. `_emit_async_system_event` catches everything and
returns `None`, so the row simply never exists.

**Their proposed second option — rename it, as `scheduler.queued` was renamed — is the wrong
one here, and this is the part worth keeping.** That precedent works because nothing keys on
the name. This event is load-bearing: `_ensure_root_execution_event_id` and
`_has_existing_execution_started` locate an async job's trace root **by
`type == execution.started`**, and `AUTO_MEMORY_EVENT_TYPES` keys capture on it. Renaming
would trade a missing row for a broken trace root. So the fix is their first option: the
submit path now declares itself an execution boundary (`async_execution_scope()`), which is
what the gate is asking for.

**★ The half they could not see, and it is bigger than the half they reported.** The same
context is what makes the *worker thread's* `execution.completed` / `execution.failed` land —
and in `_execute_job_inline` it was gated on `_async_job_loop_closure_enabled()`
(`AINDY_ASYNC_JOB_LOOP_CLOSURE`, **default off**). One flag meant two things: *do async jobs
join the Infinity loop* (its actual job) and *may the runtime record that an async job ran at
all*. With the default flag, **every** async job's terminal execution event was discarded, so
async traces started and never ended. `EVENTBUS-PUBLISH-LATCH-1` is the same shape — one field
carrying an operator switch and a runtime latch — and it is why that field was split. The
activation is now unconditional; the flag still gates loop closure, which is all it ever named.

**★ Why this was invisible to CI.** In a unit or integration test the submit almost always
happens under an active pipeline (a route test) or with the loop-closure flag on (the
Infinity suites), so the gate passes and nothing looks wrong. The refused path is the one with
no HTTP request behind it: a scheduler tick, the event-bus subscriber thread, an app
`bootstrap.py`. `tests/unit/test_async_job_execution_contract.py` pins the no-pipeline case
explicitly (`is_pipeline_active` patched False) and drives the real emit, because the whole
defect was a call that looked right being answered by a gate the caller could not see.
Mutation-tested 2/2: removing either fix fails a test.

**Not claimed:** the app team explicitly did not date it or call it a 2.3.0 regression. It is
older than that — the submit-side gate predates the flag, and the worker-side gating has been
there since INFINITY-RUNTIME-1 Gap 5 shipped default-off.

**Watch for, after the flip:** more `execution.*` rows for async jobs than before (that is the
point), and with them more memory-capture attempts, since `EXECUTION_STARTED` is in
`AUTO_MEMORY_EVENT_TYPES`. The RT-MEMTXN-LEAK-1 guards are what bound this — `async_submit_scope`,
the `RUNTIME_INTERNAL_TASK_NAMES` refusal, and the NULL-user dedup fix — and the events at issue
carry `task_name`, so runtime-internal maintenance jobs are still refused at capture.
## FR-20 — the route guard replaced a deliberately raised 4xx with an opaque 500

**Status: FIXED 2026-08-22.** Filed by the app team 2026-08-22 (observed 2026-07-22 during their
frontend walk) as 🟡 diagnostics, and offered with the violation accepted as theirs:
`masterplan_router.py` disagreed with itself about which routes enter the pipeline, and they now
enforce it in their own CI. Their ask was narrow and correct — *preserve the raised status while
still recording the violation*.

**Verified, and the runtime already disagreed with itself.** `_wrap_route_call` converted **every**
endpoint exception into `RouteExecutionViolation` (a 500) when `_is_pipeline_bypass_on_error` was
true. But `classify_execution_failure` has always let a **dependency**-raised `HTTPException`
through with its status (`FAILURE_DEPENDENCY_HTTP_ERROR`, 401 stays 401). So the same exception
type kept its meaning when raised one frame earlier and lost it when raised in the endpoint body.
The app team did not name that asymmetry; it is the strongest argument for their ask.

**★ The fix is in TWO places or it is in neither.** The route guard chooses not to raise, and then
`enforce_execution_contract` (middleware) calls `validate_execution_contract`, which raises on its
own for any classification outside its allowlist. Fixing only the guard moves the 500 one layer
out and looks like the fix failed. A new classification — `FAILURE_ENDPOINT_HTTP_ERROR` — is what
carries the decision across the two layers; a mutation reverting just the middleware half was run
and does put the 500 back, with the test catching it.

**★ The subtle cost, and why the counter is not optional: the 500 WAS the record.** Before this,
the only evidence that a managed route bypassed the pipeline was the status the caller received —
one signal carrying two meanings (the app's contract slip, and the answer to the request).
Preserving the status therefore *had* to create somewhere else for the violation to land, or the
fix would trade a wrong status for a silent one, which is strictly worse and is this file's
recurring shape. Hence `aindy_route_contract_violations_total{route, outcome}` with
`status_preserved` / `converted_500`, plus an ERROR log.

**★ It forced a liveness control to be rewritten, and that rewrite is the part to remember.**
`TestManagedRoutesStillViolate` existed because of `ROUTE-GUARD-1` — *"a fix that simply stopped
raising would pass every assertion above"*. FR-20 is exactly a fix that stops raising, for one
case. The control could not keep asserting the raise, so it moved to the signal that survived: a
managed route and an unmanaged one now both answer 418, and what separates them is whether a
violation was recorded. The alternative — deleting the control, or keeping a wrong status to
satisfy a test — is how enforcement quietly disappears.

**Scope kept deliberately narrow:** only `HTTPException` (the starlette base, so both FastAPI's
and starlette's) is preserved. Any other exception from a managed route is still a
`RouteExecutionViolation` and still a 500, which is right — an unexpected exception is not an
answer, and the guard is the only thing that notices.

## FR-19 — an enveloped and a bare response share one URL space with no discriminator

**Status: RUNTIME HALF FIXED 2026-08-22; the remaining half is the app's.** The app team's own
framing is the reason this is worth reading: it was **the dominant defect class of their entire
live-verification phase** — five defects on five surfaces, ~40 `safeMap prevented crash` lines
inside `@aindy/ui-kit`, 56 references in their walk log — and **it was never raised with us**.
They fixed it eleven times in client code and asked zero times. That ratio is the finding.

**Verified at HEAD.** Only routes that go through `ExecutionPipeline` get the envelope:
`response_adapter.adapt_response` returns the canonical dict (`{status, data, trace_id,
duration_ms, …}`) as the body. Everything else returns a bare body straight from FastAPI. Both
live under the same `/apps/*` URL space, and **nothing on the wire tells them apart** — the only
headers the adapter sets are `X-Trace-ID` and `X-EU-ID`.

**★ `X-Trace-ID` cannot be the discriminator, and this is the trap to avoid:** `log_requests`
middleware sets it on **every** response, enveloped or not. A client branching on its presence
would unwrap everything.

**★ Why the failure is so expensive to debug, in their words and confirmed by the shape:** an
envelope where a list was expected has no `.length`, so the empty-state branch does not fire
either — the surface renders **blank, with no error at all**. And a blanket unwrap is not
available as a workaround, because it corrupts any bare response that legitimately carries a
`data` key.

**Their preference 1 (envelope everything under `/apps/*`) is not ours to do** — whether a route
enters the pipeline is an app decision, and theirs were inconsistent (3 of 11 client modules
unwrapped, 8 did not; same root as FR-20). But they are right that the *consequence* is a
contract question only the runtime can settle: even with their side perfectly consistent, a
client still has to know which routes are enveloped, and there is no way to find out except by
trying.

**Direction settled 2026-08-22: preference 2 — make it detectable.** A response header on every
pipeline-adapted response, documented in `SDK_CONTRACT.md` / `UI_CONTRACT.md`, so the knowledge
lives in one client helper instead of in every module. Additive: no body shape changes, no
existing consumer breaks.

**★ Design constraint carried from `OTEL-GENAI-SEMCONV-1`: a header name is a public surface.**
Additive first, documented before it is depended on, and never renamed casually.

**Shipped: `X-AINDY-Envelope: v1`**, set on the one `adapt_response` exit that returns the
canonical envelope — **not** on the error exit, the handler-built-`Response` exit, or a registered
adapter's exit, because those bodies are not envelopes. A discriminator that over-claims is worse
than none: it makes a client unwrap a plain body. Absence therefore means *not enveloped*, never
*unknown*. Documented in `SDK_CONTRACT.md` and `UI_CONTRACT.md`.

**★★ The find that would have made the whole mechanism useless: NONE of the runtime's response
headers were readable cross-origin.** `CORSMiddleware` had `allow_headers=["*"]` and **no
`expose_headers`** — and `allow_headers` governs the REQUEST direction. A browser exposes only the
CORS safelist to page JavaScript, so the discriminator would have been invisible to precisely the
consumer it exists for (their Vite dev server on `:5173` against `:8000`). **`X-Trace-ID` has been
documented as a debugging aid all along while being unreadable by the browser doing the
debugging.** Now exposed alongside `X-Request-ID`, `X-EU-ID`, `X-API-Version`.

**★ Generalisable: a response header is not a delivered signal until CORS says so.** Anything
added to `_trace_headers` or to a middleware in future needs the same one-line addition, and
nothing enforces it — the test in `test_envelope_discriminator.py` pins the current set only.

**Still open, and it is theirs:** preference 1 — every `/apps/*` route entering the pipeline —
which removes the two shapes rather than labelling them.

## FR-21 — the operator surface exists twice, and the runtime's is missing two panels

**Status: ADOPTED 2026-08-22 — both gap panels shipped; the app team retires theirs.** The app
team offered a handover, not a complaint: they independently grew a second operator SPA
(`client/src/PlatformApp.tsx`, 5,949 lines / 13 components / 12 routes) beside the one the
runtime already serves at `/platform/`, and volunteered to delete theirs once the equivalent
lands here.

**★ Verified, and their framing overstates it by three panels.** They name five as "the clear
runtime ones" — DLQ, flow engine, webhooks, registry, admin promotion. The runtime SPA already
ships `FlowEngineConsole`, `AgentRegistry`, `AdminUsersPanel` and `ExecutionConsole`
(`platform/src/components/platform/`). **The genuine gaps are two: a webhooks panel and a DLQ
panel.** So the adoption is ~380 lines of their code, not 5,949 — check the served bundle before
scoping this, not the panel list.

**Both gaps drive runtime-owned routes**, which is what makes them ours: `POST/GET/DELETE
/platform/webhooks` (full CRUD, `webhooks_router.py`), `GET /observability/dead-letter`,
`GET /observability/dead-letter/{flow_run_id}`, `POST /platform/queue/dlq/drain`. Their own check
of our served bundle found **zero** occurrences of `webhook`, `dlq`, `dead-letter` or `drain` —
so these are not duplicate implementations, they are capabilities our operator surface does not
expose. An operator should not go to an app repo to drain a runtime DLQ.

**★ The ambiguity is the actual defect, and they say so: nobody ever established which surface is
canonical.** Settling that is most of the value; the two panels are the price of settling it in
the direction that matches route ownership.

**What stays theirs:** `RippleTraceViewer` reads an app domain. Not every panel is a runtime
concern, and the split should follow route ownership, not line count.

**Shipped:** `WebhooksPanel.jsx` and `DeadLetterQueuePanel.jsx` in
`platform/src/components/platform/`, routed at `/webhooks` and `/dead-letters`, with nav
entries, admin gates and confirm-gated destructive actions.

**★ Two wrong instruments were tried before the test worked, and both are general traps:**
(1) walking `app.routes` reports `/webhooks`, never `/platform/webhooks`, because FastAPI
>= 0.137 stores an included router as a lazy `_IncludedRouter` — the same trap that made
`HTTP-SCOPE-GAP-1`'s census wrong by 56; (2) probing over HTTP for a non-404 is **vacuous
here**, because `_SPAStaticFiles` is mounted at `/platform` and falls back to `index.html`,
so a typo'd path answers **200 with HTML**. The working instrument is the **OpenAPI schema**,
which carries full prefixes and methods — and it is what a client codes against. Mutation-tested
2/2: a typo'd URL string and a removed nav entry each fail a test.

**★ The DLQ name is ambiguous in this runtime and the panel had to pick:** `/platform/queue/dead-letters`
(async job queue, replayable because the payload is preserved) versus
`/platform/observability/dead-letter` (dead-lettered FLOW RUNS). The adopted panel is the queue
one — the app team's version targeted the same, which is worth noting since the two are one
grep apart.

**Route paths staged in `platform/src/api/_routes.js` as `RUNTIME_ROUTES`, not in ui-kit's
`ROUTES`** — ui-kit is a separate package with its own release train and a panel should not wait
on one. Fold them in on the next ui-kit release and delete the block; `UI_CONTRACT.md` is
authoritative either way.

**Note: a UI change reaches no container until a release is cut and the
Dockerfile pin is bumped** — the SPA ships as package data inside the wheel (see the *Platform UI
— build chain* section of `CLAUDE.md`). Verify against `npm run dev`, and expect the running
container to show the last *released* UI.

## FR-18 — every liveness probe persisted a full health snapshot: 99.6% of one database

**Status: FIXED 2026-08-22 (this session).** Filed by the app team the same day, found while
taking a `pg_dump` before a runtime upgrade — the dump would not finish. On a **local dev stack
with four accounts and no real traffic**: `system_events` at **3653 MB across 183,604 rows**
against a 3795 MB database, of which `health.liveness.completed` was **120,444 rows / 3317 MB**,
3528 MB of it TOAST, `n_dead_tup = 0`. Not bloat, not a missing autovacuum — live intended data.
`pg_dump --exclude-table-data=system_events` produced **17 MB**: the real data was 0.4% of it.

**Mechanism, verified at HEAD.** `health_router._emit_health_event` persisted the **entire
health response** — 26 top-level keys, `trusted_python_execution` alone ~52 kB uncompressed,
plus the deployment contract, the sandbox attestation and the full plugin inventory — on every
successful probe, each opening its own `SessionLocal`. The driver is a container healthcheck,
i.e. **a timer, not traffic**. ★ Their report says "the recommended compose shape, every 15s";
be precise about whose — **ours is the image's own `HEALTHCHECK`, `curl --fail /health` every
30s** (2,880 rows/day); our compose's `api` healthcheck probes `/ready`, which emits nothing;
their compose adds a 15s `/health` probe. Their measured rate over 34 days is ~3,500 rows/day
= **~98 MB/day, ~3 GB/month**, unbounded, with no retention. The distinction does not change
the verdict — it changes who has to act, and the answer is *both*, which is why the fix is in
the runtime and not in a compose file.

**Why it is a defect and not a preference, in one sentence: the content cannot change between
two probes seconds apart.** Sandbox posture, deployment contract and plugin inventory are
boot-time facts, so the same ~28 kB was rewritten thousands of times a day. Their three costs all
hold — it swamps the signal (65% of `system_events` rows were liveness snapshots, in the table
where FR-15 and FR-17 are investigated), it makes backup/restore impractical, and it is a
continuous write load on the one endpoint that must stay up.

**The fix takes their preference 1 AND preference 3, deliberately.** `AINDY/core/health_liveness_signal.py`
persists a **digest** (status, degraded domains, warnings, a posture fingerprint, and the byte
count of the snapshot it did not store) and only **on change**, on the first probe after boot,
or once per `AINDY_HEALTH_LIVENESS_EVENT_INTERVAL_SECONDS` (default 1h).

**★ Why both rather than the cheaper one — this is the reusable part.** They fail differently.
Change-detection depends on the fingerprint excluding every volatile field; the moment a new
health key arrives carrying a counter or a timestamp that `_VOLATILE_LEAF_KEYS` does not know,
every probe reads as *changed* and the rate control silently does nothing. The digest is not
defeated by that — it bounds a worst-case probe to a few hundred bytes instead of 28 kB. So the
failure mode of the rate control is *a smaller improvement*, not a return to 98 MB/day. **The
tell is `aindy_health_liveness_events_total{outcome="suppressed"}` staying flat while probes
flow**; that counter exists so the degradation is visible rather than inferred, which is the
`caplog`-vs-Prometheus lesson from the soak harness applied before the fact.

**★ Fingerprint by allowlist, not by exclusion.** `_POSTURE_KEYS` names the keys that describe
posture; anything else is not seen. An exclusion list has the opposite failure: a new key is
included by default, and if it moves, the rate control breaks silently. This way a new key is
ignored by default, and the cost of missing one is a change that goes unrecorded until the
hourly heartbeat — visible, and recoverable by adding the key.

**★ Found by the route test, and it is an operator-visible property: a COLD process writes
several rows before it settles.** Some posture providers populate lazily (plugin-host probe,
sandbox attestation, runtime conditions), so the first probes of a fresh container each
register a real change. The test's first version asserted one row from a cold start and failed
intermittently — it was measuring cache population, not rate control; it now warms once, resets,
then measures. Two consequences kept: the digest carries **`changed_keys`** (per-key hashes, so
a row says *which* key moved — otherwise "warming up" and "a volatile field is leaking into the
fingerprint" produce identical evidence), and an operator seeing 2–3 liveness rows right after a
restart is looking at correct behaviour.

**Nothing consumes the event.** Checked across both repos before changing the payload shape:
the only references are the emit site, this module, `RUNTIME_BEHAVIOR.md`, and the app's own
`API_CONTRACTS.md` prose. There is no reader to break.

**Escape hatches, because the shape changed:** `AINDY_HEALTH_LIVENESS_EVENT_PAYLOAD=full`
restores the whole-snapshot payload, `AINDY_HEALTH_LIVENESS_EVENTS=0` turns a liveness probe
back into a pure read, and the interval is tunable (`0` = record changes only). All three are
read **per call**, never at import — the standing rule, and FR-10 is why.

**★ What this does NOT do, and the app team named it first: it does not reclaim the 3.5 GB
already written.** The fix is to the write rate; existing rows are an operator action
(`DELETE FROM system_events WHERE type='health.liveness.completed' AND timestamp < now() - interval '7 days'`,
then `VACUUM FULL` or `pg_repack` to return the space, since a plain delete leaves the TOAST
pages allocated). That is in the changelog entry as a pre-upgrade note.

**Still open, deliberately not built here: `system_events` has no retention policy at all.**
Nothing prunes it — `scheduler_service` cleans stale logs and expired `EffectRecord` rows and
nothing else. That is a real gap and it is *not* this fix: the app team's own framing is right,
retention is a mitigation of accumulated volume and the write rate was the defect. Filing it
rather than bolting it on, because a prune job needs a per-type policy (an `execution.*` row is
the audit trail `EVENT-OUTBOX-1` and `AUDIT-CORRELATION-1` depend on; a liveness digest is not),
and choosing that policy under the pressure of a full disk is how a retention job deletes the
thing someone needed. **Tracked as `SYSEVENT-RETENTION-1`.**

**★ A number worth keeping for the next audit of this class: the app measured this on a stack
with no traffic.** Every cost above was paid by the runtime observing itself. When looking for
the next one, rank event types by `pg_total_relation_size` share, not by whether they look
important — the events that dominate a table are the ones emitted by a loop, and a loop's
events are the least likely to be read.

## SYSEVENT-RETENTION-1 — `system_events` grows without bound and nothing prunes it

**Status: OPEN — P2, filed 2026-08-22 out of `FR-18`.** The runtime prunes stale job logs
(`_cleanup_stale_logs`) and expired `EffectRecord` rows (`_cleanup_expired_effect_records`).
It prunes **nothing** from `system_events`, which is the table every execution, every
observability signal and every causal edge lands in. On the stack that produced FR-18 it
reached 3.6 GB in five weeks on a dev box with four accounts.

**FR-18 removed the loudest writer, not the class.** The remaining writers are legitimate and
open-ended: one `execution.*` trio per execution, `autonomy.decision` (25,377 rows on the same
stack), `watchdog.scan.completed` (16,648). Growth is now proportional to work done rather
than to wall-clock, which is the right shape — and still unbounded.

**★ Why this is not "add a cleanup job like the other two", and why it was not bolted onto the
FR-18 fix.** The two existing jobs prune rows whose value is known to expire. A `SystemEvent`'s
value is **not uniform by age — it is uniform by type**, and two entries in this file depend on
that: `EVENT-OUTBOX-1` treats a missing row as evidence the work never happened, and
`AUDIT-CORRELATION-1` joins `EffectRecord.action_id` to `SystemEvent` by convention with no FK,
so a pruned row silently breaks a join nothing enforces. A blanket age policy would quietly
delete the audit trail while leaving the keepalives. **The policy has to be per-type, and it
has to be chosen before the disk is full**, not during.

**Shape when it is built:** a declared retention class per event type (audit / operational /
keepalive), defaulting to *keep* for anything unclassified, so a new event type cannot be
deleted by omission — the same fail-closed default as `assurance_rank()` ranking unknown LOW.
Batch-delete like `cascade_cleanup.prune_cascade_debris`, and log what was dropped by type:
**a silent prune is indistinguishable from a lost write**, which is exactly the confusion
`EVENT-OUTBOX-1` already fights.

**Do not close this by documenting a `DELETE` for an operator to run.** That is the FR-18
mitigation, and it is what every deployment is doing by hand right now.

## IDEM-12 — `agent.undo` re-invokes every compensator when called twice

**Status: OPEN — P2 (latent).** Filed 2026-08-15, found while doing the `IDEM-11` per-syscall
audit rather than reported by any audit.

**The gap.** `undo_run_effects` (`AINDY/core/effect_compensation.py`) selects the run's effects
with `EffectRecord.status == "success"` and, for each, invokes the owning syscall's `compensate`
hook. It **never marks a record as reversed**, and it **never consults `effect_reversals`** to
see whether it has already run. So a second `sys.v1.agent.undo` on the same run re-selects the
identical set and compensates all of it again — a double refund, a second reversing transfer —
and writes a duplicate row to the append-only audit log for each.

**Why it is not live.** **Zero compensators are registered** (verified 2026-08-15 across the
whole tree: `compensate=` appears only in `register_syscall`'s own pass-through). Every effect
therefore reports `irreversible` today, and the only present-day harm is duplicate audit rows.
It becomes live the moment anyone registers the first compensator — which is precisely when
someone is reasoning about correctness of reversal, not about re-entrancy.

Same severity shape as `NATIVE-PARITY-1`: real and reachable by construction, currently
unexploitable because of a property that nothing enforces and that a future change will remove.

**Mitigation already shipped (not a fix).** `sys.v1.agent.undo` now declares
`execution_guarantee="EXACTLY_ONCE"` (`IDEM-11`), so with the idempotency gate engaged a
same-payload retry replays the cached summary instead of re-compensating. That is
defense-in-depth and **does not close this**: the gate is default-off, and it keys on
`(name, payload, scope)`, so it does not protect against a *deliberate* second undo, a
different scope, or the flag being off.

**The fix.** Make reversal re-entrant at its own layer, independent of the gate. Either mark the
`EffectRecord` (a `reversed_at` column, so the `status == "success"` filter naturally excludes
it) or filter the selection against existing `effect_reversals` rows with status `reversed`.
Prefer the second first: it needs **no schema change**, and `effect_reversals` is already
written on every attempt. Note that `irreversible` and `failed` rows must **not** suppress a
retry — only `reversed` should, or a transient compensator failure would become permanent.

**Do not close this by relying on the `IDEM-11` flag flip.** That would make correctness of
reversal depend on an environment variable, which is the shape `IDEM-10` already paid for.

## IDEM-11 — at-most-once is built, tested, and shipped disabled

**Status: OPEN — P0, audit half DONE (2026-08-15).** Filed 2026-08-15 from the Hermes
architectural map (G2), verified. Numbered `IDEM-11` per the registry's own rule rather than
given a new prefix — this is the idempotency programme, continued.

The entry's own framing was *"the per-syscall audit is the real work, not the flag."* **That
audit is now done and shipped; what remains is the flag flip after soak.** The original filing
is preserved below; four corrections to it, all verified against source, come first.

**★ Correction 1 — two of the filed numbers are wrong.** The registry holds **23** entries, not
27 (`SYSCALL_REGISTRY_MIN_COUNT = 23`, and a live count agrees). And the single pre-existing
`EXACTLY_ONCE` declaration was **`sys.v1.memory.write`**, not `sys.v1.memory.delete`.

*This inverts the significance rather than merely fixing a name.* The filing's reading — "flipping
the env flag alone would dedup a single syscall" — implied the flag was near-worthless, since
`memory.delete` has **no callers at all** (recorded under `MEM-DELETE-1`). In fact the one guarded
syscall is the runtime's **busiest write path**, on the memory-capture route that
`RT-MEMTXN-LEAK-1` traced through every request. The flag was never inert; it was narrow and
pointed at the highest-traffic non-idempotent call in the system.

**★ Correction 2 — there is a fourth engagement path the filing omits.** `_durable`
(`_durable_effects_active()`, DUR-2) engages the gate for **any** syscall, declaration-free and
**independent of the master flag**. So a durable continuation already dedups everything. "Three
conditions, the first two default off" describes the steady-state path only.

**★ Correction 3 — `register_syscall` had no `execution_guarantee` parameter at all.** The filed
fix says to make it *required* there. It could not be required, because it did not exist:
`SyscallEntry.__init__` has always accepted it, `register_syscall` never forwarded it. Every
syscall registered through that function — i.e. **every app/plugin syscall**, the FR-5b path —
silently got `AT_LEAST_ONCE` **with no way to opt in**. The gate was unreachable for plugin
syscalls *by construction*, not by configuration. Now forwarded and validated against a
two-value set; a typo'd `"EXACTLY ONCE"` raises rather than silently downgrading, because a
silent downgrade is indistinguishable from never having declared it.

**★ Correction 4 — a hard prerequisite for the flip was missing, and it is the one that would
have bitten.** The gate caches the handler's return in a **JSONB** column. The **tool** path
(MEB-0, `tool_registry.py`) has always `json.dumps`-checked that result and degraded to caching
nothing with a warning. Its **syscall** twin (MEB-1b) had no check and no `try` — so a handler
returning a `UUID`/`datetime`/ORM object raised inside `complete_effect_record`'s commit, unwound
to `dispatch()`'s belt-and-suspenders handler, and returned an **error envelope after the effect
had already happened**: a successful side-effecting syscall reported to the caller as a failure,
and only once the flag was on — i.e. **exactly when someone flips it**. Guard ported; behaviour
now identical to the tool path. Same "one boundary hardened, its twin not" shape as
`NATIVE-DISCOVERY-1` and `EVENTBUS-COVERAGE-1`.

**The audit — all 23 classified, 1 → 7 declared.**

*Non-idempotent, now `EXACTLY_ONCE` (7).* `memory.write` (pre-existing, duplicate node);
`event.emit` (duplicate `SystemEvent` — not cosmetic, `INFINITY-RUNTIME-1` closes its loop on
event counts); `flow.run` (`PersistentFlowRunner.start()` creates a new `FlowRun` every call);
`flow.execute_intent` (re-selects a strategy, starts a second flow); `nodus.execute` (re-executes
arbitrary guest script source — the widest blast radius); `job.submit` (duplicate `AutomationLog`
+ enqueue; return verified JSON-safe, `submit_async_job` is annotated `-> str`); `agent.undo`
(below).

*Idempotent, deliberately left `AT_LEAST_ONCE` (5 writes + 11 reads).* `agent.cancel` (CAS to a
terminal status; terminal is a documented no-op), `agent.ensure_initial_run` (find-or-create by
design), `agent.simulate` (overwrites one field, no status change, no real tools),
`memory.delete` (delete-by-id converges), `agent.execute` (guarded by a `status == "approved"`
precondition — `AGENT-APPROVE-001b` established that entry check as the correct guard). Reads are
never declared: there is no effect to deduplicate and a declaration would put a ledger write on a
hot read path.

**★ Defect found by the audit — `agent.undo` double-compensates.** `undo_run_effects` selects
`EffectRecord`s by `status == "success"` and **never marks them reversed**, nor consults
`effect_reversals`. A second `sys.v1.agent.undo` therefore re-invokes **every** compensator — a
double refund, a second reversing transfer — and writes duplicate audit rows. **LATENT, not live:
zero compensators are registered today** (verified), so every effect currently reports
`irreversible` and the only present-day harm is duplicate audit rows. It goes live the moment
anyone registers one. Same severity shape as `NATIVE-PARITY-1`: real, reachable-by-construction,
not currently exploitable. `EXACTLY_ONCE` is defense-in-depth here, **not the fix** — the durable
fix is for `undo_run_effects` to skip already-reversed effects, tracked as **IDEM-12**.

**★ A complementary boundary we do not have (added 2026-08-19, DBOS `e0b742c`).** DBOS carries
`deduplication_id` on `workflow_status` — idempotency at the **workflow-start** boundary
(*"do not start this run twice"*). `EffectRecord` dedupes at the **effect** boundary (*"do not
perform this effect twice"*). **Different questions; we answer only the second.** A duplicate start
today produces a second run that then correctly dedupes each of its effects — right, but wasteful,
and indistinguishable in the run list from two intended runs. Not filed separately: it is a
one-column addition to this same story, and it should be scoped **with** the flag flip rather than
before it.

**Remaining work: flip the flag.** Default `AINDY_SYSCALL_IDEMPOTENCY` on in production, after
soak. Per the standing decision (`CLAUDE.md` → current phase) that soak happens in
`aindy-apps-monolith`, not here. The declarations shipped in this change are **inert** until the
flip, so they carry no behaviour change on their own.

**Regression guard:** `tests/unit/test_syscall_execution_guarantee.py` — 10 tests,
`runtime_only`. Classification is asserted **per-name, not by count** (a count-only test passes
on the wrong six). The degradation test drives the **real dispatcher** with all four gate
conditions genuinely satisfied, and `test_gate_engages_at_all` is its **liveness control** — if
any condition silently stopped holding, a broken guard would look identical to a guard that was
never reached. Mutation-checked: removing the `json.dumps` check fails exactly the degradation
test, on the assertion that the raw `UUID` reached the JSONB cache.

---

**Original finding, as filed (retained):**

**The effect boundary requires three independent conditions to engage, and the first two both
default off:**

1. `_syscall_idempotency_enabled()` (`syscall_dispatcher.py:163`) returns `False` unless
   `AINDY_SYSCALL_IDEMPOTENCY` is explicitly `1`/`true`/`yes`. Its own docstring says it:
   *"MEB-1b global gate. When off (default), the syscall idempotency gate never fires."*
2. The syscall must declare `execution_guarantee="EXACTLY_ONCE"`, and
   `SyscallEntry.__init__` defaults to `"AT_LEAST_ONCE"` (`syscall_registry.py:193`).
3. The run scope must pass `_gate_scope_engaged` (`syscall_dispatcher.py:478`).

**★ Sharper than the audit stated — measured.** Of **27** registry entries, exactly **one**
syscall declares `EXACTLY_ONCE`: **`sys.v1.memory.delete`** (`syscall_registry.py:1441`). So even
with the environment flag turned on, the gate would deduplicate a single syscall. The mechanism
is not merely default-off; it is almost entirely undeclared.

**Why this matters more than a flag.** MEB/IDEM-10 closed the gate *at the mechanism level* and
recorded the remainder as "soak then flip". This entry is that flip, plus the part the soak
framing hides: flipping the flag alone changes almost nothing, because 26 of 27 syscalls never
declared their semantics. In default configuration the runtime has the same duplicate-effect
exposure as a system with no dedup at all — after paying the full cost of building, testing and
documenting one.

**Proposed — no new abstraction.** Make `execution_guarantee` a **required** argument to
`register_syscall`, so every syscall author states the semantics rather than inheriting a silent
default; default the global flag on in production profiles. The honest concurrency limitation
(degrade-to-at-least-once under a live concurrent pending row, `effect_ledger.py:164`) stays
documented; advisory locking remains a separate, later decision.

**Migration risk is the real work.** Enabling dedup changes behaviour for any syscall that was
accidentally relying on re-execution, so the audit of each syscall's declaration — not the flag —
is the cost. Roll out per domain.

---

## EXEC-ENV-BIND-1 — an execution unit cannot declare the environment it needs

**Status: PHASES 1 AND 2 SHIPPED 2026-08-19 — still OPEN (P1) for phases 3–4.**

**Phase 2 = the guest path asks.** `nodus_worker` derives every confinement argument from an
`ExecutionEnvironmentSpec` clamped to `GUEST_FLOOR` instead of three hardcoded `False` literals,
and passes `allowed_paths` explicitly against a per-execution scratch root. This closes
`GUEST-CONFINE-1`'s residual. **★ Two consequences: `NODUS_ALLOWED_PATHS` is now inert (nodus
reads it only on its unspecified-default branch), and a declared spec is CLAMPED to the floor
rather than merged with it — a guest cannot widen its own sandbox.** **★ It did NOT close
`ORCHESTRATOR-SPLIT-1`'s store 4, against that entry's prediction: bounding `allowed_paths` is
stronger than setting a cwd but leaves the PROCESS cwd untouched.**

**Phase 1 = declare / refuse / record, and it changes no execution path.**
`AINDY/core/execution_environment.py` + three columns on `execution_units` (`env_spec`,
`env_applied`, `env_evidence_class`; Alembic `0017`, schema contract `2026-08-19`) + an optional
`env_spec=` on `require_execution_unit`. **It confines nothing** — a populated `env_applied` is
NOT evidence of confinement; `env_evidence_class` is the field that says whether the environment
was enforced, and on the default dev runner it reads `insecure-dev/no-isolation-guarantee`.

**★ Three implementation facts worth carrying, none of which were in the filed proposal:**

1. **The re-raise guard is load-bearing and its placement is the whole mechanism.**
   `require_execution_unit` ends in a broad `except Exception` that returns `None`, and its three
   callers are documented not to block on that. `except ExecutionEnvironmentError: raise` sits
   *before* it — the `SyscallContractViolation` shape. Mutation-testing that one line red-lines
   five tests. **A refusal swallowed by a broad handler is worse than no refusal**, because the
   recorded row says `refused` while the work ran.
2. **The guard catches the BASE class, not just `Unsatisfiable`.** A malformed spec must also
   propagate: letting it fall through means the work proceeds with no environment binding *and*
   no `ExecutionUnit`, from a caller that was actively trying to declare one.
3. **`assurance_rank()` ranks an unknown class LOW (-1), deliberately.** Ranking it high would
   make a typo — or an upstream rename — satisfy every declared minimum, failing open on the one
   comparison that gates whether work runs at all. The three class-name literals are duplicated
   from `platform_layer` and pinned by test so a rename fails loudly instead of reordering the
   ladder.

**★ And the property that made it safe to ship: the clamp is narrow-only.** The effective spec is
the intersection of declared and host floor, so phase 1 cannot reduce confinement below what the
host already applies. The worst failure mode is an over-strict refusal, which is **loud**, rather
than an under-confined run, which is **silent**. Unlike `AUTHORITY-VALUE-1`'s clamp it is **not
flagged** — no caller supplies a spec today, so there is no compatibility argument, and a security
default that ships off is a pattern this registry keeps recording as a mistake.

**Remaining (phases 2–4):** the guest path asks (also closes `GUEST-CONFINE-1`'s `cwd` residual) →
the tool seam asks (`TOOL-SEAM-ISOLATION-1`, the P0) → the resources axis becomes enforcing and
`COST-GOVERNOR-1` adds spend. Design and phasing table:
`docs/runtime/EXECUTION_ENVIRONMENT_SPEC_DESIGN.md`.

---

**Original filing follows. Status: OPEN — P1.** Filed 2026-08-15 from the Hermes architectural map (G1), verified.
**Closely related to `GUEST-CONFINE-1`, and deliberately filed separately** — see the priority
note below, which is the one place two independent audits disagree.

**The gap, verified:**

- `SandboxRunner` (a 10-method ABC with three implementations and a certification suite) is
  reachable **only** from the Tier-2 plugin-host path. `create_sandbox_runner` and
  `resolve_sandbox_runner_type` appear in exactly two modules — `plugin_host.py` (the extension
  path) and `deployment_contract.py` (policy reporting). **Neither the agent tool path, the flow
  path, nor the Nodus path touches them.**
- `nodus_runtime_adapter.py:278` calls `subprocess.run` directly with its own budget logic. It
  gets *containment* (a separate process, a time budget) but not a *selected, certified
  environment*.
- Agent tool handlers and flow nodes run in-process.
- `ExecutionUnit` has 22 columns and **no** field expressing filesystem, network, CPU, memory,
  secret or assurance constraints. Nor does `SyscallContext`.

So the runtime owns a provider abstraction and a certification ladder, and has no vocabulary in
which an execution unit can *request* anything from it. The provider half of the contract is
built; the requesting half does not exist.

**★ What the three unbound paths each lose, stated precisely** — the distinction matters because
one of them *looks* contained and is not classified:

1. **Agent tool execution** — handlers run in-process (`tool_registry.py`). No containment at all;
   see `TOOL-SEAM-ISOLATION-1`.
2. **Nodus script execution** — `nodus_runtime_adapter.py:278` calls `subprocess.run` directly
   with its own budget logic. **It gets containment (a separate process, a time budget) but not
   runner selection, not assurance classification, and not `_build_child_env` allowlisting.** So
   it is the path most likely to be mistaken for sandboxed while carrying no assurance class at
   all.
3. **Flow node execution** — in-process.

**★ And the scoping argument is the most valuable part of the proposal: ONE new type, not five.**
The audit's own reasoning, verified against the code and worth preserving because it is what
keeps this from becoming a framework:

| Abstraction one might reach for | Why it is not needed |
|---|---|
| `IsolationProvider` | that is `SandboxRunner`, already an ABC with three implementations |
| `ExecutionHost` | that is `sandbox_platform_capability_matrix()`, already per-OS |
| `CapabilityGrant` | that is the capability token, already HMAC-signed and plan-bound |
| `ResourceBudget` | that is `ResourceManager` + quota, already enforced per tenant |

What is missing is **only the request record** — a declarative constraint attached to the
execution unit. Everything it would resolve against exists.

**Worth recording as a strength, since it is the reason this is a binding problem rather than a
design problem:** `sandbox_runner_assurance_posture()` refuses to overclaim — `insecure_dev_subprocess`
reports `ASSURANCE_CEILING_NO_ISOLATION_GUARANTEE`. The provider side already distinguishes a
claim from evidence; the requesting side simply has no way to ask.

**★ CORRECTION 2026-08-15 — the justification recorded here was wrong, on the axis rather than
the conclusion.** This entry originally argued the descriptor should wait until *variation* earned
it: one guest, zero scripts needing different confinement, therefore a type with one
implementation and one consumer is speculative generality. Owner's counter-argument, and it
defeats that reasoning: **the descriptor is not about variation, it is about who owns the residual
risk and whether that ownership is provable.** Accountability needs a *stated requirement*, even
when there is only one.

The runtime's honest-posture defence — *"we never claimed OS isolation for Tier 1, so putting
untrusted code there is the operator's call"* — is sound for **deployed code**, and it is what the
Tiered Isolation Contract already says. But today that posture is **unfalsifiable per execution**:
nothing records what confinement a given unit required, so *"was this the containment you asked
for?"* has no answer for any individual run. The defence is a statement about the system, not
evidence about the execution.

| | Today | With the descriptor |
|---|---|---|
| Who picks the boundary | the runtime, implicitly, by which code path was invoked | the caller, explicitly |
| Can the host refuse | no | yes — the `deployment_contract.py` refusal pattern |
| Per-execution record | none | required vs applied vs evidence class |
| *"You configured it that way"* | an assertion | a **provable** statement |

**★ And it strengthens the posture without the runtime claiming enforcement it does not have.**
The runtime claims **selection and refusal** — both enforceable in-process — while the host keeps
enforcement. That is the split the provider side already honours:
`sandbox_runner_assurance_posture()` refuses to overclaim (`insecure_dev_subprocess` reports
`ASSURANCE_CEILING_NO_ISOLATION_GUARANTEE`). The descriptor gives the *requesting* side the same
property, and turns `assurance_ceiling` from a report into a gate.

**Consequence for priority:** still second to `GUEST-CONFINE-1` — three keyword arguments beat a
schema change when a hole is already demonstrated — but its value is higher than "wait for a
second guest" implied, and it should not be deferred indefinitely on YAGNI grounds. The
accountability argument holds at n=1.

**★ One limit of the honest-posture defence, worth keeping next to it:** it covers deployed code,
not submitted content. A `.nd` script arriving through `POST /platform/nodus/run` is *data* from
an authenticated session, not something the operator placed in a Tier-1 slot — so "you did that"
does not transfer to the guest path. That is the reason `GUEST-CONFINE-1` stays P0 independently
of this entry. The other limit is documentation: `SECURITY_MATRIX.md` and the README describe
scope enforcement the code applies to 7 of 147 routes (`HTTP-SCOPE-GAP-1`), and an operator making
a trust decision on that is misled by the runtime, not by their own configuration.

**Proposed:** one declarative type, not five — `ExecutionEnvironmentSpec` — attached to
`ExecutionUnit` and resolved via the existing `resolve_sandbox_runner_type` /
`sandbox_runner_assurance_posture`.

**★★ CORRECTION 2026-08-19 — this proposal named a resolution point that does not run, and the
repo already knew.** It said "resolved at `execution_gate.gate_and_dispatch`
(`execution_gate.py:294`)". **`gate_and_dispatch` has ZERO callers repo-wide**, is not re-exported
from any `__init__.py`, and `docs/runtime/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md:50` states plainly
that it *"is dead code with no callers."* Two documents, opposite implications, and the one an
implementer would read is this one. Building resolution there is `ROUTE-AST-UNWIRED-1` exactly —
a mechanism that exists and never runs.

**The live seam is `require_execution_unit`** (`execution_gate.py:208`), three call sites:
`core/execution_pipeline/resources.py:12` (every route handler), `routes/flow_router.py:153`,
`runtime/nodus_execution_service.py:1347`.

**★ And its return contract rules out the obvious refusal mechanism:** the docstring says
*"Returns None on failure (non-fatal — callers must not block on this)"*, and all three call sites
are written to that. **A descriptor that refuses by returning `None` would be ignored by design.**
Refusal needs a distinct exception (audited for any broad `except Exception` between raise and
caller, as `SyscallContractViolation` needed) and/or a terminal `refused` EU row. Settle it before
implementing.

**★ A third constraint, which is what makes phase 1 safe:** `gate_and_dispatch` takes
`handler_fn: Callable[[], Any]` — an opaque zero-argument closure — so even alive it could not
*apply* confinement, only select and refuse. That is the split this entry already argues for, and
it means **declare + refuse + record changes no execution path at all**.

**★ Shape warning for whoever implements:** `SandboxRunner` is a long-lived JSON-RPC process ABC
(`start`/`execute`/`probe`/`heartbeat`/`shutdown`/`pid`). `TOOL-SEAM-ISOLATION-1`'s settled answer
is a command **transform** — a different shape. The descriptor must resolve to a **policy**, not
to that ABC, or it silently assumes every seam is a long-lived worker; the tool seam is not.

**★ DESIGN SETTLED 2026-08-19 — `docs/runtime/EXECUTION_ENVIRONMENT_SPEC_DESIGN.md`.** Owner chose
the **three-axis** shape (visibility / authority / resources) over the flat toggle list, and
design-doc-before-code. That doc carries the field list, the storage decision (three real columns,
not `extra`), the phasing, and the open decisions — read it before writing anything here. Invariant: an execution
unit runs only in an environment whose certified assurance class meets or exceeds its declared
minimum; if none is available on this host, the unit does not run — the pattern
`deployment_contract.py` already implements for deployment profiles.

**★★ THREE INDEPENDENT AUDITS CONVERGED ON THIS ROOT, each naming a different boundary — which
is itself the most useful result across all three.** The shared fact is one line:
`create_sandbox_runner` is reachable only from `plugin_host.py`. What differs is which seam each
audit noticed was missing it:

| Audit | Boundary it named | Filed as |
|---|---|---|
| Claude Code | the **guest VM** — compiled agent plans run unconfined | `GUEST-CONFINE-1` (P0, demonstrated) |
| Hermes | the **execution unit** — no vocabulary to request an environment | this entry (P1) |
| Codex | the **tool seam** — `execute_tool` hands a live DB session to in-process code | `TOOL-SEAM-ISOLATION-1` (P0) |

Three reviewers, three starting points, one mechanism attached to the quietest of the four
boundaries it could serve. Treat that convergence as the signal: the fix is not three fixes, it
is one provider re-homed and three call sites taught to ask for it. This entry is the *asking*
half.

**★ Where the audits disagree on priority, and how the evidence resolves it.** Hermes rates this **P1**,
reasoning *"no current escalation path, because the paths that lack environment binding are
Tier-1 trusted by contract."* The earlier substrate audit rates the overlapping guest-VM case
**P0**. **The P0 reading is the correct one for the guest path specifically, and it is settled by
demonstration, not argument:** a guest Nodus script reached `subprocess_shell` and created a file
on the host (see `GUEST-CONFINE-1`). The "Tier-1 trusted by contract" premise does not hold for
guest *script content*, which is data submitted through
`POST /platform/nodus/run`, not first-party code.

The two are not in conflict once separated:

| | Scope | Priority |
|---|---|---|
| `GUEST-CONFINE-1` | The guest VM specifically, where the hole is demonstrated | **P0** — fix with three kwargs |
| `EXEC-ENV-BIND-1` | The general request vocabulary across all execution kinds | **P1** — the primitive, once earned |

That ordering is also the cheaper one: the P0 is three keyword arguments; the P1 is a schema
change plus a resolution path.

### ★ Settle the SHAPE before the field list — orthogonal axes, not a trust level (added 2026-08-18)

Provenance: `LINUX_KERNEL_ARCHITECTURAL_AUDIT.md` §22 Lesson 5 (`C:\codev\Linux research\`).

> namespaces (**visibility**) + cgroups (**resources**) + seccomp (**syscall surface**) + creds
> (**authority**), composed. **Different isolation concerns — what you can *see*, *use*, *do* — are
> independent; composing primitives is more flexible than one rigid "container."** Treat them as
> **separate, independently-configurable axes, not a single trust level.**

**This entry currently proposes `{filesystem, network, subprocess, env}` — a list of *toggles*,
which is nearer a trust level than a set of axes.** Four booleans have sixteen states, most
meaningless, and no way to say *"this run may see a lot and do very little."*

**The decomposition worth adopting instead is visibility / resources / authority**, and the reason
it matters here is that **three separate open entries are each one axis of it and none of them
knows about the others**:

| Axis | Entry | Today |
|---|---|---|
| **Visibility** (what the execution can see) | `FS-SCOPE-1` | no vocabulary at all — verb-shaped capabilities only |
| **Authority** (what it may do) | this entry, `EGRESS-INPROC-1` | `egress_scope` for network; boolean toggles for the guest |
| **Resources** (how much it may use) | `resource_manager` — `MAX_CONCURRENT_PER_TENANT`, EU caps | **exists, and nobody has proposed folding it into the descriptor** |

The third row is the finding. Quota is already a per-execution constraint, resolved at a different
seam, expressed in a different vocabulary, with its own failure mode (`SYSMAX-*` — advisory
per-EU caps). **A descriptor that answers "what environment does this need?" and omits "how much
may it consume?" has picked two axes out of three for no reason other than which audit surfaced
them.**

**★ The decades-tested part of Lesson 5 is the orthogonality, not the axis names.** Linux composes
four independent mechanisms rather than shipping N container presets, and that is why a caller can
express a combination nobody anticipated. A descriptor built as a trust-level enum, or as a flat
bag of booleans, forecloses exactly that. **Decide the axes before the fields** — the field list is
easy to change and the shape is not.

---

## QUEUE-DURABILITY-CLASS-1 — enqueued work can change durability class without the enqueuer knowing

**Status: OPEN — P2, hardening.** Filed 2026-08-15 from the Hermes architectural map (G4),
verified.

`_fallback_to_memory_backend` (`core/distributed_queue.py:418`) swaps a durable Redis queue for
`InMemoryQueueBackend`. `QueueJobPayload` has no field expressing a required durability, so a
caller that enqueued work expecting it to survive a restart gets an identical success response
either way.

**★ The audit omits an existing control, which lowers the severity.** `AINDY_REQUIRE_REDIS`
(`config.py:305`) makes the fallback raise instead of degrade, and the fallback path already
classifies the condition `UNSAFE_DEGRADED` with `production_behavior="startup-fatal"` when
`EXECUTION_MODE == "distributed"` — alongside a metric, a runtime condition and a warning log. So
the degradation is deployment-visible and deployment-preventable; what is missing is only the
*per-job* expression.

**Proposed:** a `min_durability` field on `QueueJobPayload` (default durable) with enqueue
failing rather than silently degrading — mirroring the quota path, which already fails closed in
production.

**Assessment: agree with the audit's own downgrade.** It says explicitly *"I'd fold it into the
ownership contract rather than tracking it separately."* Given `AINDY_REQUIRE_REDIS` already
exists, a per-job field partially duplicates a deployment-level control; do it with
`ORCHESTRATOR-SPLIT-1` or not at all.

---

## ORCHESTRATOR-SPLIT-1 — three durable work stores, three recovery paths, no shared transaction

**Status: OPEN — P2.** Filed 2026-08-15 from the Hermes architectural map (§8, P2-1), verified
by inspection of the three subsystems.

Durable work state lives in three places with three independent recovery mechanisms:

1. **`runtime/flow_engine/`** — `PersistentFlowRunner`, node-granular, `FlowRun.current_node` +
   JSON state, atomic `_claim_waiting_run`, `FlowHistory` per node. The primary runtime
   orchestrator.
2. **`core/distributed_queue` + `worker/`** — durable work items, leases, visibility timeouts,
   DLQ. The dispatch fabric.
3. **Nodus `orchestration/task_graph.py`** — declared `after` dependencies, coroutines, and its
   own fsync'd JSON checkpoints under `.nodus/graphs/`.

No shared transaction spans them, so there is no single authoritative answer to *"what work
remains"* after a crash.

**Proposed, either:** (a) make Nodus graph checkpoints write through a runtime-supplied
effect/state store instead of `.nodus/graphs/*.json` — Nodus already exposes
`runtime.set_effect_store(store)` for exactly this; or (b) publish an explicit ownership contract
stating which engine owns which failure domain.

### ★ There is a FOURTH store, and fix (a) does not reach it (added 2026-08-17)

Provenance: `CREWAI_ON_NODUS_IMPLEMENTATION_STUDY.md` §6 (`C:\codev\Crewai research\`),
re-verified against Nodus at **`v5.0.4-2`** on 2026-08-17.

4. **Nodus `src/nodus_lang_workflow/`** — `models.py` (176) + `runner.py` (1 053) +
   `store.py` (1 103), a **SQLite** `LocalWorkflowStore` behind a `WorkflowStore(ABC)`.

**★ This one is categorically different from store 3 and that is why it was missed.**
`task_graph.py` is a *checkpoint file*. `nodus_lang_workflow` is an **independent
reimplementation of this runtime's entire durability vocabulary** — verified line-exact in source:

| Runtime concept | Nodus equivalent (`store.py`) |
|---|---|
| `_claim_waiting_run` CAS lease | `claim_json` on `WorkflowRunRecord` (`:201`) |
| `register_wait` + expiry | `_register_wait_on_record` (`:283`), `_expire_wait_timeout_on_record` (`:308`) |
| `RetryPolicy` | `_schedule_retry_on_record` (`:235`), `_retry_due_for_record` (`:263`) |
| `flow_run_rehydration` | `_rehydratable_run_records` (`:338`), `_terminal_run_records` (`:344`) |

**★ Fix (a) above is insufficient — verified, do not assume otherwise.** `set_effect_store` exists
at `nodus/runtime/embedding.py:530`, and `nodus_lang_workflow` contains **zero references to it**.
The hook covers store 3 and not store 4. Any ownership contract must name this store explicitly or
it will describe a system with a durability layer it does not mention.

**The concrete failure mode, and it is narrower than "no shared transaction."** A `.nd` workflow
running under `sys.v1.nodus.execute` can be resumed by **either** layer. `continue_crashed_agent_runs`
re-drives a crashed run from the last *fully completed* segment — `_count_completed_segments`
(`core/agent_continuation.py:110-118`) advances only on `total + n <= completed_steps`, so **a
partially-executed segment restarts from its first step.** If the guest independently rehydrates
its own claim/wait/retry state from SQLite while the host re-runs that segment from step one, the
two disagree about what has already happened, and nothing detects it.

**★ Why this is worse than a missing layer, and the reason it stays open rather than being
deferred:** two well-built durability implementations that have never been tested against each
other fail only where they overlap, and the overlap is only reachable by a crash. It is invisible
until it is expensive. It also sharpens the guest seam generally — per `GUEST-CONFINE-1`, the
guest is now confined, but it remains **independently stateful**, which no confinement flag
addresses.

### ★ Store 4 is not merely untracked — it is unconfigured, and fix (a) has a real form

**Added 2026-08-18, provenance `CREWAI-NODUS-2026-08-18`.** Verified against Nodus `v5.0.4-2`.

**The runtime has never had a say in where store 4 writes.** Repo-wide, `AINDY/` contains **zero**
references to `nodus_lang_workflow`, `WorkflowStore`, `NODUS_WORKFLOW_STORE_ROOT` or
`configure_default_workflow_runner`. So a guest workflow gets the default: a SQLite store rooted at
`.nodus/workflow_framework` **relative to the worker process's CWD**, which no spawn path sets —
`/home/aindy` in Docker, a directory with **no volume in compose**. Durable guest run state is
therefore **container-ephemeral**, and a daemon sweep thread is auto-started against it that this
runtime neither knows about nor stops. The full chain is written up under `GUEST-CONFINE-1`'s
residual, because the same missing `cwd=` causes it.

**★ Fix (a) above named the wrong hook. Here is the right one.** `set_effect_store`
(`nodus/runtime/embedding.py:530`) reaches store 3 and **not** store 4 — verified, zero references
to it in `nodus_lang_workflow`. But store 4 is **genuinely injectable**:

```python
# nodus_lang_workflow/store.py
class WorkflowStore(ABC):
    @abstractmethod def get_run(...)   / save_run(...)   / create_run(...)
    @abstractmethod def claim_run(...) / release_claim(...)

# nodus_lang_workflow/runner.py:437
self.store = store or LocalWorkflowStore()
```

**Implement `WorkflowStore` over this runtime's PostgreSQL and inject it, and store 4 collapses
into store 1.** That is bounded work against a small abstract surface, it removes a durability
layer rather than documenting one, and it is the only option on the table that makes the
crash-overlap question moot instead of merely answerable.

**Revised ordering for this entry.** (b) — publish the ownership contract — is still first,
because it is cheap and because a contract that omits store 4 is worse than none. But (a) is no
longer vague: it is *"write a `WorkflowStore` implementation,"* and it should be scoped as such
rather than carried as an aspiration.

**★ Interim, if neither is done soon:** at minimum set `NODUS_WORKFLOW_STORE_ROOT` to a declared,
runtime-owned, volume-backed path. That does not fix the split — two stores still disagree after a
crash — but it stops the losing half of the problem, which is state written somewhere nobody
declared and nothing preserves.

**Assessment: (b) first.** The split may well be correct — three engines with three failure
domains is a defensible design — but it is currently *undocumented*, which means it cannot be
relied on or reviewed. Writing the contract is cheap, and it is the prerequisite for deciding
whether (a) is worth doing. This is also the natural home for `QUEUE-DURABILITY-CLASS-1`.

---

## AUDIT-CORRELATION-1 — three joins the audit trail cannot make

**Status: OPEN — P2.** Filed 2026-08-15 from the Hermes architectural map (§14), verified.

Observability and auditability are otherwise the runtime's strongest properties — a parented
causal event graph, an append-only effect and reversal ledger, execution provenance on the
`ExecutionUnit`. Three correlations are missing, and each weakens after-the-fact reconstruction
rather than correctness:

1. **capability → event.** The authority that admitted a call is not persisted with it. A
   `SYSCALL_EXECUTED` payload records the capability *string*, not the authority that produced
   it. (`AUTHORITY-VALUE-1`'s `ExecutionAuthority` supplies this for free.)
2. **environment → execution.** Sandbox attestation is not joined to the execution unit — which
   is the same missing link `EXEC-ENV-BIND-1` describes from the requesting side.
3. **`EffectRecord.action_id` → `SystemEvent`.** Verified: `EffectRecord` carries a
   `ForeignKey("execution_units.id")` but none to `system_events`, so the join is by `trace_id`
   convention rather than by constraint.

**Assessment:** low urgency, but worth keeping on the list precisely because reconstruction is
the property this runtime is strongest at — the gaps are small enough to be worth closing before
they are load-bearing. (1) and (2) are byproducts of the two entries above; only (3) is
standalone.

---

## TOOL-SEAM-ISOLATION-1 — every authority check at the tool seam is advisory with respect to the code that runs next

**Status: OPEN — P0. SCOPED 2026-08-19 → `docs/runtime/TOOL_SEAM_ISOLATION_SCOPE.md`.**
Filed 2026-08-15 from the Codex comparative audit (G1), verified.
Third of three convergent isolation findings — see `EXEC-ENV-BIND-1` for the convergence table.

**★★ READ THE SCOPE DOC BEFORE ACTING ON ANYTHING BELOW.** Measuring this entry against source at
`03d5a87` produced four corrections, and two of them change what should be built:

1. **A tool is a Python CALLABLE, not a command.** The "settled" command-transform answer recorded
   below is one level of indirection off — there is no argv at `tool_registry.py:366`. The borrow
   still holds, but the thing transformed is *a tool worker's* argv, which means a
   **serialization boundary** (args + result + errors across a process) is the real cost and is
   not mentioned anywhere below.
2. **The three call sites are in three different processes.** `extension_worker.py:345` runs
   *inside* the Tier-2 sandbox (it is what `SandboxRunner` spawns); `nodus_worker.py:144` is in
   the Nodus worker subprocess; only `nodus_adapter.py:263` is host-side. "In the runtime process"
   is true of one of three.
3. **No foreign in-process code runs here at HEAD.** 3 runtime-owned tools, 15 app fns (thin
   syscall adapters), and MCP tools are **runtime-owned proxies** — the foreign code is remote.
   The gap is *structural* (the runtime cannot bound what a consumer registers), not live.
4. **All 18 tool fns take `db`; NONE uses it.** Measured across this repo and
   `aindy-apps-monolith`. The Lesson-10 revocable-handle step is therefore a measured no-op —
   the same evidence `GUEST-CONFINE-1` gathered before denying its three capabilities.

**Recommendation from the scope: ship the handle (A), declare `isolation=` (B), do NOT build the
process boundary (C) without a named consumer.** C becomes urgent the moment a consumer registers
a tool that *executes what it is given* — a shell-out, an eval, a plugin loader.

**★ Priority re-confirmed and the reason sharpened 2026-08-17** (Aider portability accuracy pass,
`AIDER-PORTABILITY-2026-08-17`). Already P0; it should not be re-levelled down, and the argument
for it is now stronger than when it was filed:

- **It is the last one.** With `GUEST-CONFINE-1` closed 2026-08-15, `agents/tool_registry.py:366`
  is the **only remaining seam in the runtime where foreign code executes unconfined** with the
  process's ambient authority and the live DB session. The convergence table listed three seams;
  two are answered. The provider is still bound to `plugin_host.py` alone — verified at HEAD:
  the only *execution* call sites of `create_sandbox_runner` are `plugin_host.py:346` and `:816`;
  every other reference reads `.metadata()`.
- **Independent corroboration, from a system that has nothing to do with this repo.** The Aider
  portability analysis proposes a minimum viable experiment — route exactly two call sites
  through the runtime — and attaches exactly **one** safety precondition: *do not route the
  agent's shell-out through the runtime until the isolation provider is wired at the tool seam*,
  on the grounds that **"a gated path that does not actually confine would be worse than the
  status quo it replaces."** An unsandboxed `run_cmd` is at least honest about being unsandboxed.
  That precondition now points solely here, and it is the argument for why this blocks adoption
  by any consumer that mutates external state.
- **It is the enforcement point two other filed items need.** `FS-SCOPE-1` (new, P1) has no
  enforcement point without it, and `EXEC-ENV-BIND-1` (P1) is the vocabulary both would consume.
  One structural change serves three entries — cost this once, not three times.

**The gap.** `execute_tool()` performs a full authority evaluation — token validity, granted
tools, required capabilities, capability policy, rate limits, egress domains, secret scope — and
then calls, verified at `agents/tool_registry.py:366`:

```python
with _egress_cm, capability_scope(_scoped_caps):
    result = entry["fn"](args=args, user_id=user_id, db=db)
```

**In the runtime process, handing the tool the live database session.** Every preceding check is
advisory with respect to what runs next: the tool can `import os`, read `os.environ`, open
sockets on a fresh `threading.Thread` (which `egress_guard`'s own docstring names as an escape),
or issue arbitrary SQL through the session it was given.

**The provider that should be on the other side already exists** (`sandbox_runner.py`) and is
reached only from `plugin_host.py`.

**★ This is NOT a finding against the sandbox-escape gate, and the two must not be read against
each other.** That suite certifies the **Tier-2 extension sandbox** — the
`ContainerizedOciSandboxRunner` path reached through `plugin_host.py` — and it passes 17/17 on
every release tag. The in-process tool seam has never been inside its scope. So *"container-grade certified"* and
*"every authority check at the tool seam is advisory"* are both true simultaneously; the gap is that one provider is bound to one seam and
not to the others. `SANDBOX_ESCAPE_AUDIT.md` Entry 014 carries the same table from the other
direction, so a reader arriving from either document reaches the same conclusion.

**Why it belongs at runtime level.** Every application mounting a tool would otherwise re-solve
containment itself, and the boundary must sit *below* the tool to mean anything. The audit's
Codex citation is the crisp statement of the principle: *"`CODEX_SANDBOX*` env vars are hints,
not controls."* The runtime currently has hints at the tool seam.

**Proposed primitive.** Promote `SandboxRunner` from a plugin-host detail to a first-class
isolation provider available at *any* effect seam, and let a registration declare what it needs:

```python
register_tool(..., isolation="in_process" | "subprocess" | "container" | "strong_vm")
```

`execute_tool` resolves the provider from the declaration and the deployment profile's
`available_runner_types`, and **fails closed** when a tool demands containment the host cannot
supply. **Default stays `in_process`, so nothing changes until opted in** — the same discipline
every MEB/DUR flag in this repository already follows.

### ★ How the provider attaches — a transform, not an ABC (added 2026-08-17)

Provenance: `workload-sandbox-provider-reference.md` in `C:\codev\Claude Code research\docs\`,
read against `codex-rs`'s ~72,000-line isolation stack. It corrects an earlier recommendation in
that same series and the correction is right. **This resolves the main open design question in
this entry — `isolation=` is the *declaration*; the following is the *application mechanism*, and
they compose.**

The instinct is to model isolation as **polymorphic execution**: an ABC with `run`/`popen`/
`upload`/`download` and one implementation per backend. `codex-rs` does the opposite and it is
better for us:

> `SandboxManager` is a **command transformer, not an executor.** It takes a command plus a policy
> and returns a command that carries its own sandbox wrapper — `sandbox-exec` with a generated
> SBPL profile on macOS, a `bwrap` invocation on Linux, a restricted-token launcher on Windows.
> **One spawn path; the isolation rides in argv.**

**★ Why this fits this runtime specifically, verified at HEAD.** There is exactly **one**
execution chokepoint — `agents/tool_registry.py:366`, `entry["fn"](args=args, user_id=user_id,
db=db)` — reached from exactly **three** call sites (`extension_worker.py:345`,
`nodus_adapter.py:263`, `nodus_worker.py:135`). A transform slots in immediately before that call
and needs **no backend implementations at all**. An ABC would require the runtime to grow N
execution environments it does not have and does not want.

**★ The reference request struct is also the answer to `FS-SCOPE-1`'s vocabulary question.**
`SandboxExecRequest` carries, as peer fields:

```rust
pub sandbox: SandboxType,                     // None | MacosSeatbelt | LinuxSeccomp | WindowsRestrictedToken
pub file_system_sandbox_policy: FileSystemSandboxPolicy,
pub network_sandbox_policy: NetworkSandboxPolicy,
pub permission_profile: PermissionProfile,
```

Filesystem policy and network policy sit **side by side**, which is exactly the resource-scoped
pairing `FS-SCOPE-1` argues for and exactly what this runtime lacks (`egress_scope` exists, no
path equivalent). It ships default-on across three operating systems, so the shape is proven
rather than proposed. Note also `SandboxablePreference { Auto, Require, Forbid }` — the
fails-closed vocabulary this entry already wants, named.

**What not to take.** The 72,000 lines. Three runners already exist here and the honest per-platform
limits are documented; this is a *seam* borrow, not an implementation borrow.

**★ The missing request *shape*, from the third direction (added 2026-08-17).** The Codex
portability analysis proposes `EphemeralIsolationRequest` (its N4) — *a per-invocation,
policy-parameterised jail as a second **shape** behind the existing `SandboxRunner` interface.*
That is the piece between the declaration and the transform, and it is why the three fit together
rather than compete:

| Layer | What it is | Source |
|---|---|---|
| `register_tool(..., isolation=…)` | the **declaration** — what containment this tool requires | this entry |
| `EphemeralIsolationRequest` | the **request** — one invocation's resolved policy, built from the declaration + the deployment profile's `available_runner_types` | Codex N4 |
| command transform at `tool_registry.py:366` | the **application** — argv rewritten to carry its own wrapper | `codex-rs` |

**★ N4's own caveat is the load-bearing one and must survive into any build: absorb the shape,
never the mechanism.** The runtime owns the request type and its policy vocabulary. Seatbelt,
Landlock, seccomp and OCI stay behind the provider boundary, exactly as `SandboxRunner` already
establishes. A runtime that grows platform-specific confinement code has moved the boundary in the
wrong direction.

**Existing runners already satisfy the long-lived shape**; what is absent is the *per-invocation*
one. That is the whole delta — not a fourth runner.

### ★ A cheaper first step than the provider: stop passing a pointer (added 2026-08-18)

Provenance: `LINUX_KERNEL_ARCHITECTURAL_AUDIT.md` §22 Lesson 10 (`C:\codev\Linux research\`), a
reframing no other document in the corpus used.

> `fd → struct file`; userspace holds an **integer index into a kernel table, never a pointer**.
> **Principle:** hand out **opaque handles, not direct references**, across a trust boundary;
> resolve them through a controlled table you can **validate, revoke, and redirect**.

**We get this half-right and then break it in the same function.** `execute_tool` resolves the tool
by *name* through `TOOL_REGISTRY` — handle-shaped, correct — and then calls, at
`agents/tool_registry.py:366`:

```python
result = entry["fn"](args=args, user_id=user_id, db=db)
```

**`db` is a live SQLAlchemy session — a direct object reference handed across the trust boundary.**
It cannot be validated, cannot be revoked mid-call, and cannot be redirected to a narrower view.
Every authority decision made before that line is advisory with respect to what the tool does with
that one argument.

**★ Why this is worth separating from the isolation work: a revocable handle is an improvement
*even without* a sandbox.** Replacing the raw session with a scoped, capability-checked accessor —
one that can be closed at return, refuses writes outside the tool's declared scope, and is
resolvable through a table the runtime controls — narrows the seam without needing a provider, a
transform, or a deployment-profile negotiation. It is the smallest thing on this entry that is
independently shippable, and it composes with everything above rather than competing with it.

**Do not treat it as a substitute for the provider.** A tool that holds a scoped session can still
`import os`, spawn a thread, or open a socket — the ambient-authority problem is untouched. This
narrows one argument; the provider bounds the process.

**Invariant gained.** A tool's effective authority is bounded by its declared isolation class,
not by the runtime process's ambient authority.

**Risk if omitted.** The capability token's guarantee is only as strong as the honesty of the
tool it authorises. `AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS` is documented as trusted-code-only
precisely because of this — which is the runtime already conceding the point in configuration.

**Relationship to the other two:** same provider, different seam.
`GUEST-CONFINE-1` (P0) is closable with three kwargs today and should not wait for this;
`EXEC-ENV-BIND-1` (P1) is the general vocabulary this and the guest case would both consume.

---

## CAPABILITY-PROVIDER-TIMEOUT-1 — the capability set silently empties when a subprocess is slow

**Status: FIXED 2026-08-16 (#466) — and DOWNGRADED P1 → P2 by measurement.** Found 2026-08-16
while regression-testing `KEY-SCOPE-ESCALATION-1`.

**★ SECOND CORRECTION: the P1 severity was also wrong, and in the direction that matters.** The
entry argued that an empty capability vocabulary would leave *"every consumer degrading quietly"*
— the implication being that capability checks become vacuous. **They do not. It fails closed.**
`check_tool_capability` refuses a tool whose mapping is missing:

```python
if not required_capabilities and tool_name in TOOL_REGISTRY:
    return {"ok": False, "error": f"tool '{tool_name}' has no registered capability mapping"}
```

Demonstrated by running it, not by reading it: with a healthy registry the check denies with
*"capability 'read_memory' not granted"*; with a failed provider it denies with *"no registered
capability mapping"*. So the real cost is **availability** — tool execution is refused, naming a
symptom that says nothing about the cause — not a security hole. Note the guard is *conditional*
on `tool_name in TOOL_REGISTRY`, which is why it is now pinned by a test rather than trusted.

**★ CORRECTION — this entry was first filed the same day as `TEST-ISOLATION-REGISTRY-1`, a
test-isolation problem. That diagnosis was WRONG.** The symptom was
`test_platform_only_registers_runtime_agent_defaults` failing with
`registry.get_capability_definitions()` empty under some selections and passing under others,
which reads exactly like registry state leaking across app boots. It is not. The log line the
first pass did not go looking for says what actually happens:

```
WARNING AINDY.platform_layer.registry Capability definition provider failed:
runtime callback command timed out after 30s
(set AINDY_RUNTIME_CALLBACK_TIMEOUT_SECS to raise the budget)
```

**The mechanism.** `capability_definition_provider` is **not** in
`_STATEFUL_IN_PROCESS_CALLBACK_SURFACES`, so `register_capability_definition_provider` routes it
through `_maybe_wrap_runtime_callback` → a **subprocess**. `_load_capability_definition_providers`
then calls each provider inside

```python
try:
    _apply_capability_provider_bundle(provider())
except Exception as exc:
    logger.warning("Capability definition provider failed: %s", exc)
```

Under load the subprocess exceeds its 30s budget, the timeout is caught, and
`get_capability_definitions()` returns `{}` — **with no error reaching the caller**. This is the
silent-degradation shape the `_maybe_wrap_runtime_callback` section of `CLAUDE.md` warns about,
in a surface that section does not list.

**Why P1 rather than a flaky-test annoyance.** Capability definitions gate what tools and agents
are allowed to do. A contended or slow host therefore produces an **empty capability vocabulary**
at runtime, and every consumer of it degrades quietly rather than failing. Nothing distinguishes
*"this deployment defines no capabilities"* from *"the process that defines them timed out"*. The
test was only the messenger, and it is the sole reason this was seen at all.

**Fixed without touching the budget or the harness, and without moving the isolation boundary.**

**★ The measurement that reframed it: this is on the tool-execution path.**
`_load_capability_definition_providers` is reached from `get_capability_definitions`,
`get_capability_definition`, `get_capabilities_for_tool` and `get_capabilities_for_agent`, hence
from `check_tool_capability` in `tool_registry.py`. So **every tool capability check spawned a
subprocess per provider** and waited on a 30s budget. The timeout was not bad luck; it was a
hot-path design defect with a 30-second fuse.

**Measured, 10 `get_capabilities_for_tool` lookups on an idle machine: 10 subprocess
invocations / 56.4s before, 1 / 11.4s after.** ~5.6s of subprocess per tool capability check,
paid on every tool call and scaling linearly with the number of checks.

1. **Cache each provider's bundle**, so a provider is called once rather than per check. The
   bundle is still *applied* on every call, so clearing the definition dicts (which the test
   fixtures do) repopulates correctly.
2. **Never cache a failure** — a transient timeout is retried on the next call instead of
   persisting for the process lifetime.
3. **Log at ERROR, not WARNING**, naming what the failure costs (*"tool execution will be
   refused"*), because the downstream error names the tool and not the cause.

**★ The cache lives on the provider object, not in a module global.** A
`_capability_providers_loaded` latch would have to be added by hand to **two** separate
registry-reset dictionaries (`tests/fixtures/client.py` and `test_platform_only_startup.py`), and
forgetting either leaves a stale `True` that empties the capability set permanently — which is
this bug again, introduced by its own fix. `_capability_definition_providers` is already in both
dictionaries, so a cache attached to the objects inside it is invalidated by every reset for free.

**Rejected: adding this surface to `_STATEFUL_IN_PROCESS_CALLBACK_SURFACES`.** That set's stated
rationale is *"reads live in-process state a bare subprocess cannot reconstruct"*, and
`runtime_capability_bundle` returns a **literal dict** — it does not qualify. Moving it there
would weaken a documented boundary for a performance reason, which is the inverse of the shim
rule (*grow the shim to match the guard, never weaken the guard*). The cache gets the same
result without touching the contract.

**Residual, stated rather than hidden:** the *first* capability lookup in a process still pays
one subprocess spawn per provider, so a sufficiently contended host can still fail it once. The
cache turns "every check, forever" into "once, retried on failure"; it does not make the budget
irrelevant. If it recurs, the budget is now the honest next lever.

**What made it fire.** Both observed failures — one full `tests/unit` run, one `-k` subset —
happened while a *second* heavy pytest run was competing for CPU on the same machine. Two isolated re-runs of the identical selection afterwards were
clean, with zero timeout warnings. So the trigger is **CPU contention starving the subprocess**,
not test order and not any particular test file. A re-run on a quiet machine will usually pass,
which is precisely what makes it easy to dismiss as noise.

**Diagnostic that settles it in one line:** `grep -c "Capability definition provider failed"` on
the run output. Non-zero means this; zero means look elsewhere. The assertion failure itself
(`get_capability_definitions()` empty) says nothing about the cause, which is how the first
diagnosis went wrong.

*(Kept under the old name in one place: `CLAUDE.md`'s registry line was written as
`TEST-ISOLATION-REGISTRY-1` before the cause was known, and is corrected in place.)*

---

## KEY-SCOPE-ESCALATION-1 — an API key can mint itself a wider API key

**Status: OPEN — P0 (security). Found 2026-08-16 while inventorying the routes left ungated by
`HTTP-SCOPE-GAP-1` D.** Demonstrated end to end against real PostgreSQL, not inferred.

**The chain, as run.** Starting from an API key holding the single scope `flow.read`:

| Step | Call | Result |
|---|---|---|
| 1 | `POST /platform/keys` `{"scopes": ["platform.admin","memory.delete","event.emit"]}` | **201** — key issued with exactly those scopes |
| 2 | `GET /platform/admin/users` with the new key | **200** — every user's email and admin flag |
| 3 | `POST /platform/admin/users/{own_id}/promote` | **200**, `is_admin: true` |

Step 3 is the one that makes this permanent: the escalation lands in the **user row**, so every
future JWT session for that account is an admin session too. Revoking the minted key does not
undo it.

**Root cause — one function, and its own docstring names the missing half.**

```python
def require_platform_admin_access(current_user = Depends(get_current_user)):
    """Allow any authenticated API key; require is_admin for JWT users.

    Used on the /platform router where API keys are pre-authorized at the
    platform level (scope enforcement happens per-endpoint or per-syscall).
    """
    if current_user.get("auth_type") == "api_key":
        return current_user          # <-- any key, any scopes, all 56 /platform routes
```

`keys_router.py` has **no per-endpoint scope check**, and neither does most of the tree. The
docstring describes a two-part design whose second part was never built — the same shape as
`ROUTE-AST-UNWIRED-1`, where the defect was the *claim* rather than the absence. The only
validation on the requested scopes is membership in `Scopes.ALL`, i.e. *"is this a real scope"*,
never *"may you grant it"*.

**Scope of exposure — narrower than it first reads, and still serious.** The `/platform` tree
requires `is_admin` for JWT callers, so an ordinary *session* cannot reach `POST /platform/keys`
at all (verified: 403). The escalation is exclusively **API key → wider API key**. That does not
soften it much: least privilege is the entire purpose of key scopes, so *any* leaked or
misplaced low-scope key is equivalent to a full platform-admin key, and the two scopes a session
is deliberately denied — `memory.delete` and `event.emit` — are both obtainable this way.

**Two guards disagree about what an API key is**, which is how this survived review:
`require_admin_principal` requires `platform.admin` on a key, while `require_platform_admin_access`
requires nothing of one. Both are reachable on the same tree.

**Fix, in order:**

1. **Delegation rule on key creation. DONE 2026-08-16 (#463).** A caller may only mint a key whose
   scopes are a **subset of its own authority** — an API key's own `api_key_scopes`, a session's
   `derive_session_scopes(is_admin)`. This closes escalation without changing what any existing
   key can already *do*; it only stops keys granting themselves more.
2. **The `/platform` tree. DONE 2026-08-16 (#465) — and it was worse than this entry said.**
   Probing what a `flow.read`-only key actually *reached* found **46 of 53** routes open,
   including `POST /platform/queue/dead-letters/drain` (200, drained it) and
   **`POST /platform/ops/rotate-secret-key` (200, rotated the platform signing key)**.

   **★ The rotation is not merely destructive — it is a full auth compromise.** The caller
   supplies the new key, so afterwards they know the signing secret and can mint tokens that
   verify: any user impersonable, admin included. **The delegation rule in (1) does not touch
   it** — that bounds what a key may *grant*, never what it may *do*. Two holes, one root cause,
   and fixing the visible one first would have left the sharper one open.

   **★ The 400 that nearly hid it.** The first probe sent a 40-character `new_key` that happened
   to equal the active `SECRET_KEY`, and got **400** — which reads as a refusal in a status
   column. It is `new_key is the same as the current active key`, raised *after* authorization
   and after the length check. Re-running with a distinct value returned 200 and completed the
   rotation. **A probe that only records status codes will mis-read a validation rejection as an
   authorization one**; the regression test deliberately uses a distinct, valid key so it cannot
   pass against the unfixed code.

   Fixed with **per-endpoint scopes on 44 routes** — `platform.admin` for keys/queue/nodes/
   observability/flow runs/flow CRUD/rotation, `webhook.manage` for webhooks, `flow.execute` for
   the nodus surface, `execution.read` for tenant usage. **The router gate itself was NOT
   tightened**, because `POST /platform/syscall` is the SDK's whole surface and is used with
   narrow scopes like `memory.read`; requiring `platform.admin` there breaks every SDK caller.
   That route and `GET /platform/syscalls` stay ungated at the route level by decision — their
   authority is per-syscall in `_resolve_dispatch_capabilities` — pinned by an equality test so a
   47th ungated route fails CI. **No JWT caller is affected at all:** the parent gate already
   required `is_admin`, and an admin session derives `platform.admin` and `webhook.manage`.
3. **Reconcile the two admin guards** so there is one answer to "is this principal an operator".
   Still open, and now lower stakes: with (2) in place `require_platform_admin_access` is no
   longer the only check on anything, so it is a tidiness problem rather than a hole.

**★ Note for whoever takes (2): SQLite cannot reproduce this.** `platform_api_keys.scopes` is a
PostgreSQL `ARRAY`; on SQLite the ORM insert fails at the driver with
`ProgrammingError: type 'list' is not supported`, **after** the authorization gate has already
been passed. A SQLite run therefore shows a 500 where PostgreSQL shows a 201 — the harness hides
the finding behind an unrelated error. The proof above was run against a throwaway
`pgvector/pgvector:pg16` container.

---

## HTTP-SCOPE-GAP-1 — the capability model does not reach the runtime's own front door

**★ FIRST HALF CLOSED 2026-08-16 (#449) — a JWT no longer bypasses scopes.**

The conditional half of this finding is fixed. `enforce_api_key_scope` gated API-key callers
only, so an interactive browser session was **strictly more privileged than any API key**. A JWT
session now presents `session_scopes` and is gated by the same check.

**The hard prerequisite this entry recorded — "a JWT carries NO scopes today, so 'stop
bypassing' implemented literally denies every session request" — was solved the recommended
way: derive from the user row.** `_resolve_authenticated_jwt_user` already loaded
`User.is_admin`; authority is derived from it **per request**, so nothing is encoded in the
token, **no live session is invalidated** (2.0.0 already did that once via `purpose`), and an
admin grant or revocation takes effect on the next call rather than the next login.

**The scope split is the app team's, not ours** (`RUNTIME_FEATURE_REQUESTS.md` → *Response to
v2.1.0 §6*). Ordinary: `flow.read`, `flow.execute`, `memory.read`, `memory.write`, `agent.run`,
`execution.read`. Admin adds `webhook.manage`, `platform.admin`. **Neither includes
`memory.delete` or `event.emit`** — nothing in their client uses either, so a session must not
inherit them; an API key can still be granted them explicitly. Both their constraints are
honoured: admin keys on the **existing user-row flag** (one source of truth for "operator"), and
nothing here pretends to answer data ownership.

**★ It ships ENFORCING rather than default-off, and the justification is an enumeration rather
than confidence.** Only **7 of 147** route decorators enforce a scope at all, and the only three
they require — `flow.read`, `flow.execute`, `memory.read` — are in the ordinary set. So every
signed-in user still passes every currently-enforcing route; the blast radius is *countable*,
which is what distinguishes this from a tightening that genuinely needs soak (`IDEM-11`,
`AUTHORITY-VALUE-1`'s clamp). `test_every_enforced_scope_is_held_by_an_ordinary_session` scans
the source for real call sites and fails if anyone adds an enforcement an ordinary session cannot
satisfy — so that argument decays into a CI failure rather than into 403s in a browser, which is
exactly the *"scattered 403s that read as a frontend bug"* outcome the app team asked us to
avoid. Escape hatch: `AINDY_JWT_SCOPE_ENFORCEMENT=0`.

**A bug this change introduced and the existing suite caught.** `_resolve_authenticated_jwt_user`
has two degraded return paths (no usable `Session`; a DB error under `TEST_MODE`) that return
before the user row is read. Deriving scopes only at the end left those principals with **no
grant at all**, i.e. denied everything. `test_scope_guard_passes_jwt_user` failed and surfaced
it. Fixed by seeding the **non-admin** grant immediately after `user_id`, before any early
return, and re-deriving once the row confirms `is_admin` — least privilege on the degraded paths,
authoritative on the normal one. That test was **rewritten, not deleted**: it had been asserting
the defect (a JWT with no scopes passing `flow.read`), and now asserts the same route still works
for the same user for a different reason, plus the two fail-closed cases.

**★ SECOND SLICE CLOSED 2026-08-16 (#462) — `memory_router.py` (item D).** All **22** routes
under `/memory/*` now carry a gate. Reads take `memory.read` **or** `memory.write`; writes take
`memory.write`; `/nodus/execute`, `/execute` and `/execute/complete` take **`flow.execute`**,
because they compile and run caller-supplied workflow code — filing them under `memory.write`
would make *"may I remember this"* and *"may I run this"* one permission.

**Counted against the running app, not the source.** Walking a booted runtime-only app:
**29 of 126 registered `APIRoute`s** now enforce a scope, up from 7. *(126 registered routes vs
this entry's original 147 `@router.*` decorators is not drift — some handlers carry stacked
decorators, and a few routers are app-profile-only. Both numbers are real; they count different
things, so do not mix them in one ratio.)*

- **★ The any-of form exists to stop one credential getting two answers.**
  `_DISPATCH_CAPABILITY_SCOPES` authorizes `memory.read` with **either** `memory.read` or
  `memory.write`, so a write-scoped key reads fine through `POST /platform/syscall`. A literal
  `memory.read` gate on these routes would refuse that same key at the HTTP door.
  `enforce_api_key_scope(scope, *alternatives)` now takes alternatives; existing single-scope
  call sites are unchanged.
- **★ The safety scan had gone blind the moment the any-of form appeared.**
  `test_every_enforced_scope_is_held_by_an_ordinary_session` matched
  `enforce_api_key_scope\(Scopes\.([A-Z_]+)\)` — a closing paren immediately after one argument
  — so a two-argument, two-line call matched **nothing** and the test kept passing on a
  shrinking sample. Replaced with a paren-balanced scan plus a liveness control
  (`test_the_scan_sees_the_any_of_form`). This is variant 2 of the green-check catalogue inside
  the guard that was supposed to prevent variant 2.
- **The coverage test is route-derived, and the first draft of it was vacuous.** Scanning
  `app.routes` for `/memory` paths found **zero** routes — FastAPI ≥ 0.137 stores
  `include_router` results as a lazy `_IncludedRouter` instead of flattening — so "nothing
  ungated" was true of an empty set. Only the `checked >= 20` floor caught it. It now walks
  `_iter_api_routes` and keys ownership on the endpoint's **module**, so a route added tomorrow
  without a gate fails this file.
- **Blast radius, measured rather than assumed.** An ordinary session already derives all three
  scopes, and a test drives the real routes to prove a signed-in user loses nothing. The only
  exposed callers are platform API keys hitting `/memory/*` over HTTP; **no first-party caller
  does** — the SDK's `client.memory.*` is `MemoryAPI(self.syscalls)`, i.e. `POST
  /platform/syscall`, which was already gated, and no app-side source sends `X-Platform-Key`.
- Mutation-checked 4/4: ungate `create_node` (3 fail), drop the read gate's write alternative
  (3), file execution under `memory.write` (3), revert any-of to exact match (1).
- One observable change beyond authorization: `POST /memory/execute/complete` has returned
  **410 Gone** since completion moved inside `POST /memory/execute`; an unscoped caller now sees
  **403** there instead, because authorization precedes the deprecation notice.

**★ CORRECTION 2026-08-16 — the "97 of 126 enforce nothing" figure published with #462 was
WRONG, and wrong in the alarming direction.** It came from walking each route's own `dependant`,
which **does not include dependencies declared on the router it was included into**. The
`/platform` tree carries `Depends(require_platform_admin_access)` on the parent `APIRouter`, so
56 routes counted as "enforcing nothing" are in fact admin-gated. Corrected census of the 126
registered routes:

| Bucket | Count | Meaning |
|---|---|---|
| scope-gated | **29** | `enforce_api_key_scope` (22 memory + 7 pre-existing) |
| admin-gated | **56** | `require_platform_admin_access` / `require_admin_principal` — authority, not a scope |
| public | **21** | health, version, `/auth/{login,register,verify-email,password/*}`, `/watcher/signals`, `/client/*` |
| **identity only** | **20** | the actual remaining gap |

*Method note that generalises: a per-route dependency walk under-reports enforcement, and a
router-level walk over-reports it for routes that opted out. Accumulate the router's
`dependencies` down each nesting level — and remember `_IncludedRouter` hides the nesting.*

**★ THIRD SLICE CLOSED 2026-08-16 (#464) — the 18 remaining identity-only routes.**
`coordination_router` (13) and `platform/agents_router` (5) are gated. Census now: **47
scope-gated, 56 admin-gated, 21 public, 2 identity-only** of 126.

- **Three gates, not one.** `agent.run` for the agent surface; `execution.read` for the run
  views (`/runs`, `/runs/{id}/children`, `/conflict/run`); `memory.read` OR `memory.write` for
  `/memory/shared` and `/conflict/memory`, which query `memory_nodes` and inspect a memory path.
  **Gating those two on `agent.run` because they live in this router would have made the agent
  surface a second door onto memory** — the exact distinction the memory router had just drawn.
  Tests assert the split in *both* directions, so neither gate is decorative.
- **No `agent.read`/`agent.manage` invented.** The agent surface is one authority because the
  vocabulary has no finer grain, and adding one obliges every consumer to grant a scope that
  answers no question they ask today. A read/write split there should be a deliberate vocabulary
  change, not a side effect of adding gates.
- **★ `/platform/agents` never inherited the `/platform` admin gate** — it is included on the app
  directly, not through `platform_router`, which is *why* FR-12b works for ordinary users and
  also why it had no authority check at all. Owner scoping is untouched and still does what a
  scope cannot: a scope answers *"may you touch agents"*, never *"may you touch **this** agent"*.
- Mutation-checked 3/3: file `/memory/shared` under `agent.run` (3 fail), ungate
  `list_my_agents` (3), file the run views under `agent.run` (2).
- **The two survivors are a decision, pinned by equality**, not a remainder:
  `POST /auth/logout` and `POST /auth/password/change` act only on the caller's own account,
  where a scope is a permission nobody could be denied. The test fails both if an ungated route
  appears **and** if someone gates one of these two while tidying.

**Discovered doing it (not fixed, not filed as its own entry — record only):** in a booted app
`coordination_router` is reachable through **two** registrations — it is in `APP_ROUTERS`,
mounted under `/apps`, and appears again via `get_routers()`. Both copies carry the same gate, so
this is a composition detail rather than an authority one, but any route census must dedupe by
`(method, path)` or it over-counts.

**★ FOURTH SLICE CLOSED 2026-08-16 (#465) — the `/platform` tree.** 44 routes gained per-endpoint
scopes; see `KEY-SCOPE-ESCALATION-1` item (2) for the mechanism and for the signing-key rotation
it closed. Census across 126 registered routes is now **91 scope-gated, 12 admin-gated, 21
public, 2 identity-only**.

**★ The safety guard was rewritten and is now stronger.**
`test_every_enforced_scope_is_held_by_an_ordinary_session` required every gate to be satisfiable
by an *ordinary* session — correct while every gated route was one an ordinary user should reach,
and by this slice it would have been **an argument for weakening `platform.admin`**. Replaced by
`test_no_route_enforces_a_scope_nobody_can_satisfy`, route-derived, with two legitimate branches:
satisfiable by an ordinary session, **or** the route is admin-gated and the scope is one an admin
session derives. A gate failing both is a permission nobody can hold — a 403 the caller cannot
fix. Mutation-checked by putting `memory.delete` on a non-admin route.

**What remains of this entry:** nothing structural on the HTTP surface. The two ungated
`/platform` routes are a recorded decision, the two identity-only routes are self-service, and
`require_platform_admin_access` is no longer the sole check on anything (item 3 of
`KEY-SCOPE-ESCALATION-1` is tidiness). The open questions the entry raised that are *not* about
route coverage remain: `execution.read` conflating scope with data ownership, and the fact that a
scope cannot answer *"may I read someone else's"*. *(The "`memory_router.py` still has zero `SyscallDispatcher`
references" half of this paragraph is now stale twice over — `ROUTE-EFFECT-BYPASS-1` A–C rewired
three of its four direct-DAO routes and this slice added the gates. What remains there is one
route, `POST /nodes/search`, and it is tracked in that entry, not this one.)*


**Status: OPEN — P0.** Filed 2026-08-15 from the Codex comparative audit (G3), verified with
exact counts.

**Measured, and the audit's numbers were right:**

| | Audit (2026-08-14) | Verified (2026-08-15) |
|---|---|---|
| `Depends(enforce_api_key_scope)` usages | 7 | **7** — 4 in `platform/flows_router.py`, 3 in `platform/platform_ops_router.py` |
| Total route decorators under `AINDY/routes/` | 141 | **147** |

The +6 is exactly the routes added since the audit (five `/platform/agents` routes plus
`…/agents/{namespace}/restore`), so the drift is fully accounted for. **Seven of 147 route
decorators carry a scope check.** Everything else — including the whole `/memory/*` surface —
depends only on `get_current_user`, which is *identity*, not authority.

**The check is additionally conditional.** `enforce_api_key_scope`'s own docstring says it:
*"JWT users carry full trust and are never gated by this check"*, and the body confirms it —
`if current_user.get("auth_type") == "api_key":` is the whole gate, so a session-authenticated
caller skips it entirely. **An interactive JWT session is strictly more privileged than any API
key.**

**And the REST path can reach effects without the dispatcher at all.** Verified:
`memory_router.py` contains **zero** references to `SyscallDispatcher` or `dispatch`, and
`create_node`'s handler instantiates `MemoryNodeDAO(db)` and calls `dao.save(...)` directly. The
capability vocabulary is never consulted on that path.

**Why this is the sharpest of the three audits' findings.** The runtime *already has* the
authority model; the gap is that its own front door does not use it. Compare
`AUTHORITY-VALUE-1`, which is about the syscall chokepoint checking a caller-supplied value —
this is about a large surface not reaching the chokepoint at all.

**Proposed:** a `CallerAuthority` derived once at the edge — from JWT claims, platform-key scopes
or an internal execution context — carried into `SyscallContext.capabilities` uniformly. Then
either route handlers dispatch through the syscall contract, or they take a `CallerAuthority`
dependency gating them with the same vocabulary.

**★ DECIDED 2026-08-15 (owner): a JWT must NOT bypass scopes.** The current behaviour —
`enforce_api_key_scope` gating API keys only, with the docstring stating *"JWT users carry full
trust and are never gated by this check"* — is to be removed, so that one capability vocabulary
governs every surface regardless of how the caller authenticated.

**★ That decision has a hard prerequisite, and it must be settled before any code changes:
a JWT carries no scopes today.** Verified — `create_access_token` encodes `tv` (token version),
`purpose` and `exp` plus whatever the caller passes; there is no scopes claim anywhere in the
token. API keys carry `api_key_scopes`; JWTs carry nothing comparable. So "stop bypassing"
implemented literally would deny every session-authenticated request, including the platform
SPA's own.

Where a JWT's authority comes from is therefore the first design question, not the last:

| Option | Token change | Existing sessions | Note |
|---|---|---|---|
| (a) Derive from the user row — `is_admin` ⇒ full set, otherwise a default set | none | keep working | No re-issue; the derivation is server-side and auditable |
| (b) Add an explicit `scopes` claim at login | yes | **invalidated** | 2.0.0 already ended every session once for the `purpose` claim; doing it again needs a reason |
| (c) (a) now, (b) later for finer grain | none now | keep working | Keeps the door open without paying for it twice |

**Recommend (a), then (c) if per-user granularity is ever needed.** And roll it out so the
derived scope set initially *matches today's effective privilege*, then narrows — flipping
straight to a restrictive set would lock out the SPA before anyone learns which scopes it
actually needs. The runtime's established pattern (ship the mechanism default-permissive, tighten
after soak) applies exactly.

**Sequencing note:** this decision makes the 7-of-147 coverage number the *smaller* half of the
work. Extending scope checks to the remaining routes is mechanical; deciding and deriving JWT
authority is the part that needs care.

**Risk if omitted.** `SECURITY_MATRIX.md` and the README describe a scope model that the code
enforces on 7 of 147 routes and not at all for session callers. Documentation that overstates
enforcement is a failure mode this repository already names — it is the whole subject of the
"trusting a green check" section in `CLAUDE.md`, one layer up.

---

## FLOW-PARALLEL-1 — the flow engine has no fan-out, join, or barrier

**Status: OPEN — P1.** Filed 2026-08-15 from the Codex comparative audit (G4), verified.

`resolve_next_node()` (`flow_engine/node_executor.py:49`) returns exactly one successor or
`None`, and the runner advances one node at a time; plan steps execute strictly sequentially.
Three independent API calls in a plan take the **sum** of their latencies.

**Why it belongs at runtime level.** Ordering, partial failure, and result determinism under
concurrency are precisely the problems a substrate should solve once. Any application needing
parallelism today must implement it *outside* the flow engine — losing `FlowHistory`, retry
policy and quota accounting for the parallel branch, which are the guarantees the engine exists
to provide.

**Proposed primitive.** A `ParallelNodeGroup` in the flow graph —
`edges[node] = ParallelGroup([n1, n2, n3], join="all" | "any" | "quorum(k)")` — with
`FlowHistory` writing one row per branch under a shared `sequence_number` prefix, output patches
merged in **declaration order rather than completion order**, branch failure resolved by the
group's declared policy, and quota accounting summed across branches so
`MAX_CONCURRENT_PER_TENANT` stays meaningful.

**★ The load-bearing detail is determinism, not speed.** Merging in declaration order is what
keeps a parallel run replayable, and replayability is the property this engine is built around.
Concurrency and determinism are separable; the runtime should own both or neither.

**★★ Declaration-order merge answers ORDERING and not CONFLICT — this entry is under-specified
(added 2026-08-18, provenance `LANGGRAPH-NODUS-2026-08-18`).** *"Merge output patches in
declaration order"* says in what sequence patches are applied. It says **nothing about what
happens when two branches wrote the same key.**

**Verified: today that is last-write-wins by default.** `runner_steps.py:257` applies
`state.update(patch)` on `node_status == "SUCCESS"` — a plain dict update. **Not a defect now**,
because `resolve_next_node` returns exactly one successor so there is never a second writer. **It
becomes one the day this entry lands**, silently, and last-write-wins is the worst possible default
for a merge nobody declared.

**LangGraph's answer is the design input, and the shape is the point rather than the policy** —
conflict resolution is **typed and declared per cell**, not implicit:

| Reducer | Semantics |
|---|---|
| `LastValue` | **rejects** more than one writer — the conflict is an error, not a silent overwrite |
| `BinaryOperatorAggregate` | folds both writes with a declared operator |
| `NamedBarrierValue` | joins — the cell is not ready until named writers have all written |
| `Topic`, `EphemeralValue`, `add_messages` | append / non-persisted / domain-specific merge |

**★ This is the same question the MetaGPT study asked about joins** (*"all-or-nothing,
first-failure-cancels-siblings, or collect-partial?"*) **arriving from the write side.** Both have
the same answer: the runtime should not pick one policy — it should require the flow to **declare**
one, and refuse an undeclared concurrent write rather than resolving it by luck of ordering.

**Do not take reducer-mediated channels wholesale.** That is a *language-layer* concern and belongs
in Nodus's absorb list, not here. What belongs here is the narrower thing: **a declared merge
policy per key, with an undeclared double-write failing loudly.** Note the same gap exists one
layer down — Nodus's state sharing is `std:memory`, a process-local KV, with no merge discipline
either, so neither layer currently has an answer.

**Settle this together with `EFFECT-PARTIAL-1`.** Whether a partially-failed parallel group is one
`FlowHistory` row or several decides what a partial-success envelope has to represent, and the
merge policy decides what a *partially applied* patch set even means.

### ★ A worked reference design now exists — read it before designing this (added 2026-08-17)

Provenance: `MAF-REFERENCE-2026-08-17`. **Four independent comparative analyses (Codex, Aider,
MetaGPT, OpenHands) each derived this same missing primitive from a different direction and none
of the four systems had one to copy.** Microsoft Agent Framework does. The checkout is at
`C:\codev\Autogen research\agent-framework\python\packages\core\agent_framework\_workflows\`,
frozen at 2026-06-24, and every line reference below was verified in it.

**★ The reframe, which makes this a smaller change than four analyses implied.** MAF runs
Pregel/BSP supersteps (`_runner.py:78`, `run_until_convergence`) and writes **one checkpoint per
superstep**, each linked to `previous_checkpoint_id` (`:84, :97, :144, :212`), with executor state
flushed into shared state *before* the checkpoint. `PersistentFlowRunner` already commits
`FlowHistory` per node with a monotonic `sequence_number` (`db/models/flow_run.py:79`). So:

> **A superstep is the existing per-node commit boundary widened to span a barrier-delimited
> group.** The work is not "add concurrency to the flow engine" — it is *widen the transaction*.

**The four edge groups, all `DictConvertible` — i.e. the topology is data, not a call stack:**

| MAF (`_edge.py`) | Semantics | Ours |
|---|---|---|
| `SingleEdgeGroup:470` | one → one | `resolve_next_node` — the only one that exists |
| `FanOutEdgeGroup:501` | one → many, concurrent | absent |
| `FanInEdgeGroup:616` | many → one, fires when all sources produced | absent |
| `SwitchCaseEdgeGroup:808` | predicate-selected branch, with `Default` | partial — conditional edges pick among alternatives |

**★ Note `SwitchCaseEdgeGroup` subclasses `FanOutEdgeGroup`, not `EdgeGroup`** — a switch is a
*constrained fan-out* in their model. That is a design hint: one mechanism, two policies, rather
than two node-group kinds.

**★ The negative result is the most valuable part, and it is documented in their own source.**
Predicates **do not serialize**. On restore, `SwitchCaseEdgeGroup._selection_func` becomes
`_missing_callable("switch_case_selection")` (`_edge.py:463`) — and `_missing_callable` appears at
`:50, :448, :463, :701`, so they hit the wall in four places and chose the same answer each time:
**serialize the shape, name the predicate, fail loudly if it is missing on restore.** Anyone
building workflow-as-data hits this exactly; do not rediscover it.

**★ That wall is harder for us than for them.** Our conditional edges are **Python closures over
in-process state** — `node_executor.py:57` calls `edge["condition"](state)` directly. MAF's
predicates are at least named functions on a builder. Naming ours is a prerequisite for
workflow-as-data, not a detail of it.

**The join-semantics question this answers.** The MetaGPT analysis asked: *all-or-nothing,
first-failure-cancels-siblings, or collect-partial-and-continue?* MAF's answer is **structural,
not a policy choice** — `FanInEdgeGroup` buffers and fires when all sources have produced, inside
a superstep whose barrier *is* the unit of progress. That converts the question from a semantics
choice into a **transaction-boundary choice**, which is the right frame and one this engine is
unusually well-placed for.

**★ The open decision that frame exposes, and it is ours to make, not MAF's to answer:** should
the barrier be the commit boundary (one row for the group, MAF's answer because BSP demands it),
or should fan-out branches commit independently (which is what our per-node commit suggests)?
**That decides whether a partially-failed group is one `FlowHistory` row or several**, and it
therefore decides what `EFFECT-PARTIAL-1` has to represent. Settle the two together.

**What to refuse.** MAF's durability posture. Its in-core checkpoint chain **does not survive the
process** — crash-durable orchestration is delegated wholesale to Azure Durable Task
(`python/packages/durabletask`; the only durable addressable actor is `AgentEntity : TaskEntity`).
**Take the topology model, refuse the delegation.** Topology is a data-model change inside one
subsystem; durability is CAS claims, leases, rehydration, watchdogs, DLQ and idempotency — the
part this runtime already has and the part every system in the comparison corpus either bought,
hand-rolled in a `while` loop, or improvised with `asyncio.create_task`.

---

## AUTHORITY-NEGOTIATION-1 — a capability denial has no bounded recovery path

**Status: OPEN — P1.** Filed 2026-08-15 from the Codex comparative audit (G5), verified.

A denied capability check terminates the step — `CAPABILITY_DENIED` is emitted at
`nodus_adapter.py:188` and `nodus_execution_service.py:334`, and there is no path that asks
*"this step was refused at the authority it requested; may it proceed at a lower one?"*

Because approval is whole-plan, the only recovery today is a human re-approving an entirely new
run, discarding the durable state the original accumulated.

**Proposed primitive.** An authority-negotiation stage in the step lifecycle: on
`CAPABILITY_DENIED`, consult a declared fallback — a reduced-scope variant of the same tool, a
rehearsal via the existing virtual-tool path, or a human WAIT gate — attempt **at most once**,
and record both attempts in `FlowHistory`.

**★ The runtime has a better fallback available than the system that prompted this finding.**
Codex's escalation path is attempt → approve → retry *outside* the sandbox, with a veto that
downgrades rather than escalates. `sys.v1.agent.simulate` already provides a zero-side-effect
rehearsal against virtual tools, which is a strictly safer fallback than re-running with more
authority. The design constraint to keep from the comparison is the shape, not the mechanism:
**bounded (exactly one retry), directional (downgrade only, never escalate), and recorded.**

**★ The missing mechanism has a name and a shape (added 2026-08-17).** The Codex portability
analysis proposes it as its N1 and it is the piece this entry describes a use for without
specifying: **`amend_token` — an authenticated, audited, monotonic-under-ceiling authority
amendment.** Verified at HEAD: `capability_service.py` has `mint_token` (`:442`) and
`refresh_token` (`:560`) and **no amendment primitive**. `refresh_token` deliberately never
widens, so today the only way to change a run's authority is to mint a new one, which means a new
approval, which is why a denial discards durable state.

**★ `amend_token` must be the dual of `refresh_token`, not its mirror.** The whole value is the
word *monotonic-under-ceiling*: an amendment may only move **within** the ceiling
`capability_service.py:479-491` already computes, and for this entry's purpose only **downward**.
An amend primitive that can widen is a second minting path, and a second minting path is how the
one hard, cryptographic guarantee in the enforcement matrix stops being hard. If it is built,
build the narrowing direction first and leave widening unimplemented rather than merely
unauthorised.

---

## EGRESS-INPROC-1 — network policy is enforced in-process and documents its own bypasses

**Status: OPEN — P2.** Filed 2026-08-15 from the Codex comparative audit (G6), verified.
**This entry exists to re-home a mechanism, not to build one.**

`egress_guard` wraps `socket.getaddrinfo`, `socket.socket.connect` and `connect_ex`, keyed on a
contextvar allowlist, with IP-literal connects failing closed unless vouched for by an allowed
resolution. It is careful work, it is **off by default** (`AINDY_EGRESS_ENFORCEMENT`), and its
own docstring names both holes under the heading *"Honest limits"* — verified verbatim:

* a tool resolving or connecting on a **thread that does not inherit the contextvar** escapes
  the scope (raw `threading.Thread` does not copy context), and closing that in-process would
  mean globally wrapping `threading.Thread`, *"intentionally not done; the sandbox path is the
  real fix"*;
* only the stdlib `socket` layer is wrapped, so a tool linking its own native resolver bypasses
  both hooks.

**★ Nothing here is a surprise to the code — the module already reaches the right conclusion**,
naming the sandbox `--network none` + mediated-proxy path as the non-bypassable version. The
finding is that policy and enforcement are in the same place when they should be split: the
*decision* is a runtime capability concern, the *enforcement* is a host concern.

**Proposed:** make the egress decision a property of the isolation provider from
`TOOL-SEAM-ISOLATION-1` rather than of the calling thread. The in-process guard remains the
`in_process` provider's best effort, with its honest limits intact, while container and
strong-VM providers enforce at the namespace, and the runtime reports which mechanism is active.

**Risk if omitted.** `capability_policy` domain allowlists read as controls in configuration and
in audit output while being advisory in the default deployment profile — the gap between what an
audit trail appears to prove and what was actually enforced.

---

## ROUTE-EFFECT-BYPASS-1 — four memory routes reach effects without the dispatcher

**Status: OPEN — P1. A+B DONE (#460), C DONE (#461); D remains.** Scoped 2026-08-16 against
`main`, split out of `HTTP-SCOPE-GAP-1` because
the fix is different work: that entry is about *scope checks not reaching routes*; this is about
*routes not reaching the chokepoint at all*. A scope decorator on a route that skips the
dispatcher still leaves the effect unmediated.

**Measured, and smaller than `HTTP-SCOPE-GAP-1` implies.** That entry says `memory_router.py`
has "zero `SyscallDispatcher` references", which is true and reads as if all 22 routes bypass.
**18 of 22 go through `_mem_run_flow` → `run_flow` → the dispatcher.** Exactly four touch
`MemoryNodeDAO` directly:

| Route | Kind | Replacement syscall |
|---|---|---|
| `POST /nodes` (`create_node`) | **write** | `sys.v1.memory.write` — exists, **`EXACTLY_ONCE`** |
| `POST /links` (`create_link`) | **write** | **none exists** |
| `POST /nodes/search` | read | `sys.v1.memory.search` — exists |
| `POST /recall` | read | `sys.v1.memory.read` — exists |

**The bypass is double: `grep -c enforce_api_key_scope AINDY/routes/memory_router.py` is 0.** These
four have neither a capability gate nor the dispatcher — no effect ledger, no tenant-isolation
check, no quota accounting, no at-most-once.

**Work, in cost order.**

- **A — `POST /nodes` → `sys.v1.memory.write`.** Cheapest and highest value. Since the `IDEM-11`
  audit the syscall declares **`EXACTLY_ONCE`**, so routing through it gains capability
  enforcement, the effect ledger, tenant isolation *and* at-most-once in one move. This is
  strictly more valuable than it was before 2.3.0.
- **B — the two reads → `memory.search` / `memory.read`.** Mechanical; both syscalls exist.
- **C — `POST /links` is net-new. DONE 2026-08-16 (#461).** `sys.v1.memory.link`, registry floor
  23 → 24, `EXACTLY_ONCE` (a retry of `create_link` builds a *second* edge between the same
  pair). **★ The capability decision was the whole point: it carries `memory.link`, which
  `memory.write` does not grant.** A syscall adding a mediation hop and no authority granularity
  would only relocate the same undifferentiated power behind a longer call path — writing a node
  and wiring the graph between nodes are different powers, and `memory.delete` already set the
  precedent. A test drives the dispatcher with a `memory.write`-only context and requires
  refusal, so the split is a boundary and not a label. Tenant scoping moved into the syscall:
  both endpoints resolve through a tenant-scoped `get_by_id`, and a foreign node is reported
  **identically to a missing one** — distinguishing them would make the route an existence oracle
  for other tenants' ids. Route status contract preserved (404 unresolvable / 422 refused, not
  both collapsed to 400). **Not added to `_STABLE_SYSCALLS`, and deliberately absent from
  `_DISPATCH_CAPABILITY_SCOPES`** — so SDK `/platform/syscall` callers get an empty grant and the
  dispatcher denies it, while the HTTP route that already had the caller works. Publishing a
  `stable=False` syscall to SDK callers is the half that cannot be withdrawn; two tests pin the
  omission as a decision rather than an oversight. Adding it later needs a `Scopes.MEMORY_LINK`
  of its own — mapping it onto `MEMORY_WRITE` would undo at the scope layer exactly the split the
  capability makes above.
- **D — scope enforcement on this router.** Belongs to `HTTP-SCOPE-GAP-1`'s remainder, and is
  independent of A–C.

**★ The "18 are mediated" half is weaker than it sounds.** `run_flow`
(`flow_engine/entrypoints.py:124`) falls back to `_run_flow_direct` when `user_id` is absent —
**absent identity SKIPS the boundary rather than denying it**, logged at `debug`. So those 18 are
mediated only while identity is present. That is `AUTHORITY-VALUE-1`, and it partially undercuts
the good news above; fixing it there fixes it here.

**Relationship to the native-enforcement plan (`PLAN_native_enforcement_tier.md`, pinned at
`d32bd5d`).** Same *shape* as its W4 — an effect reached without passing the mediating chokepoint
— but vastly cheaper. W4's hard part is *"deciding what a tool receives instead of the live DB
session — almost certainly a scoped syscall channel"*; **here that channel already exists and is
registered.** No new execution plane, no Rust, no Linux dev loop. Two of that plan's premises have
also moved since it was pinned: its §6 `GUEST-CONFINE-1` fix **shipped** (#438), and
`memory.write` became `EXACTLY_ONCE`, which raises A's payoff specifically. Its §5 dev-loop
constraint is unchanged and still gates the Rust work; nothing in A–D depends on it.

## ROUTE-AST-UNWIRED-1 — the boot-time route proof exists and is never run against the application

**Status: OPEN — P2 (documentation/assurance, not a live hole).** Found 2026-08-15 while
verifying the *invariants* the three audits credit the runtime with, rather than their gaps.

**The claim.** The substrate audit's strongest "already covered" item is that the runtime proves
its execution-entry invariant *structurally*: `validate_registered_route_execution` walks every
registered `APIRoute`, parses the endpoint's module with `ast`, builds a call graph, and raises
`RouteExecutionViolation` if the endpoint cannot reach `execute_with_pipeline`. Its enforcement
matrix records the route execution contract as a **Hard** boundary — *"(boot-time refusal)"* —
and cites it as wired in `routing.py`.

**Verified, and the claim does not hold.** `validate_registered_route_execution` has, repo-wide,
exactly **three** references: its own definition, and two lines in
`tests/unit/test_route_execution_guard.py`. **The application never calls it.** What
`routing.py:87` calls is `enforce_registered_route_execution` — the *request-time wrapper*, a
different function with a confusingly similar name.

**And its single test call site is not a proof of the real app.** It appears in
`test_helper_indirection_route_is_allowed_by_runtime_wrapper_even_if_ast_audit_is_stricter`,
against a synthetic `FastAPI()` app, inside `pytest.raises(RouteExecutionViolation)` — i.e. the
test exists to demonstrate that the AST audit is **stricter than the wrapper** and rejects a
legitimate helper-indirection route the wrapper allows.

**So this is not an oversight to simply fix.** The test name records the reason the validator is
unwired: it produces false positives on helper indirection. The defect is the **claim**, not the
absence — the runtime's real guarantee here is the request-time wrapper, which is genuine and
which `ROUTE-GUARD-1` has just been corrected in, not a boot-time AST refusal.

**★ This is the same family as `CI-MARKER-1`, `DOCS-COVERAGE-CLAIM-1` and the native-suite skip:
verification that exists and does not run.** It is the eighth variant of the pattern the
"trusting a green check" section of `CLAUDE.md` enumerates, and the first found in a *runtime*
mechanism rather than a test one.

**What to do, in preference order:**

1. **Correct the claim** wherever the boot-time refusal is asserted — the enforcement matrix in
   the audit, and any runtime doc that repeats it. Cheapest, and it is the actual defect.
2. **Decide the validator's fate deliberately.** Either teach it about helper indirection and
   wire it at boot (behind a flag first — it will reject routes that work today), or delete it
   and keep the wrapper as the sole mechanism. Leaving a stricter, unrunnable twin next to the
   real guard is what produced the false claim in the first place.

**Do not "fix" this by wiring it as-is.** By its own test, it raises on a route that functions
correctly, so wiring it unchanged would fail boot on a working application.

---

## AUDIT-INVARIANTS-VERIFIED-1 — sweep record: which claimed guarantees actually hold

**Status: RECORD (2026-08-15), no action.** Three audits credit the runtime with a long list of
invariants. The *gaps* were verified when each was filed; this entry records the sweep of the
**guarantees**, because an overclaimed guarantee is worse than a known gap — it is the thing
someone builds on.

**Held, verified against source:**

| Claimed invariant | Verified |
|---|---|
| `ExecutionUnit` distinguishes `waiting` from `resumed` | yes — both in the documented status machine |
| Waiting state is rehydrated at startup | yes — `startup._rehydrate_waiting_state` |
| `approve_run` is an atomic CAS from `pending_approval` | yes — `.where(… AgentRun.status == "pending_approval")` |
| Capability tokens are HMAC-SHA256 with a rotation window | yes — `hmac` + `hashlib.sha256` + `verification_keys()` |
| The effect ledger is used at **both** chokepoints | yes — `syscall_dispatcher.py:490` and `tool_registry.py:329` |
| Quota fails closed in production, open in dev/test | yes — `_quota_backend_failure_may_fail_open()` returns `is_testing or is_dev` |
| Background leadership: 60s lease TTL against a 20s heartbeat | yes — `LEASE_TTL_SECONDS` / `LEASE_HEARTBEAT_SECONDS` |
| Trace/pipeline ContextVars are reset on the failure path too | yes — resets run in the pipeline's finalisation block |

**Did NOT hold — two claims corrected:**

1. **The boot-time route AST proof does not run.** Filed separately as `ROUTE-AST-UNWIRED-1`.
2. **★ "Output validation is warn-only" is wrong, and the two audits contradict each other here.**
   The Hermes map records it as a no-change finding — *"N5 — Output validation is warn-only.
   Correct. … Failing closed on output would convert handler bugs into outages."* The substrate
   audit says the opposite: *"aindy also validates output, and fails closed on `stable`
   syscalls."* **Source settles it for the substrate audit.** In `syscall_dispatcher.py`, an
   output-schema mismatch on a syscall with `entry.stable` logs `logger.error`, emits an error
   event, completes the effect record as `failed`, and **returns an error envelope**; only
   *experimental* syscalls get `logger.warning` and continue.

   This matters beyond bookkeeping: N5 endorses a design that is not the current one. Anyone
   reading it as a description of today's behaviour would be surprised by a stable syscall
   failing closed on its own handler's output — which is precisely the outage mode N5 argues
   against, and which the runtime has deliberately chosen for stable surfaces.

**Method note for whoever repeats this:** verify the guarantees, not just the gaps. Both errors
above were in "already covered" sections — the parts of an audit least likely to be re-checked,
because a finding invites scrutiny and a reassurance does not.

---

## FLAKY-1 — `test_platform_only_startup` fails intermittently in a now-required check

**Status: CLOSED (2026-08-15) — fixed by FR-11's callback-budget raise. 15 healthy runs, zero
reproductions.**

| Tree | Runs | FLAKY-1 |
|---|---|---|
| `8c1d4ac` | 11 | 0 |
| current `main` (post CI-MARKER-1, 1878 collected) | 4 | 0 |
| **total** | **15** | **0** |

Run durations 780–1222s throughout, so these are full runs rather than fast-fails. The only
recurring failure across all 15 was `test_runtime_packaging`, the known local-only
`python -m build --no-isolation` case that passes in CI. Against the measured ~50% base rate,
15 clean runs is ≈0.003% by luck.

**Both trees were required, and the second was the point.** The first 11 ran on `8c1d4ac`;
`main` has since gained ~268 collected tests (`CI-MARKER-1`), and this failure was
ordering-sensitive — so evidence from the older tree alone would not have supported closing.

**The fix is FR-11**: `AINDY_RUNTIME_CALLBACK_TIMEOUT_SECS`, default 30s, replacing a hardcoded
10s budget that no call site could override. Sized on measurement — ~3.85s median cold start on
the lightest profile is only ~2.6× headroom at 10s.

**★ Kept below rather than deleted: three wrong readings of this test, in order.** Two
consecutive failures read as *deterministic* (a third run refuted it); correlation with a branch
adding neighbouring files read as *caused by them* (a second baseline refuted it); and a single
captured traceback read as *refuting the timeout mechanism* (11 clean runs refuted it — the
sample came from a machine that could not spawn processes at all). Each was a confident
conclusion from a small sample, and the third was mine. The history is the useful part of this
entry now that the flake itself is gone.

**★★ CORRECTION 2026-08-15 (same day, later). This entry briefly said the leading mechanism was
"REFUTED". That was an over-conclusion from a single sample, and the single worst sample
available — it is downgraded here rather than deleted, because being wrong about this test three
times is the entry's own recurring theme.**

**The evidence that changed it: 11 consecutive healthy full runs of
`pytest tests -m runtime_only`, zero occurrences.** Run durations 780–1194s (i.e. real runs, not
fast-fails); the only recurring failure was `test_runtime_packaging`, the known local-only
`python -m build --no-isolation` case that passes in CI. Against the measured ~50% base rate,
11 clean runs is ≈0.05% by luck.

**How that reconciles with the traceback below.** The traceback came from a run on a machine that
had lost the ability to spawn processes at all — 43 failures in that run, Windows exit code
`3221225794` (`0xC0000142`, `STATUS_DLL_INIT_FAILED`) from every subprocess-spawning test. A
worker that cannot start writes nothing to stdout, `json.loads(stdout or "{}")` makes that `{}`,
and the handler raises from the `ok:false` branch. **So the most likely reading is that the
captured failure was NOT FLAKY-1 — it was process exhaustion wearing FLAKY-1's clothes**, and the
timeout mechanism FR-11 addressed was probably correct all along.

**What remains before closing.** The 11 runs were on tree `8c1d4ac`. Current `main` has since
gained ~268 additional collected tests (`CI-MARKER-1`), which changes both ordering and load —
and this failure was ordering-sensitive. A short confirmation series on current `main` is the
remaining step; do not close on the older tree's evidence alone.

**What stands regardless:** the diagnosability fix in `runtime_callback_host.py`. It is what will
let the *next* occurrence distinguish "worker died" from "callback failed" from "timed out"
directly, instead of producing another round of inference from one sample. It was never evidence
about *which* of those happened.

**The traceback itself, kept as the record of what was seen** — it is **not** the timeout branch:

```
>   assert evaluator({"trigger_type": "user", ...})["decision"] == "execute"
tests\unit\test_platform_only_startup.py:233

E   RuntimeError: runtime callback failed
AINDY\platform_layer\runtime_callback_host.py:159
```

Line 159 is the `if not response.get("ok")` branch. The timeout branch raises a *different*,
explicit message — `"runtime callback command timed out after Ns"` — and the natural failure does
not carry it. The previous "strongly indicated" reading was produced by **forcing** a timeout and
matching the shape, never by reading a real failure. That is the same error this file warns about
under DOCS-COVERAGE-CLAIM-1: a plausible mechanism, confirmed only against itself.

**Caveat, which turned out to be the whole story:** the reproducing run was on a machine that had
lost the ability to spawn processes (see the correction above). The caveat was recorded at the
time and then under-weighted in the headline — the lesson being that a caveat which invalidates a
conclusion should change the conclusion, not sit beneath it.

**★ The traceback's real value: it showed why this was never diagnosable.** That branch carried
**no diagnostic content at all**. A worker that dies before replying writes nothing to stdout;
`json.loads(stdout or "{}")` turns that into `{}`; `{}.get("ok")` is falsy; the handler raises its
default string. So *"the subprocess never started"* and *"the callback returned `{ok: false}`"*
produced **the same message** — no exit code, no stderr, no callback name. Three earlier natural
failures were piped through `tail` and lost; the one that survived said nothing.

Fixed in `runtime_callback_host.py`: empty stdout is its own error naming the callback, every
failure path appends `exit=<code>` (and stderr when present), and the three modes are pinned
mutually distinct by `tests/unit/test_runtime_callback_diagnostics.py` — mutation-checked, 4 of 8
tests fail against the old collapsed path.

**What closes this now:** the next natural occurrence will state the exit code and stream
contents, which distinguishes *worker died* from *callback failed* from *timed out* directly.
Until then the FR-11 budget raise (10s → configurable 30s, `AINDY_RUNTIME_CALLBACK_TIMEOUT_SECS`)
stands as a mitigation whose premise is now known to be wrong — **it may still help** (a slow
worker and a dead worker are both subprocess fragility) but it should no longer be described as
addressing the identified cause. A sustained clean streak (10+ consecutive `-m runtime_only`
runs, since 4 in a row happen ~6% of the time by luck against a ~50% base rate) remains the
alternative route, and **must be run on an otherwise idle machine** — see the process-exhaustion
caveat above, which invalidated three attempts.

Measured 2026-08-14. **Pre-existing and not introduced by any current branch** — established by running the full `tests/unit/` suite six times across two
worktrees, because a single baseline pointed at the wrong cause.

| Tree | Runs | `test_platform_only_registers_runtime_agent_defaults` |
|---|---|---|
| `main` (clean worktree) | 2 | 1 fail / 1 pass |
| feature branch | 4 | 2 fail / 2 pass |

**≈50%, on both trees.** It passes 100% of the time in isolation. Two earlier readings of this
same data were wrong and are recorded so the next person does not repeat them: two consecutive
failures read as *deterministic* (a third run refuted it), and its correlation with a branch
that added neighbouring test files read as *caused by them* (the second `main` baseline refuted
it). **Do not conclude anything about this test from fewer than ~4 runs per tree.**

**Why it matters now.** The test carries `pytest.mark.runtime_only`, so `Runtime Contracts` runs
it — and as of 2026-08-14 all ten status checks are required on `main`. A ~50% test in a
required check is a merge blocked at random, and the natural response ("just re-run CI") is
exactly how a genuine regression gets waved through later.

**★ Confirmed under the CI command itself (2026-08-15), which upgrades this.** The measurements
above all came from `pytest tests/unit/`. A run of **`python -m pytest tests -m runtime_only -q`
— byte-for-byte what `Runtime Contracts` executes** — reproduced the failure locally. An earlier
guess that the marker-filtered collection might not hit the ordering is therefore **wrong**: it
does. So this is not a local-convenience annoyance that CI happens to dodge; the required check
will red-line at random, and the only reason it has not yet is the ~50% coin flip. (`Runtime
Contracts` passed on the same commit in CI minutes later, which is exactly what a coin flip
looks like and is why "it was green" is not evidence here.)

**Leading mechanism — the hardcoded subprocess budget (this is `APP-FR-*` FR-11).**
`invoke_runtime_callback` (`runtime_callback_host.py:43`) spawns a **fresh subprocess per
callback** with a hardcoded **10.0s** timeout and raises on overrun. Forcing that path
reproduces a clean signature:

```
registry.py:433              _wrapped → invoke_runtime_callback
runtime_callback_host.py:72  raise RuntimeError("runtime callback command timed out")
```

which propagates straight out of the trigger evaluator — i.e. through
`assert evaluator({...})["decision"] == "execute"` (`test_platform_only_startup.py:229`), and
before the `worker_pid != os.getpid()` assertion at `:241`. Under a 1,494-test suite the host is
loaded and a cold-start subprocess importing the transitive graph can plausibly exceed 10s;
alone it never does. That matches the observed pattern exactly (always passes in isolation,
~50% in a full run).

**Confidence: strongly indicated, NOT confirmed.** The signature above was produced by *forcing*
a timeout, not captured from a natural failure.

**Why no natural traceback exists yet — a process mistake worth not repeating.** All three
observed failures happened in runs invoked as `pytest tests/unit/ -q ... | tail`, which
discarded the traceback and kept only the summary line. By the time that was noticed and runs
switched to `--tb=long` writing to a file, the flake did not recur: **2 capture attempts, both
clean** (a third was killed). So the missing evidence is not a hard-to-reproduce failure — it is
three failures whose output was thrown away. Full tally: **7 completed runs, 3 fail / 4 pass**.

**To confirm:** loop `python -m pytest tests/unit/ -q --tb=long > cap.txt` — *never piped to
`tail`* — until `cap.txt` names the test, then check whether the traceback is the `RuntimeError`
above. If it is something else, this section is wrong and the mechanism is elsewhere.

**One alternative already eliminated:** stale `_runtime_callback_invocations` leaking in from a
prior test. The `platform_only_runtime` fixture snapshots and restores **57 registry globals**
including `_runtime_callback_invocations`, plus `TOOL_REGISTRY` and `_SUGGESTION_PROVIDERS`. The
fixture is thorough; leak-into-fixture is not the cause.

**Blast radius is two files, not one.** `tests/unit/test_extension_ownership.py` asserts on the
same subprocess boundary (15 `worker_pid` / `isolated-runtime-callback` assertions between the
two). Both are `runtime_only`; both now gate every merge. It has not been observed flaking yet,
but it is exposed to the identical race.

**This reframes FR-11.** The app team filed the 10s budget as *hardening, not a defect*, on the
grounds that it is "cold-start only, 0 recurrences warm". If the mechanism above holds, that is
too generous: it is not a theoretical cold-start concern but an intermittent failure in a
required check. FR-11's fix — make the budget configurable, and raise it — would close this too.

**Fix options, in preference order:**

1. **Make the budget configurable and raise it** (= FR-11). Fixes the class, not just the test.
   Neither `registry.py` call site overrides the default and no env key exposes it today.
2. Give these two test files a generous explicit timeout so a loaded host does not fail them,
   independent of the production default.
3. Retry the assertion. **Rejected** — it hides the very signal FR-11 needs, and a retry in a
   required check trains people to ignore red.

Do **not** "fix" this by removing the `runtime_only` marker. That is `CI-MARKER-1` in reverse:
it would make the check green by making it not run.

---

## RTR-* — Runtime Roadmap (Nodus-first execution & runtime primitives)

**Status:** Open — roadmap (not classic debt). Priorities per item below.

**Provenance.** Consolidated from the five app-side evolution docs (AGENTICS,
Reasoning, RippleTrace, …) where work was flagged "runtime-gated" or
"runtime-owned" while the app layer was built. Every claim below was
**validated against the live source tree on 2026-06-29** by three code-mapping
passes (Nodus substrate; worker model + agent execution; multi-agent / autonomy
/ memory / causality). File:symbol evidence is cited inline.

**Ownership lens.** The runtime is "kernel primitives + registration surfaces;
apps extend without editing runtime" (see `DB_OWNERSHIP_CONTRACT.md`,
`MODEL_OWNERSHIP_POLICY.md`). Discriminator used throughout: **runtime owns the
mechanism / primitive / registration surface; the app owns the policy /
semantics** (which events are significant, which triggers fire, ranking weights,
the content-domain causal graph). Every item below passes that test as
runtime-owned (or runtime-half of a split). Validation confirmed several items
are **"finish/promote what exists," not "build from scratch"** — flagged per
item as **[BUILD]** (mostly greenfield) vs **[HARDEN]** (substantial prior work
in-tree).

### RTR-1 — Nodus as primary execution substrate — **CLOSED 2026-07-07**

**CLOSED 2026-07-07.** All four defined runtime gaps are resolved: (a) the
`register_nodus_workflow` registration + discovery surface (Phase 1); (b) the
VM-backed agent adapter + agent-plan→`.nd` compiler that retires the static
`AGENT_FLOW` interpretation (Phase 2a–2e, PG-validated); (c) `.nd` asset handling
— the two stale cross-machine `.nbc` build-droppings were removed and the dir is
gitignored, with the *managed* content-hash bytecode cache explicitly kept as
roadmap (Phase 3), not a close-blocker; (d) the dead `NodusTraceEvent` trace path
**dropped** this session (Alembic `0009`; see the trace-path resolution below).
The keystone "apps finish phases without editing runtime" hook (`register_nodus_workflow`)
is live. **Not in scope for this close:** flipping `nodus_vm` to the default /
retiring `AGENT_FLOW` — that remains opt-in behind `AINDY_AGENT_EXECUTION_BACKEND=nodus_vm`
pending the app-side soak listed under "Remaining follow-ups" (app-tool execution in
the monolith, multi-instance agent resume, subprocess-per-segment perf), and the
AgentRun↔FlowRun unification tracked separately as **RTR-3**.

The historical build log (Phases 1–2e, durability, real-PG parity/soak, #152/#157)
is retained below for provenance.

**Design (2026-06-29):** the `register_nodus_workflow` registration surface
(item (a) below) is specified in `docs/runtime/NODUS_WORKFLOW_CONTRACT.md` —
Phase 1 = registration surface + `nodus_workflows` table + boot rehydration +
run-by-name (both `flow-graph` and `script` kinds); Phase 2 = agent-plan→`.nd`
+ VM-backed agent adapter; Phase 3 = bytecode cache + `NodusTraceEvent`
wire-or-drop. Implementation pending against that contract.

**Phase 1 landed (2026-06-29):** `register_nodus_workflow` surface
(`AINDY/runtime/nodus_workflow_registry.py`) — imperative + declarative
(`nodus-workflow` manifest kind), `nodus_workflows` source table (schema-contract
bumped to `2026-06-29`, Alembic `0006`), boot rehydration in `startup.py`,
`run_nodus_workflow` by name, both kinds. Mirrors `register_dynamic_flow`
(owner-class + provenance gating). 14 unit tests.

**Phase 2a landed (2026-06-30) — tool-calling seam.** Discovered the foundational
gap: AINDY tools were **not reachable from inside a Nodus workflow** at all. The
native `action tool "x"` construct lowers to nodus's built-in `__action_tool` →
its own 4-tool stub registry with **zero** capability enforcement, and those VM
builtins **cannot be overridden** (`register_function` raises). Fix: a new
`call_tool(name, args)` host function (`AINDY/runtime/nodus_worker.py` →
`run_agent_tool`) bridges the VM to `tool_registry.execute_tool` with full
capability-token enforcement — **fail-closed** (no token → refused before the
tool). `run_id` + `execution_token` thread through `NodusExecutionContext` →
worker payload (`nodus_runtime_adapter.py`). Now any Nodus workflow/script (RTR-1
flow-graph/script *and* the future agent path) can call AINDY tools with
enforcement. 7 unit tests (in-process — helper enforcement + payload threading;
no subprocess dependency). Docs: `NODUS_DEVELOPER_GUIDE.md` (`call_tool`).
**Phase 2b landed (2026-06-30) — agent-plan → `.nd` compiler.**
`compile_agent_plan(plan)` (`AINDY/runtime/agent_plan_compiler.py`) turns a flat
agent plan into a native Nodus `workflow {}` — one `step step_N after step_{N-1}`
per plan step — each calling `call_tool(get_state("__step_N_tool"),
get_state("__step_N_args"))` (the Phase 2a seam). Tool names + args pass via run
**state**, never embedded as source, so no planner/LLM-derived value becomes code
(**injection-safe**). Returns `{source, workflow_name, state_inputs, steps}`; the
`steps` metadata (index, tool, risk_level, description, `result_key`) is the
contract for 2c to map each `__step_N_result` from output state back to an
`AgentStep` row. Standalone module — **not** wired into `execute_run` yet (that's
2c). 11 unit tests incl. injection-safety + end-to-end VM execution in order.
(NB: 2c renamed the compiler's `state_inputs` → `input_payload` — args ride the
`nodus.execute` node's `input_payload` channel, which is forwarded to the script;
the `state` namespace is isolated.)

**Phase 2c landed (2026-06-30) — opt-in VM-backed agent path (Core MVP).**
`execute_agent_run_via_workflow` (`nodus_execution_service.py`) compiles the plan
(`compile_agent_plan`) and runs it via the canonical flow-backed Nodus path
(`run_nodus_script_via_flow`), so each step's tool call goes through the
capability-enforced `call_tool` seam. `execution_token` + `agent_run_id` thread
via `extra_initial_state` → flow state → the `nodus.execute` node → the execution
**context** (never the script namespace); `execute_nodus_runtime` gained
`execution_token`/`run_id` params. `AgentStep` rows, status/counters, result, and
capability/completion events are reconstructed from the workflow's output state
(`reconstruct_agent_step_results`). Selected via
`AINDY_AGENT_EXECUTION_BACKEND=nodus_vm`; **`AGENT_FLOW` stays the default**. 8
unit tests (reconstruction, selector routing, e2e run with mocked flow+capability
+ real sqlite AgentRun).

**Phase 2d landed (2026-07-03) — per-step retry + halt-on-first-failure.**
`compile_agent_plan` now emits, per step: a tool call, an in-step **retry loop**
(`max_attempts` resolved at compile time from `risk_level` via
`resolve_retry_policy` — low/med 3, high 1), a non-transient short-circuit
(`is_retryable_error` host function, new in `nodus_worker.py`), and a
**`throw`-on-final-failure**. The throw is what gives halt: a native `workflow {}`
step that raises fails its task, and the task graph never schedules the dependent
`after` steps — so no step runs on a predecessor's bad output (parity with
AGENT_FLOW's `FAILURE`-halts-the-flow). The failing step still records its result
via `set_state` before throwing, so reconstruction sees it; a trailing absent
`__step_N_result` now means *halted*, not *dropped*. **Design note:** the native
step `retries` option is deliberately NOT used — in nodus's runner it is a
*durable* retry (`status: retry_scheduled`, needs a resume call) that would strand
the single-shot VM path; the in-step loop keeps retry synchronous. Validated
in-process (VM semantics identical to the subprocess path, which the Windows dev
box blocks — WinError 4551): 12 new/updated unit tests covering attempt budgets,
halt, retry-to-exhaustion, retry-recovery, and non-retryable short-circuit.
**Phase 2e landed (2026-07-03) — mid-plan WAIT/RESUME (segment-split, live-process).**
Mid-plan wait is **net-new** — the default AGENT_FLOW path has no wait at all
(steps only return SUCCESS/FAILURE; no `waiting` AgentRun status). Chosen design:
**segment-split**, not single-workflow-suspend. A plan may now carry WAIT steps
(`{"wait_for": "<event.type>", "correlation_key"?: str}`); `split_agent_plan`
cuts the plan into segments at those boundaries (`compile_agent_segment` keeps
global `step_N`/`__step_N_result` indices contiguous across segments). The
executor runs one segment per invocation: on success with a trailing wait it
parks the run at `status="waiting"` and registers a scheduler wait whose resume
callback runs the *next* segment when the event fires. **Completed segments are
never re-run** — their `AgentStep` rows are durable, so tool calls never fire
twice (this is why segment-split was chosen over the flow engine's
re-execute-from-top resume, which would replay prior tool calls). Resume is
idempotent via a `waiting → executing` check-and-set. Why not the two obvious
mechanisms: plain-nodus `event.wait()` raises inside a native `workflow {}` step
→ caught by the task graph as a step *failure*, not a wait; and a native workflow
wait returns a normal dict → invisible to the worker/flow engine.

Initial increment was **live-process durability**: the wait rides the scheduler's
in-memory `_waiting`/`notify_event` path; `_persist_wait_backup` skips the
`WaitingFlowRun` FK-backup for `eu_type != "flow"` (agent waits). New AgentRun
status `"waiting"` (added to `ACTIVE_AGENT_RUN_STATUSES`); the EU is mirrored to
completed/failed on resume-terminal via `_sync_agent_eu_status`.

**Cross-restart durability landed (2026-07-04).** A waiting agent run now survives
a process restart. New durable `AgentRun.wait_state` JSONB column (schema bump
`2026-07-04`, Alembic `0007`) holds `{event_type, correlation_key,
resume_segment_index}`, set on park and cleared on resume/terminal. Everything
else needed to rebuild the resume is already durable: `plan` → segments,
`result["steps"]` → accumulated results, `capability_token` → the self-verifying
scoped token (reloaded; re-mint only needed past its 23h TTL). `rehydrate_waiting_agent_runs`
(`AINDY/core/agent_run_rehydration.py`) mirrors `rehydrate_waiting_flow_runs` —
queries `status="waiting"` runs and re-registers each scheduler wait from durable
state; hooked into `startup.py` Phase 14 between FlowRun and Nodus rehydration
(before `mark_rehydration_complete`/`drain_buffered_events`, so boot-buffered
events reach the fresh callbacks), guarded by `RuntimeConditionCode.AGENT_RUN_REHYDRATION_FAILED`.
The live-register and rehydration paths share one resume builder
(`_build_agent_resume_callback`) whose closure does an **atomic** `waiting →
executing` claim (`UPDATE … WHERE status='waiting'`), so a duplicate event-fire,
watchdog re-trigger, second rehydration, or multiple instances can't resume twice.
Validated in-process: wait→resume cycle (no re-run of prior steps), resume-failure,
double-fire idempotency, no-wait regression, durable `wait_state` persist/clear,
and restart-rehydration (fresh scheduler → re-register → event → resume →
complete, step 0 not re-run) + skip-guards.

**Planner WAIT steps + resume/approval landed (2026-07-04).** `planning.py`
documents the WAIT-step schema (`{"wait_for": "<event.type>"}`), excludes WAIT
steps from `overall_risk` aggregation, and `apply_wait_policy` reconciles them to
the execution backend: **stripped** on `agent_flow` (which can't execute a wait —
safety), and on `nodus_vm` with the new opt-in `AINDY_AGENT_WAIT_BEFORE_HIGH_RISK`
setting, a human-approval WAIT (`AGENT_APPROVAL_EVENT = "agent.approval.granted"`)
is **inserted before the first high-risk step**. The resume/approval action is
`resume_agent_run_runtime` (`AINDY/agents/runtime_api.py`): it reads the run's
`wait_state`, resolves the correlation the same way the register/rehydrate paths
do (`wait_state.correlation_key or run.correlation_id` — a latent trace_id-fallback
mismatch in the rehydrator was fixed to match), and calls `publish_event` to fire
the resume. A reference route `POST /agent/runs/{id}/resume` was added to the
(deprecated) runtime `agent_router.py`. Tests: policy strip/insert/disabled +
resume publish/correlation/404/409 (`test_agent_wait_policy.py`).

**Capability token refresh on resume landed (2026-07-04).** A run parked on a WAIT
across a long wait / restart could have a `capability_token` past its 24h TTL by
the time the event fires — its tools would then fail validation. The resume
callback (`_build_agent_resume_callback`) now, after the atomic claim, checks
`capability_service.token_is_expired` and, if lapsed, calls
`capability_service.refresh_token`: it rebuilds the token on a fresh clock (new
`execution_token`/`issued_at`/`expires_at`/`token_hash`) while **reusing** the
token's existing `granted_tools`/`allowed_capabilities`/`approval_mode` verbatim —
no plan re-derivation, no policy re-evaluation, no escalation — and persists it to
`AgentRun.capability_token`/`execution_token`. Applies to both the rehydration and
long-lived live-wait cases (it runs in the shared resume closure). Non-fatal: if
refresh can't rebuild the token it falls back to the original (fails cleanly as
before). Tests: `test_capability_token_refresh.py` (expiry/refresh/validate) +
resume-refreshes-expired-token e2e in `test_agent_vm_execution.py`.

**Real-Postgres parity validation landed (2026-07-04) — and uncovered + fixed THREE
latent bugs that the mocked unit tests + Windows subprocess block had hidden since
2c.** The `nodus_vm` agent path had never actually run end-to-end (unit tests mock
`run_nodus_script_via_flow`; Windows blocks the subprocess). Driving it on real PG
exposed:

1. **Engine-boundary reject (the path never ran at all).** `run_nodus_script_via_flow`
   calls `enforce_engine_boundary(entrypoint="nodus.run")`, which rejects any
   `workflow_type` without "nodus" in it as a Python-DAG flow. The nodus_vm path
   passed `workflow_type="agent_execution"` → **every real invocation raised**. Fix:
   the nodus_vm path (which IS nodus-backed) now uses `"nodus_agent_execution"`.
   (AGENT_FLOW keeps `"agent_execution"` — it uses the `flow.run` entrypoint.)
2. **No runtime tools in the subprocess.** `execute_tool` → `_ensure_tools_loaded` →
   `load_plugins()` registers nothing for runtime tools (the runtime manifest has no
   plugins); `memory.read`/`memory.write` are only registered by
   `_ensure_runtime_agent_defaults`, which fired in the parent at startup but never in
   the subprocess → every runtime-tool call returned "Tool not found". Fix:
   `_ensure_tools_loaded` now also calls `_ensure_runtime_agent_defaults` (idempotent;
   app deployments unaffected).
3. **Wait-plans couldn't be approved.** `get_grantable_tools` returned `[]` on a WAIT
   step (`tool=None`) → `mint_token` returned None → no wait-containing plan could be
   approved. Fix: skip non-tool steps in `get_grantable_tools`
   (`get_plan_required_capabilities` already did).

Validation: `tests/integration/test_agent_vm_parity.py` (marker `integration`, real PG
+ real subprocess) — success parity (both backends complete a `memory.recall` plan
with identical AgentRun/AgentStep outcomes), failure parity (invalid token denied
identically at the flow gate), and a `nodus_vm`-only durable **WAIT→RESUME** cycle on
Postgres (segment 0 executes, run parks with `wait_state`, fired resume runs segment
1, step 0 not re-run). Windows blocks the subprocess, so this suite is authoritative
on Linux CI. Regression guards for #1 and #3 added to the unit suite.

**Soak — real-PG retry/halt validation (2026-07-04).** The parity suite's failure
case was only capability-denial at the flow gate; a real mid-plan TOOL failure had
never run through the subprocess. Added a runtime **diagnostic tool**
`runtime.selftest` (`runtime_agent_defaults.py`) — executable + capability-wired
(`runtime_selftest` cap) but excluded from the planner catalog (`category="diagnostic"`)
— that echoes a caller-requested outcome and, on failure, raises an error carrying an
`(attempt N)` counter (module-level per process = per subprocess/segment). New
integration tests (`test_agent_vm_parity.py`) drive, on real PG through the real
subprocess: tool-failure parity (both backends → run failed, failed AgentStep),
halt-on-first-failure parity (downstream step never runs), and nodus_vm retry
behavior — retryable → 3 attempts, non-retryable ("permission") → 1 attempt,
high-risk → 1 attempt (all read from the recorded step error). Unit guards for the
tool + its catalog exclusion added.

**Soak — real scheduler resume + rehydration-across-restart on PG (2026-07-04).**
The wait/resume test patched the scheduler to fire the callback; two integration
tests now drive the **unpatched** production path on real PG (the env has no
scheduler loop, so the queued resume is drained explicitly via
`dequeue_next().run_callback()`, as `test_multi_instance_resume` does): (1)
`resume_agent_run_runtime` → `publish_event` → real `notify_event` correlation match
→ the agent resume callback → segment resumes and completes; (2) a parked run whose
in-memory registration is wiped (restart) is re-registered by
`rehydrate_waiting_agent_runs` from the durable `AgentRun` row, then a published event
resumes it. Both confirm the correlation resolves consistently
(`wait_state.correlation_key or run.correlation_id`).

**Remaining follow-ups:** (a) **wire the LIVE resume route in the monolith**
(`aindy-apps-monolith` `apps/agent/routes/agent_router.py`) calling
`resume_agent_run_runtime` — the runtime `agent_router.py` is deprecated/unregistered,
so the app-owned route is the real surface; must land AFTER the runtime package is
bumped/reinstalled in the monolith so the import resolves. (b) Remaining soak before
making `nodus_vm` the default / retiring `AGENT_FLOW`: **app-tool** execution under
`nodus_vm` in the monolith (validated here only with runtime-native tools — the #1
cross-repo unknown); multi-instance resume for agent runs; and subprocess-per-segment
perf. The VM path stays opt-in/non-default until then.

**#152 — CLOSED 2026-07-04: resumed segment ran outside an execution context.**
Live-Postgres execute-to-completion validation in the monolith surfaced that the
scheduler-driven resume callback (`_build_agent_resume_callback._resume`) ran the resumed
segment with **no `ExecutionPipeline` wrapper** — the initial run inherits its context from
the request pipeline, but the resume fires from the scheduler (event notify / resume
watchdog / cross-restart rehydration). With `is_pipeline_active()` False for the whole
segment, the flow runner's `execution.started` (and other `execution.*` events) tripped the
ExecutionContract guard under the default `ENFORCE_EXECUTION_CONTRACT=True`, stranding the
run at `executing`. Delivery (a running scheduler) does **not** fix it — the callback runs
inline regardless. **Fix:** `_resume` now activates the async-execution context (the same
signal the flow runner uses for background execution) around `_execute_agent_segment_chain`,
so the guard accepts the resumed chain's execution events exactly as the pipeline accepts the
initial run's. Regression: `test_resume_callback_runs_within_async_execution_context` (the
prior resume tests mocked event emission, so the guard was never exercised). The monolith §5
CI job proves full execute-to-completion once the runtime bump ships.

> **RTR-1a — CLOSED 2026-06-29.** The pre-4.x `flow.step()` host-object DSL
> collided with nodus-lang 4.0.5's reserved `step` keyword (and 4.x doesn't
> support host-object method calls at all), so the `flow-graph` kind couldn't
> compile real scripts. **Resolution:** adopt nodus-lang's **native
> `workflow {}` / `goal {}` construct** (4.x ships a first-class `orchestration/`
> workflow feature). `compile_nodus_flow` (`AINDY/runtime/nodus_flow_compiler.py`)
> now parses that construct and extracts the step dependency DAG (no execution);
> `flow-graph` workflows execute natively by appending `run_workflow(<name>)` /
> `run_goal` and running through the shared `nodus_execute` flow. The retired
> `flow.step()` node-wiring intent is already covered by `register_dynamic_flow`,
> so nothing is lost. The dead `nodus.flow.compile` node + `POST /platform/nodus/flow`
> route were repointed to the new model; the `nodus.flow.compile→run` chain is
> deprecated. Both kinds now fully working. Tests: `test_nodus_flow_compiler.py`
> (incl. end-to-end VM execution in dependency order) + updated
> `test_nodus_workflow_registry.py`.

**Gap resolution (the four original runtime gaps, all closed):**
- **(a) registration surface — DONE (Phase 1).** `register_nodus_workflow`
  (`AINDY/runtime/nodus_workflow_registry.py`) — imperative + declarative
  (`nodus-workflow` manifest kind), `nodus_workflows` source table, boot
  rehydration, `run_nodus_workflow` by name, both `flow-graph` and `script` kinds.
  Apps register/select workflows without runtime edits.
- **(b) VM-backed agent adapter + plan→`.nd` — DONE (Phase 2a–2e).**
  `compile_agent_plan` (`agent_plan_compiler.py`) generates a native `workflow {}`
  from an agent plan (injection-safe — tool names/args ride run state, never
  source); `execute_agent_run_via_workflow` (`nodus_execution_service.py`) runs it
  through the capability-enforced `call_tool` seam via the canonical flow-backed
  Nodus path. Retry/halt, mid-plan WAIT/RESUME with cross-restart durability, and
  cap-token refresh on resume all landed and were validated on real Postgres.
- **(c) `.nd` asset handling — DONE for the close; managed cache is roadmap.**
  The two stale cross-machine `.nbc` build-droppings under
  `AINDY/nodus/stdlib/.nodus/cache/` were `git rm`'d (dir already gitignored). The
  *managed* content-hash-keyed bytecode cache + `.nd` version-rollback API remain
  **Phase 3 roadmap** (NODUS_WORKFLOW_CONTRACT.md §11), not a close-blocker.
- **(d) dead trace path — DROPPED (2026-07-07).** `NodusTraceEvent` was never
  wired: `_flush_nodus_traces()` had zero call sites and the worker produced no
  per-fn records, so no row was ever written. Removed the model, reader
  (`nodus_trace_service.py`), `GET /platform/nodus/trace/{trace_id}` route, the CLI
  `trace` command + `--trace` flag, the dead duplicate trace fns in
  `runtime/__init__.py`, and the orphaned `_sanitize_args`/`_sanitize_result`
  helpers. Schema-contract bumped `2026-07-05`→`2026-07-07`; Alembic `0009` drops
  the table (idempotent, blank-DB safe; downgrade recreates). Rationale: execution
  observability is already canonical via `SystemEvent` (`source="nodus"`) +
  `EventEdge` (RTR-7), surfaced by `GET /observability/execution_graph/{trace_id}`;
  the per-function table only duplicated it at finer grain.
- **Live Nodus VM runs are not a side path** — they go through
  `PersistentFlowRunner` → create a `FlowRun`, link `AgentRun.flow_run_id`, and emit
  `SystemEvent`s (`source="nodus"`) on the canonical bus.

### RTR-2 — Durable worker model — **[HARDEN], high**

- **Evidence:** `core/distributed_queue.py` — `RedisQueueBackend` is real,
  production-grade (atomic `LPUSH`/`BRPOP`, `aindy:jobs:inflight` visibility-timeout
  hash, delayed ZSET + Lua promotion, DLQ, circuit breaker, capacity Lua,
  `requeue_stale_jobs`). `worker/worker_loop.py` is a real separate-process
  consumer with a DB-side atomic claim (`_try_claim_job`) preventing
  double-execution. `platform_layer/leadership.py` `BackgroundLeadershipElector`
  (lease in `background_task_leases`) is enforced (LEASE-1, closed). `JobLog` +
  `ExecutionUnit` rows persist job intent **before** submission.
- **Current state:** durable path **exists and is well-built**, but is **opt-in**
  behind `EXECUTION_MODE=distributed` + `REDIS_URL`. Default is in-process
  `ThreadPoolExecutor` (`async_job_service._distributed_execution_enabled()` →
  `"thread"`). Prod guards already raise without Redis (`settings.is_prod`).
- **The gap:** flip distributed to the prod default (partial mitigation already
  via prod overlay — see **SYSMAX-1**); add per-tenant queue isolation (today is
  count-based admission via `AINDY_ASYNC_MAX_CONCURRENT_*`, **not** isolated
  lanes); close the thread-mode in-flight loss (record survives, execution does
  not). **Related:** SYSMAX-1, TIER3-10 (`async_job_service` coupling), LEASE-1.

- **Advance 2026-07-08 — gaps 1+2 done; per-tenant lanes deferred.**
  **(1) prod default flipped.** New `config.resolve_execution_mode()` is the single
  source for the transport decision (replaced 3 duplicated
  `os.getenv("EXECUTION_MODE","thread")` reads in `async_job_service`,
  `execution_dispatcher`, `distributed_queue`): an explicit `EXECUTION_MODE` always
  wins, but when unset **production now defaults to `distributed`**, so a prod deploy
  that forgets it fails fast at `get_queue` (raises without `REDIS_URL`) instead of
  silently running lossy thread mode. `.env.example` documents the prod default; the
  startup advisory updated. **(2) thread-mode in-flight loss closed.**
  `platform_layer/job_recovery.py` `recover_orphaned_thread_jobs()` re-dispatches
  `JobLog` rows stranded in `pending`/`running` — **at startup only** (the sole safe
  moment: no live futures exist, so every such row is definitionally orphaned from
  the dead process; a periodic scanner can't tell a long-running job from a crashed
  one because `_ACTIVE_FUTURES` isn't log-keyed). No-op in distributed mode (worker
  `requeue_stale_jobs` owns it). Wired beside the `stuck_run_service` startup scan.
  Tests: `test_rtr2_durable_worker.py`. **Deferred: per-tenant queue lanes** — new
  Redis-key-per-tenant + worker fan-out infra, only meaningful once multi-tenant
  SaaS is real (**DEPLOY-TARGET-2**); count-based admission
  (`AINDY_ASYNC_MAX_CONCURRENT_*`) remains the interim isolation. No schema change.

### RTR-3 — Agent execution integrity — **[HARDEN/BUILD split], high**

- **Evidence:** two records with a one-directional, **nullable, post-hoc** link.
  `AgentRun` (`db/models/agent_run.py`) `flow_run_id` is
  `ForeignKey(..., ondelete="SET NULL"), nullable=True`; `execute_run`
  (`agents/agent_runtime/execution.py`) → `execute_agent_run_via_nodus`
  (`nodus_execution_service.py:368`) creates the `FlowRun` first and
  **back-patches** `agent_run.flow_run_id` after. Reconciliation is convention-
  based and guarded on the literal string `status == "executing"` (both forward
  and in `stuck_run_service._recover_agent_run`, which drives from the FlowRun
  side). **`FlowRun` is the de-facto authority; `AgentRun` is a mirrored projection.**
- **Current state:** lifecycle hardening is **substantially done** — multiple
  DB-driven recovery scanners (`stuck_run_service.scan_and_recover_stuck_runs`,
  `core/flow_run_rehydration.rehydrate_waiting_flow_runs` with atomic-claim
  `UPDATE flow_runs SET status='executing' WHERE id=? AND status='waiting'`,
  `core/resume_watchdog`, scheduler orphaned-approved recovery). Queued/waiting/
  failed states are fully inspectable and resumable without process memory.
- **The gap:** one authoritative execution-record path — unify the
  `AgentRun` ↔ `FlowRun` state machines (single authority / shared enum;
  non-nullable, non-post-hoc link) so divergence is impossible (today an AgentRun
  in any state other than `"executing"` silently no-ops recovery). Exact-position
  resume of mid-node `running` work is out of scope (today: fail + reconstruct
  from `AgentStep`, or replay fresh via `replayed_from_run_id`); thread-mode
  in-flight overlaps RTR-2.

- **Advance 2026-07-08 — HARDEN half done (canonicalization); BUILD half (full
  unification) still deferred.** The decorative enums in `kernel/condition_codes.py`
  now mirror what the engines actually write: `FlowRunStatus` gained `EXECUTING`
  (resume-claim / active-stepping) and `SUCCESS` (real terminal success — the
  runner never wrote the old `COMPLETED`, now kept as a legacy alias);
  `AgentRunStatus` gained `WAITING` (the VM WAIT park state that
  `agent_run_rehydration` already queried). Added the single-source classification
  layer — `AGENT_TERMINAL_STATUSES` / `FLOW_TERMINAL_STATUSES` /
  `AGENT_ACTIVE_STATUSES` / `FLOW_ACTIVE_STATUSES`, `is_agent_terminal` /
  `is_flow_terminal`, and deterministic `flow_status_to_agent` /
  `agent_status_to_flow` maps. **No-op recovery gap CLOSED:** the six reconcilers
  (`stuck_run_service`, `recovery_jobs`, `flow_run_rehydration`,
  `resume_watchdog`, `kernel/scheduler/recovery`, `agent_run_rehydration` +
  scheduler orphaned-approved) now classify via the helpers instead of the
  literal `status == "executing"`, so a stuck FlowRun whose linked AgentRun is
  `delegated` / `waiting` is failed instead of stranded. Both stuck-flow scans now
  cover `executing` (crash mid-step), not just `running`. Added
  `ix_agent_runs_flow_run_id` (the reconciliation join was an unindexed scan) —
  schema-contract `2026-07-07`→`2026-07-08`, Alembic `0010` (idempotent, blank-DB
  guarded). Tests: `test_run_status_canonicalization.py`. The nullable, post-hoc
  `AgentRun.flow_run_id` link and the two independent state machines are unchanged
  — full unification (non-nullable link, run-creation reorder, single authority)
  is the remaining BUILD half, deferred (trigger unchanged: when divergence is
  observed in production).

### RTR-4 — Multi-agent delegation core — **[HARDEN], medium**

- **Evidence:** **working core, not scaffolding.** `db/models/agent_registry.py`
  `AgentRegistry` is a persisted table; `agents/agent_coordinator.py` has real
  `register_or_update_agent`, `_rank_candidate_agents` (weighted
  `coordination_score`), and `dispatch_delegated_run` (creates child `AgentRun`
  with `parent_run_id` / `spawned_by_agent_id`, sets parent `status="delegated"`).
  `agents/runtime_guardrails.enforce_delegation_guardrails` enforces max depth
  (3), max children (8), cycle detection. `capability_service.mint_token` mints a
  **fresh scoped, hash-sealed, TTL-bounded token per child run**.
  `agents/agent_message_bus.py` is a SystemEvent-backed bus
  (`operation_request`/`operation_result`/`memory_share`) with
  `acknowledge_message`.
- **The gap:** (a) inter-agent **approval handshake** — today it's
  acknowledgement-only, no accept/reject/negotiate contract; (b) **independent
  per-delegate capability narrowing** — the child token currently inherits the
  parent's plan/agent_type rather than re-deriving a tighter scope; (c)
  **delegation-token-scoped private memory** — boundaries today are namespace +
  `is_shared` flags + MAS path isolation, not bound to the delegation token.

- **Advance 2026-07-08 — gaps (a) + (b) done; (c) deferred.**
  **(b) capability narrowing (active by default, security):** `mint_token` gained
  a `capability_ceiling` param; `dispatch_delegated_run` now mints the child token
  under the *delegate's* `agent_type` and clamps `allowed_capabilities` /
  `granted_tools` to `parent_grant ∩ delegate_registry_capabilities`
  (`_delegate_capability_ceiling`). No escalation via delegation; the delegated
  plan's tool capabilities always survive the intersection (parent ran the same
  plan), so only extraneous caps are dropped. **(a) approval handshake (opt-in,
  `AINDY_DELEGATION_HANDSHAKE`, default off):** new bus message types
  `operation_accept` / `operation_reject` + publishers; new non-terminal
  `AgentRunStatus.AWAITING_DELEGATION`; `respond_to_delegation(child, accept)`
  promotes an awaiting child to `approved` (execution proceeds via the normal
  approved path) or fails it — and un-hangs the waiting parent on reject
  (`delegation_rejected`). Default-off preserves today's fire-and-forget
  `approved` dispatch. Tests: `test_delegation_hardening.py`.
- **(c) token-scoped private memory — SHIPPED 2026-07-12/13** (PR1 #245 helper
  centralization, PR2 #246 `owner_run_id` + ContextVar chokepoint). This entry and the
  `CLAUDE.md` prefix registry both said "deferred" until 2026-07-31; corrected after a
  roadmap audit found the flag live in the source. Gated `AINDY_DELEGATION_PRIVATE_MEMORY`
  (`config.py:342`, default off), enforced at `memory_persistence.py:136/174` with the
  owner threaded from `execution.py:214`. Remaining work is soak-then-flip, not build.
  **Delegate writes take the DEFERRED capture path, so `MemoryNodeDAO.save` — not the
  syscall — is the write chokepoint.**
- **Still deferred:** wiring `respond_to_delegation` to an HTTP route / syscall (it ships
  as an importable runtime primitive, record-first).

### RTR-5 — Autonomous closed loop — **[BUILD], medium (split)**

- **Ownership:** runtime owns the missing execution-window primitive; the
  decision **policy** is app-owned and already tested.
- **Evidence:** `agents/autonomous_controller.py` is **evaluate-and-gate only** —
  `evaluate_trigger` returns `{execute|defer|ignore}` via an app-registered
  evaluator (`get_trigger_evaluator`); there is **no planning and no execution
  call** in the controller. `async_job_service` does the gating/scheduling
  (`defer_async_job` / `submit_async_job` + saturation→60s defer;
  `process_deferred_jobs` re-evaluates). `AutonomyDecision` is an app-layer model
  (absent in standalone runtime).
- **The gap:** a bounded, **runtime-driven trigger → plan → execute window** with
  policy enforcement and loop-scheduling primitives in the controller. Today apps
  raise triggers and the runtime only evaluates/defers/queues — there is no
  controlled runtime-driven execution window.

- **Advance 2026-07-08 — execute-window primitive shipped (opt-in, default off).**
  `agents/autonomous_window.py` `run_execute_window(db, *, user_id, objective, ...)`
  is the missing primitive: a **bounded** loop that composes the existing
  primitives — `evaluate_live_trigger` (policy stays app-owned) → `create_run`
  (which already plans + auto-approve/mints) → `execute_run` — under guardrails:
  `AINDY_AUTONOMOUS_MAX_ITERATIONS` (default 3), an active-run admission cap
  (`AINDY_AUTONOMOUS_MAX_ACTIVE_RUNS` via `count_active_executions`), and an
  optional inter-iteration cooldown (capped 30s). Human approval is respected — a
  `pending_approval` run ends the window (never force-executed). Gated behind
  `AINDY_AUTONOMOUS_EXECUTE_WINDOW` (default off): disabled ⇒ no-op, current
  evaluate/defer/queue behavior unchanged. Registered as async job
  `agent.autonomous_window` (force-imported in `async_job_service`, mirroring
  `embedding_jobs`) so the queue/defer path can dispatch it — closing the gap that
  no runtime handler existed for an autonomous `execute` decision. New event
  `SystemEventTypes.AUTONOMY_WINDOW` (started/completed); frozen event baseline
  regenerated (`01030e3f3fdc5e8d`). Tests: `test_autonomous_window.py`. No schema
  change. **Deferred:** flipping the flag on after soak; routing the existing
  autonomous `submit`/`process_deferred_jobs` `execute` branch through the window
  by default.

### RTR-6 — Reasoning at the memory layer — **[BUILD], medium**

- **Evidence:** recall/capture are real (`runtime/memory/orchestrator.py`
  `MemoryOrchestrator.get_context` recall pipeline; `memory/memory_capture_engine.py`
  `evaluate_and_capture` significance-scored capture). But memory-derived signals
  are emitted as **ordinary `SystemEvent`s** (`MEMORY_WRITE`, `AUTONOMY_DECISION`)
  carrying `impact_score` / `memory_type` in the payload, plus columns on
  `MemoryNode`. There is **no `ReasoningEvent` model and no `reasoning.*` event
  type** (grep of `core/system_event_types.py` is empty for reasoning).
- **The gap:** standardize memory-derived signals as **first-class reasoning
  inputs** in `runtime/memory/orchestrator.py` + `memory_capture_engine.py`;
  optionally add a dedicated reasoning event model and richer emission from
  `agent_runtime` / `nodus_adapter` if `SystemEvent` payload conventions get too
  loose.

- **Advance 2026-07-08 — first-class capture signal shipped (event-row-as-record,
  no model).** New `SystemEventTypes.REASONING_SIGNAL = "reasoning.signal"`
  (un-prefixed, like the Infinity ledger events) + `core/reasoning_signal.py`
  `emit_reasoning_signal(kind, payload)` (best-effort, never raises). Wired at
  `memory_capture_engine.evaluate_and_capture`: on every stored insight it emits
  a `kind="capture"` signal carrying the reasoning attributes that were otherwise
  implicit in the `MEMORY_WRITE` payload / `MemoryNode` columns —
  `{node_id, node_type, memory_type, impact_score, causal_depth, significance,
  event_type}`. **Recall was deliberately left alone:** recall inputs are already
  first-class via `RECALL_USED` (INFINITY-RUNTIME-1), so `emit_recall_used` is
  untouched (still one event) and the `kind="recall"` discriminator is reserved
  for future use. Frozen `system_event_contract` baseline regenerated
  (`a12e9f467c28f7ac`). Tests: `test_reasoning_signal.py`. **Deferred (optional):**
  a dedicated `ReasoningEvent` DB model — not needed while payload conventions
  hold; add only if they get loose.

### RTR-7 — Execution-causality as unified intelligence layer ("RippleTrace") — **[HARDEN], medium/low (split)**

- **Naming note:** "RippleTrace" does **not** appear in the runtime by design —
  as primitives were discovered, the content-domain ripple concept crystallized
  into the runtime's `SystemEvent` + `EventEdge` causal graph. The name dissolving
  into the primitive is expected, not a gap.
- **Evidence (runtime half already canonical):** `db/models/event_edge.py`
  `EventEdge` (`source_event_id` / `target_event_id` / `target_memory_node_id`,
  `relationship_type`, CHECK exactly-one-target). `platform_layer/event_trace_service.py`
  provides real graph algebra: `link_events`, `link_event_to_memory`,
  `build_trace_graph`, `get_downstream_effects` / `get_upstream_relationships`,
  `detect_root_event` / `detect_terminal_events`, `calculate_depth` (BFS),
  `calculate_impact_score`. `MemoryNode.causal_depth` / `root_event_id` /
  `source_event_id` are **first-class persisted columns**, populated by
  `MemoryCaptureEngine._build_causal_context`; the execution-event graph and the
  memory graph are unified via `link_event_to_memory("stored_as_memory")`.
- **The gap:** the **app-side** legacy content-domain causal graph is still
  heuristic; promoting/migrating it onto the canonical execution-event layer is
  app-owned. Heavy causal computation depends on the RTR-2 worker model. The
  runtime half is largely complete.

- **Advance 2026-07-08 — runtime half CLOSED.** The last runtime-side seam was
  `GET /observability/execution_graph/{trace_id}`
  (`observability_rippletrace_node`), which returned FAILURE ("rippletrace domain
  not available") whenever the app hadn't registered the `rippletrace_*` symbols —
  even though the runtime's own `event_trace_service` (`build_trace_graph`,
  `detect_root_event`, `detect_terminal_events`, `calculate_trace_span`) is a
  fully-capable equivalent. The node now **falls back to `event_trace_service`**
  when the app symbols are absent (app-registered symbols still take precedence);
  only app-domain `insights` are unavailable in the fallback (→ empty list). Also
  fixed the `getattr(row, "cau" + "sal_depth")` obfuscation in
  `_serialize_memory_node`. Tests: `test_rippletrace_runtime_fallback.py`. The
  substantive remainder (migrating the app-side heuristic content-domain graph
  onto this layer; heavy causal computation via the RTR-2 worker) is **app-owned /
  RTR-2-gated**, not a runtime gap.

### RTR-8 — PyPI publication — **CLOSED / stale (do not re-track)**

This backlog item is already done: **PYPI-PUBLISH-1 closed 2026-06-14**; the
runtime is published to PyPI and `AINDY/_version.py` is `1.4.3`. The only live
sub-question — whether `aindy-apps-monolith` pins the published package vs.
installing from source — is **apps-side config**, not a runtime gap.

---

**Side finding — RESOLVED 2026-07-07.** The two **tracked** `.nbc` files under
`AINDY/nodus/stdlib/.nodus/cache/` were stale cross-machine caches (they embedded
the absolute path `C:\dev\masterplan-infiniteweave-...`, so the nodus-lang VM
treated them as misses and regenerated). Build droppings committed by accident,
**not** load-bearing precompiled assets — `git rm`'d as part of the RTR-1 close
(the dir stays gitignored).

**Close/advance triggers:** RTR-1 — **CLOSED 2026-07-07** (the
`register_nodus_workflow` keystone shipped; all four runtime gaps resolved). The
downstream "make `nodus_vm` the default / retire `AGENT_FLOW`" decision is tracked
via the app-side soak follow-ups above and **RTR-3** (AgentRun↔FlowRun
unification), not RTR-1. RTR-2 — when distributed execution is made the prod default or
per-tenant lanes are required. RTR-3 — when AgentRun↔FlowRun divergence is
observed in production. RTR-5 — when runtime-driven autonomous execution windows
are scheduled. RTR-4/6/7 — when their named gaps block an app phase.

---

## SDK-SYSCALL-GRANT-1 — `/platform/syscall` under-grants capabilities to SDK callers

**Status:** CLOSED 2026-07-07 (PR pending)

**Resolution:** `dispatch_syscall` no longer grants a fixed
`DEFAULT_NODUS_CAPABILITIES` list. `_resolve_dispatch_capabilities`
(`platform_ops_router.py`) now derives the grant from the **requested syscall's
own `entry.capability`**, granting exactly that one capability (least-privilege)
when it is on the governed public **dispatch surface** `_DISPATCH_CAPABILITY_SCOPES`
= `{memory.read, memory.search, memory.write, execution.read, flow.run, event.emit}`.
API-key callers must carry an authorizing scope (or `platform.admin`); JWT callers
are trusted and not scope-gated. Off-surface/unknown syscalls grant nothing → the
dispatcher returns its canonical 404/403. The old prefix-based domain gate
(`_SYSCALL_REQUIRED_SCOPE`) was replaced by the capability-based map (more precise —
the prefix gate couldn't gate `execution.*` and over-required `memory.write` for reads).

Both named gaps fixed: **`flow.run`** is now grantable (authorized by the existing
`flow.execute` scope — the same scope that gates `POST /platform/flows/{name}/run`,
so a flow runs under one consistent grant regardless of entrypoint); **`event.emit`**
is grantable to API-keys via a **new `Scopes.EVENT_EMIT` scope** (added to
`api_key_auth.py` + `Scopes.ALL`; strictly additive — API-keys couldn't emit at all
before, so no regression; JWT keeps it by default). Memory reads now honor the
`memory.read` scope (previously the prefix gate required `memory.write` for all
`memory.*` — a read-only key couldn't read); `memory.write` scope still implies read.

Cross-repo: the SDK README documented a nonexistent `flow.run` scope (key creation
would 422 it) and a bogus `syscall.*` scope — corrected to `flow.execute` /
`platform.admin`. Docs updated: `SYSCALL_REFERENCE.md` §Scope requirements,
`SDK_CONTRACT.md` §Capability grant. Tests: real `_resolve_dispatch_capabilities`
coverage in `test_tier3_structural.py` (flow.run/event.emit/execution.read/memory
grants + scope rejections + off-surface + admin bypass). Not a schema change.

**Problem (historical):** `dispatch_syscall` (`AINDY/routes/platform/platform_ops_router.py`)
granted a caller only the capabilities present in `DEFAULT_NODUS_CAPABILITIES`
(`memory.read`, `memory.write`, `memory.search`, `event.emit`, `execution.read`).
JWT callers receive that full set; platform-API-key callers receive the
intersection of the set with their key scopes (`[s for s in api_key_scopes if s
in DEFAULT_NODUS_CAPABILITIES]`). Consequences for the SDK's syscall-dispatch
surface (`aindy-sdk` → `POST /platform/syscall`):

- **`client.flow.run`** dispatches `sys.v1.flow.run` (capability `flow.run`),
  which is in **no** grant path — denied for **every** caller (JWT and API key).
- **`client.events.emit`** dispatches `sys.v1.event.emit` (capability
  `event.emit`) — works for JWT callers, denied for API-key callers because
  `event.emit` is not a member of `Scopes.ALL`, so it never survives the
  intersection.
- **`client.execution.get`** (`execution.read`, added 2026-07-05) works for JWT
  out of the box; API keys need the new `execution.read` scope. See
  `SYSCALL_REFERENCE.md` / `SDK_CONTRACT.md`.

`client.memory.*` is unaffected (`memory.read`/`memory.write` are in both the
default set and `Scopes.ALL`). `client.nodus.*` is unaffected — it uses the
dedicated `/platform/nodus/*` routes, not syscall dispatch.

**Fix (deferred):** reconcile the capability↔scope bridge so the documented SDK
surface is grantable — e.g. map each stable syscall's required capability to a
grantable scope, or derive the caller's capability set from `entry.capability`
of the requested syscall gated on the key's scope, rather than a fixed default
list. Security-sensitive (widens what tokens can dispatch); do not fold into an
unrelated change. Related: **TIER3-V2V3** (closed) added the domain-level scope
*gate*; this entry is about the capability *grant*. Source: SDK cross-check
2026-07-05 while closing the `sys.v1.execution.get` gap.

---

## AIDER-PORTABILITY-2026-08-17 — provenance note for the six entries below

**Not a debt item. A label, so the six entries that follow are not mistaken for audit findings.**

`FS-SCOPE-1`, `EFFECT-PARTIAL-1`, `EFFECT-PRECONDITION-1`, `EFFECT-MANIFEST-1`,
`EMBEDDED-FLOOR-1` and `PERF-BASELINE-1` were **found in comparative portability research, not in
an audit of this repository**. The source is a three-document set at
`C:\codev\Aider research\` — an architectural audit of Aider (2026-06-24), a 3-way ownership
lens (2026-06-24), and a portability analysis asking *"if Aider ran on aindy-runtime, what would
break?"* (2026-08-15, pinned at `edd3a80` = `v2.1.0-1`). An accuracy pass against `v2.4.0` on
2026-08-17 re-verified every runtime-side claim in source and is written up in
`ACCURACY_CHECK_vs_aindy-runtime_2.4.0.md` alongside them.

**Why the provenance matters when you read these.** Every other entry in this file was found by
looking at this codebase and asking what is wrong with it. These six were found by taking a
mature external system with a *different shape* and asking what the runtime could not express for
it. That method finds a different class of gap — **absent vocabulary rather than broken wiring**
— and none of the six is a defect. Nothing is failing today. They are the things a consumer
unlike our current consumer would hit immediately, and they will not surface from inside.

**The one-line summary of what the exercise established**, because it is the most useful
sentence produced and it belongs somewhere durable:

> **Authority in `aindy-runtime` is enforced at the effect, not at the request. The agent's
> decision mechanism — planner, parser, tool call, hard-coded script, or human — is outside the
> trust boundary by construction.**

That is a *validation*, not a gap. It was untested until an agent with **no tool-call protocol at
all** was held against the model: Aider's LLM never requests anything, it emits prose and Aider's
parser recovers the edits. An authority model that gated *requested capabilities* would have had
nothing to gate. Ours gates the effect, so a text-parsed edit arriving at a tool seam is checked
by exactly the mechanism a structured tool call is. Only an agent with no tool-call protocol
could prove that, which is why three internal audits never did. It should be stated as an
invariant in `EXECUTION_INVARIANTS.md`, not left in a research file.

**The composite verdict the same exercise reached, which is why five of the six are effect-model
items:** *the runtime's authority model is better specified than its effect model.* It answers
**who is allowed to do this** with cryptographic and transactional rigour, and answers **what
exactly was done, to what version of what, and how much of it succeeded** with a binary flag and
a default-off gate.

---

## FS-SCOPE-1 — the capability vocabulary is verb-shaped; no authority statement can name a path

**Status: OPEN — P1.** Filed 2026-08-17. Provenance: `AIDER-PORTABILITY-2026-08-17` (its B1, and
the one it calls "the sharpest verified gap").

**The gap, measured.** A repo-wide grep for `allowed_paths|path_scope|writable_root|allowed_dirs|
fs_scope` under `AINDY/` returns **one hit, and it is a comment** (`nodus_worker.py:340`).
`register_tool()` carries `egress_scope` — network authority is first-class — and nothing
anywhere scopes paths. The runtime can express *may this run reach the network under scope X* and
cannot express *may this run write `src/**` but not `.github/workflows/**`*.

The vocabulary is **verb-shaped** (`memory.read`, `write_memory`, `execute_flow`), not
**resource-shaped**. That is the whole finding; everything below is why it matters and how not to
fix it.

**Why an external system made it concrete when internal audits did not.** Aider's *entire*
authority question is a path set: two tiers of file access — editable (`abs_fnames`, user-added)
and read-only (`abs_read_only_fnames`, via `--read`) — enforced in `prepare_to_edit`. That is
already a path-scoped capability model, implemented at the app layer, **with no runtime
counterpart to receive it**. Our current consumer never asks the question, so the absence has
never cost anything.

**★ Do NOT build this the way the analysis proposes.** It suggests `fs_scope` alongside
`egress_scope` on `register_tool`. That creates a **second vocabulary for the question
`EXEC-ENV-BIND-1` already asks** — an execution unit declaring the environment it needs. Two
vocabularies for one question is how this repo got `SyscallEntry.stable` and `_STABLE_SYSCALLS`
measuring different things. `fs_scope` is a **field on the descriptor**; the descriptor is the
thing to build. And its enforcement point is the same one `TOOL-SEAM-ISOLATION-1` needs. **One
structural change serves three filed items** — worth knowing before any of them is costed alone.

**★ The smallest true instance is available today and is already half-owed.** `GUEST-CONFINE-1`'s
own recommended step 1 read *"pass `allow_subprocess=False`, `allow_network=False`,
`allow_env=False` (and an explicit `allowed_paths`)"*. Three of the four landed. See that entry's
`RESIDUAL` section — one call site, one kwarg, and it converts an inherited process default into
a declared scope. **Do not close this entry with it.** One call site is not a vocabulary.

**Generality.** Path scoping is the archetype of resource-scoped authority. The same absence
would bite identically on object-store prefixes, database schemas and API resource paths.

**★ A shipped reference for the vocabulary (added 2026-08-17, provenance
`workload-sandbox-provider-reference.md`).** `codex-rs`'s `SandboxExecRequest` carries
`file_system_sandbox_policy: FileSystemSandboxPolicy` and `network_sandbox_policy:
NetworkSandboxPolicy` as **peer fields** on one request — precisely the resource-scoped pairing
this entry argues for, default-on across macOS/Linux/Windows. It also names the fails-closed
knob: `SandboxablePreference { Auto, Require, Forbid }`. So the shape is proven rather than
proposed, and the answer to *"where does `fs_scope` live?"* is *"beside `egress_scope`, on the
thing that describes one execution"* — i.e. the `EXEC-ENV-BIND-1` descriptor, which is what this
entry already says. See `TOOL-SEAM-ISOLATION-1` for how it is applied (a command **transform** at
the single chokepoint, not an execution ABC).

**What does NOT belong in the fix.** A `sys.v1.repo.*` or filesystem syscall. None exists and
none should — a versioned-filesystem syscall binds the substrate to one resource class. The
absorbable thing is the scope vocabulary, not a resource-specific verb.

---

## EFFECT-PARTIAL-1 — the envelope has two states and a batched effect has three

**Status: OPEN — P1.** Filed 2026-08-17. Provenance: `AIDER-PORTABILITY-2026-08-17` (its B2).
Carried unchanged from the June lens audit through the August re-verification — the only absorb
candidate in that document that survived two passes untouched.

**The gap.** `syscall_dispatcher.py:22` documents the envelope status as
`"status": "success" | "error"` — two states. Grepping `"partial"` across the dispatcher returns
nothing. A batched effect has a third outcome and no way to say it.

**Why it is not cosmetic.** Forced through a binary envelope, a five-unit effect with two
failures becomes either **a lie** (`success`, silently partial) or **a waste** (`error`,
discarding three applied units and re-attempting everything). That is the difference between a
retry that converges and a retry that thrashes. The external reference: Aider's most
operationally valuable failure behaviour is *"3 of 5 hunks applied; here are the 2 that didn't
and why"* — `apply_partial_hunk` and `other_hunks_applied` exist precisely so the model retries
only the failed sub-units, and its own audit names this as why its effective edit-success rate
stays high with imperfect models.

**★ Materially cheaper than the analysis assumes — measured, and this corrects its open question
#3.** That question worries *"a partial application is a fourth condition and the schema has no
room for it."* It has room. `EffectRecord.status` is
`Column(String(32), nullable=False, default="pending")` with the three-value convention stated in
a **docstring** (`effect_record.py:15, 70-71`) — not a SQLAlchemy `Enum`, no CHECK constraint.
**No migration is required.** The work is the envelope contract plus every reader of `status`,
which is a bounded grep, not a schema change.

**★ It already bites in-house — this is not a hypothetical imported from another codebase.**
`ROUTE-EFFECT-BYPASS-1` found `sys.v1.memory.write` **replacing** the caller's `extra` rather
than merging it, so a naive rewire was silent data loss behind a `201`. That is precisely a
partial effect reporting total success, in our own registry, discovered by accident.

**★ Test discipline — this is trusting-a-green-check variant 6 by construction.** The thing being
added is a *report*. A test asserting that a partial reports partial passes when the reporting
wire is broken and the effect simply succeeded. **It needs a liveness control that proves the
partial path was taken**, exactly as the EventBus wire suite needed one. Mutation-test it: break
the partial accounting and confirm the suite goes red, and count how many tests fail.

**Interaction with the ledger.** A partial application leaves external state in a condition that
neither `pending` nor `success` describes, so the sub-unit records are the substantive half —
the envelope alone would let the ledger keep lying more precisely. Record each sub-unit's effect
independently or do not do this.

**Generality.** Any batched effect: bulk API writes, multi-row upserts, fan-out notifications.

**★ Complement, not overlap (noted 2026-08-18): `RETRY-CONTEXT-1`.** This entry shapes the
*result* — which sub-units failed and why. That one carries a failure *forward* into the next
attempt, and covers the **whole-call** failure this entry does not. The GPT Engineer lens audit
derived the second half independently (`_improve_loop` re-sends the diff-parse error; `self_heal`
re-sends stderr), which is why they are filed separately rather than merged: a partial-success
envelope with nowhere to send it is half a mechanism, and so is a retry channel with nothing
structured to put in it. **Build them together if either is built.**

---

## EFFECT-PRECONDITION-1 — an effect cannot declare the version of the world it expects

**Status: OPEN — P2, deliberately deferred.** Filed 2026-08-17. Provenance:
`AIDER-PORTABILITY-2026-08-17` (its B3).

**The gap.** `EffectRecord` keys on
`compute_action_id = sha256(json({"action_type", "input", "scope"}))` — the identity of the
**request**, never of the world it acted on. An effect cannot say *"I expect resource R at
version V; refuse me if it moved."* Replay safety is therefore about whether we already ran this
call, not about whether running it now still means the same thing.

**★ The reference implementation exists, is excellent, and is not ours.** Aider's Git discipline,
verified in source at `commands.py:553`:

| The primitive | Aider's mechanism |
|---|---|
| Effect declares a read-set with expected versions | `check_for_dirty_commit()` detects dirty targets; `dirty_commit()` commits the human's work first, establishing a known base |
| Version token identifying pre-effect state | the commit hash, recorded in `self.aider_commit_hashes` |
| Ledger refuses replay when the world moved | `/undo` refuses a commit not in that set, refuses merge commits (`len(parents) > 1`), refuses when files carry local modifications (`is_dirty(path=fname)`), refuses when already pushed (local head == remote head) |
| Compensation needs the prior bytes | `git revert` of a known hash — the prior bytes are the version system's job, not the agent's |

All four refusals confirmed against the source, not taken from the audit.

**★ The design answer that falls out, and it is cheaper than the alternative we were heading
toward.** The version identity is **whatever the external system's own version mechanism
produces**. The runtime's job is to *record it, carry it, and refuse on mismatch* — and
**never to reimplement it**. Content-addressed snapshots inside the runtime is the wrong shape:
it makes the substrate authoritative over state it does not own, which fails the absorption test
the adapter form passes.

**★ Why P2 and genuinely premature — do not promote this out of order.** It needs an external
mutable resource class the runtime actually mutates. There is no filesystem syscall and no
`sys.v1.repo.*`, **correctly** — a versioned-filesystem syscall would bind the substrate to one
resource class. Until `FS-SCOPE-1` gives the runtime something to hold a version token *for*,
this is a primitive with no consumer. Build it third or not at all.

**Open question, unresolved and worth carrying:** is there one external-version abstraction, or
does every resource kind need its own adapter? Git OID, HTTP `ETag` and Kubernetes
`resourceVersion` are similar in shape and different in failure mode.

---

## EFFECT-MANIFEST-1 — authority is minted from a plan; the general primitive is a manifest

**Status: OPEN — P2, record-only.** Filed 2026-08-17. Provenance:
`AIDER-PORTABILITY-2026-08-17` (its B4, and the idea it calls "the single most transferable in
the document"). **Do not build before `FS-SCOPE-1` and `EFFECT-PARTIAL-1`.**

**Not a defect.** Nothing is broken. This is filed so it is not rediscovered, because it reframes
a design decision already made and the reframing is worth more than the code would be.

**The reframe.** Plan-once is not really about planning. **It is about knowing the effect set
before executing it.** A planner is one way to obtain that. A parse-validate-apply pipeline is
another, and a tighter one.

**The evidence, verified at `base_coder.py:2296`.** Aider's `apply_updates()` runs
`get_edits() → apply_edits_dry_run() → prepare_to_edit() → apply_edits()` — **three validation
stages before the first byte hits disk**. At the end of parsing it knows the exact file set and
the exact hunks, so a token scoped to precisely that set could be minted *after* the model speaks
and *before* any effect.

**★ The inversion worth keeping, because it is uncomfortable and correct.** That is **strictly
tighter than our own plan-once model**, where the capability set is derived from a plan the model
produced *without having seen the state it will act on*. An external system satisfies the
runtime's central assumption structurally, better than the runtime does.

**The generalisation.** A.I.N.D.Y. produces one manifest per run. An agent that re-plans tool
exposure per request cannot produce one per turn at all. Aider produces one naturally, several
times per turn. **Three granularities of one shape:** a complete declaration of intended effects,
made before any of them executes, against which authority is minted and outside which nothing may
run. If the runtime's primitive were the manifest rather than the plan, all three would be
first-class citizens of the same substrate — which is precisely the claim the word "substrate"
makes.

**Why it waits.** A manifest is a container for scoped authority statements. Until the vocabulary
can express a resource scope (`FS-SCOPE-1`) and the envelope can report per-unit outcomes
(`EFFECT-PARTIAL-1`), a manifest would carry only the verb-shaped capabilities the token already
carries, and would be ceremony.

---

## EMBEDDED-FLOOR-1 — there is no supported profile below `single-instance`, and it requires Postgres

**Status: OPEN — P2.** Filed 2026-08-17. Provenance: `AIDER-PORTABILITY-2026-08-17` (its A6/B6).

**The state, read from source.** `platform_layer/deployment_contract.py` declares four profiles.
The floor is `single-instance`, declared `"stability": "stable"`, with
`required_dependencies = {postgres: True, schema_enforcement: True, redis: False,
event_bus: False, queue_backend: False, worker_process: False}` and
`background_leadership_mode: "in-process"`. `AINDY_ALLOW_SQLITE` (`config.py:476`) is a test-only
escape, documented as such.

So a consumer shaped like *a library in a terminal* — no server, no daemon, no database, `pip
install` and run — is **out of contract by declaration**. Not by omission.

**★ The declaration is itself the finding, and it inverts what the analysis assumed.** Its §6/A6
says *"what is still missing is the **declaration** of which guarantees hold in that
configuration."* `deployment_contract.py` has existed since the initial repo extraction
(2026-05-17) and declares exactly that. The finding survives and the evidence flips: there **is**
a declared embedded contract, and it says Postgres is mandatory at the floor. That is a better
position than an undeclared one — it is falsifiable and it is honest — but it is a *no*.

**★ This is a soak-and-deployment gate, not a capability gap. State it that way or the entry
misleads.** Nothing found in this pass says the single-process case *requires* Postgres in a way
SQLite could not serve; `AINDY_ALLOW_SQLITE` exists and the entire unit suite runs on it. What is
missing is (a) a profile that **declares** the reduced guarantees, and (b) a test tier that
**asserts** them — the same *"verification that exists but does not enforce"* shape this file
catalogues elsewhere. That is work, not invention, and it is bounded.

**★ Scope boundary — keep separate from `DEPLOY-TARGET-1/2`.** Those are about scaling **up**
(cloud manifests, multi-tenant SaaS readiness). This is about scaling **down**. They share no
mechanism and folding them loses both.

**★★ Evidence, not inference (added 2026-08-19, from DBOS).** This entry said *"nothing found so
far says the single-process case **requires** Postgres in a way SQLite could not serve."* **A peer
now proves it.** DBOS ships `SQLiteSystemDatabase(SystemDatabase)` (221 lines) and
`PostgresSystemDatabase(SystemDatabase)` (343 lines) **subclassing one shared 6 508-line
implementation** — checkpointing, recovery, queues and schedules are identical, with roughly 120
lines of dialect between them. Same split at its app-data layer.

**★ And the shape tells us where ours would go: the split is at the system-database boundary, not
sprinkled through the engine** — for us, `AINDY/db/`. **The scoping question to answer first is
which Postgres-specific features the runtime actually depends on** — JSONB, advisory locks,
`FOR UPDATE`, and **pgvector, which is the real one.** An embedded profile would ship with memory
*degraded* rather than absent, and that should be decided before the work, not during it.

**Falsification target if anyone builds it:** a full session completes with no Redis and SQLite
only, with the profile's declared guarantees asserted rather than assumed.

**★ Independently re-derived (noted 2026-08-17).** The Codex portability analysis reached the same
primitive from an unrelated direction — its **N8, "supported embedded profile as a contract, not
an exception handler"** — and its §10 makes the argument this entry only implies: *downward scale
is the direction generality is never argued in, and the one that decides the word "substrate."*
Its framing of the fix is also the right one and matches §3.2 above: **absorb as contract, not as
code** — most of the degradation already exists; what is missing is the declaration that it is
supported. Two derivations from two systems, neither of which knew about the other.

---

## PERF-BASELINE-1 — no execution-path timing is asserted anywhere, and every flag flip is waiting on it

**Status: OPEN — P1.** Filed 2026-08-17. Provenance: `AIDER-PORTABILITY-2026-08-17`.

**Measured.** **Zero latency assertions across `tests/`.** Every `duration_ms` reference is a
type-or-shape assertion — `test_syscall_dispatch_contract.py:78` asserts
`isinstance(result["duration_ms"], int) and result["duration_ms"] >= 0`; the Infinity tests assert
a *mocked* value round-trips. Nothing anywhere asserts a bound on how long anything takes.

**★ Two independent comparative analyses reached the same conclusion from opposite directions:**
per-effect and per-turn overhead on the real durability path is **the** decisive unknown, and it
is the one falsification target that requires **no new primitive** to satisfy. Both marked it
`[Unknown]` and neither could resolve it, because the repository contains no baseline to read.

**★ Why P1 and not P2 — the reason is internal, not external.** Look at the standing backlog:
`IDEM-11` (flip after soak), `DUR-1..4` (soak then flip), `RTR-4` (soak then flip),
`DB-NODUS-BUDGET-1` (soak then flip), `FR-15 (a)` (`AINDY_ASYNC_HEAVY_EXECUTION`, needs soak),
`AUTHORITY-VALUE-1` (`AINDY_CHILD_CONTEXT_CLAMP`, needs a caller fix then soak),
`INFINITY-RUNTIME-1` (flag flip after soak), `NODUS-WARMPOOL-1`. **Eight items whose remaining
work is "gather evidence that the flagged path is acceptable, then flip."** There is currently
**no instrument in this repository that could produce that evidence.** The flag backlog is not
blocked on courage or on build effort; a large part of it is blocked on measurement that does not
exist. That is what makes this P1 rather than a nice-to-have.

**★ The trap to design against is variant 9 — green because there was nothing to catch.** A
timing assertion added to a suite that never exercises the durable path passes trivially and
certifies nothing. The baseline must run **against the path being flipped** — dispatcher with
`AINDY_SYSCALL_IDEMPOTENCY` on, continuation on, the real effect ledger — and every threshold
needs a control that makes it fail, or this becomes the ninth entry in that table rather than the
answer to eight others.

**What is wanted, stated so scope does not drift.** Not a microbenchmark suite and not a
performance-regression gate on every PR. A **per-effect and per-turn number on the real path,
recorded, with a regression bound**, runnable on demand. The `Integration Tests` job already
stands up live Postgres and Redis, which is the expensive half.

**Prior art in this repo, and the caution it carries.** `RT-MEMTXN-LEAK-1` produced real numbers
(login 43.6s → 0.3s, 60 held connections → 0) — under incident conditions, by hand, after the
damage. `CAPABILITY-PROVIDER-TIMEOUT-1` produced 10 lookups = 10 spawns / 56.4s → 1 / 11.4s, the
same way. **Both measurements were possible, both were made only after something broke, and
neither left a standing instrument behind.** That is the pattern to end.

---

## MAF-REFERENCE-2026-08-17 — provenance note for the three entries below

**Not a debt item. A label, same shape as `AIDER-PORTABILITY-2026-08-17`.**

`FLOW-GRAPH-SIGNATURE-1`, `WAIT-TYPED-CONTRACT-1` and `OTEL-GENAI-SEMCONV-1` come from a
**reference-implementation study, not a port analysis and not an audit**. Source:
`C:\codev\Autogen research\` — an architectural audit of Microsoft Agent Framework (2026-06-24),
a 3-way ownership lens (2026-06-24), and a study asking *"what does MAF's workflow engine ship
that we derived and could not point at?"* (2026-08-15, pinned `d32bd5d` = `v2.1.0-3`). An accuracy
pass against `v2.4.0` on 2026-08-17 re-verified every runtime-side claim in source and is written
up in `ACCURACY_CHECK_vs_aindy-runtime_2.4.0.md` beside them.

**Why this one is a different kind of input.** The Aider set found *absent vocabulary* by holding
a differently-shaped agent against the runtime. This set found **a worked design for a primitive
we had already derived**: four independent analyses each concluded fan-out/join was missing, and
none of the four systems had one to copy. MAF does — typed, validated, serializable, checkpointed.
`FLOW-PARALLEL-1` stopped being a design problem and became a reading exercise. The two entries
below are the *other* two primitives that fell out of reading it, neither of which any other
comparison surfaced.

**The framing worth keeping, because it makes the change smaller than four analyses implied:**

> A superstep is not "concurrency added to the flow engine." It is **the existing per-node commit
> boundary widened to span a barrier-delimited group of nodes.** `PersistentFlowRunner` already
> commits `FlowHistory` per node with a monotonic `sequence_number`; the question is the width of
> the transaction, not the introduction of one.

**The refusal, which is the load-bearing half and should not be lost:** MAF has the better
workflow *shape* and **no workflow durability of its own** — it delegates crash-durable
orchestration wholesale to Azure Durable Task (`python/packages/durabletask`), and its in-core
checkpoint chain dies with the process. We have the inverse. Topology is a data-model change
inside one subsystem; durability is CAS claims, leases, rehydration, watchdogs, DLQ and
idempotency. **Take the topology model, refuse the delegation.** Five systems compared across the
corpus, five different ways of not building durable orchestration natively — that is the
strongest evidence in the set that this runtime's centre of gravity is in the right place.

**★ One caution about this document specifically, which is why the accuracy pass matters:** it
asserts `[Observed]` that the runtime has **no MCP client**. `AINDY/platform_layer/mcp_client.py`
shipped 2026-07-11 (#222), a month *before* its own pin, and it answers that document's Open
Question 5 — *"how would a remote MCP tool acquire a capability?"* — with a dedicated
`MCP_EGRESS_CAPABILITY` distinct from `outbound.http`. A contributing cause was on our side: this
file's `ECOGAP-4` entry carries a preserved-original bullet reading *"G4b has zero runtime code"*
roughly 90 lines below a status header saying it shipped. That bullet is now marked superseded in
place.

---

## FLOW-GRAPH-SIGNATURE-1 — a suspended run resumes against whatever flow definition exists now

**Status: OPEN — P1.** Filed 2026-08-17. Provenance: `MAF-REFERENCE-2026-08-17`.
**Independent of `FLOW-PARALLEL-1` — do not bundle them.**

**The gap.** `flow_run_rehydration` restores a `FlowRun` against whatever definition
`register_all_flows()` produced *this boot*. Nothing records what the run was planned against and
nothing detects that it changed. Verified absent at HEAD: `graph_signature|topology_hash|
definition_hash` returns **zero hits** across `AINDY/`.

**The failure mode, and why it is worse than it sounds.** For a system whose stated value is
surviving restarts *across deploys*, the interesting case is exactly the one that has no guard: a
node renamed, an edge rerouted, a branch condition changed between suspend and resume. The run
proceeds against a definition it was never planned for, **silently and successfully.** There is no
error, no warning, and no row that says the shape moved.

**★ Sharpened by segment-level continuation.** `continue_crashed_agent_runs`
(`core/agent_continuation.py:133`) re-drives crashed runs **from a segment boundary**, so resuming
into a changed graph does not merely finish a stale plan — it **re-executes a segment whose
meaning has moved.** The two mechanisms compose badly and neither knows about the other.

**The reference implementation, verified in source.** MAF's `Runner.__init__` takes a
`graph_signature_hash` — *"a hash representing the workflow graph topology for checkpoint
validation"* (`_runner.py:40, 51, 60`) — writes it into every per-superstep checkpoint (`:228`) and
**compares it on restore** (`:275`). That is the whole primitive: hash the shape, carry it, refuse
on mismatch.

**Proposed primitive.** Hash the flow topology at run start, store it on `FlowRun`, compare on
rehydrate, and **quarantine rather than proceed** on mismatch. It converts a silent-wrong-execution
failure into a loud one, which is the shape of guard this repository already prefers everywhere
else.

**★ A peer's cheaper answer, for comparison (added 2026-08-19, from DBOS).** DBOS pins
`application_version` **on the run** (`workflow_status`) with an `application_versions` table —
a **version string**, where this entry proposes a **topology hash**. Theirs is cheaper and coarser:
it catches *"the app changed"* and not *"this flow's shape changed."* **That trade is exactly the
design question below**, with a shipped instance on one side of it. Take the comparison; the
mechanism is a choice.

**★ The design question that decides whether it is worth anything — do not skip it.** *What goes
into the hash?* A hash that changes on every deploy quarantines every in-flight run and will be
switched off within a week. A hash that ignores too much validates nothing. The likely answer is
**node identities plus edge topology, excluding node bodies and predicate implementations** — the
same split MAF makes, where the *shape* is data and the predicate is a name. Settle this before
writing the hash, not after.

**Why P1.** It is cheap, it is independent of everything else in the flow-engine backlog, and it
closes a *correctness* failure mode rather than adding capability. It also interacts with nothing
that needs soak — the mismatch branch is either taken or it is not.

**Related, not the same:** `ORCHESTRATOR-SPLIT-1` (three durable stores, no shared recovery
contract) and `FLOW-PARALLEL-1` (topology model). This entry needs neither to land.

---

## WAIT-TYPED-CONTRACT-1 — a resume payload is trusted, not checked

**Status: OPEN — P2.** Filed 2026-08-17. Provenance: `MAF-REFERENCE-2026-08-17`.

**The gap, stated as an asymmetry rather than a deficiency.** `register_wait`
(`kernel/scheduler/waits.py:8-30`) keys on `wait_for_event` plus an optional `correlation_id`,
with `WaitCondition` types `event | time | external`. It is durable, cross-instance,
Redis-mirrored, persisted-backup'd and rehydratable — **strictly stronger than MAF's on the axis
that matters**, since MAF's checkpoint chain does not survive its process. What is missing is
narrow and specific:

- **nothing binds a resume payload to a schema**, and
- **nothing ties a response back to the specific node that asked.**

**★ The asymmetry is the finding.** `SyscallDispatcher.dispatch()` validates syscall inputs and
outputs against declared schemas — that discipline exists, is enforced, and is the runtime's own
standard. The wait path, which accepts data from *outside* the process boundary after an arbitrary
delay across a restart, validates nothing. The looser gate is on the less trusted input.

**The reference implementation.** MAF's `ctx.request_info()` / `@response_handler`
(`_request_info_mixin.py`, in the workflow package rather than bolted on): the pending request is
a first-class typed message, it is checkpointed, the typed response routes back to the originating
node, and the workflow converges to `IDLE_WITH_PENDING_REQUESTS` rather than to an ambiguous idle.

**Proposed primitive.** A pending-request record layered *on top of* the existing durable wait —
payload schema plus originating-node reference — so a resume is **checked rather than trusted**.
**Do not replace the wait mechanism.** Ours is the stronger half; this is a contract on it.

**Why P2 rather than P1.** No exploit path is claimed: resumes today arrive through
authenticated surfaces, so this is defence-in-depth and a correctness/debuggability improvement,
not a hole. It rises to P1 the moment a wait can be resumed by a less-trusted caller — an external
webhook, an MCP client, a third-party connector.

**Interaction to watch.** A typed pending-request record is also the natural place to hang
`FLOW-GRAPH-SIGNATURE-1`'s originating-topology reference. Build that one first and this one gets
cheaper.

---

## OTEL-GENAI-SEMCONV-1 — our traces are richer than the standard and illegible to standard tooling

**Status: OPEN — P2.** Filed 2026-08-17. Provenance: `MAF-REFERENCE-2026-08-17`.

**The gap.** The runtime has OpenTelemetry spans, Prometheus metrics and a causal `SystemEvent`
graph (`parent_event_id`, `build_trace_graph`, `get_downstream_effects`) — **arguably richer than
the OpenTelemetry GenAI semantic conventions**, and aligned with none of them. MAF weaves the
GenAI semconv as composable MRO layers with opt-in content capture.

**★ Adopt the conventions, not the mechanism.** The MRO-layering is MAF's answer to a problem we
do not have. The *naming* is the whole value: span and attribute names that standard observability
tooling already understands, so a consumer's existing dashboards, samplers and cost attribution
work against our traces without a translation layer. Renaming is cheap; the causal graph stays as
it is and stays a differentiator.

**Why it is filed rather than done.** It is a public surface. Attribute names appear in operator
dashboards and anything a consumer has built against the current ones, so it needs the same
treatment as any other rename: additive first, both emitted for a release, then a documented
removal. That is a release-discipline question, not an engineering one — which is why it is P2 and
not simply a chore.

**Scope note.** Naming alignment only. Do **not** fold in content capture (prompt/response bodies
on spans) without a separate decision — that is a data-handling question with its own answer, and
MAF ships it opt-in for exactly that reason.

**★ Independently re-derived (noted 2026-08-18).** The June Google ADK lens audit
(`C:\codev\google adk research\`) reached the same absorb item from an unrelated system and for a
different reason: ADK's `telemetry/` emits GenAI-semconv spans (`trace_call_llm`, `trace_tool_call`,
token-usage and step metrics) and its audit's verdict on our side was *"arguably richer, and not
semconv-aligned"* — the same conclusion the MAF study reached two months later. **Two unrelated
frameworks asking for the same naming alignment, for different reasons, is better evidence than
either request alone**, and it is the same convergence pattern recorded on `EMBEDDED-FLOOR-1` and
`EFFECT-PRECONDITION-1`. It does not change the priority — this is still a public-surface rename
gated on release discipline, not engineering — but it does mean the interop value is not
speculative.

---

## SUBSTRATE-WITNESS-1 — the substrate claim has no first-party consumer that exercises it

**Status: OPEN — P1.** Filed 2026-08-17. Provenance: `claw-the-first-real-consumer.md` in
`C:\codev\Claude Code research\docs\` (2026-08-15), the capstone of a nine-document port series.
Re-verified against `C:\dev\claw` on 2026-08-17 — see the measurements below.

**This is not a defect. It is a gap in the evidence, and it is the reason several other entries in
this file cannot be closed with confidence.**

**The finding.** Claw — the flagship first-party application, 107 `.py` files / 18,766 LOC —
integrates with this runtime through **334 lines across three files**, all optional, most of it
over HTTP, with the memory backend that would use it defaulting to `"local"`.

| Measured 2026-08-17 | Value |
|---|---|
| `claw/aindy/app_registration.py` + `client.py` + `memory_store.py` | 61 + 53 + 220 = **334 lines** |
| `claw/config/schema.py:157` | `memory_backend: str = "local"  # "local" \| "aindy" \| "aindy-fallback"` |
| `execute_tool` / `EffectRecord` / `execution_token` / `EXACTLY_ONCE` in Claw's own source | **zero** — the only matches in the tree are inside its vendored `venv` copy of AINDY itself |

So Claw *depends on* the runtime as a package and routes **none of its effects through it**. Its
real effects — channel delivery across Discord/Telegram/Slack/Matrix/Signal, sessions, skills,
scheduling, LLM calls, compaction — run through the `nodus-*` package ecosystem. They do not pass
`execute_tool`, they carry no capability token, and they write no `EffectRecord`.

**★ Why this belongs in the debt file rather than in a research folder.** The audit series that
found it states the consequence plainly, and it is a statement about this repository:

> When a first-party team, with full knowledge of the runtime and every incentive to use it, built
> the flagship application, they integrated at the SDK boundary and defaulted it off.

**The corollary that matters for how this file is read:** the accumulated coverage percentages
across nine documents — 90–95% for Devika, 80–85% for MAF, 70–80% for SWE-agent, 55–65% for
Temporal, 15–25% for Aider — describe capabilities the runtime **has**, not capabilities anything
**uses**. That is not an argument that the runtime is unproven as software; the unit and
integration suites are real and the required checks are real. It is narrower and sharper: **the
substrate claim specifically — that an arbitrary agent can rely on these guarantees — has exactly
one first-party witness, and that witness is testifying about the HTTP API.**

**★ How this couples to the flag backlog, which is the practical cost.** Eight entries in this
file end in *"soak, then flip"* — `IDEM-11`, DUR, `RTR-4`, `DB-NODUS-BUDGET-1`, `FR-15 (a)`,
`AUTHORITY-VALUE-1`, `INFINITY-RUNTIME-1`, `NODUS-WARMPOOL-1`. Soak requires production traffic
**through the path being flipped**. No first-party consumer sends traffic through the effect
ledger, the capability token or the tool seam. So the soak those entries wait on is not merely
un-run — under the current integration shape it **cannot** be run. Pair this with
`PERF-BASELINE-1`: one entry says there is no instrument, this one says there is no traffic.

**The recommended slice, and it is deliberately small** (the source document's option C, and the
first recommendation across nine documents with a named, first-party, already-deployed subject):

> Route **only Claw's outbound message delivery** through `execute_tool` with a declared
> `EXACTLY_ONCE` guarantee.

That class of effect is exactly what the ledger exists for — externally visible, irreversible, and
currently at-least-once with no dedup. It exercises the capability token, the effect ledger and
idempotent replay against a real workload, in the `single-instance` profile, on a machine already
running. It answers, from production traffic rather than a harness, the three questions every port
audit deferred: **is capability metadata derivable? does continuation hold for a real workload?
does the ledger survive real tool results?**

**★ Do not close this by writing a synthetic fixture.** A harness that calls `execute_tool` in a
test proves the code path executes — which the unit suite already proves. The thing that is
missing is a *consumer that would notice if the guarantee broke*, which is a different artifact
and the only one that makes a soak mean anything. This is the `DOCS-COVERAGE-CLAIM-1` shape at the
level of the whole system: coverage asserted, exercise absent.

**Adjacent, and not the same:** `LOCAL-1` (upgrade path for local installs) and `DEPLOY-TARGET-1`
(cloud manifests) are about *deploying* the runtime. This is about a consumer *depending on its
guarantees* once deployed.

**Second-order note worth keeping.** The same document observes that Claw's README claims an
"AINDY execution kernel" and that it "fully exercises the underlying substrate." Whatever is done
about the integration, the claim and the wiring should be made to agree — an internal flagship
overstating its own integration is precisely how a team comes to believe the substrate is
validated when it is not. That half is Claw's to fix, not this repository's, and is recorded here
only so the two halves are not separated.

---

## PROGRESS-CHANNEL-1 — an execution can report a result or nothing; there is no partial-output surface

**Status: OPEN — P2.** Filed 2026-08-17. Provenance: `CODEX_ON_AINDY_RUNTIME_PORTABILITY_ANALYSIS.md`
§7 (its N5, one of two of its eight proposed primitives that six months of registry work had not
already absorbed). Verified absent at HEAD.

**The gap.** An execution produces a result when it finishes, and nothing before that. There is no
surface on which a long-running execution can emit partial output. Verified repo-wide: no
`StreamingResponse`, no `EventSourceResponse`, no `text/event-stream` on any execution surface.
(The MCP *server* has an SSE transport — that is a different surface, exposing syscalls to
external clients, not a progress channel on a running execution.)

**Why an external system surfaced it and internal audits did not.** Everything this runtime hosts
today is batch-shaped: a flow node runs, commits, and advances. Codex is interactive — a turn
streams tokens and tool output to a human who is watching, and a substrate that can only say
"done" is unusable for that class of consumer regardless of how good its durability is. Nothing in
the current workload mix asks the question.

**★ The three properties that make it a runtime primitive rather than an app concern**, and they
are what keep the entry small:

1. **It carries no authority.** A progress frame is not a capability-bearing call; nothing about
   emitting one changes what the execution may do.
2. **It constitutes no effect.** It writes no `EffectRecord`, is not replayed, and is not part of
   the idempotency key. A re-run that emits different progress frames is still the same effect.
3. **It attaches to the trace.** It belongs beside `SystemEvent`/`trace_id`, not beside the
   result — which is what makes it the runtime's to own rather than each consumer's.

Those three are also the **guard rails**. The failure mode to design against is a progress channel
that quietly becomes an effect channel — the moment a consumer depends on a frame having been
delivered, it has become a delivery guarantee and inherits every problem the effect ledger exists
to solve. **Progress is best-effort by construction, and that must be stated in the contract, not
discovered.**

**Interaction worth noting.** `agent_continuation.py:11` already records the adjacent fact:
*"AgentStep is a post-segment batch write, so mid-segment progress isn't durable."* That is the
same boundary from the durability side. A progress channel does not make mid-segment state durable
and must not be mistaken for doing so — it makes it **observable**, which is a different and much
cheaper guarantee.

**★★ OPEN QUESTION — a shipped peer decided the opposite (added 2026-08-19, from DBOS).** The
"no effect / best-effort" constraint above was reasoned from first principles. **DBOS made progress
durable**: a `streams` table keyed `(workflow_uuid, key, offset)` with `value` and `function_id`, in
the system database. **The `offset` column is the tell** — offsets exist so a consumer can resume,
which is only meaningful if the stream is durable. That is a deliberate design, not an accident.

Either they are paying the cost this entry names — every frame a durable write, and a consumer that
depends on delivery has turned observability into a delivery guarantee — **or the cost is worth
paying**, because a durable ordered stream buys resumable consumers, replayable output and a record
of *what the workflow was saying*, none of which a best-effort channel can offer and all of which
matter for an agent streaming to a human. **Source alone cannot decide it.** Do not treat this
entry's constraint as settled: it is one reasoned position against one shipped one.

**Why P2.** No current consumer needs it, and `SUBSTRATE-WITNESS-1` says why that is weak
evidence: there is one first-party consumer and it talks to the HTTP API. This rises the moment
anything interactive is hosted — which the port series argues is the untested direction that
decides whether "substrate" is the right word.

---

## SCOPE-NAMING-1 — `enforce_api_key_scope` no longer describes what it does

**Status: OPEN — P3, cosmetic.** Filed 2026-08-17. **Filed because the source already says it is
filed**: `AINDY/services/auth_service.py:601` reads *"`SCOPE-NAMING-1` tracks the rename if it is
ever worth doing"*, and that identifier appeared nowhere in this file or `CLAUDE.md` until now. A
comment asserting a thing is tracked when it is not is the smallest possible instance of the
claimed-and-absent shape this repository catalogues, so it is cheaper to make the comment true
than to remove it.

**The gap.** `enforce_api_key_scope` gates **every** caller, not only API-key callers. Since
`HTTP-SCOPE-GAP-1` removed the JWT exemption, a session presents `session_scopes` derived from the
user row (`derive_session_scopes`) and is checked by the same dependency. The name is now narrower
than the behaviour.

**★ Deliberately not renamed, and the reason is the entry.** It appears at 41 call sites across 14
route files and in the app team's own notes. Renaming a security-relevant dependency for cosmetics
churns a surface where the diff is hard to review and a missed call site fails **open**. If it is
ever done: add the new name as an alias, migrate call sites in one mechanical pass, and pin the
old name's continued behaviour by a test before deleting it — do not rename in place.

**Not to be confused with the real remainder of `HTTP-SCOPE-GAP-1`**, which is a design question:
`execution.read` conflates scope with data ownership, and a scope cannot answer *"may I read
someone else's."* That one is substantive; this one is a word.

---

## CREWAI-NODUS-2026-08-18 — provenance note

**Not a debt item.** Third provenance label in this file, after `AIDER-PORTABILITY-2026-08-17` and
`MAF-REFERENCE-2026-08-17`.

Source: `C:\codev\Crewai research\` — a CrewAI architectural audit (2026-06-24), a 3-way ownership
lens (2026-06-24), and `CREWAI_ON_NODUS_IMPLEMENTATION_STUDY.md` (2026-08-15, Nodus pinned
`1a04d1d` = `v4.2.0-4`). An accuracy pass on 2026-08-18 re-verified it against
`C:\dev\Coding Language` @ **`v5.0.4-2`**, `C:\codev\nodus-showcase-crewai`, and this runtime at
`v2.4.0`; the write-up is `ACCURACY_CHECK_vs_aindy-runtime_2.4.0.md` in that folder.

**★ Why this folder produced runtime findings at all, when it is a Nodus study.** The other four
research folders asked *"could the substrate host this system?"* and answered from source. This one
had a working implementation on disk — a CrewAI hierarchical crew expressed as a 39-line Nodus
flow with real MCP and A2A across process boundaries. Its runtime findings are therefore
**second-order**: not *"the runtime is missing X"* but *"here is what the runtime does not know
about a guest that is doing real work."* That is a class of finding no source audit of `AINDY/`
can produce, because the evidence is entirely on the other side of the seam.

**What it produced, all filed against existing entries rather than as new prefixes:**

- The **fourth durable store** and the fact that the runtime never configures it →
  `ORCHESTRATOR-SPLIT-1`, which had recorded three.
- That the **same missing `cwd=`** governs both the guest's filesystem bound and its durable-state
  location → `GUEST-CONFINE-1` residual.
- `NODUS-UPGRADE-2` — the 5.0.1 → 5.0.4 bump, filed because the above touches the guest seam.
- A four-document error traced back to **our own** `RUNTIME_MODULE_MAP.md` → corrected there.

**One empirical result worth keeping, which is validation rather than debt.** Four successive
rounds of deepening — real LLM provider, real MCP client↔server transport, cross-process A2A with
bearer auth, scope-addressed memory — left `crew_flow.nd` **byte-for-byte unchanged**. The study
cites the showcase's own `NEXT_STEPS.md` for this; that repository has exactly **one commit**, so
it cannot demonstrate invariance-under-change. **The filesystem can, and does:** `crew_flow.nd`
mtime `2026-06-24 20:20`, while all six host-wiring files (`host.py`, `a2a_client.py`,
`a2a_server.py`, `mcp_server.py`, README, NEXT_STEPS) are `2026-07-09 06:12–06:19`. A two-week gap
with the orchestration file untouched.

That is the only **tested** layer-placement result across five research folders, and it supports a
boundary this repository already holds on reasoning: **the runtime should not grow orchestration
syntax.** Recorded in `WHAT_THE_RUNTIME_IS.md` §5.

**★ And the pointed half, which belongs beside it:** the showcase never routes through `sys()`.
Its own absorption-test table marks **Enforcement ❌** — *"no sandbox, no budget governor, no
capability gate on the delegated call in this demo"* — and states plainly that it *"demonstrates
Nodus's orchestration layer, not the Surface-B gated path."* So the **composition** boundary now
has a working witness and the **authority** boundary still has none. That is
`SUBSTRATE-WITNESS-1` seen from the other side, and the two entries should be read together.

---

## NODUS-UPGRADE-2 — pin `5.0.1` → `5.0.4`; ★ filed P3-routine, was a security fix

**Status: CLOSED (2026-08-19).** Filed 2026-08-18 as *"P3, routine"*. Provenance:
`CREWAI-NODUS-2026-08-18` — the Nodus repo was cloned for that accuracy pass and turned out to be
three patch releases ahead of our pin. **The severity was wrong, and the way it was wrong is the
finding worth keeping.**

**What the bump actually contained.** `nodus-lang` 5.0.3 fixes a **cross-runtime guest-memory
disclosure**: `GLOBAL_MEMORY_STORE` was bound at **import**, so every `NodusRuntime` constructed in
one process shared a single guest memory dict. `memory_put`/`memory_get` are guest builtins — any
`.nd` script can call them — so one script could read another's values. Upstream gives each runtime
its own store; sharing is now opt-in (`memory_store=`, `share_process_state=True`). 5.0.4 is the
follow-up unbreaking a `nodus-sdk` subclass property collision — **verified we do not depend on
`nodus-sdk`**, so that half does not reach us.

**★ Why it reached us, and the docstring that hid it.** `AINDY/runtime/nodus_worker_pool.py`
reuses worker processes across requests. Its module docstring asserted:

> *"each request still runs through `nodus_worker.run_one`, which rebuilds all per-request state,
> **so a reused process never leaks state between runs**."*

**`run_one` cannot reset a module global living inside a dependency.** The claim was true of the
state this module owns and false for the channel below it. Corrected in place 2026-08-19, with the
general rule attached: *per-request state rebuilt here says nothing about process-global state held
below here.*

**Reproduced, not taken on faith** (the `NODUS-UPGRADE-1` rule — distinguish cosmetic from real).
Two `NodusRuntime`s in one process, our own import path, `allow_*=False`:

| Pin | Second runtime's `print(memory_get("secret"))` |
|---|---|
| `5.0.1` | **`password123`** |
| `5.0.4` | `nil` |

**Exposure was bounded — by the flag, not by the claim.** Reaching it needs
`AINDY_NODUS_WARM_POOL` on (**default off**, and it has never been flipped) *plus* two tenants' `.nd`
scripts using the memory builtins in the same reused worker. So this was **latent, not live** —
the same shape as `IDEM-12`, and latent for the same kind of reason: a default that happens to be
off. **Had the warm-pool soak been done before this bump, it would have been done on a pin that
made the pool's own safety claim false.**

**Guard added:** `tests/unit/test_nodus_upgrade_contract.py::test_two_runtimes_in_one_process_do_not_share_guest_memory`.
Mutation-tested — **2 of 11 fail on 5.0.1** (the isolation assert, plus the version-pin assert by
design); both liveness controls pass first, so the failure is the assertion and not the scripts
failing to run. It asserts the **default**, so a future release that re-shares, or a construction
site that starts passing `share_process_state=True`, fails here.

**What the protocol got right and what it missed.**

- ✅ **Three sites** — in the event only **two** changed. The `Install MCP extra` step pins
  `nodus-mcp`, not `nodus-lang`, and `nodus-mcp 0.1.3` has **no upper bound**, so it cannot
  re-resolve away from `requirements.txt`. The rule stays as written; it is cheap and the failure
  it prevents is silent.
- ✅ **`MCP-SDK-2X-1` reverse trap checked first.**
  `pip install --dry-run "nodus-lang==5.0.4" "nodus-mcp>=0.1.3" "mcp>=1.0.0,<2"` resolves clean, so
  `aindy-runtime[mcp]` stays installable. **The second instance of `MCP-SDK-2X-1` is now dead** —
  the `nodus-lang<5.0.0` cap that made 5.0.0 `ResolutionImpossible` is gone from 0.1.3.
- ✅ **Confinement re-verified against the real VM** — all 21 tests across the three guard files
  green on 5.0.4; nothing cosmetic went red this time.
- ❌ **★ Nothing in the protocol asks what a patch release *contains*.** It is entirely about
  *how* to bump safely — three sites, resolver traps, confinement re-verification — and says
  nothing about reading the upstream changelog to find out whether a bump is routine. That is how
  a security fix got filed P3. **Rule added: read the intervening release notes before assigning
  a severity, not after.**

**Related:** `NODUS-WARMPOOL-1` (closed — this is a precondition its soak now depends on),
`GUEST-CONFINE-1` (the guest seam; its residual still stands), `INITIATOR-IDENTITY-1` (this was
cross-*tenant* disclosure, which is that entry's concern seen from the VM side), `IDEM-12` (the
other latent-because-a-default-is-off item).

---

## ADK-LENS-2026-08-18 — provenance note

**Not a debt item.** Fourth provenance label, after `AIDER-PORTABILITY-2026-08-17`,
`MAF-REFERENCE-2026-08-17` and `CREWAI-NODUS-2026-08-18`.

Source: `C:\codev\google adk research\` — a Google ADK 2.0 architectural audit and an
A.I.N.D.Y.-lens audit, both 2026-06-24, checked against `v2.4.0` on 2026-08-18. Write-up:
`ACCURACY_CHECK_vs_aindy-runtime_2.4.0.md` in that folder.

**★ This folder produced no new debt, and that is the finding worth recording.** Every absorb item
it proposed either **already shipped**, is **already tracked**, or would **reverse a decision this
file records with reasons**. Listing it so nobody re-opens the same items from the same source:

| ADK absorb item | Disposition |
|---|---|
| Event-sourced state fold + replay-based resume | **Shipped** as DUR-4 (`core/flow_history_fold.py`); the deterministic-replay remainder was declined — see `ECOGAP-1` and the taxonomy added there |
| OTel GenAI semconv alignment | `OTEL-GENAI-SEMCONV-1` — **ADK is a second independent derivation**, noted on that entry |
| Frontier/ready-set scheduling, `JoinNode` fan-in, routed cycles | `FLOW-PARALLEL-1`; MAF already supplies the worked reference design. ADK's own engine is non-durable (`_workflow.py:261` `# TODO`), so it is a vocabulary reference, not an implementation one |
| Plugin "first-non-`None`-wins" middleware semantics | **Considered and declined** — `HOOK-PRECEDENCE-1` below |
| `app:`/`user:`/`temp:` state-prefix scoping | App-layer; the audit says so itself |
| A2A HTTP interop adapter | `ECOGAP-4`, genuinely open — the one interop axis ADK still wins |
| Schema-from-signature tool declarations, typed `finish_task` completion | Nodus-side, per the audit's own placement |
| Cloud deployment profiles | `DEPLOY-TARGET-1`, already open and correctly cited |

**★ What it did surface, and it is about our own accuracy rather than our capability:** its §22
credits two mechanisms this file has filed as open P0s. It cites *"aindy-runtime's **APScheduler
1s heartbeat** … supply the process-independent scheduler ADK does not have"* — that heartbeat is
`FR-15`, and `_decide_mode` short-circuits to `INLINE` by default
(`core/execution_dispatcher.py:142-147`), so heavy dispatch is serialized through one slot. And it
lists `EffectRecord` EXACTLY_ONCE flatly as a delivered substrate property (`IDEM-11`: eight
declarations, flag still off) while routing tool execution through a seam that runs in-process with
the live DB session (`TOOL-SEAM-ISOLATION-1`). **An external audit reading our strengths off the
mechanism names is the mirror image of the inventory-vs-reachability pattern this corpus keeps
finding in the other direction.**

**Credit where due — and it is unusual.** The lens audit's §1 makes four layer-precision
corrections to the architectural audit's §22, separating durable `EffectRecord` *enforcement* from
Nodus `std:effects` *syntax*, and file-based `.nodus/graphs/` checkpoints from DB-backed
durability. **All four hold at HEAD**, and the CrewAI/Nodus study reached the same distinctions
independently from source two months later. Its instruction *"do not merge the two checkpoint
stories"* was right — and there turned out to be three, since Nodus also carries the SQLite
`LocalWorkflowStore` now tracked as store 4 under `ORCHESTRATOR-SPLIT-1`.

---

## HOOK-PRECEDENCE-1 — first-non-`None`-wins hook semantics: considered, declined

**Status: RECORDED DECISION — not debt, not open work.** 2026-08-18. Provenance:
`ADK-LENS-2026-08-18`. Filed so the same proposal from the same source is not re-litigated.

**The proposal.** Google ADK's plugin system runs 13 app-wide hooks with **first-non-`None`-wins**
semantics: handlers are consulted in order, the first to return a non-`None` value wins, and the
rest are skipped. Both ADK documents recommend mapping that onto our `register_*` ABI and
*"preserving the first-non-`None` middleware semantics."*

**Why it is declined: it would change our model, not extend it.** Our ~40 `register_*` hooks are
already one of two shapes, and neither has an ambiguity for first-non-`None` to resolve:

| Shape | Examples | Resolution |
|---|---|---|
| **Keyed — one handler per key** | `register_response_adapter(route_prefix, …)`, `register_route_guard(route_prefix, …)`, `register_execution_adapter(entity_type, …)` | The key *is* the disambiguator. There is no second handler to lose a race to. |
| **Run-all-and-collect** | `_event_handlers[event_type].append(…)`, `_startup_hooks`, `_agent_completion_hooks[run_type].append(…)`, `_capability_definition_providers` | Every handler runs and results are accumulated. Precedence is not a question because nothing is discarded. |

**★ The substantive objection, and it is the reason this is a decision rather than a backlog
item.** First-non-`None`-wins means **a handler's effect depends on registration order relative to
handlers it cannot see**. For a UI framework that is a convenience; for a substrate whose whole
proposition is that authority and effects are auditable, it introduces a silent, order-dependent
override path — one plugin can suppress another's participation by registering earlier, and
nothing in the audit trail records that it happened. Run-all-and-collect has the opposite
property: every participant is visible in the result.

**What would change the answer.** A concrete hook where exactly one handler must win *and* the key
cannot express which — i.e. a genuine policy-arbitration point rather than a fan-out. None exists
today. If one appears, the right shape is an **explicit, declared** arbiter (one registration, Tier
1, like `register_agent_planner_backend`), which is the same conclusion `DISPATCH-ADMISSION-1`
reached for admission policy — **not** implicit ordering.

**Do not confuse with `CAPABILITY-PROVIDER-TIMEOUT-1`**, which is about a provider loop failing
closed under contention. That is a defect in a run-all path, not an argument for first-wins.

---

## RETRY-CONTEXT-1 — a retry re-attempts the same call; it cannot make a better-informed one

**Status: OPEN — P2.** Filed 2026-08-18. Provenance: the GPT Engineer lens audit
(`C:\codev\gpt engineer\`, 2026-06-24), verified against source at `v2.4.0` on 2026-08-18;
write-up in that folder's `ACCURACY_CHECK_vs_aindy-runtime_2.4.0.md`.
**Independently half-derived by the Aider analysis — see below.**

**The gap, verified.** `execute_with_retry(fn, …)` (`core/retry_policy.py:224`) calls `fn()` **with
no argument carrying the prior failure**. The policy classifies the error to decide *whether* to
retry (`is_retryable_error`, `:210`) and sleeps between attempts (`_retry_delay_seconds`, `:179`),
and that is the whole of its interaction with the failure. Repo-wide,
`last_error|previous_error|failure_context|error_context` returns only `plugin_host.py` status
fields used for *reporting*; **nothing threads a failure into a retry.**

So the runtime can re-attempt an identical call. It cannot attempt a better-informed one.

**★ Derived twice, from unrelated systems, as two halves of one primitive:**

| Source | The half it names |
|---|---|
| **GPT Engineer** | *why the last attempt failed*, fed into the next — `_improve_loop` re-sends the diff-parse error (≤2); `self_heal` re-sends stdout/stderr (≤10) |
| **Aider** | *which sub-units failed and why* — *"3 of 5 hunks applied; here are the 2 that didn't"*, so the retry re-attempts only the failures. Its own audit names this as why its effective edit-success rate stays high with imperfect models |

**`EFFECT-PARTIAL-1` covers Aider's half and not this one.** A partial-success envelope answers
*which units failed*; it says nothing about a **whole-call** failure, which is the common case and
the one gpt-engineer's two loops exist to handle. The two entries are complements: one shapes the
*result*, this one carries it *forward*.

**★ Why it belongs at runtime level — the argument is about who owns the loop.** The runtime owns
`RetryPolicy`, `execute_with_retry`, the DLQ and stuck-run recovery. If the loop is runtime-owned
but only the app can use the failure, then any app wanting an informed retry must **reimplement the
loop** to reach the context. That is exactly what happened in the comparison corpus: **three
hand-rolled retry loops across two codebases** — `_improve_loop`, `self_heal`, and Aider's
`max_reflections=3` — each existing largely to carry a string forward. A substrate whose retry
primitive forces that reimplementation is not providing the primitive.

**★ The runtime carries; it does not interpret. This is the boundary that keeps the entry small.**
Reading a stderr blob, a diff-parse error or a schema violation and deciding what to do next is
**app content** and must stay app-side. The primitive is a **channel** — the previous attempt's
error made available to the next attempt — not a policy. Note the precedent already in the file:
`is_retryable_error` inspects the error string, but only to *classify*, never to *interpret*. Stay
on that side of the line.

**Guard rails, because two of these are how it goes wrong:**

1. **Bound it.** Attempt N carrying all N−1 prior errors is a context-window bomb in exactly the
   workload that retries most. Carry the last failure, or the last K, truncated — and decide that
   before writing it, not after someone's prompt overflows.
2. **It carries no authority and constitutes no effect.** Same three properties as
   `PROGRESS-CHANNEL-1`: not capability-bearing, not an `EffectRecord`, not part of an idempotency
   key. A retry that fails differently because of a carried error is still the same effect for
   dedup purposes.
3. **Do not let it become a control channel.** If a caller starts branching on the *shape* of the
   carried error rather than passing it to a model or a validator, the runtime has acquired an
   interpretation surface it said it would not own.

**Why P2.** Nothing is broken; retries work, they are merely uninformed. It rises with the first
consumer whose retry is model-driven — which is every coding-agent-shaped workload in the
comparison corpus, and none of the workloads running today (`SUBSTRATE-WITNESS-1`).

**★ Its consumer, from the same absorb register (recorded 2026-08-19, not filed separately):**
Devika's *"EXACTLY_ONCE retry/repair routing — failure → **repair handler** before DLQ."* Today the
path is retry-the-same-call → DLQ, with nothing between. A repair stage is the natural **consumer**
of this entry's payload: you cannot repair what you were not told broke. Not opened as its own
prefix because it is unbuildable before this entry and `RETRY-CLASSIFY-1` exist — a repair handler
needs both *what failed* and *what class of failure it was*.

**Related:** `EFFECT-PARTIAL-1` (the result shape this would carry), `AUTHORITY-NEGOTIATION-1`
(the other bounded-retry entry — note it is about retrying at *lower authority*, a different axis
that composes with this one), `PROGRESS-CHANNEL-1` (same no-authority/no-effect discipline).

---

## LANGGRAPH-NODUS-2026-08-18 — provenance note

**Not a debt item.** Fifth provenance label, after `AIDER-PORTABILITY-2026-08-17`,
`MAF-REFERENCE-2026-08-17`, `CREWAI-NODUS-2026-08-18` and `ADK-LENS-2026-08-18`.

Source: `C:\codev\Langgraph research\` — a LangGraph architectural audit and lens audit (both
2026-06-24) plus `LANGGRAPH_ON_NODUS_IMPLEMENTATION_STUDY.md` (2026-08-15), verified 2026-08-18
against Nodus `v5.0.4-2`, `C:\codev\nodus-showcase-langgraph`, and this runtime at `v2.4.0`.
Write-up: `ACCURACY_CHECK_vs_aindy-runtime_2.4.0.md` in that folder.

**★ Nine systems in, this is the folder that produced the one genuinely new gap — and it is a
reframe, not a missing feature.** Every other comparison asked *"what does the runtime lack?"*
This one asked what the *unit of scheduling* costs you, and answered:

> **The runtime checkpoints at the boundary of the unit it schedules, and any control flow inside
> that unit is invisible to recovery.**

Filed as `RECOVERY-GRANULARITY-1`. It also sharpened `FLOW-PARALLEL-1` (declaration-order merge
answers ordering, not conflict) and added a fourth meaning to `ECOGAP-1`'s replay taxonomy.

**★ Why this comparison is worth more than its coverage percentage.** LangGraph is the peer on the
axis this runtime is *strongest* on — durable, checkpointed, resumable orchestration. Every prior
audit named it as the reference: MetaGPT's called it *"the more runtime-like design"*, OpenHands'
*"far better at durable, inspectable, resumable orchestration."* **A comparison against the system
built for your strongest axis is the one that finds real gaps**, because agreement on everything
else strips out the noise. The June lens audit put coverage at 85–90%; the study's read of that is
the right one — *"the 10–15% is one mechanism, and it is the mechanism the peer was built around."*

**★ And the method note worth keeping, because it is where the finding came from.** The gap was
**volunteered by the artifact, not found by an auditor.** The showcase's own `EVALUATION.md`
states it (*"a crash mid-loop resumes that step as one unit"*), and its `README.md` opens by
conceding the axis it will lose: *"LangGraph's channel-state + reducer + pending-writes replay
engine is a **more rigorous** crash-consistency-of-state story than Nodus's snapshot model… This
showcase **deliberately picks the axis Nodus wins**."* Both quotes verified verbatim. A comparison
that names its scope selection *before* making its case produces findings a defensive one cannot.

---

## RECOVERY-GRANULARITY-1 — recovery granularity is welded to scheduling granularity

**Status: OPEN — P2 (cost and blast-radius, not correctness).** Filed 2026-08-18. Provenance:
`LANGGRAPH-NODUS-2026-08-18`.

**The property, stated once because it is the general form of three prior findings:**

> The runtime checkpoints at the boundary of the unit it schedules. **Any control flow inside that
> unit is invisible to recovery** — a crash re-runs the whole unit from its start.

**★ We already have the repair at one layer, which is what makes this precise rather than
aspirational.**

| Layer | Unit | Durable write ordering | Recovery |
|---|---|---|---|
| **Flow** | a node | `runner.py:347-359` commits a `FlowHistory` row carrying `input_state` + `output_patch` **before** the snapshot and `current_node` advance | resumes at the next node; the completed node's patch is already durable |
| **Agent** | a **segment** | `AgentStep` is a **post-segment batch write** — its own docstring says so (`agent_continuation.py:11`) | `_count_completed_segments` (`:110-118`) advances only on `total + n <= completed_steps`, so **a partially-executed segment restarts from step one** |

`DUR-4`'s fold docstring states the flow-layer ordering explicitly: *"the last FlowHistory row
commits **before** the snapshot advance, so it is at least as fresh as the snapshot for the last
completed node."* **That is pending-writes-then-checkpoint.** We built it once and did not carry it
down a layer.

**★ What it costs, and why this is P2 rather than P0 — do not overstate it.** `DUR-2`'s
`durable_effects_scope()` engages all three effect chokepoints declaration-free on a continued run,
so **mediated effects do not double-fire**. The re-run is *correct*. What it costs is **work**: a
crashed agent run **re-issues every LLM call in the partially-completed segment**, and any
**un-mediated** side effect inside that segment re-fires — which is the residual `ECOGAP-1` already
names, here given a granularity. For an agent runtime, re-issuing LLM calls on every recovery is a
cost-and-latency problem worth fixing, not a soundness one.

**The mechanism to copy, and it is specific.** LangGraph's **pending-writes-then-checkpoint**:
completed node writes are persisted *before* the consolidated checkpoint, and on resume finished
tasks are **replayed rather than re-executed**. Applied here: commit each `tool_step`'s result
durably as it completes, so continuation resumes *inside* a segment.

**★★ A worked reference at exactly our missing granularity (added 2026-08-19, DBOS `e0b742c`,
MIT, 31 650 LOC).** `operation_outputs` is **one row per completed step**, keyed
`(workflow_uuid, function_id)` with `output`, `error` and `child_workflow_id`. Before any step runs,
`_core.py:2098-2115` calls `check_operation_execution(workflow_id, function_id, name)` and, if a
record exists, logs *"Replaying transaction"* and **returns the recorded output or re-raises the
recorded error — the step does not execute.** That is row 4 at **step** granularity: precisely what
we have at the flow node and lack at the agent step.

**★ And it answers "how do you know what to replay" more cheaply than the vector clock below.**
DBOS uses a **monotonic per-workflow ordinal** (`function_id`) — position *is* identity. That works
because its workflows are sequential by default; a vector clock earns its keep only once branches
advance independently, i.e. when `FLOW-PARALLEL-1` lands. **They are not competing answers — they
are the pre- and post-fan-out answers, and the ordinal is the one to build first.**

**★ The constraint it buys with, so it is not conflated with the declined row 2:** replay-by-ordinal
requires steps to be issued in a **stable order**, not deterministic *code*. `ECOGAP-1`'s three
reasons for declining row 2 still do not apply.

**★ The companion idea, which is the cheap part: `versions_seen` as one vector clock.** LangGraph
tracks `versions_seen` against `channel_versions`, and the **same comparison drives both
incremental scheduling (what is ready) and resume (what was already done)**. That answers the
question two prior studies left dangling — *how do you know what to replay* — with **one data
structure instead of a second bookkeeping system**. Do not invent a separate "what was done" ledger
if this is built.

**★ Two peers arrived at this independently.** LangGraph: pending-writes-then-checkpoint. MAF: a
parent-linked per-superstep chain with executor state flushed *before* the checkpoint. Both are
system-fact convergence — they built it.

**★ Corrected 2026-08-19, and the correction matters more than the entry.** This paragraph said
**three**, listing OpenHands' *"immutable per-event blob log"*, and called it *"the strongest
convergence signal in the whole comparative corpus."* Verified against OpenHands source
(`filesystem_event_service.py:35-36` — `model_dump_json` then `write_text`, one file per event):
**that is taxonomy row 1, an event-sourced record, which this runtime already ships as `DUR-4`.**
It is the thing you rebuild state *from*, not a write-before-checkpoint ordering. **The finding
survives on two arrivals; the superlative does not.** It was cited from a sibling document's
one-line summary without reading the system — the exact failure
`COMPARATIVE_RESEARCH_INDEX.md` §5 warns about, committed two entries after that warning was
written.

**★ It does NOT reopen the declined decision, and the distinction is load-bearing.** `ECOGAP-1`
declined *kernel deterministic replay* because determinism is a VM concern, because forward-resume
never re-executes code, and because it constrains every line of workflow code. **None of those
apply here.** This persists a finished unit's *result* before the consolidating checkpoint; it
imposes no constraint on the code and intercepts no non-determinism. See the fourth row of
`ECOGAP-1`'s replay taxonomy.

**The trade to weigh before building.** The alternative to pending writes is *smaller scheduled
units* — more checkpoints, more overhead, more rows. Pending writes is the option that **decouples
recovery granularity from scheduling granularity** instead of forcing them to move together, which
is why it is the right shape and not merely the popular one.

**★ A third, adjacent shape worth recording here rather than filing (SWE-agent, 2026-08-19):
salvage-on-terminal-failure.** Its absorb list asks to *"recover the last durable step's effect
rather than only marking FAILED"* — i.e. when you **cannot** continue, extract what completed
instead of discarding it. That is neither this entry (*resume* mid-unit) nor `EFFECT-PARTIAL-1`
(*report* a batched effect's partial outcome); it is what to do at a **terminal** boundary. It
becomes buildable only once the per-step record this entry proposes exists, which is why it is
recorded here rather than opened separately.

**Related:** `ECOGAP-1` (the taxonomy and the un-mediated-effect residual), `PROGRESS-CHANNEL-1`
(observability of mid-unit progress — a different guarantee; that one makes progress *visible*,
this one makes it *durable*, and neither implies the other), `ORCHESTRATOR-SPLIT-1` (a fourth store
with its own claim/wait/retry state that would need to agree with any of this).

---

## COST-GOVERNOR-1 — every quota exists except the one that matters for an LLM runtime

**Status: OPEN — P1.** Filed 2026-08-18. Provenance: `METAGPT_ON_AINDY_RUNTIME_PORTABILITY_ANALYSIS.md`
(`C:\codev\MetaGPT research\`, 2026-08-15, its **M2**), verified against source at `v2.4.0`.
**The last verified-but-unfiled gap across ten comparative research folders.**

**The gap, measured.** `kernel/resource_manager.py:71-74` defines exactly four quota dimensions:

| Dimension | Default |
|---|---|
| `MAX_WALL_TIME_MS` | 300 000 (5 min) |
| `MAX_MEMORY_BYTES` | 268 435 456 (256 MiB) |
| `MAX_SYSCALLS_PER_EXECUTION` | 100 |
| `MAX_CONCURRENT_PER_TENANT` | 5 |

**No token, cost, spend or budget dimension exists.**

**★ And it is worse than a missing cap — there is no meter.** A repo-wide grep for
`prompt_tokens|completion_tokens|total_tokens|token_usage` returns only
`runtime/memory/context_builder.py` and its callers, where `_estimate_tokens` sizes a **memory
context** to fit a prompt window. **Nothing anywhere captures token usage from an LLM response.**
So this is not "we measure spend and fail to cap it" — the quantity is never observed. Any fix is
**meter first, then govern**, and the meter is the larger half.

**★ The one-line statement of why this is odd rather than merely incomplete**, worth keeping
verbatim from the source analysis:

> the runtime enforces a **300-second wall-clock ceiling** and a **256 MiB memory ceiling** on
> execution units whose actual dominant cost is **tokens**, which it does not measure at all.

**Why it belongs at runtime level — it passes the absorption tests more cleanly than most entries
in this file.**

- **Generality** — every LLM-executing workload has a spend dimension regardless of what the agent
  does. Nothing about it is coding-agent-shaped or A.I.N.D.Y.-agent-shaped.
- **Enforcement** — ★ **an agent cannot bound its own spend credibly, because it is the thing
  spending.** This is the same argument that puts concurrency limits in the runtime rather than in
  a well-behaved caller.
- **Invariant** — *"this tenant cannot exceed budget B"* is unavailable to any higher layer,
  exactly as *"this tenant cannot exceed 5 concurrent executions"* is, and the runtime already owns
  the second.
- **Layer** — it sits beside four quotas that already exist, resolved at the same seam, in the same
  vocabulary.

**★ Do NOT fold this into `BILLING-2`, and the distinction is load-bearing.** That entry is
*"Metering model not chosen"* — per-seat vs per-agent-run vs usage-based — **deferred until
commercial launch**, with the reopen trigger *"before billing infrastructure or Stripe integration
begins."* That is **revenue metering**: different consumer, different accuracy requirement,
different trigger, and a decision that can wait. A cost **governor** is a runtime quota that stops
execution, and it is needed **the first time an agent loops**, not the first time an invoice is
issued. A shared meter could serve both; the two decisions must not be coupled, because deferring
the governor behind a commercial-launch gate is how a runaway run becomes a bill.

**★ Converges with a finding from an unrelated comparand.** The Linux kernel audit's Lesson 5
(recorded on `EXEC-ENV-BIND-1`) argues isolation decomposes into orthogonal axes — **visibility /
resources / authority** — and notes the *resources* axis lives in `resource_manager`, is resolved
at a different seam, and has never been folded into the environment descriptor. **This entry says
that same axis is missing its dominant dimension.** A kernel audit and a multi-agent-framework port
arriving at the same under-served axis from opposite directions is the reason both are P1 rather
than P2.

**The reference implementation, for shape only.** MetaGPT's `CostManager` is checked at every round
boundary and raises `NoMoneyException` on breach; its own architectural audit calls the budget
governor one of only four *"architectural ideas worth adopting."* Its scaling analysis notes token
cost grows **superlinearly with rounds × roles** and that *"budget cap is the only backstop"* — a
system with **only** the cost quota, meeting a runtime with **every quota except** the cost one.

### ★★ All four design questions below are answered in shipped code (added 2026-08-19)

Provenance: LiteLLM `c696fdf` (MIT), `litellm/proxy/spend_tracking/budget_reservation.py` —
**1 322 lines devoted to reservation alone**. Write-up: `LITELLM_ON_AINDY_RUNTIME_AUDIT.md`.

**The mechanism is reserve → call → reconcile:**

```
reserve_budget_for_request(...)      :148   estimate, ATOMICALLY PRE-FILL counters, return reservation
reconcile_budget_reservation(...)    :257   replace reserved with actual, per entry, then finalize
release_budget_reservation(...)      :276   = reconcile(actual_cost=0.0) — the call never happened
release_budget_reservation_on_cancel :283   reconcile a still-open reservation cancelled mid-flight
```

**★ Q3 — "estimated or actual?" — the question this entry could not settle. The answer is BOTH.**
Reserve an *estimate* so admission is decidable before spending; reconcile to the *actual* after.
**The estimate never becomes the record.** And the concurrency subtlety is recorded in their own
comment, which is the kind of thing only shipping teaches:

> *"The reservation path admits at the strict-`<` boundary and **atomically pre-fills the same
> counter** we'd read here. Re-checking with `>=` would reject a request the reservation already
> admitted…"*

A naive check is read-then-compare, which N concurrent requests all pass. **A reservation makes
admission and accounting one atomic operation.**

**★ Q1 — where checked:** an `async_pre_call_hook` (`hooks/max_budget_limiter.py`) against a
**`DualCache`** (Redis + in-process) counter. **The hot path never reads the database.**

**★ Q2 — whose budget:** six scopes *concurrently* — key, user, team, team_member, org, end_user,
plus tags. **Ours should still start with two** (per-tenant and per-run, as proposed above); the
point is that the counter design must not assume one.

**★ Q4 — fail open or closed:** a `fail_closed_budget_enforcement: bool = False` parameter —
configurable, defaulting **open**. **Take the configurability, not the default:** theirs suits a
gateway whose failure mode is a refused customer call; ours already answers this shape at
`resource_manager.py:595, :639` (closed in prod, open in dev/test) and should stay consistent with it.

**★ And the operational half this entry called "the bigger half" is bigger than stated.** The
metering infrastructure dwarfs the enforcement: async spend writes off the hot path
(`db/db_transaction_queue/spend_update_queue.py`), bulk daily aggregation, periodic budget-window
resets (`common_utils/reset_budget_job.py`) — and, the part that would **not** be designed from
first principles, **partitioning and a cleanup job** for the spend log
(`spend_logs_partition_manager.py`, `spend_log_cleanup.py`). One row per LLM call needs retention
management before it needs anything else.

**Design notes worth settling before building, because they decide the shape:**

1. **Where is it checked?** The natural seam is the same one the other quotas use
   (`resource_manager.can_execute` / `check_quota` at dispatcher entry and per flow node), but
   spend accrues *inside* an LLM call, not at a syscall boundary. A per-EU running total updated
   after each LLM response, checked at the next boundary, is the cheap version — and it inherits
   `CANCEL-REACH-1`'s limitation: **the breach is observed between effects, not during one.**
2. **Whose budget?** Per-tenant is the analogue of `MAX_CONCURRENT_PER_TENANT`; per-run is the
   analogue of `MAX_SYSCALLS_PER_EXECUTION`. MetaGPT's is per-team-run. Probably both, as the
   existing quotas already are.
3. **★ Estimated or actual?** Providers return usage in the response, so *actual* is available —
   but only **after** the spend. A pre-flight *estimate* is the only thing that can refuse a call
   before it costs money, and estimates are wrong. Decide which one the ceiling is defined against
   before writing it, or the first over-budget run will be an argument about semantics.
4. **Fail-closed or fail-open on a metering failure?** `resource_manager` already answers this for
   the other dimensions — closed in prod, open in dev/test (`:595, :639`). Follow it rather than
   inventing a second policy.

**Why P1 and not P0.** Nothing is broken and no exposure is claimed: the workloads running today
are first-party and small (`SUBSTRATE-WITNESS-1`). It is P1 rather than P2 because it is the only
quota whose absence is *unbounded* — wall-time, memory and syscalls all self-limit at some ceiling,
and spend does not.

**Related:** `EXEC-ENV-BIND-1` (the resources axis this belongs to), `BILLING-1..5` (revenue
metering — adjacent consumer, do not couple), `CANCEL-REACH-1` (a breach detected mid-effect has
the same reach problem), `SYSMAX-1/-3/-4` (the existing per-EU caps, some advisory).

---

## INITIATOR-IDENTITY-1 — the identity that initiates work is not the identity the runtime authenticates

**Status: OPEN — P2 today, P0 the day an inbound-driven consumer ships.** Filed 2026-08-18.
Provenance: `OPENCLAW_ON_AINDY_RUNTIME_AUDIT.md` (`C:\codev\openclaw_research\`) — an audit
performed to fill the one folder in the comparative corpus that had no runtime-facing document.
**It is the only finding in twelve folders that required an inbound-event-driven comparand to
produce.**

**The runtime's assumption, stated where it is made.** `kernel/tenant_context.py:13` — *"A.I.N.D.Y.
uses a single-user-per-tenant model: tenant_id == user_id."* The dispatcher establishes it by
requiring an authenticated caller (`syscall_dispatcher.py:405-409`), and exactly two tables carry
`tenant_id`. Every mechanism downstream — memory namespacing, per-tenant quota, the audit trail —
resolves against that one identity.

**★ The assumption holds only while work is *requested*.** Every consumer the runtime has been
designed against, and every port the corpus has proposed (Aider, Codex, MetaGPT), is
**user-initiated**: an authenticated caller asks, and the caller is the subject. **An
inbound-event-driven consumer breaks that.** OpenClaw listens on Discord, Telegram, Slack, Matrix,
Signal, iMessage, WhatsApp and LINE; work begins when *a message arrives*. Its unit of scoping is a
composite **session key** — account + agent + peer — built per inbound message
(`resolve-route.ts:86`, `:291`), with DM access decided separately (`allow | block | pairing`).

**A channel peer is not a runtime user.** An operator running such a consumer has **one**
authenticated identity — their own — and **N** end-user identities the runtime cannot name,
authenticate, or scope to.

**What collapses, concretely:**

| Mechanism | Intended | What actually happens |
|---|---|---|
| Memory namespacing `/memory/{tenant}/…` | per subject | **per operator** — every peer's recall shares one space, and one peer can recall another's context |
| `MAX_CONCURRENT_PER_TENANT = 5` | per subject | **per deployment** — one chatty channel starves every other |
| Audit trail / `SystemEvent` attribution | who did this | records *the operator did this*, when what happened is *a peer asked and the operator's agent did it* |
| Capability token | scoped to the subject | scoped to the operator; the peer's request is indistinguishable from the operator's own |

**★ This is not the multi-tenancy entry, and conflating them loses the finding.**
`DEPLOY-TARGET-2` is *"can we run many operators on one deployment"* — a scaling question, deferred
to a commercial trigger. **This is the opposite direction: one operator, many end users, and no
vocabulary for the second.** A perfectly single-tenant deployment has this problem the moment it
faces a channel.

**★ And it is not `PAYMENTS-ARCHITECTURE-1` question 3**, which asks the *billing* version — *"who
is the billing subject, the operator or end-users of the operator's product?"* — and defers it to
Stripe work. **The execution and authority version is this entry**, it is answerable independently,
and it is needed first: you cannot bill a subject you cannot name, but you *can* leak one
end-user's memory to another long before anyone invoices.

**What a fix would need, sketched only — do not build on this sketch.** An **acting-subject**
distinct from the authenticated caller: carried on `SyscallContext` beside `user_id`, namespacing
memory, dimensioning quota, and appearing in `SystemEvent` attribution — **without** being an
authentication claim, because the runtime cannot verify a Discord handle. That distinction is the
whole design problem: it is an *attributed* identity the caller asserts, not an *authenticated*
one, and the runtime must be honest about which it is holding. Compare `AUTHORITY-VALUE-1`: the
mistake to avoid is letting an asserted value silently acquire the standing of a verified one.

**★★ The accounting half is shipped by a peer in our own domain (added 2026-08-19, LiteLLM).**
`reserve_budget_for_request` takes `end_user_id` / `end_user_object` and resolves a dedicated
counter (`_get_end_user_budget_counter` :443) **alongside** the key/user/team/org counters. The API
key is the authenticated caller; **`end_user` is who the operator says the call is *for*** — and it
carries its own budget.

**★ It is an ATTRIBUTED, not authenticated, identity — exactly the distinction this entry insists
on.** LiteLLM cannot verify an `end_user_id`; the caller asserts it. It is used for **accounting and
limits, never for authorisation.** That is a shipped instance of the shape sketched above, and it
shows the accounting half is useful *even where the authorisation half is impossible*.

**★ The rule to carry over, and it is the one that keeps this safe: an asserted subject may only
CONSTRAIN, never WIDEN or SELECT.** LiteLLM's `end_user` can only reduce available spend. **If an
acting subject were allowed to namespace memory, an asserted value would be selecting a namespace —
a read-authorisation decision wearing accounting clothes.** Constrain-only is the boundary between
this being useful and this being a hole.

**★ Deliberately not proposed: making the peer a real `User` row.** That converts every Discord
contact into an account, drags registration, auth and the admin-bootstrap constraint into a chat
adapter, and gets the trust level exactly wrong — the runtime would be *authenticating* an identity
it has no way to verify.

**Why P2 now.** No consumer today is inbound-driven; `SUBSTRATE-WITNESS-1` records that the one
first-party consumer talks to the HTTP API and routes no effects. **It is P0 the day one ships**,
because the first symptom is cross-peer memory recall, which is a data-leak class rather than a
tidiness class, and nothing in the current model would flag it.

**Related:** `SUBSTRATE-WITNESS-1` (no consumer exercises any of this yet), `DEPLOY-TARGET-2`
(many operators — the other axis), `PAYMENTS-ARCHITECTURE-1` q3 (the billing version),
`AUTHORITY-VALUE-1` (asserted vs verified values — the trap to avoid), `TENANT-2` (per-tenant
quota, which inherits the wrong subject).

---

## LEASE-FENCE-1 — the background lease has no fencing token, so a stale leader's writes are indistinguishable from the real one's

**Status: OPEN — P2, defence-in-depth.** Filed 2026-08-18. Provenance:
`PI_ON_AINDY_RUNTIME_AUDIT.md` (`C:\codev\openclaw_research\`) — an audit of Pi, the agent-loop
library OpenClaw embeds. **It is the one primitive in the comparative corpus that an excluded layer
has and this substrate does not.**

**The gap, verified.** `db/models/background_task_lease.py` carries `name`, `owner_id`,
`acquired_at`, `heartbeat_at`, `expires_at`. A repo-wide grep for `fence|fencing|epoch` under
`AINDY/` returns **nothing**. `platform_layer/leadership.py`'s own docstring states the model:

> *"Row owned by us → renew … **Row expired → take over.**"*

**★ What expiry alone cannot do.** A process that loses its lease to expiry — a GC pause, a disk
stall, a network partition — **has no way to learn that before acting**. It finds out at its next
renew attempt, and everything it did in the interval it did believing it was leader. Expiry bounds
**how long** two leaders coexist. It does nothing about **what the stale one wrote** in that window,
because its writes are indistinguishable from the incumbent's.

**A fencing token closes exactly that.** The reference, from Pi's
`session-backends/sqlite-node/src/sqlite/storage/writer-leases.ts`:

```sql
INSERT INTO writer_leases (session_id, owner_id, fence, expires_at_ms)
VALUES (…, …, 1, …)
ON CONFLICT … DO UPDATE SET
    fence = writer_leases.fence + 1,          -- monotonic, bumped on every takeover
    expires_at_ms = excluded.expires_at_ms
  WHERE writer_leases.expires_at_ms <= <now>  -- only steal an expired lease
RETURNING owner_id, fence, expires_at_ms
```

A conditional upsert that takes the lease only from an expired holder and **increments a monotonic
fence on every takeover**. The holder then carries that fence on its writes, and the store rejects
anything presenting a lower one. **The stale leader is not asked to notice it is stale — it is
refused.**

**★ Severity, stated honestly rather than dramatically — this is not a live corruption path.** Our
lease guards *background leadership* (which instance runs maintenance jobs), **not individual
writes**. Two leaders briefly both running orphan recovery and cleanup is bounded, and those jobs
are largely idempotent. Nothing observed suggests this has bitten.

**★ It is filed anyway, for three reasons, and the third is the one that matters:**

1. The fix is **one integer column and one comparison** — smaller than most entries in this file.
2. The failure mode is **invisible when it happens**. There is no error, no log line, and no row
   that says two leaders acted; you would infer it from duplicated side effects long afterward.
3. **An agent-loop library has the better primitive than the substrate whose job this is.** Pi is
   the layer this corpus excludes from the runtime thirteen times over — and it fenced its leases
   while we did not. That is the whole argument for the entry.

**Scope note — do not over-build it.** Pi fences a **per-session writer** lease; ours is a
**per-role leadership** lease. The analogous change is a fence on the leadership row plus a check
wherever a leader-only write happens, **not** a general fencing framework. `LEASE-1` is closed and
its mechanism is sound; this extends it, it does not reopen it.

**★ Where it would actually pay, and it is worth checking before building:** the leader-only paths
are the scheduler's maintenance jobs — `_recover_orphaned_approved_runs`, the effect-record TTL
cleanup, `requeue_stale_jobs`, the stuck-run watchdog. Several already have their own guards
(`AGENT-APPROVE-001b`'s 10-minute threshold, `EffectRecord` status filters). **A fence is most
valuable on whichever of those is least idempotent** — establish that first rather than fencing
uniformly.

### ★★ Second witness, and the gap was named eight weeks before it was filed (added 2026-08-19)

**Temporal fences shard ownership with exactly this primitive**, verified in source
(`C:\codev\Temporal research\temporal`):

> `common/persistence/data_interfaces.go:144` — *"**ShardOwnershipLostError** is returned when
> conditional update fails due to **RangeID** for the shard"*

with `PreviousRangeID` and `RangeID` carried on the update requests (`:183-228`). Every write
presents the RangeID it believes it holds; a conditional update **fails** if the shard has been
re-acquired. **The stale owner is refused, not eventually informed.** That is the same mechanism as
Pi's `fence`, in the industry's reference durable-execution engine, as its **core shard-ownership
mechanism** rather than an incidental feature.

**So this is system-fact convergence ×2 — Temporal and Pi** — and by the taxonomy in
`COMPARATIVE_RESEARCH_INDEX.md` §5 that is the strong kind: both *built* it, neither is an
auditor's proposal.

**★ And the uncomfortable part, recorded because the process lesson outlives the entry:
`TEMPORAL_AINDY_LENS_AUDIT.md` named this gap on 2026-06-24** — *"a **monotonic-RangeID storage-CAS
fence** (vs aindy's DB-lease, now real but weaker)"* — listing it among the advantages that
**survive** correction, and mentioning `RangeID` six times. **It was never filed.** `LEASE-1` closed
the same day, the lease became "real," and the observation that it was *"weaker"* in a specific,
named way went into a document and nowhere else. This entry was opened 2026-08-18 from Pi, believing
it new; it was eight weeks old.

**The lesson is not "read the audits."** It is that **an audit's *surviving-gap* list is a filing
queue and was never treated as one** — six of the June lens audits carry one, and this is the second
item found in them that had no registry entry (cf. `COST-GOVERNOR-1`, where the same audit family
recorded a capability as *Covered* on the strength of a sentence about where it belonged).

**Related:** `LEASE-1` (closed — the lease mechanism this extends), `ORCHESTRATOR-SPLIT-1` (store 4,
`nodus_lang_workflow`, has its own `claim` with no fence either — same gap, different store),
`IDEM-11`/`IDEM-12` (the effect-side answer to duplicate work, which a fence complements rather
than replaces), `RETRY-CLASSIFY-1` (note that Temporal's fence failure is a **named error type**,
`ShardOwnershipLostError` — a class, not a matched substring).

---

## AUTHORITY-LIFETIME-1 — the capability token is bound to the clock, not to the execution it authorises

**Status: OPEN — P2, additive hardening.** Filed 2026-08-19. Provenance:
`OPENHANDS_ON_AINDY_RUNTIME_PORTABILITY_ANALYSIS.md` (`C:\codev\OpenHands_research`, its **O3**),
verified on both sides.

**The gap.** `agents/capability_service.py:25` — `TOKEN_TTL_HOURS = 24`, with `expires_at` threaded
through mint and verify and a rotation grace key ring. A grep of that module for
`revoke|revocation|invalidate` returns **nothing**.

**So a capability token minted for a run that finished in ninety seconds stays valid for the rest
of the day.** Nothing about the run reaching `completed`, `failed` or `cancelled` invalidates the
authority that run was issued. The token is bound to wall-clock time and to nothing else.

**The reference, verified in source.** OpenHands mints a fresh 32-byte session key per sandbox and
binds it to the sandbox's *lifecycle state* — `openhands/app_server/sandbox/session_auth.py:13`:

> *"Session API keys are only valid while the sandbox is RUNNING."*

Enforced at `:76` (`if sandbox_info.status != SandboxStatus.RUNNING`), owner-checked at `:103`,
nulled on pause and rotated on resume. **A stolen key is useless the moment the sandbox pauses.**

**★ These compose rather than compete, which is why this is additive and not a redesign.**

| Question | Answered by |
|---|---|
| *What may this bearer do?* | `capability_ceiling` + granted tools — **ours, and materially stronger; OpenHands has no grant set, ceiling or delegation semantics at all** |
| *While what is true?* | RUNNING-only — **theirs, and we have no analogue** |

The primitive is **authority bound to execution lifecycle**: mint at unit start, invalidate at unit
terminal state, rotate on resume. It closes a window a 24-hour TTL leaves open **by
construction** — not by oversight, and that distinction matters when costing it.

**What it costs, stated honestly.** A **revocation check** the token model does not currently have.
Today verification is pure crypto — HMAC compare against a key ring, no I/O. Binding to lifecycle
means the verifier must consult run state, which turns a stateless check into a stateful one on the
hot path. **That is the real design question, and it should be settled before any code:**

1. **Where does the check live?** `execute_tool`'s capability branch already has a DB session in
   hand, so the marginal cost there is small. `SyscallDispatcher` does not necessarily.
2. **Cache or not?** A terminal-state check is cacheable *negatively* — once a run is terminal it
   stays terminal — so a small negative cache is sound where a positive one is not.
3. **Fail open or closed on a lookup failure?** `resource_manager` already answers this shape for
   quota — closed in prod, open in dev/test (`:595, :639`). Follow it rather than inventing a
   second policy.

**Why P2.** No exploit path is claimed. The token is HMAC-signed and unforgeable, it is scoped by a
ceiling, and every entry point has its own authorisation — so this is **defence-in-depth on an
already-strong component**, narrowing a window rather than closing a hole. It rises if tokens ever
travel further than the process that minted them.

**★ Do not conflate with the two adjacent entries.** `AUTHORITY-VALUE-1` is about capabilities that
are *caller-supplied values* rather than issued claims — a different weakness in a different
mechanism. `KEY-SCOPE-ESCALATION-1`'s *"survives revoking the key"* was about API-key scopes, and is
closed. **This one is about a correctly-issued, correctly-scoped token outliving the thing it was
issued for.**

**Related:** `AUTHORITY-VALUE-1`, `AUTHORITY-NEGOTIATION-1` (a token amendment path —
`amend_token` there and lifecycle binding here are the two halves of "the token should track the
run"), `CANCEL-REACH-1` (cancellation that does not reach an in-flight effect has the same
between-boundaries limitation any revocation check would).

---

## RETRY-CLASSIFY-1 — retryability is decided by substring matching on an error string, in five places

**Status: OPEN — P2.** Filed 2026-08-19. Provenance: `SWE_AGENT_AINDY_LENS_AUDIT.md`
(`C:\codev\swe agent research\`), whose absorb list asks for a **declarative error policy table**
to replace *"the fragile sentinel-string channel"* — and points correctly at us.

**The classifier.** `core/retry_policy.py:95`:

```python
_NON_RETRYABLE_SUBSTRINGS: tuple[str, ...] = (
    "permission", "unauthorized", "forbidden", "not found",
    "404", "401", "403", "invalid", "blocked by policy",
)
```

`is_retryable_error(error)` lowercases the message and returns `not any(substr in lower …)`.

**★ It is wired far more widely than the module implies — five real call sites, not a helper
awaiting adoption:**

| Site | What it decides |
|---|---|
| `flow_engine/runner_steps.py:266` | whether a failed flow node retries |
| `runtime/nodus_adapter.py:279` | whether a tool result is retried |
| `runtime/nodus_worker.py:389` | **registered as a Nodus host function** — guest scripts call it directly |
| `runtime/agent_plan_compiler.py:55, :67, :145` | **emitted into generated agent plans** as `is_retryable_error(__result_0["error"])` |
| `core/retry_policy.py:228, :250` | the default classifier for `execute_with_retry` |

**The failure modes are concrete.** Substring matching on a lowercased message means:

- **`"404"` matches a duration** — `"request took 404ms"` — and an ID, a port, a byte count. Any
  transient failure whose text happens to contain those three characters becomes permanently fatal.
- **`"invalid"` matches `"invalidated cache"`** and `"cache invalidation in progress"`.
- **`"not found"` matches a DNS message** and a row-not-yet-committed race.
- **`"permission"` matches `"permission granted"`.**

**★ Direction of failure, and why it is P2 rather than P1.** A false positive **gives up on a
retryable error** — the run fails permanently where a retry would have succeeded. That is the safer
of the two directions; nothing is executed twice and no effect is duplicated. It is filed because
it is **silent**: nothing records *"this was classified non-retryable,"* so the outcome is
indistinguishable from a genuine hard failure, and a support conversation about it starts from no
evidence.

**★ The sharpest instance is the generated one.** `agent_plan_compiler.py` emits this call **into
Nodus source**, so an agent's retry behaviour — inside a compiled plan, inside a guest VM — is
decided by substring matching on an error string that may itself have been shaped by a model. A
tool whose error text an LLM influences can, in principle, influence whether its own failure is
retried.

**The shape to build, from the absorb item.** A **typed, declarative classification table**:
errors carry a class (`transient | permission | not_found | policy | fatal`) set at the raising
site or mapped from a typed exception, and the policy maps class → action. Substring matching
survives only as a **last-resort fallback for un-classed errors**, and when it fires it should say
so in the retry record.

**★ Sequence it with `RETRY-CONTEXT-1`; they are the two halves of one retry story.** That entry is
about carrying *what went wrong* **forward** into the next attempt. This one is about **classifying**
it in the first place. A typed error class is exactly what a carried failure should contain, so
building either one without the other means designing the same payload twice.

**One thing verified and NOT a finding, recorded so it is not re-derived.** The docstring on
`is_retryable_error` reads *"Current system does not use this — it is here as the central place to
add the check when callers adopt it."* **That is stale, not true**: callers adopted it in the five
sites above. It is a documentation defect, and it is **not** a fourth instance of the
published-and-unconsumed family (`ROUTE-AST-UNWIRED-1`, `DEBT-COMPAT-1`) — which is what it looked
like on first reading.

**Related:** `RETRY-CONTEXT-1` (the other half), `EFFECT-PARTIAL-1` (a partial result is a third
outcome this binary classifier cannot express), `AUTHORITY-NEGOTIATION-1` (a `CAPABILITY_DENIED`
is exactly the kind of error that should carry a class rather than a matched substring).

---

## EVENT-OUTBOX-1 — system events are buffered in memory and emitted after the work commits, so a crash loses the record of work that happened

**Status: OPEN — P2.** Filed 2026-08-19. Provenance: the **consolidated absorb register** in
`C:\codev\Ecosystem_Coverage_Analysis_v2.md` — *"RangeID monotonic CAS fence **+ transactional
outbox** (Temporal)"*. The fence half became `LEASE-FENCE-1`; **this is the other half, and it had
no entry.**

**The mechanism, traced.**

1. `core/execution_signal_helper.py:16` — `queue_system_event(...)`. While the pipeline is active it
   does **not** write anything. It appends the event to
   `ctx.metadata["queued_execution_signals"]["events"]` — **an in-memory dict on a ContextVar** —
   and returns a *provisional* UUID.
2. The handler runs and commits its own work. Flow nodes do exactly this:
   `flow_engine/runner.py:359` commits the `FlowHistory` row before the snapshot advance.
3. **Only after the handler returns** does the pipeline flush: `pipeline.py:146` extracts the
   signals, `:171` applies them, and `execution_pipeline/signals.py:111+` loops the buffer calling
   `emit_system_event`.
4. That loop wraps each emit in `try: … except Exception:` and swallows.

**So there is a window — between the handler's commit and `pipeline.py:171` — in which the work is
durable and the record of it is not.** A crash there loses the event. An emit failure there loses
it silently.

**★ What is lost is not an effect, which is why this is P2 and not P0.** `SystemEvent` rows are the
**audit and causal record**, not the work. Nothing is duplicated, nothing is corrupted, no effect
re-fires. What is lost is *the evidence that something happened.*

**★ And that is exactly the asymmetry the OpenHands analysis named, now with a mechanism behind
it:**

> A.I.N.D.Y. has the better **index** over its events and the weaker **record**.

`build_trace_graph`, `parent_event_id`, `get_downstream_effects`, MAS path queries and pgvector are
a genuinely strong index. **They index a record that can silently lose rows.** A hole in the causal
graph is worse than a missing join (`AUDIT-CORRELATION-1`) because a missing join is visible and a
missing row is not — the graph simply reads as though the work never occurred.

**Second-order consequence worth checking before this is scoped:** `memory/memory_capture_engine.py`
reads `get_downstream_effects(...)` to decide what to capture. **A lost event is therefore also a
potentially-missed memory capture**, which is silent in a second system. That coupling should be
confirmed or ruled out first — it decides whether this is an observability item or a memory-loop
item.

**The primitive: a transactional outbox.** Write the event into the **same transaction** as the
work — an `outbox` row committed atomically with the `FlowHistory`/`EffectRecord` write — and let a
relay drain it to `SystemEvent` and the event bus afterwards. **The crash window closes because
there is no window: either both landed or neither did.** Redelivery becomes at-least-once, which is
correct for an audit record and is what the relay's dedupe key handles.

**★★ A cheaper fix than the one proposed above (added 2026-08-19, from DBOS).** DBOS has no outbox
table and no relay — and no window either, because **its event store *is* its workflow store**:
`set_event_from_workflow` opens `with self.engine.begin() as c:` and writes both the step record
(`_record_operation_result_txn(…, c)`) and the `workflow_events` insert **on that one connection**.
Atomicity is free when there is nothing to span.

**`SystemEvent` already lives in the same PostgreSQL as `FlowRun`, so the same option is open to
us.** The cheaper repair is therefore **write the event on the handler's own connection inside the
handler's transaction**, keeping the buffer only for events that genuinely cannot be known until
after commit. That is materially less machinery than an outbox row plus a relay, and it closes the
same window. **Scope the co-location fix first; reach for the outbox only if something must span
two stores.**

**★ Do not "fix" this by emitting eagerly instead of buffering.** The buffer exists for a reason —
provisional IDs let a handler reference an event it has not written yet, and batching keeps
per-node event emission off the handler's critical path. Eager emission trades this gap for a
worse one: events written for work that then rolls back, i.e. a record of things that did **not**
happen. **The outbox is the shape that gets both.**

**Scope note.** The `required: bool` flag on `queue_system_event` is carried through to
`emit_system_event` but does **not** change *when* the emit happens — a `required=True` event is in
the same buffer and the same window. If a cheap interim mitigation is wanted, making `required`
events bypass the buffer is it; that is a narrowing, not a fix.

**Related:** `AUDIT-CORRELATION-1` (joins the trail cannot make — this is the row it cannot make
them *from*), `RECOVERY-GRANULARITY-1` (the flow layer already commits its `FlowHistory` row before
the snapshot; the event for that same node does not get the same treatment), `LEASE-FENCE-1` (the
other half of the same absorb bullet), `EVENTBUS-PUBLISH-LATCH-1` (closed — publish-side circuit
breaker; this is the write side and is a different failure).

---

## DBOS-PEER-2026-08-19 — provenance note

**Not a debt item.** Sixth provenance label, after `AIDER-PORTABILITY-2026-08-17`,
`MAF-REFERENCE-2026-08-17`, `CREWAI-NODUS-2026-08-18`, `ADK-LENS-2026-08-18` and
`LANGGRAPH-NODUS-2026-08-18`.

Source: `C:\codev\DBOS research\dbos-transact-py` @ `e0b742c` (2026-08-14, **MIT**, 56 non-test
`.py` / 31 650 LOC), audited 2026-08-19 against `v2.4.0`. Write-up:
`DBOS_ON_AINDY_RUNTIME_AUDIT.md` in that folder. Both sides source-verified; nothing executed.

**★ This is the only comparand in eighteen that solves OUR problem with OUR substrate choice** —
durable execution in one Postgres, no separate orchestrator. Temporal answered *"shard it"*; DBOS
answered *"don't"*, which is what we answered.

**It opened no new prefix, and that is the result.** Every finding landed on an entry that already
existed, as a **worked answer** rather than a new gap: `RECOVERY-GRANULARITY-1` (per-step
`operation_outputs` + replay-not-re-execute, and a monotonic ordinal as a cheaper identity than a
vector clock), `EVENT-OUTBOX-1` (**a simpler fix than the one filed** — no outbox, no relay, no
window, because the event store *is* the workflow store), `EMBEDDED-FLOOR-1` (SQLite and Postgres
behind one shared implementation), `FLOW-GRAPH-SIGNATURE-1` (version string vs topology hash — the
entry's own open trade, with a shipped instance on one side), `ECOGAP-5` (`automatic_backfill`),
`IDEM-11` (start-boundary `deduplication_id`), `RETRY-CLASSIFY-1` (named error types).

**★ Three method lessons that generalise past this target:**

1. **A peer on your own axis produces answers, not gaps.** Seventeen comparands solved adjacent
   problems and produced *entries*; the one that solved the same problem produced *implementations
   of entries already filed*. Given that `COMPARATIVE_RESEARCH_INDEX.md` §5c concludes the
   bottleneck is **completion rather than discovery**, this is the higher-yield kind of audit — and
   the signal that discovery has saturated.
2. **It corrected one of my own filings downward.** `EVENT-OUTBOX-1` proposed an outbox table plus a
   relay; the peer showed the window closes with a connection argument. **Reading a peer is cheaper
   than reasoning alone.**
3. **And it challenged a constraint reasoned to alone.** `PROGRESS-CHANNEL-1` specifies progress as
   best-effort *"by construction"*; DBOS ships a durable `streams` table with offsets, deliberately.
   **One reasoned position against one shipped one is not a settled question**, and that entry now
   says so rather than asserting the constraint.

**One honest downgrade, recorded so it is not overstated:** `owner_xid` is a **start-time ownership
guard**, not a per-write fence. `LEASE-FENCE-1` keeps Temporal and Pi as its witnesses; DBOS is a
third and lesser instance.

---

## LITELLM-DOMAIN-2026-08-19 — provenance note

**Not a debt item.** Seventh provenance label.

Source: `C:\codev\LiteLLM research\litellm` @ `c696fdf` (2026-08-19, **MIT**), audited 2026-08-19
against `v2.4.0`. **Scope deliberately narrow** — only `proxy/spend_tracking/`, `proxy/hooks/`,
`proxy/db/` and `proxy/auth/`. Write-up: `LITELLM_ON_AINDY_RUNTIME_AUDIT.md`.

**Not a substrate comparison.** LiteLLM is an LLM gateway, not a durable execution engine, and
nothing about its orchestration bears on us. **It was audited for one subsystem** — the one where
`COST-GOVERNOR-1` records that a comparand has something and we have nothing.

**Result: no new prefix; two entries got worked answers.**

- **`COST-GOVERNOR-1`** — all four of its stated design questions answered in shipped code, with
  **reserve → call → reconcile** as the mechanism and the hardest question (*estimated or actual?*)
  answered **both**.
- **`INITIATOR-IDENTITY-1`** — the accounting half of the acting-subject problem, shipped, as an
  **attributed, constrain-only** `end_user` budget scope.

**★ Second consecutive audit to produce answers rather than gaps** (after `DBOS-PEER-2026-08-19`).
`COMPARATIVE_RESEARCH_INDEX.md` §5c predicted this: once the bottleneck is **completion rather than
discovery**, a peer on a specific axis returns implementations of filed entries, not new ones.
**That is the signal that comparative research has done its job** — and the reason the next unit of
work is a build, not another audit.
