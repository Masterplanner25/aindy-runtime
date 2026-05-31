---
title: "Dependency Criticality Matrix"
last_verified: "2026-05-31"
api_version: "1.0"
status: current
owner: "platform-team"
---
﻿# Dependency Criticality Matrix

> Authored by Codex during non coding session. Needs review before repo commit and push.

This document classifies the major dependencies of `aindy-runtime` by operational criticality.

Its purpose is to make dependency expectations explicit across deployment profiles, health/readiness behavior, degraded-mode handling, and operator response.

This is an operational dependency contract, not a package manifest.

---

## Canonical Principle

Not every runtime dependency has the same importance.

A mature runtime must distinguish between:

- dependencies required for liveness
- dependencies required for readiness
- dependencies required only for specific profiles
- dependencies that allow safe degraded fallback
- dependencies whose failure makes the runtime unsafe to use

The goal is to avoid treating every dependency as equally critical or equally optional.

---

## Criticality Levels

### `critical`
The runtime cannot truthfully claim safe execution for the relevant profile without this dependency.

### `high`
The runtime may stay alive without this dependency, but important execution guarantees are reduced or blocked.

### `medium`
The runtime can often continue in degraded mode, but supported behavior narrows and operators must know it.

### `low`
The runtime can continue safely for most core responsibilities, though some secondary capabilities may be reduced.

---

## Dependency Matrix

| Dependency | Typical Role | Criticality | Required For All Profiles | Health Impact | Readiness Impact | Degraded Fallback | Operator Action |
|---|---|---|---|---|---|---|---|
| Primary SQL database | execution truth, persistence, recovery, runtime-owned schema | `critical` | effectively yes for meaningful runtime execution | `unhealthy` when unavailable | `not_ready` | very limited diagnostics only | restore DB access, verify schema state |
| Runtime-owned schema readiness | correctness of runtime persistence layer | `critical` | yes for profiles using runtime persistence and execution | `degraded` or `unhealthy` | `not_ready` | no honest full-execution fallback | migrate/repair schema before accepting work |
| Scheduler engine / background scheduling availability | wait/resume, delayed work, orchestration continuity | `high` | required for resumable/scheduled execution | `degraded` or `unhealthy` | usually `not_ready` for affected profiles | limited metadata-only survival | restore scheduler, verify role and startup ordering |
| Syscall registry completeness | execution dispatch correctness | `high` | required where documented syscalls are part of supported runtime behavior | `degraded` or `unhealthy` | `not_ready` when required syscalls missing | limited diagnostics only | restore registration path, verify bootstrap |
| Redis for cross-instance event bus | distributed wait/resume propagation | `high` in distributed profiles, `medium` otherwise | no | `degraded` when unavailable | `not_ready` for distributed profiles; may remain ready for local-only profiles | local-only mode if explicitly allowed | restore Redis or constrain deployment claim to local-only |
| Event bus subscriber | cross-instance resume delivery | `high` in distributed profiles, `medium` otherwise | no | `degraded` | `not_ready` for distributed profiles | local-only delivery if explicitly supported | restore subscriber, verify profile configuration |
| Wait/flow rehydration path | recovery after restart | `high` | required for honest resumable execution | `degraded` or `unhealthy` | usually `not_ready` for resumable workloads | metadata-only survival; pending work may strand | fix recovery path, inspect stranded work |
| Worker heartbeat / worker presence | distributed or async work execution | `high` in profiles that require workers | no | `degraded` or `unhealthy` | `not_ready` for distributed API assumptions | limited API liveness only | restore worker fleet or lower deployment claim |
| Mongo (when required by profile) | profile-specific data/path support | `high` when required, `low` or `medium` when optional | no | `degraded` or `unhealthy` if required | `not_ready` if required | continue only for non-Mongo-dependent paths | restore Mongo or disable dependent profile features |
| Mongo (when optional) | secondary capabilities | `medium` | no | `degraded` | often may remain ready | continue without Mongo-backed features | restore when practical, confirm fallback correctness |
| Resource/quota backend | runtime throttling and enforcement | `high` in production, `medium` in dev/test | no | `degraded` | execution blocked or `not_ready` in fail-closed production posture | fail-open only in explicitly non-production modes | restore quota backend; do not normalize fail-open |
| Nodus runtime availability | specific execution backend | `medium` or `high` depending on supported profile | no | `degraded` if profile depends on it | `not_ready` for Nodus-dependent execution claims | continue only for non-Nodus runtime paths | restore package/backend or narrow deployment claim |
| Platform registry restore | boot-time reconstruction of runtime-managed state | `high` | required for normal post-restore readiness | `degraded` | `not_ready` while pending/incomplete | liveness only until restore completes | complete restore and confirm runtime state |
| Health/observability pipeline | operator visibility | `medium` | no | may remain `degraded` | may remain ready if execution truth unaffected | reduced diagnostics only | restore visibility quickly to reduce blind operation |
| UI/SDK availability | downstream experience, not runtime truth | `low` from runtime perspective | no | usually none on runtime health | none on runtime readiness | runtime may continue normally | downstream teams address consumer impact |

---

