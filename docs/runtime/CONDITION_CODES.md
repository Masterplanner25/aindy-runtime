---
title: "Operator-Facing Condition Codes"
api_version: "1.0"
last_verified: "2026-06-06"
status: current
owner: "platform-team"
---
# Operator-Facing Condition Codes

Stable reference for all condition codes, status strings, and state machine values
returned by aindy-runtime to operators, automation tooling, the platform UI, and the
aindy-sdk.

These codes are formally defined in `AINDY/kernel/condition_codes.py`. Import them
from there rather than duplicating the strings.

**Stability commitment:** Removing or renaming a value in `RuntimeConditionCode` or
`ReadinessBlockerCode` requires a MAJOR version bump and a `CROSS_REPO_COMPATIBILITY.md`
notice. The entity status enums (`FlowRunStatus`, `AgentRunStatus`) are machine-verified
in `tests/unit/test_cross_repo_compatibility.py`.

---

## RuntimeConditionCode

Codes emitted by `set_api_runtime_condition()` during startup. They appear in:

- `/ready` response → `required_failures` list (when classification is `unsafe_degraded` or `startup_fatal`)
- `/health` response → `runtime_conditions` array
- `/platform/observability/dashboard` → conditions section

Each condition carries a `classification` from `ConditionClassification`:

| Classification | Effect on /ready | Meaning |
|---|---|---|
| `safe_degraded` | No effect — advisory only | Feature degraded but runtime is operational |
| `unsafe_degraded` | Added to `required_failures` (HTTP 503) | Correctness risk; production should not proceed |
| `startup_fatal` | Added to `required_failures` (HTTP 503) | Startup cannot continue in production |

### Condition Code Reference

| Code | Classification | Component | Meaning |
|---|---|---|---|
| `external_python_override_enabled` | `safe_degraded` | extension_policy | `AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS` is set; legacy override active (in-process execution is still blocked) |
| `redis_single_instance_mode` | `safe_degraded` | redis | `REDIS_URL` unset; cross-instance coordination unavailable |
| `event_bus_local_only` | `safe_degraded` | event_bus | WAIT/RESUME propagation is local-only; cross-instance resume unavailable |
| `event_bus_subscriber_unavailable` | `safe_degraded` | event_bus | Event bus subscriber failed to start; local-only fallback active |
| `queue_backend_fallback` | `safe_degraded` (single-instance) or `unsafe_degraded` (distributed) | queue | Redis unavailable; queue fell back to in-memory transport |
| `mongo_optional_unavailable` | `safe_degraded` | mongo | Optional MongoDB unavailable; embedding-dependent features degraded |
| `mongo_required_unavailable` | `startup_fatal` | mongo | Required MongoDB (`MONGO_REQUIRED=true`) unavailable; startup blocked |
| `distributed_worker_unavailable` | `unsafe_degraded` | worker | Distributed profile requires a worker heartbeat; none detected at startup |
| `event_bus_rehydration_drain_failed` | `unsafe_degraded` | rehydration | Buffered event drain after rehydration failed; resume events may be lost |
| `wait_eus_rehydration_failed` | `unsafe_degraded` | rehydration | WAIT execution-unit rehydration failed; pending waits may be stranded |
| `flow_run_rehydration_failed` | `unsafe_degraded` | rehydration | FlowRun rehydration failed; waiting flows may not resume correctly |
| `dynamic_registry_restore_failed` | `unsafe_degraded` | plugin_restore | Dynamic registry restore failed; runtime extensions were not restored from DB |
| `dynamic_registry_restore_incomplete` | `unsafe_degraded` | plugin_restore | Registry restore incomplete; some flows, nodes, or webhooks missing |

---

## ReadinessBlockerCode

Codes that appear in the `required_failures` list of `/ready` responses.
A non-empty `required_failures` means `status: "not_ready"` (HTTP 503).

| Code | Trigger |
|---|---|
| `startup_incomplete` | API startup sequence has not finished |
| `postgres` | PostgreSQL unreachable or connection failed |
| `schema` | Schema contract check failed (incompatible or upgrade required) |
| `redis` | Redis unavailable when required by the active deployment profile |
| `queue` | Queue backend unavailable when required |
| `event_bus` | Event bus unavailable when required |
| `worker` | Worker heartbeat missing when required |
| `scheduler` | Background scheduler not running (when this instance is elected leader) |
| `plugin_hosts` | Plugin host health check failed |
| `plugin_sandbox_attestation` | Hostile third-party profile: sandbox attestation violations detected |
| *(any RuntimeConditionCode)* | `unsafe_degraded` or `startup_fatal` condition is active |

