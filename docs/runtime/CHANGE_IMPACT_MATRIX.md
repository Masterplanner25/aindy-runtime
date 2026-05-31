---
title: "Change Impact Matrix"
last_verified: "2026-05-31"
api_version: "1.0"
status: current
owner: "platform-team"
---
﻿# Change Impact Matrix

> Authored by Codex during non coding session. Needs review before repo commit and push.

This document classifies changes to `aindy-runtime` by impact level and required review depth.

Its purpose is to make change risk explicit before release rather than inferred afterward.

This is a change-discipline document, not a git workflow policy.

---

## Canonical Principle

Not every runtime change deserves the same review, test depth, or release caution.

A mature runtime distinguishes between:

- cosmetic or local changes
- contract-affecting changes
- execution-semantic changes
- security-posture changes
- dependency and profile changes

The goal is to match engineering caution to runtime risk.

---

## Impact Levels

### `low`
A change with little or no effect on runtime contracts, execution truth, or operational posture.

### `medium`
A change that may affect documented behavior, downstream consumers, or operational interpretation, but is not expected to alter core execution semantics.

### `high`
A change that can affect execution correctness, degraded-mode truth, readiness truth, recovery behavior, or cross-repo compatibility.

### `critical`
A change that can alter runtime guarantees, security posture, supported profile claims, or execution truth in ways that could produce serious operator or consumer surprise.

---

## Change Impact Matrix

| Change Area | Default Impact | Why It Matters | Minimum Review Depth | Minimum Validation |
|---|---|---|---|---|
| Formatting, comments, doc-only updates | `low` | no runtime semantic change expected | normal review | docs sanity check |
| Internal logging/observability with no semantic effect | `low` | useful but should not change runtime truth | normal review | targeted smoke check |
| Stable route payload wording or metadata adjustments | `medium` | downstream consumers may care | contract-aware review | contract checks |
| Health/readiness status mapping changes | `high` | operators and downstream repos rely on this truth | runtime-owner review | contract + degraded-mode tests |
| Startup sequencing changes | `critical` | affects boot correctness, readiness, and recovery | runtime-owner review + explicit signoff | startup + readiness + regression tests |
| Scheduler lifecycle changes | `critical` | affects wait/resume and execution continuity | runtime-owner review + explicit signoff | invariant + integration tests |
| Wait/resume matching changes | `critical` | affects resumable execution semantics | runtime-owner review + explicit signoff | invariant + duplication/recovery tests |
| Rehydration/recovery logic changes | `critical` | affects stranded work and restart guarantees | runtime-owner review + explicit signoff | recovery + degraded-mode tests |
| Syscall dispatcher behavior changes | `critical` | affects enforcement and execution truth | runtime-owner review + explicit signoff | invariant + enforcement tests |
| Syscall registry or required syscall set changes | `high` | affects runtime capability and supported execution | runtime-owner review | startup + contract tests |
| Tenant or capability enforcement changes | `critical` | affects security posture and runtime truth | runtime-owner + security-aware review | enforcement + regression tests |
| Quota/backend fail-open vs fail-closed behavior | `critical` | changes operational safety posture | runtime-owner + security-aware review | environment-sensitive tests |
| Dependency requirement changes by profile | `high` | affects readiness truth and supported claims | runtime-owner review | profile + degraded-mode tests |
| Deployment profile contract changes | `critical` | affects what the runtime is allowed to claim | runtime-owner review + explicit signoff | profile + readiness validation |
| Extension trust-model changes | `critical` | affects security posture and support claims | runtime-owner + security-aware review | posture + enforcement checks |
| Stable public runtime surface additions | `medium` | expands downstream contract burden | contract-aware review | contract tests |
| Stable public runtime surface breaking changes | `critical` | direct downstream compatibility event | runtime-owner review + explicit signoff | contract + compatibility checks |
| Internal-only refactors in runtime-critical modules | `high` | implementation-only in theory, but high blast radius | runtime-owner review | invariant or targeted regression tests |
| Artifact/build/release pipeline changes | `high` | affects shipped reality vs source confidence | release-aware review | artifact validation |
| SDK/UI compatibility assumption changes | `high` | cross-repo drift risk | runtime-owner + downstream-aware review | compatibility checks |

