---
title: "Degraded Mode Matrix"
last_verified: "2026-05-31"
api_version: "1.0"
status: current
owner: "platform-team"
---
﻿# Degraded Mode Matrix

> Authored by Codex during non coding session. Needs review before repo commit and push.


This document defines how `aindy-runtime` should behave when critical or supporting dependencies are missing, unhealthy, partially initialized, or otherwise degraded.

Its purpose is to make degraded-mode behavior explicit instead of inferred from scattered implementation details.

This is an operational contract document.

---

## Canonical Principle

A degraded runtime is not necessarily a dead runtime.

But a degraded runtime must be explicit about:

- what still works
- what no longer works
- what is unsafe to claim as ready
- what operators should expect
- what downstream repos may safely infer

The runtime should prefer:

> visible degradation over misleading readiness

---

## Status Model

Use these terms consistently.

### `healthy`
The runtime can safely perform the documented class of work for the active deployment profile.

### `degraded`
The runtime is alive and some supported behaviors remain available, but one or more guarantees are reduced.

### `not_ready`
The runtime should not be treated as ready to accept the class of work implied by readiness, even if some routes or metadata still respond.

### `unhealthy`
The runtime is functioning poorly enough that health should report failure, typically because critical runtime guarantees are unavailable or unsafe.

---

## Operational Interpretation

### Liveness vs Readiness
`/health` and `/ready` do different jobs.

- `health` answers whether the service is alive and its current public health posture
- `ready` answers whether the runtime should receive work for the active deployment profile

A runtime may be:

- alive but not ready
- degraded but still partially useful
- healthy in metadata surfaces while not ready for execution

That distinction must stay explicit.

---

## Degraded Mode Matrix

| Condition | Health Expectation | Readiness Expectation | Safe To Continue | Unsafe / Reduced | Notes |
|---|---|---|---|---|---|
| Registry restore pending | `degraded` or service-alive posture | `not_ready` | metadata surfaces, diagnostics | execution that depends on restored registry state | Should surface as `restore_pending` until restore completes |
| Registry restore incomplete | `degraded` | `not_ready` | metadata surfaces, diagnostics | full runtime execution assumptions | Current readiness contract already distinguishes this |
| Runtime-owned schema not ready | `degraded` or `unhealthy` depending on profile | `not_ready` | health and diagnostics | execution paths that depend on runtime-owned schema | Should never look fully ready |
| Redis unavailable when event bus is optional | `degraded` | profile-dependent; may remain ready for local-only mode | local-only execution, local waits, metadata | cross-instance wait/resume propagation | Must explicitly surface local-only behavior |
| Redis unavailable when event bus is required | `degraded` or `unhealthy` | `not_ready` | diagnostics, limited local visibility | distributed execution assumptions | Should not silently downgrade a distributed profile into full readiness |
| Event bus subscriber unavailable in local-only-safe mode | `degraded` | may remain ready only for documented local profile | single-instance/local resume semantics | cross-instance resume guarantees | Runtime conditions should make this visible |
| Event bus subscriber unavailable in distributed mode | `degraded` or `unhealthy` | `not_ready` | diagnostics | multi-instance wait/resume guarantees | This should block readiness for distributed assumptions |
| Scheduler not started or not functional | `degraded` or `unhealthy` | `not_ready` for execution requiring resume/scheduling | metadata, some synchronous surfaces | scheduled/resumable execution | Runtime should not imply resumable work is safe |
| WAIT rehydration failure | `degraded` or `unhealthy` | usually `not_ready` for resumable execution | metadata, diagnostics | pending waits may be stranded | This is execution-truth degradation, not cosmetic degradation |
| Flow-run rehydration failure | `degraded` or `unhealthy` | usually `not_ready` for resumable orchestration | metadata, diagnostics | resumed orchestration guarantees | Should be surfaced as unsafe degraded condition |
| Required syscalls missing after bootstrap | `degraded` or `unhealthy` | `not_ready` if required by supported profile | metadata, diagnostics | syscall-dependent execution | Missing required syscalls should never be invisible |
| Quota backend unavailable in dev/test fail-open mode | `degraded` | may remain ready in explicitly non-production contexts | controlled testing/dev execution | production-grade quota guarantees | Must never be described as equivalent to production-safe posture |
| Quota backend unavailable in production fail-closed mode | `degraded` | `not_ready` or execution-blocked | metadata, diagnostics | quota-governed execution | Safer than silent fail-open |
| Mongo unavailable when optional | `degraded` | profile-dependent | execution paths not dependent on Mongo | Mongo-backed behavior | Readiness should follow documented profile assumptions |
| Mongo unavailable when required | `degraded` or `unhealthy` | `not_ready` | metadata, diagnostics | required Mongo-backed runtime behavior | Requirement must come from deployment contract, not guesswork |
| Primary database unavailable | `unhealthy` | `not_ready` | very limited diagnostics only | runtime execution truth, persistence, recovery | This is a hard runtime failure for most profiles |
| Worker heartbeat absent in distributed API profile | `degraded` or `unhealthy` | `not_ready` for distributed assumptions | limited metadata, diagnostics | distributed execution claims | Health and readiness should reflect profile dependency |
| Sandbox/trust posture weaker than claimed | `degraded` | `not_ready` if claim affects supported mode | diagnostics | any mode relying on stronger isolation assumptions | Security degradation is operational degradation |
| Partial dependency outage with safe documented fallback | `degraded` | may remain ready if fallback preserves supported guarantees | explicitly documented safe subset | full feature set | Fallback must be documented, not implied |

