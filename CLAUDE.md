# CLAUDE.md

Guidance for Claude Code (claude.ai/code) in this repository. **This file is the authoritative
agent-instruction surface** and is kept under Claude Code's 40 KB limit; it holds rules, hooks and
pointers, never the narrative. Companions:

- **`docs/governance/AGENT_WORKING_RULES.md`** — what an agent may change without approval, what
  needs sign-off. Read it before a change whose blast radius you are unsure of.
- **`docs/governance/TRUSTING_A_GREEN_CHECK.md`** — the 15-variant catalogue of checks that looked
  green and were not, plus the vendored-shim and test-mode-short-circuit rules. **Read before citing
  CI as evidence.**
- **`TECH_DEBT.md`** — the full record behind every registry id below (1 MB; consistently 2–5×
  longer than the line here). `docs/governance/DECISION_LOG.md` — the `DEC-NNN` record.
- **`docs/archive/CLAUDE_md_2026-09-16_pre_trim.md`** — this file before it was trimmed from
  153 KB (2026-09-16). Every rule below that reads terse had a paragraph of evidence there.
- **`CODEX.md`** is a pointer here, not a copy — do not reintroduce content there.

---

## Commands

```bash
pip install -e ".[test]"
pytest tests/unit/ -v                       # unit — SQLite in-memory, no services
pytest -m runtime_only -q                   # what CI's Runtime Contracts runs (NOT tests/unit/)
pytest -m sandbox_escape -v                 # needs Docker (Linux containers), no DB
pytest -c pytest.integration.ini -v         # needs live Postgres + Redis
docker compose -f docker-compose.test.yml up -d
ruff check AINDY tests --config AINDY/ruff.toml   # exactly what CI's Runtime Lint runs
# Do NOT run `ruff format` — the tree was never formatted; 457/559 files would change (LINT-FORMAT-1)
python scripts/check_schema_version.py      # regenerate schema baseline after any model change
aindy-runtime serve                         # needs DATABASE_URL + SECRET_KEY
docker compose --profile full --profile monitoring up -d
```

Release-state facts (newest tag, `AINDY/_version.py`, the Dockerfile pin, `## Unreleased`) are
deliberately not written here — read them from the tree. **"On `main`" and "released" are
different claims: `git tag --contains <commit>` before telling anyone a fix shipped.**

---

## Architecture

Orientation only — the tagged per-file inventory is `docs/runtime/RUNTIME_MODULE_MAP.md`.

