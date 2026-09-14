---
title: "Extension Trust Model"
last_verified: "2026-09-13"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Extension Trust Model


This document defines the runtime extension trust and ownership boundary as it
exists today.

The versioned extension ABI policy is documented separately in
[EXTENSION_ABI.md](./EXTENSION_ABI.md).
The explicit capability model is documented separately in
[EXTENSION_CAPABILITIES.md](./EXTENSION_CAPABILITIES.md).
The provenance and integrity contract is documented separately in
[EXTENSION_PROVENANCE.md](./EXTENSION_PROVENANCE.md).

## Important Limitation

- `aindy-runtime` does not provide true in-process sandboxing for Python extensions.
- Any manifest bootstrap module or dynamic plugin node that is imported into the
  interpreter executes with normal Python process privileges.
- The hardening in this repo reduces accidental trust expansion and obvious
  unsafe inputs, but it does not turn Python imports into an isolation boundary.

## Current Supported Posture

The runtime's current support posture is:

- trusted-internal runtime deployment
- explicit tenant and capability enforcement in runtime-owned paths
- constrained extension support under explicit trust assumptions

The runtime does **not** currently claim:

- hardened arbitrary third-party in-process extension hosting
- hostile multitenant compute isolation
- broad marketplace-style plugin platform support

## Ownership Classes

`aindy-runtime` operates on a two-tier trust and execution model:

- **Tier 1 — trusted-operator kernel-resident code**
  `runtime-built-in` and `first-party-app` code runs in the main interpreter
  because it is deployed by the same operator running the runtime. This is the
  intentional design for this tier.
- **Tier 2 — externalized extension surfaces**
  `external-third-party` code never runs in the main interpreter. Third-party
  execution goes through the plugin-host subprocess boundary or equivalent
  externalized mechanism.

Within this model, the runtime distinguishes three ownership classes:

- `runtime-built-in`
  Runtime-owned kernel callables and bootstrap modules shipped under `AINDY.*`.
- `first-party-app`
  Trusted app-owned integrations loaded from `apps.*`. These are not
  runtime-owned and are excluded from the runtime-only profile.
- `external-third-party`
  Third-party or non-monolith extensions. These are not treated as trusted
  in-process code.

Ownership is separate from trust:

- `runtime-built-in` and `first-party-app` Python modules are both trusted
  in-process execution
- `external-third-party` is not treated as trusted in-process code by default
- webhook nodes, webhook subscriptions, and dynamic flows are contract-driven
  integrations or data-only registrations, not Python sandbox boundaries

## Tier 1 Trusted Kernel Code

The following surfaces execute as trusted Tier 1 kernel code in the main
interpreter:

- manifest bootstrap modules loaded by
  [AINDY/platform_layer/registry.py](../../AINDY/platform_layer/registry.py)
  when `owner_class` is `runtime-built-in` or `first-party-app`
- manifest declarative extension entries loaded by the same registry for
  external onboarding without Python bootstrap execution
- runtime-built-in dynamic plugin nodes loaded by
  [AINDY/platform_layer/node_registry.py](../../AINDY/platform_layer/node_registry.py)
  when `owner_class` is `runtime-built-in`

Properties of Tier 1 kernel code:

- can execute arbitrary Python during import or call time
- can mutate process state
- can violate runtime invariants if the code itself is malicious or broken
- is trusted because the operator controls and deploys it, not because it is
  sandboxed at execution time

## Current Hardening

- manifest bootstrap modules are restricted to trusted module prefixes
  (`AINDY.` and `apps.` by default, configurable through
  `AINDY_TRUSTED_BOOTSTRAP_PREFIXES`)
- runtime-owned manifests may declare only `runtime-built-in` bootstrap entries
- external third-party Python bootstrap entries require explicit prefixes from
  `AINDY_EXTERNAL_BOOTSTRAP_PREFIXES`, but bootstrap import/execution remains
  unsupported because it is inherently in-process
