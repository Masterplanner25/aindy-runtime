# Security Posture

> Authored by Codex during non coding session. Needs review before repo commit and push.


This document defines the actual security posture of `aindy-runtime` as it exists today and the security claims the repo is justified in making.

It is intentionally blunt.

Its purpose is to prevent overstated platform claims, align runtime behavior with deployment expectations, and make trust assumptions explicit for maintainers and downstream repos.

---

## Canonical Security Statement

`aindy-runtime` is a **trusted-internal runtime platform**.

It is designed to support:

- trusted internal deployments
- operator-managed infrastructure
- runtime-owned execution with documented tenant and capability controls
- constrained extension behavior under explicit trust assumptions

It is **not** currently a fully hardened:

- third-party in-process extension platform
- hostile multitenant compute boundary
- zero-trust plugin host
- arbitrary untrusted-code execution substrate

Any claim stronger than that should be treated as false unless a narrower context is documented.

---

## What This Means In Practice

### Supported Security Posture
The runtime is suitable for environments where:

- operators control deployment infrastructure
- runtime dependencies are trusted and managed
- extension code is either first-party or explicitly trusted
- tenant separation is important, but not equivalent to hostile-code isolation
- failure and degraded-mode visibility matter operationally

### Unsupported Security Posture
The runtime should not currently be presented as safe for:

- arbitrary third-party in-process code execution
- marketplace-style plugin execution without stronger isolation
- strong hostile multitenancy based only on in-process enforcement
- assuming that extension trust and tenant isolation are equivalent controls

---

## Security Model Layers

The runtime’s current security posture is a combination of several layers.

### 1. Trust Boundary Layer
This is the most important distinction.

Current effective trust model:

- infrastructure is trusted
- runtime package and deployment operators are trusted
- first-party runtime code is trusted
- extension code may be trusted or constrained, but is not universally safe by default
- tenants are isolated at runtime-policy level, not automatically at hostile-code boundary level

This means:

- tenant isolation is a runtime contract
- code isolation is not universally equivalent to tenant isolation

### 2. Capability Enforcement Layer
The runtime uses capability-oriented enforcement for syscall and execution behavior.

This is a strong part of the model, but it should be described accurately:

- capability checks are meaningful
- capability checks protect runtime paths
- capability checks are not a substitute for full hostile-code isolation

### 3. Tenant Enforcement Layer
Tenant context and tenant validation are part of the runtime’s execution contract.

This supports:

- tenant-scoped execution
- tenant-scoped dispatch
- tenant-aware resume and orchestration behavior

But tenant enforcement should not be oversold as proof that less-trusted in-process code is safely isolated from the host.

### 4. Deployment Profile Layer
Security posture depends materially on deployment profile.

Examples:

- local-only degraded event behavior has different risk and guarantee characteristics than Redis-backed cross-instance operation
- missing dependencies may preserve liveness while reducing safe execution guarantees
- trusted internal single-organization deployments are stronger fits than broader extension-hosting scenarios

---

## Security Claims The Repo Can Defend

These are claims the runtime is reasonably justified in making.

### Defensible Claims
- The runtime has explicit tenant and capability enforcement in core execution paths.
- The runtime documents stable vs experimental public surfaces.
- The runtime exposes health/readiness/degraded behavior intended to reflect execution safety.
- The runtime has a documented trust model rather than an implied one.
- The runtime is suitable for trusted internal deployments.

### Claims That Need Narrow Wording
- “Secure extension platform”
- “Isolated plugin runtime”
- “Multitenant-safe runtime”
- “Sandboxed runtime OS”

These may only be used with qualifiers, if at all.

### Claims The Repo Should Avoid
- “Safe for arbitrary third-party extensions”
- “Safe by default for hostile in-process code”
- “Strong zero-trust plugin isolation”
- “Equivalent to a hardened runtime sandbox”

---

## Security Responsibilities Owned By `aindy-runtime`

The runtime should own the security responsibilities that directly affect execution truth and runtime guarantees.

These include:

- tenant enforcement in runtime execution paths
- capability enforcement in syscall and execution paths
- readiness/degraded signaling for unsafe runtime conditions
- extension trust-model enforcement where runtime execution is involved
- deployment-profile-aware security semantics
- persistence and recovery behavior that affects safe execution claims

---

## Security Responsibilities Not Fully Solved By `aindy-runtime`

These areas should be treated as partially solved, out of scope, or requiring stronger boundaries than currently exist.

