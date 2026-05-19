---
title: Deployment Profiles
last_verified: "2026-05-18"
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

If `AINDY_DEPLOYMENT_PROFILE` is unset:

- API startup infers the profile from `EXECUTION_MODE`
- worker startup defaults to `distributed-worker`

## Supported Profiles

| Profile | Process role | Required execution mode | Required infrastructure | Notes |
| --- | --- | --- | --- | --- |
| `single-instance` | API | `thread` | PostgreSQL, runtime schema | Redis is optional. No separate worker is required. WAIT/RESUME is local-only when Redis is absent. |
| `distributed-api` | API | `distributed` | PostgreSQL, runtime schema, Redis, event bus, durable queue backend, at least one worker process | This is the multi-instance API contract. `AINDY_EVENT_BUS_ENABLED=false` and `AINDY_CACHE_BACKEND=memory` are rejected. |
| `distributed-worker` | Worker | `distributed` | PostgreSQL, runtime schema, Redis, durable queue backend | The worker process consumes queued work and can participate in lease-based background leadership. |

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

## Background Leadership

Background scheduler semantics differ by profile:

- `single-instance`: `in-process`
- `distributed-api`: `lease-elected`
- `distributed-worker`: `lease-elected`

Lease-elected means exactly one participating runtime process becomes leader at a
time through the runtime lease table. Follower processes remain API- or queue-only.

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
