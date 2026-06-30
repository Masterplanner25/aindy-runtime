---
title: "Nodus Workflow Registration Contract (RTR-1)"
api_version: "1.0"
last_verified: "2026-06-29"
status: current
owner: "platform-team"
---
# Nodus Workflow Registration Contract (RTR-1)

> **Status: design + contract (RTR-1, Phase 1 scope).** This document is the
> reviewed design for `register_nodus_workflow` — the registration surface that
> lets apps register and select Nodus workflows **by name, at boot, without
> editing runtime**. It is the keystone of `TECH_DEBT.md` RTR-1. Implementation
> follows in a separate PR against this contract. Claims about current code were
> verified against the live tree on 2026-06-29; cited as `file:symbol`.

---

## 1. Goal and non-goals

**Goal (Phase 1).** Close the gap that forces "runtime-gated" verdicts: an app
can ship a Nodus workflow and register/select it through a first-class surface,
the same way it registers tools, syscalls, models, flows, and trigger
evaluators today — never by editing runtime code.

**Non-goals (Phase 1).**
- Agent-plan → `.nd` compilation and the VM-backed agent adapter that retires the
  static `AGENT_FLOW` shim — that is **RTR-1 Phase 2** and *consumes* this surface.
- Fine-grained `NodusTraceEvent` population — **Phase 3** (today a dead write path;
  see §9).

## 2. Architectural lens

The runtime is "kernel primitives + registration surfaces; apps extend without
editing runtime" (`docs/runtime/DB_OWNERSHIP_CONTRACT.md`,
`docs/architecture/MODEL_OWNERSHIP_POLICY.md`). The **runtime owns the mechanism
/ primitive / registration surface**; the **app owns the workflow content**
(`.nd` source, which workflow to select). This surface is therefore squarely
runtime-owned, and it must mirror the existing canonical extension pattern rather
than introduce a fourth parallel mechanism (§8).

**Canonical pattern it mirrors** (verified): a plugin manifest
(`aindy_plugins.json`) lists modules; `load_plugins()`
(`AINDY/platform_layer/registry.py:1717`) imports each and calls its top-level
`bootstrap()`, which calls `registry.register_*(...)` gated by an in-process
capability keyed on the module's `owner_class`. Reference implementation:
`AINDY/platform_layer/runtime_agent_defaults.py:register()`.

## 3. The central constraint and the design pivot

`compile_nodus_flow(script, flow_name)`
(`AINDY/runtime/nodus_flow_compiler.py:207`) turns a Nodus `flow.step()` routing
script into a `PersistentFlowRunner` flow dict, but its conditional edges are
**in-memory Python closures** (`_condition_truthy` / `_condition_falsy`) that the
module docstring explicitly states "are **not** serialised to the database." This
is the exact wall that limits `register_dynamic_flow`
(`AINDY/runtime/flow_registry.py:133`) to data-only string edges.

**Pivot — persist source, recompile on load.** The durable, versioned artifact is
the **`.nd` source**, never the compiled flow dict. On registration and on every
boot, the runtime **(re)compiles source → `FLOW_REGISTRY`**; closures live only in
memory and are deterministically reconstructed from source. Source is the source
of truth; the compiled flow (and any `.nbc` bytecode) is ephemeral cache keyed on
content hash. This sidesteps the serialization wall and preserves full Nodus
expressiveness.

## 4. The registration surface

Two entry points, both landing in the same `register_nodus_workflow()` — the same
imperative+declarative dual precedent `register_dynamic_flow` already has.

### 4.1 Imperative (from an app `bootstrap()`)

New function in `AINDY/platform_layer/registry.py`, mirroring
`register_run_tool_provider` / `register_flow`:

```python
def register_nodus_workflow(
    name: str,
    source: str,                  # .nd source text — the durable artifact
    *,
    kind: str = "flow-graph",     # "flow-graph" | "script"  (both supported, §5)
    version: str | None = None,   # app-supplied label; else short content hash
    capabilities: list[str] = (), # capability scope the workflow may use
    owner_class: str = OWNER_EXTERNAL_THIRD_PARTY,
    provenance: dict | None = None,
    overwrite: bool = False,
    db: Session | None = None,    # None at the boot loader; Session to persist
) -> dict: ...
```

Behavior:
1. `validate_nodus_workflow(...)` — new validator in
   `AINDY/platform_layer/registry_contracts.py` (mirrors `validate_startup_hook`
   et al.): name charset/length (reuse the `register_dynamic_flow` rule),
   `kind ∈ {"flow-graph","script"}`, non-empty source, capability list shape.
2. `_require_in_process_extension_capability(INPROC_CAP_REGISTER_NODUS_WORKFLOW)`
   — new constant added to the `INPROC_CAP_*` block and
   `_ALL_INPROC_EXTENSION_CAPABILITIES` (`registry.py:156-195`). Runtime-built-in
   and first-party-app get it; external-third-party is audited/denied like every
   other in-process surface.
