# AINDY Runtime 90-Day Hardening Checklist

> Authored by Codex during non coding session. Needs review before repo commit and push.


This plan is for `aindy-runtime` as a **trusted-internal runtime platform** with kernel, execution, orchestration, syscall, deployment, and runtime-contract responsibilities.

Current maturity score: `71.5 / 100`
90-day target score: `76-80 / 100`
Longer-term target: `85 / 100`

This checklist is designed to move the runtime from:

> Emerging production-grade internal runtime

toward:

> Mature specialized runtime platform

---

## 90-Day Goals

By day 90, `aindy-runtime` should have:

- a narrower and more defensible runtime ownership boundary
- clearer execution and recovery invariants
- deeper verification on scheduler, syscall, startup, and failure paths
- stronger trusted-internal security and isolation posture
- lower architectural drag in runtime-critical modules
- stronger artifact and compatibility discipline across runtime, SDK, and UI boundaries

---

## Success Criteria

- [ ] Runtime ownership boundary is documented and enforced more strictly
- [ ] Stable vs experimental runtime surfaces are clearer in critical paths
- [ ] Canonical execution invariants are documented
- [ ] Recovery, restart, wait/resume, and syscall availability behavior have stronger regression coverage
- [ ] Trusted-internal security posture is materially stronger and easier to explain
- [ ] Runtime-critical verification bar is higher than the current baseline
- [ ] Artifact validation and cross-repo compatibility checks are improved
- [ ] Maturity score improves from `71.5` to at least `76-80`

---

## Phase 1: Days 1-30

### Theme
Tighten runtime identity, ownership, and critical contracts.

### Ownership Boundary
- [x] Write one canonical definition of what `aindy-runtime` owns
- [x] Write one canonical definition of what `aindy-runtime` explicitly does **not** own
- [ ] Review current repo surface and tag directories/modules as:
  - [ ] core runtime
  - [ ] platform support
  - [ ] legacy spillover
  - [ ] candidate for extraction or de-emphasis
- [ ] Publish a runtime boundary note that aligns with SDK and UI responsibilities

### Public Surface Tightening
- [ ] Review `docs/runtime/PUBLIC_RUNTIME_SURFACES.md`
- [ ] Mark critical runtime surfaces as:
  - [ ] stable
  - [ ] conditionally stable
  - [ ] experimental
- [ ] Reduce ambiguity in docs around runtime-only boot guarantees
- [ ] Confirm extension ABI and syscall stability language is consistent across docs

### Execution Contract Inventory
- [ ] Create a canonical list of runtime invariants to preserve across releases
- [ ] Include invariants for:
  - [ ] scheduler lifecycle
  - [ ] wait/resume registration
  - [ ] syscall dispatcher availability
  - [ ] startup ordering
  - [ ] tenant/capability enforcement
  - [ ] readiness and degraded mode behavior

### Architecture Risk Review
- [ ] Identify top 5 runtime-critical modules by complexity and change risk
- [ ] Identify top 5 runtime-critical modules by operational blast radius
- [ ] Record where bootstrap, configuration, and lifecycle coupling are still too high

### Phase 1 Exit Criteria
- [ ] Runtime boundary is easier to explain in one paragraph
- [ ] Critical runtime contracts are listed in one place
- [ ] Stable vs experimental is clearer for the most important runtime surfaces

---

## Phase 2: Days 31-60

### Theme
Harden correctness, startup behavior, recovery, and security posture.

### Execution Invariants
- [ ] Create `docs/runtime/EXECUTION_INVARIANTS.md`
- [ ] Define and document invariants for:
  - [ ] startup sequencing
  - [ ] scheduler registration lifecycle
  - [ ] syscall readiness behavior
  - [ ] event delivery and resume matching
  - [ ] restart/rehydration behavior
  - [ ] readiness transitions
  - [ ] degraded-mode behavior

### Verification Expansion
- [ ] Add regression coverage for startup sequencing
- [ ] Add regression coverage for scheduler wait/resume behavior
- [ ] Add regression coverage for syscall not-ready windows
- [ ] Add regression coverage for recovery/rehydration paths
- [ ] Add regression coverage for readiness behavior against partial infrastructure
- [ ] Add integration checks for Redis/Postgres-backed execution paths where relevant

### Security and Isolation Hardening
- [ ] Create or update a runtime security matrix covering:
  - [ ] trusted internal execution
  - [ ] extension capability boundaries
  - [ ] tenant enforcement boundaries
  - [ ] deployment profile differences
  - [ ] degraded security posture under missing dependencies
- [ ] Audit all extension trust assumptions documented in runtime docs
- [ ] Verify high-risk capability paths have regression tests
- [ ] Document what is safe, unsafe, and unsupported for extension execution

### Operability Review
- [ ] Review `/health`, `/ready`, and `/api/version` expectations as runtime contracts
- [ ] Add tests for failure and partial-readiness cases
- [ ] Confirm observability expectations are explicit enough for operators
- [ ] Identify top 3 operational failure modes not yet well covered by tests

