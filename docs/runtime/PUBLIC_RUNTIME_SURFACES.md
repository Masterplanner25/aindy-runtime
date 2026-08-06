---
title: "Public Runtime Surfaces"
last_verified: "2026-08-06"
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
- `conditionally stable`: core execution surfaces deployed at v1.0.0 and
  unlikely to break, but HTTP shape not yet formally frozen; SDK and UI
  consumption should prefer these over experimental surfaces
- `experimental`: shipped and supported enough to use, but still allowed to
  change in minor releases
- `internal`: not part of the external platform contract

Important reading rule:

- route presence is not, by itself, a broad downstream compatibility promise
- downstream reliance should prefer the narrower stable and conditionally stable interpretation in `RUNTIME_STABILITY_INDEX.md`

The machine-readable form of this contract is exposed in `GET /api/version`
under `public_contract`.

Extension ABI policy is documented in
[EXTENSION_ABI.md](./EXTENSION_ABI.md).
Extension capability policy is documented in
[EXTENSION_CAPABILITIES.md](./EXTENSION_CAPABILITIES.md).
Extension provenance policy is documented in
[EXTENSION_PROVENANCE.md](./EXTENSION_PROVENANCE.md).

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
  - `AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS=true` is now only a legacy
    operator-visible setting
  - it does not re-enable third-party in-process Python imports
  - third-party plugin nodes still execute only through the isolated
    plugin-host boundary

## Stable HTTP Surfaces

- `GET /api/version`
  Returns package compatibility metadata, API version metadata, runtime-only
  surface state, the live trusted-Python execution summary, the extension
  provenance summary, and the
  machine-readable public contract inventory.
- `GET /health`
  Returns operator-visible liveness, degraded-runtime conditions, the live
  trusted-Python execution inventory, and the extension provenance summary.
- `GET /ready`
  Returns operator-visible readiness with strict dependency and unsafe-degraded
  checks for the active deployment profile. It also reports the current
  trusted-Python execution inventory and extension provenance summary. It does
  not certify extension isolation or third-party code trust.
- `GET /platform/syscalls`
  Returns the versioned syscall catalog with per-entry `stable` and
  `deprecated` markers.
- `POST /platform/syscall`
  Dispatches a versioned syscall through the public syscall envelope.

Downstream note:

- the minimum stable downstream contract should be interpreted narrowly first
- `CROSS_REPO_COMPATIBILITY.md` is authoritative for what SDK and UI should treat as compatibility commitments

## Conditionally Stable HTTP Surfaces

These are the core execution APIs shipped at v1.0.0. They are runtime-owned,
actively used by SDK and platform UI, and unlikely to break. Their HTTP shape
is not formally frozen — callers should prefer `aindy-sdk` wrappers over
direct HTTP reliance.

- `/apps/memory/*`
  Memory read, write, search, and trace surfaces. Mounted at `prefix=/apps`
  via `memory_router` (`prefix=/memory`).
- `/apps/coordination/*`
  Multi-agent coordination surfaces. Mounted at `prefix=/apps` via
  `coordination_router` (`prefix=/coordination`).

> **`/apps/agent/*` was listed here and is not runtime-owned** — corrected 2026-08-06.
> `APP_ROUTERS` in `AINDY/routes/__init__.py` contains exactly `memory_router` and
> `coordination_router`; the file's own comment records that `agent_router` **moved to the
> plugin layer**. A bare runtime does not serve `/apps/agent/*` at all — a plugin bootstrap
> such as `aindy-apps-monolith` must be mounted for those routes to exist, which is what
> `README.md` says under *Building apps on aindy-runtime*. Listing them here granted a
> runtime stability tier to routes the runtime does not ship.
>
> They still appear on any plugin-loaded deployment (verified: `/apps/agent/run` answers
> there), so the mistake is invisible unless you boot the runtime alone.

## Experimental HTTP Surfaces

- `GET /health/sandbox`
  Returns the full sandbox posture as a structured JSON object: runner type,
  assurance class, requirement satisfaction, platform capability matrix,
  verification posture, trusted-Python execution inventory, plugin host
  attestation, and active runtime conditions. Intended for integrators and
  operators who need sandbox status without parsing the full `/health` blob.
  Also the backing source for the `aindy-runtime sandbox` CLI subcommand.
- `POST /watcher/signals` / `GET /watcher/signals`
  Watcher signal ingestion and query. Signal emission uses API key auth
  (headless client process). Mounted in `ROOT_ROUTERS` at `/watcher`.
  The watcher client process ships in `aindy-sdk` as `aindy_sdk.watcher`.