3. `derive_structured_extension_provenance(...)` — same machinery
   `register_dynamic_flow` uses (`SOURCE_DATA_REGISTRATION`, owner_class).
4. Compile (kind-dependent, §5) → `register_flow(name, flow_dict)` into
   `FLOW_REGISTRY`.
5. If `db` is provided, upsert the **source row** into `nodus_workflows` (§6) —
   never the closure-bearing flow dict.
6. Return metadata `{name, kind, version, content_hash, abi_surface,
   owner_class, provenance, capabilities, dynamic: True}`.

### 4.2 Declarative (manifest extension kind)

A new `nodus-workflow` kind alongside the existing `dynamic-node` /
`webhook-subscription` / `dynamic-flow` kinds that `load_plugins` already
dispatches (`registry.py:1654-1688`):

```json
{ "kind": "nodus-workflow",
  "name": "weekly_digest",
  "source_path": "apps/reports/flows/weekly_digest.nd",
  "kind_hint": "flow-graph",
  "owner_class": "first-party-app",
  "capabilities": ["memory.read"] }
```

`_load_manifest_declarative_extensions` reads `source_path`, then calls
`register_nodus_workflow(..., db=None)` at boot (DB persistence for declarative
entries is the manifest itself — they re-register every boot from the `.nd` file).

## 5. Workflow kinds (both supported in Phase 1)

| `kind` | Source shape | Compilation | Use |
|---|---|---|---|
| `flow-graph` | A `flow.step("node", when="key")` routing script | `compile_nodus_flow()` → multi-node flow dict over **pre-registered** `NODE_REGISTRY` nodes (conditions = in-memory closures) | Conditional, multi-node orchestration that wires existing nodes |
| `script` | One arbitrary `.nodus` program | Wrapped as a single-node flow whose one node is `nodus.execute` running the source in the VM | Self-contained Nodus logic; the on-ramp for arbitrary app workflows |

Both register identically through one surface and run via the canonical
`PersistentFlowRunner` path; only the compile step differs. `flow-graph` requires
its referenced nodes to exist in `NODE_REGISTRY` at execution time.

> **Implementation note (RTR-1a).** Phase 1 ships both kinds, but `compile_nodus_flow`'s
> `flow.step(...)` DSL collides with nodus-lang 4.0.5's reserved `step` keyword
> (`flow.step` no longer parses) — a pre-existing, previously-untested bug. The
> **`script`** kind is the fully-working path today; **`flow-graph`** compilation
> is blocked until the DSL is reconciled with nodus-lang 4.x (tracked as RTR-1a in
> `TECH_DEBT.md`). The registration surface is agnostic to the DSL, so flow-graph
> works unchanged once the compiler is fixed — no surface change needed.

## 6. Storage and versioning — `nodus_workflows` table

A new runtime ORM model under `AINDY/db/models/nodus_workflow.py`, mirroring
`dynamic_flows` but storing **source, not the flow dict**:

| column | type | note |
|---|---|---|
| `id` | UUID PK | `default=uuid.uuid4` |
| `name` | String, unique, indexed | selection key |
| `is_active` | Boolean | soft-delete / supersede |
| `source` | Text | the `.nd` artifact — durable truth |
| `kind` | String(16) | `flow-graph` \| `script` |
| `version` | String | app label or short content hash |
| `content_hash` | String(64), indexed | SHA-256 of source; cache + version key |
| `capabilities` | JSONB | declared capability scope |
| `owner_class` | String | extension owner class |
| `provenance` | JSONB | `derive_structured_extension_provenance` output |
| `created_by` | String, nullable | audit |
| `created_at` / `updated_at` | DateTime(tz) | audit |

This is a change under `AINDY/db/models/` → **schema-contract protocol applies**
(CLAUDE.md): bump `SCHEMA_CONTRACT_VERSION`, regenerate baseline, update the two
`test_runtime_schema_contract.py` assertions. Versioning that `nodus_script_store`
lacks comes free: a new version is a new `content_hash` row; `is_active` selects
the live one. A future runtime Alembic migration (`0006_*`) adds the table; blank-DB
bootstrap is automatic from ORM metadata.

## 7. Boot rehydration

A new startup step (next to the `dynamic_flow` load and
`core/flow_run_rehydration.rehydrate_waiting_flow_runs`): load active
`nodus_workflows` rows and **recompile each into `FLOW_REGISTRY`**. Deterministic,
source-driven, closures rebuilt — no serialized-closure problem. Runtime-only boot
finds zero rows → no-op; app-profile boot rehydrates app-owned workflows. Failures
are logged per-workflow and never abort boot (mirrors the dynamic-flow loader).

