---
title: "Runtime Architecture"
last_verified: "2026-05-25"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Runtime Architecture

## Local + Cloud Distribution Model

`aindy-runtime` is built to serve two distinct distribution contexts simultaneously:

**Local install** — the operator owns all infrastructure. The runtime runs on the
operator's machine, in their network, against their database. They install via
`pip install aindy-runtime`, manage upgrades, and control every configuration
surface. Trust is high; isolation needs are lower.

**Cloud-hosted** — the provider (Masterplan / A.I.N.D.Y. platform team) owns the
infrastructure. The runtime runs in a provider-managed environment serving multiple
tenants. The operator interacts through an SDK and a control plane interface; they
have no direct filesystem or process access. Trust must be verified; isolation
requirements are much stricter.

The same runtime codebase targets both contexts. This is not an accident — the
deployment profile system, sandbox runner selection, and extension isolation tiers
were all designed with the local-to-cloud continuum in mind.

## Why This Framing Shapes Architecture

Five areas where the local+cloud model has concrete architectural consequences:

**1. Sandbox runner selection by deployment profile.**
The `insecure_dev_subprocess` runner is acceptable for local-install trusted
operators. For cloud-hosted multi-tenant contexts, the runtime requires
`containerized_oci` or `strong_sandbox_vm`. The deployment profile
(`hostile-third-party`) gates runner selection — this is the mechanism that
translates "local vs. cloud" into a runtime enforcement point.

**2. Extension isolation tiers are calibrated to deployment context.**
Tier 1 (kernel-resident, trusted-operator code) has lower overhead because the
trust model is explicit and the operator controls their install. Tier 2
(externalized behind a plugin-host subprocess) is the minimum for any code
that is not first-party in a cloud context. The two-tier model exists because
different code runs at different trust levels depending on who controls the
deployment.

**3. The SDK is the stability boundary between contexts.**
In a local install, the SDK is optional — operators can call the HTTP surface
directly. In the cloud model, the SDK is mandatory: it is the operator's only
programmatic interface to a runtime they do not control. This asymmetry means
the SDK's stability and compatibility guarantees matter more than they appear to
today. See `DEBT-COMPAT-1` in `TECH_DEBT.md`.

**4. Multi-tenancy is implicit today but must be explicit in cloud.**
The current user model supports multiple users per runtime process, but there
is no tenant isolation layer (quotas, data boundaries, event bus separation)
that would be required for a cloud control plane serving adversarial tenants.
This is intentional deferral, not an oversight. See `LOCAL_AND_CLOUD_AUDIT.md`
for a full gap analysis.

**5. The deployment contract is the operator's observable signal.**
`deployment_contract_summary()` and `/health` expose what the runtime believes
about its own deployment context. In a local install this is informational. In
a cloud context it becomes a load-bearing monitoring surface — the control plane
uses it to verify node registration and capability posture.

## Three Layers

### Layer 1 — Runtime Data Plane (`aindy-runtime`)

The runtime is the data plane. It owns:

- The syscall kernel and effect record lifecycle
- Extension loading, capability gating, and isolation enforcement
- Database schema, memory persistence, and scheduler jobs
- All stable HTTP surfaces (`/health`, `/flow/run`, `/memory/**`, etc.)
- The deployment profile enforcement and boot contract

The runtime is the thing that runs. It processes requests, executes flows,
manages state, and enforces all security and isolation policies.

### Layer 2 — SDK Universal Interface (`aindy-sdk`)

The SDK is the universal interface between app code and the runtime. It targets
both distribution contexts:

- Against a local runtime: `base_url="http://localhost:8000"` — the app
  developer owns both sides.
- Against a cloud runtime: `base_url="https://runtime.aindy.ai"` — the SDK
  bridges to infrastructure the app developer does not control.

The SDK makes app code portable across deployment contexts. The same application
code — the same `client.memory.read(...)` and `client.flow.run(...)` calls —
works against a locally-installed runtime or a cloud-hosted one without
modification. This is the local+cloud bridge.

The SDK's import boundary with the runtime is defined in
`PUBLIC_API_CONTRACT.md`. Its HTTP surface targets are defined in
`PUBLIC_RUNTIME_SURFACES.md`.

### Layer 3 — Cloud Control Plane (not yet built)

The cloud control plane does not exist yet. When built, it will own:

- Tenant registration and billing identity
- Runtime node registration and fleet management
- Cross-tenant observability aggregation
- Upgrade orchestration and migration coordination
- The API surface that sits above the SDK for administrative operations

The runtime is designed to accommodate this layer — the deployment contract,
health surfaces, and sandbox posture reporting are all control-plane-ready.
The runtime does not depend on the control plane existing.

## What This Framing Does Not Commit To

**It does not commit to a specific cloud timeline or architecture.**
There is no cloud control plane today. The framing describes the intent and the
design constraints, not a ship date.

**It does not commit to multi-tenancy at the runtime level.**
The current plan is for multi-tenancy to live in the control plane, with the
runtime receiving tenant context via request headers or configuration, not
owning tenant lifecycle. This may change.

**It does not commit to a specific SDK compatibility window.**
The SDK and runtime ship at matching versions today. Cross-version compatibility
policy (what SDK version N is guaranteed against runtime N+1, N+2) is an open
question tracked in `DEBT-COMPAT-1`.

**It does not commit to cloud-only features being hidden from local installs.**
Local operators may have access to cloud-oriented features (like the `/health/sandbox`
posture reporting) — the features exist in the runtime regardless of deployment
context. What the cloud control plane does with them is a separate question.

## Pointers to Other Docs

| Topic | Document |
|---|---|
| Deployment profile enforcement | [DEPLOYMENT_PROFILES.md](./DEPLOYMENT_PROFILES.md) |
| Extension isolation tiers | [EXTENSION_TRUST_MODEL.md](./EXTENSION_TRUST_MODEL.md) |
| Extension ABI versioning | [EXTENSION_ABI.md](./EXTENSION_ABI.md) |
| Stable HTTP surfaces | [PUBLIC_RUNTIME_SURFACES.md](./PUBLIC_RUNTIME_SURFACES.md) |
| Runtime import boundary (apps ↔ runtime) | [PUBLIC_API_CONTRACT.md](./PUBLIC_API_CONTRACT.md) |
| Local+cloud gap analysis | [LOCAL_AND_CLOUD_AUDIT.md](./LOCAL_AND_CLOUD_AUDIT.md) |
| Runtime ↔ apps-monolith version compat | [REPO_COMPATIBILITY_POLICY.md](./REPO_COMPATIBILITY_POLICY.md) |
| Boot and startup contract | [RUNTIME_ONLY_DEPLOYMENT.md](./RUNTIME_ONLY_DEPLOYMENT.md) |
| Idempotency and effect records | [IDEMPOTENCY_CONTRACT.md](./IDEMPOTENCY_CONTRACT.md) |
