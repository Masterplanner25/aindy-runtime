---
title: "Runtime Security Matrix"
last_verified: "2026-06-03"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Runtime Security Matrix

This document maps the five runtime security dimensions to their enforcement
paths, verified test coverage, known limitations, and deployment dependencies.

**Companion docs:**
- `SECURITY_POSTURE.md` — canonical security statement and what the runtime can/cannot claim
- `EXTENSION_TRUST_MODEL.md` — tier 1 vs tier 2 execution and ownership classes
- `EXTENSION_CAPABILITIES.md` — capability set and confinement model for Tier 2
- `EXTENSION_PROVENANCE.md` — provenance and integrity contract

---

## Dimension 1: Trusted Internal Execution

**What it means:** Tier 1 code (`runtime-built-in`, `first-party-app`) runs in the
main interpreter with ambient authority. There is no runtime capability mediation
after registration. The trust model relies on operator control of the deployment.

**Enforcement paths:**
- `AINDY/platform_layer/extension_trust_model.py` — tier classification at load time
- `AINDY/platform_layer/registry.py` — registration-time capability gates for Tier 1
  (these are registration *gates*, not execution-time confinement)
- `AINDY/kernel/syscall_dispatcher.py` — all Tier 1 code reaches syscall via
  `SyscallDispatcher.dispatch()`, where tenant and capability checks still apply

**What holds:**
- Tier 1 code must satisfy registration gates (capability checks at register time)
- After registration, Tier 1 code executes as kernel-resident trusted code — no
  runtime capability mediation applies
- Tenant context (`user_id`) is still required at dispatch time for all callers
  including Tier 1 code

**What does NOT hold:**
- Tier 1 code is not confined at execution time — a misbehaving first-party module
  can bypass tenant and capability rules at the Python level
- The runtime cannot defend against a compromised Tier 1 module
- This is a deliberate design choice for trusted-internal operator deployments

**Test coverage:**
- `tests/unit/test_syscall_not_ready.py::test_missing_user_id_returns_tenant_violation_envelope`
- `tests/unit/test_syscall_not_ready.py::test_missing_capability_returns_error_envelope`
- `tests/unit/test_security_isolation.py::test_quota_backend_failure_fails_open_in_test_mode`

**Gap:** No test verifies that a registered Tier 1 callable is NOT additionally
capability-confined at execution time. This is a design invariant, not a testable
enforcement path.

---

## Dimension 2: Extension Capability Boundaries

**What it means:** Tier 2 (`external-third-party`) code is capability-confined.
It may only invoke syscalls covered by its granted capability set. Internal
runtime objects and privileged context keys are stripped before Tier 2 code sees them.

**Enforcement paths:**
- `AINDY/platform_layer/extension_boundary.py::sanitize_extension_context()` —
  strips `_BLOCKED_ROOT_KEYS` from the context dict before it reaches extension code
- `AINDY/platform_layer/extension_boundary.py::sanitize_extension_payload()` —
  redacts any object whose `__module__` starts with `AINDY.` or looks like an ORM object
- `AINDY/kernel/syscall_dispatcher.py` — enforces capability set at dispatch time:
  `entry.capability not in context.capabilities` → error envelope, handler not called

**Blocked root keys (stripped unconditionally from extension context):**
`db`, `_db`, `session`, `engine`, `settings`, `config`, `secret`, `secrets`,
`request`, `response`, `app`

**AINDY.* object redaction:** Any object whose `__module__` starts with `AINDY.*`
is replaced with `{"_redacted_type": "<ClassName>"}` before being passed to extension code.

**What holds:**
- Blocked root keys are never exposed to extension context
- AINDY.* runtime objects are never passed raw to extension code
- Capability enforcement prevents unauthorized syscall execution
- ORM objects and SQLAlchemy sessions are explicitly detected and redacted

**Known limitation:** Nested blocked keys (e.g., `{"outer": {"db": ...}}`) are
NOT stripped by key name at depth; instead, non-primitive nested values are
redacted by type. A string value at `{"outer": {"db": "string"}}` will pass
through since it is a primitive. This is intentional — key blocking only applies
at root level.

