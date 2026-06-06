---
title: "Open Questions"
last_verified: "2026-06-05"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Open Questions

This document tracks the highest-leverage unresolved questions affecting the maturity of `aindy-runtime`.

Its purpose is to make uncertainty explicit instead of allowing it to remain hidden inside implementation sprawl, release friction, or vague platform language.

This is a strategic uncertainty document, not a backlog dump.

---

## Canonical Principle

A runtime becomes harder to mature when major open questions are left implicit.

The goal of this document is to surface the questions that most directly affect:

- runtime scope
- runtime guarantees
- security claims
- profile support
- release discipline
- downstream compatibility

These are the questions that should shape decisions, not merely follow them.

---

## Highest-Priority Open Questions

## 1. What Is The Narrowest Defensible Scope Of `aindy-runtime`?

**Status: RESOLVED (2026-06-05)**

The repo split and `RUNTIME_BOUNDARY.md` answer this. The runtime is the execution
substrate: SyscallDispatcher, flow engine, memory, scheduler, kernel, platform layer,
readiness, and health surfaces. It does not own SDK ergonomics, UI components, app
deployment assets (`aindy_plugins.json`, `apps.bootstrap`, `alembic/`, `client/`),
or backend convenience surfaces that are not execution-critical. Those live in
`aindy-apps-monolith`, `aindy-sdk`, or `aindy-ui-kit`. The `README.md` "Ownership
Boundary" section and `docs/runtime/RUNTIME_BOUNDARY.md` are the canonical references.

---

## 2. Which Runtime Surfaces Are Truly Stable Enough For Downstream Reliance?

**Status: SUBSTANTIALLY RESOLVED (2026-06-05)**

Stable surfaces are declared in `docs/runtime/SDK_CONTRACT.md`,
`docs/runtime/UI_CONTRACT.md`, and `docs/runtime/PUBLIC_RUNTIME_SURFACES.md`.
Machine-verified in `tests/unit/test_cross_repo_compatibility.py` — breakage on these
surfaces is a release failure. Experimental surfaces are explicitly not promised and
not tested as stable contracts. The remaining open edge is
`AGENT-EVAL-001` / `AGENT-API-001` — agent surfaces partially consumed by the SPA
without a stable surface declaration.

---

## 3. How Far Does The Runtime Want To Go On Extension Support?

**Status: RESOLVED (2026-06-05)**

