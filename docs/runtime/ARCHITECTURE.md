---
title: "Runtime Architecture"
last_verified: "2026-05-29"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Runtime Architecture


## Current Runtime Posture

`aindy-runtime` should currently be understood first as a **trusted-internal runtime platform**.

That means:

- the runtime owns execution-substrate responsibilities
- the runtime supports explicit local and constrained distributed trusted-internal profiles
- the runtime does not currently claim a hardened hostile multitenant or arbitrary third-party extension-host posture

This architecture document preserves broader design context, but current support
claims should be interpreted through the governing docs above.

## Local + Cloud Distribution Context

`aindy-runtime` is designed with two distribution contexts in mind:

**Local install** — the operator owns all infrastructure. The runtime runs on the
operator's machine, in their network, against their database. They install via
`pip install aindy-runtime`, manage upgrades, and control every configuration
surface.

**Cloud-hosted** — a future provider-managed context in which the runtime could
run behind a control plane serving tenants that the operator does not directly
host.

The same runtime codebase was shaped with this local-to-cloud continuum in mind.
However, the future-state cloud framing should not be mistaken for current
supported posture.

## Why This Framing Still Matters

Five areas where the local-to-cloud design context still has architectural value:

**1. Sandbox runner selection by deployment profile.**
The `insecure_dev_subprocess` runner is acceptable for trusted-internal local
operation. Stronger isolation mechanisms such as `containerized_oci` or
`strong_sandbox_vm` are relevant for stricter future deployment contexts.
Their presence as technical mechanisms does not automatically establish broader
current support claims.

**2. Extension isolation tiers are calibrated to deployment context.**
Tier 1 (kernel-resident, trusted-operator code) and Tier 2 (externalized plugin-host
execution) remain meaningful implementation distinctions. They should not be
over-read as proof that the runtime currently supports a general hardened
third-party extension platform.

**3. The SDK is a stability boundary.**
The SDK matters more as the runtime becomes a cleaner substrate. Current
cross-repo compatibility and support interpretation should be read through
`CROSS_REPO_COMPATIBILITY.md`, not inferred solely from architecture intent.

**4. Multi-tenancy remains a future-state pressure, not a current broad claim.**
The runtime carries tenant and capability concepts today, but stronger hostile
multitenant support is not currently a supported runtime claim.

**5. The deployment contract is observable and operationally important.**
`/health`, `/ready`, deployment profile reporting, and runtime state remain
load-bearing surfaces regardless of whether the runtime is deployed locally or
in a future more managed context.

## Three Layers

### Layer 1 — Runtime Data Plane (`aindy-runtime`)

The runtime is the execution substrate. It owns:

- the syscall kernel and effect record lifecycle
- extension loading, capability gating, and trust enforcement
- runtime-owned persistence and scheduler jobs
- runtime-owned stable and conditionally stable HTTP surfaces declared in
  `PUBLIC_RUNTIME_SURFACES.md` and `RUNTIME_STABILITY_INDEX.md`
- deployment profile enforcement and the boot contract

The runtime is the thing that runs. It processes requests, executes flows,
manages runtime truth, and enforces runtime-owned contracts.

### Layer 2 — SDK Universal Interface (`aindy-sdk`)

The SDK is the consumer-facing access layer for stable runtime capabilities.

It should:

- wrap stable runtime contracts
- hide consumer ergonomics that do not belong in the runtime
- help applications target runtime surfaces without depending on runtime internals

The SDK's downstream contract interpretation should follow
`CROSS_REPO_COMPATIBILITY.md`.

### Layer 3 — Future Control Plane (not yet built)

A broader cloud control plane may eventually own:

- tenant registration and billing identity
- runtime node registration and fleet management
- cross-tenant observability aggregation
- upgrade orchestration and migration coordination
- administrative APIs above the runtime substrate

The runtime is designed to accommodate such a layer, but it does not currently
depend on that layer existing, nor does that future-state architecture expand
today's supported runtime claim by itself.

## What This Framing Does Not Commit To

**It does not commit to a specific cloud timeline or architecture.**
There is no cloud control plane today. This framing describes design direction,
not a present support level.

**It does not commit to hostile multitenant runtime support.**
The runtime's current supported posture is narrower and is defined in
`SECURITY_POSTURE.md` and `PROFILE_SUPPORT_MATRIX.md`.

**It does not commit to a specific SDK compatibility window beyond the current documented contract.**
Cross-repo compatibility should be interpreted through
`CROSS_REPO_COMPATIBILITY.md`.

**It does not commit to all mounted or technically possible surfaces being stable.**
Stability is governed by `PUBLIC_RUNTIME_SURFACES.md` and
`RUNTIME_STABILITY_INDEX.md`.

## Pointers to Other Docs

| Topic | Document |
|---|---|
| Foundational execution pattern | [FOUNDATIONAL_PATTERN.md](./FOUNDATIONAL_PATTERN.md) |
| Runtime support posture | [SECURITY_POSTURE.md](./SECURITY_POSTURE.md) |
| Supported deployment profiles | [PROFILE_SUPPORT_MATRIX.md](./PROFILE_SUPPORT_MATRIX.md) |
| Deployment profile enforcement | [DEPLOYMENT_PROFILES.md](./DEPLOYMENT_PROFILES.md) |
| Extension trust and ownership | [EXTENSION_TRUST_MODEL.md](./EXTENSION_TRUST_MODEL.md) |
| Extension ABI versioning | [EXTENSION_ABI.md](./EXTENSION_ABI.md) |
| Public runtime surfaces | [PUBLIC_RUNTIME_SURFACES.md](./PUBLIC_RUNTIME_SURFACES.md) |
| Runtime stability interpretation | [RUNTIME_STABILITY_INDEX.md](./RUNTIME_STABILITY_INDEX.md) |
| Cross-repo compatibility | [CROSS_REPO_COMPATIBILITY.md](./CROSS_REPO_COMPATIBILITY.md) |
| Boot and startup contract | [RUNTIME_ONLY_DEPLOYMENT.md](./RUNTIME_ONLY_DEPLOYMENT.md) |
| Idempotency and effect records | [IDEMPOTENCY_CONTRACT.md](./IDEMPOTENCY_CONTRACT.md) |
