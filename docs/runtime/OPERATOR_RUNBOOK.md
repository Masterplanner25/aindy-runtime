# Operator Runbook

> Authored by Codex during non coding session. Needs review before repo commit and push.

This document is a high-level operator runbook for `aindy-runtime`.

Its purpose is to turn the runtime’s boundary, readiness, degraded-mode, dependency, and release docs into actionable operator guidance.

This is not a full incident manual. It is a practical operating guide.

---

## Canonical Principle

Operators should treat `aindy-runtime` as an execution substrate, not just a web service.

That means runtime operation is not only about process uptime. It is about:

- execution truth
- readiness truth
- degraded-mode truth
- dependency truth
- profile truth

A live process is not automatically a healthy runtime.

---

## First Questions To Ask

When evaluating runtime state, ask in this order:

1. Is the runtime alive?
2. Is the runtime ready for the active deployment profile?
3. Are execution-critical dependencies healthy?
4. Are any degraded conditions unsafe rather than merely inconvenient?
5. Is the runtime still honestly operating within its claimed profile?

---

## Key Surfaces To Check

### 1. Version and Identity
Use:
- `/api/version`

Purpose:
- confirm runtime identity
- confirm expected deployed version
- compare release expectations with actual runtime state

### 2. Health
Use:
- `/health`
- `/health/detail`
- `/health/deep`
- `/health/domains`
- `/health/sandbox` where relevant

Purpose:
- identify public health posture
- inspect degraded components
- understand sandbox/trust posture signals where exposed

### 3. Readiness
Use:
- `/ready`
- `/readiness`

Purpose:
- determine whether the runtime should currently receive work
- distinguish liveness from execution-safe readiness

---

## Normal Operating Expectations

In a healthy trusted-internal supported profile, operators should expect:

- `/api/version` returns expected runtime identity
- `/health` reports healthy or appropriately limited degraded state
- `/ready` reports ready only when execution-critical conditions are satisfied
- distributed profiles do not silently degrade into local-only claims
- restore/rehydration-sensitive workloads are not accepted when that path is unhealthy

---

## Common Runtime States

## 1. Healthy and Ready
Meaning:
- runtime is alive
- dependencies required for the active profile are satisfied
- execution claims are currently defensible

Operator response:
- normal monitoring
- verify release/version context if this follows deployment

## 2. Alive but Not Ready
Meaning:
- the process is up
- runtime cannot safely accept the class of work implied by readiness

Common causes:
- restore pending
- schema not ready
- required syscalls missing
- distributed dependency missing for distributed profile
- rehydration or scheduler issues

Operator response:
- do not treat this as a harmless warmup unless the reason is known and expected
- inspect readiness reason first
- verify whether the condition is transient or a real blocker

## 3. Degraded but Possibly Usable
Meaning:
- some behaviors still work
- some guarantees are reduced
- profile-specific interpretation matters

Operator response:
- determine whether degradation is safe or unsafe for the active profile
- verify whether work should continue, be limited, or be stopped
- confirm downstream systems are not assuming full guarantees

## 4. Unhealthy
Meaning:
- runtime execution truth is likely compromised or unavailable

Operator response:
- treat as operationally serious
- focus on critical dependencies and whether the runtime should continue receiving work at all

---

## Triage By Symptom

### Symptom: `/ready` returns not ready
Check:
- restore state
- runtime-owned schema readiness
- required syscalls after bootstrap
- scheduler and rehydration path
- distributed dependency state if in distributed mode

Likely action:
- hold work intake
- restore the blocking dependency or bootstrap step

### Symptom: `/health` is degraded but process is up
Check:
- whether degradation is safe or unsafe
- whether the active profile still matches the actual dependency state
- whether degraded mode is local-only fallback, visibility-only loss, or execution-core loss

Likely action:
- continue cautiously only if degradation is explicitly safe for the profile
- otherwise treat as not-ready in operational practice even if some routes still respond

### Symptom: cross-instance resume is not working
Check:
- Redis availability
- event bus subscriber state
- whether the runtime has fallen back to local-only mode
- whether the profile still claims distributed support

Likely action:
- restore Redis/event bus path
- do not keep claiming distributed readiness while local-only

### Symptom: resumed work appears stranded
Check:
- wait/flow rehydration path
- scheduler availability
- orphaned wait recovery behavior
- runtime conditions related to rehydration failure

Likely action:
- inspect recovery path and waiting state
- treat as execution-truth degradation, not cosmetic noise

