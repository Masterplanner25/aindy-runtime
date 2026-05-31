---
title: "Runtime Release Gates"
last_verified: "2026-05-31"
api_version: "1.0"
status: current
owner: "platform-team"
---
﻿# Runtime Release Gates

> Authored by Codex during non coding session. Needs review before repo commit and push.

This document defines the minimum release gates that `aindy-runtime` should satisfy before a release is considered safe to ship.

Its purpose is to turn runtime maturity expectations into explicit ship criteria.

This is a release-discipline document, not a changelog template.

---

## Canonical Principle

A runtime release is not ready because the code merged.

A runtime release is ready when:

- the claimed runtime surfaces still behave as documented
- critical execution guarantees remain intact
- degraded-mode and readiness semantics still tell the truth
- downstream repos are not broken by avoidable contract drift
- the shipped artifact behaves like the reviewed source

---

## Gate Levels

### Gate 1: Must Pass For Any Release
These are non-negotiable.

- [ ] Build succeeds for the intended release artifact.
- [ ] Installable runtime artifact is validated in a clean environment.
- [ ] `/api/version` behaves as expected.
- [ ] `/health` behaves as expected.
- [ ] `/ready` behaves as expected for the intended deployment profile.
- [ ] No known startup-fatal condition is being ignored.
- [ ] Runtime-owned schema compatibility expectations are satisfied or explicitly release-blocked.

### Gate 2: Must Pass For Execution-Affecting Releases
Required when the release touches scheduler, recovery, syscalls, startup, isolation, or readiness semantics.

- [ ] Relevant execution invariants were reviewed.
- [ ] Critical-path regression tests passed.
- [ ] Startup and rehydration behavior was verified.
- [ ] Required syscall registration still works.
- [ ] Degraded-mode and readiness semantics were reviewed for truthfulness.
- [ ] Security posture claims still match actual behavior.

### Gate 3: Must Pass For Cross-Repo Safe Releases
Required when SDK/UI consumers may be affected.

- [ ] Stable runtime surfaces used by SDK remain compatible.
- [ ] Stable runtime health/readiness/version semantics used by UI remain compatible.
- [ ] Cross-repo compatibility notes were reviewed.
- [ ] Any downstream-impacting contract changes are called out explicitly.

---

## Release Gate Checklist

### 1. Artifact Integrity
- [ ] Wheel/sdist or equivalent runtime artifact builds successfully.
- [ ] Installed artifact is tested outside the source tree.
- [ ] Runtime entrypoint still works from installed artifact.
- [ ] Version metadata is consistent across package, docs, and release notes.

### 2. Public Runtime Surface Verification
- [ ] `/api/version` contract reviewed and verified.
- [ ] `/health` contract reviewed and verified.
- [ ] `/ready` contract reviewed and verified.
- [ ] Stable runtime metadata payloads used downstream are still compatible.

### 3. Startup and Boot Verification
- [ ] Runtime boot completes for intended deployment profile.
- [ ] Required dependencies are enforced correctly.
- [ ] Required syscalls are present after bootstrap.
- [ ] Restore/rehydration path was verified if applicable.
- [ ] Startup degradation is explicit, not silent.

### 4. Execution-Critical Verification
- [ ] Scheduler lifecycle behavior reviewed if touched.
- [ ] Wait/resume behavior reviewed if touched.
- [ ] Recovery/rehydration behavior reviewed if touched.
- [ ] Syscall capability/tenant/schema enforcement reviewed if touched.
- [ ] Effect/idempotency behavior reviewed if touched.

### 5. Degraded-Mode Truthfulness
- [ ] New degraded conditions are classified correctly.
- [ ] Existing degraded conditions still map to truthful health/readiness behavior.
- [ ] Unsafe degraded states are not reported as ready.
- [ ] Local-only fallback is not mistaken for distributed readiness.

### 6. Security Posture Verification
- [ ] Trusted-internal posture still accurately describes the release.
- [ ] No new feature implies stronger extension isolation than actually exists.
- [ ] Tenant/capability enforcement remains intact on touched paths.
- [ ] Release notes do not overstate security claims.

### 7. Cross-Repo Compatibility Verification
- [ ] `aindy-sdk` assumptions about stable runtime surfaces still hold.
- [ ] `aindy-ui-kit` assumptions about health/readiness/version semantics still hold.
- [ ] No undocumented runtime internals are newly required downstream.
- [ ] Breaking changes to stable surfaces are explicitly treated as compatibility events.

---

## Invariant-Triggered Release Review

If a release touches any of these areas, `EXECUTION_INVARIANTS.md` must be reviewed before ship:

- startup sequencing
- scheduler lifecycle
- wait/resume logic
- syscall readiness or dispatch
- tenant/capability enforcement
- event delivery and recovery
- readiness and degraded-mode behavior

Minimum review questions:

- [ ] Which invariants were touched?
- [ ] Were any guarantees weakened?
- [ ] Were tests updated to prove the intended behavior?
- [ ] Does the release note need to mention a contract change?

---

## Risk-Based Release Classes

### Low-Risk Release
Examples:
- internal cleanup with no public contract effect
- doc-only updates
- observability improvements with no semantic changes

Minimum expectation:
- Gate 1

### Medium-Risk Release
Examples:
- route payload refinements on stable surfaces
- deployment-profile behavior tightening
- readiness/health logic changes without kernel changes

Minimum expectation:
- Gate 1
- targeted Gate 2 review
- Gate 3 if downstream consumers are affected

### High-Risk Release
Examples:
- scheduler changes
- startup sequencing changes
- rehydration/recovery changes
- syscall dispatch or capability enforcement changes
- degraded-mode classification changes

Minimum expectation:
- Gate 1
- full Gate 2
- Gate 3 where relevant
- explicit reviewer signoff

---

## Release Blockers

A release should be blocked when any of these are true:

- [ ] installed artifact behavior is unverified
- [ ] readiness lies about safe execution
- [ ] required syscalls are missing
- [ ] recovery or rehydration semantics are known-broken
- [ ] security posture claims exceed actual implementation posture
- [ ] stable downstream contract changed without explicit treatment
- [ ] a degraded or unsafe runtime state is being shipped as normal

---

## Recommended Evidence To Attach To A Release

- [ ] artifact install log or clean-install verification
- [ ] runtime surface smoke results
- [ ] critical-path test results
- [ ] readiness/degraded-mode validation notes
- [ ] downstream compatibility notes for SDK/UI if relevant
- [ ] explicit signoff on any changed invariants

---

## Reviewer Checklist

- [ ] Does this release preserve the stable runtime surfaces it claims to preserve?
- [ ] Does the runtime still tell the truth about health, readiness, and degradation?
- [ ] Are execution-critical behaviors verified strongly enough for the risk level?
- [ ] Are SDK/UI dependencies still safe?
- [ ] Would an operator be surprised by runtime behavior after this release?

---

## Relationship To Other Docs

This document should align with:

- `EXECUTION_INVARIANTS.md`
- `SECURITY_POSTURE.md`
- `CROSS_REPO_COMPATIBILITY.md`
- `DEGRADED_MODE_MATRIX.md`
- `RUNTIME_STABILITY_INDEX.md`
- `REPO_COMPATIBILITY_POLICY.md`
