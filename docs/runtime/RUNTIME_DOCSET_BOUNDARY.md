---
title: "Runtime Docset Boundary (migration complete)"
last_verified: "2026-08-13"
api_version: "1.0"
status: complete
owner: "platform-team"
---
# Runtime Docset Boundary (migration complete)

> ## ✅ This plan is finished — verified 2026-08-13
>
> **`status: complete`, not `current`.** This was a one-time migration plan for the repo split,
> written 2026-05-10 and executed by `0d5d382 Initial runtime repo extraction` on 2026-05-17. All
> three sections have been checked against both repos and all three are done:
>
> | Section | State |
> |---|---|
> | Move To `aindy-runtime` | ✅ **10 of 10** present in `docs/runtime/` |
> | Move To `aindy-apps-monolith` | ✅ present in `aindy-apps-monolith`, absent here |
> | Shared Or Split Later | ✅ **resolved — see below** |
>
> The third section resolved in a way the plan did not anticipate. It expected each shared
> document to be *split* into runtime and app halves. Instead the runtime grew its **own**
> companions under different names, which satisfies the intent without touching the monolith
> files:
>
> | Planned split | What the runtime actually has |
> |---|---|
> | `architecture/BOOT_PROFILES.md` | `DEPLOYMENT_PROFILES.md`, `PROFILE_SUPPORT_MATRIX.md` |
> | `architecture/ARCHITECTURE_MAP.md` | `ARCHITECTURE.md`, `RUNTIME_MODULE_MAP.md` |
> | `architecture/PLUGIN_REGISTRY_PATTERN.md` | `EXTENSION_ABI.md`, `EXTENSION_CAPABILITIES.md`, `EXTENSION_PROVENANCE.md`, `EXTENSION_TRUST_MODEL.md` |
> | `platform/interfaces/API_CONTRACTS.md` | `PUBLIC_API_CONTRACT.md`, `PUBLIC_RUNTIME_SURFACES.md`, `ROUTE_OWNERSHIP_INVENTORY.md` |
>
> **What is kept, and what is not.** The *ownership rules* below — the per-repo rules under each
> section, and the Current Boundary Notes — are still in force and are still the reference for
> deciding which repo a new document belongs to. The **"Move To …" lists are history**: they
> record where things went in May 2026, not a queue of pending work. Do not read them as
> instructions.
>
> On the status value: this repo previously used only `current` and `outdated`. Neither fit — the
> rules are not outdated, and the plan is not ongoing — so `complete` is introduced here
> deliberately. `Runtime Docs Validation` checks only that the five frontmatter keys are
> *present*, so no tooling depends on the value.

> **Paths below are in `aindy-apps-monolith`, not this repo.** They were written as
> relative links, which resolve nowhere here — corrected 2026-08-13 to plain paths so the
> boundary this document defines is visible in the citation itself.

This document defined the documentation boundary between the extracted
`aindy-runtime` and `aindy-apps-monolith` repos.

It remains the ownership map for the split repos — the *rules*, not the lists:

- docs under **Move To `aindy-runtime`** travel with the runtime repo and avoid
  app-monolith assumptions
- docs under **Move To `aindy-apps-monolith`** are app-owned and are not runtime
  contracts
- docs under **Shared Or Split Later** spanned both repos; each has since been given a
  runtime-owned companion (see the table above)

The runtime-only operating contract remains
[Runtime-Only Deployment](./RUNTIME_ONLY_DEPLOYMENT.md).

## Move To `aindy-runtime` — ✅ DONE (10/10, verified 2026-08-13)

These docs describe runtime-owned behavior, public contracts, or deployment
surfaces that must stand on their own with no `apps/` tree present.

