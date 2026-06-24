---
title: Deployment Profiles
last_verified: "2026-06-24"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Deployment Profiles


This document defines the runtime-owned deployment topology contract.

Boot mode and deployment profile are separate axes:

- boot mode controls which HTTP and extension surface is mounted
- deployment profile controls which infrastructure guarantees must exist

`AINDY_BOOT_MODE=runtime-only` selects the runtime-only surface.
`AINDY_DEPLOYMENT_PROFILE` selects the deployment topology.

Operator scope note:

- profile enforcement covers dependency and runtime-condition guarantees for the
  declared topology
- it does not imply extension isolation, third-party code trust, or a broader
  platform security certification

If `AINDY_DEPLOYMENT_PROFILE` is unset:

- API startup infers the profile from `EXECUTION_MODE`
- worker startup defaults to `distributed-worker`

## Runtime-Owned Profiles

| Profile | Process role | Required execution mode | Required infrastructure | Notes |
| --- | --- | --- | --- | --- |
| `single-instance` | API | `thread` | PostgreSQL, runtime schema | Redis is optional. No separate worker is required. WAIT/RESUME is local-only when Redis is absent. |
| `distributed-api` | API | `distributed` | PostgreSQL, runtime schema, Redis, event bus, durable queue backend, at least one worker process | This is the multi-instance API contract. `AINDY_EVENT_BUS_ENABLED=false` and `AINDY_CACHE_BACKEND=memory` are rejected. |
| `distributed-worker` | Worker | `distributed` | PostgreSQL, runtime schema, Redis, durable queue backend | The worker process consumes queued work and can participate in lease-based background leadership. |

Support interpretation:

- these rows describe runtime-owned profile mechanics and dependency requirements
- they do not by themselves imply identical support level or claim strength across all deployment narratives
- current supported posture should be interpreted through `PROFILE_SUPPORT_MATRIX.md`

## Enforcement

Startup validation now fails when the selected profile and process configuration disagree.

Examples:

- `single-instance` with `EXECUTION_MODE=distributed` -> rejected
- `distributed-api` without `REDIS_URL` -> rejected
- `distributed-api` with `AINDY_EVENT_BUS_ENABLED=false` -> rejected
- `distributed-api` with `AINDY_CACHE_BACKEND=memory` -> rejected
- `distributed-worker` with `EXECUTION_MODE=thread` -> rejected

For the distributed API profile, worker presence is a required dependency:

- development may surface an explicit unsafe degraded condition when no worker heartbeat is visible yet
- production startup fails rather than presenting a healthy distributed API with no workers

This should be read together with:

- `DEGRADED_MODE_MATRIX.md` for readiness and fallback truth
- `DEPENDENCY_CRITICALITY_MATRIX.md` for dependency impact by profile
- `PROFILE_SUPPORT_MATRIX.md` for what the runtime is currently willing to claim as supported

## Background Leadership

Background scheduler semantics differ by profile:

- `single-instance`: `in-process`
- `distributed-api`: `lease-elected`
- `distributed-worker`: `lease-elected`

Lease-elected means exactly one participating runtime process becomes leader at a
time through the runtime lease table. Follower processes remain API- or queue-only.

This is enforced (LEASE-1, `AINDY/platform_layer/leadership.py`): a leader is the
process that atomically holds the row in `background_task_leases`. Each lease-electing
process runs a `BackgroundLeadershipElector` that claims/renews the lease on a
heartbeat (`AINDY_BACKGROUND_LEASE_HEARTBEAT_SECONDS`, default 20s) within a TTL
(`AINDY_BACKGROUND_LEASE_TTL_SECONDS`, default 60s). On leader death the lease
lapses and a follower takes over within at most one TTL; a leader that loses the
lease stands its scheduler down to avoid split-brain. The lease is released on
graceful shutdown so a standby is promoted immediately. The `single-instance`
profile is `in-process`: a single local process, no lease.

## Runtime State And Health

Runtime state now reports:

- `process_role`
- `deployment_profile`
- `deployment_profile_source`
- `background_leadership_mode`

`/health` and `/ready` include the active deployment profile through the
deployment contract payload and readiness checks.

## Runtime-Only Boot Pairing

`runtime-only` boot may be paired with:

- `single-instance`
- `distributed-api`

It must not be confused with `distributed-worker`, which is a worker process role
rather than an HTTP surface selection.
