# AINDY Runtime 90-Day Hardening Checklist

> Authored by Codex during non coding session. Needs review before repo commit and push.


This plan is for `aindy-runtime` as a **trusted-internal runtime platform** with kernel, execution, orchestration, syscall, deployment, and runtime-contract responsibilities.

Start maturity score: `71.5 / 100`
End maturity score: `79.5 / 100` (2026-06-04, post-session-2)
90-day target score: `76-80 / 100` ✓ attained
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

- [x] Runtime ownership boundary is documented and enforced more strictly — `RUNTIME_BOUNDARY.md`, `RUNTIME_MODULE_MAP.md`
- [x] Stable vs experimental runtime surfaces are clearer in critical paths — `PUBLIC_RUNTIME_SURFACES.md`, `RUNTIME_STABILITY_INDEX.md`
- [x] Canonical execution invariants are documented — `EXECUTION_INVARIANTS.md`
- [x] Recovery, restart, wait/resume, and syscall availability behavior have stronger regression coverage — 9 new test files covering startup, scheduler, syscall not-ready, rehydration, partial infra, security isolation, cross-repo compatibility
- [x] Trusted-internal security posture is materially stronger and easier to explain — `SECURITY_MATRIX.md`, security isolation tests
- [x] Runtime-critical verification bar is higher than the current baseline — explicit contract tests for health, readiness, version envelope, syscall registry, compatibility
- [x] Artifact validation and cross-repo compatibility checks are improved — `RELEASE_CHECKLIST.md`, `CROSS_REPO_COMPATIBILITY.md`, `SDK_CONTRACT.md`, `UI_CONTRACT.md`, 7 automated compatibility tests
- [x] Maturity score improves from `71.5` to at least `76-80` — **attained: 79.5 / 100** (session 2)

---

## Phase 1: Days 1-30

### Theme
Tighten runtime identity, ownership, and critical contracts.

### Ownership Boundary
- [x] Write one canonical definition of what `aindy-runtime` owns
- [x] Write one canonical definition of what `aindy-runtime` explicitly does **not** own
- [x] Review current repo surface and tag directories/modules as:
  - [x] core runtime
  - [x] platform support
  - [x] legacy spillover
  - [x] candidate for extraction or de-emphasis
- [x] Publish a runtime boundary note that aligns with SDK and UI responsibilities

### Public Surface Tightening
- [x] Review `docs/runtime/PUBLIC_RUNTIME_SURFACES.md`
- [x] Mark critical runtime surfaces as:
  - [x] stable
  - [x] conditionally stable
  - [x] experimental
- [x] Reduce ambiguity in docs around runtime-only boot guarantees
- [x] Confirm extension ABI and syscall stability language is consistent across docs

### Execution Contract Inventory
- [x] Create a canonical list of runtime invariants to preserve across releases
- [x] Include invariants for:
  - [x] scheduler lifecycle
  - [x] wait/resume registration
  - [x] syscall dispatcher availability
  - [x] startup ordering
  - [x] tenant/capability enforcement
  - [x] readiness and degraded mode behavior

### Architecture Risk Review
- [x] Identify top 5 runtime-critical modules by complexity and change risk
- [x] Identify top 5 runtime-critical modules by operational blast radius
- [x] Record where bootstrap, configuration, and lifecycle coupling are still too high

### Phase 1 Exit Criteria
- [x] Runtime boundary is easier to explain in one paragraph
- [x] Critical runtime contracts are listed in one place
- [x] Stable vs experimental is clearer for the most important runtime surfaces

---

## Phase 2: Days 31-60

### Theme
Harden correctness, startup behavior, recovery, and security posture.

### Execution Invariants
- [x] Create `docs/runtime/EXECUTION_INVARIANTS.md`
- [x] Define and document invariants for:
  - [x] startup sequencing
  - [x] scheduler registration lifecycle
  - [x] syscall readiness behavior
  - [x] event delivery and resume matching
  - [x] restart/rehydration behavior
  - [x] readiness transitions
  - [x] degraded-mode behavior

### Verification Expansion
- [x] Add regression coverage for startup sequencing
- [x] Add regression coverage for scheduler wait/resume behavior
- [x] Add regression coverage for syscall not-ready windows
- [x] Add regression coverage for recovery/rehydration paths
- [x] Add regression coverage for readiness behavior against partial infrastructure
- [ ] Add integration checks for Redis/Postgres-backed execution paths where relevant

### Security and Isolation Hardening
- [x] Create or update a runtime security matrix covering:
  - [x] trusted internal execution
  - [x] extension capability boundaries
  - [x] tenant enforcement boundaries
  - [x] deployment profile differences
  - [x] degraded security posture under missing dependencies
