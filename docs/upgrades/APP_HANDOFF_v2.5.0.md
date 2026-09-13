---
title: "App Handoff — Runtime v2.5.0"
api_version: "1.0"
last_verified: "2026-08-20"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.5.0

**Read §1 and §2 before upgrading.** Unlike `2.4.1`, this one is not a `pip install`. There is a
schema step you must run, and three execution defaults changed.

**No feature requests moved.** `APP-FR-*` is where `v2.4.0` left it — **FR-6 items 2+3** and
**FR-14's remaining half** are still open, nothing closed, nothing accepted. **No route began
enforcing a new scope**, so no caller loses access. **No dependency pins moved.**

---

## 1. ★ The schema step — do this or your container will crash-loop

`execution_units` gains three additive, nullable columns (`env_spec`, `env_applied`,
`env_evidence_class`). Alembic **`0017`**; schema contract **`2026-08-19`**.

Per `FR-14`, an additive runtime column makes a bare `bootstrap-schema` exit **3**:

```bash
aindy-runtime bootstrap-schema --reconcile      # existing database: run this
```

**Under `set -e` with `restart: unless-stopped`, a bare `bootstrap-schema` is a crash loop, not a
warning.** That is what took a live stack down on 2.1.0, and it is why this section exists rather
than a line in the changelog. Alternatively, branch on the exit code:

| Exit | Meaning | Safe to automate? |
|---|---|---|
| 0 | nothing to do | — |
| **3** | additive reconcile required | **yes — this release** |
| 4 | offline migration required | no |
| 5 | manual repair | no |

**A fresh database needs nothing** — `create_all` produces the columns.

**Nothing to backfill.** `NULL` means "declared nothing" and is defined to behave exactly as the
code did before these columns existed. Every existing row is `NULL`.

---

## 2. ★ Three execution defaults moved from off to on

Each is a behaviour change on upgrade. Each has an off switch that accepts `0`, `false`, `no`,
`off`.

### 2.1 `AINDY_CHILD_CONTEXT_CLAMP` — a nested context can no longer widen

`child_context()` now narrows the parent's capability grant and never widens it; a widening
request is dropped and logged at WARNING.

**How to check whether this affects you, before upgrading:** every widening has been logged since
2026-08-16. Grep your logs for:

```
child_context WIDENED authority
```

If that never appears, this changes nothing for you.

**It does affect one thing we know about, and it degrades gracefully.**
`apps/automation/syscalls/syscall_handlers.py`'s `_handle_agent_suggest_tools` widens to
`analytics.read` for an *optional* cached-suggestions lookup — inside `try/except`, with a full
KPI-based fallback beneath it. Clamped, it logs a warning and recomputes. The other 18 functions
that widen through `_dispatch_owner_syscall` **are never registered**, so the clamp cannot reach
them.

### 2.2 `AINDY_SYSCALL_IDEMPOTENCY` — 8 syscalls dedup within a run

Affected: `memory.write`, `memory.link`, `event.emit`, `flow.run`, `flow.execute_intent`,
`nodus.execute`, `job.submit`, `agent.undo`.

**The scope is the execution unit id**, so a retry *within one run* replays the cached result and
**two legitimate calls in different runs are untouched**. That scoping is what made this safe to
default on.

**★ It is not exactly-once under contention.** A call that loses the insert race against a live
pending row degrades to `AT_LEAST_ONCE` and logs a warning — strict at-most-once needs advisory
locking. Measured: 8 concurrent identical calls ran the handler twice.

**Watch `aindy_effect_gate_outcomes_total{outcome="degraded"}`.** If that is a meaningful fraction
of `reserved` in your deployment, the guarantee you have is weaker than the flag's name suggests,
and for a genuinely non-idempotent effect you may want `AINDY_SYSCALL_IDEMPOTENCY=0` until the
advisory-locking work lands.

### 2.3 `AINDY_NODUS_WARM_POOL` — warm workers instead of a fresh subprocess

Reuses a bounded pool (default 4) so plugin cold-start is paid once rather than per execution.
**Any warm-path failure falls back to a fresh subprocess**, so this cannot make execution worse
than the path it replaces.

If you see anything odd in Nodus execution after upgrading, `AINDY_NODUS_WARM_POOL=0` restores
the previous behaviour exactly and is a safe first diagnostic.

---

## 3. New: a tool can declare the isolation it needs

`register_tool(..., isolation=<assurance class>)` — `"insecure-dev"`,
`"container-grade-sandbox"`, `"strong-sandbox-tier"`.

**Entirely opt-in and nothing changes for your existing tools.** A tool that declares nothing
(all 20 of yours) behaves exactly as before.

If you *do* declare one:

- The tool is **refused** when the host cannot provide that class, fail-closed.
- The tool runs in a **worker subprocess**, and **`db` is `None` there** — a session cannot cross
  a process boundary. Safe for your tools as written: all 15 take `db` and **none uses it**;
  they dispatch through `invoke_tool_syscall`, which continues to work.
- **There is no fallback.** A worker that crashes, times out or cannot start means the tool does
  not run. Falling back would execute a tool that asked to be confined *unconfined*.
- **Check `aindy_tool_return_contract_violations_total` first.** A tool whose return does not
  marshal cannot cross the boundary, and that counter is exactly the list of tools that cannot be
  declared yet.

`AINDY_TOOL_ISOLATION=0` reverts to declare-and-refuse without the subprocess.

---

## 4. Upgrade path

```bash
pip install --upgrade "aindy-runtime==2.5.0"
aindy-runtime bootstrap-schema --reconcile      # §1 — required on an existing database
# then start the app as usual
```

`recommended_runtime_requirement` stays `>=2.0,<3.0`, so no consumer pin has to move.

**Docker:** the image's builder-stage pin is `2.5.0`. Your entrypoint must handle exit 3 from
`bootstrap-schema` (§1) or run `--reconcile` explicitly.

---

## 5. Verification behind this release

Stated because "it was green" means different things per check:

- All required checks green on the tagged commit.
- **`Upgrade Path Guard`'s main job is doing real work for the first time.** On `2.4.1` it passed
  trivially — no schema change, nothing to detect. This release contains the condition, so read
  the main job, not only the `negative-control`.
- **Sandbox escape gate: see `SANDBOX_ESCAPE_AUDIT.md`.** Read the entry rather than the number:
  that suite certifies the Tier-2 extension sandbox and has never covered the in-process tool
  seam or the Nodus guest.
- The idempotency gate was tested **under contention on real Postgres**, not only sequentially —
  which is where its degradation behaviour was found.

---

## 6. Known-open, so you are not surprised

- **`IDEM-12`** — `undo_run_effects` never consults `effect_reversals`, so a deliberate second
  `sys.v1.agent.undo` re-invokes every compensator. **The idempotency flip does not close this**;
  the gate is defence-in-depth, not the fix. Not live for you today because zero compensators are
  registered — it goes live with the first one.
- **`GUEST-CONFINE-1` residual — CLOSED this release.** The guest VM's `allowed_paths` no longer
  inherits the server's working directory; it is bounded to a per-execution scratch root, which
  also makes `NODUS_ALLOWED_PATHS` inert.
- **Undeclared tools still run in-process** with ambient authority. `TOOL-SEAM-ISOLATION-1`'s
  invariant holds for tools that declare a class; that is the deliberate remaining gap.
