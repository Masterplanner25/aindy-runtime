---
title: "Model Ownership Policy"
last_verified: "2026-08-05"
api_version: "1.0"
status: current
owner: "platform-team"
---

# Model Ownership Policy

> **Post-split note (2026-06-27):** Relocated into `aindy-runtime` from the
> pre-split monolith archive. The `AINDY/...` paths are runtime-owned in this
> repo; `apps/...` paths are illustrative of the app-owned tree, which now
> lives in the **aindy-apps-monolith** repo. The ownership rule below is
> repo-agnostic and remains normative.

## Rule

A SQLAlchemy model belongs in AINDY/db/models/ if and only if it meets
ALL of the following criteria:

1. It represents a concept that is platform-wide, not domain-specific.
2. It is imported or referenced by more than one domain app, OR it is
   referenced directly by AINDY/ platform code (pipeline, startup,
   scheduler, auth).
3. It does not encode business logic belonging to a single domain.

> **One exception, and it is deliberate.** The Memory Bridge models
> (`MemoryNodeModel` / `memory_nodes`, `MemoryLinkModel` / `memory_links`) are
> runtime-owned but live in `AINDY/memory/memory_persistence.py`, not
> `AINDY/db/models/`. Read "belongs in `AINDY/db/models/`" as "is runtime-owned" — the
> directory is the normal home, not the definition. The schema-contract hash covers
> both locations (`MODEL_ROOT` **and** `MEMORY_PERSISTENCE_PATH` in
> `scripts/check_schema_version.py`), so editing either one triggers the protocol below.
> Note the asymmetry this creates. `memory_nodes` and `memory_links` are **excluded from
> the Alembic autogenerate allowlist** (`_RUNTIME_TABLES` in `alembic/env.py`, 33 of the
> 36 runtime tables) — `env.py` does not import the memory model, and registers only a
> one-column stub `memory_nodes` so foreign keys pointing at it still resolve. Those two
> tables are create_all-managed through the schema contract instead. The other memory
> tables (`memory_traces`, `memory_node_history`, `memory_metrics`, `memory_trace_nodes`)
> live under `AINDY/db/models/` and *are* tracked normally. This split is deliberate, not
> an oversight — do not "fix" it by adding them to the allowlist without also giving
> `env.py` a real import.

Examples of platform models: User, FlowRun, ExecutionUnit, SystemEvent,
MemoryTrace, PlatformAPIKey, AgentRun, AgentStep,
AgentEvent, AgentTrustSettings.

Examples of domain models (must live in apps/X/models.py):
LearningRecord, WatcherSignal, AutonomyDecision.

Agent route handlers, flows, syscalls, and tool registration remain
domain behavior under `apps/agent/`, but the persistence types
`AgentRun`, `AgentStep`, `AgentEvent`, and `AgentTrustSettings` are
runtime-owned because they are referenced directly by runtime execution,
recovery, observability, and capability enforcement code in `AINDY/`.

## Adding a new model

If your model is domain-specific, add it to `apps/your_app/models.py` (or
`apps/your_app/models/`) and register it from your app's `bootstrap.py` via
`register_models()`. Nothing in this repo needs to change — that is the point of the
boundary.

If you believe a model is truly platform-owned, add it to
AINDY/db/models/ and get a second review confirming it meets all three
criteria above.

### The schema-contract protocol is mandatory and CI-enforced

Any change under `AINDY/db/models/` or to `AINDY/memory/memory_persistence.py` —
**including a comment or a new `info=` entry, because `orm_hash` is a content hash of
those files, not a schema diff** — requires three follow-up steps, in order, or CI fails:

1. Bump `SCHEMA_CONTRACT_VERSION` in `AINDY/db/schema_contract.py`
   (`"YYYY-MM-DD"`, then `"YYYY-MM-DD.1"`, `.2`, … for repeats on one date).
2. Regenerate the baseline: `python scripts/check_schema_version.py`
   (exit 0 is the pass condition).
3. Update the two hardcoded version assertions in
   `tests/unit/test_runtime_schema_contract.py` (grep `schema_contract_version`).

A version bump with no DDL behind it is normal and expected — say so in the commit
message so downstream consumers who assert on the contract version know a schema diff
will show nothing.

If the change adds an Alembic revision, also bump `RUNTIME_ALEMBIC_HEAD_REVISION` in
`AINDY/db/alembic_head.py`. The `alembic/` scripts directory is **not shipped in the
wheel**, so `aindy-runtime bootstrap-schema` reads the head from that constant rather
than from the scripts. `tests/unit/test_runtime_alembic_head.py` catches a forgotten bump.

### Adding a column to a model that already has rows

`server_default` governs rows written *afterwards*. It is not always the right value for
rows that already exist, and the two can differ in ways that matter — a NOT NULL security
flag defaulting to "off" silently marks every pre-existing row as un-privileged.

Because the `alembic/` tree is absent from the wheel, a migration's backfill **never runs
on a wheel install**; those deployments reconcile from packaged metadata instead. If
pre-existing rows need a different value, declare it on the column so both paths agree:

```python
is_verified = Column(
    Boolean, nullable=False, server_default="false",
    info={"reconcile_backfill": "true"},   # applied to rows that predate the column
)
```

`bootstrap-schema --reconcile` issues the `UPDATE` immediately after the `ADD COLUMN`.
This was FR-8; before it existed, wheel deployments silently diverged from source
checkouts on exactly this point.

## Migrating a model between ownership layers

If a model in `AINDY/db/models/` is later determined to be domain-specific,
or a model in `apps/X/models/` is later determined to be runtime-owned,
migrate ownership deliberately using the following process:

1. Move the full class definition to the new owner package.
   Preserve `__tablename__` exactly unless a schema migration is intentional.
2. Update model registration so Alembic and startup load the canonical owner.
3. Update all `AINDY/`, app, and test imports to depend on the new owner.
4. If compatibility is needed during the transition, leave a narrow shim in
   the old location that re-exports the canonical class.
5. Add or update CI enforcement so the old forbidden import direction fails. The
   runtime-side guard is `tests/unit/test_runtime_boundary.py`, which asserts runtime
   code never imports `apps.*` — it runs in `Runtime Contracts`, a required check.
6. If table shape is unchanged, record the ownership transfer without a schema
   migration. Only add Alembic changes when the database schema actually changes.

The agent persistence move back into `AINDY/db/models/` is the canonical
runtime-ownership reference.
