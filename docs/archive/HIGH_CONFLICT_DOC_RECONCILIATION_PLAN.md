---
title: "High-Conflict Doc Reconciliation Plan"
last_verified: "2026-05-31"
api_version: "1.0"
status: current
owner: "platform-team"
---
﻿# High-Conflict Doc Reconciliation Plan

This document gives a concrete reconciliation plan for the three older runtime docs currently in the highest conflict with the newer governing runtime docs:

- `ARCHITECTURE.md`
- `EXTENSION_TRUST_MODEL.md`
- `REPO_COMPATIBILITY_POLICY.md`

Its purpose is to reduce claim drift without forcing a full docset rewrite.

This is a targeted reconciliation plan, not the reconciliation itself.

---

## Why These Three First

These three docs create the highest-risk ambiguity because they touch:

- runtime scope and future-state framing
- extension and isolation claims
- cross-repo compatibility framing

If they remain broader than the newer governing docs, the repo will continue telling two different stories at once.

---

## Governing Docs To Reconcile Against

These newer docs should be treated as the current claim ceiling:

- `RUNTIME_BOUNDARY.md`
- `SECURITY_POSTURE.md`
- `CROSS_REPO_COMPATIBILITY.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `RUNTIME_STABILITY_INDEX.md`
- `RUNTIME_DOCSET_GOVERNANCE.md`
- `DECISION_LOG.md`

---

## 1. `ARCHITECTURE.md`

### Current Problem
This doc still reads as though the runtime is equally oriented around:

- current trusted-internal operation
- future cloud control plane posture
- stronger multitenant and hostile-third-party deployment shapes

That makes it too easy for readers to overread future-state architecture as current support reality.

### Reconciliation Goal
Keep the architecture detail, but narrow the support framing.

### Required Changes
- Reframe the opening so current support posture comes before future distribution ambition.
- Label cloud/control-plane sections explicitly as future-state or deferred.
- Remove or tighten any language that sounds like current broad hostile multitenant support.
- Replace broad stable-surface wording with references to:
  - `RUNTIME_STABILITY_INDEX.md`
  - `PUBLIC_RUNTIME_SURFACES.md`
  - `CROSS_REPO_COMPATIBILITY.md`
- Make clear that architecture intent does not equal current supported profile.

### Keep
- useful subsystem layering
- local vs cloud design-history context, if clearly labeled as context rather than support claim
- pointers to runtime docs

### Add
- explicit note that `PROFILE_SUPPORT_MATRIX.md` and `SECURITY_POSTURE.md` govern current claim strength

### Done When
- a reader cannot mistake architecture ambition for present support level
- trusted-internal posture is clearly primary

---

## 2. `EXTENSION_TRUST_MODEL.md`

### Current Problem
This doc contains the sharpest claim drift.

It has detailed and technically useful material, but it also reads too close to supporting:

- stronger third-party plugin-host posture
- production-safe extension language broader than the current governing docs permit
- hostile-third-party-like runtime framing

### Reconciliation Goal
Preserve the technical trust and execution-path detail while clearly subordinating it to the narrower security posture.

### Required Changes
- Put a blunt current-posture summary at the top:
  - trusted-internal runtime first
  - not a hardened arbitrary third-party in-process extension platform
- Separate:
  - current supported trust posture
  - available technical mechanisms
  - future or stronger posture possibilities
- Tighten or relabel any language that sounds like broad current support for stronger third-party plugin-host claims.
- Explicitly align supported extension posture with `PROFILE_SUPPORT_MATRIX.md`.
- Make sure technical mechanism description is not mistaken for supported security certification.

### Keep
- ownership classes
- Tier 1 / Tier 2 distinction if still accurate
- detailed hardening and provenance notes
- operator visibility sections

### Add
- explicit cross-reference to `SECURITY_POSTURE.md` as claim ceiling
- explicit note that stronger container/plugin-host detail does not automatically promote the runtime into a hardened external extension platform

### Done When
- the doc is still technically rich
- but no longer reads as more ambitious than the runtime can honestly claim

---

## 3. `REPO_COMPATIBILITY_POLICY.md`

### Current Problem
This doc is stale against the current repo reality.

It still frames compatibility around:
- future runtime repo
- future apps-monolith repo

The actual active repo picture is now:
- runtime
- SDK
- UI kit

### Reconciliation Goal
Replace stale monolith-future framing with the actual current cross-repo contract model.

### Required Changes
- Rewrite the scope around `aindy-runtime`, `aindy-sdk`, and `aindy-ui-kit`.
- Distinguish:
  - package compatibility
  - API compatibility
  - behavioral/status compatibility
  - stable-vs-incidental downstream dependence
- Defer current authoritative compatibility interpretation to `CROSS_REPO_COMPATIBILITY.md`.
- Keep only the parts still useful as policy-level guidance.

### Keep
- major/minor compatibility caution
- version-range discipline
- operator/tooling usefulness of version metadata

### Remove Or Reframe
- “future apps repo” framing
- any assumption that runtime compatibility is basically runtime-vs-monolith only

### Add
- explicit current repo landscape
- link to `RUNTIME_STABILITY_INDEX.md` and `CROSS_REPO_COMPATIBILITY.md`

### Done When
- the doc reflects the current repo split
- compatibility language is no longer stale or monolith-anchored

---

## Reconciliation Order

Do these in this order:

1. `EXTENSION_TRUST_MODEL.md`
2. `ARCHITECTURE.md`
3. `REPO_COMPATIBILITY_POLICY.md`

Reason:
- extension/security overclaim is the riskiest
- architecture framing is the next-biggest source of ambiguity
- compatibility policy is stale, but easier to correct once the support posture is clearer

---

## Editing Strategy

### Strategy 1: Add Narrowing Language Before Deep Rewrites
First pass should:
- add clear opening posture statements
- add cross-references to governing docs
- mark future-state or broader claims explicitly

That alone will reduce a lot of risk.

### Strategy 2: Preserve Useful Technical Detail
Do not rewrite these into shallow summaries.

The goal is:
- keep the technical value
- remove the broader implied support claims

### Strategy 3: Prefer Claim Tightening Over New Detail
If time is limited, focus on:
- narrowing support language
- updating stale repo references
- clarifying precedence

before adding more explanation.

---

## Fastest Possible First Pass

If only a quick pass is possible:

### `EXTENSION_TRUST_MODEL.md`
- add a top-level warning block pointing to `SECURITY_POSTURE.md`
- mark stronger third-party/plugin-host language as future-state or constrained technical capability, not current broad support

### `ARCHITECTURE.md`
- add a top-level note that architecture intent is broader than current supported runtime posture
- point readers to `PROFILE_SUPPORT_MATRIX.md` and `SECURITY_POSTURE.md` for current claim truth

### `REPO_COMPATIBILITY_POLICY.md`
- add a top-level note that current cross-repo compatibility interpretation is governed by `CROSS_REPO_COMPATIBILITY.md`
- mark monolith-future language as outdated framing pending rewrite

That would already reduce major misreads.

---

## Success Criteria

This reconciliation pass succeeds when:

- older docs stop silently over-claiming
- readers can still get useful technical detail from them
- the runtime’s current supported posture is consistent across the docset
- repo split and downstream contract language reflect current reality

The goal is not to erase history.
The goal is to stop history from acting like current support policy.

---

## Relationship To Other Docs

This plan should be read with:

- `RUNTIME_DOC_ALIGNMENT_AUDIT.md`
- `RUNTIME_DOCSET_GOVERNANCE.md`
- `OPEN_QUESTIONS.md`
- `DECISION_LOG.md`