| `AINDY/` | Holds |
|---|---|
| `kernel/` | SyscallDispatcher/Registry, EventBus, SchedulerEngine, CircuitBreaker, ResourceManager, TenantContext |
| `platform_layer/` | LLM clients, metrics, OTel, rate limiter, extension ABI + sandbox runner, scheduler_service |
| `core/` | ExecutionPipeline middleware, RetryPolicy, DistributedQueue, SystemEventService, ResumeWatchdog |
| `runtime/` | Flow engine (DAG executor), Nodus script execution, memory loop |
| `agents/` | Agent runtime, planner backends, tool registry, AgentCoordinator, AutonomousController |
| `memory/` | MemoryNode persistence, MemoryAddressSpace, embedding pipeline, scoring; optional Rust scorer |
| `db/` | SQLAlchemy models, Alembic env, DAO layer, schema contract |
| `routes/` | FastAPI routers — auth, flows, agents, memory, platform/* |
| `worker/`, `nodus/` | Background workers; Nodus stdlib (`memory.nd`) + runtime adapter |

- **Pipeline:** every route runs inside `ExecutionPipeline` (`core/execution_pipeline/pipeline.py`):
  ContextVars, claim/release an `ExecutionUnit`, metrics, memory-signal capture, `SystemEvent`s.
  Handlers reach the kernel only via `SyscallDispatcher.dispatch()`.
- **Syscalls (`sys.v1.domain.action`):** `kernel/syscall_dispatcher.py` is the single entry:
  registry check, capabilities, tenant isolation, quota, schema validation, idempotency gate for
  `EXACTLY_ONCE` (EffectRecord), OTel span, uniform `{status, data, trace_id, duration_ms, error}`.
- **Flow WAIT/RESUME:** `FlowRun` is `pending → executing → waiting → completed/failed`. A WAIT is
  triggered by `execution_gate.py`'s `{"status": "WAIT", "wait_for": …}` or the guest's
  `nodus_wait_requested` state flag — **there is no `sys.v1.event.wait` syscall** (23 in the
  registry; `event.emit` is the only `event.*`). The bus broadcasts by `run_id`, never a payload
  (DEC-013); `flow_run_rehydration.py` re-registers `waiting` rows on restart.
- **Nodus:** `runtime/nodus_worker.py` runs `.nd` scripts via `nodus-lang`; memory writes are
  deferred and committed after the script. The guest wait is `await_event(event, schema)` (DEC-017).
- **Agents:** `agents/agent_runtime/execution.py::execute_run` — requires `status == "approved"`,
  validates the scoped capability token, resolves tools, then `execute_agent_run_via_nodus()`.
- **Memory:** `memory_nodes` (pgvector `Vector(1536)`); writes via `memory_ingest_service.py` →
  embedding queue → worker; hybrid retrieval (vector + tags + MAS path queries).

---

## Schema contract version protocol

Any change under `AINDY/db/models/` or to `AINDY/memory/memory_persistence.py` (content-hashed):
1. bump `SCHEMA_CONTRACT_VERSION` in `AINDY/db/schema_contract.py` (`"YYYY-MM-DD"`, then `.1`, `.2` same day);
2. `python scripts/check_schema_version.py` → "Schema version baseline updated.";
3. update the two version-string assertions in `tests/unit/test_runtime_schema_contract.py`.

**Never put a vocabulary (string sets) under `db/models/`** — a no-DDL change then costs a schema
bump, so the list rots instead (AGENT-EVENT-VOCAB-1).

## Alembic conventions

- Idempotent: `IF NOT EXISTS` / `IF EXISTS` everywhere; `downgrade()` drops what `upgrade()` made.
- Table is `alembic_version_runtime`; naming `NNNN_short_description.py`.
- **Head is `RUNTIME_ALEMBIC_HEAD_REVISION` in `AINDY/db/alembic_head.py`** — bump it with every new
  migration (`bootstrap-schema` stamps from it; `alembic/` is not in the wheel). CI-enforced by
  `tests/unit/test_runtime_alembic_head.py`. This file never lists the chain; it went stale twice.
- **Blank-DB safety (ALEMBIC-FRESH-DB-1):** compose runs `alembic upgrade head` before `create_all`.
  Any DML or `CREATE INDEX ON <table>` must sit inside a `DO $$ … IF EXISTS (SELECT 1 FROM
  pg_catalog.pg_tables WHERE tablename='t' AND schemaname='public') THEN … END IF; END $$` guard.
  `IF NOT EXISTS` on the index name alone still raises `UndefinedTable`.
- `memory_nodes` is runtime-owned but create_all-managed, NOT alembic-tracked — deliberate.

## Decisions + changelog protocols — in the PR that acts on it

- **Decisions:** a choice made in conversation (declined option, chosen shape, deferral) is
  `DEC-NNN` in `docs/governance/DECISION_LOG.md` in the same PR (DEC-010); entries cite the id;
  `test_decision_log_integrity.py` fails on a cited id that does not exist.
- **Changelog:** a PR that changes behaviour, API, config, schema, or **what a green check means**
  writes `changelog.d/<PR>-<slug>.md` (prefix `00-` if an operator must read it before upgrading).
  **Never hand-edit `## Unreleased`** (three collisions in one afternoon silently reverted an entry);
  `scripts/assemble_changelog.py` folds them at release. Format `### Added|Changed|Fixed|Removed —
  title (#PR)` + bullets saying *why it was wrong*. Never rewrite a published entry — correct it
  with a new dated one. Doc-only edits and pure refactors need none; when unsure, write it.

---

## Scheduler job pattern (`scheduler_service.py`)

Reference: `_cleanup_stale_logs`. All imports and `SessionLocal()` **inside** the `try`;
`db.commit()` + `db.close()` before the `except`; `logger.error` for fatal, `warning` for
recoverable. Tests patch at the source: `patch("AINDY.db.database.SessionLocal", …)` — patching
`scheduler_service.SessionLocal` raises `AttributeError`.

## EffectRecord rules

`_resolve_effect_record` / `_complete_effect_record` use `db.commit()`, not `flush()` — durability
across session close is load-bearing. Pending rows are never TTL-deleted. Status: `pending |
success | failed | partial | unknown` (`pending` is REFUSED as a completion — see
EFFECT-OUTCOME-UNKNOWN-1); resolved at ONE point, `kernel/syscall_outcome.py`.

## Agent approve path

`approve_run()` (`agents/agent_runtime/approvals.py`) is an atomic CAS from `pending_approval`
only; `execute_run` runs on a daemon thread. A crash before its first commit strands the run in
`approved`; `_recover_orphaned_approved_runs` (every 5 min, threshold 10 min, **lease-fenced** —
LEASE-FENCE-1) re-dispatches. **Do not add a second CAS in `execute_run`** (DEC-033). The path
bypasses `SyscallDispatcher` — no EffectRecord idempotency. Patch `AINDY.agents.agent_runtime.execute_run`
(the re-export), and `approvals.mint_token` / `approvals.record_agent_event` directly.

## Admin bootstrap — grant-only

`AINDY_BOOTSTRAP_ADMIN_EMAIL` and `aindy-runtime auth promote-admin <email>` only ever set
`is_admin=True`. **First-registered-user-gets-admin is forbidden under any framing** — register is
public, so it is a privilege-escalation race. Flow: register (neutral `202`, no token; verify-email
issues it) → promote. `_bootstrap_admin_email()` is startup Phase 5.5, idempotent.

## Platform UI (SPA under `platform/`, served at `/platform`)

- `_SPAStaticFiles` (`AINDY/routing.py`) falls back to `index.html` only when the path does NOT
  start with `assets/` — `/platform/assets/missing.js` must 404, never 200+HTML.
- `PlatformGuard` (`platform/src/PlatformApp.tsx`): `/login` stays outside the guard; redirect via
  `<Navigate to="/login" replace />` (never `window.location`); authenticated-but-not-admin renders
  `<NotAdmin />` (a `<Navigate>` there loops). `VITE_APP_BASE_URL` is removed — do not reintroduce
  it as a redirect target. `VITE_API_BASE_URL` defaults to `""` (relative); vite `server.proxy`
  covers local dev.
- **Docker installs the SPA prebuilt from the PyPI wheel** (`platform/dist/**` is package data);
  there is no node stage. A UI change reaches no container until a release is cut AND the Dockerfile
  pin bumped. Verify UI work with `npm run dev`; the container shows the last *released* UI.
- ui-kit: source `C:\dev\aindy-ui-kit\src\`, npm `@aindy/ui-kit`; the installed copy is a compiled
  bundle. Loop: build ui-kit → copy `dist/*` into `platform/node_modules/@aindy/ui-kit/dist/` →
  `npm run build` in `platform/` → restart `api`. `bootIdentity`, `loginUser`, `registerUser` must
  all `.then(unwrapEnvelope)` or the post-login redirect misfires.

## Import hazards

- **`AINDY.routes` shadow:** `from AINDY.routes import health_router` returns the `APIRouter`, not
  the module (same for every exported router). Import the function directly
  (`from AINDY.routes.health_router import _check_…`) or go through `sys.modules`.
- **`PLATFORM_ROUTERS`** carry bare prefixes and are mounted under `/platform`; `GET
  /platform/syscalls` lives on `platform_router.routes`, not in `PLATFORM_ROUTERS`.
- **`runtime_only.py`** lazy-loads `app` via module `__getattr__` so `--help` / `sandbox` never
  import `AINDY.db` (whose `create_engine(DATABASE_URL)` runs at import). Add nothing at module
  scope that reaches `AINDY.main` or `AINDY.db`; `AINDY.platform_layer.health_service` is known
  unsafe for `_run_sandbox_check()`.
- **`SyscallContractViolation`** (and any exception callers must catch) needs an explicit
  `except X: raise` **before** `dispatch()`'s broad `except Exception`.
- **FastAPI `_IncludedRouter`:** walk routes with `_iter_api_routes`; `app.routes` scanned for a
  prefix finds ZERO; router-level `dependencies` are invisible to a per-route `dependant` walk.

## `_maybe_wrap_runtime_callback` — subprocess isolation

Registered callbacks run in a subprocess (`runtime_callback_host.py` / `_worker.py`) with
`cwd=Path(__file__).resolve().parents[2]` — **read-only site-packages in a wheel**, so any
import-time relative file I/O fails; guard `mkdir` with `except PermissionError` and `FileHandler`
with `except OSError` (`config.py::_build_log_handler`). A failing subprocess collapses to
`_decision("defer", …)` and a 202 — silent permanent deferral. **Exceptions that run in-process**
(`_STATEFUL_IN_PROCESS_CALLBACK_SURFACES`): `run_tool_provider`, `planner_context`,
`agent_completion_hook` — they need live `TOOL_REGISTRY` / app state; completion hooks get `run_id`
and re-fetch, never a db handle.

## Tests — hazards that bite

- **`tests/unit/conftest.py` auto-marks `runtime_only`** and snapshots/restores runtime ContextVars
  per test (failing the leaker). **Outside `tests/unit/` nothing marks a file** and
  `pytest.integration.ini` reaches only `tests/integration` — a new test dir needs a job.
- **`pytest.mark.integration` skips the whole test when `DATABASE_URL` is not live Postgres** — the
  hook in `tests/integration/conftest.py` sees the whole session. Docker-only suites use
  `pytest.mark.sandbox_escape` alone; the Redis wire test is marked `redis`, not `integration`.
- **`pythonpath = . AINDY`** makes `import apscheduler` resolve to the shim in `AINDY/apscheduler/`
  for every test — anything the shim lacks is untested by construction (bit three times). **Grow the
  shim to the guard, never the reverse**; `test_apscheduler_shim_parity.py` derives the census.
  `AINDY/nodus/` shadows the installed `nodus` the same way (pinned to resolve installed).
- The shared `db_session` / `runtime_only_app` fixture puts app and test on ONE connection in ONE
  transaction: **a flush reads as a commit** (variant 15) — and the app's `rollback()` erases the
  test's own rows. A durability assertion, or a route that answers 4xx/5xx, runs on the private engine
  (`tests/fixtures/db.py::build_private_engine`) with a liveness control first.
- `ResourceManager.can_execute` is `(True, None)` under `settings.is_testing` (a pydantic property —
  patch it on the class); test-mode short-circuits above the real decision make the real path
  unreachable while green (`async_heavy_execution_enabled`, `get_queue` — put the guard BELOW the
  switch; assert the backend/branch you think you are exercising actually ran).
- **A route test must call the route** — the status code is the contract; source-text assertions are
  a supplement, never the coverage (prefer the AST if you must read source). A guard that iterates a
  collection must DERIVE it from source and assert non-empty. A test asserting an absence needs a
  liveness control. `caplog` cannot see a worker thread — assert on the Prometheus counter.
- Mutation-test a new suite (verify the mutation bites). Never pipe a flaky run through `tail`. A
  partial local sweep is not evidence — check host memory first (want > ~1500 MB free).
- A version read from an interpreter is cwd-sensitive — print `AINDY.__path__` beside it, in the
  container. A check read a minute after a push is the PREVIOUS commit's — compare
  `gh pr view --json headRefOid` with `git rev-parse HEAD` before merging.
- Module-import-time env reads are invisible to behavioural tests — grep the source when auditing
  env handling. MCP `call_tool()` never raises; check `result.isError`.

## Docs frontmatter

Every `*.md` under `docs/{runtime,operations,governance,upgrades,handoffs,design,tutorials}/` needs
`title`, `api_version`, `last_verified` (real, `>= 2026-05-17`), `status`, `owner` or
`Runtime Docs Validation` fails. `docs/archive/` is deliberately unchecked.

## Branch protection — `main`

Direct pushes blocked (`enforce_admins`). **All twelve checks required, `strict: true`** (rebase when
`main` moves; Integration Tests ~7 min sets merge latency — batch dependency bumps): Runtime Lint ·
Runtime Docs Validation · Runtime Contracts (`-m runtime_only` + schema + native) · Native Crate Build
· Integration Tests (PG+Redis) · Platform UI Build · Runtime Package Build · Install Smoke Test ·
pip-audit (OSV) · Boot Smoke (published wheel vs real PG) · Upgrade Path Guard · its Negative control
(required BESIDE the main job: with no schema change the main job passes trivially — variant 9).
**A thirteenth must have no `paths:` filter and no job-level `if:`** — a filtered required check
never reports and blocks every unrelated PR forever. `pip-audit` asks about the OUTSIDE world: read
the run date (`gh run list --workflow=… --branch …`) before citing it; it also runs on `push: main`.

## Registry conventions (`TECH_DEBT.md`)

Numbers are sequential per prefix and never reused (next: **FR-47**, **IDEM-15**). Closing an entry:
`Status: CLOSED (YYYY-MM-DD)` + what shipped and what remains. **Write findings in `TECH_DEBT.md`,
not here** — the registry below is one line per item, enforced by
`tests/unit/test_debt_registry_accuracy.py` (UTF-8 byte cap per entry; a closed entry may not sit
under `### Open`; the registry may not exceed 60% of this file). **Measure the whole file before and
after a trim** — #487 reported −15 KB while the file grew 18 KB. Provenance tags like *(Aider
research)* mean a comparative audit (`docs/governance/COMPARATIVE_RESEARCH_INDEX.md`) — absent
vocabulary, not broken wiring; read the index before acting on one.

**Phase (2026-08-20): runtime testing.** App-side FRs are the testing method, not scope creep.
**Soak happens HERE** (`tests/integration/soak_harness.py`, live PG+Redis on every PR) — it found
`EXACTLY_ONCE` is not exactly-once under contention. Capabilities ship default-off until evidence.
A soak assertion must not be stricter than the contract.

---

## TECH_DEBT.md — prefix registry

**An index, not the record. Read the `TECH_DEBT.md` entry before acting on any item.**

### Open — P0

- **FR-15** — dispatch is serialised; DISTRIBUTED half opt-in (#551–#556). ★ 2026-09-22 evidence step (1) OBTAINED on the dev-host topology (`docker-compose.fr15-evidence.yml`, DEC-072 — socket mount, never a profile): a resume crossed api→Redis→worker, DLQ flat; found losses #5 (a FOLLOWER api queues a woken resume nothing drains → forwarded) and #6 (worker never opened its scheduler). Open for a production stack + reconstruction-primary waits. Do NOT close on the opt-in.

### Open — P1

- **FLOW-PARALLEL-1** — `FanOutEdgeGroup(join=all|any|quorum)`; phases 0–3a shipped, open for phase 4 (default flip on evidence). A lenient join past a failed branch is the first `partial` emitter; width bound is process-wide (SYSMAX-5); a named predicate is in the graph signature. 3b declined (DEC-015). Design: `docs/design/FLOW_PARALLEL_DESIGN.md`.
- **AUTHORITY-NEGOTIATION-1** — phases 0–2 shipped on BOTH backends (nodus_vm half #734, design §9), default-OFF; open for the flip on evidence from both. It CANNOT grant authority — picks WHICH tool, `execute_tool` re-checks; the gate decides `skip | abort` (DEC-016). On nodus_vm the gate is a guest wait from `call_tool`; the CHAIN parks, the node never does. Design: `docs/design/AUTHORITY_NEGOTIATION_DESIGN.md`.
- **FS-SCOPE-1** — *(Aider)* path authority exists as `visibility.filesystem {mode, roots}` on `ExecutionEnvironmentSpec`, enforced on the guest path only. **The tool seam sets `cwd`, not a boundary** — enforcement needs the container runner. Never a second vocabulary beside `egress_scope`.
- **SUBSTRATE-WITNESS-1** — *(Claude Code)* ★★ LIVE CHANNEL 2026-09-22: Claw on **Telegram**, 3 turns → 3 `success` rows; the same `message_key` replayed (`message_id` identical, nothing sent), gate off → duplicate arrives. A refused duplicate a PERSON would have seen. WebChat cannot witness it (it streams; `deliver()` is the non-WebChat branch). Open for the SOAK + concurrency; the gate counter is unreadable from a live Claw. Never a synthetic fixture.
- **PERF-BASELINE-1** — *(Aider)* metric readback + concurrency half CLOSED via the soak harness; latency assertions still 1. Do NOT close with wall-clock thresholds on shared CI — COUNT WORK (both real regressions were query counts).

### Open — P2 and below

- **IDEM-13** — the TOOL seam's `EXACTLY_ONCE` had no strict mode: FR-27's lock was wired into `syscall_dispatcher` ONLY, so under contention EVERY concurrent caller ran (5 concurrent sends → 5 real messages; 8-way → 8 runs). ★ BUILT 2026-09-23: `AINDY_TOOL_IDEMPOTENCY_STRICT` (wait 60s) → 1 run / 7 replays / 0 degraded. Default OFF; open for the FLIP only.
- **EFFECT-OUTCOME-UNKNOWN-1** — `unknown` status shipped (#560) for a read timeout after a full write only; nothing emits it; `AT_MOST_ONCE` absent from the guarantee set. A claim about the WORLD — an unclassified exception is still `failed`.
- **SANDBOX-EVIDENCE-2** — the strong runner attests `mount_mode`/`network_policy` by reading its own argv; real evidence is the live `/proc` probe, deployment-time. `strong-sandbox-certified` is a deployment claim.
- **EFFECT-PRECONDITION-1** — *(Aider)* an effect cannot name the world-version it expects. Record the external system's OWN version token; never reimplement. After FS-SCOPE-1 or not at all.
- **EFFECT-MANIFEST-1** — *(Aider)* record-only: know the effect set before executing. Not before FS-SCOPE-1 + EFFECT-PARTIAL-1.
- **EMBEDDED-FLOOR-1** — *(Aider)* no profile below `single-instance`; a soak-and-declare gate, not a capability gap.
- **RETRY-CONTEXT-1** — *(GPT Engineer)* the classify half shipped (#703); the carry half is a SCOPE, never an argument (a failure folded into `args` un-dedups the retry). Closes on a first-party consumer.
- **PROGRESS-CHANNEL-1** — *(Codex)* no partial-output surface. If built: NO authority, NO effect, attaches to the trace, best-effort by contract.
- **TEST-ORDER-RUNTIME-STATE-1** — P3: the published deployment profile shadows `AINDY_DEPLOYMENT_PROFILE` in unit tests; one file un-shadows it for whatever runs next. Fix: snapshot/restore in conftest.
- **LINT-FORMAT-1** — P3: the tree was never `ruff format`ted; CI checks only. Never format in one sweep; if wanted, format + enforce in the same PR.
- **SCOPE-NAMING-1** — P3: `enforce_api_key_scope` gates every caller. Not renamed on purpose — a missed call site on a security dependency fails OPEN.
- **DEBT-COMPAT-1** — P2: consumers run below the advertised floor and nothing reads `runtime_compatibility.py`. Fix: one comparison where `/api/version` is fetched; warn, never refuse.
- **INITIATOR-IDENTITY-1** — *(OpenClaw)* initiating identity ≠ authenticated one; an asserted subject may only CONSTRAIN, never a `User` row. Design filed; P0 the day an inbound consumer ships.
- **DISPATCH-ADMISSION-1** — deferred. Do NOT build a general hook system in the kernel process (Tier 1 only).
- **MEM-EXPAND-DEAD-1** — `expand()`'s semantic half always returns `[]` (pgvector `ndarray` vs `list` guard). pgvector 0.5.0 fixes it — which is why #390 was HELD: it turns expansion on in the path that exhausted the pool.
- **DB-NODUS-BUDGET-1** — both fixes shipped; remaining soak + flip `AINDY_MEMORY_RECALL_OWN_SESSION`. Do NOT roll back the caller's session.
- **LOCKFILE-PLATFORM-1** — a Windows lockfile cannot satisfy Linux `npm ci`; `Platform Lockfile` workflow regenerates. Verify with `npm ci`, never `npm install` + build.
- **DEP-UPGRADE-DEFERRED-1** — otel packages are version-locked; hand-align and `pip install --dry-run`. react-router 7→8 waits on a ui-kit release.
- **PACK-DEBT-6** — P3: `nltk` + `textstat` are runtime pins NOTHING here imports; kept because the app's search service imports both UNDECLARED. Four audit ignores + the dismissed Dependabot pair exist only for them. App declares → runtime deprecates with a date → drops both. Never a fifth ignore.
- **C3** — non-Linux strong sandbox (C2 closed). `C3_NON_LINUX_STRONG_SANDBOX_PLAN.md`.
- **SYSMAX-1 / -3 / -4** — thread-mode 100-job cap; memory not enforced per EU (guest half shipped #697, `AINDY_NODUS_MAX_MEMORY_MB`); syscall/wall-time caps advisory.
- **CLI-1 · CLI-SANDBOX-FORMAT-1 · TIER3-10 · DEPLOY-TARGET-1/2 · BILLING-1..5 · LAYER-1..5 · ROUTE-EXTRACT-\* · PACK-DEBT-\* · TENANT-\* · COMPAT-\* · DATA-\* · LOCAL-\*** — deferred; triggers and detail in `TECH_DEBT.md`.

### Open — programs and multi-item prefixes

- **APP-FR-\*** — app-side feature requests. **Next: FR-47** (the app numbers ahead of this ledger — read its register before numbering). Open: FR-14 recurrence half; FR-46 (built #764, off). Closed: FR-43 #761, FR-44 #760, FR-45 #759. FR-42 closed #750 (DEC-071).
- **ECOGAP-\*** — ECOGAP-1 ph1–3 and ECOGAP-4 G4b shipped opt-in; G4a built-but-INERT until a policy is registered. ECOGAP-2 is C2/C3, ECOGAP-3 is MEMORY-EMBEDDING-PROVIDER-1 — don't double-track.
- **RTR-\*** — 1/5/6 closed; 2/3/4/7 harden-halves done. RTR-4 remaining: soak + flip `AINDY_DELEGATION_PRIVATE_MEMORY`; delegate writes take the deferred path, so `MemoryNodeDAO.save` is the chokepoint.
- **DOCS-\*** — check `APP_ROUTERS` + `ROUTE_OWNERSHIP_INVENTORY.md`, never file presence, before calling a route runtime-owned.
- **SYSCALL-STABILITY-\*** — `SyscallEntry.stable` and `_STABLE_SYSCALLS` measure different things; the duplicate guard is on `SyscallRegistry.__setitem__`; `stable` defaults `True`.
- **AUDIT-INVARIANTS-VERIFIED-1** — record: output validation is warn-only for *experimental* syscalls only; `stable` ones return an error envelope. Verify the guarantees, not just the gaps.

### Recorded decisions — an INDEX of `docs/governance/DECISION_LOG.md`, not the record

DEC-001..009 are the founding principles. From DEC-010 on, one line per id
(`test_decision_log_integrity.py` pins that every id is indexed and exists):

- **DEC-011** wait's pending request is a STATE KEY, not a column · **DEC-012** no `nodus_output_state` seeding on resume · **DEC-013** the bus never carries a payload; the ROW is its home · **DEC-014** no request-level WAIT (`ExecutionWaitSignal` removed) · **DEC-015** `SwitchCaseEdgeGroup` declined · **DEC-016** the authority gate decides `skip | abort`, never `grant` · **DEC-017** guest wait is `await_event()`; raise-based builtins deleted · **DEC-018** first-non-`None` hooks declined · **DEC-019** kernel deterministic replay declined · **DEC-020** `agent_execution` resolves for RESUME only, never public `FLOW_REGISTRY`.
- **DEC-021** no `waiting → completed` edge on a unit · **DEC-022** `agent.list_recent_durations` removed (floor 23) · **DEC-023** route contract enforced at REQUEST time only; boot AST validator deleted · **DEC-024** `execute_with_retry` deleted; `decide_retry()` is the primitive · **DEC-025** `failure_class` is a STRING set at the raising site; only `transient` retries.
- **DEC-026** prune LEAVES only · **DEC-027** unclassified type KEPT · **DEC-028** failure-shaped = audit · **DEC-029** 90 d / 7 d / never, ships UNSET · **DEC-030** lease `fence` increments only on takeover · **DEC-031** the check is `FOR SHARE` inside the job txn · **DEC-032** two jobs fenced, ten not · **DEC-033** `execute_run` untouched — no second CAS.
- **DEC-034** GenAI semconv = EMIT three span kinds · **DEC-035** the meter lives inside `llm_operation` · **DEC-036** `enduser.id` beside `user.id` for one release · **DEC-037** `gen_ai.client.*` via a MeterProvider BESIDE `aindy_llm_*` · **DEC-038** content capture OUT · **DEC-039** `WorkflowStore` over Postgres declined until the runtime READS guest state.
- FR-35: **DEC-040** guest LLM usage rides the worker reply (fourth deferred collection) · **DEC-041** deferral REPLACES observation in the worker · **DEC-042** admission there, accounting in the parent · **DEC-043** parent attributes from the reply's explicit context · **DEC-044** `chat` span replayed as `aindy.deferred` · **DEC-045** ledger cap `AINDY_NODUS_LLM_LEDGER_MAX` (256).
- **DEC-046** HTTP-SCOPE-GAP-1: scope = VERB, row filter = OWNERSHIP · **DEC-047** CLI-EXEC-SURFACE-1: operator half HTTP-only (both accepted 2026-09-17, #723).
- EGRESS-INPROC-1: **DEC-048** decision once, before the isolation branch · **DEC-049** worker installs it from its payload · **DEC-050** mechanism REPORTED, never refused · **DEC-051** flag stays default off.
- AUDIT-CORRELATION-1: **DEC-052** additive keys, no schema · **DEC-053** join 2 = `env_applied` · **DEC-054** convention on `action_id`, NO FK · **DEC-055** stays `operational`, TTL-bounded.
- AUTHORITY-LIFETIME-1: **DEC-056** authority ends with the run, two sites · **DEC-057** cancel read widened, sticky · **DEC-058** fail-OPEN · **DEC-059** `waiting` keeps authority.
- EVENT-OUTBOX-1: **DEC-060** event rides the handler's session · **DEC-061** id stays client-assigned · **DEC-062** a raising handler is rolled back; unit row commits at creation.
- RECOVERY-GRANULARITY-1: **DEC-063** per-step write at the worker seam · **DEC-064** `agent_steps`, no table · **DEC-065** plan STEP INDEX, never an ordinal · **DEC-066** replay only continued + `success`.
- **DEC-067** FR-40: args-validation tally rides the worker reply (fifth deferred collection); deferral replaces observation; errors capped `AINDY_TOOL_ARGS_VALIDATION_LEDGER_MAX` (32), counts never. **DEC-071** FR-42: the capability-mapping audit row is per RUN; a non-AgentRun scope gets type rows only + `mapping_recorded: false` on the token, outside the HMAC. **DEC-072** FR-15 evidence topology mounts the host docker socket — an instrument, never a profile or operator recipe.
- FR-46 (#764): **DEC-073** `$from_step` reference · **DEC-074** resolved before `execute_tool` · **DEC-075** unresolved fails; off. **DEC-076** IDEM-14: tool key per STEP.
- FR-38 (§9, #734): **DEC-068** nodus_vm gate = a guest wait from inside `call_tool`; the CHAIN parks mid-segment (the node reports, never parks) · **DEC-069** `skip` = a `skipped` `agent_steps` row replayed on re-drive (widens DEC-066) · **DEC-070** the event and counter come from the worker.

### Standing rule — not an item

- **A test-mode short-circuit ABOVE the real decision** makes the real path untestable while every test passes (two instances in FR-15's path). Put the guard below the switch, or give it an opt-in test mode cannot veto; assert the mechanism you think you are exercising is the one running.
- **A source-text assertion is a supplement, never the coverage** — a comment satisfied `register_flows()`; prefer the AST or drive the real entry point.
- **A fixture that neutralises a dependency also neutralises any test of HOW it is used** — assert on the interaction outside the fixture; a no-op patch never proves a call did not happen (variants 13, 15).

### Closed — kept as one line because the rule still bites

- **MCP-SDK-2X-1** — CLOSED 2026-09-20 (#727): `mcp<2` lifted; nodus-mcp 0.1.4 branches per SDK major. A cap on the `[mcp]` extra must be REPEATED in the CI `Install MCP extra` step (it installs directly). Never isolate the MCP tests to go green.
- **HTTP-SCOPE-GAP-1** — CLOSED 2026-09-17 (DEC-046 accepted, #723). Scope answers the VERB, the row filter answers OWNERSHIP; no `:any` scope. Gotchas: `enforce_api_key_scope` takes ANY-OF alternatives; router-level `dependencies` are invisible to a per-route `dependant` walk; scan routes with `_iter_api_routes`.
- **CLI-EXEC-SURFACE-1** — CLOSED 2026-09-17 (DEC-047 accepted, #723). The operator half stays HTTP-only; a transport cannot grant authority it lacks; an operator syscall opens three doors at once.
- **RECOVERY-GRANULARITY-1** — CLOSED 2026-09-17 (#722; DEC-063..066). The worker seam writes `agent_steps` per step as it completes, keyed on the plan's STEP INDEX (third `call_tool` arg, arity (2,3)); a CONTINUED run replays a `success` row (`replayed: True`). ★ nodus absorbs a step's `throw` — the worker reply reads `success`; the parent reads failure from the step results.
- **EVENT-OUTBOX-1** — CLOSED 2026-09-17 (#721; DEC-060..062). In a pipeline a queued event rides the handler's session and its next commit. ★ The pipeline now ROLLS BACK the request session when the HANDLER raises (the failure emit used to commit its pending writes); the unit row commits at creation. Test a 4xx route on the private engine.
- **AUTHORITY-LIFETIME-1** — CLOSED 2026-09-17 (#720; DEC-056..059). A token for a run in a TERMINAL status is refused at `check_tool_capability` (before the HMAC) and the dispatcher's agent gate — CANCEL-REACH-1's read widened (`run_terminal_status`), sticky once terminal, fail-OPEN. `waiting` KEEPS authority; the token stays stateless.
- **AUDIT-CORRELATION-1** — CLOSED 2026-09-17 (#719; DEC-052..055). `syscall.executed` carries `capability`, `guarantee`, `action_id` (None unless the gate engaged); `capability.allowed` carries `action_id`. A documented CONVENTION on the unique `action_id` — no FK either way; both sides TTL-bounded. Join: `IDEMPOTENCY_CONTRACT.md`.
- **EGRESS-INPROC-1** — CLOSED 2026-09-16 (#718; DEC-048..051). The ISOLATED branch returned before `egress_scope`, so a distrusted tool had NO egress enforcement. Now `(mode, domains)` is resolved once before the branch; the worker installs it process-globally from its payload; the envelope reports `egress.mechanism`. Flag stays default off.
- **EU-DOUBLE-FINALIZE-1** — CLOSED 2026-09-17 (#713). Three sites finalised an agent run's unit; a verify-failed run's unit stayed `executing` forever. Use `ExecutionUnitService.finalize_for_run_status`. Read a consumer's "noise" as a claim.
- **SESSION-COMMIT-1** — CLOSED 2026-09-16. `SessionLocal()` … write … `close()` without `commit()` is a rollback that every shared-fixture test reads as a commit; four instances in a week. Guard: `test_own_session_commits.py` (derived census, empty allowlist).
- **IDEM-11** — CLOSED 2026-08-19: `AINDY_SYSCALL_IDEMPOTENCY` defaults on. NOT exactly-once under contention (8 concurrent calls ran twice; FR-27 advisory lock is the opt-in strict mode). Watch ALL labels of `aindy_effect_gate_outcomes_total`. `_durable` engages the gate for any syscall.
- **CANCEL-REACH-1** — CLOSED 2026-09-15. Fails OPEN (unreadable = not cancelled); one own-session read per run per 2 s; the isolated branch is refused pre-spawn and the parent kills the worker.
- **RETRY-CLASSIFY-1** — CLOSED 2026-09-16 (#703). Pass the WHOLE result dict to `is_retryable_error`, never `result["error"]`; the class is a string because the guest swallows host exceptions.
- **TOOL-SEAM-ISOLATION-1** — CLOSED 2026-08-19. A declared `isolation=` tool runs out of process with NO fallback — a crashed worker means the tool does not run. `db` is None there; undeclared tools run in-process by design.
- **NODUS-UPGRADE-1** — bump `nodus-lang` + `nodus-mcp` at ALL THREE sites (`pyproject.toml`, `AINDY/requirements.txt`, the `Install MCP extra` CI step — `--no-deps` there ignores pyproject). Read release notes before assigning severity (NODUS-UPGRADE-2: 5.0.1→5.0.4 was a security fix filed P3).
- **RT-MEMTXN-LEAK-1** — CLOSED. Never hold a shared-session transaction across a slow external call; never `rollback()` a shared session to free it; a capture must not enqueue capturable work. Touching an ORM attribute after commit silently reopens a transaction.
- **ROUTE-EFFECT-BYPASS-1** — CLOSED 2026-08-16. `memory.write` REPLACED the caller's `extra` — a naive rewire was silent data loss behind a 201. `POST /nodes/search` still bypasses, pinned.
- **CAPABILITY-PROVIDER-TIMEOUT-1** — FIXED 2026-08-16. Provider checks fail CLOSED; the cache lives on the provider object, not a module global (a module latch must be added to two registry-reset dicts).
- **AGENT-EVENT-VOCAB-1** — CLOSED 2026-09-10. Never put a vocabulary under `db/models/`; the load-bearing guard is the AST census that every EMITTED type is declared.
- **NATIVE-DISCOVERY-1 / NATIVE-CI-1** — CLOSED. `cargo build` emits `libmemory_bridge_rs.so`/`.dll`; Python imports neither — rename by hand locally. A `pull_request`-triggered new workflow runs on its own PR; a `push` one does not.
- **PYPI-PUBLISH-1** — CLOSED. Bump the Dockerfile pin AND changelog in one PR; after the tag, append the `SANDBOX_ESCAPE_AUDIT.md` gate entry. `publish.yml` now `workflow_call`s Boot Smoke (which retries on PyPI CDN lag). Protocol: `docs/governance/RELEASE_CHECKLIST.md`.
- **FLAKY-1 / CI-MARKER-1 / EXEC-ENV-BIND-1 / COST-GOVERNOR-1 / QUOTA-ACCRUAL-ORPHAN-1** — CLOSED; rules kept above. Cost governor: reserve only for `METERED_METHODS`; planning has no run id, only the tenant window catches a runaway planner; a refusal reaches the route one `__cause__` down. Quota: a unit is reaped by whoever established it; reproduce where the CALLER enters.
- **NODUS-SYS-SURFACE-1** — CLOSED: `import "std:sys"` hits nodus's own stub, not the dispatcher; only bare `sys(...)` reaches `dispatch_syscall`; fail-loud guard in `nodus_worker.py`.

~50 further closed entries are history only or have their rules absorbed above — `TECH_DEBT.md`, or the pre-trim archive for the one-line form. Adding a `SystemEventTypes` value: regenerate `tests/baselines/system_event_contract.json`.

---

## Key file locations

| What | Where |
|---|---|
| Idempotency gate / effect ledger counter | `AINDY/kernel/syscall_dispatcher.py`, `kernel/effect_ledger.py` |
| Syscall outcome vocabulary (envelope + ledger resolved once) | `AINDY/kernel/syscall_outcome.py` |
| Cancellation (fails open) | `AINDY/kernel/cancellation.py` |
| Schema version / baseline | `AINDY/db/schema_contract.py`; `scripts/schema_version_baseline.json` |
| Alembic head constant + `bootstrap-schema` | `AINDY/db/alembic_head.py`; `AINDY/runtime_only.py::_bootstrap_schema` |
| Scheduler jobs; leadership + lease fence | `AINDY/platform_layer/scheduler_service.py`; `leadership.py` |
| Retry policy; pending request on a wait; flow state conflict policy | `AINDY/core/retry_policy.py`; `core/pending_request.py`; `runtime/flow_engine/state_merge.py` |
| System-event retention | `AINDY/core/system_event_retention.py` |
| Execution environment vocabulary | `AINDY/core/execution_environment.py` — `ExecutionEnvironmentSpec` |
| Route-guard bypass; route inventory | `AINDY/core/route_execution_guard.py`; `AINDY/route_inventory.json` + `scripts/check_route_inventory.py` |
| Token meter / LLM usage ledger; GenAI telemetry | `AINDY/platform_layer/token_meter.py`; `genai_telemetry.py` |
| Connector hook + authorized outbound | `platform_layer/registry.py::register_connector`; `external_call_service.py` |
| Runtime-callback subprocess (CWD hazard) | `AINDY/platform_layer/runtime_callback_host.py`, `_worker.py`; `config.py::_build_log_handler` |
| Sandbox runner / escape posture | `AINDY/platform_layer/sandbox_runner.py`; suite `tests/sandbox/`; audit `docs/runtime/SANDBOX_ESCAPE_AUDIT.md` |
| Tool worker (out-of-process, no fallback); revocable DB handle | `AINDY/agents/tool_worker.py`; `agents/tool_session.py` |
| Native crate loader | `AINDY/memory/native_bridge.py` |
| Auth router / service; admin bootstrap | `routes/auth_router.py`; `services/auth_service.py`; `startup.py::_bootstrap_admin_email` |
| Agent admin + user-owned agent routes; system agent roster | `routes/platform/admin_router.py`, `agents_router.py`; `db/models/agent.py::SYSTEM_AGENT_SPECS` |
| SPA entry / static files / vite config | `platform/src/PlatformApp.tsx`; `AINDY/routing.py::_SPAStaticFiles`; `platform/vite.config.ts` |
| Soak harness; unit conftest + marker guard | `tests/integration/soak_harness.py`; `tests/unit/conftest.py`, `test_ci_marker_default.py` |
| Runtime contracts (idempotency, sandbox, connector, SDK, UI, invariants, durable-state ownership) | `docs/runtime/*_CONTRACT.md`, `EXECUTION_INVARIANTS.md`, `SECURITY_MATRIX.md`, `SYSCALL_REFERENCE.md`, `NODUS_DEVELOPER_GUIDE.md` |
| Design records index (every scope/design doc + status) | `docs/design/README.md` |
| Comparative research index (8 systems; what is settled) | `docs/governance/COMPARATIVE_RESEARCH_INDEX.md` |
| Release checklist; upgrades index; latest app handoff | `docs/governance/RELEASE_CHECKLIST.md`; `docs/upgrades/README.md`; `docs/upgrades/APP_HANDOFF_v2.23.0.md` |
| Outbound handoffs to Nodus | `docs/handoffs/README.md` |
| Route ownership; deployment targets | `docs/runtime/ROUTE_OWNERSHIP_INVENTORY.md`; `docs/operations/DEPLOYMENT_TARGETS.md` |
| Sibling repos | ui-kit `C:\dev\aindy-ui-kit\src\`; apps monolith `C:\dev\aindy-apps-monolith\CLAUDE.md` |
