# `aindy-runtime` documentation

Seven folders, each answering one kind of question. If you are not sure where something is,
the question you are asking tells you the folder.

| You want to know… | Folder | Maintained? |
|---|---|---|
| **What the runtime is and what it guarantees** — contracts, invariants, references | [`runtime/`](#runtime--what-the-runtime-is-and-guarantees) | yes — must be true of `main` |
| **How to run it** — install, deploy, read `/ready`, respond to an incident | [`operations/`](#operations--running-it) | yes |
| **How work is done on this repo** — rules, release gates, CI, testing, decisions | [`governance/`](#governance--how-we-work-on-the-repo) | yes |
| **Why something was built the way it was** — scopes, designs, program plans, proposals | [`design/`](design/README.md) | status per doc |
| **What changes when I move between releases** — one handoff per release, indexed by schema step | [`upgrades/`](upgrades/README.md) | append per release |
| **What this repo is asking of a sibling repo** — outbound handoffs to Nodus | [`handoffs/`](handoffs/README.md) | status per ask |
| **Worked examples** for writing flows and scripts | [`tutorials/`](tutorials/index.md) | yes — every call checked against source 2026-09-13 |
| **What was true once** — completed plans, point-in-time audits, superseded trackers | [`archive/`](archive/README.md) | **no** — audit trail only |

`Runtime Docs Validation` in CI checks five-key frontmatter and a real `last_verified` date on
every file in `runtime/`, `operations/`, `governance/`, `upgrades/`, `handoffs/`, `design/` and `tutorials/`.
The archive is deliberately outside it.

**History.** Until 2026-09-13 almost everything lived in `docs/runtime/` (108 files), with three
governance docs buried at `docs/platform/governance/` — the monolith's path, copied verbatim in
the 2026-06-27 relocation and never chosen. The split into the folders above happened in six
passes that day; `archive/README.md` records what was archived and why, per document.

---

## `runtime/` — what the runtime is and guarantees

Start with the first three; everything else is a reference you go to when a specific question
arises. Every claim here is meant to be true of `main` today — if it is not, that is a bug in the
doc or the code, and either way it gets fixed rather than caveated.

**Orientation**
- [`WHAT_THE_RUNTIME_IS.md`](runtime/WHAT_THE_RUNTIME_IS.md) — the category, what a consumer inherits, and where the claims stop. *"The app owns the formulas. The runtime owns the loop."*
- [`ARCHITECTURE.md`](runtime/ARCHITECTURE.md) — layers, request → execution pipeline, and the contracts table
- [`FOUNDATIONAL_PATTERN.md`](runtime/FOUNDATIONAL_PATTERN.md) — the Infinity Algorithm loop the runtime exists to run
- [`RUNTIME_BOUNDARY.md`](runtime/RUNTIME_BOUNDARY.md) — what is runtime-owned and what is not
- [`RUNTIME_MODULE_MAP.md`](runtime/RUNTIME_MODULE_MAP.md) — every directory and module under `AINDY/`, tagged
- [`RUNTIME_BEHAVIOR.md`](runtime/RUNTIME_BEHAVIOR.md) — the FastAPI backend as implemented in `AINDY/main.py`
- [`ROUTE_OWNERSHIP_INVENTORY.md`](runtime/ROUTE_OWNERSHIP_INVENTORY.md) — every router classified core / platform / app

**Contracts** — numbered invariants with enforcement points and the tests that pin them
- [`EXECUTION_CONTRACT.md`](runtime/EXECUTION_CONTRACT.md) — how work is structured, persisted and observed
- [`EXECUTION_INVARIANTS.md`](runtime/EXECUTION_INVARIANTS.md) — behaviours preserved across refactors and releases
- [`IDEMPOTENCY_CONTRACT.md`](runtime/IDEMPOTENCY_CONTRACT.md) — how an effect is prevented from happening twice; `EffectRecord`
- [`SANDBOX_CONTRACT.md`](runtime/SANDBOX_CONTRACT.md) — how far code the runtime did not author can reach; the three seams
- [`RETRY_POLICY.md`](runtime/RETRY_POLICY.md) — retry semantics and the retryability classifier
- [`CONNECTOR_CONTRACT.md`](runtime/CONNECTOR_CONTRACT.md) — connector registration and the authorized outbound boundary (FR-1)
- [`NODUS_WORKFLOW_CONTRACT.md`](runtime/NODUS_WORKFLOW_CONTRACT.md) — `register_nodus_workflow` (RTR-1)
- [`DB_OWNERSHIP_CONTRACT.md`](runtime/DB_OWNERSHIP_CONTRACT.md) — runtime-vs-app database ownership
- [`SCHEMA_LIFECYCLE.md`](runtime/SCHEMA_LIFECYCLE.md) — schema contract version, `bootstrap-schema`, `--reconcile`, the runtime's Alembic table
- [`DATA_MODEL_MAP.md`](runtime/DATA_MODEL_MAP.md) — runtime-owned PostgreSQL and memory-bridge data model

**Public surfaces and their stability**
- [`PUBLIC_RUNTIME_SURFACES.md`](runtime/PUBLIC_RUNTIME_SURFACES.md) — the external surface in stability terms
- [`RUNTIME_STABILITY_INDEX.md`](runtime/RUNTIME_STABILITY_INDEX.md) — four stability levels and which surface sits where
- [`PUBLIC_API_CONTRACT.md`](runtime/PUBLIC_API_CONTRACT.md) — the import contract between runtime and apps
- [`SDK_CONTRACT.md`](runtime/SDK_CONTRACT.md) — what `aindy-sdk` may rely on
- [`UI_CONTRACT.md`](runtime/UI_CONTRACT.md) — what the platform SPA may rely on
- [`SYSCALL_SYSTEM.md`](runtime/SYSCALL_SYSTEM.md) — `sys.v1.domain.action`, the dispatcher, the registry
- [`SYSCALL_REFERENCE.md`](runtime/SYSCALL_REFERENCE.md) — every registered syscall; capability is not API-key scope

**Subsystems**
- [`AGENT_RUNTIME.md`](runtime/AGENT_RUNTIME.md) — `AINDY/agents/`: approval, execution, tools, coordination
- [`APP_AGENT_REGISTRATION.md`](runtime/APP_AGENT_REGISTRATION.md) — the two registration models (apps vs agents)
- [`NODUS_DEVELOPER_GUIDE.md`](runtime/NODUS_DEVELOPER_GUIDE.md) — writing Nodus scripts: injected globals, builtins, WAIT/RESUME, errors
- [`MCP_INTEGRATION.md`](runtime/MCP_INTEGRATION.md) — agents calling external MCP tools (ECOGAP-4 G4b)
- [`OS_ISOLATION_LAYER.md`](runtime/OS_ISOLATION_LAYER.md) — tenant isolation, quotas, priority scheduling
- [`MEMORY_ADDRESS_SPACE.md`](runtime/MEMORY_ADDRESS_SPACE.md) — `/memory/{tenant}/{namespace}/{type}/{id}`
- [`MEMORY_BRIDGE.md`](runtime/MEMORY_BRIDGE.md) — the memory bridge; prefer the narrower contracts it points to
- [`MEMORY_BRIDGE_CONTRACT.md`](runtime/MEMORY_BRIDGE_CONTRACT.md) — API contract and security boundary; legacy `/bridge/*` half is app-owned
- [`NATIVE_MEMORY_BRIDGE.md`](runtime/NATIVE_MEMORY_BRIDGE.md) — the optional Rust scorer and its loader

**Extensions and security**
- [`EXTENSION_TRUST_MODEL.md`](runtime/EXTENSION_TRUST_MODEL.md) — the two-tier contract; §Assurance Reporting defines the sandbox vocabulary
- [`EXTENSION_CAPABILITIES.md`](runtime/EXTENSION_CAPABILITIES.md) — the capability model for extension surfaces
- [`EXTENSION_ABI.md`](runtime/EXTENSION_ABI.md) — ABI versioning policy
- [`EXTENSION_PROVENANCE.md`](runtime/EXTENSION_PROVENANCE.md) — provenance and integrity for admitted extensions
- [`SECURITY_POSTURE.md`](runtime/SECURITY_POSTURE.md) — the posture as it exists and the claim the team will defend
- [`SECURITY_MATRIX.md`](runtime/SECURITY_MATRIX.md) — five security dimensions mapped to enforcement
- [`SANDBOX_ESCAPE_AUDIT.md`](runtime/SANDBOX_ESCAPE_AUDIT.md) — **append-only** log of every release-gate escape run; the evidence behind the sandbox claim

**Roadmap-shaped**
- [`ECOSYSTEM_CAPABILITY_GAPS.md`](runtime/ECOSYSTEM_CAPABILITY_GAPS.md) — `ECOGAP-1..6`, corrected against source

---

## `operations/` — running it

- [`QUICKSTART.md`](operations/QUICKSTART.md) — a local server against real PostgreSQL in under five minutes
- [`RUNTIME_ONLY_DEPLOYMENT.md`](operations/RUNTIME_ONLY_DEPLOYMENT.md) — the boot and startup contract without any app loaded
- [`DEPLOYMENT_PROFILES.md`](operations/DEPLOYMENT_PROFILES.md) — `single-instance` / `distributed-api` / `distributed-worker` / `hostile-third-party` and what each requires
- [`PROFILE_SUPPORT_MATRIX.md`](operations/PROFILE_SUPPORT_MATRIX.md) — what each profile may claim, and on which hosts
- [`DEPLOYMENT_TARGETS.md`](operations/DEPLOYMENT_TARGETS.md) — cloud targets and the readiness gates for them (`DEPLOY-TARGET-1/2`)
- [`DEPENDENCY_CRITICALITY_MATRIX.md`](operations/DEPENDENCY_CRITICALITY_MATRIX.md) — which dependency loss does what
- [`DEGRADED_MODE_MATRIX.md`](operations/DEGRADED_MODE_MATRIX.md) — behaviour when a dependency is missing or degraded
- [`DEGRADED_RUNTIME_MODES.md`](operations/DEGRADED_RUNTIME_MODES.md) — the degraded-mode contract as exposed through `/health` and `/ready`
- [`CONDITION_CODES.md`](operations/CONDITION_CODES.md) — every operator-facing code, status string and state-machine value; a change is a MAJOR bump
- [`OPERATOR_RUNBOOK.md`](operations/OPERATOR_RUNBOOK.md) — the high-level runbook
- [`INCIDENT_CLASSIFICATION.md`](operations/INCIDENT_CLASSIFICATION.md) — how an incident is classified
- [`MACOS_CONTAINER_POLICY.md`](operations/MACOS_CONTAINER_POLICY.md) — the macOS container-sandbox policy

Version-to-version moves are [`upgrades/`](upgrades/README.md), not here.

---

## `governance/` — how we work on the repo

**Read first** — the three that were buried under `docs/platform/governance/`
- [`AGENT_WORKING_RULES.md`](governance/AGENT_WORKING_RULES.md) — what an agent may change without approval, what needs sign-off, and the §8 proposal-first rule. **`CLAUDE.md` names this as its companion.**
- [`INVARIANTS.md`](governance/INVARIANTS.md) — the runtime-owned invariants every change is reviewed against; design docs cite "invariants touched" from here
- [`ERROR_HANDLING_POLICY.md`](governance/ERROR_HANDLING_POLICY.md) — the runtime-owned error-handling policy

**Release**
- [`RELEASE_CHECKLIST.md`](governance/RELEASE_CHECKLIST.md) — the per-release verification steps, including writing the upgrade handoff
- [`RELEASE_GATES.md`](governance/RELEASE_GATES.md) — what must be green, and what a green means
- [`RELEASE_STAGING.md`](governance/RELEASE_STAGING.md) — staging the release flow without publishing
- [`CHANGE_IMPACT_MATRIX.md`](governance/CHANGE_IMPACT_MATRIX.md) — change class drives review depth; "small diff" is not low risk

**Verification**
- [`CI_OWNERSHIP.md`](governance/CI_OWNERSHIP.md) — which checks are authoritative, and that a coverage threshold is not proof
- [`TEST_STRATEGY.md`](governance/TEST_STRATEGY.md) — the test strategy
- [`INVARIANT_TEST_MAPPING.md`](governance/INVARIANT_TEST_MAPPING.md) — invariant → test
- [`GITHUB_SETTINGS_CHECKLIST.md`](governance/GITHUB_SETTINGS_CHECKLIST.md) — the manual GitHub UI settings (branch protection, required checks)

**Compatibility and policy**
- [`CROSS_REPO_COMPATIBILITY.md`](governance/CROSS_REPO_COMPATIBILITY.md) — the obligations the runtime must satisfy before a release
- [`REPO_COMPATIBILITY_POLICY.md`](governance/REPO_COMPATIBILITY_POLICY.md) — the high-level policy across the repo split
- [`MODEL_OWNERSHIP_POLICY.md`](governance/MODEL_OWNERSHIP_POLICY.md) — when a SQLAlchemy model is runtime-owned vs app-owned
- [`SECURITY_POLICY.md`](governance/SECURITY_POLICY.md) — vulnerability reporting and the response SLA

**The docset itself**
- [`RUNTIME_DOCSET_GOVERNANCE.md`](governance/RUNTIME_DOCSET_GOVERNANCE.md) — status labels, what a doc may claim, how drift is handled
- [`DECISION_LOG.md`](governance/DECISION_LOG.md) — the nine founding decisions. Decisions since August are in `TECH_DEBT.md` (`DECISIONS-2026-08-01`) and `CLAUDE.md` §*Recorded decisions*; the log says so
- [`DOCSET_CHANGELOG.md`](governance/DOCSET_CHANGELOG.md) — notable changes to the docset's structure
- [`COMPARATIVE_RESEARCH_INDEX.md`](governance/COMPARATIVE_RESEARCH_INDEX.md) — nineteen external systems audited against this runtime: what each produced, what is settled, the recurring errors

---

## Where things are *not*

- **The changelog** is `CHANGELOG.md` at the repo root, assembled at release from `changelog.d/`. The pre-split monolith's changelog is in the archive.
- **Open work** is `TECH_DEBT.md` at the root, indexed by the prefix registry in `CLAUDE.md`. Neither is a doc in this tree.
- **Open questions** live with the contract they concern (the *Open Operational Questions* section at the end of a contract) or in the owning `TECH_DEBT.md` entry — there is no standalone tracker any more.
- **Runnable examples** are `tutorials/` — verified live. `examples/` at the repo root is a pointer
  to them and to the real consumer, with `SUBSTRATE-WITNESS-1`'s caveat.
- **The apps, SDK and UI kit** have their own repositories and docsets; this tree describes the runtime's side of each boundary only.
