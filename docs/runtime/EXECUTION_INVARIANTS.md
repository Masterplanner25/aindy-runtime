---
title: "Execution Invariants"
last_verified: "2026-05-31"
api_version: "1.0"
status: current
owner: "platform-team"
---
﻿# Execution Invariants

> Authored by Codex during non coding session. Needs review before repo commit and push.


This document defines the runtime behaviors that `aindy-runtime` must preserve across refactors, releases, and deployment profiles.

These are not implementation notes. They are operational and correctness constraints for the runtime.

Scope:

- startup and initialization ordering
- scheduler lifecycle
- wait/resume behavior
- syscall readiness and dispatch behavior
- tenant and capability enforcement
- event delivery and recovery behavior
- readiness and degraded-mode behavior

Status:

- Draft
- Intended audience: runtime maintainers, reviewers, release owners
- Companion docs:
  - `ARCHITECTURE.md`
  - `EXECUTION_CONTRACT.md`
  - `PUBLIC_RUNTIME_SURFACES.md`
  - `SYSCALL_SYSTEM.md`
  - `SECURITY_POLICY.md`

---

## How To Use This Document

Every invariant should have:

- a clear statement of the guaranteed behavior
- explicit scope
- known exceptions
- the enforcement path in code
- tests that verify the behavior
- release impact if broken

Suggested tags:

- `stable`
- `internal-stable`
- `experimental`
- `deployment-dependent`

---

## 1. Startup Sequencing Invariants

### INV-STARTUP-001
**Status:** `draft`

**Invariant**
Runtime startup must initialize critical execution dependencies in a predictable order before accepting execution that depends on them.

**Scope**
- application startup
- runtime-only deployment boot
- background service initialization
- scheduler and event infrastructure initialization

**Must Hold**
- [ ] Critical runtime services are initialized before dependent execution paths are treated as ready.
- [ ] Readiness does not report healthy execution capability before required dependencies are available.
- [ ] Partial startup states are observable and distinguishable from ready states.

**Known Exceptions**
- Event bus startup may degrade to local-only behavior when Redis-backed propagation is not required.
- Testing mode bypasses parts of startup and rehydration.

**Enforcement Path**
- `AINDY/startup.py::_start_event_bus()`
- `AINDY/startup.py::_rehydrate_waiting_state()`
- `AINDY/startup.py::_verify_required_syscalls_registered()`
- `AINDY/routes/health_router.py::_readiness_response()`
- `AINDY/platform_layer/health_service.py::get_readiness_report()`

**Tests**
- [ ] Add startup-order regression tests here
- [ ] Add readiness-before-bootstrap tests here

**Release Impact If Broken**
- Undefined startup behavior, misleading readiness, or execution before dependency availability.

### INV-STARTUP-002
**Status:** `draft`

**Invariant**
Runtime metadata surfaces must remain available even when deeper execution subsystems are not yet fully ready, unless startup has failed completely.

**Scope**
- `/health`
- `/api/version`
- runtime metadata and identity surfaces

**Must Hold**
- [ ] Metadata endpoints fail less broadly than full execution readiness.
- [ ] Operators can distinguish service liveness from execution readiness.

**Known Exceptions**
- Severe startup-fatal conditions may invalidate the assumption that metadata remains reachable.

**Enforcement Path**
- `AINDY/routes/health_router.py::_build_health_response()`
- `AINDY/routes/health_router.py::_readiness_response()`
- `AINDY/platform_layer/health_service.py::derive_public_status()`
- `AINDY/platform_layer/health_service.py::get_readiness_report()`

**Tests**
- [ ] Add endpoint behavior tests here
- [ ] Add liveness-vs-readiness distinction tests here

---

## 2. Scheduler Lifecycle Invariants

### INV-SCHED-001
**Status:** `draft`

**Invariant**
The scheduler lifecycle must not allow a run or execution unit to appear actively resumable unless the scheduler can actually service the resume path.

**Scope**
- wait registration
- resume callback registration
- scheduler engine lifecycle
- rehydration after restart

**Must Hold**
- [ ] Waiting state is not reported as durable unless resume servicing is available or explicitly recoverable.
- [ ] Resume registration failures are observable.
- [ ] Recovery paths re-establish resumability deterministically.

**Known Exceptions**
- Redis wait registration is best-effort; local wait tracking still proceeds when Redis registration fails.
- Pre-rehydration events may be buffered rather than immediately serviced.

