# Runtime Test Strategy

> Authored by Codex during non coding session. Needs review before repo commit and push.

This document defines the testing strategy that should support `aindy-runtime` as a trusted-internal runtime platform.

Its purpose is to connect runtime maturity claims to concrete test classes.

This is not a test inventory. It is a testing model.

---

## Canonical Principle

A runtime is not well tested because it has many tests.

A runtime is well tested when the tests protect:

- execution truth
- startup truth
- readiness truth
- degraded-mode truth
- tenant/capability enforcement
- shipped artifact behavior
- downstream contract stability

The goal is not test volume.
The goal is protection of runtime guarantees.

---

## Testing Priorities

Order of importance:

1. execution-critical correctness
2. startup and recovery correctness
3. security and isolation enforcement
4. readiness and degraded-mode truthfulness
5. stable runtime-surface compatibility
6. artifact and release verification
7. broader regression coverage and tooling confidence

---

## Test Classes

### 1. Invariant Tests
These prove the runtime behaviors documented in `EXECUTION_INVARIANTS.md`.

Priority areas:
- startup sequencing
- scheduler lifecycle
- wait/resume correctness
- syscall readiness and dispatch
- tenant/capability continuity
- event delivery and recovery
- readiness/degraded-mode behavior

Purpose:
- prevent semantic drift in execution-critical behavior

### 2. Contract Tests
These verify stable and conditionally stable runtime surfaces.

Priority surfaces:
- `/api/version`
- `/health`
- `/ready`
- documented runtime metadata and status semantics
- narrow stable syscall surfaces where applicable

Purpose:
- protect downstream repos and operators from accidental contract breakage

### 3. Integration Tests
These validate the runtime against real dependency interactions.

Examples:
- database-backed startup and execution
- Redis-backed event bus behavior
- rehydration and resume behavior
- deployment-profile-sensitive readiness behavior

Purpose:
- catch failures that unit tests cannot model well

### 4. Degraded-Mode Tests
These validate truthfulness under partial failure.

Examples:
- restore pending
- registry restore incomplete
- event bus local-only fallback
- required dependency missing
- scheduler or rehydration failure
- readiness blocked under unsafe degraded conditions

Purpose:
- ensure the runtime does not overstate safety when partially broken

### 5. Security and Isolation Tests
These validate the trusted-internal security posture and its limits.

Examples:
- tenant enforcement checks
- capability enforcement checks
- schema-before-side-effect behavior
- extension-trust-path restrictions
- fail-open vs fail-closed behavior where explicitly documented

Purpose:
- keep security claims aligned with reality

### 6. Artifact Tests
These validate the shipped runtime artifact rather than just the repo source.

Examples:
- clean install of built artifact
- runtime entrypoint behavior from installed package
- smoke verification of stable runtime surfaces from installed artifact

Purpose:
- prevent source-tree-only confidence

### 7. Cross-Repo Compatibility Tests
These validate that runtime releases do not accidentally break SDK/UI consumers.

Examples:
- SDK compatibility with stable runtime endpoints
- UI compatibility with health/readiness/version semantics
- compatibility checks for documented downstream-consumed fields

Purpose:
- keep repo boundaries honest and supportable

---

## Recommended Test Pyramid For This Runtime

This runtime should not use a naive app-style pyramid.

A better mix is:

- a strong base of focused invariant and contract tests
- a meaningful layer of integration and degraded-mode tests
- a smaller but mandatory layer of artifact and cross-repo checks

Reason:
- the biggest failures here are semantic, operational, and contractual
- not just local unit regressions

---

## Required Mapping From Docs To Tests

Each of these documents should drive concrete test work:

### `EXECUTION_INVARIANTS.md`
Should map to:
- invariant tests
- startup/recovery tests
- scheduler/syscall/regression tests

### `DEGRADED_MODE_MATRIX.md`
Should map to:
- degraded-mode tests
- readiness truthfulness tests
- dependency-loss integration tests

### `SECURITY_POSTURE.md`
Should map to:
- security enforcement tests
- trusted-internal posture verification
- extension trust and tenant/capability tests

### `CROSS_REPO_COMPATIBILITY.md`
Should map to:
- downstream compatibility checks
- stable-surface contract tests

### `RUNTIME_STABILITY_INDEX.md`
Should map to:
- stronger testing for `stable` and `conditionally stable` surfaces
- lighter expectations for `experimental`
- no downstream compatibility reliance on `internal only`

---

## Test Coverage Guidance

Coverage percentage is useful, but insufficient.

### Coverage Should Be Interpreted By Surface Type
- `stable` surfaces should have strong contract coverage
- execution-critical internals should have strong invariant coverage
- degraded-mode behavior should have explicit scenario coverage
- internal-only surfaces do not need the same public-contract test burden, but still need correctness tests where blast radius is high

### Warning
A low overall coverage threshold may be acceptable temporarily, but it should not be mistaken for runtime-grade assurance.

The runtime should increasingly ask:

- are the dangerous paths tested?
- are the public surfaces defended?
- are degraded and recovery states exercised?

---

## Risk-Based Test Expectations

### Low-Risk Change
Examples:
- docs only
- logging or observability only
- internal cleanup with no public/runtime-semantic effect

Expected:
- targeted unit/regression validation as appropriate

### Medium-Risk Change
Examples:
- readiness logic refinement
- deployment-profile behavior updates
- route payload changes on stable surfaces

Expected:
- contract tests
- targeted integration tests
- degraded-mode review if relevant

### High-Risk Change
Examples:
- scheduler behavior
- wait/resume changes
- rehydration changes
- syscall dispatcher behavior
- startup ordering changes
- tenant/capability enforcement changes

Expected:
- invariant tests
- integration tests
- degraded-mode tests if relevant
- artifact verification
- downstream compatibility review if relevant

---

## Minimum Test Set To Strengthen First

If the runtime cannot improve everything at once, the highest-leverage additions are:

1. startup sequencing and readiness truth tests
2. scheduler wait/resume and rehydration tests
3. syscall not-ready and enforcement tests
4. degraded-mode profile tests for Redis/event bus and schema readiness
5. installed-artifact runtime surface smoke tests
6. at least one SDK and one UI compatibility smoke path

These would raise confidence faster than broad low-value test expansion.

---

## Test Smells

These are warning signs that the test strategy is weak.

- many tests exist, but invariants are undocumented
- health responds, but readiness truth is not stressed under failure
- source tree passes, but installed artifact is unverified
- SDK/UI rely on runtime behavior that no compatibility test exercises
- degraded conditions are logged but not tested
- coverage increases without improving protection of risky paths

---

## Release-Critical Test Checklist

Before shipping a risky runtime change:

- [ ] Which invariants were affected?
- [ ] Which stable surfaces were affected?
- [ ] Which degraded conditions were affected?
- [ ] Are installed-artifact checks included?
- [ ] Are cross-repo assumptions still true?
- [ ] Are security posture claims still defended by tests?

---

## What Maturity Looks Like

The runtime test strategy is mature when:

- critical invariants are directly tested
- degraded and not-ready states are intentionally exercised
- artifact validation is routine
- downstream compatibility is checked, not assumed
- coverage serves runtime truth rather than vanity metrics

The goal is for tests to protect the runtime’s claims, not just its lines of code.

---

## Relationship To Other Docs

This document should align with:

- `EXECUTION_INVARIANTS.md`
- `DEGRADED_MODE_MATRIX.md`
- `SECURITY_POSTURE.md`
- `CROSS_REPO_COMPATIBILITY.md`
- `RUNTIME_STABILITY_INDEX.md`
- `RELEASE_GATES.md`
