# AINDY Runtime Maturity Checklist

This rubric evaluates `aindy-runtime` as a runtime/OS-like platform.

Current evaluation posture:

> A trusted-internal runtime platform with orchestration, syscall, deployment, and execution-substrate responsibilities.

It is **not** currently scored as a hardened third-party extension platform.

---

## Scoring

Each category is scored from `0-10`.

Formula:

`weighted_score = (category_score / 10) * weight`

Overall maturity score:

`sum(weighted_scores)`

---

## Score Bands

- `0-39` Prototype
- `40-54` Early experimental runtime
- `55-69` Serious advanced beta runtime
- `70-79` Emerging production-grade internal runtime
- `80-89` Mature specialized runtime platform
- `90-100` Industry-grade runtime platform

---

## Current Scorecard

> Last updated: **2026-06-04 (session 2)** — AGENT-APPROVE-001b closed, artifact CI tests added.
> Trajectory: 71.5 (baseline) → 77.5 (session 1) → **~79 / 100** (session 2).
> See `AINDY_RUNTIME_90_DAY_CHECKLIST.md` for full delta breakdown.

| Category | Weight | Score | Weighted | Notes |
|---|---:|---:|---:|---|
| Runtime Identity & Scope | 10 | 9.0 | 9.0 | Watcher removed; RUNTIME_BOUNDARY.md canonical. |
| Architecture & Subsystem Separation | 12 | 8.0 | 9.6 | RUNTIME_MODULE_MAP.md; coupling risk remains. |
| Public Contract Discipline | 10 | 9.0 | 9.0 | SDK_CONTRACT + UI_CONTRACT + 7 compat tests. |
| Execution & Orchestration Correctness | 12 | 8.0 | 9.6 | EXECUTION_INVARIANTS; approve now async (001b closed). |
| Deployment & Operability | 10 | 7.5 | 7.5 | RELEASE_CHECKLIST; CLI help CI-tested. |
| Security & Isolation | 14 | 6.5 | 9.1 | SECURITY_MATRIX + security isolation tests. |
| Codebase Discipline & Complexity Control | 10 | 6.5 | 6.5 | AGENT-APPROVE-001b closed; CLI-1 guard validated. |
| Testing & Verification | 10 | 8.5 | 8.5 | 11 new test files; threading.Event coordination. |
| Release & Compatibility Discipline | 6 | 8.5 | 5.1 | Automated CLI help test covers RELEASE_CHECKLIST step 5. |
| Ecosystem Boundary Readiness | 6 | 6.5 | 3.9 | SDK/UI contract docs + cross-repo regression tests. |

**Current total: `~79 / 100`**

Current band:

- [x] Emerging production-grade internal runtime
- [x] Approaching mature specialized runtime platform
- [ ] Mature specialized runtime platform
- [ ] Industry-grade runtime platform

---

## Category Checklists

### 1. Runtime Identity & Scope
- [x] Runtime purpose is explicitly documented.
- [x] Repo states what it does not claim to be.
- [x] Runtime ownership boundary is narrow and enforced.
- [ ] Non-runtime concerns are pushed out of the repo where possible.
- [ ] Internal vs external platform claims are clearly separated in release messaging.

### 2. Architecture & Subsystem Separation
- [x] Core runtime subsystems are visibly separated.
- [x] Kernel/execution/orchestration concepts exist.
- [ ] Cross-layer imports and accidental coupling are actively constrained.
- [ ] Runtime core is materially smaller than surrounding platform surface.
- [x] Architectural ownership boundaries are documented and enforced.

### 3. Public Contract Discipline
- [x] Stable vs experimental surfaces are documented.
- [x] Runtime contract docs exist.
- [x] Compatibility policy exists in some form.
- [ ] Public runtime surfaces are versioned with stricter governance.
- [ ] Experimental seams are reduced in critical execution paths.
- [x] Contract compliance is verified in CI beyond documentation checks.

### 4. Execution & Orchestration Correctness
- [x] Scheduler/wait/resume/syscall infrastructure exists.
- [x] Execution semantics are more than prototype-level.
- [x] Core execution invariants are documented in one canonical place.
- [ ] Recovery/restart semantics are deeply regression-tested.
- [ ] Cross-instance event delivery guarantees are explicit and tested.
- [ ] Failure-mode behavior is verified, not assumed.

### 5. Deployment & Operability
- [x] Health/readiness behavior exists.
- [x] Runtime-only deployment path exists.
- [x] Packaging and boot path are real.
- [ ] Operational profiles are validated under realistic load/failure scenarios.
- [ ] Observability expectations are documented as runtime guarantees.
- [ ] Upgrade and rollback behavior is standardized and tested.

### 6. Security & Isolation
- [x] Trust model is explicitly discussed.
- [x] Capability/isolation concepts exist.
- [ ] Security posture is strong enough for less-trusted extension scenarios.
- [ ] Third-party extension isolation defaults to safer boundaries.
- [ ] Cross-context security regression testing is a release gate.
- [ ] Tenant/isolation guarantees are documented as enforceable contracts.

