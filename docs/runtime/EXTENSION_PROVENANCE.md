---
title: "Extension Provenance"
last_verified: "2026-05-20"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Extension Provenance

This document defines the runtime-owned provenance and integrity contract for
deployable extensions.

## What The Runtime Verifies

The runtime records and exposes:

- `extension_id`
- `version`
- `source_type`
- `source_ref`
- observed `sha256` integrity where the runtime can compute artifact bytes
- verification status

Verification classes:

- `declared-and-verified`
  The operator supplied provenance, including a `sha256`, and the runtime
  matched it against local source bytes or a canonical registration payload.
- `runtime-derived`
  The runtime derived provenance from local trusted source paths and computed
  the observed `sha256` itself.
- `legacy-restore-unverified`
  A persisted legacy external registration was restored without stored
  provenance. The runtime can still identify the loaded registration, but it
  was not admitted through the current provenance gate.

## Current Policy

- `runtime-built-in`
  Provenance may be runtime-derived. Source type is typically
  `runtime-package`.
- `first-party-app`
  Provenance may be runtime-derived. Source type is typically
  `first-party-source-tree`.
- `external-third-party`
  New registrations must declare provenance and integrity for:
  - dynamic plugin nodes
  - webhook nodes
  - webhook subscriptions
  - dynamic flows

The runtime does not implement artifact signing or a remote trust registry.
There is no PKI-backed verification in this repo today.

## Operator Surfaces

Extension provenance is visible through:

- `GET /api/version`
  Runtime summary plus public provenance policy.
- `GET /health`
  Live provenance inventory summary.
- `GET /ready`
  Provenance inventory inside readiness checks.
- runtime registration/list APIs
  Per-extension metadata includes the normalized `provenance` object.

## Integrity Sources

- trusted Python modules
  `sha256` of the loaded source file
- external plugin nodes
  `sha256` of the plugin source file, verified before admission
- webhook nodes and webhook subscriptions
  `sha256` of the canonical registration payload
- dynamic flows
  `sha256` of the canonical flow registration payload

## What Is Not Claimed

- The runtime does not claim signed artifact verification.
- The runtime does not claim a remote package registry or publisher identity
  service.
- Provenance visibility does not imply sandboxing.
