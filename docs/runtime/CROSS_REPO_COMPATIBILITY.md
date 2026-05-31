---
title: "Cross-Repo Compatibility"
last_verified: "2026-05-31"
api_version: "1.0"
status: current
owner: "platform-team"
---
﻿# Cross-Repo Compatibility

> Authored by Codex during non coding session. Needs review before repo commit and push.


This document defines compatibility expectations across:

- `aindy-runtime`
- `aindy-sdk`
- `aindy-ui-kit`

Its purpose is to prevent cross-repo drift, make release expectations explicit, and distinguish stable contracts from implementation coincidence.

---

## Canonical Compatibility Principle

`aindy-runtime` is the source of truth for runtime behavior and runtime-facing contracts.

`aindy-sdk` and `aindy-ui-kit` should depend on:

- documented runtime contracts
- versioned runtime surfaces
- explicitly supported metadata, health, readiness, and execution APIs

They should **not** depend on:

- undocumented runtime internals
- startup side effects that are not contractually defined
- implementation details that happen to exist in the current repo layout

---

## Compatibility Goals

Cross-repo compatibility should make the platform:

- easier to release safely
- less dependent on tribal knowledge
- less sensitive to runtime refactors
- clearer about what downstream consumers may rely on

The goal is not perfect independence.
The goal is explicit dependency.

---

## Repo Roles

### `aindy-runtime`
Owns:

- runtime behavior
- execution semantics
- scheduler/wait/resume behavior
- syscall and capability contracts
- readiness/health/version truth
- deployment and degraded-mode truth

### `aindy-sdk`
Owns:

- consumer-facing client abstractions
- typed wrappers around stable runtime contracts
- developer ergonomics for invoking runtime capabilities
- compatibility smoothing where allowed by runtime contract

### `aindy-ui-kit`
Owns:

- presentation-layer components
- operator/admin/runtime-facing visual patterns
- UI states built on stable runtime metadata and API responses
- visual composition, not backend truth

---

## Compatibility Layers

Think of compatibility in layers.

### Layer 1: Contract Compatibility
This is the highest-priority layer.

Downstream repos may rely on:

- documented stable API routes
- documented runtime metadata shapes
- documented readiness and health semantics
- documented version and compatibility metadata
- documented stable execution-facing contracts

If this layer breaks, downstream breakage is runtime responsibility unless the contract was marked unstable.

### Layer 2: Behavioral Compatibility
This covers behavior that may not be fully schema-visible but is still part of platform expectations.

Examples:

- readiness returning not-ready during restore-pending conditions
- degraded-mode status behavior
- stable interpretation of health tiers
- stable semantics of runtime version reporting

This layer should be documented when downstream repos rely on it.

### Layer 3: Implementation Compatibility
This is the weakest layer and should not be relied upon unless explicitly elevated.

Examples:

- internal module names
- import paths not declared public
- startup ordering artifacts that are not part of public contract
- incidental JSON fields not documented as stable

Downstream repos should assume this layer can change.

---

## Stable Cross-Repo Inputs From `aindy-runtime`

The runtime should provide a small set of stable surfaces that SDK and UI are allowed to trust.

### 1. Runtime Identity and Version Surfaces
These should remain stable or explicitly versioned:

- `/api/version`
- runtime package version metadata
- runtime capability/compatibility metadata that is explicitly documented

### 2. Runtime Health and Readiness Surfaces
These may be relied on when documented:

- `/health`
- `/ready`
- documented degraded/not-ready semantics
- documented health/status fields intended for downstream use

### 3. Stable Execution-Facing APIs
These may be wrapped by SDKs or consumed by UI/admin flows only if declared stable.

### 4. Documented Runtime Error/Status Conventions
If SDK or UI depends on specific error/status semantics, those semantics must be documented rather than inferred.

---

## Unstable or Restricted Inputs

SDK and UI should not rely on these unless explicitly promoted to stable contract.

- internal Python modules in `aindy-runtime`
- undocumented route payload fields
- incidental route availability outside documented public surfaces
- implicit startup ordering side effects
- undocumented runtime condition codes
- registry/internal loader behavior not declared public
- implementation details of kernel, scheduler, or dispatcher internals

---

## Compatibility Promises By Repo

### What `aindy-runtime` Should Promise
- Stable documented runtime surfaces will not change casually.
- Unstable/experimental surfaces will be labeled as such.
- Runtime version and readiness semantics will be documented when downstream repos are expected to consume them.
- Breaking changes to stable runtime contracts should be called out explicitly.