## Profile-Sensitive Interpretation

Dependency criticality is not absolute. It changes with deployment profile.

### Local / Single-Instance Trusted Internal Profile
Typical interpretation:

- SQL database: `critical`
- schema readiness: `critical`
- scheduler: `high`
- Redis/event bus: often `medium`
- worker heartbeat: often `low` or profile-not-applicable
- Mongo: `medium` or lower unless explicitly required

### Distributed API / Multi-Instance Profile
Typical interpretation:

- SQL database: `critical`
- schema readiness: `critical`
- scheduler: `high`
- Redis/event bus: `high`
- event bus subscriber: `high`
- worker presence/heartbeat: `high`
- restore/rehydration: `high`

### Extension-Heavy Trusted Internal Profile
Typical interpretation:

- tenant/capability/security posture dependencies rise in importance
- trust-model violations become operationally high severity even if liveness remains intact
- quota and enforcement backends matter more if extension activity is significant

---

## Dependency Families

### 1. Execution Truth Dependencies
These control whether the runtime can safely execute work.

Examples:
- primary database
- schema readiness
- scheduler
- required syscalls
- rehydration/recovery path

Rule:
- failures here should rarely be treated as minor degradation

### 2. Distribution Dependencies
These matter most when runtime claims cross-instance or distributed guarantees.

Examples:
- Redis
- event bus subscriber
- worker presence/heartbeat

Rule:
- local fallback is acceptable only when the deployment profile allows it and readiness tells the truth

### 3. Policy and Enforcement Dependencies
These control whether the runtime can still defend its execution posture.

Examples:
- quota backend
- capability enforcement path
- tenant-context continuity

Rule:
- production-safe posture should generally fail closed rather than pretend full safety

### 4. Visibility Dependencies
These improve operation and diagnosis but do not always define runtime truth.

Examples:
- health aggregation internals
- observability pipeline
- some secondary stores or integrations

Rule:
- visibility failure should still be surfaced, but should not be confused with total execution failure unless it hides unsafe conditions

---

## Required Operator Responses

Use these expectations when a dependency degrades.

### Immediate Response Required
Use for failures involving:
- primary database
- schema readiness
- required syscalls missing
- scheduler unavailable for resumable execution
- distributed dependencies missing in distributed profiles
- rehydration failure that can strand work

### Prompt Response Required
Use for failures involving:
- optional Mongo in partially degraded profiles
- visibility pipeline degradation
- non-critical secondary backends

### Review Deployment Claim
Whenever fallback occurs, operators should ask:

- Is the current deployment profile still being described honestly?
- Is the runtime still ready for the class of work it is receiving?
- Are SDK/UI consumers being misled by partial availability?

---

## Dependency Decision Rules

When adding a new dependency, classify it with these questions:

1. Does runtime correctness depend on it?
2. Does readiness truth depend on it?
3. Is it required in all profiles or only some?
4. Is there a safe fallback, or only continued liveness without correctness?
5. If it fails, should operators stop sending work?
6. Would downstream repos misinterpret the runtime state if this dependency failed?

If a dependency affects execution truth, it is at least `high` criticality.

---

## Dependency Smells

These are warning signs that dependency handling is immature.

- the runtime cannot explain whether a dependency is required or optional
- local fallback exists but readiness still implies full distributed guarantees
- operator action is undocumented for critical dependency loss
- downstream repos interpret degraded dependency states inconsistently
- a dependency is treated as optional only because the code survives without it
- health says degraded while actual execution truth is no longer defensible

---

## Minimum Critical Set To Keep Defensible

If dependency classification cannot be completed for everything immediately, keep these clearly defined first:

1. primary SQL database
2. runtime-owned schema readiness
3. scheduler availability
4. required syscalls after bootstrap
5. Redis/event bus in distributed profiles
6. rehydration/recovery path
7. worker presence where distributed profiles depend on it

These are the dependencies most likely to create dangerous ambiguity.

---

## What Maturity Looks Like

Dependency maturity is reached when:

- every major dependency has an explicit criticality classification
- readiness behavior matches dependency truth
- fallback rules are profile-aware and documented
- operators know what requires immediate intervention
- downstream repos do not have to guess what degraded dependency states mean

The runtime should increasingly behave like a system with explicit dependency policy, not a service that merely survives missing infrastructure.

---

## Relationship To Other Docs

This document should align with:

- `DEGRADED_MODE_MATRIX.md`
- `SECURITY_POSTURE.md`
- `CROSS_REPO_COMPATIBILITY.md`
- `RUNTIME_STABILITY_INDEX.md`
- `RELEASE_GATES.md`
- `TEST_STRATEGY.md`

These docs answer different questions:

- `DEGRADED_MODE_MATRIX.md`: what remains safe under partial failure
- `SECURITY_POSTURE.md`: what trust and isolation claims are actually true
- `CROSS_REPO_COMPATIBILITY.md`: what downstream repos may safely depend on
- `RUNTIME_STABILITY_INDEX.md`: which surfaces are actually stable
- `DEPENDENCY_CRITICALITY_MATRIX.md`: which dependencies matter most, by profile and failure impact
