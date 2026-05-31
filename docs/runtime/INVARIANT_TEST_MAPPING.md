---
title: "Invariant To Test Mapping"
last_verified: "2026-05-31"
api_version: "1.0"
status: current
owner: "platform-team"
---
﻿# Invariant To Test Mapping

> Authored by Codex during non coding session. Needs review before repo commit and push.

This document maps the runtime invariants in `EXECUTION_INVARIANTS.md` to the current test tree in `tests/`.

Its purpose is to show:

- which invariants already have meaningful coverage
- which invariants are only partially covered
- which invariants still have obvious gaps

This is a mapping document, not a guarantee that current tests are sufficient.

---

## Status Labels

### `covered`
There is at least one reasonably direct existing test target for the invariant.

### `partial`
There is related test coverage, but it does not yet fully defend the invariant as written.

### `gap`
No clear direct existing test target was identified from the current test tree.

---

## Invariant Mapping Table

| Invariant ID | Current Status | Existing Test Targets | Notes |
|---|---|---|---|
| `INV-STARTUP-001` | `partial` | `tests/unit/test_platform_only_startup.py`, `tests/unit/test_deployment_profiles.py`, `tests/unit/test_runtime_packaging.py` | Startup behavior is exercised, but invariant-level ordering and dependency-readiness truth still need tighter mapping. |
| `INV-STARTUP-002` | `covered` | `tests/api/test_version_api.py`, `tests/unit/test_runtime_public_contract.py` | Metadata and version surfaces are already being exercised; liveness-vs-readiness distinction still deserves explicit assertions. |
| `INV-SCHED-001` | `partial` | `tests/integration/test_system_event_persistence.py`, `tests/integration/test_multi_instance_resume.py` | Resume path and scheduler wake-up are covered, but not all lifecycle failure cases. |
| `INV-SCHED-002` | `partial` | `tests/integration/test_multi_instance_resume.py` | Duplicate-claim and cross-instance resume behavior are tested; restart/recovery idempotence is still weaker than the invariant demands. |
| `INV-WAIT-001` | `partial` | `tests/integration/test_system_event_persistence.py`, `tests/integration/test_multi_instance_resume.py` | Event wait matching exists in tests; correlation-model and ambiguity cases need more explicit coverage. |
| `INV-WAIT-002` | `partial` | `tests/integration/test_multi_instance_resume.py` | Duplicate wakeup resistance is partly covered through cross-instance claim behavior. Buffer-overflow and repeated-delivery cases still look open. |
| `INV-SYSCALL-001` | `partial` | `tests/api/test_platform_syscall_contract.py`, `tests/unit/test_syscall_contract.py`, `tests/integration/test_execution_contract.py` | Syscall contract is tested, but bootstrap not-ready windows and required-registration timing need sharper coverage. |
| `INV-SYSCALL-002` | `covered` | `tests/unit/test_syscall_contract.py`, `tests/integration/test_execution_contract.py`, `tests/integration/test_platform_quickstart.py`, `tests/integration/test_idempotency_gate_e2e.py`, `tests/unit/test_idempotency_gate.py` | Dispatcher envelope, contract, auth/scope, and idempotency paths already have real coverage. |
| `INV-TENANT-001` | `partial` | `tests/integration/test_request_context.py`, `tests/integration/test_multi_instance_resume.py`, `tests/integration/test_platform_quickstart.py` | Tenant continuity and scoped execution are present indirectly; explicit tenant-isolation regression targets still look thin. |
| `INV-TENANT-002` | `partial` | `tests/integration/test_platform_quickstart.py`, `tests/unit/test_extension_boundary_contract.py`, `tests/unit/test_extension_ownership.py` | Capability behavior is covered in pieces, but not yet as a unified cross-path invariant. |
| `INV-EVENT-001` | `partial` | `tests/unit/test_event_bus_redis_url.py`, `tests/integration/test_multi_instance_resume.py`, `tests/integration/test_system_event_persistence.py` | Event path exists in tests, but deployment-profile-specific delivery guarantees are not fully pinned down. |
| `INV-EVENT-002` | `gap` | related: `tests/integration/test_multi_instance_resume.py` | Orphaned-wait surfacing and explicit unrecoverable-state visibility are not clearly defended by the current test tree. |
| `INV-READY-001` | `partial` | `tests/api/test_version_api.py`, `tests/unit/test_runtime_degraded_modes.py`, `tests/unit/test_deployment_profiles.py`, `tests/unit/test_runtime_schema_contract.py` | Readiness-related behavior exists, but profile-by-profile readiness truth still needs stronger direct tests. |
| `INV-READY-002` | `partial` | `tests/unit/test_runtime_degraded_modes.py`, `tests/unit/test_deployment_profiles.py` | Degraded classification exists in tests, but safe-vs-unsafe operational meaning is broader in the doc than in the current test tree. |