**Enforcement Path**
- `AINDY/kernel/scheduler/waits.py::register_wait()`
- `AINDY/kernel/scheduler/core.py::mark_rehydration_complete()`
- `AINDY/startup.py::_rehydrate_waiting_state()`
- `AINDY/core/wait_rehydration.py::rehydrate_waiting_eus()`
- `AINDY/core/flow_run_rehydration.py::rehydrate_waiting_flow_runs()`

**Tests**
- [ ] Add wait registration lifecycle tests here
- [ ] Add post-restart resumability tests here

### INV-SCHED-002
**Status:** `draft`

**Invariant**
Scheduler recovery after restart must not duplicate completed work or silently drop resumable work.

**Scope**
- restart
- rehydration
- cross-instance resume behavior
- persisted waiting state

**Must Hold**
- [ ] Recovery is idempotent within the documented execution model.
- [ ] Previously completed work is not replayed as new work unless explicitly designed.
- [ ] Persisted waiting work is either resumed, retried, or surfaced as recoverable failure.

**Known Exceptions**
- Event bus delivery is best-effort in local-only mode for cross-instance propagation.
- Recovery of orphaned waits depends on persisted waiting state and database availability.

**Enforcement Path**
- `AINDY/kernel/scheduler/recovery.py::recover_orphaned_waits()`
- `AINDY/kernel/scheduler/recovery.py::_check_stale_waits()`
- `AINDY/kernel/scheduler/core.py::mark_rehydration_complete()`
- `AINDY/kernel/scheduler/cross_instance.py::_cross_instance_resume()`
- `AINDY/kernel/event_bus.py` DB-claim model notes and subscriber flow

**Tests**
- [ ] Add restart/rehydration regression tests here
- [ ] Add duplicate-resume prevention tests here

---

## 3. Wait/Resume Invariants

### INV-WAIT-001
**Status:** `draft`

**Invariant**
A registered wait must have a well-defined correlation model for the event or condition that resumes it.

**Scope**
- event waits
- correlation IDs
- resume matching
- time-based waits

**Must Hold**
- [ ] Resume matching rules are deterministic.
- [ ] Correlation semantics are documented for each wait type.
- [ ] Ambiguous matches are rejected or resolved by documented rules.

**Known Exceptions**
- Event matching currently allows broad event-name matching when no correlation ID is set.
- Time waits and event waits follow different matching paths.

**Enforcement Path**
- `AINDY/kernel/scheduler/waits.py::register_wait()`
- `AINDY/kernel/scheduler/waits.py::notify_event()`
- `AINDY/kernel/scheduler/waits.py::tick_time_waits()`
- `AINDY/kernel/scheduler/waits.py::peek_matching_run_ids()`

**Tests**
- [ ] Add matching-behavior tests here
- [ ] Add event-vs-time wait tests here

### INV-WAIT-002
**Status:** `draft`

**Invariant**
Resume signals must not produce uncontrolled duplicate wakeups for the same resumable unit.

**Scope**
- repeated events
- duplicate delivery
- cross-instance event fanout
- retry behavior

**Must Hold**
- [ ] Duplicate resume attempts are either idempotent or explicitly rejected.
- [ ] Repeated event delivery does not create silent state corruption.

**Known Exceptions**
- Cross-instance fanout can deliver multiple notifications; DB-level claim is relied upon as the authoritative anti-duplication gate.
- Pre-rehydration buffering can drop events when the configured buffer is full.

**Enforcement Path**
- `AINDY/kernel/scheduler/waits.py::notify_event()`
- `AINDY/kernel/scheduler/core.py::mark_rehydration_complete()`
- `AINDY/kernel/event_bus.py` duplicate-prevention notes
- `AINDY/kernel/scheduler/cross_instance.py::_cross_instance_resume()`

**Tests**
- [ ] Add duplicate-delivery tests here
- [ ] Add pre-rehydration buffer overflow tests here

---

## 4. Syscall Readiness And Dispatch Invariants

### INV-SYSCALL-001
**Status:** `draft`

**Invariant**
A syscall must not be reported as available before its registry and dispatch prerequisites are actually ready.

**Scope**
- syscall registry initialization
- dispatcher availability
- startup race windows
- extension registration

**Must Hold**
- [ ] Availability signaling is consistent with actual dispatch capability.
- [ ] Not-ready states are explicit, not silent misroutes.
- [ ] Startup race windows are bounded and testable.

**Known Exceptions**
- Current repo debt acknowledges a not-ready syscall window risk during bootstrap.
- Missing required syscalls may be logged or treated as fatal depending on deployment posture.

**Enforcement Path**
- `AINDY/startup.py::_verify_required_syscalls_registered()`
- `AINDY/kernel/syscall_registry.py`
- `AINDY/kernel/syscall_dispatcher.py::dispatch()`
- `AINDY/kernel/syscall_dispatcher.py::_dispatch()`

