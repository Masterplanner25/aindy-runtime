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
### DEC-020
**Status:** `accepted` (2026-09-16 — FR-31 / `RESUME-FLOW-UNREGISTERED-1`)

**Decision**
`agent_execution` — the AGENT_FLOW backend's `flow_name` — is resolvable for **resume only**
(`resolve_resumable_flow()`) and is never registered into the public `FLOW_REGISTRY`.
`nodus_execute` is registered at boot.

**Why**
A rehydrated resume must find the flow by the row's name, and `agent_execution` had never been
registered anywhere — so a gate-parked agent run could not survive a restart. The obvious fix,
registering it beside `nodus_execute`, would have made it startable through `sys.v1.flow.run` by
any holder of `flow.run`; `agent_execute_step` checks tool capability only when the state carries
an `execution_token`, so that path runs tools with no token — approval bypassed. Resume-only
resolution gives rehydration the dict without giving `flow.run` the name.

**Implications**
- `test_agent_execution_is_resolvable_for_resume_but_never_publicly_registered` pins both halves
- any future runtime-owned flow that is started with a direct `flow=` and labelled by name must go
  through `resolve_resumable_flow`, not `register_flow`, unless it is meant to be publicly startable

**Related Docs**
- `AINDY/runtime/nodus_execution_service.py` (`ensure_runtime_flows_registered`, `resolve_resumable_flow`)
- `TECH_DEBT.md` `FR-31 / RESUME-FLOW-UNREGISTERED-1`

