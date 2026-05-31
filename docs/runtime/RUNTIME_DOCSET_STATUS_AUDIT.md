# Runtime Docset Status Audit

> Authored by Codex during non coding session. Needs review before repo commit and push.

This document classifies the runtime docset as of `2026-05-29` into:

- `reconciled`
- `partially reconciled`
- `still legacy`

It is the close-out status pass for the runtime governance and alignment work
done in this session.

Important interpretation:

- `reconciled` means the document was either created in this governance pass or
  updated in place so its claims now align with the newer governing posture
- `partially reconciled` means the document is still useful, but has not yet
  been fully normalized against the governing layer and should not be treated as
  a first-stop source of truth without cross-checking the newer docs
- `still legacy` means the document is primarily historical, transitional, or
  superseded in framing, and should be read cautiously until it is either
  reconciled or archived

This is a governance status map, not a full technical re-verification of every
runtime claim.

---

## Governing Layer

These docs now form the governing layer for runtime claim interpretation:

- `RUNTIME_BOUNDARY.md`
- `SECURITY_POSTURE.md`
- `CROSS_REPO_COMPATIBILITY.md`
- `DEGRADED_MODE_MATRIX.md`
- `EXECUTION_INVARIANTS.md`
- `RUNTIME_STABILITY_INDEX.md`
- `RELEASE_GATES.md`
- `TEST_STRATEGY.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `DEPENDENCY_CRITICALITY_MATRIX.md`
- `OPERATOR_RUNBOOK.md`
- `CHANGE_IMPACT_MATRIX.md`
- `INCIDENT_CLASSIFICATION.md`
- `OPEN_QUESTIONS.md`
- `DECISION_LOG.md`
- `RUNTIME_DOCSET_GOVERNANCE.md`
- `RUNTIME_DOC_INDEX.md`

If an older document reads broader than these docs, the governing layer wins.

---

## Executive Summary

### Overall Status

- the runtime docset now has a real governance spine
- the highest-conflict older docs have been reconciled in place
- the next tier of partial-alignment docs has been reconciled in place
- a remaining long tail of useful technical docs still needs normalization
- a small set of clearly older or transitional docs should now be treated as
  legacy until specifically updated

### Practical Reading Rule

Use the docset in this order:

1. `RUNTIME_DOC_INDEX.md`
2. governing-layer docs
3. reconciled older docs
4. partially reconciled technical docs
5. legacy docs only when historical context is needed

---

## Reconciled

These docs are now aligned closely enough with the newer governing posture to
be used as active current-state references.

### New governance and operating docs created in this session

- `RUNTIME_BOUNDARY.md`
- `SECURITY_POSTURE.md`
- `CROSS_REPO_COMPATIBILITY.md`
- `DEGRADED_MODE_MATRIX.md`
- `EXECUTION_INVARIANTS.md`
- `RUNTIME_STABILITY_INDEX.md`
- `RELEASE_GATES.md`
- `TEST_STRATEGY.md`
- `DEPENDENCY_CRITICALITY_MATRIX.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `OPERATOR_RUNBOOK.md`
- `CHANGE_IMPACT_MATRIX.md`
- `INCIDENT_CLASSIFICATION.md`
- `OPEN_QUESTIONS.md`
- `DECISION_LOG.md`
- `RUNTIME_DOC_ALIGNMENT_AUDIT.md`
- `RUNTIME_DOCSET_GOVERNANCE.md`
- `INVARIANT_TEST_MAPPING.md`
- `HIGH_CONFLICT_DOC_RECONCILIATION_PLAN.md`
- `RUNTIME_DOC_INDEX.md`
- `TEST_GAP_BACKLOG.md`
- `TEST_GAP_WORK_ITEMS.md`
- `DOCSET_CHANGELOG.md`
- `RUNTIME_DOCSET_STATUS_AUDIT.md`

### Older docs reconciled in place during this session

- `ARCHITECTURE.md`
- `EXTENSION_TRUST_MODEL.md`
- `REPO_COMPATIBILITY_POLICY.md`
- `DEPLOYMENT_PROFILES.md`
- `PUBLIC_RUNTIME_SURFACES.md`
- `RUNTIME_ONLY_DEPLOYMENT.md`
- `DEGRADED_RUNTIME_MODES.md`
- `SECURITY_POLICY.md`
- `CI_OWNERSHIP.md`
- `AGENT_RUNTIME.md`
- `DB_OWNERSHIP_CONTRACT.md`
- `PUBLIC_API_CONTRACT.md`