**Tests**
- [ ] Add syscall-not-ready tests here
- [ ] Add startup registration race tests here

### INV-SYSCALL-002
**Status:** `draft`

**Invariant**
Syscall dispatch must preserve capability, tenant, and schema enforcement before side effects occur.

**Scope**
- dispatcher path
- schema validation
- capability checks
- side-effect recording or idempotency handling

**Must Hold**
- [ ] Validation occurs before unsafe side effects.
- [ ] Capability and tenant checks are not bypassed by alternate dispatch paths.
- [ ] Failed validation leaves a recoverable and observable outcome.

**Known Exceptions**
- Quota backend failures may fail open in development/test modes.
- Output schema validation is non-fatal by design in some paths.

**Enforcement Path**
- `AINDY/kernel/syscall_dispatcher.py::_dispatch()`
- `AINDY/kernel/syscall_dispatcher.py::_validate_runtime_owned_call_metadata()`
- `AINDY/kernel/syscall_dispatcher.py::_resolve_effect_record()`
- `AINDY/kernel/syscall_dispatcher.py::_complete_effect_record()`
- `AINDY/kernel/resource_manager.py`

**Tests**
- [ ] Add dispatcher enforcement tests here
- [ ] Add exact-once idempotency gate tests here
- [ ] Add tenant/capability bypass regression tests here

---

## 5. Tenant And Capability Enforcement Invariants

### INV-TENANT-001
**Status:** `draft`

**Invariant**
Execution state belonging to one tenant must not be observable or mutable by another tenant except through explicitly authorized cross-tenant mechanisms.

**Scope**
- execution units
- flow runs
- event routing
- memory or state lookups
- syscall dispatch

**Must Hold**
- [ ] Tenant context is preserved across execution and resume paths.
- [ ] Cross-tenant access requires explicit, auditable authorization.

**Known Exceptions**
- System-owned runtime contexts may legitimately execute outside normal tenant ownership but must be explicit.

**Enforcement Path**
- `AINDY/kernel/syscall_dispatcher.py::_dispatch()` tenant checks
- `AINDY/kernel/syscall_dispatcher.py::_validate_runtime_owned_call_metadata()`
- `AINDY/kernel/tenant_context.py`
- `AINDY/kernel/scheduler/waits.py` tenant-carrying wait registration

**Tests**
- [ ] Add tenant-isolation tests here
- [ ] Add resumed-execution tenant continuity tests here

### INV-TENANT-002
**Status:** `draft`

**Invariant**
Capability enforcement must remain effective across direct execution, resumed execution, and extension-mediated execution.

**Scope**
- direct runtime execution
- resumed work
- syscall handlers
- extension and plugin boundaries

**Must Hold**
- [ ] Capability checks are applied consistently across execution paths.
- [ ] Resumed execution does not inherit broader permissions than originally granted.

**Known Exceptions**
- Trusted internal extension scenarios may rely on narrower operational controls rather than strong in-process isolation.

**Enforcement Path**
- `AINDY/kernel/syscall_dispatcher.py::_dispatch()` capability checks
- `AINDY/kernel/syscall_registry.py` capability declarations
- `AINDY/docs/runtime/EXTENSION_CAPABILITIES.md`
- `AINDY/docs/runtime/EXTENSION_TRUST_MODEL.md`

**Tests**
- [ ] Add cross-path capability enforcement tests here
- [ ] Add extension-mediated permission regression tests here

---

## 6. Event Delivery And Recovery Invariants

### INV-EVENT-001
**Status:** `draft`

**Invariant**
Event delivery that participates in resume behavior must be either durable enough for the documented deployment profile or explicitly documented as best-effort.

**Scope**
- local event delivery
- distributed event propagation
- restart interaction
- deployment profile differences

**Must Hold**
- [ ] Delivery guarantees are documented by profile.
- [ ] Resume-critical events are not ambiguously treated as durable if they are best-effort.

**Known Exceptions**
- Redis pub/sub transport is explicitly non-fatal and can degrade to local-only mode.
- Local-only mode does not provide cross-instance resume delivery.

**Enforcement Path**
- `AINDY/kernel/event_bus.py::publish()`
- `AINDY/kernel/event_bus.py::start_subscriber()`
- `AINDY/startup.py::_start_event_bus()`
- `AINDY/docs/runtime/DEPLOYMENT_PROFILES.md`

**Tests**
- [ ] Add event-delivery tests by deployment profile here
- [ ] Add local-only degradation tests here

### INV-EVENT-002
**Status:** `draft`