- [RUNTIME_ONLY_DEPLOYMENT.md](./RUNTIME_ONLY_DEPLOYMENT.md)
- [AGENT_RUNTIME.md](./AGENT_RUNTIME.md)
- [SYSCALL_SYSTEM.md](./SYSCALL_SYSTEM.md)
- [PUBLIC_API_CONTRACT.md](./PUBLIC_API_CONTRACT.md)
- [DB_OWNERSHIP_CONTRACT.md](./DB_OWNERSHIP_CONTRACT.md)
- [REPO_COMPATIBILITY_POLICY.md](./REPO_COMPATIBILITY_POLICY.md)
- [EXECUTION_CONTRACT.md](./EXECUTION_CONTRACT.md)
- [OS_ISOLATION_LAYER.md](./OS_ISOLATION_LAYER.md)
- [MEMORY_ADDRESS_SPACE.md](./MEMORY_ADDRESS_SPACE.md)
- [RUNTIME_BEHAVIOR.md](./RUNTIME_BEHAVIOR.md)

Runtime-repo rule:
- these docs may describe optional plugin enrichment, but their normative
  contracts must remain valid when no app plugin is installed

## Move To `aindy-apps-monolith` — ✅ DONE (verified 2026-08-13)

These docs describe app-domain features, enrichment behavior, or monolith-only
user-facing capabilities.

- `apps/APPS_MONOLITH_REPO_SHAPE.md`
- `apps/CLIENT_OWNERSHIP.md`
- `apps/AGENTICS.md`
- `docs/apps/*` domain guides such as analytics, freelancing, rippletrace, and
  other app feature docs
- app route and app service behavior currently cataloged under monolith-facing
  API and architecture references

Apps-repo rule:
- app docs may depend on runtime contracts, but must not redefine runtime
  ownership or imply that app bootstrap is part of the runtime baseline

## Shared Or Split Later — ✅ RESOLVED (verified 2026-08-13)

These docs spanned both runtime and app concerns. Each now has a runtime-owned companion in
`docs/runtime/` (table at the top of this file); the monolith copies stay app-owned. The
per-document split guidance below is retained as the reasoning behind those companions.

- `architecture/BOOT_PROFILES.md`
- `architecture/ARCHITECTURE_MAP.md`
- `architecture/PLUGIN_REGISTRY_PATTERN.md`
- `platform/interfaces/API_CONTRACTS.md`

Split guidance:
- `BOOT_PROFILES.md` is shared until both repos have their own startup docs;
  after the split, runtime-only boot guidance should live in the runtime repo
  and app-profile boot guidance should live in the apps repo
- `ARCHITECTURE_MAP.md` currently explains both `AINDY/` and `apps/`; it
  should later become separate runtime and apps architecture maps
- `PLUGIN_REGISTRY_PATTERN.md` describes the registration contract between the
  two repos and may remain shared conceptually, but examples should avoid
  implying one repo-root manifest
- `API_CONTRACTS.md` is currently a monolith HTTP inventory; the runtime repo
  should eventually carry only runtime-owned routes and interfaces, while the
  apps repo carries app-route inventories

## Current Boundary Notes

- Runtime-owned `/apps/*` routes are still runtime docs, not app docs. As of 2026-08-13
  `APP_ROUTERS` in `AINDY/routes/__init__.py` is exactly **`memory_router`** and
  **`coordination_router`**.

  > **Corrected 2026-08-13.** This bullet listed *"the agent, memory, watcher, and coordination
  > surfaces"*. Two of the four are wrong now:
  >
  > - **`agent_router` moved to the plugin layer.** `AINDY/routes/agent_router.py` still exists
  >   but is unregistered and its docstring says so; the canonical implementation is
  >   `apps/agent/routes/agent_router.py` in `aindy-apps-monolith`. Same correction as
  >   `PUBLIC_RUNTIME_SURFACES.md` (2026-08-06) and `EXECUTION_CONTRACT.md`.
  > - **`watcher_router` is not an `/apps/*` route at all.** It is in `ROOT_ROUTERS`, serving
  >   `/watcher/signals` under API-key auth.
  >
  > `memory_metrics_router` and `memory_trace_router` also moved to the plugin layer. This is the
  > single most-repeated error in the docset: a router file left in `AINDY/routes/` but absent
  > from `APP_ROUTERS` reads as runtime-owned until you boot the runtime with no plugins.
- App-owned `/apps/*` routes remain app docs even though they share the `/apps`
  URL prefix.
- Runtime docs may reference app-profile behavior only to explain what is
  intentionally unavailable without plugins.
- App docs should reference runtime contracts instead of copying runtime rules.