---

## SyscallResponseStatus

Every `SyscallDispatcher.dispatch()` call returns an envelope with a top-level `status` field:

| Value | Meaning |
|---|---|
| `success` | Handler completed without exception |
| `error` | Handler raised exception or input/output validation failed |

The full envelope shape:

```json
{
  "status": "success",
  "data": {},
  "trace_id": "...",
  "execution_unit_id": "...",
  "syscall": "sys.v1.domain.action",
  "version": "v1",
  "duration_ms": 42,
  "error": null,
  "warning": null
}
```

---

## FlowRunStatus

Lifecycle states for a `FlowRun` entity. Returned in flow execution responses.

| Value | Terminal | Meaning |
|---|---|---|
| `running` | No | Execution in progress |
| `waiting` | No | Suspended on a WAIT node; resumes when matching event fires |
| `completed` | Yes | Flow finished successfully |
| `failed` | Yes | Flow terminated with error |

The `waiting_for` field on `FlowRun` stores the event type being awaited
(e.g. `__time_wait__` for timed waits, or an application-defined event name).

---

## AgentRunStatus

Lifecycle states for an `AgentRun` entity. Returned in agent execution responses.

| Value | Terminal | Meaning |
|---|---|---|
| `pending_approval` | No | Awaiting operator approval before execution |
| `approved` | No | Approved; execution may begin |
| `executing` | No | Plan in progress |
| `delegated` | No | Sub-agent dispatched; not yet terminal |
| `completed` | Yes | Agent run finished successfully |
| `failed` | Yes | Agent run terminated with error |

---

## DependencyStatus

Per-component status in `/health/deep` dependency checks.

| Value | Meaning |
|---|---|
| `ok` | Component reachable and operational |
| `degraded` | Operational with non-critical failures |
| `unavailable` | Component unreachable or has failed |
| `not_configured` | Optional dependency not wired up (e.g. `REDIS_URL` unset) |
| `not_running` | Initialized but not actively running (e.g. scheduler follower) |
| `not_applicable` | Check not relevant for this deployment profile |

---

## PublicHealthStatus

Top-level status in `/health` responses.

| Value | Meaning |
|---|---|
| `ok` | All critical dependencies operational |
| `degraded` | Non-critical failures or domain health issues |
| `unhealthy` | A critical dependency has failed |

---

## AutonomyDecision

Decision codes returned by the trigger evaluator for autonomous agent runs.
Returned as `status: EXECUTE / DEFERRED / IGNORED` in agent autonomous responses.

| Value | HTTP status field | Meaning |
|---|---|---|
| `execute` | `EXECUTE` | Trigger fires; agent execution proceeds immediately |
| `defer` | `DEFERRED` | Trigger deferred; re-evaluation scheduled after `defer_seconds` |
| `ignore` | `IGNORED` | Trigger dismissed; no further re-evaluation |

---

## Usage

Import from the kernel module — do not copy the string literals:

```python
from AINDY.kernel.condition_codes import (
    AgentRunStatus,
    AutonomyDecision,
    ConditionClassification,
    DependencyStatus,
    FlowRunStatus,
    PublicHealthStatus,
    ReadinessBlockerCode,
    RuntimeConditionCode,
    SyscallResponseStatus,
)

# Emit a stable condition
from AINDY.platform_layer.deployment_contract import set_api_runtime_condition
set_api_runtime_condition(
    code=RuntimeConditionCode.QUEUE_BACKEND_FALLBACK,
    component="queue",
    classification=ConditionClassification.SAFE_DEGRADED,
    detail="...",
    production_behavior="...",
)

# Check a flow run status
if flow_run.status == FlowRunStatus.WAITING:
    ...
```

---

## Relationship to Other Docs

- `docs/runtime/SDK_CONTRACT.md` — SDK stable surfaces; condition codes are an operator surface, not SDK surface
- `docs/runtime/DEGRADED_RUNTIME_MODES.md` — what degrades when each condition fires
- `docs/runtime/OPERATOR_RUNBOOK.md` — triage guidance keyed by condition code
- `AINDY/kernel/condition_codes.py` — authoritative source of truth
- `tests/unit/test_cross_repo_compatibility.py` — machine-verified stability assertions
