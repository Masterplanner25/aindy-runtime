---
title: "Profile Support Matrix"
last_verified: "2026-05-31"
api_version: "1.0"
status: current
owner: "platform-team"
---
﻿# Profile Support Matrix

> Authored by Codex during non coding session. Needs review before repo commit and push.

This document defines which deployment profiles `aindy-runtime` supports, what each profile is allowed to claim, and which capabilities are required, optional, degraded, or unsupported in each mode.

Its purpose is to stop profile language from drifting into vague or overstated platform claims.

This is a support-boundary document, not a deployment tutorial.

---

## Canonical Principle

A profile is supported only when the runtime can honestly defend the guarantees associated with that profile.

A profile is not supported just because the process can start.

Each profile must answer:

- what the runtime is claiming
- what dependencies are required
- what execution guarantees are expected
- what degraded fallback is acceptable
- what is explicitly unsupported

---

## Support Levels

### `supported`
Use when:

- the runtime intentionally supports the profile
- dependency expectations are documented
- readiness semantics for the profile are defined
- the team is willing to treat profile breakage as a real release issue

### `supported with constraints`
Use when:

- the profile is real and useful
- the runtime supports it only within narrower conditions
- some capabilities are intentionally absent or reduced
- the constraints are explicit and should not be hand-waved away

### `experimental`
Use when:

- the profile may work
- parts of the contract are still moving
- the team is not yet ready to defend it as a stable runtime promise

### `unsupported`
Use when:

- the runtime may start, but the profile should not be claimed as safe or supported
- core guarantees for the profile cannot be defended

---

## Profile Matrix

| Profile | Support Level | Runtime Claim | Required Dependencies | Key Guarantees | Allowed Degraded Fallback | Explicitly Unsupported |
|---|---|---|---|---|---|---|
| Local trusted-internal single-instance | `supported` | single-instance trusted-internal runtime with execution, health, readiness, and resumable work support | primary DB, schema readiness, scheduler, required syscalls | truthful readiness, local execution, local wait/resume, runtime metadata surfaces | limited degraded operation when documented, e.g. optional secondary dependencies absent | distributed cross-instance guarantees without required infra |
| Local trusted-internal single-instance without Redis/event bus | `supported with constraints` | local-only runtime with no cross-instance resume guarantee | primary DB, schema readiness, scheduler, required syscalls | local execution and local wait/resume only | local-only degraded mode is acceptable if claimed honestly | any implied distributed resume or multi-instance wakeup guarantee |
| Multi-instance trusted-internal distributed API runtime | `supported with constraints` | distributed runtime with cross-instance wait/resume and worker-aware execution semantics | primary DB, schema readiness, scheduler, Redis/event bus, required syscalls, worker presence where profile depends on it | truthful distributed readiness, cross-instance resume where documented, profile-aware degraded handling | narrow degraded survival for diagnostics only; not full ready state when distributed guarantees are broken | silently collapsing into local-only semantics while still claiming distributed readiness |
| Runtime-only deployment for operator/admin/runtime contracts | `supported` | runtime substrate focused on health, readiness, metadata, and execution contract surfaces | primary DB, schema readiness, required runtime boot dependencies | stable runtime metadata and operational truth | limited degraded liveness for diagnostics | product-level platform claims beyond runtime contract |
| Extension-heavy trusted-internal runtime | `supported with constraints` | trusted-internal runtime with explicit extension trust assumptions | primary DB, schema readiness, required execution deps, capability enforcement, trust-model adherence | tenant/capability enforcement in runtime paths, trusted-internal extension posture | constrained degraded continuation only if security posture remains truthful | arbitrary untrusted third-party in-process extension claims |
| Reduced-dependency development/test profile | `supported with constraints` | non-production runtime for development and testing with narrower guarantees | enough dependencies to satisfy explicit dev/test mode | developer feedback, diagnostics, limited execution posture | fail-open behavior only where explicitly documented as non-production | production-equivalent security or quota guarantees |
| Third-party plugin-host / marketplace-style runtime | `unsupported` | none beyond experimental investigation | stronger isolation would be required than current runtime posture supports | none should be claimed as supported today | none | hardened arbitrary third-party extension hosting |
| Hostile multitenant compute substrate | `unsupported` | none beyond runtime-policy-level tenant handling | stronger isolation and trust boundaries than currently documented | none should be claimed as supported today | none | strong hostile-code multitenancy claims |

---

## Profile Details

## 1. Local Trusted-Internal Single-Instance

### Support Level
`supported`

### What This Profile May Claim
- trusted-internal runtime deployment
- local execution substrate
- local wait/resume behavior
- truthful `/health`, `/ready`, and `/api/version`

### Required Conditions
- primary database available
- runtime-owned schema ready
- scheduler available for resumable/scheduled semantics
- required syscalls registered

### Allowed Constraints
- no need to promise cross-instance propagation
- no claim of hardened third-party extension isolation

### Release Standard
Breakage here is a real runtime release issue.

---

## 2. Local-Only Without Redis/Event Bus

### Support Level
`supported with constraints`

