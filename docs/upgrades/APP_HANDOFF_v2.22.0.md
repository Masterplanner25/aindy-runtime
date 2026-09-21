---
title: "App Handoff — Runtime v2.22.0"
api_version: "1.0"
last_verified: "2026-09-20"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.22.0

Pin bump `constraints.txt` `==2.21.0` → `==2.22.0` (your contract test moves the floor with it).

**Required of you: the pin, a rebuild, and ★ one schema step** (§1 — `system_events.source`
widens; your entrypoint's exit-3 path handles it, as it did for 2.20.0). Four of your five open
FRs shipped in this release and each changes something on your side (§2). One deprecation with a
date, answering your ask 4 from last time (§3). One dependency edge you get for free (§4). Two
asks, one of them the same one for the third time — with a different answer now (§6).

> ## ★ Confirm what you are actually running — in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.22.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Which path are you on?

| | Docker compose (your `RUNTIME_DEPENDENCY.md` path) | Anything else |
|---|---|---|
| Schema | **★ Alembic `0020`** — contract `2026-09-16` → `2026-09-20`; `bootstrap-schema` exits **3** until `--reconcile` | `alembic upgrade head` or `bootstrap-schema --reconcile` once |
| Code | pin bump + rebuild | pin bump + restart |

---

## 1. ★ The schema step — Alembic `0020`, one column widened (FR-41, #730)

`system_events.source VARCHAR(32)` → **`VARCHAR(128)`**. `SCHEMA_CONTRACT_VERSION` moves
`2026-09-16` → **`2026-09-20`**; the runtime Alembic head moves `0019` → **`0020`**.

**Your `docker/entrypoint.sh` branches on `bootstrap-schema`'s exit code 3** (proved on 2.20.0).
On the rebuilt image:

- with `AINDY_BOOTSTRAP_RECONCILE` on: the entrypoint runs `--reconcile`, the column is widened,
  the api boots.
- with it off (your default): `bootstrap-schema` exits **3** and the entrypoint stops. Run
  `docker exec <api> aindy-runtime bootstrap-schema --reconcile` once, then `up`.

Widening only: no row is rewritten, nothing is backfilled. (`downgrade()` narrows with
`left(source, 32)`, which is lossy for rows written with a longer name after the upgrade — only
relevant if you ever roll back past 0020.)

**What it fixes — your FR-41, exactly as filed.** At 32, a route name of 33+ characters raised
`StringDataRightTruncation` inside the *required* `execution.started` emit on every request; the
pipeline caught it, logged a WARNING, and the request proceeded **unrecorded**. You renamed your
eight over-width routes (`ROUTE-NAME-EVENT-SOURCE-1`); you may keep the renames or revert them —
128 holds `masterplan.strategy.conclusion.propose` with room. **And the failure mode is gone
regardless of the width:** an over-width `source` is now a **contract violation checked at
pipeline entry**, before the handler — under `ENFORCE_EXECUTION_CONTRACT` (the default) the
request fails (500) and never runs unrecorded; with it off the violation is logged at ERROR once
per name. Never truncated. The width is documented on `execute_with_pipeline`'s `route_name`
and readable as `system_event_service.system_event_source_max_length()` — your test that reads
the width off the model keeps working.

---

## 2. Your FRs — four shipped, what each changes for you

### FR-39 — planner-context / tools-for-run providers receive `user_id` as a **string** (#729)

Both hook contexts crossed the extension boundary as a `uuid.UUID`, which the boundary redacts —
so your `build_planner_context` provider got `{"_redacted_type": "UUID"}` and no `db`, and returned
the bare base prompt on every plan since 2026-05-20. Now `user_id` arrives as the string, and
**`db` is absent by design** (documented on `register_planner_context_provider` /
`register_run_tool_provider`): your `AGENT-PLANNER-CONTEXT-BOUNDARY-1` — open your own session,
read the tenant from the string — can land. The boundary test now covers every tenant-bearing hook
context the runtime builds, so a third instance fails here, not in your log.

### FR-40 — the `warn` recipe holds on `nodus_vm` (#731; DEC-067)

`aindy_tool_args_validation_total{tool, outcome, mode}` now has samples on the **api's** `/metrics`
for steps validated in the pool worker (the tally rides the worker reply, like `llm_usage`), and
the `warn` WARNING is re-emitted in the api log with the errors and the call count. So: leave
`AINDY_TOOL_ARGS_VALIDATION=warn`, read `outcome="invalid"` on your `/metrics`, flip to `enforce`
when it reads zero — the recipe as written in the 2.20.0 handoff, now followable on your backend.
Cap: `AINDY_TOOL_ARGS_VALIDATION_LEDGER_MAX` (errors carried per tool, default 32; counts never
dropped).

### FR-38 — the authority gate reaches `nodus_vm` (#732 design, #734 build; DEC-068..070)

Your filing found the fifth denial site (`execute_tool`'s own chokepoint, the one `nodus_vm` hits)
and the design's census gained it. Under `AINDY_AUTHORITY_NEGOTIATION=true` on `nodus_vm`:

- a denied step negotiates one downgrade to a declared `degraded_variant` the token grants; else
- a tool declaring `on_denial="wait"` **parks the run** (`agent_runs.status="waiting"`,
  `wait_state.authority_gate {step_index, tool, denied_error, negotiation_outcome, tool_args,
  decisions: [skip, abort]}`), `AUTHORITY_NEGOTIATED` recorded, the `WAITING` event emitted;
- **the operator decides through the agent resume route, which now takes a body:**
  `POST /api/agent/runs/{id}/resume {"decision": "skip" | "abort", "note": "…"}`. `skip` records
  the step `skipped` and re-drives the segment (finished steps replay, the skip replays, the rest
  run); `abort` fails the run with your reason. A resume **without** a decision on a gate-parked
  run is **409** and the run stays parked; an unknown decision is **422**, recorded on the gate.
  An ordinary plan-declared wait resumes exactly as before, body or no body.

**Yours:** your live resume surface (the app-owned one that mirrors `resume_agent_run_runtime`)
must pass the body through — `resume_agent_run_runtime(db=, user_id=, run_id=, payload=)`. Until
it does, a gate-parked run on your stack can only be resumed through the runtime's reference
route. Your two small things: `COMPLETED.steps_completed` now counts successes only (`steps_total`
beside it) — the `1/1` is gone; the "`wait_state` not cleared after a resumed run completed" was
**not reproduced at HEAD** on the in-process path (the suite pins `None` at completion) — if you
see it again on a rehydrated (cross-process) resume, that is the path to name.

### FR-37 — the ui-kit reads `X-AINDY-Envelope` (ui-kit 2.1.0; runtime #735)

`request()` resolves bodies from the header; a bare `{data: …}` row is no longer mistaken for an
envelope once the backend has been seen stamping. **Yours:** bump `@aindy/ui-kit` to 2.1.0 in the
app's client and delete the per-route unwrap workarounds (`client/src/api/*.js`) — a cleanup with
no decision in it, exactly as your filing said.

---

## 3. ★ Deprecation with a removal date — `nltk` and `textstat` (PACK-DEBT-6, #733)

You declared both in your `pyproject.toml` (your #391 — thank you). The runtime's notice, as
promised: **both pins are removed from the runtime's dependencies in the release after this one,
and not before 2026-10-01.** Nothing changes for you at any point; you already install them
yourself. The four `pip-audit` acceptances that existed only for them go with the pins.

---

## 4. Dependency edge — `[mcp]` and nodus (#727)

`nodus-lang` 5.14.0 (the guest's first HTTP call no longer pays ~0.5 s for the CA bundle — per
process now, not per unit); `nodus-mcp` ≥ 0.1.4; and **the `[mcp]` extra no longer caps `mcp<2`**
— `pip install aindy-runtime[mcp]` resolves the newest 2.x. You do not install the extra; listed
so the resolver change is not a surprise if you ever do.

---

## 5. Verification after the rebuild

```bash
# 1. the version, from inside the container, with the path
docker exec <api> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"

# 2. ★ the schema step landed: head 0020, contract 2026-09-20, exit 0 AFTER reconcile
docker exec <api> aindy-runtime bootstrap-schema; echo "exit=$?"
#    expect: exit 0 and "stamped 0020" (exit 3 means §1's reconcile has not run yet)
#    and the column: SELECT character_maximum_length FROM information_schema.columns
#                    WHERE table_name='system_events' AND column_name='source';   -- 128

# 3. FR-41: your longest route name is recorded (no "[SystemEvent] Failed to emit" for it)
#    SELECT source, count(*) FROM system_events WHERE type='execution.started'
#      GROUP BY source ORDER BY length(source) DESC LIMIT 5;

# 4. FR-40: the validation samples are on the api's /metrics after one nodus_vm run
curl -s http://localhost:8000/metrics | grep aindy_tool_args_validation_total

# 5. FR-39: your planner-context provider sees a string tenant (your log at WARNING: no
#    "get_user_kpi_snapshot failed for {'_redacted_type': 'UUID'}")

# 6. FR-38: re-run your manufactured denial on nodus_vm (the leadgen.act ceiling token):
#    expect the run `waiting` with wait_state.authority_gate, then
#    POST /api/agent/runs/{id}/resume {"decision":"skip","note":"…"} → completed, step 0 `skipped`
```

---

## 6. Two asks

1. **Re-run your manufactured denial on `nodus_vm`** (verification step 6) and send the result —
   this is `AUTHORITY-NEGOTIATION-1` §9.6 step 5, the last thing owed before the phase-3 flip is a
   judgment on both backends. The evidence you filed for `agent_flow` is on record and was what
   drove the build; the handoff's earlier "observe the first denial" ask is answered by it — we
   asked it twice after you had recorded it, and §9 now credits it. This is a different ask: the
   backend that could not fire before.
2. **Pass the decision body through your resume surface** (§2, FR-38) and **bump `@aindy/ui-kit`
   to 2.1.0** (§2, FR-37). Both are the cleanups your own filings named as yours.
