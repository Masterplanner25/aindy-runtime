# Runtime Stability Index

> Authored by Codex during non coding session. Needs review before repo commit and push.


This document classifies the major surfaces of `aindy-runtime` into one of four stability levels:

- `stable`
- `conditionally stable`
- `experimental`
- `internal only`

Its purpose is to reduce ambiguity about what downstream repos, operators, and maintainers may rely on.

This is not a full API reference. It is a stability map.

---

## Canonical Rule

A runtime surface is not stable just because it exists.

A surface is stable only when:

- it is intentionally exposed
- its semantics are documented
- downstream consumers are allowed to depend on it
- breaking it would be treated as a real compatibility event

If those conditions are not met, the surface should not be treated as stable.

---

## Stability Levels

### `stable`
Use when:

- the surface is intentionally public
- the behavior is documented
- downstream repos may rely on it
- changes require explicit compatibility review

### `conditionally stable`
Use when:

- the surface is intended for use
- some semantics are profile-dependent or still tightening
- consumers may rely on it within documented limits
- changes require care, but the full contract is narrower than the raw surface suggests

### `experimental`
Use when:

- the surface exists for active iteration
- semantics may still move
- downstream consumers should avoid depending on it unless explicitly opting in
- breakage is possible without major-version treatment

### `internal only`
Use when:

- the surface is implementation detail
- it is not a supported consumer contract
- other repos should not rely on it
- refactors may change it freely within normal engineering discipline

---

## Stable Surfaces

These are the strongest current candidates for stable treatment.

### Runtime Metadata and Operator Identity
| Surface | Stability | Why |
|---|---|---|
| `/api/version` | `stable` | Runtime identity/version metadata is a core operator and downstream contract. |
| runtime package identity and installable entrypoint | `stable` | The runtime must remain installable and identifiable in a predictable way. |

### Runtime Liveness and Readiness Contracts
| Surface | Stability | Why |
|---|---|---|
| `/health` | `stable` | Public liveness/health posture is a core operational contract. |
| `/ready` | `stable` | Readiness truth is a core deployment and orchestration contract. |
| health vs readiness distinction | `stable` | Downstream systems and operators must be able to rely on the difference. |

### Runtime Deployment Identity
| Surface | Stability | Why |
|---|---|---|
| runtime-only deployment posture | `stable` | The repo explicitly claims this deployment mode. |
| documented deployment profile semantics | `conditionally stable` | Important contract, but still profile-sensitive and operationally tightening. |

---

## Conditionally Stable Surfaces

These are intended for real use, but the contract is narrower than the total implementation surface.

### Public Runtime Status Semantics
| Surface | Stability | Why |
|---|---|---|
| degraded/not-ready status semantics | `conditionally stable` | Important and already relied upon, but some reason details and edge classifications are still tightening. |
| runtime condition visibility | `conditionally stable` | Runtime conditions are meaningful, but exact condition sets and payload details may still evolve. |

### Syscall System
| Surface | Stability | Why |
|---|---|---|
| `sys.v1.*` baseline contract | `conditionally stable` | The repo documents a stable baseline, but per-entry stability remains authoritative. |
| required runtime syscalls | `conditionally stable` | Operationally important, but exact required sets may evolve with runtime profile and platform changes. |
| syscall capability enforcement model | `conditionally stable` | A real runtime contract, but not every implementation detail is public or frozen. |

### Execution and Resume Semantics
| Surface | Stability | Why |
|---|---|---|
| wait/resume as a runtime capability | `conditionally stable` | Central to runtime behavior, but deeper invariants are still being formalized. |
| recovery and rehydration behavior | `conditionally stable` | Intended behavior exists, but recovery guarantees need tighter verification language. |
| event-bus-backed cross-instance resume for supported profiles | `conditionally stable` | Real feature, but profile-dependent and degraded-mode-sensitive. |

### Extension and Trust Contracts
| Surface | Stability | Why |
|---|---|---|
| manifest-level extension ABI v1 | `conditionally stable` | Explicitly documented as stable or closest to stable. |
| extension trust posture for trusted-internal use | `conditionally stable` | Real and documented, but should be consumed with strict scope limits. |

---

## Experimental Surfaces

These are real surfaces, but should not yet be treated as broadly dependable contracts.

### Dynamic Extension and Registration Paths
| Surface | Stability | Why |
|---|---|---|
| dynamic extension registration APIs | `experimental` | The repo already signals that these are not fully frozen. |
| emerging extension capability surfaces beyond narrowly documented ABI | `experimental` | The trust and capability story is still tightening. |

### Broader Execution Platform Surfaces
| Surface | Stability | Why |
|---|---|---|
| broader orchestration surfaces not explicitly listed as public runtime surfaces | `experimental` | Important internally, but not all of it should be treated as downstream contract. |
| route groups inherited from earlier platform/monolith structure | `experimental` | Presence in the repo is not proof of long-term stable ownership. |

