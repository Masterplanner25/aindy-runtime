---
title: "Runtime Docset Governance"
last_verified: "2026-05-31"
api_version: "1.0"
status: current
owner: "platform-team"
---
﻿# Runtime Docset Governance

This document defines how to interpret the `docs/runtime` docset when documents differ in age, scope, or claim strength.

Its purpose is to prevent broader or older docs from silently overriding narrower, newer runtime-governance documents.

This is a docset-precedence document, not an architecture reference.

---

## Canonical Principle

When runtime docs disagree, the runtime should prefer:

> narrower, more explicit, more current claims over broader, older, or more aspirational claims

The goal is to keep the docset safe, governable, and usable during an active maturity-tightening phase.

---

## Why This File Exists

The current runtime docset contains both:

- older docs that preserve useful architectural and operational detail
- newer docs created to narrow claims, sharpen boundaries, and improve release/runtime discipline

Without an explicit precedence rule, readers may accidentally treat:

- future-state language as current support
- route presence as stable contract
- extension capability detail as stronger security posture than is actually supported
- older compatibility or cloud framing as more authoritative than newer boundary docs

This file prevents that.

---

## Precedence Rules

### Rule 1: Newer Governing Docs Override Broader Older Framing
If an older doc and a newer governing doc disagree about:

- runtime scope
- support claims
- security posture
- profile support
- degraded-mode meaning
- downstream compatibility
- stability classification

then the newer governing doc wins.

### Rule 2: Narrower Claim Wins Over Broader Claim
If one doc says a runtime surface or profile is more broadly supported than another doc, prefer the narrower claim unless an explicit reviewed decision has superseded it.

### Rule 3: Public Contract Docs Beat Incidental Implementation Description
If a document describing implementation details appears to imply broader downstream guarantees than the public contract docs, the public contract docs win.

### Rule 4: Security Posture Must Be Interpreted Conservatively
If a technical doc sounds like the runtime could support a stronger extension or isolation mode, but the governing security and profile docs do not support that claim, the conservative security posture wins.

### Rule 5: Unsupported Modes Must Be Read Literally
If newer support docs mark a mode unsupported, no older aspirational or architecture doc should be read as reviving that mode into current support.

---

## Governing Docs

The following documents are the governing layer for current runtime claims.

### Runtime Boundaries and Scope
- `RUNTIME_BOUNDARY.md`
- `RUNTIME_STABILITY_INDEX.md`

### Security and Trust
- `SECURITY_POSTURE.md`
- `EXECUTION_INVARIANTS.md` where security-sensitive guarantees are involved

### Runtime Status and Degraded Behavior
- `DEGRADED_MODE_MATRIX.md`
- `DEPENDENCY_CRITICALITY_MATRIX.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `OPERATOR_RUNBOOK.md`

### Release and Verification Discipline
- `RELEASE_GATES.md`
- `TEST_STRATEGY.md`
- `CHANGE_IMPACT_MATRIX.md`
- `INCIDENT_CLASSIFICATION.md`

### Cross-Repo and Contract Discipline
- `CROSS_REPO_COMPATIBILITY.md`
- `DECISION_LOG.md`
- `OPEN_QUESTIONS.md`

These should be treated as the primary source for current runtime claim interpretation.

---

## Older Docs That Still Matter, But Must Be Read Through Governance

These documents remain useful, but should be interpreted through the governing layer above.

### High-Value But Potentially Broader Docs
- `ARCHITECTURE.md`
- `DEPLOYMENT_PROFILES.md`
- `PUBLIC_RUNTIME_SURFACES.md`
- `RUNTIME_ONLY_DEPLOYMENT.md`
- `EXTENSION_TRUST_MODEL.md`
- `SECURITY_POLICY.md`
- `REPO_COMPATIBILITY_POLICY.md`
- `DEGRADED_RUNTIME_MODES.md`
- `AGENT_RUNTIME.md`
- `CI_OWNERSHIP.md`

These docs may contain:

- correct implementation detail
- useful historical framing
- still-valid operational specifics
- broader ambition than the current supported posture

They should not be treated as the final word when claim strength differs.

---

## How To Resolve Common Conflicts

### Conflict Type 1: Architecture Ambition vs Current Support
If `ARCHITECTURE.md` suggests a broader cloud or multitenant future than the support docs allow today:

- treat the broader framing as future-state or design intent
- treat `PROFILE_SUPPORT_MATRIX.md` and `SECURITY_POSTURE.md` as current support truth

### Conflict Type 2: Technical Capability vs Supported Posture
If `EXTENSION_TRUST_MODEL.md` or another technical doc describes mechanisms that sound stronger than the supported security posture:

- interpret those mechanisms as technical detail, not automatic support claim
- treat `SECURITY_POSTURE.md` as the actual claim ceiling

### Conflict Type 3: Route Presence vs Stability
If an older doc lists a route as mounted or available:

- do not assume it is stable or downstream-safe
- check `RUNTIME_STABILITY_INDEX.md` and `CROSS_REPO_COMPATIBILITY.md`

### Conflict Type 4: Profile Availability vs Profile Support
If a profile can boot but newer docs classify it as constrained or unsupported:

- treat bootability as implementation fact
- treat support level docs as the authoritative support claim

### Conflict Type 5: Legacy Repo Compatibility Language vs Current Repo Split
If an older compatibility doc talks about future apps-monolith relationships:

- prefer `CROSS_REPO_COMPATIBILITY.md` for current runtime/SDK/UI meaning

---

## Reader Guidance

### For Maintainers
Use the governing docs when deciding:

- what the runtime should claim
- whether a change is high risk
- whether a surface is stable enough for downstream reliance
- whether degraded continuation is honest
- whether a deployment profile is supported

### For Operators
Prefer:

- `OPERATOR_RUNBOOK.md`
- `DEGRADED_MODE_MATRIX.md`
- `DEPENDENCY_CRITICALITY_MATRIX.md`
- `PROFILE_SUPPORT_MATRIX.md`

Do not infer operational truth from architecture intent alone.

### For SDK/UI Consumers
Prefer:

- `CROSS_REPO_COMPATIBILITY.md`
- `RUNTIME_STABILITY_INDEX.md`
- `PUBLIC_RUNTIME_SURFACES.md` interpreted through the newer stability docs

Do not rely on older implementation-oriented docs as compatibility guarantees.

---

## Update Expectations

As older docs are aligned, this governance file should become lighter, not heavier.

The intended trajectory is:

1. newer governing docs establish safer claim boundaries
2. older docs are revised to align with those boundaries
3. this file eventually becomes a short interpretation rule rather than a major warning layer

Until then, this file should remain explicit.

---

## What Good Governance Looks Like

The runtime docset is well governed when:

- readers can tell which docs define current claims
- older docs remain useful without silently over-expanding support language
- support, security, stability, profile, and compatibility claims stay consistent across the set
- maintainers can narrow claims quickly when reality is weaker than aspiration

The goal is not to suppress useful technical detail.
The goal is to stop documentation drift from turning into platform overclaim.

---

## Relationship To Other Docs

This document should be read together with:

- `RUNTIME_DOC_ALIGNMENT_AUDIT.md`
- `DECISION_LOG.md`
- `OPEN_QUESTIONS.md`

These answer different questions:

- `RUNTIME_DOC_ALIGNMENT_AUDIT.md`: which older docs are aligned, partial, or conflicting
- `DECISION_LOG.md`: which core runtime decisions are already accepted
- `OPEN_QUESTIONS.md`: which important questions remain unresolved