### 7. Codebase Discipline & Complexity Control
- [x] Tech debt is tracked explicitly.
- [x] Known core debt items are documented.
- [ ] Core configuration/bootstrap coupling is reduced.
- [ ] Runtime-critical modules are simplified and split where needed.
- [ ] Architectural debt trends are improving release to release.
- [ ] New features are not increasing ambiguity in the runtime core.

### 8. Testing & Verification
- [x] CI exists.
- [x] Runtime-only checks exist.
- [x] Integration testing exists.
- [ ] Coverage expectations for runtime-critical code are materially higher.
- [ ] Contract tests cover public runtime surfaces.
- [ ] Failure-mode and recovery testing are first-class.
- [ ] Security-sensitive behaviors have mandatory regression coverage.

### 9. Release & Compatibility Discipline
- [x] Runtime release/signoff documentation exists.
- [x] Compatibility thinking exists.
- [x] Built artifacts are validated as rigorously as source.
- [ ] Runtime/API/schema compatibility gates are stricter.
- [x] Cross-repo compatibility with SDK/UI is automated.
- [ ] Release notes distinguish stable guarantees from internal implementation changes.

### 10. Ecosystem Boundary Readiness
- [x] SDK/UI/runtime repo split exists.
- [ ] Runtime contracts are consumed cleanly by SDK without churn.
- [ ] UI assumptions do not depend on unstable runtime internals.
- [ ] Cross-repo version compatibility matrix exists.
- [ ] Runtime is a stable substrate rather than a catch-all center of gravity.

---

## Milestone Gates

### Gate 1: Reach 75/100
Focus: tighten the internal-runtime story.

Required:
- [ ] Publish a stricter runtime ownership boundary.
- [ ] Reduce obvious non-runtime spillover in repo responsibilities.
- [ ] Document core execution invariants.
- [ ] Add stronger regression coverage for startup/recovery/wait-resume paths.
- [ ] Raise confidence in trusted-internal deployment posture.

Definition of done:
- Runtime is easier to describe in one sentence.
- Critical orchestration behavior is documented as contract, not just implementation.

### Gate 2: Reach 80/100
Focus: become a mature specialized internal runtime platform.

Required:
- [ ] Freeze critical public runtime surfaces and version them more explicitly.
- [ ] Raise runtime-critical verification bar materially above current baseline.
- [ ] Harden security and isolation boundaries for at least "trusted internal plus constrained extensions."
- [ ] Reduce core bootstrap/settings/syscall-window debt.
- [ ] Add stronger artifact and compatibility verification.

Definition of done:
- Runtime claims are narrower, stronger, and easier to defend.
- Security posture and correctness posture both improve materially.

### Gate 3: Reach 85/100
Focus: become a genuinely mature specialized runtime platform.

Required:
- [ ] Critical-path failure and degraded-mode behavior are deeply tested.
- [ ] Runtime core is smaller, simpler, and less coupled.
- [ ] Cross-repo compatibility with `aindy-sdk` and `aindy-ui-kit` is routine and automated.
- [ ] Extension/trust boundaries are strong enough to support broader platform claims.
- [ ] Operational and security guarantees are explicit enough for platform consumers to rely on.

Definition of done:
- The runtime is trusted not just because the team knows it, but because the platform behavior is stable, verified, and constrained.

---

## Priority Worklist

### Highest Priority
- [ ] Narrow and enforce runtime ownership boundary
- [ ] Define canonical execution invariants
- [ ] Deepen verification on scheduler/syscall/recovery/startup paths
- [ ] Raise security/isolation confidence
- [ ] Reduce core architectural debt in bootstrap/config/runtime-critical modules

### Medium Priority
- [ ] Improve artifact validation and compatibility automation
- [ ] Clarify cross-repo contracts with `aindy-sdk` and `aindy-ui-kit`
- [ ] Reduce experimental seams in critical public surfaces

### Lower Priority
- [ ] Broaden external extension claims
- [ ] Add more platform surface area before core hardening
- [ ] Expand runtime responsibilities without removing existing ones

---

## Current Top Blockers

- [ ] Security and isolation posture is not yet strong enough for broader runtime claims.
- [ ] Runtime core still appears to own too much platform surface.
- [ ] Verification depth on critical runtime behavior is not yet at mature-platform level.
- [ ] Core architectural debt is still meaningful, not cosmetic.
- [ ] Cross-repo boundary proof with SDK/UI is not yet strong enough.

---

## Next Review

Review date:
- [ ] YYYY-MM-DD

Reviewer:
- [ ] Name / role

Target score for next review:
- [ ] 75+
- [ ] 80+
- [ ] 85+

Notes:
- [ ] Add release-specific findings here