### Cross-Repo Consumer Assumptions Beyond Core Status Metadata
| Surface | Stability | Why |
|---|---|---|
| SDK/UI reliance on incidental runtime payload details | `experimental` | These dependencies may exist in practice but should not be normalized as stable contracts. |

---

## Internal-Only Surfaces

These should not be treated as supported external contracts.

### Kernel and Runtime Internals
| Surface | Stability | Why |
|---|---|---|
| `AINDY/kernel/*` implementation structure | `internal only` | Critical internals, but not a public consumer contract. |
| scheduler queue structures and buffering internals | `internal only` | Behavior matters; internal data structures do not. |
| exact event-bus implementation details | `internal only` | Public behavior may be documented; internal mechanism may change. |
| startup ordering implementation details | `internal only` | Startup guarantees may become contractual; raw code order is not. |
| internal recovery/watchdog mechanics | `internal only` | Recovery outcomes matter more than implementation shape. |

### Internal Python Modules Not Declared Public
| Surface | Stability | Why |
|---|---|---|
| internal module paths under `AINDY/` not listed as public runtime surfaces | `internal only` | Other repos should not rely on import-time coincidence. |
| implementation-specific route payload fields not documented as stable | `internal only` | May change during refactor or cleanup. |
| internal config/bootstrap helpers | `internal only` | Operationally important, but not downstream contract. |

---

## Surface Classification Rules

Use these rules when classifying new or existing surfaces.

### Classify As `stable` When
- breaking it would break a supported downstream contract
- operators or downstream repos are expected to rely on it
- the runtime intends to preserve it across routine refactors

### Classify As `conditionally stable` When
- consumers may rely on it, but only inside documented limits
- deployment profile or trust posture changes affect the guarantee
- the surface is real, but the full shape should not be over-interpreted

### Classify As `experimental` When
- the runtime is still learning what the contract should be
- semantics are likely to move
- it exists, but the team is not ready to freeze it

### Classify As `internal only` When
- the value to consumers is incidental
- the runtime needs freedom to refactor it
- support burden would outweigh real platform benefit

---

## Downstream Consumption Rules

### `aindy-sdk`
SDK may depend on:

- `stable` surfaces
- selected `conditionally stable` surfaces with explicit contract wording

SDK should avoid relying on:

- `experimental` surfaces unless intentionally opt-in
- `internal only` surfaces entirely

### `aindy-ui-kit`
UI may depend on:

- `stable` runtime identity and health/readiness semantics
- selected `conditionally stable` status conventions when documented

UI should avoid relying on:

- implementation-specific payload details
- runtime internal route behavior
- any `internal only` surface

---

## Current High-Risk Ambiguities

These are areas where stability interpretation is most likely to drift.

- route existence being mistaken for stable ownership
- syscall presence being mistaken for fully frozen semantics
- degraded-mode payload details being treated as stable without explicit documentation
- extension trust assumptions being consumed as broader extension guarantees
- internal runtime module structure being treated as if SDK/UI may safely depend on it

---

## Review Checklist

Use this before promoting a surface to broader use.

- [ ] Is this surface intentionally public?
- [ ] Is the behavior documented, not just implemented?
- [ ] May SDK rely on it?
- [ ] May UI rely on it?
- [ ] Would breaking it require explicit release communication?
- [ ] Is the guarantee narrower than the raw implementation shape?
- [ ] Should this remain experimental or internal instead?

---

## Minimum Stable Set To Protect First

If the runtime cannot freeze everything, it should protect these first:

1. `/api/version`
2. `/health`
3. `/ready`
4. health vs readiness semantics
5. documented deployment profile meanings
6. the narrow syscall surfaces already declared stable
7. the explicit trusted-internal security posture wording

That is enough to make cross-repo and operational behavior meaningfully safer.

---

## What Maturity Looks Like

The runtime has good stability discipline when:

- stable surfaces are few, clear, and defended
- conditionally stable surfaces have explicit limits
- experimental surfaces are not mistaken for contracts
- internal surfaces stay free to change
- SDK and UI rely on documented runtime truth rather than implementation coincidence

The goal is not to maximize what is stable.
The goal is to stabilize only what the platform can actually support.

---

## Relationship To Other Docs

This document should align with:

- `RUNTIME_BOUNDARY.md`
- `SECURITY_POSTURE.md`
- `CROSS_REPO_COMPATIBILITY.md`
- `DEGRADED_MODE_MATRIX.md`
- `PUBLIC_RUNTIME_SURFACES.md`
- `REPO_COMPATIBILITY_POLICY.md`

These docs answer different questions:

- `RUNTIME_BOUNDARY.md`: what the runtime owns
- `SECURITY_POSTURE.md`: what security claims are actually true
- `CROSS_REPO_COMPATIBILITY.md`: what downstream repos may safely depend on
- `DEGRADED_MODE_MATRIX.md`: what remains safe under partial failure
- `RUNTIME_STABILITY_INDEX.md`: which runtime surfaces are actually stable