### Meaning

These docs may still contain future-state discussion or technical debt notes,
but they now defer appropriately to the narrower support, boundary, stability,
security, and compatibility posture established by the governing layer.

---

## Partially Reconciled

These docs appear useful and mostly directionally compatible, but they have not
yet been explicitly normalized in this session. Treat them as secondary
technical references rather than first-stop support-claim docs.

### Execution and runtime semantics

- `EXECUTION_CONTRACT.md`
- `EXECUTION_AUDIT.md`
- `RUNTIME_BEHAVIOR.md`
- `SYSCALL_SYSTEM.md`
- `RETRY_POLICY.md`
- `IDEMPOTENCY_CONTRACT.md`
- `SCHEMA_LIFECYCLE.md`

### Extension and isolation detail

- `EXTENSION_ABI.md`
- `EXTENSION_CAPABILITIES.md`
- `EXTENSION_PROVENANCE.md`
- `OS_ISOLATION_LAYER.md`

### Memory and bridge detail

- `MEMORY_ADDRESS_SPACE.md`
- `MEMORY_BRIDGE.md`
- `MEMORY_BRIDGE_CONTRACT.md`
- `NATIVE_MEMORY_BRIDGE.md`

### Release and operational auxiliary docs

- `RELEASE_STAGING.md`
- `GITHUB_SETTINGS_CHECKLIST.md`

### Meaning

These docs likely contain valuable technical detail, but they may still:

- predate the newer trust and profile posture
- imply broader support than the runtime should currently claim
- assume older repo-split or legacy-monolith framing
- lack explicit cross-links into the governing layer

Use them with cross-checks against:

- `SECURITY_POSTURE.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `RUNTIME_STABILITY_INDEX.md`
- `CROSS_REPO_COMPATIBILITY.md`
- `RUNTIME_BOUNDARY.md`

---

## Still Legacy

These docs should currently be treated as historical, transitional, or
superseded in framing until they receive an explicit reconciliation pass.

- `LOCAL_AND_CLOUD_AUDIT.md`
- `RUNTIME_DOCSET_BOUNDARY.md`

### Likely reasons

`LOCAL_AND_CLOUD_AUDIT.md`
- likely reflects the older, broader local/cloud ambition framing that the new
  governance layer intentionally narrows

`RUNTIME_DOCSET_BOUNDARY.md`
- likely reflects an earlier split-map or documentation-boundary stage that has
  now been superseded by `RUNTIME_DOCSET_GOVERNANCE.md`,
  `RUNTIME_DOC_INDEX.md`, and the newer boundary/compatibility docs

### Reading rule

Do not use these docs to set current support claims, compatibility expectations,
or platform posture without first checking whether their conclusions are still
supported by the governing layer.

---

## Triage Order For Future Cleanup

If the next pass continues, the best order is:

1. `EXECUTION_CONTRACT.md`
2. `SYSCALL_SYSTEM.md`
3. `OS_ISOLATION_LAYER.md`
4. `EXTENSION_ABI.md`
5. `EXTENSION_CAPABILITIES.md`
6. `RETRY_POLICY.md`
7. `SCHEMA_LIFECYCLE.md`
8. `MEMORY_BRIDGE_CONTRACT.md`
9. `RUNTIME_BEHAVIOR.md`
10. legacy docs last, unless they are still being actively referenced

Reason:

- execution, syscall, and isolation docs are closest to runtime claim-critical
  behavior
- extension docs are next because they can easily overstate support posture
- memory/bridge and auxiliary docs matter, but are less central to the runtime
  maturity claim
- legacy docs should either be reconciled only if still needed or marked more
  explicitly as archival

---

## Current Reading Recommendation

### Use first

- `RUNTIME_DOC_INDEX.md`
- `RUNTIME_DOCSET_GOVERNANCE.md`
- governing-layer docs

### Use second

- reconciled older docs that provide technical depth

### Use cautiously

- partially reconciled docs

### Use as historical context only

- still legacy docs

---

## Final Status

The runtime docset is no longer just a loose collection of architecture and
operations notes. It now has:

- a governing layer
- a compatibility and boundary posture
- explicit degraded-mode and dependency interpretation
- release and test governance
- a reconciliation record
- a status-based way to read the remaining docs safely

That is a meaningful maturity improvement even before any code changes follow.
