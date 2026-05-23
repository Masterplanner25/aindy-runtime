---
title: "Extension Trust Model"
last_verified: "2026-05-23"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Extension Trust Model

This document defines the runtime extension trust and ownership boundary as it
exists today.

The versioned extension ABI policy is documented separately in
[EXTENSION_ABI.md](/abs/path/C:/dev/aindy-runtime/docs/runtime/EXTENSION_ABI.md).
The explicit capability model is documented separately in
[EXTENSION_CAPABILITIES.md](/abs/path/C:/dev/aindy-runtime/docs/runtime/EXTENSION_CAPABILITIES.md).
The provenance and integrity contract is documented separately in
[EXTENSION_PROVENANCE.md](/abs/path/C:/dev/aindy-runtime/docs/runtime/EXTENSION_PROVENANCE.md).

Important limitation:

- `aindy-runtime` does not provide true in-process sandboxing for Python extensions.
- Any manifest bootstrap module or dynamic plugin node that is imported into the
  interpreter executes with normal Python process privileges.
- The hardening in this repo reduces accidental trust expansion and obvious
  unsafe inputs, but it does not turn Python imports into an isolation boundary.

## Ownership Classes

aindy-runtime operates on a two-tier isolation model:

- **Tier 1 — trusted-operator kernel-resident code**
  `runtime-built-in` and `first-party-app` code runs in the main interpreter
  because it is deployed by the same operator running the runtime. This is the
  intentional design for this tier, not an exception to a more-isolated baseline.
  Capability mediation gates what each caller may register; after registration,
  Tier 1 callables execute as kernel-resident trusted code.
- **Tier 2 — third-party externalized extension surfaces**
  `external-third-party` code never runs in the main interpreter. All third-party
  execution goes through the isolated plugin-host subprocess boundary. No
  exceptions.

Within this model, the runtime distinguishes three ownership classes:

- `runtime-built-in`
  Runtime-owned kernel callables and bootstrap modules shipped under `AINDY.*`.
  These are Tier 1 trusted-operator code and are the only class allowed in the
  runtime-owned manifest.
- `first-party-app`
  Trusted app-owned integrations loaded from `apps.*`. These are Tier 1
  trusted-operator code, but they are not runtime-owned and are excluded from
  the runtime-only profile.
- `external-third-party`
  Third-party or non-monolith extensions. These are Tier 2: never trusted as
  in-process code. Third-party manifest bootstrap modules remain unsupported,
  and third-party plugin nodes execute only through the isolated plugin-host
  boundary. External onboarding should use declarative manifest entries or the
  runtime registration APIs for webhook nodes, webhook subscriptions, dynamic
  flows, or isolated plugin nodes. The plugin-host boundary enforces
  runtime-owned socket and standard file-API restrictions, but it is still not
  a full OS sandbox.

Ownership is separate from trust:

- `runtime-built-in` and `first-party-app` Python modules are both trusted
  in-process code execution
- `external-third-party` is not treated as trusted in-process code by default
- webhook nodes, webhook subscriptions, and dynamic flows are contract-driven
  integrations or data-only registrations, not Python sandbox boundaries

Tier 1 — first-party execution model:

- first-party manifest bootstrap is the intentional in-process registration path
  for trusted-operator boot wiring; it is Tier 1 kernel code, not a privileged
  exception to a more-isolated baseline
- first-party dynamic plugin nodes execute through the isolated plugin-host path,
  not in-process
- first-party module-style callback providers registered into runtime registries
  execute through a runtime-owned callback worker when the handler is resolvable
  as a module-level function
- in-process bootstrap interaction with the kernel goes through an explicit
  runtime-owned registration capability boundary, with a smaller default
  allowlist for `first-party-app` than for `runtime-built-in`

## Tier 1 Trusted Kernel Code

The following surfaces execute as trusted Tier 1 kernel code in the main
interpreter. They are kernel code deployed by the operator, not extension code
requiring a sandbox boundary:

- manifest bootstrap modules loaded by
  [AINDY/platform_layer/registry.py](/abs/path/C:/dev/aindy-runtime/AINDY/platform_layer/registry.py)
  when `owner_class` is `runtime-built-in` or `first-party-app`
- manifest declarative extension entries loaded by
  [AINDY/platform_layer/registry.py](/abs/path/C:/dev/aindy-runtime/AINDY/platform_layer/registry.py)
  for external onboarding without Python bootstrap execution
- runtime-built-in dynamic plugin nodes loaded by
  [AINDY/platform_layer/node_registry.py](/abs/path/C:/dev/aindy-runtime/AINDY/platform_layer/node_registry.py)
  when `owner_class` is `runtime-built-in`

Properties of Tier 1 kernel code:

- can execute arbitrary Python during import or call time
- can mutate process state
- can violate runtime invariants if the code itself is malicious or broken
- is trusted because the operator controls and deploys it — not because it is
  sandboxed or capability-confined at execution time

Current hardening:

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
  boundary by default, using the same explicit runtime API capability model as
  third-party plugin nodes
- first-party and runtime-built-in module callbacks registered for startup
  hooks, planner context, run-tool providers, trigger evaluators, completion
  hooks, and capability-definition providers execute through the
  `runtime_callback_worker` boundary when they are module-level functions the
  runtime can resolve explicitly
- third-party plugin-host lifecycle is now mediated through the runtime-owned
  sandbox runner interface; the current implementation is
  `insecure_dev_subprocess`, which is a containment boundary rather than a
  sandbox claim
- when `containerized_oci` is selected, the runtime reports kernel-level
  hardening controls explicitly per environment, including active controls and
  unsupported controls; it does not claim Linux security features are active on
  platforms where they cannot be enforced
- external third-party dynamic plugin nodes receive no ambient runtime
  capabilities by default; allowed runtime interactions must be granted
  explicitly and go through the runtime extension API
- `AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS=true` is now only a legacy operator
  marker; it does not re-enable third-party in-process imports
- `/health`, `/ready`, and `/api/version` also publish a live trusted-Python
  inventory covering:
  - loaded manifest bootstrap modules
  - bootstrap registrations published by those modules
  - trusted dynamic plugin nodes
  - ownership-class counts distinguishing `runtime-built-in` from
    `first-party-app`
  - allowed, used, and denied in-process bootstrap registration capabilities
- plugin node handlers are loaded only from `AINDY/plugins/nodes/`
- plugin node loading no longer mutates `sys.path`
- plugin node handlers must expose a callable compatible with the node contract

## Supported Platform Sandbox Matrix

The runtime now publishes a platform capability matrix through `/api/version`,
`/health`, and `/ready`. The matrix is runtime-owned and reflects the current
host platform rather than implying uniform guarantees across operating systems.

Current support summary:

- Linux
  - available runners: `insecure_dev_subprocess`, `containerized_oci`,
    `strong_sandbox_vm`
  - production-safe third-party plugin sandbox support: yes, when a compatible
    container runtime is available
  - declared fully supported host platform for strong sandbox and
    `hostile-third-party`: yes
  - stronger controls may be reported active for `containerized_oci`, including
    `no_new_privileges`, dropped capabilities, PID limits, seccomp, AppArmor,
    and SELinux label controls
- Windows
  - available runners: `insecure_dev_subprocess`, `containerized_oci` when a
    container runtime is installed
  - production-safe third-party plugin sandbox support: no
  - declared fully supported host platform for strong sandbox and
    `hostile-third-party`: no
  - degraded mode: Linux-only kernel hardening controls are not reported as
    enforceable on the Windows host
- macOS
  - available runners: `insecure_dev_subprocess`, `containerized_oci` when a
    container runtime is installed
  - production-safe third-party plugin sandbox support: no
  - declared fully supported host platform for strong sandbox and
    `hostile-third-party`: no
  - degraded mode: container execution depends on host virtualization and does
    not imply native macOS kernel policy enforcement
- Other hosts
  - available runners: `insecure_dev_subprocess`, plus `containerized_oci`
    only if a compatible container runtime is available
  - production-safe third-party plugin sandbox support: no
  - declared fully supported host platform for strong sandbox and
    `hostile-third-party`: no
  - degraded mode: the host is outside the explicitly characterized sandbox
    support set

Important implications:

- production-oriented deployment profiles reject third-party sandbox execution
  unless the runtime can provide the documented Linux containerized guarantees
- the repo’s declared strong-sandbox support set is Linux-only; non-Linux hosts
  remain explicitly visible in the matrix, but they are not part of the
  equivalent high-assurance support claim
- `insecure_dev_subprocess` remains a development containment boundary only
- `containerized_oci` on non-Linux hosts is reported explicitly as degraded
  rather than being treated as equivalent to the Linux hardened path

## Assurance Reporting

Operator surfaces now distinguish three separate concepts for third-party plugin
execution:

- assurance class
  The current runner category reported by the runtime:
  `insecure-dev`, `container-grade-sandbox`, or `strong-sandbox-tier`.
- attestation
  What the runtime actually observed, primarily from launch-time backend
  identity and command evidence.
- certification tier
  What the runtime can justify from verified evidence and the shared worker
  policy suite:
  `contained-process-certified`, `container-sandbox-certified`, or
  `strong-sandbox-certified`.

These are related but not interchangeable:

- a stronger assurance class does not by itself imply certification
- verified attestation is narrower than a blanket sandbox guarantee
- readiness does not imply a higher assurance class than the runtime reports

Profile expectations:

- `single-instance`
  - required assurance class: none
  - certification tier: none required
- `distributed-api` / `distributed-worker`
  - required assurance class: `container-grade-sandbox`
  - certification tier: none required at startup
- `hostile-third-party`
  - required assurance class: `strong-sandbox-tier`
  - required certification tier: `strong-sandbox-certified`

Tier 1 trusted-operator surfaces — manifest bootstrap for `runtime-built-in`
and `first-party-app`, kernel-resident callables, and runtime-built-in plugin
nodes — are excluded from plugin sandbox attestation. These surfaces are trusted
kernel code deployed by the same operator running the runtime. They do not
require a process isolation boundary, and sandbox attestation is therefore not
applicable to them.

## Untrusted Or Less-Trusted Extension Classes

These extension classes are treated as contract-driven integrations or data,
not trusted Python code:

- webhook nodes registered through `/platform/nodes/register` with `type=webhook`
- webhook subscriptions registered through `/platform/webhooks`
- dynamic flows registered through `/platform/flows`

Properties:

- webhook targets are outside the process and receive serialized payloads only
- dynamic flows are data-only graph definitions and do not inject Python
- registry restore paths preserve their persisted ownership class and re-apply
  the same validation on restart

Current hardening:

- outbound webhook targets must be `http://` or `https://`
- embedded URL credentials are rejected
- private and loopback targets are rejected by default to reduce SSRF-style
  mistakes; override only with `AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS=true`
- dynamic flow definitions are limited to data-only shapes with size and
  duplicate checks
- webhook restore paths re-apply outbound target validation instead of
  silently loading stale unsafe endpoints

## File-Level Threat Model

- [AINDY/platform_layer/registry.py](/abs/path/C:/dev/aindy-runtime/AINDY/platform_layer/registry.py)
  Trusted bootstrap import path. Risk: manifest-selected arbitrary Python import.
  Hardening: ownership-aware prefix validation, runtime-only manifest scoping,
  and default blocking of external third-party Python execution.
- [AINDY/platform_layer/node_registry.py](/abs/path/C:/dev/aindy-runtime/AINDY/platform_layer/node_registry.py)
  Dynamic node registration path. Risk: in-process Python import, historical
  `sys.path` mutation, unsafe webhook targets. Hardening: file-bound module
  loading for trusted code, isolated subprocess execution for third-party
  plugin nodes, callable-shape validation, and webhook URL policy.
- [AINDY/platform_layer/event_service.py](/abs/path/C:/dev/aindy-runtime/AINDY/platform_layer/event_service.py)
  Outbound webhook subscription dispatch. Risk: SSRF or accidental delivery into
  private control planes. Hardening: outbound target validation on both create
  and restore paths.
- [AINDY/platform_layer/platform_loader.py](/abs/path/C:/dev/aindy-runtime/AINDY/platform_layer/platform_loader.py)
  Restart restore path. Risk: stale persisted registrations silently regaining
  broader privileges on restart. Hardening: owner_class persistence and
  re-application of registration-time policy during restore.
- [AINDY/runtime/flow_registry.py](/abs/path/C:/dev/aindy-runtime/AINDY/runtime/flow_registry.py)
  Dynamic flow definition path. Risk: oversized or malformed data-driven
  orchestration that references existing runtime nodes. Hardening: shape, size,
  and duplicate validation. No code sandbox claim.

## Operational Guidance

- Treat manifest bootstrap modules and dynamic plugin nodes as trusted code
  deployment, not user content.
- Treat `runtime-built-in` and `first-party-app` as distinct operator-visible
  classes:
  runtime-built-in code is runtime-owned infrastructure code;
  first-party-app code is trusted app-owned code, with manifest bootstrap still
  loaded in-process and plugin-style execution isolated where the runtime can
  externalize it.
- Treat first-party manifest bootstrap as Tier 1 kernel code: the intentional
  in-process registration path for trusted-operator boot wiring. It runs
  in-process because it performs registry mutation and boot-time kernel wiring,
  not because it is a transitional exception awaiting future externalization.
- Treat first-party plugin nodes and first-party registry callback providers as
  Tier 2 isolated by default. Only first-party manifest bootstrap executes
  in-process, as intentional Tier 1 kernel code.
- Treat external third-party manifest bootstrap as unsupported.
- Treat external third-party plugin nodes as isolated subprocess work, not
  trusted in-process imports.
- Prefer webhook nodes or dynamic flows when a use case can stay data-driven.
- If you need real isolation for untrusted extension code, it must be moved out
  of process into a separately sandboxed execution environment.