### DEC-021
**Status:** `declined` (2026-09-16 — from the app's `TASK-EU-NOT-PERSISTED-1`, #363)

**Decision**
`_STATUS_TRANSITIONS` gains no `waiting → completed` edge. An execution unit completes only
from `executing`; a `waiting` unit reaches a terminal state either by resuming
(`resume_execution_unit()` → `resumed → executing`) and then completing, or by `failed`.

**Why**
`waiting` is a runtime OBLIGATION, not a display state: it means the unit is parked on an event
the scheduler will deliver and its work is unfinished. `waiting → failed` exists because
abandoning a parked unit is a truthful terminal exit; "completed while parked" would not be,
and no runtime path needs it. Adding the edge would let a parked flow or agent unit be finalised
without ever resuming, and would erase the `waiting`/`resumed` distinction the audit trail is
documented to preserve (`WHAT_THE_RUNTIME_IS.md` §"a real status machine").

The app hit this because it maps a paused human task onto `waiting` (`task_service.pause_task`)
and then steps `waiting → executing → completed` on complete, through the edge kept for
backward compatibility — the one path that skips `resumed`. The mismatch is the mapping: a
paused task is not parked on a runtime event (no `wait_condition`, nothing will wake it). The
consumer-side shapes that are truthful: leave the unit `executing` across a pause, or call
`resume_execution_unit()` before completing.

**Implications**
- `tests/unit/test_eu_transitions_dec021.py` pins the absent edge and the two truthful exits
- the compat edge `waiting → executing` stays: the runtime's own gate re-entry uses it
  (`execution_gate.py::require_execution_unit` on an existing unit)
- app handoff (next release) carries the two consumer-side shapes

**Related Docs**
- `AINDY/core/execution_unit_service.py` (`_STATUS_TRANSITIONS`, `resume_execution_unit`)
- `docs/runtime/WHAT_THE_RUNTIME_IS.md`

---
### DEC-022
**Status:** `accepted` (2026-09-16 — follows `SYSTEM-STATE-TENANT-1`, #692)

**Decision**
`sys.v1.agent.list_recent_durations` is REMOVED from `SYSCALL_REGISTRY`;
`SYSCALL_REGISTRY_MIN_COUNT` 24 → 23. `sys.v1.agent.count_runs` stays (the app's
`identity_boot_service` dispatches it).

**Why**
Its only consumer was the runtime's own `compute_current_state`, which dispatched it with no
tenant and therefore never received an answer; #692 moved that read to a direct query. After
that: `stable=False`, absent from `_STABLE_SYSCALLS`, the SDK, Claw, the app, and the MCP
allowlist. It remained reachable through `POST /platform/syscall` by any holder of
`agent.read` — a per-tenant read of raw timestamps that nothing calls. `SYSCALL_SYSTEM.md` §9
justified it as kernel-owned "because runtime/platform code depends on it", which was false
once #692 merged. A surface that exists only because a caller was deleted is a surface, not a
capability; keeping it would mean documenting and guarding something with no user.

**Implications**
- consumer-visible removal — `changelog.d` entry under Removed; no major bump (experimental,
  uncalled, and the cross-repo guard does not list it)
- the floor constant's comment now states the one legitimate reason to lower it: a deliberate
  removal citing its DEC-NNN, in the same PR
- `tests/unit/test_syscall_removed_dec022.py` pins the absence so a re-registration is a decision

**Related Docs**
- `AINDY/kernel/syscall_registry.py`
- `docs/runtime/SYSCALL_REFERENCE.md`, `docs/runtime/SYSCALL_SYSTEM.md`
- `TECH_DEBT.md` `SYSTEM-STATE-TENANT-1`

### DEC-023
**Status:** `accepted` (2026-09-16 — `ROUTE-AST-UNWIRED-1`, #698)

**Decision**
The route execution contract is enforced at REQUEST time by the wrapper
`enforce_registered_route_execution` installs on every registered, non-exempt route, and by
nothing else. The boot-time AST validator `validate_registered_route_execution` is DELETED,
not wired. The boot-time property that is true — every non-exempt route on the registered app
carries the wrapper — is pinned by a derived census over the real app.

**Why**
The validator was never called by the application (three references repo-wide: its
definition and one test), and by that test it raised on a route that works — a module-level
alias of `execute_with_pipeline` — because it resolved calls by NAME inside one module. Wiring
it would fail boot on a working application; teaching it cross-module, alias-aware resolution
is building a static analyser whose only gain over the request-time refusal is catching a
bypass before the first request, on a surface the per-route probe suite (FR-25b) already
drives. Leaving a stricter, unrunnable twin beside the real guard is what let a "boot-time
refusal" be CLAIMED in the first place (catalogue variant 8). The honest guarantee is the
wrapper: it judges what HAPPENED on the request, not what the source looks like.

**Implications**
- `route_execution_guard.py` is smaller by the AST machinery; its module docstring now states
  the guarantee precisely (request-time; required only where the router declared
  `require_execution_context`; admin / user-agent / automation routers wrapped but not required)
- `test_every_registered_non_exempt_route_is_wrapped_for_execution_enforcement` replaces a
  one-route check with a derived census over `register_routes` (non-empty asserted); unwiring
  the wrapper at boot turns it red
- any future "structural proof" of the contract must be a CI check over the real app, not a
  runtime function with no call site

**Related Docs**
- `AINDY/core/route_execution_guard.py`
- `docs/runtime/EXECUTION_CONTRACT.md` (status note corrected)
- `TECH_DEBT.md` `ROUTE-AST-UNWIRED-1`

---
### DEC-024
**Status:** `accepted` (2026-09-16 — `RETRY-CLASSIFY-1`, #703)

**Decision**
`execute_with_retry` and `_execute_with_retry` in `AINDY/core/retry_policy.py` are DELETED, not
taught the new failure payload. The runtime's three retry loops — flow node
(`runner_steps.py`), tool step (`nodus_adapter.py`) and the compiled plan's guest `while` —
are inline and are the only loops; the module's retry primitive is `decide_retry()` (classify +
decide + count), which each of them calls.

**Why**
Both helpers had zero callers (the only other `execute_with_retry` in the tree is an unrelated
local closure in `scheduler_service.py`). `RETRY-CONTEXT-1`'s argument that "the runtime owns
the loop" named this helper as the loop; a channel threaded only through it would have been
covered, correct and unreachable — `ROUTE-AST-UNWIRED-1`'s shape (catalogue variant 8), and
the reason DEC-023 deleted rather than wired. `RETRY_POLICY.md` had already recorded the zero
callers; the entry's "five sites" count was the copy that was wrong.

**Implications**
- `backoff_ms` / `exponential_backoff` remain declared-and-unapplied (`RETRY_POLICY.md`
  §Backoff); introducing real backoff now means calling `_sleep_before_retry` from a loop
- phase 2 (`RETRY-CONTEXT-1`) threads its scope into the three real loops, not a helper

**Related Docs**
- `docs/design/RETRY_CLASSIFICATION_AND_CONTEXT_DESIGN.md` §2
- `docs/runtime/RETRY_POLICY.md`

---

### DEC-025
**Status:** `accepted` (2026-09-16 — `RETRY-CLASSIFY-1`, #703)

**Decision**
A failure's class is a STRING on the result dict — `failure_class`, one of
`transient | cancelled | permission | not_found | invalid | fatal` — set by the RAISING SITE
(`execute_tool`'s refusals, the dispatcher's error envelope). The substring table survives only
as the fallback for an un-classed string, and when it fires the record says so
(`classified_by="substring"`); an unmatched string stays `transient` (`"default"`), so the
flip changed nothing for any string the table did not already stop. Only `transient` is
retryable.

**Why**
Not an exception hierarchy: the three loops consume result DICTS, and the guest boundary swallows
host exceptions into `ok: False` — a type cannot cross it, a string can (the same reason
`syscall_outcome.py`'s vocabulary is strings). Not a per-tool `retryable_errors=[...]`
declaration on `register_tool`: that is the substring table moved into the manifest. Measured
before the change: `execute_tool`'s own *cancelled*, *missing token* and *enforcement crashed*
refusals matched no needle and read RETRY. `execute_tool` already returned `"cancelled": True`
beside the string on one refusal — this generalises that precedent.

**Implications**
- every `"success": False` return in `tool_registry.py` must carry `failure_class` (AST census,
  non-empty asserted; `None` allowed only where a tool's/worker's own failure is relayed)
- the dispatcher error envelope gains `failure_class` (additive; error envelopes only)
- `aindy_retry_classifications_total` is the operator signal; `classified_by="substring"` is the
  residue to drive to zero
- no schema: the record rides the `flow.node.*` / `agent.step.*` event payloads

**Related Docs**
- `docs/design/RETRY_CLASSIFICATION_AND_CONTEXT_DESIGN.md` §3–§5
- `docs/runtime/RETRY_POLICY.md` §Error classification
- `docs/runtime/SYSCALL_SYSTEM.md` (envelope)

---

### DEC-026
**Status:** `accepted` (2026-09-16 — `SYSEVENT-RETENTION-1`, #704)

**Decision**
The `system_events` retention job prunes LEAVES ONLY. A row referenced by any of the five
columns that point at `system_events.id` — `system_events.parent_event_id`,
`agent_events.system_event_id`, `memory_nodes.source_event_id`, `memory_nodes.root_event_id`,
`event_edges.source_event_id` / `target_event_id` — is never eligible, whatever its type's
class. The `event_edges` pair is in the predicate even though the database would not refuse
that delete.

**Why**
Four of the five constraints are `NO ACTION`: a referenced event cannot be deleted at all, and a
type-and-age `DELETE` on a real deployment aborts on the first parent row it meets — a job that
aborts hourly is `SYSMAX-5`'s brownout with a new cause. The fifth is `CASCADE`, on the causal
graph `build_trace_graph` reads: the delete passes and the edge vanishes silently, which is
exactly the "missing row reads as never happened" failure `EVENT-OUTBOX-1` describes. So
"referenced" is structurally "audit", and the type classes refine that rule rather than replace
it. On the SQLite harness FKs are off, so the tests exercise the module's predicate — each
anti-join paired with a control that selects the same row once its referrer is gone.

**Implications**
- a keepalive that became a parent (e.g. a decision that led to a dispatch) is kept for free
- `event_edges`' `CASCADE` is NOT changed to `NO ACTION` — the predicate is the guard, pinned
- any future referrer of `system_events.id` must be added to `_leaf_filter`, with its control

**Related Docs**
- `docs/design/SYSEVENT_RETENTION_DESIGN.md` §2
- `AINDY/core/system_event_retention.py`

---

### DEC-027
**Status:** `accepted` (2026-09-16 — `SYSEVENT-RETENTION-1`, #704)

**Decision**
An event type with no registered retention class is KEPT — never selected, and counted on
`aindy_system_events_unclassified_rows`. The class table is a registry the runtime seeds and
the app extends (`register_event_retention`, exact names or globs), not a literal keyed on the
enum; a misspelled class is refused, never read as "keep".

**Why**
`SystemEventTypes` declares 46 names, the runtime emits 22 more as literals, and the app
registers its own — a literal table would delete a new type by omission (green-check variant
12). Default-delete-with-exceptions is the shape that loses data; default-keep-with-a-gauge is
the inverse, and the gauge turns "someone should classify this" into a number an operator sees
grow.

**Implications**
- a derived test asserts every enum type has a seed class, so the runtime's own types never
  start unclassified
- the app owns classifying its types; until it does, they cost disk, never audit

**Related Docs**
- `docs/design/SYSEVENT_RETENTION_DESIGN.md` §3
- `AINDY/platform_layer/registry.py` `register_event_retention`

---

### DEC-028
**Status:** `accepted` (2026-09-16 — `SYSEVENT-RETENTION-1`, #704)

**Decision**
The seed table in `system_event_retention.py`: failure-shaped events are `audit` regardless of
family (`execution.failed` is audit while `execution.started` is operational); `capability.*`,
`auth.*`, `platform.*`, dead-letter and recovery events are audit; the execution ledger, traces,
embeddings, syscall and signal events are `operational`; `watchdog.scan.completed` and
`health.liveness.completed` are `keepalive`. **`autonomy.decision` is `operational`** — the one
class decided rather than derived.

**Why**
A failure is the row a support conversation starts from; its siblings say the run existed.
`autonomy.decision` (25k rows of "deferred" on the FR-18 stack) is also the only record of why
a trigger did not fire — but a decision old enough to prune is one nobody is still asking about,
and a decision that led to a dispatch became a parent and is kept by DEC-026. If wrong, the
class is one `register_event_retention` line.

**Related Docs**
- `docs/design/SYSEVENT_RETENTION_DESIGN.md` §4

---

### DEC-029
**Status:** `accepted` (2026-09-16 — `SYSEVENT-RETENTION-1`, #704)

**Decision**
Defaults: `operational` 90 days, `keepalive` 7 days, `audit` never by age (no override exists).
`AINDY_SYSEVENT_RETENTION` ships UNSET — no job registered, the pre-existing behaviour;
`report` runs the selection and logs per-type counts without deleting; `prune` deletes in
committed batches. An unrecognised value is off.

**Why**
An operator reads one report before the first real run, and a typo must never delete rows.
The design considered shipping `report` as the default value; the env var ships unset instead so
that upgrading changes nothing until someone chooses — the same discipline as every other
default-off capability here.

**Related Docs**
- `docs/design/SYSEVENT_RETENTION_DESIGN.md` §5, §8
- `AINDY/.env.example`

---

### DEC-030
**Status:** `accepted` (2026-09-16 — `LEASE-FENCE-1`, #705)

**Decision**
The background lease carries one integer, `background_task_leases.fence`, incremented ONLY on a
takeover of an expired lease (1 on the first claim, unchanged on renew). Alembic `0019`; a row
predating the column reads 0 and its next takeover makes it 1.

**Why**
Expiry bounds how long two leaders coexist and says nothing about what the stale one writes
before its next tick — and a job already running when the lease is lost runs to completion as
leader. A fence that moves only on takeover is exactly the generation counter Pi's writer lease
and Temporal's shard `RangeID` carry; renew must not move it, or a leader's own heartbeat would
refuse its own in-flight job.

**Related Docs**
- `docs/design/LEASE_FENCE_DESIGN.md` §3a
- `alembic/versions/0019_background_task_lease_fence.py`

---

### DEC-031
**Status:** `accepted` (2026-09-16 — `LEASE-FENCE-1`, #705)

**Decision**
The check is `assert_lease_fence(db, held)`: a `FOR SHARE` read of the lease row INSIDE the
job's own transaction, before its commit, raising `LeaseFenceLost` on mismatch. It is NOT a
wrapper that consults the elector's local `is_leader` before starting a job.

**Why**
The local boolean is the same belief the stale leader already holds — updated on its next
successful tick, which a stalled process has not had; a wrapper on it is a second copy of the
thing that is wrong. `FOR SHARE` reads the row under a lock a takeover must contend for:
a takeover's `FOR UPDATE` blocks until the job's transaction ends, so a job that passed commits
before anyone can lead; one that already committed leaves a higher fence. Refused, not asked.
On SQLite the lock clause is a no-op (as `claim_lease` already documents), so the unit suite
pins the comparison and an integration test pins the blocking.

**Implications**
- `expected_fence=None` (in-process profile; no elector) skips the check — refusing there would
  stop maintenance on every non-distributed deployment
- the elector exposes its fence only while leader; a claim that raised leaves the hold stale

**Related Docs**
- `docs/design/LEASE_FENCE_DESIGN.md` §3b, §4
- `tests/integration/test_lease_fence_contention.py`

---

### DEC-032
**Status:** `accepted` (2026-09-16 — `LEASE-FENCE-1`, #705)

**Decision**
Two jobs are fenced — `recover_orphaned_approved_runs` and `deferred_async_job_retry` — and the
other leader-only jobs are deliberately NOT.

**Why**
Read for what a second leader running the same job at the same moment would do: ten of thirteen
re-run harmlessly (CAS-guarded, status-filtered, atomic queue ops, or process-local). The orphan
job spawns `execute_run` per row, whose entry guard is a read-then-set that `CLAUDE.md` says not
to guard twice because "the 10-minute threshold ensures the original thread is dead" — an
argument that holds for ONE leader. The deferred-job path double-dispatches a handler. A fence on
the idempotent ten is a row lock per job for nothing, and contention on the lease row delays
takeover, the one thing the elector must stay fast at.

**Related Docs**
- `docs/design/LEASE_FENCE_DESIGN.md` §2, §6

---

### DEC-033
**Status:** `accepted` (2026-09-16 — `LEASE-FENCE-1`, #705)

**Decision**
`execute_run` is untouched: no second CAS is added to its entry guard.

**Why**
`CLAUDE.md` declines it, and the fence is what makes that decline hold under two leaders: the
window shrinks from "as long as the stale leader stays stale" to the length of one job's
dispatch decision. Closing the last milliseconds would mean a CAS in a path that `approve_run`
also calls, for a race the fence has already made unobservable in practice.

**Related Docs**
- `docs/design/LEASE_FENCE_DESIGN.md` §3c
- `CLAUDE.md` §Agent approve path

---

### DEC-034
**Status:** `accepted` (2026-09-16 — `OTEL-GENAI-SEMCONV-1`, #706)

**Decision**
"Adopt the GenAI semantic conventions" means EMIT three new span kinds — `chat {model}`,
`execute_tool {tool}`, `invoke_agent {agent}` — at the three chokepoints that already exist
(the four provider clients, `execute_tool`, `execute_run`), additively. The two existing span
names, `syscall.{name}` and `async_job.{task}`, are unchanged: semconv has no vocabulary for a
syscall, and a `gen_ai.*` name for one would be false alignment.

**Why**
Measured before the change, the runtime emitted no LLM, tool or agent span at all and
`set_attribute` had zero call sites; the entry's "rename a public surface" risk applied to five
attribute keys on two span kinds. Standard tooling could not read our traces not because the
names were wrong but because the operations it looks for were not there.

**Related Docs**
- `docs/design/OTEL_GENAI_SEMCONV_DESIGN.md` §1, §2

---

### DEC-035
**Status:** `accepted` (2026-09-16 — `OTEL-GENAI-SEMCONV-1`, #706)

**Decision**
The token meter lives INSIDE the span helper: a provider client wraps its raw call in
`with llm_operation(...) as op:` and calls `op.record(response)`, which is the one call to
`observe_llm_usage`. A direct `observe_llm_usage` call in a client is refused by the derived
census guard, and a `with` that never records is refused too.

**Why**
One seam, one shape: a client cannot meter without tracing or trace without metering, and the
census (`test_token_meter.py`, itself the answer to catalogue variant 12) asserts exactly that
with one more `With` node to find. The meter's own accounting is untouched — `record` calls it
exactly once, and the double-count guard moved its count from `observe_llm_usage` to `record`.

**Related Docs**
- `docs/design/OTEL_GENAI_SEMCONV_DESIGN.md` §3
- `AINDY/platform_layer/genai_telemetry.py`

---

### DEC-036
**Status:** `accepted` (2026-09-16 — `OTEL-GENAI-SEMCONV-1`, #706)

**Decision**
`enduser.id` is emitted beside `user.id` on the `syscall.*` span for one release; `user.id`
is dropped the release after. This is the only rename; `trace.id` stays (redundant with the
span's own trace id, not wrong, and a consumer may filter on it).

**Why**
The entry's "additive first, both emitted for a release, documented removal" protocol, applied
to the only key it actually applies to — `user.id` exists on the syscall span alone; the async
job span carries `job.name`/`job.id`/`trace.id`.

**Related Docs**
- `docs/design/OTEL_GENAI_SEMCONV_DESIGN.md` §5

---

### DEC-037
**Status:** `accepted` (2026-09-16 — `OTEL-GENAI-SEMCONV-1`, #706)

**Decision**
`gen_ai.client.token.usage` and `gen_ai.client.operation.duration` are emitted through an OTel
`MeterProvider` beside the `TracerProvider`, over the same OTLP endpoint — BESIDE the Prometheus
`aindy_llm_*` counters, never instead of them. Dimensions stop at provider and model (plus
`gen_ai.token.type`); tenant attribution stays on the span (`enduser.id`) where cardinality is
free.

**Why**
The Prometheus names are the operator surface the governor accrues from and the soak harness
reads; two names for one number on two pipelines is the design. Per-tenant labels on a metric
are a time series per customer (`token_meter.py` records why they were left out).

**Related Docs**
- `docs/design/OTEL_GENAI_SEMCONV_DESIGN.md` §6, §7

---

### DEC-038
**Status:** `accepted` (2026-09-16 — `OTEL-GENAI-SEMCONV-1`, #706)

**Decision**
Content capture is OUT: no `gen_ai.input.messages` / `gen_ai.output.messages`, no prompt or
completion bodies on any span or event, and a test refuses any `gen_ai.input*` / `gen_ai.output*`
attribute on an emitted span. Auto-instrumentation packages (`opentelemetry-instrumentation-openai`
et al.) are NOT used — they patch the SDK clients, would produce a second span per call beside
ours, and enable content capture by default in some versions.

**Why**
A data-handling decision with its own answer — MAF ships it opt-in for exactly that reason — and
it needs its own proposal. Our seam is the four clients we own.

**Related Docs**
- `docs/design/OTEL_GENAI_SEMCONV_DESIGN.md` §6, §7

---

### DEC-039
**Status:** `accepted` (2026-09-16 — `ORCHESTRATOR-SPLIT-1`, #707)

**Decision**
Option (a) of `ORCHESTRATOR-SPLIT-1` — implement `WorkflowStore` over this runtime's PostgreSQL
and inject it so the guest's workflow store (store 4) collapses into `flow_runs` — is DECLINED
at HEAD. The trigger that reopens it is the first time the runtime wants to READ guest run
state (a guest-side resume, a `nodus workflow` inspection surfaced in the operator console, or
`RECOVERY-GRANULARITY-1` resuming a segment from the guest's step ordinal). Until then stores
3 and 4 are write-only and advisory, and INV-OWN-003 pins that nothing under `AINDY/` imports
`nodus_lang_workflow`.

**Why**
The entry kept (a) open as the only option that made the crash-overlap moot. The contract
(`DURABLE_STATE_OWNERSHIP_CONTRACT.md` §4) shows the overlap is already moot for a different
reason: the runtime never reads store 4, nodus's sweep can only dead-letter and is declared off
(#611), and a resumed node runs a fresh VM that creates a new record (DEC-012). After a crash
there is no second layer *acting* — two records of one run, not two executions. Moving a
write-only advisory store into Postgres would give the authority table a second copy of runs it
already tracks, written by the guest, read by nobody, and reaped by nothing (`running` is
non-terminal there too). A migration of state nothing consumes is not a durability improvement.

**Implications**
- `ORCHESTRATOR-SPLIT-1` closes on the contract plus this decision; `QUEUE-DURABILITY-CLASS-1`
  folds into the contract's §6 and closes with it
- INV-OWN-001..003 are adopted in `EXECUTION_INVARIANTS.md` §7
- the import census going red is the signal to re-decide §4 — not a reason to delete the test

**Related Docs**
- `docs/runtime/DURABLE_STATE_OWNERSHIP_CONTRACT.md` §4, §7
- `tests/unit/test_durable_state_ownership.py`
- `docs/design/WORKFLOW_STORE_DECLARATION_PROPOSAL.md`

---

### DEC-040
**Status:** `accepted` (2026-09-17 — `FR-35`, #712)

**Decision**
LLM usage spent inside the Nodus worker rides the worker reply as a fourth DEFERRED collection
(`llm_usage`, beside `memory_writes` / `emitted_events` / `simulated_effects`) and is recorded
in the parent by `record_llm_usage`. The filing's ask 1.

**Why**
The reply is the one channel a guest already has for things it cannot do across the process
boundary — commit, emit, accrue. One counter, one process, one registry; the same channel
carries the timing that lets the parent emit the span the worker could not.

**Related Docs**
- `docs/design/FR35_GUEST_LLM_USAGE_DESIGN.md` §1–§2

---

### DEC-041
**Status:** `accepted` (2026-09-17 — `FR-35`, #712)

**Decision**
Inside the worker's deferral scope, `observe_llm_usage` appends to the ledger and observes
NOTHING else — no local counters, no `_attribute_usage`. Deferral replaces observation.

**Why**
Two accrual sites are the double-count the meter's own design rejects
(`test_a_chat_call_is_metered_exactly_once`): on a Redis-backed resource manager the worker's
accrual and the parent's replay would both land in the tenant window. The worker's Prometheus
registry is never scraped, so nothing is lost by not counting there.

**Related Docs**
- `docs/design/FR35_GUEST_LLM_USAGE_DESIGN.md` §3

---

### DEC-042
**Status:** `accepted` (2026-09-17 — `FR-35`, #712)

**Decision**
The governor's ADMISSION (reserve before the call) stays in the worker: `run_one` enters
`llm_attribution_scope(tenant_id=ctx.user_id, run_id=<agent run>)` so `resolve_llm_subject()`
answers there. It is real only with a Redis-backed resource manager; with an in-memory one the
worker's window is empty and admission is vacuous on the guest path — stated, not hidden.
ACCOUNTING is the parent's, from the reply, correct on every backend.

**Why**
`llm_budget_reservation` is reserve → call → reconcile and reconcile only releases the estimate;
the call is made in the worker and cannot be refused from outside it. The two halves of the
governor therefore have different homes, and pretending one place serves both would either
double-count (accrue in the worker) or never refuse (reserve in the parent).

**Related Docs**
- `docs/design/FR35_GUEST_LLM_USAGE_DESIGN.md` §3

---

### DEC-043
**Status:** `accepted` (2026-09-17 — `FR-35`, #712)

**Decision**
The parent attributes the replayed usage from the reply's EXPLICIT context — `run_id` (the agent
run when the segment belongs to one), `execution_unit_id`, `user_id` — with the ambient
ContextVars as the fallback, field by field.

**Why**
A ContextVar that happened to be set on the calling thread is how this spend was lost to begin
with; a resumed segment runs from the scheduler with no scope at all. The request context is
already carried to the worker and is the run's real identity.

**Related Docs**
- `docs/design/FR35_GUEST_LLM_USAGE_DESIGN.md` §5

---

### DEC-044
**Status:** `accepted` (2026-09-17 — `FR-35`, #712)

**Decision**
For each per-call record the parent replays a `chat {model}` span with the record's own
`started_at_ms` / `duration_ms` as explicit start and end times, nested under the current span,
marked `aindy.deferred = true`, carrying `gen_ai.tool.name` when the seam knew the tool.

**Why**
The worker has no tracer provider, so #706's span was started and dropped there. A late span
with true timestamps says when the call happened, in the trace the operator is looking at; a
span with the replay's timestamps would be a lie about a call that had already finished.

**Related Docs**
- `docs/design/FR35_GUEST_LLM_USAGE_DESIGN.md` §5
- `AINDY/platform_layer/genai_telemetry.py::replay_deferred_llm_span`

---

### DEC-045
**Status:** `accepted` (2026-09-17 — `FR-35`, #712)

**Decision**
The ledger carries at most `AINDY_NODUS_LLM_LEDGER_MAX` (default 256) per-call records; calls
past the cap are aggregated per `(provider, model)` into a tail of `{calls, prompt_tokens,
completion_tokens}`. Accounting reads both halves; spans are replayed from records only.

**Why**
A guest loop that makes ten thousand calls must not produce a ten-thousand-entry reply. The
tail keeps the accounting exact; what it gives up is per-call spans past the cap, which is the
right thing to lose.

**Related Docs**
- `docs/design/FR35_GUEST_LLM_USAGE_DESIGN.md` §4

---

### DEC-046
**Status:** `provisional` (2026-09-17 — `HTTP-SCOPE-GAP-1` remainder; closes the entry on acceptance)

**Decision**
A scope answers *which verb* and a row filter answers *whose data*; they are two checks and
stay two. `execution.read`-class scopes are not widened to answer "may I read someone else's",
and no cross-owner read path is added. A run is readable by its owner (row filter
`FlowRun.user_id == user_id`, `flow_definitions_engine.py:60`) under the route's scope
(`flow_router.py:39` `_REQUIRE_PLATFORM_ADMIN` on the run routes); an operator who needs
another owner's run reads it through the operator surfaces (`/platform/*`), which are
`is_operator_principal`-gated and row-unfiltered by design.

**Why**
The entry's remainder was a design question — *"`execution.read` conflates scope with data
ownership"*. Measured at HEAD it does not conflate them: the scope is checked by the
dependency and the ownership by the query, and neither is asked the other's question. Making
a scope carry ownership (`execution.read:any`) would put an authorisation decision into a
string the key's issuer types, which is `KEY-SCOPE-ESCALATION-1`'s shape from the other side.

**Implications**
- `HTTP-SCOPE-GAP-1` closes on acceptance; its three gotchas move to the closed line.
- Any future cross-owner read is an operator route, never a scope variant.

**Related Docs**
- `TECH_DEBT.md` `HTTP-SCOPE-GAP-1`; `AINDY/services/auth_service.py::is_operator_principal`

---

### DEC-047
**Status:** `provisional` (2026-09-17 — `CLI-EXEC-SURFACE-1`; closes the entry on acceptance)

**Decision**
The operator half of the runtime (resume, flow list/get, queue + DLQ, trace, health) stays
HTTP-only. It is **not** added to the syscall vocabulary, so no transport — `/platform/syscall`,
the MCP allowlist, or a CLI — can reach it by being a transport. No CLI is built.

**Why**
The scope doc's §8 reframe: a terminal command is a transport over the syscall vocabulary,
and a transport cannot grant authority it does not have. The question was whether the
*operator* half should be syscall-addressable, and the answer is decided by what an operator
syscall opens — three doors at once (a route-ungated `/platform/syscall`, the MCP allowlist
where an LLM client sits, and any future CLI). A DLQ drain reachable by an LLM client is a
decision, and it is declined. The execution half (`flow.run`, `nodus.execute`, `agent.*`,
`job.submit`, `memory.*`) is already syscall-addressable and unchanged.

**Implications**
- `CLI-EXEC-SURFACE-1` closes on acceptance. `INITIATOR-IDENTITY-1` and
  `QUOTA-ACCRUAL-ORPHAN-1`'s transport notes stand on their own.
- Reopening requires a per-syscall answer to "which of the three doors", not a CLI proposal.

**Related Docs**
- `docs/design/CLI_EXECUTION_SURFACE_SCOPE.md` §8; `AINDY/mcp_server.py`

---

### DEC-048
**Status:** `accepted` (2026-09-16 — `EGRESS-INPROC-1`, #718)

**Decision**
The egress decision for a tool call — `EgressDecision(mode, domains)` — is resolved ONCE in
`execute_tool`, BEFORE the isolation branch, from the capability policies' domain allowlist and
the tool's effective `authority.network` (declared spec clamped to the tool floor, which is
`open`). Resolution: `none` → deny-all whatever the policies say; any policy domains → `scoped`
to exactly those; `scoped` with no domains → deny-all (fail-closed); `open` with no domains →
nothing to enforce. `egress_guard.resolve_egress_decision` is the one place this is computed.

**Why**
The allowlist was already computed at the right place; the *enforcement* was attached to the
wrong branch. `egress_scope` was entered around the in-process call only, and the isolated
branch returned above that `with` — so the tool the runtime distrusts enough to move out of
process was the one tool the guard never covered, flag on or off. Making the decision a value
resolved before the branch is what lets each provider enforce it; folding `authority.network`
in is what stops the spec and the policy being two vocabularies for one question.

**Implications**
- A tool that declares `authority.network="none"` is now deny-all when the flag is on — on
  both branches. Before, the tool seam ignored that axis entirely.
- Flag off: no decision is resolved, nothing changes — byte-identical envelopes.

**Related Docs**
- `docs/design/EGRESS_INPROC_DESIGN.md` §2; `AINDY/platform_layer/egress_guard.py`

---

### DEC-049
**Status:** `accepted` (2026-09-16 — `EGRESS-INPROC-1`, #718)

**Decision**
The tool worker receives the DECISION in its request payload (`{"egress": {"mode",
"domains"}}`, an additive key) and installs the socket guard PROCESS-GLOBALLY from it
(`install_process_egress`) before the plugin stack loads or the function is resolved. It never
reads capability policy. The process-global install is what closes the raw-`threading.Thread`
contextvar bypass in the worker; in-process that bypass stays open and documented.

**Why**
`tool_worker.py`'s standing rule: authority is not re-evaluated in the process the boundary
distrusts, and the worker has no db to evaluate it with. A worker runs exactly one tool, so
"this process" and "this call" are the same scope there — a global install is strictly stronger
than the contextvar and costs nothing. Closing the same bypass in-process would mean wrapping
`threading.Thread` globally, which the guard's docstring declines; the worker is where it can be
closed without that.

**Related Docs**
- `AINDY/agents/tool_worker.py`; `tests/unit/test_egress_inproc_worker.py`

---

### DEC-050
**Status:** `accepted` (2026-09-16 — `EGRESS-INPROC-1`, #718)

**Decision**
The mechanism that applied is REPORTED, never assumed: the worker's reply carries
`egress_mechanism`, and the parent's envelope carries `egress: {mode, mechanism}` (only when
enforcement is on) with `aindy.egress.mode` / `aindy.egress.mechanism` on the `execute_tool`
span. Values: `socket_guard` (in-process, both documented bypasses apply),
`socket_guard:worker` (thread bypass closed), `none` (nothing enforced — flag off, or a worker
that never saw the decision). A provider that cannot enforce a declared mode REPORTS rather
than refuses.

**Why**
The design named `env_applied.network` as the channel; that column is per execution UNIT and a
unit runs many tools, so the per-call truth belongs on the call's envelope and span. Reporting
over refusing: every host today lacks a container runner, so refusing a `none` that only the
socket guard can approximate would fail every declared tool everywhere — and the point of
`EXEC-ENV-BIND-1` was that a reader learns what BOUND, not what was asked. A reply with no
mechanism reads as `none` on purpose: a dropped decision is visible on the envelope, not
silently open.

**Related Docs**
- `docs/design/EGRESS_INPROC_DESIGN.md` §2 (the table); `EXECUTION_ENVIRONMENT_SPEC_DESIGN.md`

---

### DEC-051
**Status:** `accepted` (2026-09-16 — `EGRESS-INPROC-1`, #718)

**Decision**
`AINDY_EGRESS_ENFORCEMENT` remains the single switch and remains default-off. This change moves
WHERE a decision is enforced when the switch is on; it does not turn it on. Flipping the default
is `MEB-2b`'s soak, not this entry.

**Why**
The defect is closed for every deployment that has the flag on, and a deployment that has it off
is exactly as it was. Coupling the fix to the flip would have made a correctness fix wait on
evidence it does not need.

**Related Docs**
- `docs/design/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md` (MEB-2b)

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

**Pending — designs filed 2026-09-16, each listing the decisions it asks for; recorded here as
`DEC-NNN` by the PR that implements (or declines) them, per DEC-010:**
`docs/design/RETRY_CLASSIFICATION_AND_CONTEXT_DESIGN.md` §9 (three left — 1 and 2 are DEC-024/025), `SYSEVENT_RETENTION_DESIGN.md`
§8 (none — DEC-026..029), `LEASE_FENCE_DESIGN.md` §7 (none — DEC-030..033), `OTEL_GENAI_SEMCONV_DESIGN.md` §8 (none — DEC-034..038), and
`docs/runtime/DURABLE_STATE_OWNERSHIP_CONTRACT.md` §7 (none — DEC-039). All five designs' decisions are
now recorded (DEC-024..039); the paragraph stays as the record of how they arrived.
`docs/design/FR35_GUEST_LLM_USAGE_DESIGN.md` §8 (six) — recorded as DEC-040..045 (#712).
**Next tier, filed 2026-09-17:** `EVENT_OUTBOX_DESIGN.md` §6 (three), `RECOVERY_GRANULARITY_DESIGN.md` §6 (four),
`AUTHORITY_LIFETIME_DESIGN.md` §5 (four), `INITIATOR_IDENTITY_DESIGN.md` §5 (four), `AUDIT_CORRELATION_DESIGN.md` §4 (four),
`EGRESS_INPROC_DESIGN.md` §5 (four) — pending. `HTTP-SCOPE-GAP-1` remainder and `CLI-EXEC-SURFACE-1` needed no design:
each is one decision, recorded PROVISIONAL as DEC-046 / DEC-047 and accepted (closing the entry) or declined by the owner.

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
