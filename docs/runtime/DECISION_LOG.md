# Decision Log

> Authored by Codex during non coding session. Needs review before repo commit and push.

This document records high-value runtime decisions that should remain visible across refactors, releases, and cross-repo coordination.

Its purpose is to reduce repeated re-litigation of core runtime decisions and to preserve the reasoning behind important boundaries and claims.

This is not a full architecture history. It is a concise decision register.

---

## Canonical Principle

A runtime matures faster when major decisions are explicit, reviewable, and reusable.

The goal of this log is to capture decisions that affect:

- runtime scope
- runtime guarantees
- security posture
- profile support
- release discipline
- downstream compatibility

If a decision changes, the runtime should update the record rather than rely on tribal memory.

---

## Decision Format

Use this structure for each entry:

- `ID`
- `Status`
- `Decision`
- `Why`
- `Implications`
- `Related Docs`

Suggested statuses:

- `accepted`
- `provisional`
- `superseded`
- `needs review`

---

## Current Decisions

### DEC-001
**Status:** `accepted`

**Decision**
`aindy-runtime` is a trusted-internal runtime platform, not a hardened third-party in-process extension platform.

**Why**
Current documentation and architecture support a trusted-internal posture, but not a strong general hostile-code extension claim.

**Implications**
- release language must remain narrow
- extension claims must stay constrained
- stronger plugin-host claims require future hardening work

**Related Docs**
- `SECURITY_POSTURE.md`
- `RUNTIME_BOUNDARY.md`

---

### DEC-002
**Status:** `accepted`

**Decision**
Health and readiness are separate contracts and must not be conflated.

**Why**
A live runtime is not automatically safe to receive work. Operational truth depends on readiness, not just liveness.

**Implications**
- `/health` and `/ready` must remain semantically distinct
- degraded-mode handling must preserve truthful readiness
- SDK/UI should not collapse the two concepts

**Related Docs**
- `DEGRADED_MODE_MATRIX.md`
- `CROSS_REPO_COMPATIBILITY.md`
- `OPERATOR_RUNBOOK.md`

---

### DEC-003
**Status:** `accepted`

**Decision**
Local-only fallback must not be treated as equivalent to distributed runtime support.

**Why**
Cross-instance guarantees depend on dependencies and runtime conditions that local-only fallback does not satisfy.

**Implications**
- distributed profiles must require distributed prerequisites
- readiness should narrow when only local-only behavior remains
- operators and downstream consumers must not be misled

**Related Docs**
- `PROFILE_SUPPORT_MATRIX.md`
- `DEGRADED_MODE_MATRIX.md`
- `DEPENDENCY_CRITICALITY_MATRIX.md`

---

### DEC-004
**Status:** `accepted`

**Decision**
Stable runtime surfaces should remain narrow and intentionally governed.

**Why**
A broad stable surface area creates heavy compatibility burden and makes internal cleanup harder.

**Implications**
- route existence is not enough to establish stability
- SDK/UI should depend only on documented stable or conditionally stable surfaces
- internal-only surfaces should stay refactorable

**Related Docs**
- `RUNTIME_STABILITY_INDEX.md`
- `CROSS_REPO_COMPATIBILITY.md`

---

### DEC-005
**Status:** `accepted`

**Decision**
Runtime maturity should be driven by stronger guarantees and narrower scope, not by adding more platform surface area.

**Why**
Current maturity risks are mostly about scope ambiguity, security posture limits, and runtime-critical verification depth.

**Implications**
- new scope should be justified by runtime truth, not convenience
- extraction and boundary discipline are part of maturity
- “bigger runtime” is not the target state

**Related Docs**
- `RUNTIME_BOUNDARY.md`
- `AINDY_RUNTIME_90_DAY_CHECKLIST.md`

---

### DEC-006
**Status:** `accepted`

**Decision**
Runtime-critical changes must be reviewed by impact, not by code size.

**Why**
Small changes in startup, scheduler, readiness, or security paths can carry much more risk than large local refactors elsewhere.

**Implications**
- change classification should drive review depth
- “small diff” is not a valid proxy for low runtime risk
- release discipline should follow impact class

**Related Docs**
- `CHANGE_IMPACT_MATRIX.md`
- `RELEASE_GATES.md`
- `TEST_STRATEGY.md`

---

### DEC-007
**Status:** `accepted`

**Decision**
Dependency loss must be interpreted through deployment profile, not only through generic service health.

**Why**
The same dependency can be optional in one profile and execution-critical in another.

**Implications**
- dependency classification must be profile-aware
- readiness truth must track profile truth
- operators need profile-sensitive guidance

**Related Docs**
- `DEPENDENCY_CRITICALITY_MATRIX.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `OPERATOR_RUNBOOK.md`

---

### DEC-008
**Status:** `accepted`

**Decision**
Unsupported profiles should be stated plainly rather than implied by omission.

**Why**
Silence around unsupported modes encourages accidental over-claiming.

**Implications**
- hostile multitenant or marketplace-style plugin-host claims remain out of scope unless explicitly revisited
- profile support docs should remain blunt

**Related Docs**
- `PROFILE_SUPPORT_MATRIX.md`
- `SECURITY_POSTURE.md`

---

### DEC-009
**Status:** `provisional`

**Decision**
The minimum stable downstream contract should be anchored first around `/api/version`, `/health`, `/ready`, and documented runtime status semantics.

**Why**
These surfaces are the most important for operator truth and cross-repo coordination, and they are a narrower place to start than freezing large route sets.

**Implications**
- release and compatibility checks should prioritize these surfaces first
- broader route stability can remain narrower or conditional

**Related Docs**
- `CROSS_REPO_COMPATIBILITY.md`
- `RUNTIME_STABILITY_INDEX.md`
- `RELEASE_GATES.md`

---

## Future Decisions To Record

The following decisions should be added here once resolved:

- final runtime ownership boundary after extraction/contraction review
- exact distributed profile support definition
- final stable surface list for downstream reliance
- stronger or revised extension support posture if adopted
- explicit readiness-blocker policy by profile

---

## When To Add Or Update An Entry

Add or revise a decision when:

- the runtime adopts a new durable boundary
- a support or security claim becomes narrower or stronger
- release discipline changes meaningfully
- a cross-repo compatibility promise is formalized
- a previously provisional decision becomes accepted or is superseded

---

## Decision Smells

These are warning signs that a decision should be logged but is not.

- the same architectural debate keeps recurring
- release reviews depend on unwritten assumptions
- downstream repos rely on behavior that no one has formally accepted as contract
- a profile or security claim is widely used but not explicitly recorded
- maintainers cannot explain why a boundary exists without tracing old context

---

## What Maturity Looks Like

Decision-log maturity is reached when:

- core runtime decisions are easy to find
- maintainers can distinguish accepted decisions from open questions
- important changes are explained in terms of prior decisions rather than memory
- fewer debates depend on historical guesswork

The runtime should increasingly preserve reasoning, not just code.

---

## Relationship To Other Docs

This document should align with:

- `OPEN_QUESTIONS.md`
- `RUNTIME_BOUNDARY.md`
- `SECURITY_POSTURE.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `RUNTIME_STABILITY_INDEX.md`
- `RELEASE_GATES.md`
- `CHANGE_IMPACT_MATRIX.md`
