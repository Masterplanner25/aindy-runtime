---
title: "Extension Trust Model"
last_verified: "2026-05-18"
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
  prefix allowlisting through `AINDY_EXTERNAL_BOOTSTRAP_PREFIXES`.

Ownership is separate from trust:

- `runtime-built-in` and `first-party-app` Python modules are both trusted
  in-process code execution
- `external-third-party` may still be trusted by a deployment, but the runtime
  tracks it separately and does not collapse it into the app/runtime buckets

## Trusted Extension Classes

These extension classes are trusted code execution:

- manifest bootstrap modules loaded by
  [AINDY/platform_layer/registry.py](/abs/path/C:/dev/aindy-runtime/AINDY/platform_layer/registry.py)
- in-process dynamic plugin nodes loaded by
  [AINDY/platform_layer/node_registry.py](/abs/path/C:/dev/aindy-runtime/AINDY/platform_layer/node_registry.py)

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
- plugin node handlers are loaded only from `AINDY/plugins/nodes/`
- plugin node loading no longer mutates `sys.path`
- plugin node handlers must expose a callable compatible with the node contract

## Untrusted Or Less-Trusted Extension Classes

These extension classes are treated as external data/config integrations, not
trusted Python code:

- webhook nodes registered through `/platform/nodes/register` with `type=webhook`
- webhook subscriptions registered through `/platform/webhooks`
- dynamic flows registered through `/platform/flows`

Properties:

- webhook targets are outside the process and receive serialized payloads only
- dynamic flows are data-only graph definitions and do not inject Python
- runtime metadata now records their ownership class separately from their trust
  class

Current hardening:

- outbound webhook targets must be `http://` or `https://`
- embedded URL credentials are rejected
- private and loopback targets are rejected by default to reduce SSRF-style
  mistakes; override only with `AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS=true`
- dynamic flow definitions are limited to data-only shapes with size and
  duplicate checks

## File-Level Threat Model

- [AINDY/platform_layer/registry.py](/abs/path/C:/dev/aindy-runtime/AINDY/platform_layer/registry.py)
  Trusted bootstrap import path. Risk: manifest-selected arbitrary Python import.
  Hardening: trusted-prefix validation before import.
- [AINDY/platform_layer/node_registry.py](/abs/path/C:/dev/aindy-runtime/AINDY/platform_layer/node_registry.py)
  Dynamic node registration path. Risk: in-process Python import, historical
  `sys.path` mutation, unsafe webhook targets. Hardening: file-bound module
  loading, callable-shape validation, webhook URL policy.
- [AINDY/platform_layer/event_service.py](/abs/path/C:/dev/aindy-runtime/AINDY/platform_layer/event_service.py)
  Outbound webhook subscription dispatch. Risk: SSRF or accidental delivery into
  private control planes. Hardening: outbound target validation.
- [AINDY/runtime/flow_registry.py](/abs/path/C:/dev/aindy-runtime/AINDY/runtime/flow_registry.py)
  Dynamic flow definition path. Risk: oversized or malformed data-driven
  orchestration that references existing runtime nodes. Hardening: shape, size,
  and duplicate validation. No code sandbox claim.

## Operational Guidance

- Treat manifest bootstrap modules and dynamic plugin nodes as trusted code
  deployment, not user content.
- Prefer webhook nodes or dynamic flows when a use case can stay data-driven.
- If you need real isolation for untrusted extension code, it must be moved out
  of process into a separately sandboxed execution environment.