**Test coverage:**
- `tests/unit/test_security_isolation.py::test_blocked_root_key_is_stripped_from_extension_context` (parametrized over all 11 keys)
- `tests/unit/test_security_isolation.py::test_settings_key_is_stripped_from_extension_context`
- `tests/unit/test_security_isolation.py::test_secret_key_is_stripped_from_extension_context`
- `tests/unit/test_security_isolation.py::test_aindy_object_is_redacted_in_extension_payload`
- `tests/unit/test_security_isolation.py::test_aindy_object_in_dict_is_redacted_in_extension_payload`
- `tests/unit/test_security_isolation.py::test_aindy_object_in_nested_list_is_redacted`
- `tests/unit/test_extension_boundary_contract.py::test_planner_context_provider_receives_sanitized_structured_context`
- `tests/unit/test_extension_boundary_contract.py::test_internal_event_handlers_do_not_receive_db_or_raw_internal_objects`

---

## Dimension 3: Tenant Enforcement Boundaries

**What it means:** Every syscall dispatch requires an authenticated tenant context
(`user_id` present and non-empty). Extension code that invokes syscalls must provide
a `_extension_call` metadata block whose `tenant_user_id` matches the execution
context's `user_id`. Mismatch → `TENANT_VIOLATION`, handler not called.

**Enforcement paths:**
- `AINDY/kernel/syscall_dispatcher.py` Step 2b — `not context.user_id` → TENANT_VIOLATION
- `AINDY/kernel/syscall_dispatcher.py::_validate_runtime_owned_call_metadata()` —
  `tenant_user_id != context.user_id` → TENANT_VIOLATION
- `AINDY/kernel/syscall_registry.py::SyscallContext` — `user_id` is a required field

**What holds:**
- No syscall executes without a verified `user_id`
- Extension calls claiming a different tenant than the execution context are rejected
- Handler is never called when tenant check fails

**Limitation:** Tenant enforcement is a runtime policy control, not a
cryptographic isolation boundary. A compromised Tier 1 module can construct any
`SyscallContext` with any `user_id` it chooses.

**Test coverage:**
- `tests/unit/test_syscall_not_ready.py::test_missing_user_id_returns_tenant_violation_envelope`
- `tests/unit/test_syscall_not_ready.py::test_missing_user_id_handler_is_never_called`
- `tests/unit/test_security_isolation.py::test_extension_runtime_call_with_mismatched_tenant_returns_violation_envelope`
- `tests/unit/test_security_isolation.py::test_extension_runtime_call_with_matching_tenant_passes_gate`

---

## Dimension 4: Deployment Profile Security Differences

**What it means:** Security guarantees differ by deployment profile. The runtime
operates in three deployment profiles: `single-instance`, `distributed-api`, and
`distributed-api-worker`. Some security-adjacent behaviors (quota enforcement,
Redis-backed event isolation) depend on which profile is active.

| Profile | Redis required | Cross-instance event isolation | Quota fail behavior |
|---|---|---|---|
| `single-instance` | No | Local only (in-process dict) | Fail open in dev/test |
| `distributed-api` | Yes | Redis pub/sub | Fail closed in prod |
| `distributed-api-worker` | Yes | Redis pub/sub | Fail closed in prod |

**Enforcement paths:**
- `AINDY/platform_layer/deployment_contract.py::redis_required()` — returns True only
  for `DEPLOYMENT_PROFILE_DISTRIBUTED_API`
- `AINDY/kernel/syscall_dispatcher.py::_quota_backend_failure_may_fail_open()` —
  `settings.is_testing or settings.is_dev` → fail open; production → fail closed
- `AINDY/platform_layer/health_service.py::get_readiness_report()` — redis
  failure blocks readiness only when `redis_required()` is True

**Security implications of single-instance deployment:**
- Redis is not required; cross-instance event synchronization is local-only
- A second instance cannot safely share wait/resume state without Redis
- Quota backend failures fail open in dev/test to avoid blocking development
  — in production, quota backend failures always fail closed

**Test coverage:**
- `tests/unit/test_security_isolation.py::test_quota_backend_failure_fails_closed_in_production`
- `tests/unit/test_security_isolation.py::test_quota_backend_failure_fails_open_in_test_mode`
- `tests/unit/test_partial_infrastructure_readiness.py::test_redis_down_does_not_block_readiness_when_not_required`

---

## Dimension 5: Degraded Security Posture Under Missing Dependencies

**What it means:** When key dependencies are unavailable, the runtime's security
enforcement may degrade in specific ways. This documents what degrades and what
is preserved.