### What `aindy-sdk` Should Promise
- It consumes stable runtime contracts, not incidental internals.
- It can absorb some runtime evolution without exposing that churn directly to consumers where the contract allows.
- It will not silently redefine runtime guarantees.

### What `aindy-ui-kit` Should Promise
- It consumes runtime-facing API and metadata contracts, not runtime internals.
- It will treat backend status as an input, not an implementation to duplicate.
- It will not rely on undocumented runtime behavior to render critical states.

---

## Compatibility Matrix Guidance

Use this model when reasoning about versions.

### Compatible
A release is compatible when:

- SDK and UI consume only documented stable runtime surfaces
- runtime responses and semantics match the documented contract
- no stable field, route, or behavior relied on by SDK/UI changed incompatibly

### Soft-Incompatible
A release is soft-incompatible when:

- downstream repos depended on unstable or undocumented behavior
- runtime changed internal structure or incidental output
- the break is real, but the contract was not actually guaranteed

This still matters operationally, but it is a signal to improve boundary discipline.

### Hard-Incompatible
A release is hard-incompatible when:

- a documented stable runtime contract changed incompatibly
- health/readiness/version semantics changed in a way that breaks supported SDK/UI use
- runtime claims stayed the same while downstream-compatible behavior changed underneath them

---

## Required Cross-Repo Compatibility Checks

Before release, the platform should increasingly verify:

### Runtime -> SDK
- SDK can authenticate and call documented runtime surfaces
- SDK wrappers still match runtime response shapes and status behavior
- SDK does not require undocumented runtime fields or timing assumptions

### Runtime -> UI
- UI-facing status components still map correctly to health/readiness/version responses
- degraded and not-ready states are still represented correctly
- runtime metadata consumed by operator/admin views remains compatible

### Runtime -> Both
- version and compatibility metadata remain coherent
- stable route semantics remain unchanged unless explicitly versioned
- stable degraded-mode meaning remains consistent

---

## Cross-Repo Boundary Smells

These are warning signs that compatibility is being maintained by accident rather than design.

- SDK imports or mirrors runtime internals instead of consuming contract surfaces.
- UI behavior depends on undocumented status payload details.
- Runtime refactors routinely break SDK/UI even when no stable contract changed.
- Release coordination depends on maintainers remembering hidden coupling.
- New runtime fields are treated as immediately stable without documentation.
- UI semantics and runtime semantics diverge for the same health or readiness condition.

---

## Compatibility Review Checklist

Use this during design and release review.

- [ ] Is this runtime surface documented as stable, experimental, or internal?
- [ ] Will SDK rely on this as a contract or merely wrap it temporarily?
- [ ] Will UI rely on this for operator-visible state?
- [ ] Is this dependency based on documented behavior or current implementation?
- [ ] If runtime internals change, should SDK/UI still work unchanged?
- [ ] If the answer is no, is that because the contract changed or because the repos are too tightly coupled?
- [ ] Are release notes explicit when stable cross-repo behavior changes?

---

## Minimum Compatibility Contract To Establish First

If compatibility work needs to start small, define and hold these first:

1. `/api/version` shape and meaning
2. `/health` shape and public status mapping
3. `/ready` status behavior and basic reasons
4. stable runtime error/status conventions used by SDK/UI
5. the list of runtime surfaces SDK and UI are officially allowed to consume

This is a better starting point than trying to freeze every route.

---

## What Maturity Looks Like

Cross-repo compatibility is mature when:

- runtime can refactor internally without routinely breaking SDK/UI
- SDK and UI depend on contract surfaces, not repo coincidence
- release coordination is predictable
- stable runtime contracts are few, clear, and well defended
- version compatibility is testable rather than assumed

The main goal is not to eliminate coupling.
The goal is to make coupling explicit, intentional, and supportable.

---

## Relationship To Other Docs

This document should align with:

- `RUNTIME_BOUNDARY.md`
- `EXECUTION_INVARIANTS.md`
- `SECURITY_POSTURE.md`
- `PUBLIC_RUNTIME_SURFACES.md`
- `REPO_COMPATIBILITY_POLICY.md`

These docs answer different questions:

- `RUNTIME_BOUNDARY.md`: what the runtime should own
- `EXECUTION_INVARIANTS.md`: what runtime behavior must not drift
- `SECURITY_POSTURE.md`: what security claims are actually true
- `CROSS_REPO_COMPATIBILITY.md`: what downstream repos may safely depend on

