---
title: "App Handoff — Runtime v2.19.0"
api_version: "1.0"
last_verified: "2026-09-16"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.19.0

Pin bump `constraints.txt` `==2.18.0` → `==2.19.0` (your contract test moves the floor with it).

**Required of you: nothing.** No schema step, no required code change. This release closes the
runtime half of a finding **you made** (§1), removes one syscall you never called (§2), ships one
new operator setting that is off until set (§3), and records a decision that touches one hook of
yours (§4). Your two 2.17.0 asks are unchanged (§6).

> ## ★ Confirm what you are actually running — in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.19.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Which path are you on?

| If the container reports… | Read |
|---|---|
| **2.9.0 through 2.18.0** | Pin bump + rebuild. No schema step. Go. |
| **2.8.0** | Same, plus the envelope change from `RUNTIME_2_9_0_UPGRADE.md` §1 you skipped. |
| **2.7.0 or earlier** | `RUNTIME_2_11_0_UPGRADE.md` §1 first — you owe 2.8.0's `flow_runs.graph_signature` step (`bootstrap-schema --reconcile`, Alembic `0018`), then this. |

2.19.0 adds no schema: Alembic head `0018`, `SCHEMA_CONTRACT_VERSION` `2026-09-10` unchanged,
`git diff v2.18.0..v2.19.0 -- AINDY/db/models/ AINDY/memory/memory_persistence.py` empty.

---

## 1. Your `SYSCALL-SILENT-ERRORS-1` — the runtime half is fixed, and your inference was wrong in a way worth knowing

Your #365 commit body recorded *"the runtime's `system_state_service` passes `None` for the two
agent syscalls"* — correct, and not filed as an FR, so it was picked up from your commit. Fixed in
#692 (`SYSTEM-STATE-TENANT-1`). Two things your entry inferred that the source does not support:

- *"Inside a request the pipeline's tenant context covers an empty ctx."* **Nothing does.** No
  ContextVar fills `SyscallContext.user_id`; the dispatcher refused those two dispatches on
  **every** call, request or job. Your route probes "succeeded" because no runtime route calls
  `compute_current_state` — your three callers (`triggers.py:92`, `dependency_adapter.py:138`,
  `ranking.py:39`) are the only ones anywhere.
- *Passing a tenant is the fix.* It is not: both syscalls scope to ONE user by construction, and
  the snapshot is a whole-system reading. `AgentRun` is now read directly, the way `FlowRun`
  always was in the same function.

**What you will see:** `compute_current_state(db)["active_runs"]` now includes agent runs in
`approved | executing | pending_approval`, and `avg_execution_time` includes agent durations —
both had been silently zero since 2.0.0. `system_load` and `health_status` derive from them, so
on a deployment with agent traffic the snapshot your triggers and ranking read will show a
**higher load and a less calm health status. That is the true reading, not a regression.** If a
trigger threshold was tuned against the old (under-reported) numbers, expect it to fire more.

---

## 2. Removed — `sys.v1.agent.list_recent_durations` (#693, DEC-022)

**You never called it** (`apps/` grep: 0; your `identity_boot_service` calls `count_runs`, which
is unchanged). It was experimental, absent from the SDK and the cross-repo rename guard, and its
only caller in any repo was the snapshot in §1, which never received an answer from it. A dispatch
now returns the standard `Unknown syscall` error envelope. `SYSCALL_REGISTRY_MIN_COUNT` is 23;
readiness compares against it and moved with it.

---

## 3. New setting, off until set — `AINDY_NODUS_MAX_MEMORY_MB` (#697)

A per-execution memory ceiling for the Nodus guest VM, enforced by nodus-lang 5.13's
`max_memory_mb`. **Unset by default: nothing changes on adoption.** If you set it:

- the VM reads the worker's RSS when a script starts and fails the run with a `sandbox` error
  (`Memory limit exceeded: this run grew the process to N MB, past its N MB ceiling`) once the
  process has **grown** past the budget. It bounds growth over the run, polled — a script that
  grows 64 MB in 26 instructions can finish under an 8 MB ceiling because the poll never fires;
  only an OS-level cap (container memory limit) prevents a single large allocation. Set both.
- it applies to the guest path only. Your agent runs and flow nodes in the API process are still
  unbounded (`SYSMAX-3` stays open).