| Dependency down | What degrades | What is preserved |
|---|---|---|
| Postgres | Flow/agent execution fails; EffectRecord idempotency gate fails | Syscall capability/tenant checks still run |
| Redis (non-required profile) | Cross-instance event isolation lost; scheduler fallback to local | All in-process capability and tenant enforcement |
| Redis (required profile) | Readiness gate blocks; 503 until Redis recovers | System is declared not-ready before any execution |
| Quota backend | In prod: all syscall execution blocked; in dev/test: fail open, no quota enforcement | Capability/tenant checks still run regardless |
| Schema check fails | Readiness gate blocks (if `critical=True`); DB migrations may not have run | Runtime reports degraded state before execution |

**Enforcement paths:**
- `AINDY/platform_layer/health_service.py::get_readiness_report()` — `required_failures` accumulates failures
- `AINDY/kernel/syscall_dispatcher.py::_quota_backend_failure_may_fail_open()` — context-aware fail behavior
- `AINDY/routes/health_router.py::_readiness_response()` — `restore_pending` and `registry_restore_incomplete` gates

**What is preserved in all degraded states:**
- Capability enforcement: `entry.capability not in context.capabilities` always blocks
- Tenant enforcement: missing `user_id` always blocks
- `SyscallContractViolation` always propagates out of `dispatch()` (never swallowed)
- `/health` and `/ready` endpoints remain reachable to report degraded state

**What degrades or is not preserved:**
- Idempotency (EffectRecord gate) fails with a DB error — the error is surfaced, not silenced
- Quota enforcement fails open in dev/test when the quota backend is down
- Cross-instance wait/resume coordination is lost without Redis (single-instance fallback only)

**Test coverage:**
- `tests/unit/test_partial_infrastructure_readiness.py` — postgres/schema/redis/multi-failure readiness gates
- `tests/unit/test_syscall_not_ready.py::test_syscall_contract_violation_propagates_through_dispatch`
- `tests/unit/test_operability_contracts.py::test_health_endpoint_returns_503_when_system_is_critical`
- `tests/unit/test_security_isolation.py::test_quota_backend_failure_fails_closed_in_production`

---

## What Is Safe, Unsafe, and Unsupported for Extension Execution

### Safe (Supported)
- **Tier 1 (runtime-built-in / first-party-app):** Python modules deployed by the
  same operator as the runtime. These execute in-process with ambient authority.
  Registration gates apply; execution-time capability confinement does not.
- **Tier 2 dynamic plugin nodes:** External code executed via the isolated
  plugin-host subprocess boundary. Capability-confined, context-sanitized, no
  access to `AINDY.*` internals, AINDY.* module imports blocked.
- **Webhook and contract-driven surfaces:** Data-only registrations. No Python
  execution boundary — intrinsic capability is `outbound.http`.

### Unsafe (Not Supported)
- **Running untrusted Tier 1 code:** Any Python code that is treated as Tier 1
  (`runtime-built-in` or `first-party-app`) executes with ambient authority.
  Operator is responsible for ensuring Tier 1 code is trustworthy.
- **Assuming tenant isolation prevents hostile-code escape:** Tenant checks block
  cross-tenant syscall dispatch, but they do not prevent a compromised Tier 1
  module from reading or writing cross-tenant data at the Python level.
- **Marketplace-style arbitrary third-party in-process execution:** Not supported.
  Third-party code must go through the plugin-host subprocess boundary.

### Unsupported
- **Hostile multitenant compute isolation:** Not claimed. The runtime is designed
  for trusted internal deployments where all tenants are part of the same
  organization.
- **Zero-trust plugin hosting:** Not claimed. Plugin trust is established at
  registration time by operator configuration, not cryptographic attestation.
- **Strong cryptographic extension provenance verification:** The provenance model
  (`EXTENSION_PROVENANCE.md`) records trust origins but does not enforce
  cryptographic signatures in the current implementation.

---

## Security Matrix Summary

| Dimension | Enforcement Level | Test Coverage | Known Gap |
|---|---|---|---|
| Trusted internal execution | Design + registration gates | Partial | Tier 1 ambient authority is a design choice, not a testable enforcement path |
| Extension capability boundaries | Enforced: sanitization + capability check | Covered | Nested key blocking only applies at root level |
| Tenant enforcement boundaries | Enforced: user_id + metadata check | Covered | Cannot prevent compromised Tier 1 code from forging context |
| Deployment profile differences | Enforced: redis_required + quota fail policy | Covered | Redis-backed quota enforcement not tested in distributed profile |
| Degraded security posture | Partial: readiness gates + capability/tenant preserved | Covered | Quota fail-open in dev/test is a known deliberate degradation |