---

## Existing Test Clusters Worth Reusing First

These are the best current anchors for future invariant hardening.

### Startup / Boot / Profile Cluster
- `tests/unit/test_platform_only_startup.py`
- `tests/unit/test_deployment_profiles.py`
- `tests/unit/test_runtime_packaging.py`
- `tests/unit/test_runtime_schema_contract.py`

Best use:
- startup sequencing
- runtime-only boot expectations
- dependency/profile gating
- schema-readiness-related boot truth

### Public Contract / Health / Version Cluster
- `tests/api/test_version_api.py`
- `tests/api/test_platform_syscall_contract.py`
- `tests/unit/test_runtime_public_contract.py`
- `tests/unit/test_runtime_compatibility_metadata.py`
- `tests/unit/test_runtime_degraded_modes.py`

Best use:
- `/api/version`
- `/health`
- public contract inventory
- readiness/degraded semantics
- stable vs experimental signaling

### Scheduler / Resume / Event Cluster
- `tests/integration/test_multi_instance_resume.py`
- `tests/integration/test_system_event_persistence.py`

Best use:
- wait/resume behavior
- event delivery
- cross-instance claim behavior
- scheduler wake-up semantics

### Syscall / Idempotency / Enforcement Cluster
- `tests/unit/test_syscall_contract.py`
- `tests/integration/test_execution_contract.py`
- `tests/integration/test_platform_quickstart.py`
- `tests/integration/test_idempotency_gate_e2e.py`
- `tests/unit/test_idempotency_gate.py`

Best use:
- syscall envelope and validation behavior
- capability/scope checks
- idempotency gate behavior
- runtime-facing syscall contract stability

### Extension / Trust / Boundary Cluster
- `tests/unit/test_extension_boundary_contract.py`
- `tests/unit/test_extension_hardening.py`
- `tests/unit/test_extension_ownership.py`
- `tests/unit/test_extension_provenance.py`
- `tests/unit/test_plugin_host.py`
- `tests/unit/test_plugin_sandbox_certification.py`
- `tests/unit/test_sandbox_runner.py`
- `tests/unit/test_sandbox_verification_posture.py`

Best use:
- extension posture checks
- trust-model claims
- ownership and provenance rules
- sandbox posture and plugin host behavior

---

## Highest-Leverage Gaps

These are the best next mapping-driven test gaps to close.

### 1. Startup Readiness Truth
Main invariants:
- `INV-STARTUP-001`
- `INV-READY-001`

Why high leverage:
- startup truth and readiness truth are core operator contracts
- they affect release gates, degraded-mode interpretation, and profile support

### 2. Recovery / Orphaned Wait Visibility
Main invariants:
- `INV-SCHED-002`
- `INV-EVENT-002`

Why high leverage:
- these are high-risk failure modes
- they directly affect stranded work and operator truth

### 3. Syscall Not-Ready Window
Main invariant:
- `INV-SYSCALL-001`

Why high leverage:
- current runtime docs explicitly care about bootstrap correctness and required syscall registration
- this is a small area with high semantic risk

### 4. Profile-Aware Readiness Under Dependency Loss
Main invariants:
- `INV-READY-001`
- `INV-READY-002`
- `INV-EVENT-001`

Why high leverage:
- this is where local-only vs distributed truth can drift
- it ties together profiles, degraded mode, and operator behavior

### 5. Explicit Tenant Isolation Regression Paths
Main invariants:
- `INV-TENANT-001`
- `INV-TENANT-002`

Why high leverage:
- current tests appear to cover tenant/capability behavior indirectly more than directly
- a runtime with strong trust-language needs clearer isolation regression targets

---

## Suggested Test-Doc Mapping Order

If the next step is to tighten tests against docs, do it in this order:

1. `INV-READY-001`
2. `INV-STARTUP-001`
3. `INV-SYSCALL-001`
4. `INV-SCHED-002`
5. `INV-EVENT-002`
6. `INV-TENANT-001`
7. `INV-TENANT-002`

Reason:
- readiness truth and startup truth are the most operator-visible
- syscall bootstrap correctness is small but dangerous
- recovery/orphan visibility is one of the highest-blast-radius weak areas
- tenant/capability coverage should become more direct once the critical operational path is clearer

---

## Practical Interpretation

This repo already has a meaningful runtime test base.

The issue is not absence of tests.
The issue is that the tests were not previously organized around the runtime invariants now documented.

That means the highest leverage is:
- not writing hundreds more tests
- but explicitly binding the dangerous runtime truths to the best existing test clusters and then filling the clearest gaps

---

## Relationship To Other Docs

This document should align with:

- `EXECUTION_INVARIANTS.md`
- `TEST_STRATEGY.md`
- `RELEASE_GATES.md`
- `DEGRADED_MODE_MATRIX.md`
- `PROFILE_SUPPORT_MATRIX.md`
