---
title: "Ecosystem Capability Gaps — Corrected aindy-runtime/Nodus Lens"
api_version: "1.0"
last_verified: "2026-06-26"
status: current
owner: "platform-team"
---

# Ecosystem Capability Gaps (corrected lens)

## Purpose & provenance

Distilled from the 12-project ecosystem re-audit (`C:\codev`, 2026-06-24 → 26): 12 open-source
AI/workflow/automation projects (Temporal, LangGraph, Google ADK, MS Agent Framework, CrewAI,
MetaGPT, OpenHands, Open Interpreter, SWE-agent, GPT Engineer, Aider, Devika) re-judged against
**source-verified** aindy-runtime/Nodus facts. It supersedes the gap framing in the external
`Ecosystem_Coverage_Analysis.md` (v1), which rested on three stale premises (corrected below).

This doc is the **in-repo, runtime-POV record** so the findings are discoverable here instead of
stranded in the research directory. The full per-project audits and the corrected aggregate
(`Ecosystem_Coverage_Analysis_v2.md`) live in `C:\codev`.

## Corrected lens facts (do NOT reintroduce the stale premises)

The v1 analysis discounted nearly every rating on three false premises. All three are corrected and
source-cited:

1. **nodus-lang is `4.0.5`, not `3.0.2`** — `pyproject.toml:50`, `AINDY/requirements.txt:27`, installed
   package all 4.0.5. "3.0.2" survives only in a historical code comment and a closed tech-debt note.
2. **The AINDY↔Nodus integration is LIVE on the execution path**, not "two decoupled layers / unbuilt."
   `.nd` scripts run via `sys.v1.nodus.execute` → `NodusRuntimeAdapter._execute()` → subprocess
   `AINDY/runtime/nodus_worker.py`; the host builtin `sys(name, payload)` (`nodus_worker.py:167`) calls
   `dispatch_syscall` → the real `SyscallDispatcher`. This is **Surface B**, live.
3. **`std:sys` (Surface A) is a tracked footgun, not proof of decoupling** — the idiomatic `import "std:sys"`
   bottoms out in nodus-lang's own 4-call in-process ephemeral stub; aindy-runtime does not intercept it.
   Tracked as **NODUS-SYS-SURFACE-1**. It is name-disjoint (`sys` vs `syscall`), not evidence the stack is
   unintegrated.

## Taxonomy — why these are mostly NOT classic tech debt

These are **capability / roadmap gaps surfaced by competitive analysis**, not shortcuts in existing code.
Only **G6** (and, narrowly, **G5a**) are debt-shaped. The rest are deferred roadmap items, recorded as
`ECOGAP-*` in `TECH_DEBT.md` (the same way `MONETIZATION_AUDIT.md`→`BILLING-*` and
`DEPLOYMENT_TARGETS.md`→`DEPLOY-TARGET-*` already work), and several map onto **existing** tracking.

## Gap register (altitude-corrected)

| # | Gap | Class | Severity | Existing tracking | Who leads in the field |
|---|---|---|---|---|---|
| **G1** | **Event-sourced durable execution / transparent crash continuation.** Non-waiting `running` flows are marked FAILED on restart; no replay log. (WAIT/RESUME + rehydration + ResumeWatchdog already cover *suspended* flows.) | Roadmap | **P0** | `ECOGAP-1` (new) | Temporal (gold); LangGraph partial; ADK/OpenHands/OI ship event logs |
| **G2** | **Hostile-safe sandboxing — strong-VM tier on non-Linux.** *(Audit overstated this — see correction below.)* Container-grade is closed/certified/escape-tested cross-platform; the residual is the `strong_sandbox_vm` (dedicated-VM, hostile-third-party) tier being Linux-only, plus the dev default being unsandboxed by design. | Roadmap | **P2** (was wrongly P0) | **C2 (closed), C3 (open)** | OpenHands/OI/SWE-agent at container-to-strong; near parity |
| **G3** | **Provider breadth + embedding SPOF.** Only OpenAI + DeepSeek concretely in tree; OpenAI hard-required for embeddings. | Roadmap | **P1** | **MEMORY-EMBEDDING-PROVIDER-1** + `ECOGAP-3` (LLM breadth) | ADK (100+), MS (~20), MetaGPT (~27), CrewAI (5+cache) |
| **G4a** | **Capability-gated egress + secret-broker** (MCP-as-syscall-boundary). Runtime-owned, trusted/enforced half. | Roadmap | **P1** | `ECOGAP-4` (new) | OpenHands (control-plane MCP host + key proxy) |
| **G4b** | **Concrete MCP/A2A wire adapters.** App/plugin layer, registered via the plugin ABI — *not* a kernel primitive. | Hosted/plugin | P2 | `ECOGAP-4` (new) | CrewAI (MCP+A2A client), ADK/MS (A2A edge) |
| **G5a** | **Durable timer / misfire handling.** Largely solved: user schedules are DB-backed (`NodusScheduledJob`) and rehydrated on boot (`restore_nodus_scheduled_jobs()`); residual = missed-fire/misfire-grace during downtime. | Debt (small) | **P3** | `ECOGAP-5` (new) | Temporal (durable timer queue) |
| **G5b** | **Workflow-as-data.** `FLOW_REGISTRY` is in-process Python (business structure in kernel code); the fix is a loadable graph artifact (Nodus `.nodus/graphs/<id>.json`) the runtime interprets. **Anti-creep mechanism**, Nodus/language layer. | Roadmap (Nodus) | **P2** | `ECOGAP-5` (new) | Temporal (CHASM workflow-as-data) |
| **G6** | **Execution-path test coverage.** *(Corrected 2026-07-12 — see ECOGAP-6.)* Surface-B has real-subprocess/real-PG coverage (`test_agent_vm_parity`, `test_planner_loop_*`) and CI runs the real PG+Redis integration tier; the true gap was `worker/worker_loop.py` (zero) + continuation resume (unit-only). Now largely closed. | **Debt** | **P2** | `ECOGAP-6` (largely closed) | internal hygiene; no external leader |

