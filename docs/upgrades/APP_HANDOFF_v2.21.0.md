---
title: "App Handoff — Runtime v2.21.0"
api_version: "1.0"
last_verified: "2026-09-18"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.21.0

Pin bump `constraints.txt` `==2.20.0` → `==2.21.0` (your contract test moves the floor with it).

**Required of you: nothing but the pin and a rebuild.** No schema step this release. There is
**one behaviour change to read before you rebuild** (§1 — a route handler that raises is now
rolled back), one removal that was dated in the 2.20.0 handoff (§2 — `user.id` on syscall
spans), one guest-surface addition you get for free (§3), and three hardenings that change
nothing unless you turned a flag on (§4). Two asks carried over, still yours (§6).

> ## ★ Confirm what you are actually running — in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.21.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Which path are you on?

| | Docker compose (your `RUNTIME_DEPENDENCY.md` path) | Anything else |
|---|---|---|
| Schema | **nothing** — contract stays `2026-09-16`, Alembic head stays `0019` | nothing; `bootstrap-schema` exits 0, no `--reconcile` |
| Code | pin bump + rebuild | pin bump + restart |

---

## 1. ★ A route handler that raises is now rolled back (`EVENT-OUTBOX-1`, #721)

**What changed.** When a handler running inside `ExecutionPipeline` raises — an `HTTPException`
included — the pipeline now rolls the request session back **before** recording
`execution.failed`. Before 2.21.0 the failure event's own `db.commit()` on the request session
landed the handler's pending, uncommitted writes as a side effect. So a route that wrote, did
not commit, and then raised (say, `db.add(row)` then `raise HTTPException(409)`) *kept* the row.
It no longer does. A handler that **returned** is never rolled back, whatever the post-handler
machinery does. The envelope records it as side effect `handler.rollback`.

**Why.** System events queued inside a request now ride the handler's own transaction (they used
to be flushed after the handler, on separate commits, under a swallowing `try` — a crash between
the handler's commit and that flush kept the work and lost the record). Riding the transaction
means rolling back with it; the pipeline had never actually rolled back on a raise, so now it does.

**What to check on your side, before the rebuild:**

- Any route under the pipeline that relies on writes surviving a raise. The honest shape is
  `db.commit()` before `raise`. We could not find one in the runtime's own routes; your
  `apps/*` routes are yours to grep — look for `db.add` / `db.execute` followed by `raise` with
  no `commit` between.
- **Your test suite, if it shares one session between app and test** (the `mock_db` /
  `db_session` shape): the app's rollback under a shared outer transaction rolls back the OUTER
  transaction and erases the test's own fixture rows. A test that drives a route to a 4xx/5xx and
  then reads fixture rows through the shared session will break with *"Could not refresh
  instance"* or an empty read. The fix is to run that module on a private engine — the runtime
  ships the recipe as `tests/fixtures/db.py::build_private_engine` (file-backed SQLite,
  `NullPool`, no shared outer transaction). One runtime module needed it (`test_auth_password_change`).

**What does not change:** non-request paths (scheduler jobs, workers, resume callbacks) — they
already wrote on the caller's session and committed. `emit_system_event` called directly is
unchanged.

---

## 2. `user.id` is gone from `syscall.*` OTel spans (#723; dated in the 2.20.0 handoff §3)

As announced: 2.20.0 emitted `enduser.id` beside `user.id` for one release; 2.21.0 emits
`enduser.id` alone, with the same value. Move any dashboard, alert or trace query filtering
syscall spans on `user.id` before you rebuild. `trace.id` stays.

---

## 3. Guest surface — `call_tool(name, args, step_index)` (`RECOVERY-GRANULARITY-1`, #722)

