---
title: "App Handoff — Runtime v2.20.0"
api_version: "1.0"
last_verified: "2026-09-17"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.20.0

Pin bump `constraints.txt` `==2.19.0` → `==2.20.0` (your contract test moves the floor with it).

**Required of you: one schema step, and it is the one your entrypoint already handles** (§1).
No required code change. This release ships **all five FRs you filed against 2.19.0** (§2), one
deprecation with a removal date (§3), three new settings at inert defaults (§4), and several
behaviour changes you will *see* in numbers you already read (§5). Two asks, both small (§7).

> ## ★ Confirm what you are actually running — in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.20.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Which path are you on?

| If the container reports… | Read |
|---|---|
| `2.19.0` | this whole document — §1 first |
| `2.18.0` or earlier | `APP_HANDOFF_v2.19.0.md` first (no schema step there), then this |

---

## 1. ★ The schema step — Alembic `0019`, one additive column

`background_task_leases.fence BIGINT NOT NULL DEFAULT 0` (`LEASE-FENCE-1`, #705).
`SCHEMA_CONTRACT_VERSION` moves `2026-09-10` → **`2026-09-16`**; the runtime Alembic head moves
`0018` → **`0019`**.

**Your `docker/entrypoint.sh` already branches on `bootstrap-schema`'s exit code 3** (FR-14, your
own `AINDY_BOOTSTRAP_RECONCILE` gate). So on the rebuilt image:

- with `AINDY_BOOTSTRAP_RECONCILE` on: the entrypoint runs `--reconcile`, the column is added, the
  api boots. Expect the boot log line for it.
- with it off (your default): `bootstrap-schema` exits **3** and the entrypoint stops with the
  message it already prints for that case. Run `docker exec <api> aindy-runtime bootstrap-schema
  --reconcile` once, then `up`. Nothing is dropped, nothing is backfilled — a lease row that
  predates the column reads `fence = 0` and moves to 1 on its next takeover.

Either way, **this is the release to prove your exit-3 path on**, which the 2.3.0 handoff asked
for and nothing since has exercised. Verification step 2 below reads the result.

What the column does: under a leadership split (two api replicas both believing they lead — a
GC pause past the 60 s lease TTL), the *old* leader's `recover_orphaned_approved_runs` and
`deferred_async_job_retry` now refuse to dispatch instead of double-dispatching. You run one
replica; the `single-instance` profile holds no lease and is unaffected. You will never see it
fire; it is there for the day you run two.

---

## 2. Your five FRs — all shipped, and what each one changes for you

