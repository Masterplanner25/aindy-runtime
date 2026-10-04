---
title: "App Handoff — Runtime v2.26.0"
api_version: "1.0"
last_verified: "2026-10-04"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.26.0

Pin bump `constraints.txt` `==2.25.0` → `==2.26.0` (your contract test moves the floor with it).
No ui-kit change: `@aindy/ui-kit` 2.1.1 stays current.

**What you need to do:** bump the pin and rebuild. There is no migration. **One thing to watch:** a
run's syscalls now count against one cap (§2). Read one number after your first real runs.

**What this release is.** Three defaults turn on (§1–§3). Ambiguous effects are now held instead of
re-run, and an operator can settle them (§4). A declared filesystem scope is enforced on both seams
(§5). A latency floor exists and runs on demand (§6).

**Checked against your source on 2026-10-04 (not your venv).** You call no `outbound_request`. You
declare no `FanOutEdgeGroup`, no filesystem scope, no `AINDY_MCP_SERVERS` and no tool guarantee. So
§3–§5 should change nothing you can see. §1 you already run. §2 is the one that can.

> ## ★ Confirm what you are actually running: in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.26.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Schema: none

No change under `AINDY/db/models/`. Alembic head stays **`0020`**, schema contract **`2026-09-20`**.
`bootstrap-schema` exits 0 on a current stack. The `Upgrade Path Guard` passes **trivially** on a
release like this, because there is no drift to find. Its negative control is the half that carries
meaning, and it was green.

## 1. Plan step references are ON by default (#786, FR-46, DEC-084)

`AINDY_PLAN_STEP_REFERENCES` now defaults on, as announced after your FR-48 adoption. Only
`0/false/no/off` disable it. Your run `19dcf508` was the evidence. Nothing to do.

## 2. ★ A run's syscalls count against ONE cap (#796, SYSMAX-4, DEC-095, DEC-096)

`AINDY_RUN_SCOPED_QUOTA` now defaults on. A guest's `sys()` calls, and every dispatch made during an
agent run, now count against that run's `AINDY_QUOTA_MAX_SYSCALLS` (default **100**). This applies on
both backends. Once a run is past the cap, its next call is refused with `RESOURCE_LIMIT_EXCEEDED`.

**Before**, each of those dispatches created a one-call unit of its own, so the cap never applied to
them.

**Also fixed:** on `nodus_vm`, a run's tool calls never reached the run's count at all. The worker
charged its own unit, in its own process memory, so a 3-step run read 1 syscall. Now it charges the
run.

**Wall time is NOT summed per run (DEC-096).** The 300 s `AINDY_QUOTA_CPU_MS` is a sum of syscall
durations. A real `memory.recall` step takes 30–40 s, so summing it per run would refuse long
LLM-heavy runs partway through. A run counts calls only.

**The ask.** After your first real agent runs on 2.26.0, check the run's logs for a refused step:

```bash
docker logs <api-container> 2>&1 | grep RESOURCE_LIMIT_EXCEEDED | tail
```

- If a legitimate run hits it, raise `AINDY_QUOTA_MAX_SYSCALLS`, and tell us the run's step count so
  the default can be revisited.
- `AINDY_RUN_SCOPED_QUOTA=0` restores per-dispatch accounting.
- A refused step is retried 3 times before the run fails (it is classified `transient`). That's
  harmless, but it makes the log line appear 3 times.

## 3. Fan-out branches run concurrently by default (#797, FLOW-PARALLEL-1, DEC-097)

`AINDY_FLOW_FAN_OUT` now defaults on. You declare no `FanOutEdgeGroup`, so nothing changes for you.

If you ever declare one:
- Each running branch holds its own DB connection, up to `AINDY_FLOW_FAN_OUT_MAX_WIDTH` (default 4)
  across the whole process.
- Results are the same with the flag on or off; only the timing changes.

The soak behind the flip also fixed a defect: the runner held a DB transaction across the whole
superstep. With `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` at 60 s, a fan-out longer than a minute would
have failed at the barrier.

**When you declare your first group, tell us.** The soak register's item 6 still wants a real one.

## 4. An effect whose outcome is unknown is held, and an operator settles it (#790–#793, EFFECT-OUTCOME-UNKNOWN-1)

- **Held, never re-run.** A replay of an `EXACTLY_ONCE` effect recorded `unknown` or `partial`
  returns the recorded outcome; the handler does not run again. A held `unknown` carries
  `failure_class: "unknown"`, which no retry loop retries.
- **`AT_MOST_ONCE`** is a new guarantee for effects that must never run twice. It refuses
  (`transient`, not dispatched) wherever `EXACTLY_ONCE` would degrade to at-least-once.
- **Emitters:**
  - An isolated tool with a declared guarantee whose worker is lost mid-call is now `unknown`.
  - An MCP server declared with a `guarantee` reports a post-send timeout as `unknown`.
  - `outbound_request` no longer blindly retries a POST/PATCH that may have been processed. It
    raises `EffectOutcomeUnknown` unless you pass `idempotent=True`. You don't call it, so this is
    noted only.
- **Settling one (admin):**
  - `GET /platform/effects/unknown` lists them.
  - `POST /platform/effects/{action_id}/resolve {"status": "success"|"failed", "note"}` settles one.
    `success` means later calls replay it; `failed` frees the slot for a retry.
  - Each resolution writes an `effect.reconciled` audit event.
  - The `aindy_effect_unknown_unresolved` gauge counts what's open.

## 5. A declared filesystem scope is enforced (#794, #795, FS-SCOPE-1, DEC-091..094)

- **Guest scripts:** declared roots are clamped to the floor, and `readonly` really is read-only.
- **Isolated tools:** a tool declaring `visibility.filesystem = scoped | readonly | none` has it
  enforced in its worker by a Python audit hook. The envelope reports
  `filesystem: {mode, mechanism}`.
- This is not a kernel boundary: C extensions and child processes bypass it.
- An in-process tool cannot be scoped. `register_tool` warns if one declares a scope without
  `isolation`.

You declare none of this, so nothing changes.

## 6. Smaller items

- **DEBT-COMPAT-1 (#786):** the boot-time range check names where it read your metadata, and warns
  when another copy shadows it. This is the egg-info trap you hit on 2.25.0.
- **PERF-BASELINE-1 (#798):** an on-demand latency floor exists (`AINDY_LATENCY_FLOOR=1`, plus a
  manual `Latency Floor` workflow). It is not a per-PR gate.

## Asks, in one place

1. Pin `==2.26.0`, rebuild, and confirm the version and path with the command at the top.
2. After your first real agent runs, grep for `RESOURCE_LIMIT_EXCEEDED` (§2) and report any hit,
   with the run's step count.
3. **Still owed from 2.25.0:** the `aindy_memory_recall_failures_total` readout, due on or after
   2026-10-09. Read `/metrics/` **before** rebuilding for this pin, because the counter resets on
   restart. That readout gates the `AINDY_MEMORY_RECALL_OWN_SESSION` flip (DEC-083).
