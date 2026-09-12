---
title: "App Handoff — Runtime v2.12.0"
api_version: "1.0"
last_verified: "2026-09-12"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.12.0

Floor/pin move `>=2.11.0,<3.0` → bump the `constraints.txt` pin `==2.11.0` → `==2.12.0`.

**Required of you: nothing.** No schema step, no required code change, and the two
behaviour-adjacent items in this release were checked against your source and **do not touch a
path you use** (§3). This is a plain pin bump + rebuild. The rest of this doc is what you *gain*
and what to verify.

> ## ★ First, confirm what you are actually running — your own upgrade docs verified the wrong runtime twice.
>
> The 2.9.0 and 2.11.0 adoption checks in this repo ran their suites against **2.6.0** — your dev
> venv (`aindy_runtime-2.6.0.dist-info`, non-editable) is what `pytest` imports, while your
> *container* is what resolves the range. `importlib.metadata`, `pip show`, and
> `import AINDY._version` are all **cwd-sensitive** (from `C:\dev\aindy-runtime` the sibling
> checkout shadows site-packages and any of them reports the checkout's version, not yours). The
> only instrument that says where it answered from:
>
> ```bash
> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> ```
>
> Run it **in the container** (the authoritative environment) after the rebuild — the path must be
> the installed `site-packages/AINDY`, the version `2.12.0`.

---

## 0. Which path are you on?

| If the container reports… | Read |
|---|---|
| **2.9.0, 2.10.0, or 2.11.0** | This whole doc is a pin bump + rebuild. No schema step. Go. |
| **2.8.0** | Same — plus the envelope change from `RUNTIME_2_9_0_UPGRADE.md` §1 you skipped. No schema step. |
| **2.7.0 or earlier** | **`RUNTIME_2_11_0_UPGRADE.md` §1 first — you owe 2.8.0's `flow_runs.graph_signature` schema step** (`bootstrap-schema --reconcile`, Alembic `0018`), then this. 2.12.0 itself adds no schema. |

2.12.0 changes no schema of its own: the Alembic head is unchanged at `0018`,
`SCHEMA_CONTRACT_VERSION` did not move, and `git diff v2.11.0..v2.12.0 -- AINDY/db/models/
AINDY/memory/memory_persistence.py` is empty. So `bootstrap-schema --reconcile` is **not** needed
*for this release* — only if you are crossing the 2.8.0 step above.

---

## 1. What you gain — fixes to things you filed or render

- **`FR-26` — the double trace id is fixed (this was your `TRACE-ID-DUAL-1`).** An enveloped
  `/apps/*` response's body `trace_id` now **equals** its `X-Trace-ID` header, instead of a
  separate uuid that resolved to a different event graph. If you added any client-side workaround
  that preferred `X-Trace-ID` over the body, you can drop it — they agree now. (We deliberately did
  **not** start honouring a *client-sent* `X-Trace-ID`; that's a trust-boundary decision.)
- **`FR-23` — `/observability/system` stops lying.** It reported `syscall_count: 0` and
  `tool_count: 0` on a full boot while ~90 and 16 were live, because it counted registries the
  dispatcher never reads. It now counts `SYSCALL_REGISTRY` and `TOOL_REGISTRY` and adds
  `run_tool_provider_run_types`. **Your operator dashboard will show real numbers** — expect the
  count to jump from 0 to the live totals, which is the fix, not a regression.
- **`FR-25 a+c` — failures you could not see before now log.** A dispatcher error now logs once at
  `WARNING` with the message it was already returning (e.g. *"Permission denied: requires capability
  X"*), and a plugin-load failure in the Nodus worker is a `WARNING` that names the manifest instead
  of a swallowed DEBUG. **Expect new WARNING lines only where something was already failing silently**
  — a caller lacking a capability it requests every call, or a worker that can't import a package.
  These are the signals you asked for, not new failures.
- **`FR-25 b` — the runtime-owned routes you flagged now answer 422, not 500**, on a malformed id
  (`coordination`, `keys`, `admin/users/.../promote`). The six were measured by probing every served
  route on both SQLite and Postgres; they were the only 500s.

---

## 2. The opt-in feature — default off, nothing changes until you set it

- **`FR-27` — strict at-most-once under contention** (`AINDY_SYSCALL_IDEMPOTENCY_STRICT`, default
  off, PostgreSQL only). The `EXACTLY_ONCE` gate degraded every concurrent duplicate (handler ran up
  to N times under N-way contention); with the flag on, a duplicate **blocks** on a Postgres advisory
  lock until the winner finishes and then replays — handler runs exactly once.
  - **If you turn it on:** a blocked duplicate holds **one pooled DB connection while it waits**
    (default 300s, `AINDY_SYSCALL_IDEMPOTENCY_STRICT_WAIT_SECONDS`). Size `DB_POOL_SIZE` /
    `DB_MAX_OVERFLOW` for your expected duplicate fan-in before enabling.
  - **Not a guarantee:** exactly-once across a *winner process crash* (a `pending` row whose lock
    dropped on disconnect still degrades until the stale threshold). Stated, not built.
  - New metric label `aindy_effect_gate_outcomes_total{outcome="degraded_lock_timeout"}` — non-zero
    means a handler is slower than the wait; `degraded` now means "lock not attempted" (non-PG or
    flag off).

---

## 3. Two changes that sound like they affect you but do not — checked against your source

- **`FR-23` deprecates two ABI registration functions, and neither is one you call.**
  `platform_layer.register_syscall` and `register_agent_tool` now emit a `DeprecationWarning`. You
  register through **`AINDY.agents.tool_registry.register_tool`** (e.g.
  `apps/agent/agents/tools.py`) and the **kernel** `syscall_registry.register_syscall` — the live
  paths, which are not deprecated. Verified: `grep` for the deprecated names across `apps/` is empty.
  **You will see no deprecation warnings.** (If you later add a call to either, the warning names the
  replacement.)
- **`FR-28` tightens message acknowledgement, and you don't call that route.**
  `POST /coordination/messages/{id}/acknowledge` now 404s an unknown id and 403s another agent's
  message (it used to 200 anything, and one agent could suppress another's inbox). Verified: nothing
  in `apps/` or `client/` calls it. **No caller of yours changes.** If you add one: acknowledge only
  a message addressed to the acting agent; a bad id is now a 422 (the param is a typed UUID).

---

## 4. Verification after the rebuild

```bash
# 1. You are actually on 2.12.0 — IN THE CONTAINER, and print the path (see the box above)
python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
#    expect: 2.12.0  ['/usr/local/lib/python3.11/site-packages/AINDY']

# 2. No schema drift (exit 0; a non-zero exit here means you are crossing the 2.8.0 step, §0)
aindy-runtime bootstrap-schema            # 0 = current

# 3. Healthy, registry intact — and FR-23's fix visible
curl -s localhost:8000/health/deep | jq '.checks.syscall_registry'
#    expect {"status":"ok","count": >= 22, ...}
curl -s localhost:8000/observability/system | jq '.data.registry // .registry | {syscall_count, tool_count}'
#    expect NON-ZERO counts (the FR-23 fix; 0 would mean the app plugin stack did not load)

# 4. The strict-idempotency flag is off unless you set it
python -c "import os; print('strict:', os.getenv('AINDY_SYSCALL_IDEMPOTENCY_STRICT','<unset=off>'))"
```

**What this establishes, and what it does not.** It confirms you are on 2.12.0 with a current
schema and a loaded plugin stack. It does **not** exercise strict idempotency (off) or the
deprecation seams (you don't call them) — both correct states for a release whose required action
is a pin bump.

---

## 5. Version-pin hygiene (your `DEBT-COMPAT-1` instance)

Bump the `constraints.txt` pin to `==2.12.0` in the same change as the rebuild, and update the
`validated on` note and any `Dockerfile`/`RUNTIME_DEPENDENCY.md` version strings — three of those
carried a stale `2.4.1` until the 2.11.0 adoption. The runtime publishes
`recommended_runtime_requirement` on `/api/version`; a one-line comparison at a call site that
already fetches it would make a future drift visible (warn, never refuse). The cheaper habit: a
test that asserts `AINDY._version.__version__` satisfies your declared range, so the interpreter —
not just two strings in two files — is what gets checked.