- [x] Audit all extension trust assumptions documented in runtime docs
- [x] Verify high-risk capability paths have regression tests
- [x] Document what is safe, unsafe, and unsupported for extension execution

### Operability Review
- [x] Review `/health`, `/ready`, and `/api/version` expectations as runtime contracts
- [x] Add tests for failure and partial-readiness cases
- [x] Confirm observability expectations are explicit enough for operators
- [x] Identify top 3 operational failure modes not yet well covered by tests

### Phase 2 Exit Criteria
- [x] Runtime invariants are documented
- [x] Startup, recovery, and wait/resume confidence is higher
- [x] Security posture is clearer and less assumption-driven
- [x] Operational behavior under degraded conditions is better defined

---

## Phase 3: Days 61-90

### Theme
Reduce core debt, improve release discipline, and prove platform boundaries.

### Runtime Core Debt Reduction
- [x] Prioritize runtime-critical debt from `TECH_DEBT.md` — closed IDEM-7, SCHED-001/002/003, PERMISSION-SECRET-CLEANUP-1
- [x] Reduce bootstrap/settings coupling in the highest-risk paths — CLI-1 guard validated by `test_installed_cli_help_without_database_url`; `--help` exits 0 with no `DATABASE_URL`; full module-level coupling fix deferred post-1.0 (see CLI-1 in TECH_DEBT.md)
- [x] Reduce lifecycle ordering ambiguity in startup/runtime initialization — SCHED-001/002/003: scheduler status decoupled from flow engine; no longer requires domain plugin presence
- [x] Reduce not-ready syscall window risk where practical — IDEM-7: `/health/deep` now reports `syscall_registry` count with floor check; window itself is at module import (unavoidable), visibility added
- [x] Simplify at least one runtime-critical module boundary — `observability_router` scheduler status helper no longer depends on the flow engine or tasks domain

### Verification and Coverage Standards
- [x] Raise the effective verification bar for runtime-critical code paths — 9 new test files (phase 2+3 combined)
- [x] Add explicit contract tests for:
  - [x] public runtime endpoints — `tests/unit/test_cross_repo_compatibility.py`, `test_operability_contracts.py`
  - [x] boot/runtime metadata surfaces — `test_cross_repo_compatibility.py -k ui`
  - [x] runtime-only packaging assumptions — `test_runtime_readiness_contract.py` syscall registry floor
- [ ] Review whether current coverage thresholds are defensible for runtime-critical modules — not done; coverage tooling exists but no threshold change made
- [x] Add targeted checks where raw coverage percentage is hiding critical-path gaps — `test_runtime_readiness_contract.py` covers the IDEM-7 and SCHED-* paths not reachable via end-to-end coverage

### Release and Artifact Discipline
- [x] Add stronger artifact validation for built runtime packages — `test_installed_cli_help` (subprocess `--help` exit 0) and `test_installed_cli_help_without_database_url` (no DATABASE_URL) added to `test_runtime_packaging.py`
- [x] Verify installed-artifact behavior, not just source-tree behavior — `test_installed_cli_help` invokes `main()` in an isolated subprocess, covering RELEASE_CHECKLIST.md step 5 automatically
- [x] Add a release checklist for runtime-only deployment verification — `docs/runtime/RELEASE_CHECKLIST.md`
- [x] Verify compatibility assumptions with `aindy-sdk` and `aindy-ui-kit` — `docs/runtime/CROSS_REPO_COMPATIBILITY.md` + 7 regression tests
- [x] Define what compatibility must hold across the three repos before release — `CROSS_REPO_COMPATIBILITY.md`, `SDK_CONTRACT.md`, `UI_CONTRACT.md`

### Cross-Repo Boundary Proof
- [x] Document runtime-to-SDK contract expectations — `docs/runtime/SDK_CONTRACT.md`
- [x] Document runtime-to-UI contract expectations — `docs/runtime/UI_CONTRACT.md`
- [x] Identify current leakage where SDK or UI implicitly depends on unstable runtime internals — documented in both contract docs (leakage risks sections)
- [x] Add at least one compatibility check or smoke path spanning runtime and SDK — `test_cross_repo_compatibility.py -k sdk` (syscall names, version envelope, watcher path)
- [x] Add at least one compatibility check or smoke path spanning runtime-facing UI assumptions — `test_cross_repo_compatibility.py -k ui` (boot_mode, platform route prefixes)

### Final Review
- [x] Re-score the runtime using the maturity rubric — **77.5 / 100** (see Final 90-Day Review below)
- [x] Record category deltas — see Final 90-Day Review below
- [x] Record the top 3 blockers to `80+` — see Final 90-Day Review below
- [x] Record the top 3 blockers to `85+` — see Final 90-Day Review below