---

## Condition Families

### 1. Restore And Bootstrap Conditions
These occur before the runtime can honestly claim stable execution capability.

Examples:
- registry restore pending
- registry restore incomplete
- schema not ready
- required syscalls not registered

Expected rule:

- metadata may still work
- readiness should remain blocked
- operators should get a reason, not a generic failure

### 2. Execution-Core Degradations
These affect the execution substrate directly.

Examples:
- scheduler unavailable
- wait rehydration failure
- flow-run rehydration failure
- syscall dispatch prerequisites missing

Expected rule:

- readiness should not pretend execution is safe
- health should clearly show degraded or unsafe state
- downstream repos should not infer normal execution semantics

### 3. Profile-Dependent Dependency Loss
These depend on deployment profile.

Examples:
- Redis/event bus unavailable
- Mongo optional vs required
- worker heartbeat required in distributed API profiles

Expected rule:

- fallback is allowed only if the profile contract allows it
- local-only fallback must be explicit
- distributed guarantees must not silently collapse into local semantics

### 4. Security-Posture Degradations
These are often under-documented but critical.

Examples:
- extension trust assumptions no longer hold
- runtime isolation claim weaker than deployment expects
- sandbox posture weaker than required mode

Expected rule:

- degraded security must be surfaced as an operational condition
- readiness must not remain green when supported-mode security assumptions are false

---

## Safe Continuation Rules

A degraded runtime may continue serving only when all of these are true:

- the remaining behavior is documented
- the unsupported behavior is explicit
- readiness semantics still tell the truth for the active profile
- security posture is not being overstated
- downstream repos are not misled into assuming full runtime guarantees

If those conditions are not met, the runtime should prefer `not_ready`.

---

## Downstream Expectations

### `aindy-sdk`
SDK consumers may rely on:

- degraded and not-ready states being explicit
- health/readiness distinctions being meaningful
- unsupported execution states not being silently presented as normal

SDK consumers should not assume:

- every degraded state is still execution-safe
- local-only fallback equals distributed readiness
- liveness equals execution readiness

### `aindy-ui-kit`
UI components may rely on:

- degraded state being real and meaningful
- health/readiness labels mapping to stable semantics
- runtime reasons being suitable for operator-facing display when documented

UI should not assume:

- a responding runtime is automatically ready
- degraded means “minor cosmetic issue”
- all degraded states are equivalent in severity

---

## Severity Heuristics

Use these heuristics when classifying new degraded conditions.

### Safe Degraded
Use when:

- fallback behavior is documented
- execution guarantees for the active profile remain honest
- operators are informed
- unsupported behavior is clearly excluded

### Unsafe Degraded
Use when:

- execution truth is compromised
- resumability or tenant/capability guarantees may be weakened
- required bootstrap or recovery steps did not complete
- supported-mode security assumptions are no longer true

Unsafe degraded states should usually imply `not_ready`.

---

## Review Checklist

Use this when adding a new dependency, runtime condition, or degraded path.

- [ ] Does this condition affect liveness, readiness, or both?
- [ ] Can the runtime still safely execute the documented class of work?
- [ ] Is there a real fallback, or just survival without correctness?
- [ ] Does the active deployment profile allow degraded continuation?
- [ ] Are cross-instance guarantees affected?
- [ ] Are security or trust assumptions weakened?
- [ ] Should downstream SDK/UI treat this as not-ready?
- [ ] Is the operator-facing reason explicit enough?

---

## Minimum Matrix To Keep Stable

If the runtime cannot stabilize every degraded condition immediately, it should at minimum keep these consistent:

1. restore pending vs restore incomplete
2. schema not ready
3. event bus optional local-only fallback vs distributed requirement
4. scheduler/rehydration failure
5. required-syscall bootstrap failure
6. primary database unavailable
7. readiness semantics for unsafe degraded conditions

These are the conditions most likely to create damaging ambiguity.

---

## What Maturity Looks Like

Degraded-mode maturity is reached when:

- degraded states are few, named, and understandable
- readiness is operationally trustworthy
- fallback behavior is explicit and profile-aware
- operators can distinguish survivable degradation from unsafe execution
- SDK and UI consume stable degraded-mode semantics rather than guessing

The runtime should increasingly make degraded behavior boring and predictable.

---

## Relationship To Other Docs

This document should align with:

- `RUNTIME_BOUNDARY.md`
- `EXECUTION_INVARIANTS.md`
- `SECURITY_POSTURE.md`
- `CROSS_REPO_COMPATIBILITY.md`
- `PUBLIC_RUNTIME_SURFACES.md`
- `DEPLOYMENT_PROFILES.md`

These docs answer different questions:

- `RUNTIME_BOUNDARY.md`: what the runtime owns
- `EXECUTION_INVARIANTS.md`: what runtime behavior must not drift
- `SECURITY_POSTURE.md`: what security claims are actually true
- `CROSS_REPO_COMPATIBILITY.md`: what downstream repos may safely depend on
- `DEGRADED_MODE_MATRIX.md`: what remains safe under partial failure

