---
title: "Architecture Risk Review"
last_verified: "2026-06-03"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Architecture Risk Review

A point-in-time risk map for `aindy-runtime`. Metrics: git commit frequency (last 6 months), static line count, function count, and reverse-import fan-in count.

Intended audience: runtime maintainers, release owners, refactor planners.

---

## Top 5 by Complexity and Change Risk

These modules combine high implementation complexity with frequent recent changes —
the combination most likely to introduce regressions.

| Rank | Module | Lines | Fns | Changes (6 mo) | Risk note |
|---|---|---|---|---|---|
| 1 | `platform_layer/health_service.py` | 924 | 24 | 12 | Highest change velocity; drives `/health` and `/ready` — any bug silences liveness or masks readiness failures |
| 2 | `platform_layer/registry.py` | 1875 | 130 | 9 | Largest function surface in the codebase; bridges startup hooks, extension registration, and runtime dispatch; changes here affect every registered capability |
| 3 | `platform_layer/deployment_contract.py` | 1196 | 41 | 9 | Controls deployment-profile resolution and infrastructure requirements; incorrect profile logic silently changes what startup guards are enforced |
| 4 | `platform_layer/sandbox_runner.py` | 2179 | 75 | 8 | Largest file by line count; handles sandbox tier selection, subprocess isolation, and attestation; mistakes here affect extension execution isolation claims |
| 5 | `startup.py` | 1541 | 37 | 5 | Orchestrates all 15 startup phases; imports 43 distinct AINDY modules; any unhandled exception in a non-degraded phase blocks the server from serving |

---

## Top 5 by Operational Blast Radius

These modules, if broken or degraded, affect the largest portion of serving
behavior at runtime.

| Rank | Module | Fan-in (importers) | Blast note |
|---|---|---|---|
| 1 | `db/database.py` | 88 | SessionLocal and the SQLAlchemy engine are global singletons imported by 88 modules; a pool misconfiguration or import-time engine failure breaks every DB-dependent route and background job simultaneously |
| 2 | `config.py` | 40 | Pydantic settings loaded at module import time; any missing required env var raises at import, which cascades through 40 dependent modules before a single route handler runs |
| 3 | `platform_layer/registry.py` | 36 | All extension hooks, startup callbacks, and plugin dispatch go through the registry; a crash here at startup disables every registered capability |
| 4 | `kernel/syscall_dispatcher.py` | runtime hub | Low static importer count, but every capability-gated call at runtime routes through it; a dispatcher bug degrades all syscall execution across agents, flows, memory, and events simultaneously |
| 5 | `startup.py` | 43 deps | Not imported by many modules, but it owns the boot sequence; a fatal phase failure before `startup_complete=True` means zero serving capacity — the process runs but all routes return errors or the server never becomes ready |

---

## Bootstrap, Configuration, and Lifecycle Coupling

### Coupling-1: `startup.py` imports 43 AINDY modules

`startup.py` is the broadest import consumer in the repo. Most imports are inside
function bodies (lazy per phase), which is the correct pattern. The risk is that
any of those 43 modules can introduce a module-level side effect that runs at
startup import time rather than at phase execution time, making it invisible
until a specific startup ordering is triggered.

**Observable symptom:** Silent startup failures or unexpected phase ordering when
a newly added module-level import in any of the 43 sources pulls in a heavy or
IO-bound dependency chain.

**Current mitigation:** Phase function imports are inside `try` blocks; individual
phase failures log and propagate rather than silently swallowing. The CLI import
chain hazard (documented in CLAUDE.md) is the clearest active instance.

**Remaining gap:** No systematic test asserts that startup phases remain lazy
(i.e., that importing `AINDY.startup` itself does not eagerly import the full
dependency chain).

---

### Coupling-2: `db/database.py` has 88 static importers

`SessionLocal` and the SQLAlchemy engine are created at module-load time when
`DATABASE_URL` is read. With 88 static importers, any module import that
transitively reaches `db.database` will attempt to create the engine — which
fails loudly if `DATABASE_URL` is unset.

This is why `AINDY/runtime_only.py` uses `__getattr__` lazy loading (documented
in CLAUDE.md). The hazard extends to any new module that imports from `db.*` at
module scope without recognizing the engine-creation side effect.

**Observable symptom:** `CLI --help` or `sandbox` subcommand crashes with
`sqlalchemy.exc.ArgumentError` when `DATABASE_URL` is unset, even though no DB
operation was requested.

**Remaining gap:** No import-safety test for the 88-importer graph. TECH_DEBT
CLI-1 (lazy settings getter) is the closest registered item.

---

### Coupling-3: `config.py` has 40 static importers

`AINDY.config` creates a `Settings` instance at module level via Pydantic. Any
module that imports from `config` must be present in an environment where the
required env vars are set, or the import raises `ValidationError`.

Combined with the 88-importer `db.database` graph, this means a significant
fraction of the codebase cannot be imported in a test environment without either
mocking env vars or relying on `is_testing` overrides.

**Remaining gap:** Test isolation depends on env var mocking discipline rather
than a structural boundary. Drift in `REQUIRED_*` fields in `Settings` can break
tests silently if the test runner provides a superset of required vars.

---

### Coupling-4: `platform_layer/registry.py` mixes startup orchestration with runtime dispatch

`registry.py` (1,875 lines, 130 functions) serves two roles:
- **Startup:** accepts `register_*` calls from bootstrap modules; builds the
  extension inventory; runs startup hooks.
- **Runtime:** dispatches to registered callbacks (trigger evaluators, planner
  backends, tool providers, completion hooks) on every agent run.

These two roles share state (the registered callback tables) but have different
failure modes and testing requirements. A startup-phase test that registers a
mock callback affects the runtime dispatch tables for the rest of the test
session unless explicitly torn down.

**Observable symptom:** Test-order sensitivity in any test that calls `registry`
registration helpers without cleanup.

**Remaining gap:** No registry teardown fixture; integration between startup
registration and runtime dispatch is only testable end-to-end. Related: the
subprocess isolation hazard (`_maybe_wrap_runtime_callback`) documented in
CLAUDE.md is a downstream symptom of this coupling.

---

### Coupling-5: `platform_layer/health_service.py` imports `db.schema_contract` at module level

`health_service.py` line 48 imports `AINDY.db.schema_contract` at module scope.
This makes it unsafe to import in the CLI context without `DATABASE_URL`, because
`db.schema_contract` transitively reaches `db.database`. This is documented in
CLAUDE.md and is an active known risk for the CLI import chain.

**Current mitigation:** The CLI guard in `runtime_only.py` wraps health service
imports in `try/except`. Any new addition to `_run_sandbox_check()` that reaches
`health_service` must add the same guard.

**Remaining gap:** The module-level import is load-bearing for the health module
itself; fixing it requires moving the schema version check inside a function body.
This is a clean fix but has not been prioritized.

---

## Review Notes

- Metrics collected 2026-06-03; git log covers the prior 6 months.
- Fan-in counts reflect static `from X import` and `import X` references; dynamic
  imports inside function bodies are not counted.
- Complexity and change-risk rankings are independent; a module ranks high on this
  list only when both are elevated.
- These findings are input for backlog prioritization, not a directive to refactor
  immediately. Phase 2 hardening work should consult this map when scheduling
  test coverage expansion for scheduler, wait/resume, and syscall dispatch.
