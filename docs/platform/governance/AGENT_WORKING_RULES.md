---
title: "Agent Working Rules"
last_verified: "2026-08-05"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Agent Working Rules

This document defines enforceable collaboration boundaries for AI agents operating in this repository. It is directive and governance-focused.

> **How this is reached.** [`CLAUDE.md`](../../../CLAUDE.md) is the authoritative
> agent-instruction surface and links here from its header; `CODEX.md` is a pointer to
> `CLAUDE.md`. That chain is load-bearing — until 2026-08-05 nothing referenced this file,
> so a document calling itself directive was in practice read by no one. If you restructure
> the agent-instruction docs, keep a path from `CLAUDE.md` to here.
>
> Division of labour: `CLAUDE.md` records what is *true* about the codebase (invariants,
> protocols, hazards). This file records what you are *permitted to do* to it.

> **Post-split note (2026-06-27):** Relocated into `aindy-runtime` from the
> pre-split monolith archive. File paths have been updated for the
> runtime/apps split: paths under `AINDY/...` are runtime-owned in this repo;
> paths under `apps/...` and `client/...` are app-owned and now live in the
> **aindy-apps-monolith** repo. `INVARIANTS.md` is now split — the runtime half
> is [`docs/platform/governance/INVARIANTS.md`](./INVARIANTS.md) in this repo
> and the app-domain half lives in aindy-apps-monolith. `DATA_MODEL_MAP.md` is
> now relocated (runtime-scoped, Tier-2 surgery — `docs/architecture/DATA_MODEL_MAP.md`).
> Two governance docs referenced below were not part of this migration pass.
> Re-checked 2026-08-05: `SYSTEM_SPEC.md` exists in **neither** repo and its references
> are historical only; `GOVERNANCE_INDEX.md` does **not** exist here but does exist in
> aindy-apps-monolith at `docs/GOVERNANCE_INDEX.md`, so references to it mean that file. The rules themselves are repo-agnostic and remain normative.

## 1. Scope of Authority

### Allowed Without Approval
- Documentation updates limited to `/docs` that reflect current implementation.
- Small, localized code changes that fix clear defects without changing public API, schema, or runtime behavior.
- Test additions that validate existing behavior without altering runtime logic.

### Requires Explicit Human Approval
- Any change to API contracts in `AINDY/routes/` or `client/src/api.js` _(app-owned, aindy-apps-monolith)_.
- Any change to database schema, including ORM models in `AINDY/db/models/`.
- Any change to `AINDY/config.py` or `AINDY/db/database.py`.
- Any change to Memory Bridge logic (`AINDY/memory/memory_persistence.py`; `apps/bridge/routes/bridge_router.py` _(app-owned, aindy-apps-monolith)_).
- Any change to Genesis session or masterplan logic (`apps/masterplan/routes/genesis_router.py`, `apps/masterplan/services/masterplan_factory.py`, masterplan ORM model — all app-owned, aindy-apps-monolith).
- Any change to background tasks or concurrency behavior (`AINDY/main.py`; `apps/tasks/services/task_service.py` _(app-owned, aindy-apps-monolith; renamed from `task_services.py`)_).

### Prohibited Without Exception
- Removing security checks or permission validation.
- Changing or deleting existing Alembic migration files.
- Introducing new frameworks or replacing core libraries without a written proposal and approval.

## 2. Refactoring Rules

### Refactoring Is Allowed Only If
- The refactor is small and localized, or
- A plan-first proposal has been approved for larger changes.

### Refactoring Must Preserve
- All invariants in `docs/platform/governance/INVARIANTS.md` _(runtime half; app-domain invariants in aindy-apps-monolith)_.
- Public API contracts (FastAPI routes and request/response shapes).
- Migration compatibility for existing database state.

### Refactoring Must Not
- Change the database schema without a new Alembic migration.
- Alter cross-module boundaries (e.g., move responsibilities across `routes/`, `services/`, `db/`).
- Modify the runtime concurrency model (threads, async behavior, background loops).

### Large Refactors
- Require a proposal-first plan and explicit approval before any implementation.

## 3. Sensitive Files and Directories

The following are high-sensitivity areas and require explanation of impact and explicit confirmation before any modification:
- `AINDY/db/models/`
- `alembic/`
- `AINDY/config.py`
- `AINDY/db/database.py`
- Memory Bridge logic: `AINDY/memory/memory_persistence.py`; `apps/bridge/routes/bridge_router.py` _(app-owned)_
- Genesis session logic: `apps/masterplan/routes/genesis_router.py`, `apps/masterplan/services/masterplan_factory.py`, masterplan ORM model _(all app-owned)_

## 4. Database and Migration Safety Rules
- Never edit existing Alembic migration files after they have been applied.
- Schema changes must include:
- ORM model update.
- New Alembic revision.
- Documentation update in `docs/architecture/DATA_MODEL_MAP.md` _(runtime-scoped; app-domain tables tracked in aindy-apps-monolith)_.
- Never remove constraints without explicit approval.

## 5. Concurrency and Session Rules
- Never share SQLAlchemy sessions across threads or requests.
- Never introduce global mutable state.
- Do not modify background loop behavior without approval.

## 6. Testing Requirements Before Merge
- New business logic requires tests.
- Changes that affect invariants require test coverage.
- Schema changes require migration validation instructions.
- Do not remove existing tests without approval.

## 7. Documentation Discipline
- Any architectural change must update:
- `docs/architecture/SYSTEM_SPEC.md` (if structural) _(pre-split governance doc; not migrated)_.
- `docs/platform/governance/INVARIANTS.md` (if enforcement changes) _(runtime half; app-domain invariants in aindy-apps-monolith)_.
- `docs/architecture/DATA_MODEL_MAP.md` (if schema changes) _(runtime-scoped; app-domain tables tracked in aindy-apps-monolith)_.
- Documentation must reflect actual implementation, not intended behavior.
- Update the `Last updated` date in `docs/GOVERNANCE_INDEX.md` whenever any file under `docs/` changes _(pre-split governance doc; not migrated)_.

## 8. Proposal-First Rule

For any of the following, a proposal must be written and approved before implementation:
- Large refactors.
- Schema redesign.
- Runtime behavior changes.
- Cross-layer boundary changes.

The proposal must include:
- A structured change plan.
- Impact analysis on invariants in `docs/platform/governance/INVARIANTS.md` _(runtime half; app-domain invariants in aindy-apps-monolith)_.
- Migration and API contract implications.

## 9. Non-Goals

AI agents must not:
- Optimize prematurely.
- Replace libraries without clear justification and approval.
- Introduce new frameworks.
- Rewrite working subsystems.
