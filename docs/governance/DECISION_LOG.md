---
title: "Decision Log"
last_verified: "2026-09-16"
api_version: "1.0"
status: current
owner: "platform-team"
---
﻿# Decision Log

This document records high-value runtime decisions that should remain visible across refactors, releases, and cross-repo coordination.

Its purpose is to reduce repeated re-litigation of core runtime decisions and to preserve the reasoning behind important boundaries and claims.

This is not a full architecture history. It is a concise decision register.

---

## Canonical Principle

A runtime matures faster when major decisions are explicit, reviewable, and reusable.

The goal of this log is to capture decisions that affect:

- runtime scope
- runtime guarantees
- security posture
- profile support
- release discipline
- downstream compatibility

If a decision changes, the runtime should update the record rather than rely on tribal memory.

---

## Decision Format

Use this structure for each entry:

- `ID`
- `Status`
- `Decision`
- `Why`
- `Implications`
- `Related Docs`

Suggested statuses:

- `accepted`
- `provisional`
- `superseded`
- `declined` — a considered-and-refused option, recorded so it is not re-derived (DEC-010)
- `needs review`

---

## Current Decisions

### DEC-001
**Status:** `accepted`

**Decision**
`aindy-runtime` is a trusted-internal runtime platform, not a hardened third-party in-process extension platform.

**Why**
Current documentation and architecture support a trusted-internal posture, but not a strong general hostile-code extension claim.

**Implications**
- release language must remain narrow
- extension claims must stay constrained
- stronger plugin-host claims require future hardening work

**Related Docs**
- `SECURITY_POSTURE.md`
- `RUNTIME_BOUNDARY.md`

---

### DEC-002
**Status:** `accepted`

**Decision**
Health and readiness are separate contracts and must not be conflated.

**Why**
A live runtime is not automatically safe to receive work. Operational truth depends on readiness, not just liveness.

**Implications**
- `/health` and `/ready` must remain semantically distinct
- degraded-mode handling must preserve truthful readiness
- SDK/UI should not collapse the two concepts

**Related Docs**
- `DEGRADED_MODE_MATRIX.md`
- `CROSS_REPO_COMPATIBILITY.md`
- `OPERATOR_RUNBOOK.md`

---

### DEC-003
**Status:** `accepted`

**Decision**
Local-only fallback must not be treated as equivalent to distributed runtime support.

**Why**
Cross-instance guarantees depend on dependencies and runtime conditions that local-only fallback does not satisfy.

**Implications**
- distributed profiles must require distributed prerequisites
- readiness should narrow when only local-only behavior remains
- operators and downstream consumers must not be misled

**Related Docs**
- `PROFILE_SUPPORT_MATRIX.md`
- `DEGRADED_MODE_MATRIX.md`
- `DEPENDENCY_CRITICALITY_MATRIX.md`

---

### DEC-004
**Status:** `accepted`

**Decision**
Stable runtime surfaces should remain narrow and intentionally governed.

**Why**
A broad stable surface area creates heavy compatibility burden and makes internal cleanup harder.

**Implications**
- route existence is not enough to establish stability
- SDK/UI should depend only on documented stable or conditionally stable surfaces
- internal-only surfaces should stay refactorable

**Related Docs**
- `RUNTIME_STABILITY_INDEX.md`
- `CROSS_REPO_COMPATIBILITY.md`

---

### DEC-005
**Status:** `accepted`

**Decision**
Runtime maturity should be driven by stronger guarantees and narrower scope, not by adding more platform surface area.

**Why**
Current maturity risks are mostly about scope ambiguity, security posture limits, and runtime-critical verification depth.

**Implications**
- new scope should be justified by runtime truth, not convenience
- extraction and boundary discipline are part of maturity
- “bigger runtime” is not the target state

**Related Docs**
- `RUNTIME_BOUNDARY.md`
- `../archive/AINDY_RUNTIME_90_DAY_CHECKLIST.md` (completed 2026-06-04; archived 2026-09-13)

---

### DEC-006
**Status:** `accepted`

