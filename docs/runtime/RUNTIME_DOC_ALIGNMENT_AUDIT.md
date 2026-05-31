# Runtime Doc Alignment Audit

> Authored by Codex during non coding session. Needs review before repo commit and push.

This document audits older runtime docs against the newer governing docs created in this session.

Its purpose is to identify where the existing docset is already aligned, where it is only partially aligned, and where it now conflicts with the narrower runtime maturity posture.

This is an alignment map, not an in-place edit.

---

## Governing Docs Used As Baseline

These newer docs are treated as the current governing layer for this audit:

- `RUNTIME_BOUNDARY.md`
- `SECURITY_POSTURE.md`
- `CROSS_REPO_COMPATIBILITY.md`
- `DEGRADED_MODE_MATRIX.md`
- `EXECUTION_INVARIANTS.md`
- `RUNTIME_STABILITY_INDEX.md`
- `DEPENDENCY_CRITICALITY_MATRIX.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `OPERATOR_RUNBOOK.md`
- `RELEASE_GATES.md`
- `TEST_STRATEGY.md`
- `CHANGE_IMPACT_MATRIX.md`
- `INCIDENT_CLASSIFICATION.md`
- `OPEN_QUESTIONS.md`
- `DECISION_LOG.md`

---

## Audit Labels

### `aligned`
The older doc is directionally consistent with the newer governing layer.

### `partially aligned`
The older doc is mostly useful, but contains scope, claim, or framing drift that should be corrected.

### `conflict`
The older doc now over-claims, misframes, or materially diverges from the newer governing posture.

---

## Executive Summary

### Strongest Existing Docs
These appear easiest to preserve with modest tightening:

- `DEPLOYMENT_PROFILES.md`
- `PUBLIC_RUNTIME_SURFACES.md`
- `RUNTIME_ONLY_DEPLOYMENT.md`
- `DEGRADED_RUNTIME_MODES.md`

### Highest-Risk Docs
These are the most likely to mislead if left unaligned:

- `ARCHITECTURE.md`
- `EXTENSION_TRUST_MODEL.md`
- `REPO_COMPATIBILITY_POLICY.md`

### Main Pattern
The older docs often reflect a broader and more ambitious runtime posture:

- cloud-hosted framing
- stronger extension/isolation language
- broader stable or runtime-owned surface implications
- older monolith/apps compatibility assumptions

The newer governing docs intentionally narrow the runtime claim to:

- trusted-internal runtime platform
- narrower stable downstream contract
- explicit unsupported stronger platform modes
- profile-aware truthfulness over broad future-facing claims

---

## Per-Document Alignment

## 1. `ARCHITECTURE.md`
**Status:** `conflict`

### Why
This doc currently frames `aindy-runtime` around a local + cloud distribution model and discusses:

- cloud-hosted provider-managed operation
- future control plane assumptions
- hostile-third-party deployment profile language
- stable HTTP surfaces that appear broader than the newer stability posture
- multi-tenancy and isolation aspirations that read more ambitious than the newer security baseline

### Main Conflicts
- The newer security posture is intentionally narrower: trusted-internal first.
- The newer profile docs explicitly keep hostile multitenant and marketplace-style claims unsupported.
- The newer stability docs narrow what should count as stable or downstream-safe.
- The newer boundary docs push against “runtime as broad platform center.”

### Recommended Alignment
- Reframe this doc around the current runtime maturity posture, not the broadest intended future.
- Move cloud/control-plane language into clearly labeled future-state or deferred sections.
- Replace broad “all stable HTTP surfaces” phrasing with references to `RUNTIME_STABILITY_INDEX.md` and `PUBLIC_RUNTIME_SURFACES.md`.
- Make unsupported stronger profiles explicit instead of aspirationally adjacent.

### Priority
`high`

---

## 2. `DEPLOYMENT_PROFILES.md`
**Status:** `partially aligned`

### Why
This doc is strong and concrete. It already distinguishes:

- `single-instance`
- `distributed-api`
- `distributed-worker`

and ties them to dependency expectations.

### Gaps
- It does not yet map to the newer support-language distinctions:
  - `supported`
  - `supported with constraints`
  - `unsupported`
- It is operationally strong, but should align more explicitly with `PROFILE_SUPPORT_MATRIX.md` and `DEPENDENCY_CRITICALITY_MATRIX.md`.
- It should reference the difference between boot mode and support claim more explicitly.

### Recommended Alignment
- Add explicit support-level framing.
- Cross-reference profile truth, degraded fallback, and unsupported claims.
- Make clear that profile enforcement does not imply broader security certification.

### Priority
`medium`

---

## 3. `PUBLIC_RUNTIME_SURFACES.md`
**Status:** `partially aligned`

### Why
This doc is already one of the stronger older docs. It is conservative, defines stable/experimental/internal, and clearly states trusted-internal posture.

### Gaps
- It predates the sharper split introduced by `RUNTIME_STABILITY_INDEX.md` and `CROSS_REPO_COMPATIBILITY.md`.
- Its stable surface language is still slightly broader in feel than the newer “minimum stable downstream contract first” posture.
- It mixes public surface declaration with some deeper extension/trust details that now have clearer homes elsewhere.

### Recommended Alignment
- Keep it as the public surface contract, but tighten references to the newer stability index.
- Make clear that route availability does not equal broad downstream compatibility.
- Reference `CROSS_REPO_COMPATIBILITY.md` for what SDK/UI are actually allowed to rely on.

### Priority
`medium`

---

## 4. `RUNTIME_ONLY_DEPLOYMENT.md`
**Status:** `partially aligned`

### Why
This doc is useful and mostly consistent with the newer runtime-only and profile-aware posture.

### Gaps
- It still mixes runtime contract with frontend behavior and app-shell presentation details.
- That frontend material is less aligned with the newer `RUNTIME_BOUNDARY.md`, which pushes presentation concerns away from runtime ownership.
- It lists mounted routes clearly, but should further distinguish route presence from stability/support level.

### Recommended Alignment
- Keep the boot and contract sections.
- Reduce or move frontend-specific presentation guidance.
- Point stability questions to `RUNTIME_STABILITY_INDEX.md` and cross-repo reliance questions to `CROSS_REPO_COMPATIBILITY.md`.

### Priority
`medium`

---

## 5. `EXTENSION_TRUST_MODEL.md`
**Status:** `conflict`

### Why
This doc contains the largest posture mismatch.

It contains stronger language around:
- production-safe third-party plugin sandbox support
- containerized OCI as production-safe on Windows/macOS when Linux containers are available
- explicit hostile-third-party profile language
- broader extension/isolation calibration tied to cloud-oriented contexts

### Main Conflicts
- The newer `SECURITY_POSTURE.md` deliberately narrows the runtime claim to trusted-internal.
- `PROFILE_SUPPORT_MATRIX.md` marks marketplace-style third-party plugin hosting and hostile multitenant runtime claims as unsupported.
- The newer posture treats stronger extension platform claims as deferred, not current support.

### Recommended Alignment
- Narrow this doc so it describes current supported trust posture first, not the strongest possible container-backed scenario.
- Explicitly separate current trusted-internal support from future stronger plugin-host ambitions.
- Remove or strongly qualify language that reads like broad supported hostile-third-party posture.

### Priority
`critical`

---

## 6. `SECURITY_POLICY.md`
**Status:** `partially aligned`

### Why
This doc is mostly about vulnerability response rather than runtime posture, so it does not conflict deeply.

### Gaps
- It should align terminology with `SECURITY_POSTURE.md`.
- It currently frames cloud-hosted policy as future and local-install as present, which is compatible, but the posture wording should reference the current trusted-internal runtime claim more explicitly.
- Accepted-finding rationales should avoid relying on broader implied deployment assumptions than the newer profile docs allow.

### Recommended Alignment
- Cross-reference `SECURITY_POSTURE.md` explicitly.
- Normalize terminology around trusted-internal supported posture.

### Priority
`low` to `medium`

---

## 7. `REPO_COMPATIBILITY_POLICY.md`
**Status:** `conflict`

### Why
This doc still speaks in terms of the future runtime repo and a future apps-monolith repo.

### Main Conflicts
- The actual repo landscape now includes `aindy-runtime`, `aindy-sdk`, and `aindy-ui-kit`.
- The newer compatibility model is cross-repo and layered, not just runtime vs future monolith.
- The current language is stale relative to the runtime’s actual split.

### Recommended Alignment
- Replace future-monolith framing with the current three-repo reality.
- Align with `CROSS_REPO_COMPATIBILITY.md`.
- Distinguish package compatibility, API compatibility, and downstream semantic compatibility.

### Priority
`high`

---

## 8. `DEGRADED_RUNTIME_MODES.md`
**Status:** `partially aligned`

### Why
This doc remains useful. Its `safe_degraded`, `unsafe_degraded`, and `startup_fatal` classifications still fit well with the newer degraded-mode work.

### Gaps
- It is narrower than the newer `DEGRADED_MODE_MATRIX.md`.
- It reads like the condition-code contract, while the newer doc is the operator/runtime truth contract.
- It should reference profile-aware semantics more directly.

### Recommended Alignment
- Keep this as the condition taxonomy doc.
- Reference `DEGRADED_MODE_MATRIX.md` for operator-facing implications.
- Make explicit that not all degraded states are equivalent across profiles.

### Priority
`medium`

---

## 9. `PUBLIC_API_CONTRACT.md`
**Status:** `partially aligned` (inferred)

### Why
This doc likely still matters, but the newer governance layer shifts attention from monolith/import boundary concerns toward runtime/SDK/UI contract clarity.

### Recommended Alignment
- Ensure it does not imply broader stability than `RUNTIME_STABILITY_INDEX.md`.
- Ensure import-boundary language does not override newer cross-repo contract language.

### Priority
`medium`

---

## 10. `CI_OWNERSHIP.md`
**Status:** `partially aligned`

### Why
The doc is likely still useful, but the newer `TEST_STRATEGY.md` and `RELEASE_GATES.md` raise the bar from “what CI owns” to “what runtime claims must be defended.”

### Recommended Alignment
- Tie CI scope to runtime-critical invariant, degraded-mode, artifact, and compatibility checks.
- Avoid treating coverage thresholds alone as maturity indicators.

### Priority
`medium`

---

## 11. `AGENT_RUNTIME.md`
**Status:** `partially aligned`

### Why
The sampled lines show some useful caution already, including explicit notes that certain checks do not provide sandboxing.

### Gaps
- It may still need boundary alignment against `RUNTIME_BOUNDARY.md` and `SECURITY_POSTURE.md`.
- Agent features are a classic borderline area where runtime truth and product/platform convenience can blur.

### Recommended Alignment
- Ensure the doc distinguishes runtime-owned agent execution semantics from broader agent product behavior.
- Avoid implying stronger extension or isolation claims than the governing security docs allow.

### Priority
`medium`

---

## 12. `DB_OWNERSHIP_CONTRACT.md`
**Status:** `aligned` to `partially aligned`

### Why
This doc appears structurally consistent with the newer boundary and readiness work.

### Recommended Alignment
- Cross-link to `DEPENDENCY_CRITICALITY_MATRIX.md` and `DEGRADED_MODE_MATRIX.md` where schema readiness affects operator truth.

### Priority
`low`

---

## Highest-Leverage Alignment Pass Order

If these older docs are aligned in stages, the best order is:

1. `EXTENSION_TRUST_MODEL.md`
2. `ARCHITECTURE.md`
3. `REPO_COMPATIBILITY_POLICY.md`
4. `DEPLOYMENT_PROFILES.md`
5. `PUBLIC_RUNTIME_SURFACES.md`
6. `RUNTIME_ONLY_DEPLOYMENT.md`
7. `DEGRADED_RUNTIME_MODES.md`
8. `AGENT_RUNTIME.md`
9. `CI_OWNERSHIP.md`
10. `SECURITY_POLICY.md`

Reason:
- the first three create the most dangerous claim drift if left uncorrected
- the next four govern profile/stability/boot truth
- the rest are still important, but less likely to create immediate maturity misreads

---

## Recommended Alignment Strategy

### Strategy 1: Do Not Rewrite Everything At Once
Use the newer docs as governing overlays first.

That means:
- older docs remain useful sources
- but where they conflict, the newer governance docs should win

### Strategy 2: Tighten Claims Before Expanding Detail
When updating older docs, prioritize:
- narrowing claims
- clarifying supported vs unsupported posture
- reducing future-state ambiguity

before adding more architecture detail.

### Strategy 3: Make Cross-References Explicit
The older docs should increasingly defer to the newer governing docs for:

- security claim ceilings
- stability classification
- profile support levels
- degraded-mode truth
- cross-repo compatibility expectations

### Strategy 4: Preserve Useful Technical Detail, Remove Over-Broad Posture Language
Many older docs are technically valuable even where their framing is too ambitious.

Do not discard the useful detail.
Do narrow the claims around it.

---

## Immediate Alignment Recommendations

If only a small pass is possible, make these changes first:

- mark `EXTENSION_TRUST_MODEL.md` as constrained by `SECURITY_POSTURE.md` and `PROFILE_SUPPORT_MATRIX.md`
- mark `ARCHITECTURE.md` as partially future-state and not the authoritative support-claim doc
- mark `REPO_COMPATIBILITY_POLICY.md` as superseded in part by `CROSS_REPO_COMPATIBILITY.md`
- mark `DEGRADED_RUNTIME_MODES.md` as taxonomy-level and `DEGRADED_MODE_MATRIX.md` as operator-truth-level
- mark `PUBLIC_RUNTIME_SURFACES.md` as governed by `RUNTIME_STABILITY_INDEX.md` for stability interpretation

Those alone would reduce a lot of docset ambiguity.

---

## What Good Alignment Looks Like

The runtime docs are well aligned when:

- older docs still provide useful technical detail
- newer docs clearly govern current claims
- no doc implies a stronger supported posture than the runtime can defend
- profile, readiness, security, and compatibility language are consistent across the set
- SDK/UI/runtime boundaries are described as present-day contracts, not future coincidence

The goal is not to erase older docs.
The goal is to stop them from silently setting broader expectations than the runtime should currently carry.

---

## Next Practical Step

The next practical move after this audit is:

1. create a short `DOCSET_PRECEDENCE.md` or `RUNTIME_DOCSET_GOVERNANCE.md` saying which docs govern claims now
2. then do a targeted update pass on the three highest-conflict older docs:
   - `EXTENSION_TRUST_MODEL.md`
   - `ARCHITECTURE.md`
   - `REPO_COMPATIBILITY_POLICY.md`
