---
title: "Extension ABI"
last_verified: "2026-05-20"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Extension ABI

This document defines the runtime-owned extension ABI policy.

## Policy

- stable surface:
  - manifest ABI `aindy.extension.manifest/v1`
- experimental surfaces:
  - dynamic node registration ABI `aindy.extension.node-registration/v1alpha1`
  - webhook subscription ABI `aindy.extension.webhook-registration/v1alpha1`
  - dynamic flow registration ABI `aindy.extension.flow-registration/v1alpha1`
  - agent tool registration ABI `aindy.extension.agent-tool-registration/v1alpha1`
  - planner backend registration ABI `aindy.extension.planner-backend-registration/v1alpha1`

The machine-readable policy is exposed in `GET /api/version` under
`public_contract.extensions.abi`.

## Compatibility Rules

- manifest ABI `v1` is the stable manifest contract
- legacy unversioned manifests are still accepted for backward compatibility
- unsupported manifest ABI versions are rejected at load time
- manifest profiles may carry:
  - trusted `plugins` bootstrap entries for runtime-built-in or first-party code
  - declarative `extensions` entries for external onboarding without bootstrap execution
- unsupported dynamic registration ABI versions are rejected at request validation time
- experimental ABI versions are explicit compatibility markers, not stability claims

## Operator Meaning

- a versioned manifest means the runtime can validate the manifest shape before trusted bootstrap
  or declarative extension onboarding
- a versioned registration payload means the runtime can reject incompatible clients clearly
- experimental ABI markers do not imply long-term compatibility across minor releases
