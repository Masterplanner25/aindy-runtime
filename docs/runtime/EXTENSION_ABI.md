---
title: "Extension ABI"
last_verified: "2026-06-15"
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

## Deprecation and Forward-Compatibility Policy

**Stable ABI versions** (currently: `aindy.extension.manifest/v1`) follow a minimum two-minor-release
support window:

1. When a new stable ABI version ships (e.g. `manifest/v2`), the previous stable version
   (`manifest/v1`) enters the deprecation window but continues to be accepted.
2. The deprecated version is flagged in `GET /api/version` under
   `public_contract.extensions.abi.deprecated_versions` from that release onward.
3. The deprecated version is removed no earlier than two minor runtime releases after the
   newer stable version shipped (e.g. if `v2` ships in `1.4.0`, `v1` is not removed before `1.6.0`).
4. Removal is announced in the runtime changelog and the `EXTENSION_ABI.md` policy doc is
   updated to reflect the new `ABI_VERSIONS` set before the release that removes it.

**Experimental ABI versions** (all `v1alpha*` surfaces) carry no support window. They may be
changed or removed in any minor or patch release without a deprecation period. Plugin authors
must not depend on experimental surfaces in production extensions.

**Trigger for this policy:** before any ABI version other than `manifest/v1` is promoted to
stable or before any experimental surface is promoted to stable.