### 1. Hostile In-Process Extension Isolation
This is the largest current limitation.

If extension code runs in-process, tenant and capability checks alone do not prove strong code isolation.

### 2. Marketplace-Grade Third-Party Execution
A generalized external extension ecosystem requires stronger default isolation, reviewability, and abuse resistance than the runtime currently claims.

### 3. Strong Zero-Trust Multitenancy
Runtime-level tenant separation is important, but it is not enough to claim strong hostile-tenant execution isolation without stronger process or environment boundaries.

### 4. Security By Documentation Alone
A documented trust model is necessary, but not sufficient. The repo must continue to align implementation, tests, and release claims with the posture described here.

---

## Deployment Posture Levels

Use these labels when discussing runtime security.

### Level 1: Trusted Internal
Use when:

- infrastructure is controlled by the operating team
- extension code is first-party or explicitly trusted
- tenants are subject to runtime enforcement but not treated as hostile-code principals

This is the runtime’s current primary posture.

### Level 2: Constrained Internal Extension Platform
Use only when:

- extension capabilities are explicitly bounded
- deployment profile restrictions are enforced
- unsafe extension modes are disabled or operationally prohibited
- regression coverage exists for key trust boundaries

This posture may be partially true in some deployments, but should be claimed carefully.

### Level 3: Hardened External Platform
This would require stronger guarantees than the runtime currently documents.

Do not claim this level without:

- stronger isolation defaults
- stronger third-party execution boundaries
- deeper security verification
- explicit release-grade support for hostile extension scenarios

---

## Security Invariants The Runtime Should Preserve

The following expectations should remain true across releases.

- Tenant context must not be silently dropped on execution or resume paths.
- Capability checks must happen before unsafe side effects.
- Readiness must not claim safe execution when unsafe degraded conditions exist.
- Extension trust assumptions must be explicit, not implied by convenience paths.
- Degraded security posture must be visible to operators.
- Security-sensitive behavior must not vary silently by deployment profile.

These should align with `EXECUTION_INVARIANTS.md` and future runtime security test planning.

---

## Current Security Risks To Watch

These are the major maturity risks implied by the current posture.

### 1. Scope Creep Becoming Security Creep
As the runtime grows, more non-runtime concerns may accidentally gain security significance without receiving runtime-grade scrutiny.

### 2. Tenant Isolation Being Over-Interpreted
There is a real risk of treating tenant enforcement as if it proves hard code isolation. It does not.

### 3. Extension Convenience Outrunning Trust Controls
If extension ergonomics improve faster than isolation controls, the repo may begin making de facto claims it cannot safely support.

### 4. Degraded Modes Being Misunderstood
A runtime that stays live in degraded conditions is useful operationally, but those conditions must remain visible and must not be mistaken for full readiness.

---

## Language For Maintainers And Release Notes

Preferred phrasing:

- “trusted internal runtime deployment”
- “tenant and capability enforcement in runtime execution paths”
- “not a hardened third-party in-process extension platform”
- “deployment-profile-dependent security guarantees”
- “degraded but observable runtime state”

Avoid phrasing like:

- “fully sandboxed runtime”
- “secure plugin OS”
- “multitenant-safe by default”
- “safe for arbitrary third-party execution”

---

## Security Review Checklist

Use this when reviewing new runtime features or release claims.

- [ ] Does this change strengthen or weaken the trusted-internal posture?
- [ ] Does it introduce new extension trust assumptions?
- [ ] Does it blur tenant isolation and code isolation claims?
- [ ] Does degraded mode remain explicit and operator-visible?
- [ ] Would this feature be safe only for trusted code, or for less-trusted code too?
- [ ] Are release notes describing the security posture accurately?
- [ ] Does the runtime now need a stronger isolation boundary than it currently has?

---

## Relationship To Other Docs

This document should align with:

- `RUNTIME_BOUNDARY.md`
- `EXECUTION_INVARIANTS.md`
- `PUBLIC_RUNTIME_SURFACES.md`
- `EXTENSION_TRUST_MODEL.md`
- `SECURITY_POLICY.md`
- future `CROSS_REPO_COMPATIBILITY.md`

These docs answer different questions:

- `RUNTIME_BOUNDARY.md`: what the runtime owns
- `EXECUTION_INVARIANTS.md`: what runtime behavior must not drift
- `SECURITY_POSTURE.md`: what security claims are actually true
- `CROSS_REPO_COMPATIBILITY.md`: what downstream repos may rely on