**Altitude note.** G4 and G5 each staple a runtime primitive to an app/data concern; the table splits them.
The test: a *mechanism* (timer, socket, scheduler, capability gate) is runtime; a *policy/content* (which job,
which protocol schema, which cadence) is app/data the runtime interprets. By that test, "speak MCP on the wire"
(G4b) is a hosted plugin, and **workflow-as-data (G5b) is the mechanism that removes business logic from the
runtime**, not new creep.

## G2 — correction notice (the ecosystem audit was wrong)

The v1/v2 ecosystem analysis flagged hostile-safe sandboxing as a leading **P0** gap ("default execution is
in-process/trusted; the execution-sandbox peers are materially ahead"). **This understates the actual,
source-verified state** and is corrected here:

- **A real 3-tier model exists** (`sandbox_runner.py`): `insecure_dev_subprocess` / `containerized_oci` /
  `strong_sandbox_vm`, with assurance classes, OCI digest pinning, and kernel-observable vs worker-self-report
  verification.
- **Container-grade is closed and certified cross-platform** — **C2 CLOSED 2026-05-24**; Linux + Windows + macOS
  reach `container-grade-sandbox`; live-verified on Windows + Docker Desktop (`tier_status: certified`).
- **It is adversarially escape-tested** — 17 tests across 6 categories (`tests/sandbox/`, marker
  `sandbox_escape`) run against **real Docker, no mocking**; all PASS (filesystem escape, host-secret-canary
  unreachable, path-traversal containment, read-only rootfs/mount). Append-only threat-model audit log
  (`SANDBOX_ESCAPE_AUDIT.md`), macOS CI escape workflow, WSL2 detection, and a `sandbox_escape_test_posture()`
  CI gate back the claim.
- **The default is environment-aware** (`resolve_sandbox_runner_type()`): distributed/production profiles
  (`distributed-api`/`distributed-worker`/`EXECUTION_MODE=distributed`) auto-select `containerized_oci` — the
  certified tier — and only the **dev** profile falls back to `insecure_dev_subprocess`. "Default is in-process/
  trusted" is true of dev only.

**The genuine residual gap** (already tracked as **C3**, Phases 1–4 open): the strongest tier
`strong_sandbox_vm` (dedicated-VM boundary; the only tier rated for *hostile third-party* code) is **Linux-only**
— non-Linux hosts cap at `container-sandbox-certified`, not `strong-sandbox-certified`. Closing it needs
platform-specific strong-sandbox runtimes (e.g. a Windows `aindy-sandbox-vm` bridging to WSL2; macOS native).

**Net:** G2 is **P2, not P0**, and is owned by the existing **C2 (closed) / C3 (open)** entries — no new debt.
The OpenHands / Open Interpreter / SWE-agent per-project audits and the v2 aggregate overstate it and should be
reconciled against C2/C3.

## Absorb register (condensed; placed on the correct layer)

**→ aindy-runtime (durable kernel):** event-sourced replay / transparent crash continuation (ADK append-event
fold, LangGraph pending-writes + `versions_seen`, OI/OpenHands rollout-as-truth, Temporal at-least-once
idempotent-start) — the dominant ask; gated egress + secret-broker (OpenHands); provider breadth behind
`CircuitBreakerLLMClient` (CrewAI multi-SDK + cache-breakpoint, Devika 7-backend, litellm reach); durable
self-repair / RangeID fence / transactional outbox (Temporal, Devika, SWE-agent); hard token/$ budget ceiling
on `ResourceManager` (MetaGPT). *Do not import weaker JSON-snapshot durability (MetaGPT).*

**→ Nodus (orchestration language / VM):** workflow-as-data hardening of `.nodus/graphs/<id>.json` (Temporal,
MS, MetaGPT); richer task-graph scheduling syntax — frontier/ready-set + JoinNode fan-in + dynamic nodes (ADK),
fire-when-all-sources buffering (MS); reducer-cell merge / serde allowlist (LangGraph); `std:tool`/`std:retry`
ergonomics — introspective registration (MetaGPT), schema-from-signature (ADK), ACI tool-bundle model
(SWE-agent); suspension seams — one-liner ask-human→suspend→resume backed by WAIT/RESUME, yield-don't-block
subprocess (OI). *Determinism/replay stays a VM concern, never a kernel concern (Temporal design rule).*

**→ App-layer (hosted; the stack runs it):** personas/SOPs/Roles as data (Devika, MetaGPT, CrewAI); ReAct /
planning / routing content; coding-agent mechanics — windowed ACI (SWE-agent), repo-map + edit-formats (Aider,
GPTe), `_harness` loop (MS); context-window compaction loops (OpenHands condenser, OI); eval / reproducibility /
external control planes (GPTe, ADK, SWE-agent, CrewAI AMP — do not reimplement).

## Cross-references

- `NODUS-SYS-SURFACE-1` — the Surface A/B footgun (TECH_DEBT.md).
- `C2` (closed) / `C3` (open) — the sandbox tiers that own G2.
- `MEMORY-EMBEDDING-PROVIDER-1` — owns part of G3 (embedding SPOF).
- `ECOGAP-1..6` — the deferred capability-gap entries (TECH_DEBT.md) derived from this doc.
- `C:\codev\Ecosystem_Coverage_Analysis_v2.md` — the full corrected aggregate.
