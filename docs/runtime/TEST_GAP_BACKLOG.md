---
title: "Test Gap Backlog"
last_verified: "2026-05-31"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Test Gap Backlog

> Authored by Codex during non coding session. Needs review before repo commit and push.

This document turns `INVARIANT_TEST_MAPPING.md` into a concrete backlog of runtime test gaps.

Its purpose is to make the next testing moves actionable instead of leaving them as generic coverage observations.

This is a prioritization document, not a full test plan.

---

## Priority Levels

### `P0`
High-risk runtime truth gap. Should be addressed before claiming stronger maturity.

### `P1`
Important runtime guarantee gap. Should follow immediately after `P0`.

### `P2`
Useful hardening or cleanup gap. Worth doing, but lower leverage than `P0` and `P1`.

---

## P0 Backlog

## TG-001: Startup sequencing truth
**Priority:** `P0`

**Primary invariants**
- `INV-STARTUP-001`
- `INV-READY-001`

**Problem**
Current tests exercise startup and profile behavior, but do not yet defend startup ordering and readiness truth as explicit invariants.

**Existing anchors**
- `tests/unit/test_platform_only_startup.py`
- `tests/unit/test_deployment_profiles.py`
- `tests/unit/test_runtime_packaging.py`
- `tests/unit/test_runtime_schema_contract.py`

**Gap to close**
- verify critical startup dependencies are established before the runtime claims readiness
- verify readiness stays false during restore-pending and equivalent bootstrap-blocking states
- verify metadata surfaces remain distinguishable from execution readiness

**Expected output**
- one focused startup-invariant test module or additions to the startup/profile test cluster

---

## TG-002: Syscall not-ready bootstrap window
**Priority:** `P0`

**Primary invariant**
- `INV-SYSCALL-001`

**Problem**
Syscall contract and dispatch are tested, but the bootstrap timing window around required registration is not strongly defended.

**Existing anchors**
- `tests/api/test_platform_syscall_contract.py`
- `tests/unit/test_syscall_contract.py`
- `tests/integration/test_execution_contract.py`

**Gap to close**
- verify required-syscall absence is surfaced clearly
- verify startup/boot does not silently present required syscall availability when registration is incomplete
- verify not-ready/boot-failure behavior matches profile expectations

**Expected output**
- explicit tests around missing required syscalls after bootstrap

---

## TG-003: Recovery and orphaned-wait visibility
**Priority:** `P0`

**Primary invariants**
- `INV-SCHED-002`
- `INV-EVENT-002`

**Problem**
Resume and cross-instance claim behavior are tested, but explicit orphaned-wait surfacing and unrecoverable waiting-state visibility are not clearly covered.

**Existing anchors**
- `tests/integration/test_multi_instance_resume.py`
- related: `tests/integration/test_system_event_persistence.py`

**Gap to close**
- verify orphaned waiting work is surfaced rather than silently stranded
- verify cleanup/recovery paths behave predictably when waiting state no longer matches execution truth
- verify operator-visible failure semantics where recovery cannot restore correctness

**Expected output**
- dedicated recovery/watchdog/orphan tests

---

## TG-004: Profile-aware readiness under dependency loss
**Priority:** `P0`

**Primary invariants**
- `INV-READY-001`
- `INV-READY-002`
- `INV-EVENT-001`

**Problem**
Readiness and degraded tests exist, but the newer docs require sharper profile-aware truth, especially local-only fallback vs distributed claims.

**Existing anchors**
- `tests/unit/test_runtime_degraded_modes.py`
- `tests/unit/test_deployment_profiles.py`
- `tests/unit/test_event_bus_redis_url.py`
- `tests/integration/test_multi_instance_resume.py`

**Gap to close**
- verify Redis/event-bus loss is handled differently for local vs distributed profiles
- verify local-only fallback never reads as full distributed readiness
- verify dependency loss changes readiness truth consistently with profile contract

**Expected output**
- profile-matrix-style degraded tests

---

## P1 Backlog

## TG-005: Correlation and duplicate wakeup cases
**Priority:** `P1`

**Primary invariants**
- `INV-WAIT-001`
- `INV-WAIT-002`

**Problem**
Event wakeup behavior is partially covered, but correlation-ID and repeated-delivery edge cases remain under-specified in tests.