The committed answer: constrained trusted-internal extension runtime, not a general
extension platform. This is stated explicitly in `README.md` ("not a hardened
third-party extension platform"), enforced by the C3 sandbox escape suite
(`pytest -m sandbox_escape`), and reflected in `PROFILE_SUPPORT_MATRIX.md` marking
"Third-party plugin host" and "Hostile multitenant compute substrate" as unsupported.
`AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS=true` is a trusted-code override, not a safe
third-party extension mode — documented in `README.md` and `SECURITY_MATRIX.md`.

---

## 4. What Is The Real Supported Distributed Profile?

**Status: RESOLVED (2026-06-05)**

`PROFILE_SUPPORT_MATRIX.md` documents the exact prerequisites: primary DB, schema
readiness, Redis/event bus, worker presence where required, required syscalls, and
restore/rehydration correctness. Allowed fallback (local-only degraded liveness) and
what invalidates full distributed support are both explicit. The operator runbook
(`OPERATOR_RUNBOOK.md`) translates this into actionable triage guidance.

---

## 5. What Security Posture Is The Team Willing To Defend Publicly?

**Status: RESOLVED (2026-06-05)**

Trusted-internal is the maximum honest claim for current releases. Third-party plugin
host and hostile multitenant compute substrate are explicitly unsupported until stronger
isolation is built. Documented in `SECURITY_MATRIX.md` and `PROFILE_SUPPORT_MATRIX.md`.
The C3 sandbox escape suite (`sandbox_escape_test_posture()`) makes the current posture
machine-readable and ties it to the release gate.

---

## 6. What Failure Modes Must Block Readiness Absolutely?

**Status: SUBSTANTIALLY RESOLVED (2026-06-05)**

The `/ready` endpoint enforces: DB unavailable, schema not ready, required syscalls
missing (floor: `SYSCALL_REGISTRY_MIN_COUNT = 17`), restore pending, and
startup_incomplete. Profile-aware degraded-mode guidance lives in
`OPERATOR_RUNBOOK.md` and `PROFILE_SUPPORT_MATRIX.md`. The remaining gap: no single
"readiness blockers by profile" reference table — the behavior is correct and enforced
in code, but the cross-profile declarative summary does not yet exist as a standalone doc.

---

## 7. How Much Test Assurance Is Enough For Runtime-Critical Change?

**Status: SUBSTANTIALLY RESOLVED (2026-06-05)**

`docs/runtime/RELEASE_CHECKLIST.md` formalizes the required bar: branch protection
requires Runtime Lint, Docs Validation, and Runtime Contracts; a version tag additionally
requires Integration Tests, Platform UI Build, Runtime Package Build, Install Smoke,
and the Sandbox Escape Gate. The remaining open question is whether specific runtime-critical
paths (scheduler rehydration, EffectRecord idempotency, distributed resume) need their
own explicit assurance floor beyond the current suite.

---

## 8. What Cross-Repo Breakage Is Acceptable?

**Status: SUBSTANTIALLY RESOLVED (2026-06-05)**

`docs/runtime/CROSS_REPO_COMPATIBILITY.md` and `tests/unit/test_cross_repo_compatibility.py`
answer this. Breakage on stable surfaces (SDK contract, UI contract, public runtime
surfaces) is a release failure. Experimental and undeclared surfaces may change without
a compatibility obligation. The remaining gap: `AGENT-API-001` (agent.js ROUTES
constants) and `API-MODULE-DRIFT-1` (rippletrace.js, analytics.js, platform.js
undefined ROUTES groups) are known cross-repo breakage points not yet behind stable
declarations — tracked in `TECH_DEBT.md`.

---

## Secondary Open Questions

### 9. What Belongs In Runtime-Only Deployment Versus Broader Platform Deployment?

**Status: RESOLVED (2026-06-05)**

Answered by the boot profile split: `platform-only` (runtime-only, no app plugins)
vs `default-apps` (with `apps.bootstrap` loading the 16 domain apps from
`aindy-apps-monolith`). The manifest owns profile selection;
`docs/architecture/BOOT_PROFILES.md` in the monolith documents the distinction.

### 10. Which Legacy Route Groups Are Intentional Runtime Ownership Versus Extraction Candidates?

**Status: RESOLVED (2026-06-05)**

Full inventory in `docs/runtime/ROUTE_OWNERSHIP_INVENTORY.md`. Summary:

- **Core (keep):** `health_router`, `auth_router`, `version_router`, `flow_router`
- **Operator (keep — coupled to live runtime state):** `watcher_router`,
  `observability_router`, `db_verify_router`, `platform_router` (composite)
- **Extraction candidates (`/apps/` layer → `aindy-apps-monolith`):**
  `agent_router` (high readiness, blocker: `AGENT-API-001`),
  `memory_metrics_router` + `memory_trace_router` (high readiness, move with memory),
  `memory_router` CRUD/search (medium — split required; execution endpoints blocked by
  Nodus service interface),
  `coordination_router` (low — `AgentRegistry` model ownership gap)

Route existence no longer implies mature runtime ownership — the inventory makes the
distinction explicit and adds decision rules for future route additions.

### 11. Which Runtime Conditions Should Become Stable Operator-Facing Codes?

**Status: RESOLVED (2026-06-06)**

Implemented in `AINDY/kernel/condition_codes.py`. Nine enum classes cover all
operator-facing runtime strings: `RuntimeConditionCode` (13 codes), `ReadinessBlockerCode`
(10 codes), `ConditionClassification` (3 tiers), `FlowRunStatus`, `AgentRunStatus`,
`SyscallResponseStatus`, `DependencyStatus`, `PublicHealthStatus`, and `AutonomyDecision`.

`startup.py` and `distributed_queue.py` import and use the enum values — no more raw
strings in condition-emitting code paths. Machine-verified by 6 new tests in
`tests/unit/test_cross_repo_compatibility.py`. Reference: `docs/runtime/CONDITION_CODES.md`.

---

## Questions That Need Resolution Before Claiming Higher Maturity

- [x] narrow runtime ownership boundary — resolved
- [x] stable vs conditional runtime surface boundaries — substantially resolved
- [x] supported distributed profile definition — resolved
- [x] maximum defensible security claim — resolved
- [x] absolute readiness blockers by profile — substantially resolved (behavior enforced; declarative table pending)
- [x] cross-repo compatibility commitments — substantially resolved
- [x] extraction candidates for legacy route groups — resolved, see `ROUTE_OWNERSHIP_INVENTORY.md`
- [x] stable operator-facing condition codes (Q11) — resolved 2026-06-06, `AINDY/kernel/condition_codes.py`

---

## How To Use This Document

Use this during:

- architecture review
- release planning
- maturity review
- cross-repo coordination
- security posture review

Good use:
- answering one question enough to improve runtime boundaries or claims

Bad use:
- letting the same unresolved question persist release after release while still broadening claims

---

## Question Smells

These are signs that an open question is being mishandled.

- the runtime is already making a strong claim without answering the underlying question
- SDK or UI behavior depends on an unresolved runtime contract question
- profile support language keeps expanding while readiness truth remains unsettled
- security language gets broader without stronger isolation or enforcement
- implementation keeps moving, but the governing decision is never written down

---

## What Maturity Looks Like

Open-question maturity is reached when:

- major architectural and contract questions are written down early
- the team can point to deliberate decisions instead of accidental outcomes
- unresolved questions shrink over time instead of silently compounding
- claims narrow when answers are missing, rather than widening optimistically

The runtime should increasingly make unknowns visible before they become instability.

---

## Relationship To Other Docs

This document should align with:

- `RUNTIME_BOUNDARY.md`
- `SECURITY_POSTURE.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `RUNTIME_STABILITY_INDEX.md`
- `RELEASE_GATES.md`
- `CHANGE_IMPACT_MATRIX.md`
- `INCIDENT_CLASSIFICATION.md`
