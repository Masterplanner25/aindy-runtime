---
title: "Incident Classification"
last_verified: "2026-05-31"
api_version: "1.0"
status: current
owner: "platform-team"
---
﻿# Incident Classification

> Authored by Codex during non coding session. Needs review before repo commit and push.

This document defines how to classify incidents involving `aindy-runtime`.

Its purpose is to make incident severity, response urgency, and communication posture more consistent with runtime reality.

This is an operational classification document, not a pager policy.

---

## Canonical Principle

Runtime incidents should be classified by impact on runtime truth, not just by whether the process is reachable.

The key questions are:

- is execution truth compromised?
- is readiness truth compromised?
- is a supported profile claim no longer defensible?
- are security or isolation assumptions weakened?
- are downstream operators or consumers being misled?

---

## Severity Levels

### `SEV-1`
Critical runtime failure or unsafe runtime state.

Use when:
- supported execution is unavailable or unsafe
- readiness truth is materially broken
- a supported profile claim is false in a way that risks real harm
- security posture is materially weaker than the deployed claim
- broad customer/operator impact or serious execution-integrity risk exists

### `SEV-2`
Major runtime degradation with meaningful operational risk.

Use when:
- important execution guarantees are reduced
- degraded mode is active in a way that blocks a supported class of work
- distributed/runtime coordination is materially impaired
- downstream behavior is likely to misbehave if not contained quickly

### `SEV-3`
Contained degradation or partial capability loss.

Use when:
- core runtime truth remains mostly intact
- some supported behaviors are reduced or unavailable
- impact is real but bounded
- fallback exists and is honest

### `SEV-4`
Low-severity issue or localized defect.

Use when:
- no core runtime guarantee is materially affected
- issue is mostly cosmetic, diagnostic, or narrowly contained
- operators remain able to trust health/readiness and execution posture

---

## Severity Matrix

| Incident Type | Typical Severity | Why |
|---|---|---|
| Primary DB unavailable | `SEV-1` | runtime execution truth and persistence are compromised |
| Runtime-owned schema not ready in a supported profile | `SEV-1` | runtime should not accept meaningful work |
| Readiness falsely reporting ready while unsafe | `SEV-1` | operators are being misled about execution safety |
| Scheduler unavailable for resumable execution | `SEV-1` or `SEV-2` | depends on whether supported workloads are blocked or stranded |
| Rehydration/recovery failure stranding pending work | `SEV-1` or `SEV-2` | execution continuity is compromised |
| Required syscalls missing after bootstrap | `SEV-1` or `SEV-2` | supported execution contract is broken |
| Distributed profile running without distributed prerequisites | `SEV-1` or `SEV-2` | supported profile claim is no longer true |
| Redis/event bus unavailable in local-only-safe profile | `SEV-3` | real degradation, but may be contained if claim narrows honestly |
| Worker heartbeat absent in worker-dependent distributed profile | `SEV-2` | important execution capability reduced or blocked |
| Optional Mongo unavailable | `SEV-3` | partial capability loss if profile permits fallback |
| Quota backend fail-open in explicit dev/test | `SEV-3` | not ideal, but may match documented non-production posture |
| Quota backend failure in production fail-closed mode | `SEV-2` | execution blocking but safer than silent unsafe continuation |
| Tenant/capability enforcement regression | `SEV-1` | security posture and runtime truth affected |
| Extension trust posture weaker than claimed | `SEV-1` or `SEV-2` | depends on breadth of exposure and active risk |
| Incorrect or misleading `/health` details with truthful `/ready` | `SEV-3` or `SEV-4` | visibility issue unless it causes operational misrouting |
| Cosmetic metadata/reporting issue | `SEV-4` | low operational impact |

---

## Classification Rules

## 1. Execution Truth First
Classify upward when:

- work may be lost, stranded, duplicated, or accepted unsafely
- restart or recovery guarantees are compromised
- supported execution semantics are no longer trustworthy

## 2. Readiness Truth Matters
Classify upward when:

- `/ready` says ready but the runtime cannot safely do the claimed work
- deployment automation or routing decisions may be wrong because of the runtime’s signaling

## 3. Profile Truth Matters
Classify upward when:

- the runtime is still being described as a supported profile it can no longer honestly satisfy
- local-only fallback is active while distributed assumptions are still in play

## 4. Security Truth Matters
Classify upward when:

- tenant/capability enforcement is weakened
- trust boundaries are weaker than the deployment expects
- extension behavior exceeds supported security posture

---

## Response Expectations

### `SEV-1`
Expected response:
- immediate operator attention
- rapid containment or traffic restriction
- explicit acknowledgment that runtime truth is compromised
- release rollback or mitigation consideration if caused by recent change

### `SEV-2`
Expected response:
- urgent response
- active mitigation
- close monitoring of degraded state and supported profile claims

### `SEV-3`
Expected response:
- prompt investigation
- mitigation if trend suggests escalation
- communication if downstream teams may misinterpret state

### `SEV-4`
Expected response:
- routine handling
- backlog or minor operational follow-up as appropriate

---

## Escalation Triggers

Escalate severity if any of these are true:

- a degraded condition is being reported as ready
- a fallback mode is active but downstream systems are unaware
- a previously bounded issue begins affecting execution truth
- incident scope crosses from one profile into a broader supported posture
- security assumptions are no longer true for the active deployment

---

## Containment Guidance

### When To Stop or Limit Work Intake
Containment should be considered when:

- readiness truth is compromised
- primary execution dependencies are down
- scheduler/rehydration issues may strand work
- distributed guarantees are broken in a distributed profile
- security posture is weaker than the supported claim

### When Degraded Continuation Is Acceptable
Degraded continuation is more acceptable when:

- the fallback is documented
- the profile still supports the narrower behavior honestly
- operators and downstream consumers can tell what is reduced
- execution truth is not being overstated

---

## Communication Guidance

When communicating incidents, include:

- active deployment profile
- current health and readiness posture
- whether the runtime is alive, ready, degraded, or unsafe
- which dependency or guarantee failed
- whether the runtime claim had to be narrowed
- whether SDK/UI consumers may observe changed behavior

Avoid saying only:
- “service is up”
- “degraded”
- “looks fine locally”

Those are too vague for runtime incidents.

---

## Incident Smells

These are signs the incident is being under-classified.

- the process is reachable, so severity is assumed low
- readiness is false but treated as informational only
- local-only fallback is framed as harmless during distributed operation
- stranded or unrecoverable work is treated as a normal transient
- tenant/capability regressions are framed as edge cases
- downstream misrouting risk is ignored because the API still responds

---

## Minimum Incident Record

Each meaningful runtime incident should record at least:

- severity level
- active profile
- affected guarantees
- health vs readiness state
- dependency failures involved
- whether degraded mode was safe or unsafe
- downstream repo impact if known
- containment action taken
- whether profile claim or security claim had to be narrowed

---

## What Maturity Looks Like

Incident classification maturity is reached when:

- incidents are classified by runtime truth, not process visibility
- operators escalate when guarantees weaken, not only when processes die
- profile and security posture changes are treated as real incidents when appropriate
- downstream and operator communication becomes more precise and less surprising

The runtime should increasingly detect and describe unsafe states before they turn into ambiguous outages.

---

## Relationship To Other Docs

This document should align with:

- `OPERATOR_RUNBOOK.md`
- `DEGRADED_MODE_MATRIX.md`
- `DEPENDENCY_CRITICALITY_MATRIX.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `SECURITY_POSTURE.md`
- `RELEASE_GATES.md`