---

## Area-Based Guidance

## 1. Public Contract Changes

Examples:
- `/api/version` shape changes
- `/health` or `/ready` field meaning changes
- stable runtime metadata changes

Default impact:
- `high` if additive and carefully bounded
- `critical` if breaking or reinterpretive

Required response:
- explicit contract review
- downstream compatibility review
- release note clarity

---

## 2. Execution-Semantic Changes

Examples:
- scheduler behavior
- wait/resume matching
- resume deduplication
- restart rehydration behavior
- required syscall resolution

Default impact:
- `critical`

Required response:
- explicit invariant review
- deeper runtime-owner validation
- stronger than normal release caution

---

## 3. Security-Posture Changes

Examples:
- tenant enforcement path changes
- capability enforcement changes
- extension trust assumptions changing
- fail-open/fail-closed behavior changes

Default impact:
- `critical`

Required response:
- review against `SECURITY_POSTURE.md`
- explicit test and release scrutiny
- careful wording in release communication

---

## 4. Dependency and Profile Changes

Examples:
- Redis becoming required or optional in a profile
- worker presence assumptions changing
- Mongo requirement changes
- schema/bootstrap dependency interpretation changes

Default impact:
- `high` or `critical` depending on whether profile claims change

Required response:
- update profile and dependency docs
- re-check readiness truthfulness
- ensure fallback remains honest

---

## 5. Internal Refactors

Examples:
- runtime-critical module cleanup
- startup code rearrangement
- scheduler-internal restructuring

Default impact:
- `medium` for clearly local non-critical cleanup
- `high` for runtime-critical internals even when no public contract is intended to change

Required response:
- avoid assuming “internal” means “low risk” when blast radius is high

---

## Review Depth Expectations

### Low Impact
Expected:
- normal engineering review
- local validation as appropriate

### Medium Impact
Expected:
- contract-aware review if public behavior is touched
- targeted tests or smoke verification
- check whether any docs need updates

### High Impact
Expected:
- runtime-owner review
- explicit validation of affected runtime truths
- review against degraded-mode and compatibility implications

### Critical Impact
Expected:
- runtime-owner review
- explicit reviewer signoff
- release-gate scrutiny
- validation against invariants, security posture, degraded modes, profile claims, and downstream compatibility as relevant

---

## Impact Escalation Rules

A change should be escalated upward if any of these are true:

- it touches startup order
- it touches readiness truth
- it touches scheduler, wait/resume, or rehydration
- it touches tenant or capability enforcement
- it changes supported profile language
- it changes whether a dependency is required for a profile
- it changes a stable downstream-consumed field or route
- it widens or narrows the security claim the runtime can honestly make

If in doubt, classify upward rather than downward.

---

## Change Smells

These are warning signs that a change is being under-classified.

- “it’s just internal” in a runtime-critical module
- “tests still pass” without checking the affected contract or invariant
- readiness behavior changed but no degraded-mode review happened
- dependency handling changed but profile support language did not
- security-sensitive path changed but release notes stayed silent
- stable payload changed but only source-local tests were run

---

## Minimum Review Mapping

Use this quick mapping:

- `low` -> normal review
- `medium` -> normal review plus contract awareness if public behavior changed
- `high` -> runtime-owner review plus targeted validation
- `critical` -> runtime-owner review plus explicit signoff and release-gate treatment

---

## What Maturity Looks Like

Change-discipline maturity is reached when:

- runtime changes are classified consistently
- risky changes trigger deeper review automatically
- profile, degraded-mode, and security implications are checked before release
- “internal only” is not used to excuse high-blast-radius changes
- stable downstream surfaces are treated as real compatibility commitments

The runtime should increasingly fail at review time rather than surprising operators after deployment.

---

## Relationship To Other Docs

This document should align with:

- `RELEASE_GATES.md`
- `TEST_STRATEGY.md`
- `EXECUTION_INVARIANTS.md`
- `SECURITY_POSTURE.md`
- `DEGRADED_MODE_MATRIX.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `CROSS_REPO_COMPATIBILITY.md`