### What This Profile May Claim
- trusted-internal single-instance runtime
- local-only execution and local-only resume semantics

### What It Must Not Claim
- distributed wakeup or cross-instance wait/resume
- multi-instance-ready semantics

### Required Conditions
- core local execution dependencies remain healthy

### Allowed Fallback
- local-only degraded continuation if readiness remains truthful for that local profile

---

## 3. Multi-Instance Trusted-Internal Distributed API Runtime

### Support Level
`supported with constraints`

### What This Profile May Claim
- distributed runtime behavior only when required distributed dependencies are healthy
- cross-instance resume behavior only when event propagation path is actually available

### Required Conditions
- primary DB
- schema readiness
- scheduler lifecycle correctness
- Redis/event bus availability
- worker presence where profile semantics require it
- required syscalls and restore/rehydration correctness

### What Invalidates Full Support
- event bus unavailable while still claiming distributed resume
- worker-dependent distributed assumptions with no healthy workers
- rehydration/recovery path not trustworthy

### Allowed Fallback
- diagnostics and degraded liveness only
- not full ready state when distributed guarantees are broken

---

## 4. Runtime-Only Deployment

### Support Level
`supported`

### What This Profile May Claim
- runtime substrate deployment
- operator/runtime metadata surfaces
- health/readiness/version truth
- execution contract surfaces that are explicitly runtime-owned

### What It Must Not Claim
- full broader platform ownership
- product-layer guarantees outside runtime contract

### Required Conditions
- enough runtime boot and persistence correctness to defend runtime-owned surfaces

---

## 5. Extension-Heavy Trusted-Internal Runtime

### Support Level
`supported with constraints`

### What This Profile May Claim
- trusted-internal extension execution under explicit trust assumptions
- runtime tenant/capability enforcement in supported paths

### What It Must Not Claim
- safe arbitrary third-party in-process extension hosting
- hardened zero-trust plugin model

### Required Conditions
- trust-model assumptions remain true
- capability and tenant enforcement remain intact
- runtime does not overstate extension isolation

---

## 6. Reduced-Dependency Development/Test Profile

### Support Level
`supported with constraints`

### What This Profile May Claim
- development and testing usefulness
- controlled non-production fallback where explicitly documented

### What It Must Not Claim
- production-equivalent readiness guarantees
- production-equivalent enforcement where fail-open behavior is explicitly allowed in dev/test

### Required Conditions
- docs and runtime behavior must make non-production posture obvious

---

## 7. Unsupported Profiles

These should remain unsupported until the runtime can defend stronger guarantees.

### Third-Party Plugin Host
Why unsupported:
- current trust posture is trusted-internal, not hardened arbitrary third-party code hosting
- stronger isolation would be required

### Hostile Multitenant Compute Runtime
Why unsupported:
- tenant enforcement is real, but not equivalent to strong hostile-code isolation
- current security posture does not justify this claim

---

## Profile Decision Rules

When defining or revising a profile, ask:

1. What exact runtime claim is this profile making?
2. Which dependencies are required for that claim to be true?
3. Which guarantees are execution-critical?
4. What degraded fallback is still honest?
5. What must explicitly be forbidden or unsupported?
6. Would an operator, SDK, or UI consumer misunderstand the profile if it degraded?

If the answer is unclear, the profile is not mature enough to be claimed strongly.

---

## Profile Smells

These are warning signs that profile discipline is weak.

- a profile is described by infrastructure shape but not by guarantees
- the runtime process starts, so the profile is treated as supported by default
- local-only fallback is silently treated as distributed support
- development/test exceptions leak into production posture language
- unsupported extension or multitenancy claims are implied by convenience
- SDK/UI cannot tell which profile assumptions are safe to consume

---

## Minimum Profiles To Keep Defensible

If the runtime cannot sharpen every profile immediately, it should keep these three especially clear:

1. local trusted-internal single-instance
2. multi-instance distributed trusted-internal
3. reduced-dependency dev/test

Those are the profiles most likely to be conflated.

---

## What Maturity Looks Like

Profile maturity is reached when:

- each profile has a narrow claim
- required dependencies are explicit
- degraded fallback is honest
- readiness is profile-aware
- unsupported modes are stated plainly

The runtime should increasingly support fewer, clearer profiles rather than many vaguely supported ones.

---

## Relationship To Other Docs

This document should align with:

- `RUNTIME_BOUNDARY.md`
- `SECURITY_POSTURE.md`
- `DEGRADED_MODE_MATRIX.md`
- `DEPENDENCY_CRITICALITY_MATRIX.md`
- `CROSS_REPO_COMPATIBILITY.md`
- `RUNTIME_STABILITY_INDEX.md`

These docs answer different questions:

- `RUNTIME_BOUNDARY.md`: what the runtime owns
- `SECURITY_POSTURE.md`: what security claims are actually true
- `DEGRADED_MODE_MATRIX.md`: what remains safe under partial failure
- `DEPENDENCY_CRITICALITY_MATRIX.md`: which dependencies matter most by profile
- `PROFILE_SUPPORT_MATRIX.md`: which runtime profiles are actually supported
