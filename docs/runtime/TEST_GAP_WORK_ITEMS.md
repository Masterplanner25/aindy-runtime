# Test Gap Work Items

> Authored by Codex during non coding session. Needs review before repo commit and push.

This document turns `TEST_GAP_BACKLOG.md` into issue-style work items that can be copied into a tracker.

Its purpose is to make the test-gap backlog easy to execute as concrete work rather than as an abstract testing wish list.

---

## Work Item Format

Each item includes:

- title
- priority
- why it matters
- existing anchors
- definition of done

---

## WG-001: Defend startup sequencing and readiness truth
**Priority:** `P0`

**Why it matters**
- startup and readiness are operator-facing core contracts
- maturity claims are weak until startup truth is tested explicitly

**Existing anchors**
- `tests/unit/test_platform_only_startup.py`
- `tests/unit/test_deployment_profiles.py`
- `tests/unit/test_runtime_packaging.py`
- `tests/unit/test_runtime_schema_contract.py`

**Definition of done**
- explicit tests prove readiness stays false during restore-pending or equivalent bootstrap-blocking states
- explicit tests prove critical startup prerequisites are established before the runtime claims readiness
- explicit tests distinguish metadata/liveness from execution readiness

---

## WG-002: Add required-syscall bootstrap window coverage
**Priority:** `P0`

**Why it matters**
- required-syscall registration is a small area with high blast radius
- current contract tests do not fully pin bootstrap not-ready behavior

**Existing anchors**
- `tests/api/test_platform_syscall_contract.py`
- `tests/unit/test_syscall_contract.py`
- `tests/integration/test_execution_contract.py`

**Definition of done**
- tests prove missing required syscalls are surfaced clearly
- tests prove startup/boot does not silently present required syscall availability when registration is incomplete
- tests align with the runtime’s not-ready or boot-failure semantics

---

## WG-003: Add orphaned-wait and recovery visibility tests
**Priority:** `P0`

**Why it matters**
- stranded or silently unrecoverable work is one of the highest-risk runtime failure modes

**Existing anchors**
- `tests/integration/test_multi_instance_resume.py`
- `tests/integration/test_system_event_persistence.py`

**Definition of done**
- tests prove orphaned waiting work is surfaced instead of silently lingering
- tests cover cleanup/recovery behavior when waiting state no longer matches execution truth
- tests prove failure semantics are visible enough for operator interpretation

---

## WG-004: Add profile-aware readiness tests under dependency loss
**Priority:** `P0`

**Why it matters**
- local-only fallback vs distributed truth is one of the easiest places for the runtime to overclaim

**Existing anchors**
- `tests/unit/test_runtime_degraded_modes.py`
- `tests/unit/test_deployment_profiles.py`
- `tests/unit/test_event_bus_redis_url.py`
- `tests/integration/test_multi_instance_resume.py`

**Definition of done**
- tests prove Redis/event-bus loss is handled differently for local vs distributed profiles
- tests prove local-only fallback never reads as full distributed readiness
- tests prove dependency loss changes readiness truth consistently with the documented profile contract

---

## WG-005: Add direct tenant-isolation regression coverage
**Priority:** `P1`

**Why it matters**
- tenant and capability behavior are important enough that indirect coverage is not enough

**Existing anchors**
- `tests/integration/test_request_context.py`
- `tests/integration/test_platform_quickstart.py`
- `tests/unit/test_extension_boundary_contract.py`
- `tests/unit/test_extension_ownership.py`

**Definition of done**
- tests explicitly reject cross-tenant access
- tests prove resumed execution preserves tenant context
- tests prove capability enforcement stays consistent across direct, resumed, and extension-mediated paths where applicable

---

## WG-006: Add correlation and duplicate-wakeup edge-case coverage
**Priority:** `P1`

**Why it matters**
- wait/resume correctness depends on edge cases, not just happy-path wakeups

**Existing anchors**
- `tests/integration/test_multi_instance_resume.py`
- `tests/integration/test_system_event_persistence.py`

**Definition of done**
- correlation mismatch handling is tested
- duplicate or repeated delivery does not cause uncontrolled wakeups
- ambiguous wait matching behavior is either documented and tested or constrained

---

## WG-007: Make health-vs-readiness distinction a direct tested contract
**Priority:** `P1`

**Why it matters**
- current docs now treat this distinction as foundational

**Existing anchors**
- `tests/api/test_version_api.py`
- `tests/unit/test_runtime_public_contract.py`
- `tests/unit/test_runtime_degraded_modes.py`

**Definition of done**
- tests explicitly prove liveness/metadata can remain available while readiness is false
- tests explicitly prove readiness reasons match documented degraded and restore states

---

## WG-008: Add deployment-profile smoke set
**Priority:** `P2`

**Why it matters**
- profile support is now clearer in docs than in the current test organization

**Definition of done**
- one smoke path exists per supported profile
- at least one explicit failure path exists per unsupported or constrained claim boundary

---

## WG-009: Add installed-artifact runtime surface smokes
**Priority:** `P2`

**Why it matters**
- runtime confidence should not stay source-tree-only

**Definition of done**
- installed-artifact smokes cover `/api/version`, `/health`, and `/ready`
- one installed-artifact check covers required syscall surface availability

---

## WG-010: Add direct security-posture claim regression checks
**Priority:** `P2`

**Why it matters**
- the newer security posture is narrower and should be defended explicitly

**Existing anchors**
- `tests/unit/test_extension_boundary_contract.py`
- `tests/unit/test_extension_hardening.py`
- `tests/unit/test_plugin_sandbox_certification.py`
- `tests/unit/test_sandbox_verification_posture.py`

**Definition of done**
- tests directly assert that unsupported broader claims are not implied by runtime-reported posture

---

## Suggested Tracker Order

Open in this order:

1. `WG-001`
2. `WG-002`
3. `WG-003`
4. `WG-004`
5. `WG-005`
6. `WG-006`
7. `WG-007`
8. `WG-008`
9. `WG-009`
10. `WG-010`

---

## Relationship To Other Docs

This document should align with:

- `TEST_GAP_BACKLOG.md`
- `INVARIANT_TEST_MAPPING.md`
- `EXECUTION_INVARIANTS.md`
- `TEST_STRATEGY.md`
- `RELEASE_GATES.md`
