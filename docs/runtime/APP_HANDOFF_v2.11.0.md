---
title: "App Handoff — Runtime v2.11.0"
api_version: "1.0"
last_verified: "2026-09-10"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.11.0

> ## ★★ Before anything else: you are almost certainly **not** on the version you think you are.
>
> **Measured in `aindy-apps-monolith` on 2026-09-10, four sources disagree, and the authoritative
> one is the lowest:**
>
> | Source | Says |
> |---|---|
> | `venv/Lib/site-packages/aindy_runtime-*.dist-info` | **2.6.0** |
> | the installed package's own `AINDY._version.__version__` | **2.6.0** |
> | `pyproject.toml` | `aindy-runtime>=2.9.0,<3.0` |
> | your `SESSION_HANDOFF.md` | *"`aindy-runtime 2.9.0`, floor and pin in lockstep"* |
> | your `Dockerfile` comment | `aindy-runtime>=2.4.1,<3.0` |
>
> **A declared floor is not evidence of what is installed.** `>=2.9.0` says pip may not go below
> 2.9.0 the next time it resolves; it says nothing about a venv resolved months ago and never
> re-resolved. Please check before you plan this upgrade:
>
> ```bash
> python -c "import importlib.metadata as m; print(m.version('aindy-runtime'))"
> ```
>
> **Why this is the first thing in the document rather than a footnote:** every other section
> changes meaning depending on the answer. If you are on 2.9.0, 2.11.0 is a plain `pip install`.
> **If you are on 2.6.0, it is not** — you cross a schema step, and you cross an envelope change
> you may have read about but never actually run. §1 and §2 are for you in that case.
>
> This is `DEBT-COMPAT-1` on our side, and the honest version of it is that our compatibility
> policy is *published, served on `/api/version`, and read by nothing* — so a drifted consumer is
> invisible to the mechanism that exists to notice.

---

## 0. Which path are you on?

| If `importlib.metadata.version('aindy-runtime')` says… | Read |
|---|---|
| **2.9.0 or 2.10.0** | §3 onward. Plain `pip install`, no schema step, no required code change. |
| **2.8.0** | §2 (the envelope change you skipped), then §3 onward. No schema step. |
| **2.7.0 or earlier — including 2.6.0** | **§1 first — you owe a schema step**, then §2, then §3 onward. |

★ **A `docker compose build` resolves `>=2.9.0,<3.0` to the newest release, which is now 2.11.0.**
If your running image was built against an older resolution, a rebuild moves you several releases
in one step — and the table above still applies, based on what your **database** has been served
by, not on what the image contains.

---

## 1. If you are below 2.8.0: the schema step, and what happens if you skip it

**This is not new in 2.11.0.** It is 2.8.0's step, and skipping a release does not skip its
schema change. We are repeating it because the measurement above says you are likely to meet it.

`flow_runs.graph_signature` (Alembic `0018`) is one additive, nullable column. **Nothing to
backfill.** But an additive runtime column makes a bare `bootstrap-schema` exit `3`, and under
`set -e` with `restart: unless-stopped` that is a container that fails, restarts, and fails
again — **a crash loop that looks like a broken image rather than a missing flag.**

```bash
aindy-runtime bootstrap-schema --reconcile
```

Or branch on the exit code, which is what an automated deploy should do:

| exit | meaning | safe to automate? |
|---|---|---|
| `0` | schema is current | — |
| **`3`** | **additive drift** | **yes — re-run with `--reconcile`** |
| `4` | offline migration required | no — human |
| `5` | manual repair required | no — human |

**`3` is the only one safe to automate.** Do not blanket-retry `--reconcile` on any non-zero exit.

**★ The Alembic head has been `0018` since 2.8.0.** 2.9.0, 2.10.0 and 2.11.0 have each added
nothing. So this is the *only* schema step between 2.6.0 and 2.11.0 — one step, not four.

---

## 2. If you are below 2.9.0: the envelope has values your code may not handle

2.9.0 widened the syscall response envelope. If you never actually ran 2.9.0, you have not
exercised this even if you read its handoff.

```jsonc
{
  "status":  "success" | "partial" | "unknown" | "error",
  "outcome": null,        // or {"units": [...], "detail": "..."} when partial/unknown
  "data":    { },
  ...                     // every other key unchanged
}
```

```python
# ✅ safe
if envelope["status"] != "success":
    handle_failure(envelope)

# ❌ not safe — a `partial` falls through to the success branch and you believe a
#    half-applied effect fully applied
if envelope["status"] == "error":
    handle_failure(envelope)
```

**Nothing in the runtime emits `partial` or `unknown` yet**, so this is latent rather than live —
which is exactly why it is worth fixing before it isn't. We had four of these ourselves.

---

## 3. What 2.10.0 and 2.11.0 actually require of you: nothing

**There is no required consumer code change in either release.** Both are additive or internal.
The rest of this document is things worth *knowing*, not things to do.

*(2.10.0 shipped without a handoff of its own. Its consumer-relevant content is folded in here.)*

**From 2.10.0:**

- **`pydantic-core` is no longer pinned by us.** If you were holding it to our number to make a
  resolution work, you can stop. Reproducibility never came from our line — it comes from
  `pydantic`'s own exact requirement.