- `/platform/flows*`
  Dynamic flow management remains experimental.
- `/platform/nodes*`
  Dynamic external node management remains experimental.
- `/platform/nodus*`
  Nodus upload and script-management routes remain experimental.
- `/platform/webhooks*`
  Runtime-owned, but not yet declared stable.

Experimental means:

- available and runtime-owned enough to discuss
- not automatically safe for SDK/UI compatibility dependence
- not automatically promoted by runtime-only boot or route visibility

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

> ### ⚠️ Two sources of truth for "stable", and they disagree
>
> Recorded 2026-08-06. This is a **code-level inconsistency**, not a documentation error —
> the lists above are faithful to the registry flag, and `CROSS_REPO_COMPATIBILITY.md` /
> `SDK_CONTRACT.md` are faithful to the other source. Each doc is internally correct, which
> is precisely why the conflict survived: cross-checking one against another finds
> disagreement and no obvious arbiter.
>
> | Source | What it is | Count |
> |---|---|---|
> | `SyscallEntry.stable` | what `GET /platform/syscalls` advertises to every caller | 15 |
> | `_STABLE_SYSCALLS` in `tests/unit/test_cross_repo_compatibility.py` | the CI-enforced no-rename-before-major contract | 13 |
>
> They agree on 11 of 17. The six that differ:
>
> - **In the contract, flagged experimental:** `sys.v1.memory.tree`, `sys.v1.memory.trace`.
>   An SDK author who honours the `stable` field — as this document instructs — will avoid
>   depending on them, while CI promises they will not be renamed.
> - **Flagged stable, absent from the contract:** `sys.v1.agent.cancel`,
>   `sys.v1.agent.simulate`, `sys.v1.agent.undo`, `sys.v1.execution.get`. These advertise
>   stability to every caller with no CI check guarding the name.
>
> **Not resolved here, deliberately.** Reconciling them is a code change — either set the
> four `stable` flags' entries into `_STABLE_SYSCALLS`, or correct the flags — and which way
> it should go is a product decision about what has actually been promised. Until then, treat
> `_STABLE_SYSCALLS` as the stronger guarantee: it is the one with a test behind it.

## Extension Registration Surfaces

These are externally consumable integration points, but they are still
experimental. Each surface belongs to one of two isolation tiers:

- **Tier 1 — kernel-resident, trusted-operator**: manifest bootstrap modules
  and declarative entries for `runtime-built-in` and `first-party-app` code.
  These run in the main interpreter by design, as intentional kernel code.
- **Tier 2 — externalized, plugin-host isolated**: dynamic plugin nodes for
  `external-third-party` code (and `first-party-app` plugin nodes), webhook
  nodes, and dynamic flows. These always execute behind a subprocess or network
  boundary.

Surfaces in scope:

- manifest bootstrap modules loaded through `AINDY.platform_layer.registry`
- manifest declarative extension entries loaded through
  `AINDY.platform_layer.registry`
- `AINDY.platform_layer.registry.register_*` helper shapes
- `AINDY.agents.tool_registry.register_tool`
- dynamic plugin nodes via
  `AINDY.platform_layer.node_registry.register_external_node(type="plugin")`
- webhook nodes via
  `AINDY.platform_layer.node_registry.register_external_node(type="webhook")`
- dynamic flows via `AINDY.runtime.flow_registry.register_dynamic_flow`

Reason for the experimental classification:

- they are validated and runtime-owned, but the exact registration shapes and
  extension boundaries are still moving
- the runtime does not claim in-process isolation for Tier 2 extension surfaces;
  Tier 1 surfaces are kernel-resident by design, not by omission

Ownership model:

- `runtime-built-in`: Tier 1 — runtime-owned kernel callables and bootstrap
  modules under `AINDY.*`
- `first-party-app`: Tier 1 — trusted app-owned integrations under `apps.*`
- `external-third-party`: Tier 2 — explicitly separate third-party integrations,
  always externalized behind the plugin-host boundary

The runtime-owned manifest may load only `runtime-built-in` entries. App and
third-party ownership classes must stay out of the runtime-only profile.
External onboarding should use declarative manifest `extensions` entries or the
runtime registration APIs instead of third-party bootstrap modules.

Operator visibility:

- `GET /api/version` publishes the current trusted-Python execution summary
- `GET /health` publishes the full trusted-Python inventory
- `GET /ready` publishes the current trusted-Python inventory alongside
  dependency readiness checks

If the external Python override is enabled, the runtime surfaces that state as
operator-visible configuration state. Health and readiness still describe
dependency state, but they also show that third-party execution remains behind
the isolated worker boundary.

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
