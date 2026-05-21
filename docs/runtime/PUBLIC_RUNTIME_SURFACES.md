---
title: "Public Runtime Surfaces"
last_verified: "2026-05-20"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Public Runtime Surfaces

This document defines the external runtime surface in stability terms. It is
intentionally conservative.

Meanings:

- `stable`: expected to remain compatible within the current runtime-package
  and API MAJOR series
- `experimental`: shipped and supported enough to use, but still allowed to
  change in minor releases
- `internal`: not part of the external platform contract

The machine-readable form of this contract is exposed in `GET /api/version`
under `public_contract`.

Release posture:

- support tier: `trusted-internal`
- not claimed:
  - third-party extension isolation
  - sandboxed in-process plugin execution
  - fully frozen semantics outside the declared stable surfaces
- operator meaning of `ready`:
  - dependency and unsafe-condition checks for the active deployment profile passed
  - this is not a certification of extension trust or isolation
- external Python override note:
  - `AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS=true` is a trusted-code override
  - it does not create sandboxing for third-party Python
  - production use also requires `AINDY_ACK_UNSANDBOXED_EXTERNAL_PYTHON=true`

## Stable HTTP Surfaces

- `GET /api/version`
  Returns package compatibility metadata, API version metadata, runtime-only
  surface state, and the machine-readable public contract inventory.
- `GET /health`
  Returns operator-visible liveness and degraded-runtime conditions.
- `GET /ready`
  Returns operator-visible readiness with strict dependency and unsafe-degraded
  checks for the active deployment profile. It does not certify extension
  isolation or third-party code trust.
- `GET /platform/syscalls`
  Returns the versioned syscall catalog with per-entry `stable` and
  `deprecated` markers.
- `POST /platform/syscall`
  Dispatches a versioned syscall through the public syscall envelope.

## Experimental HTTP Surfaces

- `/apps/agent/*`
  Runtime-owned and supported, but the external orchestration semantics are
  still evolving.
- `/apps/memory/*`
  Runtime-owned and available, but the HTTP shape is not yet frozen as a long
  term public contract.
- `/apps/coordination/*`
  Available but not declared stable.
- `/platform/flows*`
  Dynamic flow management remains experimental.
- `/platform/nodes*`
  Dynamic external node management remains experimental.
- `/platform/nodus*`
  Nodus upload and script-management routes remain experimental.
- `/platform/webhooks*`
  Runtime-owned, but not yet declared stable.

## Syscall Contract

The syscall ABI is versioned. The current baseline is:

- stable version family: `v1`
- experimental version family: `v2`

Important constraint:

- do not assume every `sys.v1.*` syscall is stable
- the authoritative marker is the per-entry `stable` field from
  `GET /platform/syscalls`

Representative stable entries today:

- `sys.v1.memory.read`
- `sys.v1.memory.write`
- `sys.v1.flow.run`
- `sys.v1.event.emit`
- `sys.v1.agent.execute`

Representative experimental entries today:

- `sys.v1.memory.list`
- `sys.v1.memory.tree`
- `sys.v1.memory.trace`
- `sys.v1.agent.count_runs`
- `sys.v2.memory.read`

## Extension Registration Surfaces

These are externally consumable integration points, but they are still
experimental:

- manifest bootstrap modules loaded through `AINDY.platform_layer.registry`
- `AINDY.platform_layer.registry.register_*` helper shapes
- `AINDY.agents.tool_registry.register_tool`
- dynamic plugin nodes via
  `AINDY.platform_layer.node_registry.register_external_node(type="plugin")`
- webhook nodes via
  `AINDY.platform_layer.node_registry.register_external_node(type="webhook")`
- dynamic flows via `AINDY.runtime.flow_registry.register_dynamic_flow`

Reason for the experimental classification:

- these surfaces still reflect extraction-era architecture
- they are validated and runtime-owned, but the exact registration shapes and
  extension boundaries are still moving
- the runtime does not claim in-process isolation for trusted Python extensions

Ownership model:

- `runtime-built-in`: runtime-owned internal extensions under `AINDY.*`
- `first-party-app`: trusted app-owned integrations under `apps.*`
- `external-third-party`: explicitly separate third-party integrations

The runtime-owned manifest may load only `runtime-built-in` entries. App and
third-party ownership classes must stay out of the runtime-only profile.

If the external Python override is enabled, the runtime surfaces that state as
operator-visible degraded mode. Health and readiness still describe dependency
state, but they also show that unsandboxed external Python execution has been
explicitly enabled.

## Stable Boot Contract

`runtime-only` boot is a stable external boot contract.

Stable invariants:

- `AINDY_BOOT_MODE=runtime-only` selects the runtime-only surface
- the resolved boot profile is `platform-only`
- runtime-only boot still enforces schema, readiness, and degraded-mode safety
- the baseline agent/tool surface remains limited to runtime-owned capabilities

Important scope limit:

- the stable claim applies to boot selection and runtime-owned baseline behavior
- it does not promote experimental extension surfaces into a hardened external
  platform contract

Use [RUNTIME_ONLY_DEPLOYMENT.md](./RUNTIME_ONLY_DEPLOYMENT.md) for the full
bootstrap contract and [PUBLIC_API_CONTRACT.md](./PUBLIC_API_CONTRACT.md) for
the import boundary.
