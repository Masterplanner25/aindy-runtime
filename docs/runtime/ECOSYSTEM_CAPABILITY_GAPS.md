---
title: "Ecosystem Capability Gaps — Corrected aindy-runtime/Nodus Lens"
api_version: "1.0"
last_verified: "2026-08-17"
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

1. **nodus-lang is not `3.0.2`** — *(version updated 2026-08-17: the pin is now **`nodus-lang==5.0.1`**
   with `nodus-mcp>=0.1.3`, in `pyproject.toml` and `AINDY/requirements.txt`, which must agree —
   `tests/unit/test_dependency_pin_agreement.py` enforces it. This line read "4.0.5" and was quoted at
   that value by four external analyses; the standing point is that the embed is current, not that it
   is any particular number, so re-read the pin rather than this line.)* "3.0.2" survives only in a
   historical code comment and a closed tech-debt note.
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
| **G1** | **Event-sourced durable execution / transparent crash continuation.** *(Corrected 2026-08-17 — this row described the state before ECOGAP-1 Phase 1 merged 2026-07-08.)* Still true **in default configuration**: a non-waiting `running`/`executing` flow is failed by the stuck-run scanners. But `core/flow_continuation.py` + `core/agent_continuation.py` + the `FlowHistory` fold now provide checkpoint-resume from the last committed node — **opt-in and default-off** (`AINDY_DURABLE_CONTINUATION`), continuation-safe flows only. Checkpoint resume, not replay. (WAIT/RESUME + rehydration + ResumeWatchdog already cover *suspended* flows.) | Roadmap | **P0** | `ECOGAP-1` (Phases 1+2+2a shipped opt-in; DUR-1..4 delivered) | Temporal (gold); LangGraph partial; ADK/OpenHands/OI ship event logs |
| **G2** | **Hostile-safe sandboxing — strong-VM tier on non-Linux.** *(Audit overstated this — see the correction notice below.)* Container-grade is closed/certified/escape-tested cross-platform; the residual is the `strong_sandbox_vm` (dedicated-VM, hostile-third-party) tier being Linux-only, plus the dev default being unsandboxed by design. | Roadmap | **P2** (was wrongly P0) | **C2 (closed), C3 (open)** | **SWE-agent + OpenClaw** — ★ **the witness list was wrong twice; see the G2 correction notice before citing this row** |
| **G3** | **Provider breadth + embedding SPOF.** *(★ CLOSED-IN-SUBSTANCE 2026-07-12 via ECOGAP-3; this row was NOT updated and the stale text propagated — corrected 2026-08-17.)* It read *"Only OpenAI + DeepSeek concretely in tree; OpenAI hard-required for embeddings."* **Both halves are now false:** the LLM registry is open (`register_llm_provider`, `FallbackLLMClient`, `registered_provider_names`) with **four** built-ins — `openai`, `deepseek`, `anthropic`, `azure_openai` — and embeddings run behind `AINDY_EMBEDDING_PROVIDER` with a `local` sentence-transformers path plus a `memory reembed` migration. Residual: built-in *breadth* by count still trails the field; **extensibility no longer requires a core edit.** | Roadmap | **P3** (was P1) | **MEMORY-EMBEDDING-PROVIDER-1** + `ECOGAP-3` (shipped) | ADK (100+), MS (~20), MetaGPT (~27), CrewAI (5+cache) |
| **G4a** | **Capability-gated egress + secret-broker** (MCP-as-syscall-boundary). Runtime-owned, trusted/enforced half. | Roadmap | **P1** | `ECOGAP-4` (new) | OpenHands (control-plane MCP host + key proxy) |
| **G4b** | **Concrete MCP/A2A wire adapters.** *(★ MCP SHIPPED both directions 2026-07-11, opt-in — this row was not updated and an external analysis published "[Observed] the runtime has no MCP client" against a pin a month later. Corrected 2026-08-17.)* **In tree:** `platform_layer/mcp_client.py` (#222 — discovers a remote server's tools and registers each via `register_tool` under a dedicated `MCP_EGRESS_CAPABILITY`, distinct from `outbound.http`, so remote tools pass the same `execute_tool` gate as local ones) and `platform_layer/mcp_server.py` (#223 — stdio + SSE, syscall allowlist, auth hook). Both default-off; `[mcp]` extra. **Still absent: A2A — zero matches under `AINDY/`.** | Hosted/plugin | P2 (**A2A only**) | `ECOGAP-4` (G4b MCP shipped) | CrewAI (MCP+A2A client), ADK/MS (A2A edge) |
| **G5a** | **Durable timer / misfire handling.** *(SHIPPED 2026-07-12 — see ECOGAP-5.)* Fixed a latent bug where restored jobs failed to register with the real scheduler; added per-job `misfire_policy` (`skip`/`run_once`) with a coalesced downtime catch-up. FireTime primitive deferred. **★ Shipped reference for the residual (2026-08-19): DBOS's `workflow_schedules` carries `automatic_backfill` beside `last_fired_at` — backfill as a per-schedule declared property rather than a global policy.** | Debt (small) | **P3** | `ECOGAP-5` (5a shipped) | Temporal (durable timer queue) |
| **G5b** | **Workflow-as-data.** *(Largely DELIVERED via RTR-1 — tracking was stale.)* Ships as the `NodusWorkflow` table (versioned `.nd` source artifact) + `register`/`rehydrate`/`run_nodus_workflow`; `FLOW_REGISTRY` holds only runtime kernel flows, not business creep. JSON-graph variant deferred. | Roadmap (Nodus) | **P2** | `ECOGAP-5` (5b delivered) | Temporal (CHASM workflow-as-data) |
| **G6** | **Execution-path test coverage.** *(Corrected 2026-07-12 — see ECOGAP-6.)* Surface-B has real-subprocess/real-PG coverage (`test_agent_vm_parity`, `test_planner_loop_*`) and CI runs the real PG+Redis integration tier; the true gap was `worker/worker_loop.py` (zero) + continuation resume (unit-only). Now largely closed. | **Debt** | **P2** | `ECOGAP-6` (largely closed) | internal hygiene; no external leader |

**Altitude note.** G4 and G5 each staple a runtime primitive to an app/data concern; the table splits them.
The test: a *mechanism* (timer, socket, scheduler, capability gate) is runtime; a *policy/content* (which job,
which protocol schema, which cadence) is app/data the runtime interprets. By that test, "speak MCP on the wire"
(G4b) is a hosted plugin, and **workflow-as-data (G5b) is the mechanism that removes business logic from the
runtime**, not new creep.

## G2 — correction notice (the ecosystem audit was wrong)

**★ Second correction, 2026-08-19 — the *witnesses* were wrong, and so was the framing.**

The "who leads" column read **"OpenHands/OI/SWE-agent at container-to-strong; near parity."**
Two of those four cited witnesses do not survive contact with their own source:

| Cited | Standing after verification |
|---|---|
| **Open Interpreter** | **Discounted** (2026-08-18) — a fork of the Codex monorepo; `package.json` names it `codex-monorepo` and the `sandboxing/` crates credited to it are `codex-rs`'s |
| **OpenHands** | **Materially weakened** (2026-08-19) — `openhands/app_server/sandbox/docker_sandbox_service.py` sets **no** `mem_limit`, `nano_cpus`, `security_opt`, `seccomp`, `apparmor`, `cap_drop`, `user=`, `read_only` or `pids_limit`. A container per session **with default settings**. The control plane holds the Docker client directly (`:102`) — its own audit: *"compromise of the FastAPI app = host takeover."* Only the **remote** backend carries `runtime_class` (gvisor/sysbox), enforced in an external service no audit could inspect |
| **SWE-agent** | **Stands** — tools are installed *into* the deployment; the boundary is structural, not a wrapper around host execution |
| **OpenClaw** | **Stands** — bind mounts, network mode, seccomp and AppArmor validated and refused before container start |

**★ And the framing was wrong, not only the count.** This row has been read as *"the
execution-sandbox peers are materially ahead."* **On container hardening, we are ahead of OpenHands
on the self-hosted path**: `ContainerizedOciSandboxRunner` applies a read-only rootfs, a read-only
plugin mount and `--pids-limit`, is escape-tested 17/17 across six vector classes every release,
and reports an assurance ceiling that refuses to overclaim.

**What this runtime actually lacks is narrower, and each half is already tracked:**

1. **Default-on** — `insecure_dev_subprocess` is the default outside distributed profiles.
2. **Wiring** — `TOOL-SEAM-ISOLATION-1`: the provider reaches the plugin seam, not the tool seam.
3. **The non-Linux strong tier** — `C3`.

**The gap is defaults and wiring, not capability** — a materially different piece of work from
"catch up on container hardening," and the reason this row sat at a wrong severity for so long.


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
