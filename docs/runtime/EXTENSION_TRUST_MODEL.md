---
title: "Extension Trust Model"
last_verified: "2026-05-20"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Extension Trust Model

This document defines the runtime extension trust and ownership boundary as it
exists today.

Important limitation:

- `aindy-runtime` does not provide true in-process sandboxing for Python extensions.
- Any manifest bootstrap module or dynamic plugin node that is imported into the
  interpreter executes with normal Python process privileges.
- The hardening in this repo reduces accidental trust expansion and obvious
  unsafe inputs, but it does not turn Python imports into an isolation boundary.

## Ownership Classes

The runtime now distinguishes three ownership classes:

- `runtime-built-in`
  Runtime-owned extensions shipped under `AINDY.*`. These are trusted internal
  extensions and are the only class allowed in the runtime-owned manifest.
- `first-party-app`
  Trusted app-owned integrations loaded from `apps.*`. These remain first-party
  code, but they are not runtime-owned and are excluded from the runtime-only
  profile.
- `external-third-party`
  Third-party or non-monolith extensions. These are never treated as
  runtime-owned. Python bootstrap/import paths in this class require explicit
  prefix allowlisting through `AINDY_EXTERNAL_BOOTSTRAP_PREFIXES`, and remain
  blocked from in-process execution unless explicitly trusted.

Ownership is separate from trust:

- `runtime-built-in` and `first-party-app` Python modules are both trusted
  in-process code execution
- `external-third-party` is not treated as trusted in-process code by default
- webhook nodes, webhook subscriptions, and dynamic flows are contract-driven
  integrations or data-only registrations, not Python sandbox boundaries

## Trusted Extension Classes

These extension classes are trusted code execution:

- manifest bootstrap modules loaded by
  [AINDY/platform_layer/registry.py](/abs/path/C:/dev/aindy-runtime/AINDY/platform_layer/registry.py)
  when `owner_class` is `runtime-built-in` or `first-party-app`
- dynamic plugin nodes loaded by
  [AINDY/platform_layer/node_registry.py](/abs/path/C:/dev/aindy-runtime/AINDY/platform_layer/node_registry.py)
  when `owner_class` is `runtime-built-in` or `first-party-app`

Properties:

- they can execute arbitrary Python during import or call time
- they can mutate process state
- they can violate runtime invariants if the code itself is malicious or broken

Current hardening:

- manifest bootstrap modules are restricted to trusted module prefixes
  (`AINDY.` and `apps.` by default, configurable through
  `AINDY_TRUSTED_BOOTSTRAP_PREFIXES`)
- runtime-owned manifests may declare only `runtime-built-in` bootstrap entries
- external third-party Python bootstrap entries require explicit prefixes from
  `AINDY_EXTERNAL_BOOTSTRAP_PREFIXES`
- external third-party Python bootstrap modules are blocked from import and
  bootstrap execution by default, even when their prefix is allowlisted
- external third-party dynamic plugin nodes are blocked from in-process loading
  by default
- the only override is `AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS=true`, which
  marks the deployment as explicitly trusting that external Python code
- production use of that override also requires
  `AINDY_ACK_UNSANDBOXED_EXTERNAL_PYTHON=true`
- when the override is active, `/health`, `/ready`, and `/api/version` expose
  that state explicitly so operators can see that unsandboxed external Python
  execution is enabled
- `/health`, `/ready`, and `/api/version` also publish a live trusted-Python
  inventory covering:
  - loaded manifest bootstrap modules
  - bootstrap registrations published by those modules
  - trusted dynamic plugin nodes
  - ownership-class counts distinguishing `runtime-built-in` from
    `first-party-app`
- plugin node handlers are loaded only from `AINDY/plugins/nodes/`
- plugin node loading no longer mutates `sys.path`
- plugin node handlers must expose a callable compatible with the node contract

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
  loading, callable-shape validation, webhook URL policy, and blocking of
  external third-party plugin-node imports by default.
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
  first-party-app code is trusted app-owned code loaded into the same process.
- Treat external third-party Python extensions as blocked by default unless the
  deployment owner explicitly sets `AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS=true`.
- Treat that override as an operator exception, not a normal production mode.
  It enables trusted in-process code execution and does not create isolation.
- In production, pair the override with
  `AINDY_ACK_UNSANDBOXED_EXTERNAL_PYTHON=true` or startup will fail closed.
- Prefer webhook nodes or dynamic flows when a use case can stay data-driven.
- If you need real isolation for untrusted extension code, it must be moved out
  of process into a separately sandboxed execution environment.