### Symptom: extension-heavy paths behave unsafely or unexpectedly
Check:
- whether current deployment is still within trusted-internal assumptions
- capability enforcement and tenant behavior on the affected path
- whether a stronger security claim is being implicitly assumed than the runtime supports

Likely action:
- reduce claim scope
- disable or constrain risky extension behavior until posture is restored

---

## Dependency-Focused Triage

### Primary Database Failure
Impact:
- critical
- runtime execution truth is compromised
- readiness should be false

Operator action:
- restore DB connectivity
- verify schema state after recovery
- do not treat liveness as sufficient

### Schema Not Ready
Impact:
- critical
- runtime should not accept work that depends on runtime-owned persistence truth

Operator action:
- complete migration or repair
- verify readiness changes only after schema truth is restored

### Scheduler or Rehydration Failure
Impact:
- high
- resumable execution claims are weakened or invalid

Operator action:
- verify startup ordering, scheduler role, and recovery path
- treat pending waits/runs as at risk until proven otherwise

### Redis/Event Bus Failure in Distributed Profile
Impact:
- high
- distributed resume guarantees are reduced or absent

Operator action:
- restore Redis/event bus
- if fallback is local-only, narrow operational claim immediately

### Worker Failure in Worker-Dependent Profile
Impact:
- high
- distributed execution assumptions may no longer hold

Operator action:
- restore worker health or stop claiming that profile’s readiness

### Quota/Policy Backend Failure
Impact:
- high in production
- may be weaker in explicit dev/test modes

Operator action:
- verify whether current behavior is fail-open or fail-closed
- never normalize dev/test fail-open assumptions into production operations

---

## Profile-Aware Operating Rules

### Local Trusted-Internal Single-Instance
Operators may tolerate:
- absence of distributed dependencies if the profile does not claim them

Operators must not tolerate:
- schema, DB, scheduler, or required syscall failures being hidden behind superficial liveness

### Multi-Instance Distributed Trusted-Internal
Operators must verify:
- Redis/event bus health
- worker presence where required
- readiness truth for distributed assumptions

Operators must not tolerate:
- local-only fallback while continuing to route work as if full distributed support exists

### Reduced-Dependency Dev/Test
Operators may allow:
- narrower guarantees
- explicit dev/test fail-open behaviors where documented

Operators must not do:
- treat dev/test posture as equivalent to production support

---

## Escalation Heuristics

Escalate immediately when:

- readiness is false for unknown reasons
- primary DB or schema readiness is compromised
- required syscalls are missing after bootstrap
- scheduler/rehydration failure may strand work
- distributed profile is operating without distributed prerequisites
- runtime security posture is weaker than the deployed claim

Escalate with urgency but not necessarily outage posture when:

- optional secondary dependencies degrade
- observability is reduced but execution truth remains intact
- local-only mode is active in a profile that intentionally allows it

---

## Release-Day Checks

After deploying a new runtime release, operators should verify:

- `/api/version` matches expected release
- `/health` reports expected posture
- `/ready` reflects truthful profile state
- no unexpected degraded runtime conditions appeared
- required distributed/runtime dependencies are still recognized correctly
- downstream SDK/UI consumers are not obviously broken on stable surfaces

This should align with `RELEASE_GATES.md`.

---

## What Operators Should Not Assume

- a running process means runtime execution is safe
- degraded always means minor
- local-only fallback is acceptable for distributed claims
- tenant enforcement automatically means strong hostile-code isolation
- extension-heavy operation is safe outside trusted-internal assumptions
- downstream repos will interpret ambiguous runtime states correctly without explicit signals

---

## Minimal Incident Notes To Record

When a significant runtime incident occurs, record at least:

- active deployment profile
- observed health and readiness states
- dependency failures involved
- whether degraded mode was safe or unsafe
- whether downstream SDK/UI behavior was affected
- whether profile claims had to be narrowed during the incident
- what invariant or dependency classification failed in practice

---

## Relationship To Other Docs

This runbook should align with:

- `DEGRADED_MODE_MATRIX.md`
- `DEPENDENCY_CRITICALITY_MATRIX.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `SECURITY_POSTURE.md`
- `RELEASE_GATES.md`
- `TEST_STRATEGY.md`

These docs answer different questions:

- `DEGRADED_MODE_MATRIX.md`: what remains safe under partial failure
- `DEPENDENCY_CRITICALITY_MATRIX.md`: which dependencies matter most
- `PROFILE_SUPPORT_MATRIX.md`: which deployment profiles are actually supported
- `SECURITY_POSTURE.md`: what security claims are actually true
- `OPERATOR_RUNBOOK.md`: how operators should interpret and respond to runtime state