| FR | Shipped | What you will see |
|---|---|---|
| **FR-36** (#708) | the completion-hook context's `user_id` is a `str` | your `AGENT-COMPLETION-HOOK-USERID-1` workaround (tenant from the re-fetched run) keeps working and is now redundant; `loop_enforced` should start appearing on completed runs |
| **FR-34** (#708) | `steps_completed` counts steps that **succeeded**, on both backends | ★ your filing named the `agent_flow` sites; your runs were on `nodus_vm`, which had four more. `Assistant.jsx:268` and `AgentConsole.jsx:134` ("N/M done") now read honest numbers — a failed run's N is **lower** than before. `score.computed.dimensions.steps_completed` likewise |
| **FR-32** (#708, **option 2**) | a plugin's `memory_execute_loop` **wins**; the runtime's is a default registered *last* on both api and worker boots | you may now register your own `memory_execute_loop` (ending at `memory_execution_run`) and delete `memory_execution_orchestrate` (`flow_definitions.py:404`) — your `test_memory_execute_no_recalc.py` pin becomes unnecessary. Until you do, nothing changes: the runtime default stands |
| **FR-33** (#709) | `register_tool(..., args_schema={...})`; the planner catalog renders `args={…}`; `execute_tool` checks before dispatch under `AINDY_TOOL_ARGS_VALIDATION` (**`warn`** by default) | §7 ask 1. Nothing changes until you declare a schema |
| **FR-35** (#712) | tool-step LLM usage on `nodus_vm` rides the worker reply and is recorded in the api process | `aindy_llm_tokens_total{provider="deepseek"}`, `aindy_llm_calls_total{attributed="run"}`, `aindy:rm:tenant:<user>:tokens` and `score.computed.dimensions.llm_tokens` read the spend for the first time (your table in the filing). **The governor's tenant window now moves on this backend** — if you set `AINDY_QUOTA_MAX_TENANT_TOKENS`, it will refuse guest calls it never saw before. ★ Found building it: the worker seam had been dropping `failure_class` from every tool result, so `RETRY-CLASSIFY-1`'s class never reached your compiled plans — fixed |

---

## 3. ★ Deprecation with a removal date — `user.id` on `syscall.*` OTel spans

`syscall.*` spans now carry **`enduser.id`** beside `user.id` (#706, DEC-036 — the GenAI
semantic-convention key). **`user.id` is removed in the release after this one.** If any
dashboard, sampler or trace query of yours filters on `user.id`, repoint it to `enduser.id`
during this release. (You export no traces today — recorded so the removal is not a surprise.)

The same PR adds three span kinds you did not have: `chat {model}` around every provider call,
`execute_tool {tool}`, `invoke_agent {agent_type}` — nested, with `gen_ai.*` attributes, no
prompt or completion content ever. Visible only with `OTEL_EXPORTER_OTLP_ENDPOINT` set.

---

## 4. New settings — all at inert defaults

| Setting | Default | What it does when set |
|---|---|---|
| `AINDY_SYSEVENT_RETENTION` | **unset** = no job | `report` runs a daily selection and logs per-type counts, deleting nothing; `prune` deletes. Classes per event type (`audit` never / `operational` 90 d / `keepalive` 7 d); **your own event types are unclassified = kept** until you declare them via `register_event_retention(type, class)`. Prunes LEAVES only — anything referenced by a memory node, an agent event, a child event or a causal edge is never eligible. Read one `report` before `prune`. `aindy_system_events_unclassified_rows` is the ask to classify (#704) |
| `AINDY_TOOL_ARGS_VALIDATION` | **`warn`** | `enforce` refuses a step whose args violate the tool's declared `args_schema` (as `failure_class: invalid`, never retried) — read `aindy_tool_args_validation_total{outcome="invalid"}` under `warn` first (#709) |
| `AINDY_NODUS_LLM_LEDGER_MAX` | 256 | per-call records the worker carries; the rest aggregated per (provider, model) (#712) |

---

## 5. Behaviour changes you will see without doing anything

- **A cancelled run's tool, a call missing its capability token, and a crashed enforcement
  check are attempted once, not three times** (#703). Every `execute_tool` refusal and every
  syscall **error** envelope now carries `failure_class` (`transient | cancelled | permission |
  not_found | invalid | fatal`) — additive; your `!= "success"` handling is unaffected.
- **A verify-failed agent run's execution unit now finalises as `failed`** (#713); it used to
  stay `executing` forever, and every completed `nodus_vm` run logged `[EU] invalid transition
  completed→completed` — the line your 2.19.0 doc called noise. **Units of your past
  verify-failed runs are still `executing` in `execution_units`** — not backfilled; harmless,
  and worth knowing when you count them.
- `steps_completed` lower on failed runs (§2, FR-34); LLM readings higher on `nodus_vm` (§2, FR-35).

---

## 6. Verification after the rebuild

```bash
# 1. You are on 2.20.0 — IN THE CONTAINER, path printed
docker exec <api> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
#    expect: 2.20.0  ['/usr/local/lib/python3.11/site-packages/AINDY']

# 2. ★ The schema step landed: head 0019, contract 2026-09-16, exit 0 AFTER reconcile
docker exec <api> aindy-runtime bootstrap-schema; echo "exit $?"
#    expect: exit 0 and "stamped 0019" (exit 3 means §1's reconcile has not run yet)
docker exec <api> python -c "from AINDY.db.schema_contract import SCHEMA_CONTRACT_VERSION as v; print(v)"
#    expect: 2026-09-16

# 3. ★ FR-35 live: run the arm.analyze objective from your 2.19.0 §6 again, then
curl -s .../metrics | grep 'aindy_llm_tokens_total{provider="deepseek"'
#    expect: a sample, roughly the 1965 you measured in the worker
curl -s .../metrics | grep 'aindy_llm_calls_total{.*attributed="run"'
#    expect: >= 1 — the reading your 2.13.0 note said only appeared on agent_flow
#    and the run's score.computed carries dimensions.llm_tokens > 0

# 4. FR-34: a run that fails on step 2 of 2 reads steps_completed 1, current_step 2
#    (the four arm.analyze runs from 2026-09-16, if re-run without file_path, are that shape)

# 5. FR-36: a completed run's log has no "user_id is required" WARNING; loop_enforced appears
docker logs <api> 2>&1 | grep -c "user_id is required"     # expect 0 on new runs

# 6. #713: no "invalid transition completed→completed" on a completed nodus_vm run
docker logs <api> 2>&1 | grep -c "invalid transition completed"   # expect 0 on new runs

# 7. Readiness unchanged
curl -s .../health/deep | jq '.checks.syscall_registry'    # count >= 23
```

---

## 7. Two asks — both small, both yours

1. **Declare `args_schema` on your tools and drop the `Args:` prose convention** (FR-33, your
   filing). One kwarg per `register_tool`, in the dispatcher's dialect (`required` +
   `properties[].type`); pass it through as the Claude planner's per-tool `input_schema`. Leave
   `AINDY_TOOL_ARGS_VALIDATION` at `warn`, watch `aindy_tool_args_validation_total{outcome="invalid"}`
   read zero on your stack, then flip to `enforce`. Your
   `test_agent_tool_descriptions_declare_args.py` can then pin the schema instead of the prose.
2. **Observe the first authority denial on `leadgen.act`** (`on_denial="wait"`, your #378). This
   is the second half of `AUTHORITY-NEGOTIATION-1` phase 3's evidence — the runtime's registry
   said "zero tools declare a gate" until 2026-09-17; yours is the first, and the flip waits on a
   denial being *seen*. Your 2.17.0 §3.3 names the recipe: `AINDY_AUTHORITY_NEGOTIATION=true` on
   a non-production profile, manufacture the token-expiry case (a run parked on approval past the
   24 h TTL, then resumed), and report what parked: `AgentRun.status = waiting`,
   `wait_state.authority_gate`, and the `skip | abort` resume. Record it in your 2.20.0 upgrade
   doc; we flip the default on that record.

Your other 2.17.0 ask — named predicates behind a drain — is unchanged and still the owner's call.
