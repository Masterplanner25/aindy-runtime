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
**Status:** `accepted` (2026-09-17 — `HTTP-SCOPE-GAP-1` remainder; recorded provisional in #716, accepted by the owner 2026-09-17, #723 — the entry closes with it)

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
**Status:** `accepted` (2026-09-17 — `CLI-EXEC-SURFACE-1`; recorded provisional in #716, accepted by the owner 2026-09-17, #723 — the entry closes with it)

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

### DEC-052
**Status:** `accepted` (2026-09-17 — `AUDIT-CORRELATION-1`, #719)

**Decision**
Joins (1) capability → dispatch and (3) effect → dispatch close by ADDITIVE payload keys, no
schema: `syscall.executed` gains `capability` (the entry's required capability), `guarantee`
(the entry's declared execution guarantee) and `action_id` (the ledger row's key, `None` unless
the idempotency gate engaged); the tool path's admission event `capability.allowed` gains
`action_id`, computed BEFORE the event (the value is a pure hash of tool, args and run scope;
the ledger is still consulted where it always was).

**Why**
The entry said join (1) would fall out of `AUTHORITY-VALUE-1`'s `ExecutionAuthority`; that
object does not exist — the entry closed as the `child_context` clamp. The capability string
plus the run's granted set is what admitted the call, and it was already a local at the emit
site. For (3), the only join was `effect_records.execution_id = payload->>'execution_unit_id'`
— unindexed JSONB, yielding every event of the UNIT rather than the one dispatch. The
`action_id` names the dispatch and was already a local too. On the tool path the event was
emitted before the id existed; hoisting the pure computation keeps the event ORDER unchanged —
moving the event below the gate would have silenced admission on a replay.

**Related Docs**
- `docs/design/AUDIT_CORRELATION_DESIGN.md` §1; `docs/runtime/IDEMPOTENCY_CONTRACT.md` §"Reconstruction join"

---

### DEC-053
**Status:** `accepted` (2026-09-17 — `AUDIT-CORRELATION-1`, #719)

**Decision**
Join (2) environment → execution is closed by `EXEC-ENV-BIND-1`'s `execution_units.env_applied`
(what bound to the unit). The attestation remainder — the strong runner self-reporting
`mount_mode` / `network_policy` — is `SANDBOX-EVIDENCE-2`, not this entry.

**Why**
Re-measured at HEAD the join already exists; recording it here stops it being re-derived as
open work under this id.

**Related Docs**
- `docs/design/EXECUTION_ENVIRONMENT_SPEC_DESIGN.md`; `TECH_DEBT.md` `SANDBOX-EVIDENCE-2`

---

### DEC-054
**Status:** `accepted` (2026-09-17 — `AUDIT-CORRELATION-1`, #719)

**Decision**
The effect ↔ event join is a DOCUMENTED CONVENTION on the unique-indexed
`effect_records.action_id` (`uq_effect_records_action_id`). No foreign key in either direction;
no `trace_id` column on `EffectRecord`; no GIN index on `system_events.payload`.
`test_audit_correlation.py` pins the absence of a cross-table FK.

**Why**
The ledger row is committed in the gate's own session BEFORE the handler runs
(`_resolve_effect_record` commits); the event is written AFTER, on a separate session, under a
swallowing `try`. An FK from effect to event would require the event first; from event to
effect it would turn a swallowed emit failure into a constraint violation. `execution_id` is
already the stronger key on the ledger; `action_id` lookups are forensic, not hot.

**Related Docs**
- `docs/design/AUDIT_CORRELATION_DESIGN.md` §3; CLAUDE.md "EffectRecord rules"

---

### DEC-055
**Status:** `accepted` (2026-09-17 — `AUDIT-CORRELATION-1`, #719)

**Decision**
`syscall.executed` stays `operational` under `SYSEVENT-RETENTION-1` (90 d default). The join is
time-bounded by the shorter of the operational window and the effect-record TTL, and
`IDEMPOTENCY_CONTRACT.md` says so. A deployment that wants the join for longer sets
`AINDY_SYSEVENT_RETENTION_OPERATIONAL_DAYS` to match its effect TTL; it does not reclass the event.

**Why**
`syscall.executed` is the highest-volume event type; keeping it forever is the growth the
retention entry was built to stop. An effect record is a dedup key with a TTL, not a permanent
audit row, so a join bounded on both sides is the honest shape. The `error.*` / `capability.*`
classes the retention entry protected stay KEEP.

**Related Docs**
- `docs/design/SYSEVENT_RETENTION_DESIGN.md`; `AINDY/core/system_event_retention.py`

---

### DEC-056
**Status:** `accepted` (2026-09-17 — `AUTHORITY-LIFETIME-1`, #720)

**Decision**
Authority ends with the run. A capability token presented for a run in a TERMINAL status
(`TERMINAL_RUN_STATUSES` = the kernel's `AGENT_TERMINAL_STATUSES` + `refused`) is refused at
exactly the two sites `CANCEL-REACH-1` already reads — `check_tool_capability`, BEFORE the HMAC
check, and the dispatcher's agent-span gate — and nowhere else. `verify_token` / `validate_token`
stay stateless (AST-pinned): the planner path and any caller without a run keep the pure HMAC
check. A cancelled run keeps the envelope CANCEL-REACH-1 promised (`failure_class="cancelled"`,
`cancelled=True`); every other terminal status refuses as `permission`.

**Why**
`TOKEN_TTL_HOURS = 24` bound the token to the clock; a run that finished in 90 s could present
its token all day — not as a bearer escape (the ceiling and run binding hold) but as a stale
worker, a replayed request or a leaked token continuing a finished run's work. The cancel
chokepoints are the two places an effect is about to happen under a run's authority; a third
site would be a third read on the hot path for the same fact.

**Related Docs**
- `docs/design/AUTHORITY_LIFETIME_DESIGN.md` §2; `AINDY/kernel/cancellation.py`

---

### DEC-057
**Status:** `accepted` (2026-09-17 — `AUTHORITY-LIFETIME-1`, #720)

**Decision**
The read is `cancellation.py`'s existing own-session, per-run-cached read, widened to return
the status (`run_terminal_status`); `is_run_cancelled` is `== "cancelled"` over it, behaviour
pinned identical. A terminal answer is STICKY for the process lifetime — the negative cache is
the cache — so after the first terminal read a finished run costs zero queries.

**Why**
This adds NO read to the hot path: `is_run_cancelled` already ran once per tool call and once
per dispatch on every agent run. A run never leaves a terminal state (`_STATUS_TRANSITIONS`),
so re-asking is pure cost; a separate revocation list would be a second store to keep in sync.

**Related Docs**
- `docs/design/AUTHORITY_LIFETIME_DESIGN.md` §3; `tests/unit/test_authority_lifetime.py`

---

### DEC-058
**Status:** `accepted` (2026-09-17 — `AUTHORITY-LIFETIME-1`, #720)

**Decision**
Fail-OPEN, as cancel does: an unreadable run status answers "live". The token's HMAC expiry
remains the outer bound.

**Why**
Refusing an effect because a database blip made the answer unreadable would abort live work
nobody ended, and an aborted effect is not recoverable by retrying the check. A missed refusal
costs one more effect under a finished run's ceiling; a false refusal costs the run.

**Related Docs**
- `AINDY/kernel/cancellation.py` module docstring ("why it fails open")

---

### DEC-059
**Status:** `accepted` (2026-09-17 — `AUTHORITY-LIFETIME-1`, #720)

**Decision**
A `waiting` run KEEPS its authority. OpenHands' "nulled on pause" is declined; so is revocation
on `pending_approval` (no token exists yet) and any token version / rotate-on-resume.

**Why**
A parked run resumes with the same token (`FR-31`); revoking on wait would make every resume a
re-mint, which needs a grant path the authority WAIT gate was deliberately denied (`DEC-016`).
The run id is the version; a resumed run is the same run.

**Related Docs**
- `docs/design/AUTHORITY_LIFETIME_DESIGN.md` §2, §4; `DEC-016`

---

### DEC-060
**Status:** `accepted` (2026-09-17 — `EVENT-OUTBOX-1`, #721)

**Decision**
Inside a request pipeline, a queued system event is ADDED to the handler's session (add +
flush, `_persist_system_event(commit=False)`) and never committed by the event path. It rides
whatever the handler commits next; the FR-30 execution-unit finalize is the last commit on every
request, so a read-only handler's events ride that. The post-handler pass (`_apply_event_signals`)
runs only the DERIVED effects for a persisted entry — internal handlers, feedback signals, memory
capture, webhooks, the scheduler wake (`run_post_persist_effects`) — and keeps its swallowing
`try`, which is now correct: what it does is derived from the record, not the record. No outbox
table, no relay. The non-pipeline branch is untouched (it already wrote on the caller's session
and committed).

**Why**
The in-memory bucket was written AFTER the handler, on separate commits, under a swallowing
`try`: a crash between the handler's commit and that flush kept the work and lost the record —
"better index, weaker record". One store, one connection: the row belongs in the transaction
that holds the work. Every reason the buffer had is either gone (the provisional id is a
client-side uuid4 either way) or preserved (post-handler-only content still goes post-handler).

**Related Docs**
- `docs/design/EVENT_OUTBOX_DESIGN.md` §2–§3; `AINDY/core/execution_signal_helper.py`

---

### DEC-061
**Status:** `accepted` (2026-09-17 — `EVENT-OUTBOX-1`, #721)

**Decision**
The event's id stays client-assigned (`SystemEvent.id` defaults to `uuid4`); the id
`queue_system_event` returns IS the row's id. Nothing changes for a caller holding it.

**Why**
The provisional id the bucket handed out was already a client-side `uuid4`; a row added with
that id is referenceable before commit exactly as the buffered dict was.

**Related Docs**
- `tests/unit/test_event_outbox.py::test_the_returned_id_is_the_rows_id`

---

### DEC-062
**Status:** `accepted` (2026-09-17 — `EVENT-OUTBOX-1`, #721)

**Decision**
A handler that RAISES leaves no event row. The pipeline rolls the request session back before
recording `execution.failed` (`_safe_rollback_handler_work`), and only when the handler raised —
once the handler has returned, its work stands whatever the post-handler machinery does. To
make that safe, the execution-unit row is committed where it is created (`_safe_require_eu`),
so the finalize that follows the rollback still finds it.

**Why**
The design said "a handler that raises rolls back its session … the error event is written on
its own session, as today" — measured at implementation, neither held: nothing rolled the
request session back, and the pipeline's `execution.failed` emit committed the REQUEST session
through `_persist_system_event`, landing the handler's pending writes — and now its queued
events — as a side effect (that function's own comment says it wants to avoid exactly that).
Rollback semantics are the point of riding the transaction, so the pipeline now provides them.
★ Behaviour change beyond events: a handler that raises no longer has its uncommitted writes
landed by the failure event. ★ Test rule: under the shared fixture the app's rollback reaches
the OUTER transaction and erases the test's own rows — a route that answers 4xx must be tested
on the private engine (`tests/fixtures/db.py::build_private_engine`).

**Related Docs**
- `AINDY/core/execution_pipeline/resources.py`; `tests/unit/test_auth_password_change.py` (moved to the private engine)

---

### DEC-063
**Status:** `accepted` (2026-09-17 — `RECOVERY-GRANULARITY-1`, #722)

**Decision**
The per-step durable write happens at the WORKER seam — `nodus_worker.run_agent_tool`, the
`call_tool` host function — on its own short-lived session, committed, as each step completes
and before the segment's script returns. The parent's segment-end batch
(`_run_agent_segment_flow`) becomes an upsert by `(run_id, step_index)` that inserts only what
the worker did not write and fills the plan's descriptive fields (risk, description,
correlation) on rows the worker did.

**Why**
That seam already holds a session for `execute_tool`; the write crosses no new process
boundary. A crash between the tool returning and the row committing is the only window left,
and the parent's upsert covers it. Nothing about `_count_completed_segments` changes — it still
picks the segment holding the first unfinished step; replay is what makes re-running it cheap.

**Related Docs**
- `docs/design/RECOVERY_GRANULARITY_DESIGN.md` §1–§2; `AINDY/runtime/nodus_worker.py::_record_step`

---

### DEC-064
**Status:** `accepted` (2026-09-17 — `RECOVERY-GRANULARITY-1`, #722)

**Decision**
The row is `agent_steps`, keyed `(run_id, step_index)`. No new table, no new column, no unique
constraint added (query-then-write; the seam is the only concurrent writer for a run's step).
`steps_completed` keeps FR-34's meaning (successes).

**Why**
`AgentStep` already carries `(run_id, step_index, status, result, error_message, executed_at)`
— DBOS's `operation_outputs` shape. A second table would be a copy read by the same reader.

**Related Docs**
- `AINDY/db/models/agent_run.py` (`AgentStep`)

---

### DEC-065
**Status:** `accepted` (2026-09-17 — `RECOVERY-GRANULARITY-1`, #722)

**Decision**
Identity is the plan's STEP INDEX, emitted by the compiler as a third `call_tool` argument
(`call_tool(tool, args, N)`; `register_function` arity `(2, 3)`), never a call ordinal. A
hand-written `call_tool(name, args)` is unkeyed and unrecorded, as before. A failed attempt
writes a `failed` row that the next attempt of the SAME index overwrites.

**Why**
The compiled plan's retry loop lives INSIDE the guest, so attempt 2 of step 3 is the next
`call_tool` in ordinal terms; an ordinal would record it as step 4 — the mutation the suite
pins. The compiled source already knows `N` (`__step_N_result`).

**Related Docs**
- `AINDY/runtime/agent_plan_compiler.py::_step_source`; `tests/unit/test_recovery_granularity.py::test_a_retry_overwrites_the_same_row`

---

### DEC-066
**Status:** `accepted` (2026-09-17 — `RECOVERY-GRANULARITY-1`, #722)

**Decision**
Replay only on a CONTINUED run (`continuation=True`, set by `continue_crashed_agent_runs`,
threaded through the resume callback → segment chain → segment flow → `__continuation` in flow
state → the worker context) and only from a `success` row; the replayed result carries
`replayed: True`, no new row is written and no step event is emitted for it. A fresh run never
replays; a `failed` row never replays; an unreadable row executes. Not kernel replay
(`DEC-019`): a finished step's RESULT is recorded, nothing is intercepted.

**Why**
A run id is never reused, so a fresh run with a matching row is an upstream bug, and even then
executing is the safe direction. Replayed steps make no LLM call, so the FR-35 ledger and the
effect gate see nothing — which is the cost the entry is about. ★ Implementation note: the
first draft returned `None` for "no row", which also meant "a success row whose result is
`None`" — a tool that returned nothing re-ran on every continuation; found by a surviving
mutation, fixed with a sentinel.

**Related Docs**
- `docs/design/RECOVERY_GRANULARITY_DESIGN.md` §4; `AINDY/runtime/nodus_worker.py::_read_recorded_step`

---

### DEC-067
**Status:** `accepted` (2026-09-20 — `FR-40`, #731)

**Decision**
Declared-args validation outcomes observed in a nodus_vm pool worker ride the worker reply as
`args_validation` — the FIFTH deferred collection, beside `llm_usage` — as a per-tool tally
`{valid, invalid, mode, errors[], dropped_errors}`. Deferral REPLACES observation in the worker
(`DEC-041` applies unchanged): inside `args_validation_deferral_scope()` `execute_tool` neither
increments `aindy_tool_args_validation_total` nor logs the `warn` line. The parent
(`nodus_runtime_adapter.run_script`) increments the counter under the api's registry by the
tallied counts and re-emits ONE `warn` WARNING per tool naming the call count, the carried
errors and the origin. The errors carried per tool are capped at
`AINDY_TOOL_ARGS_VALIDATION_LEDGER_MAX` (default 32); past the cap only `dropped_errors` grows
and the WARNING says how many were not carried. A reply without the key records nothing.

**Why**
The 2.20.0 recipe for FR-33 — leave at `warn`, watch `outcome=invalid` read zero, then
`enforce` — could not be followed on the backend the app runs: the worker's registry never
serves `/metrics` and the pool opens it with `stderr=DEVNULL`, so "every step validated clean"
and "validation never ran" were indistinguishable from the api. Piping worker stderr into the api
log was declined by the filing and here: the frame channel is the design, and `llm_usage` is the
precedent for what crosses it. A tally rather than per-call records because the consumer is a
counter; the errors are carried because the WARNING is useless without them, and capped because
a guest can loop a malformed call. Counts are never dropped: the counter is the witness.

**Related Docs**
- `AINDY/agents/tool_registry.py::ArgsValidationLedger`, `apply_deferred_args_validation`;
  `docs/design/FR35_GUEST_LLM_USAGE_DESIGN.md` (the mechanism this copies)

---

### DEC-068
**Status:** `accepted` (2026-09-20 — `FR-38` / `AUTHORITY-NEGOTIATION-1` §9; filed provisional in #732, accepted by the build, #734)

**Decision**
On the `nodus_vm` backend the WAIT gate is a GUEST WAIT raised from inside `call_tool`: the worker
sets the three guest-wait state keys (`nodus_wait_event_type = "agent.authority.decision"`, the
§5a `resume_schema`, and an `authority_gate` record) and halts the guest exactly as
`await_event()` does; the reply reads `waiting`, and the parent parks the run mid-segment with
`wait_state {event_type, authority_gate, resume_segment_index: this segment, continuation: true}`.

**Why**
The vm chain parks only between segments, at waits the plan declared; a denial happens inside a
segment, in the worker, where nothing can park today. Every primitive needed exists (the guest
wait, the durable `wait_state`, the resume route); a second wait mechanism beside DEC-017's would
be the vocabulary drift `FS-SCOPE-1` warns about. The halt-from-inside-a-compiled-step is the one
unproven assumption and is the first thing the build must test (§9.6 step 1).

★ **Refined by the build (#734).** The guest wait is raised in the worker exactly as decided, but
the FLOW layer does not park on it: inside an agent segment the `nodus.execute` node reports the
gate as a TERMINAL node result (`nodus_status: "authority_gate"`) and the segment chain — which
owns the AgentRun's park, rehydration and atomic claim, and must run the segments after this
one — parks the AgentRun and registers the re-drive. Two waits on one event would have competed,
and a flow-level resume runs on a scheduler thread the Python chain cannot be continued from.
Also found: the halt raised from `call_tool` is redundant with the compiled step's own `throw`
while `permission` stays non-retryable — kept anyway, because it halts AT the call (no failed
`__step_N_result` for the parent to ignore) and covers a hand-written script; the test pins the
difference.

---

### DEC-069
**Status:** `accepted` (2026-09-20 — `FR-38` / `AUTHORITY-NEGOTIATION-1` §9; filed provisional in #732, accepted by the build, #734)

**Decision**
An operator's `skip` on the `nodus_vm` gate is recorded as the `agent_steps` row for
`(run_id, step_index)` with `status="skipped"` and the note, and the segment is re-driven as a
continuation; `_read_recorded_step` replays a `skipped` row as
`{"success": true, "skipped": true, "result": null, "replayed": true}`. This WIDENS `DEC-066`
("replay only a `success` row") to `success | skipped` — both terminal outcomes that a tool or an
operator decided; a `failed` row still never replays. A skipped step never increments
`steps_completed`, on either backend.

**Why**
The re-drive already replays finished steps and executes the rest fresh; the skip only has to look
like a finished step to the compiled plan. Writing the row the worker would have written keeps
the record honest (the step index has an outcome, and it says `skipped`, with who said so), and
keeps `steps_completed` a count of successes (FR-34) — the filing's second small thing was a
`skipped` step counted as completed on `agent_flow`.

---

### DEC-070
**Status:** `accepted` (2026-09-20 — `FR-38` / `AUTHORITY-NEGOTIATION-1` §9; filed provisional in #732, accepted by the build, #734)

**Decision**
`AUTHORITY_NEGOTIATED` is recorded FROM THE WORKER on its own short-lived session, the way
`agent_steps` rows are (DEC-063); the negotiation counter is process-local there and rides the
worker reply as a `{outcome: n}` map beside FR-40's `args_validation`, recorded in the parent.

**Why**
The worker already owns a per-step durable write at this seam; a second write on the same
session pattern is not a new mechanism. The counter cannot be observed in the worker (FR-35,
FR-40 — same shape), and a tally is the right carrier because the consumer is a counter.

---

### DEC-071
**Status:** `accepted` (2026-09-22 — `FR-42`, #750)

**Decision**
The capability-mapping audit row is owed per RUN. `create_run_capability_mappings` writes the
run-scoped `agent_capability_mappings` rows only when the run id is an `agent_runs` row; for any
other scope (a first-party consumer's session, `SUBSTRATE-WITNESS-1`) it writes the agent-type
rows, skips the run rows deliberately, logs at INFO, and the minted token carries
`mapping_recorded: false` — informational, outside the HMAC. The mint never fails on its audit
trail; the foreign key stays.

**Why**
The alternative (drop the FK, record grants per token) was a schema step to serve a row nobody
reads yet; the row's consumers (`AUDIT-CORRELATION-1`'s joins) are keyed on runs. Before this the
non-agent case was a foreign-key violation caught by a broad `except` and logged as a failure —
indistinguishable from a real write failure, and silent to the consumer. A decision that says so
on the token is what the witness needed. If a non-agent consumer ever needs its grants recorded,
that is a per-token table, filed then, not a widened FK now.

**Related Docs**
- `AINDY/agents/capability_service.py::create_run_capability_mappings`; `TECH_DEBT.md` FR-42

---

### DEC-072
**Status:** `accepted` (2026-09-22 — `FR-15`, #751)

**Decision**
FR-15's process-boundary evidence is gathered on ONE dev-host topology, `docker-compose.fr15-evidence.yml`
+ `docker/fr15-evidence/Dockerfile`: the shipped runtime image plus a docker CLI, with the host's
docker socket mounted into the api and worker containers, `EXECUTION_MODE=distributed`,
`AINDY_ASYNC_SCHEDULER_DISPATCH=1`, and the production-safe sandbox chain satisfied minimally and
explicitly (`containerized_oci`, a digest-pinned `python:3.11-alpine` — the gate's own digest — no
source/issuer policy). That file is an evidence instrument: it is never a deployment profile, its
socket mount is never copied into `docker-compose.yml` / `docker-compose.prod.yml` or an operator
document, and it says so in its own header.

**Why**
The distributed half shipped 2026-09-02 and its evidence has never been obtained: the
production-safe profiles demand a container-grade sandbox (`shutil.which("docker")` + a daemon),
the shipped image has no docker binary and the shipped compose grants no daemon, so the stack
crash-loops on its own correct guards (the 2026-09-08 attempt). Lowering the profile is refused
by design; relaxing the guards would be the wrong fix. Mounting the socket gives whatever runs
in those containers root-equivalence on the host — acceptable exactly once, on a machine the
owner controls, to read two numbers (`aindy_execution_dispatch_total{mode="async"}` moving,
`aindy_async_queue_dlq_depth` flat). Production unblocking is a different decision: a nested
container runtime or a sandbox host the worker delegates to, and it is not taken here.

**Related Docs**
- `docker-compose.fr15-evidence.yml`; `TECH_DEBT.md` FR-15 (the 2026-09-08 blocker and the evidence)

★ What the run added to the file: the worker must NOT depend on the api (the `distributed-api`
readiness REQUIRES a worker heartbeat — a worker waiting on a healthy api never starts); the
blank database is bootstrapped with `bootstrap-schema`, not `AINDY_SCHEMA_RECONCILE` (FK order);
the pgvector init script is mounted; the working tree is overlaid on site-packages so a fix is
witnessed before it ships; the docker CLI is downloaded on the host (the build VM's network
fetched it at ~7 KB/s for three hours). Evidence obtained the same day: FR-15 losses #5 and #6.

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

### DEC-073
**Status:** `accepted` (2026-09-25 — `FR-46`; `docs/design/FR46_STEP_REFERENCES_DESIGN.md` §3.1–3.2; accepted by the owner, built #764)

**Decision**
A plan step's argument value may be exactly `{"$from_step": N, "path": "<dot.path>"}` (path
optional): tool step N's result, or a field inside it, replacing the placeholder whole. N is the
tool-step ordinal, strictly earlier. The path uses the verifier's `_resolve_path` vocabulary,
lifted to one shared helper. It is validated at plan time in `generate_plan` and in
`_create_run_from_plan`; an invalid reference refuses the plan. There are no string templates,
no expressions, and no references to anything but an earlier tool step's result.

**Why**
The app's first real goal ran its second half blind (FR-46). One reference form is what it asked
for. A `$`-key cannot collide with an argument name and survives every JSON hop unchanged, and
the runtime already has one path vocabulary into a step result.

### DEC-074
**Status:** `accepted` (2026-09-25 — `FR-46`; design §3.3; accepted by the owner, built #764)

**Decision**
References are resolved by one pure resolver in the two callers of `execute_tool`, before it:
`agent_execute_step` (lookup: flow state `step_results`) and `nodus_worker.run_agent_tool`
(lookup: the `agent_steps` row by `(run_id, step_index)`, which covers WAIT boundaries and
durable step granularity; guest state does not). The idempotency key, `args_schema` validation
and the recorded `tool_args` all see the resolved args. A continuation replay returns before
resolution, unchanged.

**Why**
`execute_tool` is the one seam, but it carries no step context, and threading plan semantics
into it would reach direct tool calls, MCP and syscalls. Resolving before it is what makes
FR-33's `enforce` mode validate the value rather than the placeholder, and what keeps two runs'
different findings from deduping against each other.

### DEC-075
**Status:** `accepted` (2026-09-25 — `FR-46`; design §3.4–3.5; accepted by the owner, built #764)

**Decision**
An unresolvable reference (no entry, a status other than `success` including an authority-gate
`skipped`, or a missing path) fails the step with `failure_class: "invalid"`. The step is never
called with the literal or a partial resolution. The feature ships behind
`AINDY_PLAN_STEP_REFERENCES`, off by default. When it is on, the runtime's tool catalog
(`_build_planner_prompt`) carries the one planner line, so no app prompt has to.

**Why**
A step that runs on a placeholder is the quiet `success` FR-46 was filed for. `invalid` is not
retried (DEC-025) because no retry can make an earlier result appear. Capabilities ship
default-off until evidence; the evidence is the app's own goal re-run.

### DEC-076
**Status:** `accepted` (2026-09-25 — `IDEM-14`, by the owner; #763)

**Decision**
The tool seam's idempotency key is scoped to ONE STEP when the caller knows the step:
`compute_action_id(tool, args, scope=f"{run_id}#step:{step_index}")`. Both agent backends pass
the plan's tool-step index. Every caller without one (syscalls, MCP, extensions, a hand-written
`call_tool(name, args)`) keeps the run scope. A re-attempt of one step is one effect and replays;
two steps asking for the same effect are two effects.

**Why**
Scoped to the run, a plan whose steps 1 and 4 call the same `EXACTLY_ONCE` tool with identical
args ran step 1 and REPLAYED it as step 4. The second effect never happened, and the run said
`success`. The owner's call: "idempotent per tool call / individual step — that's the actual
safe bet". The at-most-once the gate exists for is about a RETRY; a retry never changes its step.

**Accepted cost**
An in-flight step whose effect completed under the old run-scoped key and re-drives across the
upgrade does not find that row. On `nodus_vm` the continuation replays from `agent_steps` before
`execute_tool`, so only an effect whose step row was never recorded is exposed.