**Invariant**
Recovery logic must surface orphaned or unrecoverable waiting work rather than leaving it indefinitely silent.

**Scope**
- orphan detection
- recovery watchdogs
- stale wait cleanup
- operator visibility

**Must Hold**
- [ ] Unrecoverable waiting state becomes visible.
- [ ] Silent indefinite limbo is treated as a defect state.

**Known Exceptions**
- Some recovery failures currently degrade and log rather than fail startup.
- Visibility may depend on runtime conditions surfacing through health/readiness machinery.

**Enforcement Path**
- `AINDY/kernel/scheduler/recovery.py::recover_orphaned_waits()`
- `AINDY/kernel/scheduler/recovery.py::cleanup_stale_waits()`
- `AINDY/startup.py::_rehydrate_waiting_state()`
- `AINDY/platform_layer/health_service.py` runtime condition reporting

**Tests**
- [ ] Add orphaned-wait detection tests here
- [ ] Add stranded-wait surfacing tests here

---

## 7. Readiness And Degraded-Mode Invariants

### INV-READY-001
**Status:** `draft`

**Invariant**
Readiness must reflect whether the runtime can safely perform the class of work it claims to be ready for.

**Scope**
- readiness endpoint behavior
- dependency availability
- runtime-only deployment
- partial infrastructure conditions

**Must Hold**
- [ ] Ready means execution capability is present for the documented deployment profile.
- [ ] Missing critical dependencies prevent misleading readiness.
- [ ] Partial readiness states are documented where supported.

**Known Exceptions**
- Registry restore may hold readiness at `503 restore_pending` or `503 degraded` before deeper health logic is consulted.
- Some non-critical dependencies are allowed to degrade rather than block readiness.

**Enforcement Path**
- `AINDY/routes/health_router.py::_readiness_response()`
- `AINDY/platform_layer/health_service.py::get_readiness_report()`
- `AINDY/platform_layer/health_service.py::derive_public_status()`
- `AINDY/startup.py` runtime condition publication helpers

**Tests**
- [ ] Add readiness-behavior tests here
- [ ] Add partial-infrastructure readiness tests here

### INV-READY-002
**Status:** `draft`

**Invariant**
Degraded mode must be explicit about what remains safe and what is unavailable.

**Scope**
- degraded startup
- dependency outages
- temporary loss of scheduler/event infrastructure
- runtime-only operational fallback

**Must Hold**
- [ ] Degraded mode is visible to operators.
- [ ] Unsupported operations fail explicitly.
- [ ] Safe operations remain identifiable.

**Known Exceptions**
- Some degraded states are documented as safe degraded and allow continued internal operation.
- Some unsafe degraded states intentionally keep the server running for diagnostics while readiness remains not ready.

**Enforcement Path**
- `AINDY/startup.py::_handle_runtime_degradation()` and runtime condition publication
- `AINDY/platform_layer/health_service.py::get_readiness_report()`
- `AINDY/routes/health_router.py::_build_health_response()`
- `AINDY/routes/health_router.py::_readiness_response()`

**Tests**
- [ ] Add degraded-mode tests here
- [ ] Add safe-vs-unsafe degraded condition tests here

---

## Test Mapping

Populate this section as invariants are adopted.

| Invariant ID | Test File(s) | Test Type | Status |
|---|---|---|---|
| INV-STARTUP-001 |  | startup/regression | pending |
| INV-STARTUP-002 |  | route/health | pending |
| INV-SCHED-001 |  | scheduler/regression | pending |
| INV-SCHED-002 |  | recovery/regression | pending |
| INV-WAIT-001 |  | scheduler/matching | pending |
| INV-WAIT-002 |  | scheduler/duplication | pending |
| INV-SYSCALL-001 |  | bootstrap/dispatch | pending |
| INV-SYSCALL-002 |  | dispatch/security | pending |
| INV-TENANT-001 |  | isolation/regression | pending |
| INV-TENANT-002 |  | capability/regression | pending |
| INV-EVENT-001 |  | integration/deployment-profile | pending |
| INV-EVENT-002 |  | recovery/watchdog | pending |
| INV-READY-001 |  | readiness/integration | pending |
| INV-READY-002 |  | degraded-mode/integration | pending |

---

## Release Review Checklist

Use before runtime releases that touch scheduler, startup, syscalls, events, isolation, or readiness.

- [ ] Which invariants were touched?
- [ ] Were any invariants weakened, expanded, or reinterpreted?
- [ ] Do associated tests still prove the intended behavior?
- [ ] Did any deployment profile change the guarantee level?
- [ ] Do release notes need to call out any contract change?