- external third-party dynamic plugin nodes no longer import into the runtime
  process; they validate and execute through `AINDY.platform_layer.extension_worker`
  over a subprocess request/response boundary
- first-party app dynamic plugin nodes also execute through the plugin-host
  boundary by default
- first-party and runtime-built-in module callbacks registered for startup
  hooks, planner context, run-tool providers, trigger evaluators, completion
  hooks, and capability-definition providers execute through the
  `runtime_callback_worker` boundary when they are module-level functions the
  runtime can resolve explicitly
- third-party plugin-host lifecycle is mediated through the runtime-owned
  sandbox runner interface; `insecure_dev_subprocess` remains a containment
  boundary rather than a sandbox claim
- external third-party dynamic plugin nodes receive no ambient runtime
  capabilities by default; allowed runtime interactions must be granted
  explicitly and go through the runtime extension API
- `AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS=true` is only a legacy operator
  marker; it does not re-enable third-party in-process imports
- `/health`, `/ready`, and `/api/version` publish a live trusted-Python
  inventory covering bootstrap modules, registrations, dynamic plugin nodes,
  ownership-class counts, and in-process bootstrap registration capability use

## Available Platform Sandbox Mechanism Matrix

The runtime publishes a platform capability matrix through `/api/version`,
`/health`, and `/ready`. The matrix is runtime-owned and reflects current host
capabilities rather than implying uniform guarantees across operating systems.

The matrix below describes available mechanisms and detected host/runtime
capabilities. It does not, by itself, establish the support level of a broader
deployment profile or extension-host claim.

### Current Mechanism Summary

- Linux
  - available runners: `insecure_dev_subprocess`, `containerized_oci`,
    `strong_sandbox_vm`
  - container-backed third-party plugin isolation mechanism available: yes,
    when a compatible container runtime is available
  - strong-sandbox host support characterization: Linux-only
- Windows
  - available runners: `insecure_dev_subprocess`, `containerized_oci` when
    Docker Desktop or Podman is configured for Linux containers
  - container-backed third-party plugin isolation mechanism available: yes,
    when the configured container runtime is in Linux-containers mode
  - strong-sandbox host support characterization: no
- macOS
  - available runners: `insecure_dev_subprocess`, `containerized_oci` when
    Docker Desktop or Podman is configured for Linux containers
  - container-backed third-party plugin isolation mechanism available: yes,
    when the configured container runtime reports `OSType=linux`
  - strong-sandbox host support characterization: no
- Other hosts
  - available runners: `insecure_dev_subprocess`, plus `containerized_oci`
    only if a compatible container runtime is available
  - container-backed third-party plugin isolation mechanism available: no
  - strong-sandbox host support characterization: no

Important implications:

- technical mechanism availability is not the same thing as broad supported
  profile posture
- stronger third-party plugin-host claims remain constrained by
  `SECURITY_POSTURE.md` and `PROFILE_SUPPORT_MATRIX.md`
- `insecure_dev_subprocess` remains a development containment boundary only

### Container-Backed Third-Party Plugin Isolation Semantics

The runtime defines container-backed third-party plugin isolation as a property
of the container backend, not the host operating system. This is a technical
mechanism description, not a broad support claim by itself.

A host has this mechanism available when:

1. a supported container runtime is available on PATH
2. the runtime is configured to run Linux containers (`OSType=linux`)

Both conditions are detected at runtime startup via the runtime's container
backend detection path and are visible through `/api/version`.

This definition holds the runtime to delivering Linux container semantics —
pinned OCI images, runtime-managed read-only mounts, kernel-level hardening
controls active inside the container — regardless of the host OS that supplies
the kernel. Strong-sandbox guarantees remain Linux-host-specific.

## Assurance Reporting

