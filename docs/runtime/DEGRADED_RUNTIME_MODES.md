---
title: "Degraded Runtime Modes"
last_verified: "2026-05-18"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Degraded Runtime Modes


This document defines the runtime-owned degraded-mode contract exposed through
`AINDY/platform_layer/deployment_contract.py`, `/health`, and `/ready`.

## Classifications

- `safe_degraded`
  - The process may continue serving traffic.
  - `/health` reports a degraded state.
  - `/ready` stays green unless another required dependency fails.
- `unsafe_degraded`
  - The process is running, but a runtime invariant is broken.
  - `/health` reports `unhealthy`.
  - `/ready` fails.
  - Production startup should reject these states where the failure is known at boot time.
- `startup_fatal`
  - The runtime must not complete startup in this state.
  - The condition may still be recorded in runtime state just before the startup exception is raised.

Reading rule:

- this file defines classification labels and current condition-code mapping
- it does not replace the broader profile-aware degraded truth model in `DEGRADED_MODE_MATRIX.md`

## Current Contract

| Component | Condition code | Classification | Development behavior | Production behavior |
| --- | --- | --- | --- | --- |
| Redis | `redis_single_instance_mode` | `safe_degraded` | Start in single-instance mode; `/health` shows degradation | Allowed only when deployment does not require Redis |
| Event bus | `event_bus_local_only` | `safe_degraded` | Start with local-only WAIT/RESUME propagation | Allowed only when deployment does not require cross-instance coordination |
| Event bus | `event_bus_subscriber_unavailable` | `safe_degraded` | Start and expose degraded state when Redis/event bus is optional | If the deployment contract requires the event bus, startup fails before this degraded path is used |
| Queue backend fallback | `queue_backend_fallback` | `safe_degraded` in thread mode, `unsafe_degraded` in distributed mode | Continue only for non-production thread-mode fallback | Production distributed startup must fail rather than silently using an in-memory queue |
| Mongo optional paths | `mongo_optional_unavailable` | `safe_degraded` | Start without Mongo-backed features | Same, unless `MONGO_REQUIRED=true` |
| Mongo required paths | `mongo_required_unavailable` | `startup_fatal` | N/A | Startup fails |
| Dynamic registry restore | `dynamic_registry_restore_failed` | `unsafe_degraded` | Start, but `/ready` fails and operators see the missing restore state | Startup fails |
| Dynamic registry verification | `dynamic_registry_restore_incomplete` | `unsafe_degraded` | Start, but `/ready` fails and operators see incomplete restore counts | Startup fails |
| WAIT EU rehydration | `wait_eus_rehydration_failed` | `unsafe_degraded` | Start, but `/ready` fails and operators see stranded-wait risk | Startup fails |
| FlowRun rehydration | `flow_run_rehydration_failed` | `unsafe_degraded` | Start, but `/ready` fails and operators see stranded-wait risk | Startup fails |
| Event drain after rehydration | `event_bus_rehydration_drain_failed` | `unsafe_degraded` | Start, but `/ready` fails and operators see lost-resume risk | Startup fails |

## Important Limit

These signals improve operator visibility. They do not provide isolation or
sandboxing. If the process is alive, it is still the same in-process runtime,
with the same trust boundaries described in `docs/runtime/EXTENSION_TRUST_MODEL.md`.