### Phase 2 Exit Criteria
- [ ] Runtime invariants are documented
- [ ] Startup, recovery, and wait/resume confidence is higher
- [ ] Security posture is clearer and less assumption-driven
- [ ] Operational behavior under degraded conditions is better defined

---

## Phase 3: Days 61-90

### Theme
Reduce core debt, improve release discipline, and prove platform boundaries.

### Runtime Core Debt Reduction
- [ ] Prioritize runtime-critical debt from `TECH_DEBT.md`
- [ ] Reduce bootstrap/settings coupling in the highest-risk paths
- [ ] Reduce lifecycle ordering ambiguity in startup/runtime initialization
- [ ] Reduce not-ready syscall window risk where practical
- [ ] Simplify at least one runtime-critical module boundary

### Verification and Coverage Standards
- [ ] Raise the effective verification bar for runtime-critical code paths
- [ ] Add explicit contract tests for:
  - [ ] public runtime endpoints
  - [ ] boot/runtime metadata surfaces
  - [ ] runtime-only packaging assumptions
- [ ] Review whether current coverage thresholds are defensible for runtime-critical modules
- [ ] Add targeted checks where raw coverage percentage is hiding critical-path gaps

### Release and Artifact Discipline
- [ ] Add stronger artifact validation for built runtime packages
- [ ] Verify installed-artifact behavior, not just source-tree behavior
- [ ] Add a release checklist for runtime-only deployment verification
- [ ] Verify compatibility assumptions with `aindy-sdk` and `aindy-ui-kit`
- [ ] Define what compatibility must hold across the three repos before release

### Cross-Repo Boundary Proof
- [ ] Document runtime-to-SDK contract expectations
- [ ] Document runtime-to-UI contract expectations
- [ ] Identify current leakage where SDK or UI implicitly depends on unstable runtime internals
- [ ] Add at least one compatibility check or smoke path spanning runtime and SDK
- [ ] Add at least one compatibility check or smoke path spanning runtime-facing UI assumptions

### Final Review
- [ ] Re-score the runtime using the maturity rubric
- [ ] Record category deltas
- [ ] Record the top 3 blockers to `80+`
- [ ] Record the top 3 blockers to `85+`

### Phase 3 Exit Criteria
- [ ] Runtime core is less coupled in its most fragile paths
- [ ] Release confidence is higher for built artifacts
- [ ] Runtime boundaries with SDK and UI are clearer and more testable
- [ ] The runtime can defend a score in the `76-80` range

---

## Weekly Operating Checklist

Use this every week during the 90-day window.

### Week Review
- [ ] What runtime-critical risk was reduced this week?
- [ ] What boundary became clearer this week?
- [ ] What failure mode is now better tested?
- [ ] What debt was removed instead of deferred?
- [ ] Did any new feature expand runtime scope without reducing existing surface?

### Guardrails
- [ ] Do not add major new runtime surface unless it reduces architectural ambiguity
- [ ] Do not broaden external-platform claims before security and isolation justify them
- [ ] Do not treat doc quality as a substitute for recovery/failure verification
- [ ] Do not treat raw test count as proof of runtime maturity
- [ ] Do not let SDK/UI convenience pull unstable responsibilities back into the runtime core

---

## Priority Order

If time gets tight, do work in this order:

1. [ ] Narrow and document runtime ownership boundary
2. [ ] Define and publish execution invariants
3. [ ] Deepen startup/recovery/wait-resume verification
4. [ ] Strengthen trusted-internal security and isolation posture
5. [ ] Reduce bootstrap/configuration/lifecycle coupling
6. [ ] Improve artifact and cross-repo compatibility verification

---

## Milestone Gates

### Gate A: Reach `75 / 100`
- [ ] Runtime boundary is narrower and more explicit
- [ ] Execution invariants are documented
- [ ] Startup and recovery verification is stronger
- [ ] Security posture is clearer for trusted-internal deployments

### Gate B: Reach `80 / 100`
- [ ] Critical public runtime surfaces are more tightly governed
- [ ] Runtime-critical verification is materially deeper
- [ ] Core debt in bootstrap/lifecycle/syscall paths is reduced
- [ ] Artifact and compatibility discipline is stronger across repos

### Gate C: Prepare for `85 / 100`
- [ ] Failure and degraded-mode behavior is deeply tested
- [ ] Runtime core is smaller and less coupled
- [ ] Security and isolation claims are stronger and easier to defend
- [ ] SDK/UI/runtime boundaries are routine, stable, and release-tested

---

## Risks to Avoid

- [ ] Expanding runtime scope before reducing current surface area
- [ ] Claiming stronger platform maturity than the trust model supports
- [ ] Leaving critical lifecycle behavior implicit instead of contract-tested
- [ ] Deferring architectural debt in bootstrap, lifecycle, and capability paths repeatedly
- [ ] Letting cross-repo integration rely on undocumented runtime behavior

---

## Final 90-Day Review

Review date:
- [ ] YYYY-MM-DD

Reviewer:
- [ ] Name / role

Start score:
- [x] `71.5 / 100`

Target score:
- [ ] `76-80 / 100`

Stretch target:
- [ ] `80+ / 100`

Top wins:
- [ ] Add summary here

Top remaining blockers:
- [ ] Add summary here