**Decision**
Runtime-critical changes must be reviewed by impact, not by code size.

**Why**
Small changes in startup, scheduler, readiness, or security paths can carry much more risk than large local refactors elsewhere.

**Implications**
- change classification should drive review depth
- “small diff” is not a valid proxy for low runtime risk
- release discipline should follow impact class

**Related Docs**
- `CHANGE_IMPACT_MATRIX.md`
- `RELEASE_GATES.md`
- `TEST_STRATEGY.md`

---

### DEC-007
**Status:** `accepted`

**Decision**
Dependency loss must be interpreted through deployment profile, not only through generic service health.

**Why**
The same dependency can be optional in one profile and execution-critical in another.

**Implications**
- dependency classification must be profile-aware
- readiness truth must track profile truth
- operators need profile-sensitive guidance

**Related Docs**
- `DEPENDENCY_CRITICALITY_MATRIX.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `OPERATOR_RUNBOOK.md`

---

### DEC-008
**Status:** `accepted`

**Decision**
Unsupported profiles should be stated plainly rather than implied by omission.

**Why**
Silence around unsupported modes encourages accidental over-claiming.

**Implications**
- hostile multitenant or marketplace-style plugin-host claims remain out of scope unless explicitly revisited
- profile support docs should remain blunt

**Related Docs**
- `PROFILE_SUPPORT_MATRIX.md`
- `SECURITY_POSTURE.md`

---

### DEC-009
**Status:** `provisional`

**Decision**
The minimum stable downstream contract should be anchored first around `/api/version`, `/health`, `/ready`, and documented runtime status semantics.

**Why**
These surfaces are the most important for operator truth and cross-repo coordination, and they are a narrower place to start than freezing large route sets.

**Implications**
- release and compatibility checks should prioritize these surfaces first
- broader route stability can remain narrower or conditional

**Related Docs**
- `CROSS_REPO_COMPATIBILITY.md`
- `RUNTIME_STABILITY_INDEX.md`
- `RELEASE_GATES.md`

---

### DEC-010
**Status:** `accepted` (2026-09-16)

**Decision**
This log is the single register of runtime decisions. A decision made in conversation is recorded
here as `DEC-NNN` **in the PR that acts on it** — the same discipline `changelog.d/` enforces for
changelog entries. `TECH_DEBT.md` entries and `docs/design/` documents keep the decision's
*narrative* inline (they need the context) and **cite the id**; `CLAUDE.md` §Recorded decisions
is an **index** of ids, never the record. Decisions made before this date stay where they are
(`DEC-001..009` here; `TECH_DEBT.md` `DECISIONS-2026-08-01`) with pointers; they are not
back-filled except where a decision from the week this rule was written had no other formatted
home (DEC-011..019 below).

**Why**
Three places held decisions and none had a rule for which (#648 flagged it 2026-09-13). The one
with a format — this log — had not been written to since June, so the six decisions made in the
week of 2026-09-15 landed as `DECIDED` / `DECLINED` markers in prose with no id anyone could
cite. Re-litigation is prevented by a citable id, not by prose.

**Implications**
- a PR that records a decision touches this file; `tests/unit/test_decision_log_integrity.py`
  pins that every `DEC-NNN` cited anywhere in the repo exists here and that ids are unique
- an inline `DECIDED` / `DECLINED` in an entry or design doc should carry `(DEC-NNN)`; the guard
  does not enforce that yet (phase 2 if the markers drift)
- `Status` values: `accepted`, `provisional`, `superseded`, `declined` — `declined` is added for
  a considered-and-refused option, which is what most of the entries below are

**Related Docs**
- `../../CLAUDE.md` §Recorded decisions (the index), §CHANGELOG protocol (the sibling rule)
- `../../TECH_DEBT.md` `DECISIONS-2026-08-01` (the legacy batch, unchanged)

---

### DEC-011
**Status:** `accepted` (2026-09-15, #677 — `WAIT-TYPED-CONTRACT-1` phase 1)

**Decision**
A waiting node's declared `resume_schema` is recorded on the run's **state** under a reserved
key (`__pending_request`), not in a `flow_runs` column.

**Why**
An additive column makes every existing deployment owe `bootstrap-schema --reconcile` (Alembic
0018's operator note) for a defence-in-depth check. The stated cost: the DUR-4 history fold does
not reconstruct the record, so a run recovered from a torn snapshot resumes untyped — absent is
never a mismatch, so the degradation is to the pre-feature behaviour, never a wrong rejection.

**Implications**
- `test_a_state_reconstructed_by_the_fold_resumes_untyped` pins the degradation; changing either
  side (fold or record placement) is a decision, not a drift

**Related Docs**
- `AINDY/core/pending_request.py`; `TECH_DEBT.md` `WAIT-TYPED-CONTRACT-1` phase-1 record

---

### DEC-012
**Status:** `declined` (2026-09-15, #677)

**Decision**
A resumed Nodus script does **not** get its prior `nodus_output_state` seeded back into its
namespace. A re-run starts with an empty namespace plus `nodus_received_events`.

**Why**
The two-phase run-from-the-top shape is the documented contract (`NODUS_DEVELOPER_GUIDE.md` §4,
Tutorial 2). Seeding prior state would re-apply phase 1's effects on every re-run unless the
author guards them. What the script set before it parked stays readable on the waiting run's
`nodus_output_state`; it is not handed back.

**Related Docs**
- `NODUS_DEVELOPER_GUIDE.md` §4; `TECH_DEBT.md` `NODUS-RESUME-BRIDGE-1` (the question's origin)

---

### DEC-013
**Status:** `accepted` (2026-09-15, #678 — `WAIT-PAYLOAD-PATH-1`)

**Decision**
The event bus never carries a resume payload. A payload's home is the durable **row**
(`flow_runs.state["event"]`), written and committed **before** the wake; the wake carries only
`event_type`, `correlation_id`, `run_id`. Any future payload path (webhook, connector, MCP)
writes the row, then wakes by `run_id` — `route_event(run_id=…)` is the reference shape.
`sys.v1.event.emit` stays a payload-less wake by construction: it names an event, not a run.

**Why**
This is exactly what makes a resume reconstructible from `run_id` alone (`FR-15`): the woken run
reads its payload off its own row on whichever instance claims it, after any restart. A payload
on the Redis message would be a second, non-durable copy with a size and ordering question
attached, delivering nothing the row does not.

**Related Docs**
- `AINDY/runtime/flow_engine/event_router.py`; `TECH_DEBT.md` `WAIT-PAYLOAD-PATH-1` close record

---

### DEC-014
**Status:** `accepted` (2026-09-15, #679 — `EU-WAIT-SIGNAL-DEAD-1`)

**Decision**
There is no request-level WAIT. `ExecutionWaitSignal` was **removed** rather than repaired; a
request's execution unit completes when its handler returns, by every path.

**Why**
The promise ("raise from any handler to park the request's unit, to be resumed later") was
unfulfillable by construction — a route has already answered its client before it can raise, and
nothing re-executes a request — nothing in four repos raised it, and its resume callbacks rolled
back on close. Building semantics for a surface with no possible caller would have been
`SUBSTRATE-WITNESS-1`'s shape.

**Implications**
- a request's unit cannot enter `waiting` by any path (pinned behaviourally and by AST)
- `execution.waiting` stays in the frozen-hash event enum with no emitter

**Related Docs**
- `EXECUTION_CONTRACT.md`; `TECH_DEBT.md` `EU-WAIT-SIGNAL-DEAD-1`

---

### DEC-015
**Status:** `declined` (2026-09-15, #680 — `FLOW-PARALLEL-1` phase 3b)

**Decision**
No `SwitchCaseEdgeGroup` type. An ordered list of `{"target", "when": "<name>"}` edges ending in
`when: "default"` **is** a switch-case.

**Why**
The design row came from MAF, where a switch subclasses fan-out because its edges have no
first-match semantics. Ours do: `resolve_next_node` takes the first matching edge in declaration
order, a non-terminal node with no match already fails the run loudly, and the order is already
in the graph signature. A distinct type would be a second spelling of one semantics; if ever
wanted for readability it is registration-time sugar expanding to the `when` list.

**Related Docs**
- `docs/design/FLOW_PARALLEL_DESIGN.md` §6a

---

### DEC-016
**Status:** `accepted` (2026-09-15, #681 — `AUTHORITY-NEGOTIATION-1` phase 2)

**Decision**
The authority WAIT gate's operator decisions are `skip` and `abort`. **Not `grant`** — the gate
cannot widen authority (design §7). **Provide-a-result** (human-as-the-tool) is *deferred*, not
declined: if built, it is a third decision on this gate, never a new gate.

**Why**
`grant` would be a second minting path, the one thing the enforcement matrix's single hard
cryptographic guarantee cannot survive. An asserted result is `EFFECT-PARTIAL-1`'s lie in a nicer
costume until it is designed on its own terms — who vouches, how it is marked, what downstream
steps may assume.

**Related Docs**
- `docs/design/AUTHORITY_NEGOTIATION_DESIGN.md` §5a

---

### DEC-017
**Status:** `accepted` (2026-09-16 — `WAIT-TYPED-CONTRACT-1` / `GUEST-BUILTINS-DEAD-1` step 2; PR to follow)

**Decision**
The guest wait contract is a host function, `await_event(event_type, schema=None)`, layered on
the three state keys (`nodus_wait_requested`, `nodus_wait_event_type`, `nodus_wait_resume_schema`),
which remain the wire contract. It sets the keys and raises to halt the script at the call; on
the resumed run it returns the payload. `nodus_builtins.py` (the raise-based `event.wait()` /
`NodusWaitSignal` design, 530 lines, zero importers) and `WorkerWaitSignal` are deleted.

**Why**
Two facts measured before deciding: nodus 5.13 **swallows host-function exceptions** into an
`{"ok": False}` result, so the raise-based design could never have worked on any version; and
`wait` is a **reserved nodus built-in** (`builtins/coroutine.py`) that cannot be registered over
— the `NODUS-SYS-SURFACE-1` trap under another name. The halt still works because the worker
checks `nodus_wait_requested` before it looks at `ok`. The keys were already the documented API;
the function is the ergonomic surface over them.

**Implications**
- everything before `await_event()` re-runs on resume; the `if received == nil` guard remains
  the safe pattern unless phase-1 effects are mediated (DUR-1/2) — documented, not hidden

**Related Docs**
- `NODUS_DEVELOPER_GUIDE.md` §4; `TECH_DEBT.md` `GUEST-BUILTINS-DEAD-1`, `WAIT-TYPED-CONTRACT-1`

---

### DEC-018
**Status:** `declined` (2026-08-18, *ADK research*; back-filled from `CLAUDE.md`)

**Decision**
No first-non-`None`-wins hook precedence (`HOOK-PRECEDENCE-1`).

**Why**
Our ~40 `register_*` hooks are either keyed (one handler per key — the key disambiguates) or
run-all-and-collect (nothing is discarded). First-wins makes a handler's effect depend on
registration order relative to handlers it cannot see, so one plugin can silently suppress
another and nothing records it. What would change the answer: a genuine policy-arbitration point
where exactly one handler must win and the key cannot say which — none exists; if one appears the
right shape is an explicit declared arbiter (`DISPATCH-ADMISSION-1`'s conclusion).

**Related Docs**
- `TECH_DEBT.md` `HOOK-PRECEDENCE-1`; `COMPARATIVE_RESEARCH_INDEX.md`

---

### DEC-019
**Status:** `declined` (2026-07-12, `ECOGAP-1` phase 3 reframe; back-filled from `CLAUDE.md`)

**Decision**
No kernel deterministic replay (Temporal-style: store non-deterministic results and re-run the
code with them injected).

**Why**
Determinism is a VM concern, not a kernel one; forward-resume never re-executes code, so the
problem does not arise; and it is a constraint on every line of workflow code rather than a
feature. Six audits have cited "replay" meaning three different things — `ECOGAP-1` carries the
taxonomy: (1) event-sourced state fold, **shipped** as DUR-4; (2) deterministic code replay,
**declined**; (3) ordering replay, specified in `FLOW-PARALLEL-1`. The honest residual is the
single re-run node's un-mediated side effects.

**Related Docs**
- `TECH_DEBT.md` `ECOGAP-1`; `docs/design/DURABLE_EXECUTION_PROGRAM.md`

---

## Future Decisions To Record

*(Checked 2026-09-13. Every item below was resolved by 2026-06-06 and none was added here —
the log stopped being where decisions were recorded. They are listed with where each landed,
rather than back-filled as `DEC-010..014`, because a second copy of a decision that already has
a canonical home is the drift this docset keeps paying for.)*

- ~~final runtime ownership boundary after extraction/contraction review~~ — `RUNTIME_BOUNDARY.md`
  and `README.md` §Ownership Boundary (resolved 2026-06-05; `ROUTE_OWNERSHIP_INVENTORY.md` for
  the per-route answer)
- ~~exact distributed profile support definition~~ — `DEPLOYMENT_PROFILES.md`,
  `PROFILE_SUPPORT_MATRIX.md`, enforced by `deployment_contract.py`
- ~~final stable surface list for downstream reliance~~ — `PUBLIC_RUNTIME_SURFACES.md`,
  `SDK_CONTRACT.md`, `UI_CONTRACT.md`, pinned by `tests/unit/test_cross_repo_compatibility.py`
- ~~stronger or revised extension support posture if adopted~~ — the two-tier isolation contract,
  `EXTENSION_TRUST_MODEL.md` and `SANDBOX_CONTRACT.md` (2026-05-23 decision; design record in
  `docs/archive/ISOLATION_MODEL_PLAN.md`)
- ~~explicit readiness-blocker policy by profile~~ — `ReadinessBlockerCode` in
  `AINDY/kernel/condition_codes.py`, documented in `CONDITION_CODES.md` and
  `DEGRADED_MODE_MATRIX.md` (2026-06-06)

**Where decisions are recorded now — DEC-010 (2026-09-16).** Here, as `DEC-NNN`, in the PR
that acts on the decision. `TECH_DEBT.md` entries and `docs/design/` documents keep the narrative
and cite the id; `CLAUDE.md` §Recorded decisions indexes the ids. `TECH_DEBT.md`
`DECISIONS-2026-08-01` (seven owner answers from 2026-08-01) is the one legacy batch outside this
log and stays there with a pointer. `tests/unit/test_decision_log_integrity.py` pins that every
`DEC-NNN` cited anywhere in the repo exists here, once.

---

## When To Add Or Update An Entry

Add or revise a decision when:

- the runtime adopts a new durable boundary
- a support or security claim becomes narrower or stronger
- release discipline changes meaningfully
- a cross-repo compatibility promise is formalized
- a previously provisional decision becomes accepted or is superseded

---

## Decision Smells

These are warning signs that a decision should be logged but is not.

- the same architectural debate keeps recurring
- release reviews depend on unwritten assumptions
- downstream repos rely on behavior that no one has formally accepted as contract
- a profile or security claim is widely used but not explicitly recorded
- maintainers cannot explain why a boundary exists without tracing old context

---

## What Maturity Looks Like

Decision-log maturity is reached when:

- core runtime decisions are easy to find
- maintainers can distinguish accepted decisions from open questions
- important changes are explained in terms of prior decisions rather than memory
- fewer debates depend on historical guesswork

The runtime should increasingly preserve reasoning, not just code.

---

## Relationship To Other Docs

This document should align with:

- `../archive/OPEN_QUESTIONS.md` (all eleven resolved; archived 2026-09-13)
- `RUNTIME_BOUNDARY.md`
- `SECURITY_POSTURE.md`
- `PROFILE_SUPPORT_MATRIX.md`
- `RUNTIME_STABILITY_INDEX.md`
- `RELEASE_GATES.md`
- `CHANGE_IMPACT_MATRIX.md`