**Existing anchors**
- `tests/integration/test_multi_instance_resume.py`
- `tests/integration/test_system_event_persistence.py`

**Gap to close**
- correlation mismatch handling
- ambiguous match handling
- repeated event delivery / duplicate wakeup containment
- pre-rehydration buffer overflow/drop semantics if intended to remain visible behavior

---

## TG-006: Direct tenant-isolation regression paths
**Priority:** `P1`

**Primary invariants**
- `INV-TENANT-001`
- `INV-TENANT-002`

**Problem**
Tenant and capability behavior are covered indirectly more than directly.

**Existing anchors**
- `tests/integration/test_request_context.py`
- `tests/integration/test_platform_quickstart.py`
- `tests/unit/test_extension_boundary_contract.py`
- `tests/unit/test_extension_ownership.py`

**Gap to close**
- explicit cross-tenant access rejection
- resumed execution tenant continuity
- capability continuity across direct vs resumed vs extension-mediated paths

---

## TG-007: Readiness and health distinction as a direct contract
**Priority:** `P1`

**Primary invariants**
- `INV-STARTUP-002`
- `INV-READY-001`

**Problem**
Public routes are tested, but health-vs-readiness truth should be asserted as an explicit contract rather than just observed behavior.

**Existing anchors**
- `tests/api/test_version_api.py`
- `tests/unit/test_runtime_public_contract.py`
- `tests/unit/test_runtime_degraded_modes.py`

**Gap to close**
- direct tests proving liveness/metadata may remain available while readiness is blocked
- direct tests ensuring `/ready` reasons align with documented degraded/restore states

---

## P2 Backlog

## TG-008: Deployment-profile-by-profile contract smoke set
**Priority:** `P2`

**Problem**
The newer profile and dependency docs create a clearer support model than the current test organization reflects.

**Gap to close**
- one smoke path per supported profile
- one explicit failure path per unsupported or constrained claim boundary

---

## TG-009: Artifact-level invariant smokes
**Priority:** `P2`

**Problem**
Artifact validation exists conceptually, but the invariant mapping is still source-tree heavy.

**Gap to close**
- installed-artifact checks for `/api/version`, `/health`, `/ready`
- one artifact-level check for required syscall surface

---

## TG-010: Security-posture claim regression set
**Priority:** `P2`

**Problem**
Extension and sandbox tests exist, but the newer `SECURITY_POSTURE.md` creates a cleaner trusted-internal claim ceiling that could be asserted more directly.

**Existing anchors**
- `tests/unit/test_extension_boundary_contract.py`
- `tests/unit/test_extension_hardening.py`
- `tests/unit/test_plugin_sandbox_certification.py`
- `tests/unit/test_sandbox_verification_posture.py`

**Gap to close**
- direct assertions that stronger unsupported claims are not implied by runtime-reported posture

---

## Suggested Execution Order

1. `TG-001` startup sequencing truth
2. `TG-002` syscall not-ready bootstrap window
3. `TG-003` recovery and orphaned-wait visibility
4. `TG-004` profile-aware readiness under dependency loss
5. `TG-006` direct tenant-isolation regression paths
6. `TG-005` correlation and duplicate wakeup cases
7. `TG-007` readiness vs health direct contract
8. `TG-008` to `TG-010`

Reason:
- startup truth, syscall readiness, and recovery truth are the highest-blast-radius runtime gaps
- profile-aware readiness is the next major operator-truth issue
- tenant/capability tests should become more explicit once the core operational path is tighter

---

## What Good Progress Looks Like

This backlog is paying off when:

- `EXECUTION_INVARIANTS.md` stops having major `gap` entries on runtime-critical paths
- release-gate confidence depends less on interpretation
- profile and degraded-mode claims become easier to defend
- runtime maturity increases through stronger guarantees, not just more total tests

---

## Relationship To Other Docs

This document should align with:

- `INVARIANT_TEST_MAPPING.md`
- `EXECUTION_INVARIANTS.md`
- `TEST_STRATEGY.md`
- `RELEASE_GATES.md`
- `DEGRADED_MODE_MATRIX.md`
- `PROFILE_SUPPORT_MATRIX.md`
