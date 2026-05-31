---
title: "Repo Compatibility Policy"
last_verified: "2026-05-29"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Repo Compatibility Policy


This document defines the high-level compatibility policy across the current
repo split:

- `aindy-runtime`
- `aindy-sdk`
- `aindy-ui-kit`

## Policy

Compatibility is expressed through two layers:

- normal Python package dependency constraints on `aindy-runtime`
- the runtime HTTP/API contract version exposed through `GET /api/version`

The runtime does not attempt active negotiation with downstream repos.
Downstream consumers declare which runtime versions they support through package
constraints and by consuming only documented stable runtime contracts.

## Required Downstream Declaration

Downstream Python repos such as `aindy-sdk` should declare `aindy-runtime`
using a PEP 440 range with an explicit upper bound before the next MAJOR
release.

Recommended pattern:

```toml
dependencies = [
  "aindy-runtime>=1.0,<2.0",
]
```

Rules:

- use an explicit upper bound at the next runtime MAJOR version
- widening the supported range is a downstream release decision
- do not depend on unbounded `>=` requirements for runtime

## Runtime Compatibility Boundary

The runtime publishes compatibility metadata through `GET /api/version`:

- runtime package identity and version
- runtime/API contract version information
- compatibility metadata intended for downstream consumers

Older `apps_repo_contract.*` naming in the metadata should be treated as a
legacy shape until the compatibility payload is fully realigned to the current
repo split.

This metadata is descriptive, not a handshake protocol. It tells operators and
tooling what compatibility shape downstream repos should declare and consume.

## Version Meaning

- runtime package version:
  the installable `aindy-runtime` package version from packaging metadata
- API version:
  the runtime HTTP/API contract version exposed at `/api/version`

Compatibility expectations:

- runtime package MAJOR changes may break downstream dependency contracts
- API MAJOR changes may break SDK/UI HTTP assumptions
- MINOR and PATCH changes are expected to remain compatible within the same
  MAJOR series unless explicitly documented otherwise

## Compatibility Layers

This repo policy should be interpreted together with the layered model in
`CROSS_REPO_COMPATIBILITY.md`:

- package compatibility
- API compatibility
- behavioral/status compatibility
- stable-vs-incidental downstream dependence

This file does not replace that model. It gives the high-level policy shape.

## Operational Guidance

When the runtime repo releases a new version:

1. downstream repos update their `aindy-runtime` dependency within the
   supported range where needed
2. downstream repos verify stable runtime contract compatibility against that
   runtime version
3. if a downstream repo needs newly introduced runtime features, it widens or
   bumps its lower bound deliberately

During staged release preparation before publication:

1. the runtime repo builds artifacts and runs `twine check`
2. the runtime repo verifies `/api/version` compatibility metadata for the new version
3. downstream repos confirm their bounded dependency declarations and stable
   runtime assumptions still match the supported compatibility model

When the runtime crosses a MAJOR version boundary:

- the runtime must document the breaking change
- downstream repos must update dependency ranges explicitly
- compatibility should be treated as opt-in, not assumed

## Reading Rule

If this file and `CROSS_REPO_COMPATIBILITY.md` differ in claim strength or
detail, prefer `CROSS_REPO_COMPATIBILITY.md`.
