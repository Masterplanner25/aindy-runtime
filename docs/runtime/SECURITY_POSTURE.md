---
title: "Security Posture"
last_verified: "2026-08-05"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Security Posture

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

**Added 2026-08-05.** Four controls shipped after this document was first written and are
absent from the original text. Each is defensible because it is *tested*, not merely present:

- **Container-backed extension sandbox, escape-tested.** `ContainerizedOciSandboxRunner`
  carries a `container-grade-sandbox` tier certified on **native Linux**, verified by a
  17-vector escape suite (`pytest -m sandbox_escape`) covering filesystem escape, network
  escape, pid limits, privilege escalation, host env leak and path boundaries. Results are
  recorded per release in an append-only log, `SANDBOX_ESCAPE_AUDIT.md`, and the suite runs
  as a release gate on every version tag. This is a **separate execution path** from
  in-process extensions — see the sharpened distinction under "Not Fully Solved" below.
- **Signed extension bundles.** Ed25519 detached signatures with a trust registry and a
  CycloneDX-lite SBOM (`platform_layer/extension_signing.py`). The production profile can
  refuse unsigned or untrusted bundles; enforcement for external third-party artifacts is
  gated by `AINDY_REQUIRE_SIGNED_PLUGINS`.
- **Brokered secrets.** `platform_layer/secret_broker.py` resolves credentials
  just-in-time under a capability scope, with env/file/Vault/chain backends. Secrets are
  consumed in-tool and never transit the trace-logged syscall envelope.
- **Declarative capability policy.** `agents/capability_policy.py` supports per-capability
  recipient and domain allow-lists plus rate limits, enforced in `execute_tool`. It is
  **vacuous until a policy is registered** — do not claim it as an active control on a
  deployment that has registered none.

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

*Removed from this list 2026-08-05:* “Equivalent to a hardened runtime sandbox”. Blanket
avoidance is no longer accurate — for the **containerized** path the claim is supported by a
17-vector escape suite and a per-release audit log. It remains unsupportable for the
in-process path. State the path, and state the platform: the certification is for native
Linux, and macOS/Docker-Desktop backends are covered separately in
`MACOS_CONTAINER_POLICY.md`.

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
This is the largest current limitation, and it remains true.

If extension code runs **in-process** — the `AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS` path —
tenant and capability checks alone do not prove strong code isolation. That path is a
trusted-code mechanism. It is not a sandbox and must not be described as one.

**The distinction this document originally lacked:** the runtime has *two* extension
execution paths, and this limitation applies to only one of them.

| Path | Isolation | Claimable as |
|---|---|---|
| In-process (`AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS`) | none — same process, same interpreter | trusted internal code only |
| Containerized (`ContainerizedOciSandboxRunner`) | OCI container, `--network none`, dropped capabilities, read-only rootfs, pid limits | container-grade, escape-tested on native Linux |

Conflating them in either direction is the error to avoid: do not claim the in-process path
is sandboxed, and do not deny the container path the posture its escape suite demonstrates.

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

