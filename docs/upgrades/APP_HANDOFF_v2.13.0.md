---
title: "App Handoff — Runtime v2.13.0"
api_version: "1.0"
last_verified: "2026-09-13"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.13.0

Floor/pin move `>=2.12.0,<3.0` → bump the `constraints.txt` pin `==2.12.0` → `==2.13.0`.

**Required of you: nothing.** No schema step, no required code change. The one consumer-visible
envelope change in this release (§2) cannot fire for any flow you have today, and this was
checked against your source rather than assumed. This is a plain pin bump + rebuild; the rest is
what you *gain*, what you can *opt into*, and what to verify.

> ## ★ Confirm what you are actually running — in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.13.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```
>
> `importlib.metadata`, `pip show` and `import AINDY._version` are cwd-sensitive (the 2.11.0
> handoff's correction records both sides of that trap). Your dev venv was re-checked on
> 2026-09-13: it is on **2.12.0**, real site-packages, not the shadow — the 2.6.0 problem is gone.

---

## 0. Which path are you on?

| If the container reports… | Read |
|---|---|
| **2.9.0 through 2.12.0** | Pin bump + rebuild. No schema step. Go. |
| **2.8.0** | Same, plus the envelope change from `RUNTIME_2_9_0_UPGRADE.md` §1 you skipped. |
| **2.7.0 or earlier** | `RUNTIME_2_11_0_UPGRADE.md` §1 first — you owe 2.8.0's `flow_runs.graph_signature` step (`bootstrap-schema --reconcile`, Alembic `0018`), then this. |

2.13.0 adds no schema: Alembic head `0018`, `SCHEMA_CONTRACT_VERSION` unchanged,
`git diff v2.12.0..v2.13.0 -- AINDY/db/models/ AINDY/memory/memory_persistence.py` empty.

---

## 1. What you gain without doing anything

- **Your planner's spend is now attributed and accrued** (#635). Every LLM call through the seam
  lands on the tenant's rolling window and — for execution-time calls — on the run; an agent
  run's total appears on its `SCORE_COMPUTED` record as `dimensions.llm_tokens`. Your Claude
  planner (#321) runs inside `generate_plan`, which declares the tenant, so **the app half of
  this was nothing**: read `aindy_llm_calls_total{attributed}` after the rebuild and you should
  see `unit` (planning inside a request) and `run` (execution), not `none`.
- **Per-request accounting is coherent** (#632). A syscall dispatched inside a request now carries
  the request's `trace_id` (equal to `X-Trace-ID`) and `execution_unit_id` on its envelope, and
  memory-node provenance names the real `ExecutionUnit`. Previously each dispatch minted its own
  UUID and leaked one usage snapshot per call for the life of the process — the MCP server (per
  tool call) and every route were affected.
- **A scheduler-thread trace bug is fixed** (#633): the first flow started on a scheduler thread
  pinned that thread's `trace_id` for every later flow it ran, so unrelated runs shared a trace
  id in your observability. If you ever saw "different runs, same trace" — that was it.
- **Four new metrics:** `aindy_llm_calls_total{provider,attributed}`,
  `aindy_llm_budget_outcomes_total{scope,outcome}`, `aindy_syscall_unowned_unit_total{syscall}`,
  `aindy_resource_usage_evicted_total`.

---

## 2. The one consumer-visible change — checked against your source, does not fire for you

**`sys.v1.flow.run` can now return `status: "partial"`** (#640). It happens only when a flow
declares a fan-out group with `join="any"` or `join="quorum"` and a branch fails. **You declare
no fan-out group** (`grep -rn FanOutEdgeGroup apps/` → 0 files), so no flow of yours can produce
it today. The 2.9.0 handoff's rule stands and is now load-bearing rather than latent: branch on
`!= "success"`, never `== "error"`. Your two remaining `== "error"` sites
(`rippletrace/services/content_ingest.py:743` — a poll-outcome dict;
`social/routes/social_router.py:196` — a canonical HTTP response) are **not** syscall envelopes
and are fine.

If you do adopt fan-out (§3 of `FLOW_PARALLEL_DESIGN.md`): `all` is the default and fails whole,
exactly as before; a lenient join names each failed branch in `outcome.units` and on the run's
state under `_superstep_partials`.

---

## 3. What you can opt into — all default OFF

| Env var | What it does | Before you set it |
|---|---|---|
| `AINDY_QUOTA_MAX_TENANT_TOKENS` | The LLM token governor's per-tenant window (24 h rolling). A call that would exceed it is refused with `RESOURCE_LIMIT_EXCEEDED` **before** the provider is called; `POST /apps/agent/run` answers **429** with the reason (verified live against your image, #638). This is the one that catches a runaway planner — planning runs before the `AgentRun` row exists, so the per-run ceiling cannot. | **Size it against `max_tokens`, not typical actuals.** Your planner passes `max_tokens=4096`, so each call reserves ~5.2k until it reconciles to the actual (~2.3k for a plan). A window of 8,000 admits two plans then refuses; a window under ~5,300 admits nothing. |
| `AINDY_QUOTA_MAX_TOKENS` | Per-execution token ceiling (an agent run's execution span, or a bound request). | Same sizing rule. Does not cover planning. |
| `AINDY_RUN_SCOPED_QUOTA` | Makes the run the quota subject for a guest's `sys()` calls and an agent run's execution span — the 100-syscall cap (`AINDY_QUOTA_MAX_SYSCALLS`) applies to a whole script / whole run for the first time (#639). | Read `aindy_syscall_unowned_unit_total` on your deployment first: it names exactly the callers this moves, with counts. Raise `AINDY_QUOTA_MAX_SYSCALLS` if a legitimate script needs it. |

Alarm on `aindy_llm_budget_outcomes_total{outcome="refused"}` once a cap is set; a rising
`{outcome="degraded"}` means the budget store was unreachable and the call was admitted (dev/test
fail-open — prod refuses).

---

## 4. Verification after the rebuild

```bash
# 1. You are on 2.13.0 — IN THE CONTAINER, path printed
docker exec <api> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
#    expect: 2.13.0  ['/usr/local/lib/python3.11/site-packages/AINDY']

# 2. No schema drift (exit 0)
docker exec <api> aindy-runtime bootstrap-schema   # exit 0; 3 = additive reconcile owed (not this release), 4 = stop

# 3. The new metric families are registered (note the trailing slash — /metrics 307s to /metrics/)
curl -sL http://localhost:8000/metrics/ | grep -E "^# HELP aindy_(llm_calls|llm_budget_outcomes|syscall_unowned_unit|resource_usage_evicted)_total"
#    expect four HELP lines. No samples until a call happens — that is a correct reading of an
#    unfired labelled counter, not an absent meter.

# 4. One planner call attributes (create any agent run), then:
curl -sL http://localhost:8000/metrics/ | grep "^aindy_llm_calls_total"
#    expect attributed="unit" (planning inside the request) — NOT "none"

# 5. Nothing refuses until you set a cap
curl -sL http://localhost:8000/metrics/ | grep 'aindy_llm_budget_outcomes_total.*refused'
#    expect: no sample
```

---

## 5. Version-pin hygiene

Bump `constraints.txt` `aindy-runtime==2.12.0` → `==2.13.0` and the `pyproject.toml` floor to
`>=2.13.0,<3.0` if you want §3's knobs guaranteed present. Your dev venv is now on real
site-packages (2.12.0 as of 2026-09-13); `pip install -c constraints.txt` brings it to 2.13.0 so
your `pytest` imports what your container runs — the `DEBT-COMPAT-1` shape that bit both repos
on 2026-09-11 is closed on your side by that habit, not by any code.