- a per-execution `env_spec` may narrow it, never widen it.
- on a host where the VM cannot read RSS, a declared ceiling **refuses** the run
  (`declared memory ceiling cannot be enforced on this host …`) rather than running it
  unbounded. Your Linux container meters fine; this is for the record.

Your `execute_nodus_task_payload` (`apps/memory/bootstrap.py`) is untouched.

---

## 4. A decision that touches your task hooks — `waiting → completed` is not an edge, by design (DEC-021)

Your `TASK-EU-NOT-PERSISTED-1` (#363) noted *"the runtime's transition table has no
`waiting → completed`"* and steps a paused task's unit `waiting → executing → completed`
(`task_service.py:716-723`). Looked into and **declined as an edge**: `waiting` on an execution
unit means *parked on an event the scheduler will deliver; work unfinished* — the truthful exits
are resuming (`resume_execution_unit()` → `resumed → executing`) then completing, or `failed`.
Your workaround **works and keeps working** — the `waiting → executing` edge it uses is kept
(the runtime's own gate re-entry uses it). What it costs you is honesty in your own audit trail:
that path skips the `resumed` state and leaves a zero-duration `executing`. Two truthful shapes,
your call, no urgency:

- don't move a paused task's unit to `waiting` at all — from the runtime's view a paused task is
  still in flight, and nothing will ever wake that unit (no `wait_condition`, not in the
  scheduler); or
- call `ExecutionUnitService(db).resume_execution_unit(eu.id)` before `update_status(..., "completed")`.

---

## 5. Other changes, none of which reach you

| Change | Why it does not reach you |
|---|---|
| Plugin hosts: a failed post-launch check (hostile attestation OR strong-sandbox live verification) now kills the worker on **every** path, and one failure counts once with its real kind (#694) | you run no `strong_sandbox_vm` / `hostile-third-party` plugin host |
| `sys.v1.agent.undo` is re-entrant: a second undo never re-invokes a compensator; response gains `already_reversed` (#696, `IDEM-12`) | you dispatch no `agent.undo`; no compensator is registered anywhere |
| The route execution contract is request-time only; the unwired boot-time AST validator is deleted (#698, DEC-023) | no behaviour change — the wrapper was always the only enforcement |
| `TenantContext.validate_memory_path` and MAS `validate_tenant_path` share one rule; the two admin guards share `is_operator_principal` (#699) | you import none of the four; `require_platform_admin_access` still admits any API key on the `/platform` tree by design |

---

## 6. Verification after the rebuild

```bash
# 1. You are on 2.19.0 — IN THE CONTAINER, path printed
docker exec <api> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
#    expect: 2.19.0  ['/usr/local/lib/python3.11/site-packages/AINDY']

# 2. No schema drift (exit 0)
docker exec <api> aindy-runtime bootstrap-schema

# 3. ★ §1 live: the snapshot counts agent runs. With at least one agent run in
#    approved/executing/pending_approval:
docker exec <api> python -c "
from AINDY.db.database import SessionLocal
from AINDY.platform_layer.system_state_service import compute_current_state
db = SessionLocal(); s = compute_current_state(db, force_refresh=True, persist_snapshot=False)
print(s['active_runs'], s['avg_execution_time'], s['health_status'])"
#    expect: active_runs >= your count of active agent runs (it was flow runs only before)
#    and NO `TENANT_VIOLATION` in the log for count_runs / list_recent_durations:
docker logs <api> 2>&1 | grep -c "sys.v1.agent.list_recent_durations"   # expect 0

# 4. The removed syscall answers with the standard envelope, not a 500
curl -X POST .../platform/syscall -H 'X-Platform-Key: <key with agent.read>' \
  -d '{"syscall":"sys.v1.agent.list_recent_durations","payload":{}}'
#    expect: status "error", error "Unknown syscall: 'sys.v1.agent.list_recent_durations'"

# 5. Readiness still passes with the smaller floor
curl -s .../health/deep | jq '.checks.syscall_registry'    # count >= 23
```

---

## 7. Your two 2.17.0 asks — unchanged, still yours

Recorded in your `RUNTIME_2_17_0_UPGRADE.md` §3 as owner's calls, correctly. Nothing in 2.19.0
changes their shape or cost:

- **Named predicates (#680):** migrate the seven lambdas to `when` **behind a drain**.
- **Authority gate (#681):** declare `on_denial="wait"` or a `degraded_variant` on one real tool
  so phase 3 has a first denial to flip on; a gate-parked run has survived a restart since 2.18.0.
