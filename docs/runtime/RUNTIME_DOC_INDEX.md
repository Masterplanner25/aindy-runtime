---
title: "Runtime Doc Index"
last_verified: "2026-05-31"
api_version: "1.0"
status: current
owner: "platform-team"
---
﻿# Runtime Doc Index

> Authored by Codex during non coding session. Needs review before repo commit and push.

This is the starting index for the `docs/runtime` docset.

Its purpose is to help readers find the right runtime document quickly and avoid starting from the wrong layer of the docset.

---

## Read This First

If you are new to the runtime docset, start here:

1. `RUNTIME_DOCSET_GOVERNANCE.md`
2. `RUNTIME_BOUNDARY.md`
3. `SECURITY_POSTURE.md`
4. `PROFILE_SUPPORT_MATRIX.md`
5. `RUNTIME_STABILITY_INDEX.md`

These five docs define the current runtime claim ceiling.

---

## By Reader Type

### Maintainers
Start with:
- `RUNTIME_DOCSET_GOVERNANCE.md`
- `RUNTIME_BOUNDARY.md`
- `EXECUTION_INVARIANTS.md`
- `CHANGE_IMPACT_MATRIX.md`
- `RELEASE_GATES.md`
- `TEST_STRATEGY.md`
- `DECISION_LOG.md`
- `OPEN_QUESTIONS.md`

### Operators
Start with:
- `OPERATOR_RUNBOOK.md`
- `DEGRADED_MODE_MATRIX.md`
- `DEPENDENCY_CRITICALITY_MATRIX.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `SECURITY_POSTURE.md`

### SDK / UI Consumers
Start with:
- `CROSS_REPO_COMPATIBILITY.md`
- `RUNTIME_STABILITY_INDEX.md`
- `PUBLIC_RUNTIME_SURFACES.md`
- `PROFILE_SUPPORT_MATRIX.md`

### Developers writing Nodus scripts
Start with:
- `NODUS_DEVELOPER_GUIDE.md`
- `SYSCALL_REFERENCE.md`
- `SYSCALL_SYSTEM.md` (architecture detail)

### Release / QA Reviewers
Start with:
- `RELEASE_GATES.md`
- `TEST_STRATEGY.md`
- `CHANGE_IMPACT_MATRIX.md`
- `INCIDENT_CLASSIFICATION.md`
- `INVARIANT_TEST_MAPPING.md`

---

## Core Governance Layer

These docs define the current governing posture for the runtime.

- `RUNTIME_DOCSET_GOVERNANCE.md`
- `RUNTIME_BOUNDARY.md`
- `SECURITY_POSTURE.md`
- `CROSS_REPO_COMPATIBILITY.md`
- `RUNTIME_STABILITY_INDEX.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `DECISION_LOG.md`
- `OPEN_QUESTIONS.md`

---

## Runtime Truth And Safety

These docs define what the runtime must preserve and how to interpret unsafe or degraded states.

- `EXECUTION_INVARIANTS.md`
- `DEGRADED_MODE_MATRIX.md`
- `DEPENDENCY_CRITICALITY_MATRIX.md`
- `INCIDENT_CLASSIFICATION.md`
- `OPERATOR_RUNBOOK.md`

---

## Release And Verification

These docs define how runtime changes should be reviewed and shipped.

- `RELEASE_GATES.md`
- `TEST_STRATEGY.md`
- `CHANGE_IMPACT_MATRIX.md`
- `INVARIANT_TEST_MAPPING.md`
- `AINDY_RUNTIME_90_DAY_CHECKLIST.md`

---

## Docset Alignment And Reconciliation

These docs explain how the current runtime docset is being tightened.

- `RUNTIME_DOC_ALIGNMENT_AUDIT.md`
- `HIGH_CONFLICT_DOC_RECONCILIATION_PLAN.md`
- `RUNTIME_DOCSET_GOVERNANCE.md`

---

## Developer Reference

Docs for developers integrating with or scripting against the runtime.

- `NODUS_DEVELOPER_GUIDE.md` — writing Nodus scripts in A.I.N.D.Y.: injected globals, built-ins, WAIT/RESUME, error semantics
- `SYSCALL_REFERENCE.md` — all registered syscalls with payloads and return shapes
- `SYSCALL_SYSTEM.md` — dispatcher pipeline, ABI versioning, registration guide

---

## Older Technical Docs Still In Use

These remain important, but should be read through the governing docs above.

- `ARCHITECTURE.md`
- `DEPLOYMENT_PROFILES.md`
- `PUBLIC_RUNTIME_SURFACES.md`
- `RUNTIME_ONLY_DEPLOYMENT.md`
- `EXTENSION_TRUST_MODEL.md`
- `SECURITY_POLICY.md`
- `REPO_COMPATIBILITY_POLICY.md`
- `DEGRADED_RUNTIME_MODES.md`
- `AGENT_RUNTIME.md`
- `CI_OWNERSHIP.md`

---

## Useful Reading Orders

### Fast Maturity Review Order
1. `RUNTIME_BOUNDARY.md`
2. `SECURITY_POSTURE.md`
3. `PROFILE_SUPPORT_MATRIX.md`
4. `RUNTIME_STABILITY_INDEX.md`
5. `DEGRADED_MODE_MATRIX.md`
6. `RELEASE_GATES.md`
7. `OPEN_QUESTIONS.md`

### Fast Operator Truth Order
1. `PROFILE_SUPPORT_MATRIX.md`
2. `DEGRADED_MODE_MATRIX.md`
3. `DEPENDENCY_CRITICALITY_MATRIX.md`
4. `OPERATOR_RUNBOOK.md`
5. `INCIDENT_CLASSIFICATION.md`

### Fast Release Discipline Order
1. `CHANGE_IMPACT_MATRIX.md`
2. `EXECUTION_INVARIANTS.md`
3. `INVARIANT_TEST_MAPPING.md`
4. `TEST_STRATEGY.md`
5. `RELEASE_GATES.md`

---

## Current Highest-Conflict Older Docs

These are the first older docs to reconcile in place:

- `EXTENSION_TRUST_MODEL.md`
- `ARCHITECTURE.md`
- `REPO_COMPATIBILITY_POLICY.md`

Use:
- `RUNTIME_DOC_ALIGNMENT_AUDIT.md`
- `HIGH_CONFLICT_DOC_RECONCILIATION_PLAN.md`

before editing them.

---

## Practical Rule

If you are unsure which doc to trust:

- prefer the newer governing docs
- prefer narrower claims over broader claims
- prefer support matrices and posture docs over aspirational architecture framing
- prefer runtime truth docs over implementation coincidence

That is the current docset operating rule.