Operator surfaces (`/health` and `/health/deep` under `plugin_sandbox_posture`, and
`/api/version`) report third-party plugin execution with five distinct vocabularies. They
are related but not interchangeable, and every one of them is a claim about **Tier 2**
only. Constants live in `AINDY/platform_layer/sandbox_runner.py`,
`sandbox_certification.py` and `deployment_contract.py`; this section is the prose
reading of them.

- **Assurance class** — the *category* of the runner currently selected:
  `insecure-dev` (`insecure_dev_subprocess`), `container-grade-sandbox`
  (`containerized_oci`), or `strong-sandbox-tier` (`strong_sandbox_vm`). It says what the
  runtime is *trying* to provide, not what it has verified.
- **Attestation** — what the runtime actually *observed* at launch: backend identity,
  runtime identity (pinned OCI digest), mount mode, and resource-limit mode, each
  carrying its own `verified` flag. Attestation is narrower than a blanket sandbox
  guarantee — it covers the launch, not the running worker's state.
- **Verification method** — how any *post-launch* claim was established:
  `worker-self-report` (an authenticated RPC probe the worker answers about its own
  guard state), `kernel-observable` (unprivileged `/proc/<pid>` evidence for seccomp,
  cgroup and namespace membership, collected on Linux hosts after a live worker PID
  exists), or `none`.
- **Assurance ceiling** — the strongest claim the *current evidence* can support:
  `no-isolation-guarantee`, `worker-self-report-verified`, or
  `kernel-observable-verified`. A stronger assurance class does not raise the ceiling;
  only evidence does. Non-Linux hosts stay at `worker-self-report-verified`.
- **Certification tier** — what the runtime can *justify* from verified attestation plus
  the shared worker-policy suite: `contained-process-certified`,
  `container-sandbox-certified`, or `strong-sandbox-certified`. The tier is computed,
  never configured, and `tier_status` is `certified` only when every requirement of the
  tier for the *selected runner* is met. A runner that is merely selected is
  `not_certified_for_runner`.

Rules that follow from the separation:

- a stronger assurance class does not by itself imply a certification tier
- verified attestation is narrower than a certification tier, which also needs the
  policy suite and (for strong) a passed post-launch probe
- readiness does not imply a higher assurance class than the runtime reports
- `strong-sandbox-certified` is reachable on Linux hosts only
  (`STRONG_SANDBOX_SUPPORTED_HOST_PLATFORMS`); other hosts cap at
  `container-sandbox-certified` — this is the open `C3` remainder

Profile expectations (`plugin_sandbox_profile_requirements`):

| Deployment profile | Required assurance class | Required certification tier |
|---|---|---|
| `single-instance` | none | none |
| `distributed-api` / `distributed-worker` | `container-grade-sandbox` | none at startup |
| `hostile-third-party` | `strong-sandbox-tier` | `strong-sandbox-certified` |

**Tier 1 surfaces are outside all five vocabularies.** Manifest bootstrap for
`runtime-built-in` and `first-party-app`, kernel-resident callables, and runtime-built-in
plugin nodes are excluded from plugin sandbox attestation and carry no assurance class,
ceiling or certification tier. They are trusted kernel code deployed by the same operator
running the runtime; they do not require a process isolation boundary, so sandbox
attestation is not applicable to them. Their absence from the posture report is by
design, not a gap in coverage.

*(This section was first written for the Tiered Isolation Contract plan (A3), removed in the
2026-05-31 docset reconciliation, and restored 2026-09-13 against the current constants —
the vocabulary had been in use across four documents with no definition in between.)*

## Reading Rule

When this document describes a technical mechanism that is stronger than the
current supported trusted-internal posture, interpret it as:

- current mechanism detail
- future-state or stricter-deployment capability context
- not automatic promotion of the runtime into a broader supported extension or
  multitenant platform

For supported claim ceilings, always defer to:

- `SECURITY_POSTURE.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `RUNTIME_STABILITY_INDEX.md`