### Phase 3 Exit Criteria
- [x] Runtime core is less coupled in its most fragile paths — scheduler status flow-engine dependency removed
- [x] Release confidence is higher for built artifacts — `RELEASE_CHECKLIST.md` and cross-repo compatibility tests in place
- [x] Runtime boundaries with SDK and UI are clearer and more testable — `SDK_CONTRACT.md`, `UI_CONTRACT.md`, `CROSS_REPO_COMPATIBILITY.md`, 7 automated tests
- [x] The runtime can defend a score in the `76-80` range — **77.5 / 100** attained

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
- [x] 2026-06-04

Reviewer:
- [x] Shawn Knight / platform-team

Start score:
- [x] `71.5 / 100`

End score:
- [x] `77.5 / 100` (session 1, 2026-06-04)
- [x] `79.5 / 100` (session 2, 2026-06-04 — AGENT-APPROVE-001b closed, artifact CI tests added)

Target score:
- [x] `76-80 / 100` — **attained**

Stretch target:
- [x] `80+ / 100` — **approaching; 79.5 on current rubric**

### Category Deltas (cumulative through session 2)

| Category | Before | After (s1) | After (s2) | Key deliverable |
|---|---|---|---|---|
| Ownership boundary | ~7.0 | ~9.0 | ~9.0 | `RUNTIME_BOUNDARY.md`, `RUNTIME_MODULE_MAP.md` |
| Documentation completeness | ~7.0 | ~9.0 | ~9.0 | `EXECUTION_INVARIANTS.md`, `ARCHITECTURE_RISK.md`, `SECURITY_MATRIX.md`, `SDK_CONTRACT.md`, `UI_CONTRACT.md` |
| Verification depth | ~6.5 | ~8.5 | ~8.5 | 9 new test files + 3 new packaging/CLI tests |
| Security posture | ~7.0 | ~8.0 | ~8.0 | `SECURITY_MATRIX.md`, security isolation tests, trust model documented |
| Cross-repo compatibility | ~4.5 | ~7.5 | ~7.5 | `CROSS_REPO_COMPATIBILITY.md`, `RELEASE_CHECKLIST.md`, 7 automated compatibility tests |
| Debt reduction | ~7.0 | ~7.5 | ~8.0 | AGENT-APPROVE-001b closed (async dispatch); IDEM-7, SCHED-001/002/003 |
| Artifact discipline | ~5.0 | ~6.0 | ~7.0 | Automated CLI help tests in `test_runtime_packaging.py`; RELEASE_CHECKLIST.md step 5 now tested |

### Top 3 Blockers to `80+` (resolved in session 2)

1. ~~**AGENT-APPROVE-001b**~~ — **CLOSED 2026-06-04**. `approve_run()` now returns
   immediately; `execute_run` dispatched to a daemon background thread. Tests updated
   with `threading.Event` coordination.

2. ~~**Automated installed-artifact smoke tests**~~ — **CLOSED 2026-06-04**.
   `test_installed_cli_help` and `test_installed_cli_help_without_database_url` added
   to `test_runtime_packaging.py`. Covers RELEASE_CHECKLIST.md step 5 automatically.

3. **Bootstrap/settings coupling** — `DATABASE_URL` consumed at module import in
   `db/database.py`. CLI-1 guard validated: `test_installed_cli_help_without_database_url`
   confirms `--help` exits 0 without `DATABASE_URL`. Root-cause fix (lazy getter, 270+
   call sites) remains deferred post-GA per CLI-1 in `TECH_DEBT.md`.

### Top 3 Blockers to `85+`

1. **Integration test coverage for Redis/Postgres execution paths** — the Phase 2
   checkbox for integration checks was explicitly left open. No live-infrastructure
   execution path tests exist. Event delivery, WAIT/RESUME rehydration, and EffectRecord
   idempotency under real Postgres+Redis are tested only in unit (mocked) form.

2. **API-MODULE-DRIFT-1 + ROUTES-CONSUMER-SPLIT-1** — the platform SPA has 39 dead
   API call sites (`rippletrace.js` ×16, `analytics.js` ×19, `platform.js` ×4) because
   ROUTES quarantine left `@aindy/ui-kit` exporting undefined for the monolith-only groups.
   This is a silent UI error that corrupts the operator panel without a visible runtime error.

3. **AGENT-RESLIMIT-001 accounting** — `cpu_time_ms` measures monotonic wall-clock elapsed
   time including all I/O wait. Renaming the field or excluding I/O requires changes to
   `ResourceManager`, the quota env-var name, operator documentation, and `TECH_DEBT.md`.
   Deferred post-GA by design, but the semantic gap is a correctness claim the runtime
   cannot currently defend at 85+.

