---
title: "Open Questions"
last_verified: "2026-05-31"
api_version: "1.0"
status: current
owner: "platform-team"
---
﻿# Open Questions

> Authored by Codex during non coding session. Needs review before repo commit and push.

This document tracks the highest-leverage unresolved questions affecting the maturity of `aindy-runtime`.

Its purpose is to make uncertainty explicit instead of allowing it to remain hidden inside implementation sprawl, release friction, or vague platform language.

This is a strategic uncertainty document, not a backlog dump.

---

## Canonical Principle

A runtime becomes harder to mature when major open questions are left implicit.

The goal of this document is to surface the questions that most directly affect:

- runtime scope
- runtime guarantees
- security claims
- profile support
- release discipline
- downstream compatibility

These are the questions that should shape decisions, not merely follow them.

---

## Highest-Priority Open Questions

## 1. What Is The Narrowest Defensible Scope Of `aindy-runtime`?

Why this matters:
- scope ambiguity is one of the runtime’s biggest maturity risks
- broad ownership makes architecture, security, and release discipline harder

Current tension:
- the repo wants to be a runtime substrate
- but it still appears to carry broader platform surface inherited from earlier structure

Question:
- what must remain in `aindy-runtime` for execution truth and runtime contract integrity, and what should be pushed to SDK, UI, or higher layers?

What a good answer would produce:
- a smaller, stronger runtime core
- clearer extraction candidates
- less accidental platform gravity

---

## 2. Which Runtime Surfaces Are Truly Stable Enough For Downstream Reliance?

Why this matters:
- SDK and UI maturity depend on trustworthy runtime contracts
- release discipline gets weaker when “public enough” is treated as “stable”

Current tension:
- some surfaces clearly should be stable
- others exist in practice but are not yet governed tightly enough

Question:
- which routes, payload fields, readiness semantics, and execution-facing surfaces should be treated as real compatibility commitments?

What a good answer would produce:
- narrower stable surface area
- fewer accidental downstream dependencies
- stronger cross-repo release confidence

---

## 3. How Far Does The Runtime Want To Go On Extension Support?

Why this matters:
- extension posture drives security claims, support claims, and architecture pressure

Current tension:
- the runtime documents a trusted-internal posture
- but extension-heavy usage creates pressure toward broader platform claims

Question:
- is the intended future a constrained trusted-internal extension runtime, or a stronger general extension platform?

What a good answer would produce:
- a clearer security roadmap
- fewer ambiguous claims
- better boundaries around extension UX vs runtime truth

---

## 4. What Is The Real Supported Distributed Profile?

Why this matters:
- distributed runtime claims are among the easiest to overstate
- readiness and degraded-mode truth become much harder in distributed operation

Current tension:
- the runtime appears to support distributed behavior under certain conditions
- but fallback and dependency loss can narrow guarantees sharply

Question:
- what exact distributed profile is supported today, and what prerequisites must be true before the runtime may honestly claim it?

What a good answer would produce:
- clearer deployment guidance
- stronger readiness semantics
- fewer misleading distributed claims

---

## 5. What Security Posture Is The Team Willing To Defend Publicly?

Why this matters:
- runtime maturity depends on matching claims to actual trust boundaries

Current tension:
- the trusted-internal claim is defendable
- stronger sandbox/plugin/multitenant claims are not yet clearly defendable

Question:
- what security wording is the maximum honest claim for current releases, and what is intentionally deferred?

What a good answer would produce:
- safer release language
- better prioritization of security hardening work
- fewer implicit promises to downstream repos or operators

---

## 6. What Failure Modes Must Block Readiness Absolutely?

Why this matters:
- readiness truth is one of the most important runtime promises

Current tension:
- some degraded modes clearly should block readiness
- others appear profile-dependent or partially recoverable

Question:
- which exact classes of failure are absolute readiness blockers, and which are allowed degraded continuations under which profiles?

What a good answer would produce:
- less ambiguous operator behavior
- stronger degraded-mode consistency
- fewer runtime states that “survive” without being safely ready

---

## 7. How Much Test Assurance Is Enough For Runtime-Critical Change?

Why this matters:
- the runtime needs stronger confidence on dangerous paths, not just more tests in aggregate

Current tension:
- CI and tests exist
- but not every runtime-critical behavior appears equally defended

Question:
- what explicit assurance bar should apply to startup, scheduler, rehydration, syscall, readiness, and distributed-profile changes?

What a good answer would produce:
- clearer release gates
- stronger review discipline
- better targeting of verification effort

---

## 8. What Cross-Repo Breakage Is Acceptable?

Why this matters:
- platform maturity depends on whether runtime, SDK, and UI evolve by contract or by coincidence

Current tension:
- some breakage is expected while contracts tighten
- too much tolerated breakage means there is no real platform boundary

Question:
- which downstream breaks are acceptable because the surface is not yet stable, and which should be treated as release failures?

What a good answer would produce:
- more predictable coordination across repos
- better classification of stable vs incidental behavior
- more honest compatibility promises

---

## Secondary Open Questions

These are important, but slightly less foundational than the set above.

### 9. What Belongs In Runtime-Only Deployment Versus Broader Platform Deployment?
- how thin can the runtime-only deployment contract become while still being operationally useful?

### 10. Which Legacy Route Groups Are Intentional Runtime Ownership Versus Extraction Candidates?
- route existence should not be mistaken for mature runtime ownership

### 11. Which Runtime Conditions Should Become Stable Operator-Facing Codes?
- useful for UI, SDK, automation, and incident tooling consistency

---

## Questions That Need Resolution Before Claiming Higher Maturity

The following should be answered before the runtime can credibly claim a substantially higher maturity tier:

- [ ] narrow runtime ownership boundary
- [ ] stable vs conditional runtime surface boundaries
- [ ] supported distributed profile definition
- [ ] maximum defensible security claim
- [ ] absolute readiness blockers by profile
- [ ] cross-repo compatibility commitments

These are not optional polish questions. They shape what the runtime is allowed to claim.

---

## How To Use This Document

Use this during:

- architecture review
- release planning
- maturity review
- cross-repo coordination
- security posture review

Good use:
- answering one question enough to improve runtime boundaries or claims

Bad use:
- letting the same unresolved question persist release after release while still broadening claims

---

## Question Smells

These are signs that an open question is being mishandled.

- the runtime is already making a strong claim without answering the underlying question
- SDK or UI behavior depends on an unresolved runtime contract question
- profile support language keeps expanding while readiness truth remains unsettled
- security language gets broader without stronger isolation or enforcement
- implementation keeps moving, but the governing decision is never written down

---

## What Maturity Looks Like

Open-question maturity is reached when:

- major architectural and contract questions are written down early
- the team can point to deliberate decisions instead of accidental outcomes
- unresolved questions shrink over time instead of silently compounding
- claims narrow when answers are missing, rather than widening optimistically

The runtime should increasingly make unknowns visible before they become instability.

---

## Relationship To Other Docs

This document should align with:

- `RUNTIME_BOUNDARY.md`
- `SECURITY_POSTURE.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `RUNTIME_STABILITY_INDEX.md`
- `RELEASE_GATES.md`
- `CHANGE_IMPACT_MATRIX.md`
- `INCIDENT_CLASSIFICATION.md`