`call_tool` now accepts an optional third argument, the plan step index. **Compiled agent plans
pass it — you do nothing.** With it, the runtime records each step's `agent_steps` row *as the
step completes* (own session, committed, before the segment's script returns), and a **crash
continuation replays recorded successes** instead of re-running the segment from step one — no
LLM call re-issued, no usage, nothing at the effect gate.

**What you may see:** on a continued run, `__step_N_result` in the output state and
`result.steps` on the run carry `replayed: true` for a replayed step. **The key is present only
when true** — a normal run's shape is unchanged. Hand-written `.nd` scripts calling
`call_tool(name, args)` are unchanged and unrecorded. A retried step overwrites its own row (one
row per step, never one per attempt); `steps_completed` keeps FR-34's meaning.

---

## 4. Three hardenings — inert unless you opted in

| Change | When it applies to you | What it does |
|---|---|---|
| **`EGRESS-INPROC-1`** (#718) | only with `AINDY_EGRESS_ENFORCEMENT=1` (default off) | The socket-level egress guard now reaches a tool that declared `isolation=` — it never did; the isolated branch returned before the guard was entered, so the one tool moved out of process for being distrusted ran with no egress enforcement. Also: a tool whose `env_spec` declares `authority.network="none"` is now deny-all on both branches. The tool envelope carries `egress: {mode, mechanism}` when enforcement is on. |
| **`AUTHORITY-LIFETIME-1`** (#720) | always; you will only notice on a stale token | A capability token presented for a run in a terminal status (`completed`, `failed`, `verify_failed`, `cancelled`) is refused at the tool seam and the dispatcher: `failure_class="permission"`, `run <id> is <status>; authority ended with the run`. A cancelled run keeps its existing envelope. `waiting` runs keep their authority. No new read on the hot path (it is the cancel read, widened). Counter `aindy_authority_lifetime_refusals_total{status, surface}`. |
| **`AUDIT-CORRELATION-1`** (#719) | always; additive | `syscall.executed` payload gains `capability`, `guarantee`, `action_id` (null unless the idempotency gate engaged); `capability.allowed` gains `action_id`. The effect ↔ dispatch join is `IDEMPOTENCY_CONTRACT.md` §"Reconstruction join" — a convention on the unique `action_id`, no FK, time-bounded on both sides. |

Two entries also closed by decision with no code change: `HTTP-SCOPE-GAP-1` (DEC-046: scope
answers the verb, the row filter answers ownership; no `:any` scope) and `CLI-EXEC-SURFACE-1`
(DEC-047: the operator half stays HTTP-only).

---

## 5. Verification after the rebuild

```bash
# 1. the version, from inside the container, with the path
docker exec <api> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"

# 2. no schema step was needed
docker exec <api> aindy-runtime bootstrap-schema; echo "exit=$?"    # expect 0

# 3. a 4xx route leaves no half-written rows behind (pick any route of yours that raises after a write)
#    and its envelope carries the rollback side effect
curl -s ... | jq '.metadata.side_effects["handler.rollback"]'       # {"status":"ok",...} on a raise

# 4. syscall spans carry enduser.id and no user.id (your OTel backend)

# 5. a continued agent run shows replayed steps (only if you exercise crash continuation)
#    SELECT step_index, status, executed_at FROM agent_steps WHERE run_id = '<id>' ORDER BY step_index;
```

---

## 6. Two asks — carried over, both still yours

1. **Observe the first `leadgen.act` denial** (`AUTHORITY-NEGOTIATION-1` phase 3). Your
   `on_denial="wait"` declaration is half the evidence; the first observed denial is the other
   half. We do not flip on the declaration alone.
2. **Declare `args_schema`** on tools you own (FR-33 shipped in 2.20.0; `AINDY_TOOL_ARGS_VALIDATION`
   defaults to `warn`).

And two new ones, both small:

3. **Grep your pipeline routes for a write followed by a raise with no commit between** (§1)
   before the rebuild, and tell us if you find any — if the pattern is common on your side we
   would rather know before it bites than after.
4. **Declare `nltk` and `textstat` in your own `pyproject.toml`** (`PACK-DEBT-6`).
   `apps/search/services/seo_services.py` imports both; your manifest declares neither. They
   arrive today only because the runtime pins them — and nothing in the runtime uses them. Once
   you declare them (the versions the runtime pins, `nltk==3.10.3` / `textstat==0.7.13`, are fine
   as a floor), the runtime will announce a dated removal of both pins in the following release
   notes and drop them the release after. Nothing changes for you at any point; the dependency
   just moves to where the import is. (Context: the two nltk Dependabot alerts on the runtime —
   no fix released, not reachable on either side — were dismissed 2026-09-18 with this plan.)
