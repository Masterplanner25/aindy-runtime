---
title: "Extension Capabilities"
last_verified: "2026-05-20"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Extension Capabilities

This document defines the runtime-owned capability model for extension
execution surfaces.

## Scope

Capability confinement currently applies to external third-party execution
surfaces that cross the isolated plugin-host or contract-driven webhook
boundary.

It does not apply to trusted internal Python code:

- `runtime-built-in`
- `first-party-app`

Those classes remain trusted internal code execution, not capability-confined
third-party extensions.

## Capability Set

Runtime-managed extension capabilities:

- `memory.read`
- `memory.write`
- `flow.run`
- `event.emit`
- `tool.invoke`
- `outbound.http`

Not exposed as extension capabilities:

- `secret.read`
- `config.read`

## Enforcement

- external third-party dynamic plugin nodes:
  - authority model: `isolated-explicit-capabilities`
  - default runtime capabilities: none
  - live DB/session/runtime objects are not passed into plugin code
  - internal `AINDY.*` imports are blocked except the extension runtime API
  - outbound network is blocked unless `outbound.http` is granted
  - private/loopback literal targets are blocked by default even when `outbound.http` is granted
  - standard Python file APIs are restricted to read-only approved roots needed for execution
  - environment exposure is allowlist-only: `PATH`, `PATHEXT`, `SYSTEMROOT`, `TEMP`, `TMP`, `WINDIR`
  - secrets are not injected into plugin-visible environment variables
- webhook nodes:
  - authority model: `contract-driven-surface`
  - intrinsic capability: `outbound.http`
- webhook subscriptions:
  - authority model: `contract-driven-surface`
  - intrinsic capability: `outbound.http`
- manifest bootstrap:
  - authority model: `trusted-internal-ambient-authority`
  - not capability-confined

## Operator Visibility

- `GET /api/version` publishes the machine-readable capability model under
  `public_contract.extensions.capability_model`
- dynamic node and webhook metadata publish:
  - `authority_model`
  - `granted_capabilities`
  - `resource_access`