- **`nodus-lang` 5.9.0 → 5.13.0 is a security release upstream, and this runtime was not
  exposed.** nodus `#843` confined `nodus serve`'s filesystem; this runtime has zero references to
  `nodus serve` or `RuntimeService` — it embeds `NodusRuntime` directly and passes `allowed_paths`
  explicitly. Stated plainly so nobody has to infer their exposure from a version number.

**From 2.11.0:** two features ship **default-off** and one fix removes log noise. See §5.

---

## 4. ★ One real deployment change: guest workflow state becomes durable

**This affects you only if you run guest Nodus workflows in containers.**

Nodus's workflow framework keeps its own run records. They previously wrote to the worker
process's working directory — `/home/aindy`, **which has no volume** — so they were **lost on
every container recreate**. Our `docker-compose.yml` now sets:

```yaml
environment:
  NODUS_RUN_STATE_ROOT: /var/lib/aindy/nodus-state
volumes:
  - nodus_state:/var/lib/aindy/nodus-state
```

**If you use your own compose or Kubernetes manifests, you get nothing automatically.** Not
setting it leaves you exactly where you were — ephemeral, which is the status quo and not a
regression. Setting it makes that state durable.

**Three things to get right if you do set it:**

- **Use `NODUS_RUN_STATE_ROOT`, not the legacy `NODUS_WORKFLOW_STORE_ROOT`.** The latter
  relocates only the record half and leaves `.nodus/graphs/` behind.
- **Point it at local storage.** The store runs SQLite in WAL mode, which is unsafe on NFS/CIFS.
- **It grows and nothing prunes it.** Budget for that, or leave it ephemeral deliberately.

**★ This does not widen what a guest can reach.** The guest's writable path is still an explicit
per-execution scratch directory; what moved is the framework's own bookkeeping, and the relocated
store sits inside nodus's Floor deny-list — verified, and recorded in `SANDBOX_ESCAPE_AUDIT.md`
Entry 025.

**Also in 2.11.0:** the runtime now declares `NODUS_WORKFLOW_STORE_BACKEND=sqlite` and
`NODUS_WORKFLOW_AUTOSWEEP=0` for its worker. **Neither overrides a value you have already set.**
This exists because nodus 6.0.0 flips the default store JSON → SQLite and the two cannot read each
other's records; declaring the choice means that flip changes nothing for us.

---

## 5. The two new features, both default-off

Neither changes any behaviour until you set its flag, and **neither is exercised by any shipped
flow or tool yet** — they are phase 1 of multi-phase work.

**`AINDY_AUTHORITY_NEGOTIATION`** — on a tool capability denial, offer exactly one downgrade to a
fallback the *tool* declared via `register_tool(..., degraded_variant=)`.

> ★ **It cannot grant authority, and that is structural rather than careful.** Negotiation only
> chooses *which tool to attempt*; `execute_tool` then runs its own capability check, so a
> negotiated tool passes exactly the gate an ordinary one passes. Downgrade-only, one attempt, no
> chains. **Nothing you own declares a `degraded_variant`, so turning this on today does nothing
> except move a counter.**

**`AINDY_FLOW_FAN_OUT`** — run a declared `FanOutEdgeGroup`'s branches concurrently.

> ★ **The flag gates concurrency, not semantics.** With it off, a declared group still runs — in
> declaration order, producing the same patches, merge, ordinals and history. Only timing differs,
> so turning it off can never change what a flow computes.
>
> ★ Width is bounded **process-wide** (`AINDY_FLOW_FAN_OUT_MAX_WIDTH`, default 4), not per run.
> Every branch holds a DB session, and that budget is shared with request handling.

---

## 6. Smaller things

- **Eight spurious `Unknown event type` warnings per affected agent run disappear.** They were
  real emissions against an incomplete declaration — `AGENT_STEP_COMPLETED`, `AGENT_STEP_FAILED`,
  `COLLABORATION_STARTED`, `FAILED` and four `DELEGATION_*` — not a behaviour change. If you
  alert on that log line, expect the rate to drop to zero.
- **★ `SCHEMA_CONTRACT_VERSION` moved (`2026-09-02.1` → `2026-09-10`) and the database did not.**
  The bump is our ORM content hash reacting to a *deleted constant*; the Alembic head is unchanged
  and no migration ships. **Verified: the constant appears only in report payloads and is never
  compared against anything stored, and `bootstrap-schema` does not read it.** If you assert on
  the value in `/api/version` or `runtime_schema_contract_metadata()`, update your expectation —
  otherwise ignore it.

---

## 7. Verification after upgrading

```bash
# 1. You are actually on 2.11.0 — check the installed package, not the declared floor
python -c "import importlib.metadata as m; print(m.version('aindy-runtime'))"

# 2. Schema is current (exit 0; exit 3 means §1 applies to you)
aindy-runtime bootstrap-schema; echo "exit=$?"

# 3. The service is healthy and the registry is intact
curl -s localhost:8000/health/deep | jq '.checks.syscall_registry'
#    expect {"status": "ok", "count": >= 22, ...}

# 4. Both new flags are off unless you set them
curl -s localhost:8000/api/version | jq '.data.version'
```

**If you are on 2.6.0 today, do step 2 before step 3** — that is the ordering that turns a crash
loop into a one-line fix.