## 8. Execution path (reuses the canonical pipeline)

Selecting a registered workflow by name runs it through the **existing** path —
no new execution machinery:

```
select by name → FLOW_REGISTRY[name] → PersistentFlowRunner
  → nodus.execute node(s) → FlowRun + SystemEvent(source="nodus") on trace_id
```

Live Nodus VM runs already participate in the canonical `FlowRun` / `SystemEvent`
pipeline (verified) — this surface only adds the *registration + selection* layer
in front of it. Agent-plan → workflow (Phase 2) becomes: compile the plan to an
`.nd`, `register_nodus_workflow(..., kind="script")`, run by name.

## 9. Consolidation — no fourth mechanism

This surface **subsumes** today's three half-mechanisms:

- `nodus_script_store` (`AINDY/platform_layer/nodus_script_store.py`) — name-keyed
  source blobs, **no versioning** → folded into `nodus_workflows` (versioned).
  `POST /platform/nodus` upload becomes a thin wrapper / deprecated alias.
- `compile_nodus_flow` (`nodus_flow_compiler.py`) — becomes the `flow-graph`
  compile step (unchanged, now invoked through the surface).
- `register_dynamic_flow` (`flow_registry.py`) — **kept** for pure data-only DAGs
  of pre-registered nodes; Nodus workflows are the superset that adds conditional
  routing via source. The two coexist; the doc cross-links them.

**Trace path:** `NodusTraceEvent` (`AINDY/db/models/nodus_trace_event.py`) + its
reader + `GET /platform/nodus/trace/{trace_id}` exist but the writer
`_flush_nodus_traces()` has no call sites — a dead path. Phase 3 decides
**wire-or-drop**; Phase 1 leaves it untouched (workflow runs already emit canonical
`SystemEvent`s, so observability is not blocked).

## 10. Capability, ownership, provenance

Identical to `register_dynamic_flow`: `owner_class` validated via
`validate_extension_owner_class`; provenance via
`derive_structured_extension_provenance`; in-process capability via the new
`INPROC_CAP_REGISTER_NODUS_WORKFLOW`. The workflow's own `capabilities` list is
recorded for downstream enforcement when nodes dispatch syscalls (the dispatcher
already enforces `SyscallContext.capabilities`).

## 11. Phasing

- **Phase 1 (next PR, against this contract):** `register_nodus_workflow`
  (imperative + declarative), `nodus_workflows` table + schema-contract bump +
  Alembic `0006`, boot rehydration, run-by-name, both kinds, capability/provenance
  gating, tests, this contract finalized. **No agent changes.**
- **Phase 2:** agent-plan → `.nd` mapping + VM-backed agent adapter; retire the
  `AGENT_FLOW` shim.
- **Phase 3:** managed bytecode cache keyed on `content_hash` (replaces the stale
  path+mtime `.nbc`); wire-or-drop `NodusTraceEvent`; version rollback API.

## 12. Test plan (Phase 1)

- Unit: `register_nodus_workflow` happy path (both kinds); duplicate-without-overwrite
  rejection; owner-class denial for external-third-party; validator rejects bad
  `kind` / empty source / bad name; content-hash + version derivation.
- Persistence: upsert into `nodus_workflows`; boot rehydration recompiles source
  into `FLOW_REGISTRY` and the workflow runs by name (real Postgres in the
  integration tier).
- Manifest: a declarative `nodus-workflow` entry registers at `load_plugins` time
  (mirror `test_extension_ownership.py`).
- Execution: registered `script` workflow runs via `nodus.execute` and emits
  canonical `FlowRun` + `SystemEvent(source="nodus")`.

## 13. Implementation file-touch list

| File | Change |
|---|---|
| `AINDY/platform_layer/registry.py` | `register_nodus_workflow`, `INPROC_CAP_REGISTER_NODUS_WORKFLOW`, manifest `nodus-workflow` dispatch in `_load_manifest_declarative_extensions` |
| `AINDY/platform_layer/registry_contracts.py` | `validate_nodus_workflow` |
| `AINDY/db/models/nodus_workflow.py` | new `NodusWorkflow` ORM model |
| `AINDY/db/schema_contract.py` + baseline + `test_runtime_schema_contract.py` | schema-contract bump |
| `alembic/versions/0006_nodus_workflows.py` | additive table migration (idempotent, table-existence-guarded) |
| `AINDY/startup.py` | boot rehydration step |
| `AINDY/platform_layer/nodus_script_store.py` + `POST /platform/nodus` | deprecate/wrap onto the new surface |
| `tests/unit/`, `tests/integration/` | per §12 |

---

**Related:** `TECH_DEBT.md` RTR-1 · `DB_OWNERSHIP_CONTRACT.md` ·
`NODUS_DEVELOPER_GUIDE.md` · `SYSCALL_REFERENCE.md`.
