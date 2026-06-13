# Changelog

## Unreleased

---

## 1.3.0 — 2026-06-12

### Added

- **`aindy-runtime init`** — new CLI scaffold command. Writes four files to the target
  directory from a single command, closing the operator onboarding gap found during the
  1.2.0 live walkthrough:
  - `AINDY/.env` — generated 64-char hex `SECRET_KEY` + correct `DATABASE_URL` pointing
    at the compose `postgres` service name (not `localhost`)
  - `Dockerfile` — `FROM python:3.11-slim`, `pip install aindy-runtime==<version>` from
    PyPI, `CMD ["aindy-runtime", "serve"]`
  - `docker-compose.yml` — postgres (pgvector:pg16) + api (build: .) + redis
    (`--profile full`), with correct `AINDY/.env` volume mount and `env_file` wiring
  - `docker/init-pgvector.sql` — `CREATE EXTENSION IF NOT EXISTS vector`
  - Existing files are skipped unless `--force` is given (idempotent re-runs).
  - `--dir PATH` targets a different directory (default: CWD).

### Fixed

- **Platform UI — Agent Registry crash on empty state** (`platform/src/components/platform/AgentRegistry.jsx`):
  `useState` / `useCallback` / `useEffect` were called after a conditional
  `if (!isAdmin) return` early return, violating React's Rules of Hooks. When auth state
  loads asynchronously, `isAdmin` briefly differs between renders, causing React to throw
  _"Rendered more hooks than during the previous render."_ The empty-state UI for zero
  agents was already in the component — it never rendered because the crash happened first.
  Fix: all hooks moved above the `isAdmin` guard; `loadAgents()` gated inside `useEffect`
  with `if (isAdmin)`.

- **Platform UI — crashed screen poisoned subsequent navigation** (`platform/src/PlatformApp.tsx`):
  The outer `<ErrorBoundary>` wrapping all routes stayed in `hasError=true` after catching
  a crash, blocking every in-app navigation until a full page reload. Fix: routes extracted
  into `<PlatformRoutes>` which keys the boundary on `location.pathname` — resets
  automatically on every navigation.

- **OpenClaw example — `or` fallback syntax** (`examples/openclaw/openclaw_agent.nd`):
  `x or "fallback"` is not valid Nodus 4.0.3 — `or` is treated as a variable name at
  runtime. Fixed two occurrences with explicit nil-check pattern.

- **OpenClaw runner — `sys.v1.job.submit` missing `task_name` field**
  (`examples/openclaw/openclaw_runner.py`): Added `"task_name": "openclaw.reminder"` to
  the schedule reminder dispatch payload.

- **OpenClaw runner — state readback used wrong key** (`examples/openclaw/openclaw_runner.py`):
  CLI printout read from `result["extras"]["globals"]` but `set_state` writes to the
  runner-owned `agent_state` dict. Fixed to `result.get("agent_state")`.

### Docs

- **`docs/runtime/USER_WALKTHROUGH_LOG.md`** (new): live operator onboarding issue log
  (Issues 1–9) from the first real pip-install walkthrough of 1.2.0 against a live stack.
- **`docs/runtime/QUICKSTART.md`**, **`KERNEL_CAPABILITY_AUDIT.md`**,
  **`INFINITY_LOOP_AUDIT.md`** added to the doc index.

---

## 1.2.0 — 2026-06-11

### Added — REPLAY-1: Clock abstraction for deterministic replay

- **`AINDY/kernel/clock.py`** (new): ContextVar-backed `utcnow()` + `frozen_at(t)` context
  manager. Production code calls `utcnow()` instead of `datetime.now(timezone.utc)`; tests
  freeze time with `frozen_at(fixed_dt)`. Override is async-safe and thread-safe — each
  coroutine or thread has its own ContextVar slot.
- 12 call sites updated across the execution-critical path: `SyscallDispatcher` EffectRecord
  gate (3), `CircuitBreaker._now()`, `SchedulerEngine` time-wait tick, `ExecutionUnitService._now()`,
  `SystemEventService` event timestamp + 5 cutoff queries, `flow_engine` runner completion,
  runner failure, and `_default_wait_deadline`.
- **`tests/unit/test_clock.py`** (new): 12 tests covering core clock behaviour, nested freeze,
  thread isolation, and end-to-end verification of `CircuitBreaker`, `ExecutionUnitService`,
  `_default_wait_deadline`, `_complete_effect_record`, and `emit_system_event`.

### Changed — NODUS-UPGRADE-1: nodus-lang 3.0.2 → 4.0.3

- **`pyproject.toml`** + **`AINDY/requirements.txt`**: Pin updated to `nodus-lang==4.0.3`.
- **`AINDY/runtime/nodus_worker.py`**: `_runtime_emitted_events()` updated from deprecated
  `runtime.last_vm` (removed in v4) to `runtime._get_active_vm()`.
- **`docs/runtime/NODUS_DEVELOPER_GUIDE.md`** §8: Version table + v3→v4 breaking-change notes
  added. Key changes: `last_vm` → `_get_active_vm()`; `allowed_paths` default now `[os.getcwd()]`
  (was `None`).

### Changed — CI smoke: install from PyPI wheel

- **`.github/workflows/smoke-postgres.yml`**: Install step changed from `pip install -e .[test]`
  to `pip install aindy-runtime==$AINDY_VERSION` — validates the published PyPI wheel on every
  push rather than the local editable install. Cache key simplified to hash `pyproject.toml` only.

### Added — OpenClaw Infinite Weave spike

- **`examples/openclaw/`** (new): Demonstrates the aindy-runtime complement to OpenClaw's
  `pi-agent-core` loop. `openclaw_agent.nd` — Nodus 4.0.3 agent script (persona recall, skill
  routing, pgvector turn persistence). `openclaw_runner.py` — Python bootstrap, 4 host functions,
  NodusRuntime wiring. `README.md` — 8-dimension delta table and standalone + live-stack run
  instructions.

---

## 1.1.0 — 2026-06-08

### Added — CI-SMOKE-1: PostgreSQL boot smoke workflow + Quickstart (2026-06-08)

- **`.github/workflows/smoke-postgres.yml`** (new): Boots the runtime against
  `pgvector/pgvector:pg16` + Redis 7, waits up to 30 s for `/health/deep` to reach
  `{"status":"healthy"}`, asserts the `/api/version` boot surface, and records TTFA
  (time-to-first-answer) for `/health` and `/health/deep` as a `smoke-ttfa-py3.11` JSON
  artifact retained 90 days.
- **`docs/runtime/QUICKSTART.md`** (new): Five-minute boot guide covering Docker Compose
  quickstart and bare-metal install with the correct `/health/deep` response shape documented.
- Install step uses `pip install -e ".[test]"` (editable); a comment marks the line for
  switching to `pip install aindy-runtime` when PYPI-PUBLISH-1 closes.

### Fixed — Boot smoke CI: health + registry assertion bugs (2026-06-08)

- **`AINDY/routes/health_router.py`**: Scheduler check returns `{"status": "disabled"}` when
  `AINDY_ENABLE_BACKGROUND_TASKS=false`. `"disabled"` was not in the non-degrading status set
  (`{"ok", "not_configured", "not_applicable"}`), so `/health/deep` always reported `"degraded"`
  in the smoke environment and the workflow timed out after 30 s. Added `"disabled"` to the set.
- **`.github/workflows/smoke-postgres.yml`**: The `syscall_registry` assertion read
  `data.get("syscall_registry")` from the top-level response; the key lives at
  `data["checks"]["syscall_registry"]`. Fixed to `(data.get("checks") or {}).get("syscall_registry")`.
  `docs/runtime/QUICKSTART.md` example JSON updated to match the real response shape.

### Fixed — MEMORY-1 + EVENT-1: atomic ingest + explicit emission guard (2026-06-08)

- **`AINDY/memory/memory_ingest_service.py`** (MEMORY-1): `persist_memory_ingest_payload` now
  uses a single transaction (`commit=False` on all DAO calls, single `db.commit()` at end).
  Any failure rolls back the entire write atomically — eliminates the partial-write orphan window.
- **`AINDY/core/execution_pipeline/pipeline.py`** (EVENT-1): `_safe_emit_event` now sets an
  `_emission_failed` flag on `ctx.metadata` on first failure and short-circuits on re-entrant
  calls. The loop-prevention guard is now explicit rather than relying on exception swallowing.

### Added — C3 Phase 5: macOS sandbox escape CI certification workflow (2026-06-06)

- **`.github/workflows/macos-sandbox.yml`** (new): `workflow_dispatch` job targets `macos-14`
  (Apple Silicon). Installs Colima as the Linux-backend Docker provider, runs
  `pytest -m sandbox_escape -v` against the full 17-test escape suite, and uploads
  `sandbox_escape_results.json` as a workflow artifact.

### Fixed — Auth hardening: AUTH-V1, V4, V6 (2026-06-06)

- **AUTH-V1** (`AINDY/routes/__init__.py`): Removed duplicate `health_router` re-export that
  shadowed the module with an `APIRouter` object.
- **AUTH-V4** (`@aindy/ui-kit` `src/api/auth.js`): `logoutUser()` added. The platform SPA can
  now call logout without a manual `fetch()`.
- **AUTH-V6**: `require_admin_principal` now correctly gates `/platform/admin/*` routes to
  tokens carrying the `admin` scope; API keys without it receive 403.

### Fixed — EVENTBUS-REDIS-URL-CONSOLIDATION-1: AINDY_REDIS_URL alias removed (2026-06-06)

- **`AINDY/kernel/event_bus.py`**, **`AINDY/config.py`**, **`AINDY/.env.example`**:
  `AINDY_REDIS_URL` alias fully removed — all components now read `REDIS_URL` exclusively.
  `AINDY_SKIP_MONGO_PING` alias also removed; reads `SKIP_MONGO_PING` directly.

### Fixed — EXEC-EU-1 + OBS-1: EU lifecycle safety + observability (2026-06-06)

- **`AINDY/core/execution_pipeline/pipeline.py`** (EXEC-EU-1): `_safe_finalize_eu` moved into
  a `finally` block; `ctx.metadata["eu_finalized"]` guard prevents double-finalization; `finally`
  call gated by `eu_status != "waiting"` so suspending flows are not erroneously finalized.
- **`AINDY/core/execution_pipeline/resources.py`** (OBS-1): `_safe_require_eu`,
  `_safe_finalize_eu`, and `_safe_emit_event` failures promoted from `DEBUG` to `logger.warning`.

### Fixed — OPER-EXEC-001/002: distributed mode default + ContextVar propagation (2026-06-06)

- **OPER-EXEC-001**: Worker compose environment and `AINDY/.env.example` updated to enforce
  distributed execution mode in production; thread-mode carries an explicit dev-only warning.
- **OPER-EXEC-002**: `copy_context()` added at both `ThreadPoolExecutor.submit` call sites in
  the execution pipeline so ContextVar values (trace_id, pipeline_active, etc.) propagate
  correctly into worker threads. 3 regression tests added.

### Fixed — ROUTE-EXTRACT-001: agent/memory routers extracted to aindy-apps-monolith (2026-06-06)

- **`AINDY/routes/__init__.py`**: `agent_router`, `memory_metrics_router`, and
  `memory_trace_router` removed from runtime router registration. Now registered by
  `aindy-apps-monolith` via `register_router()` at bootstrap time (PR #37).

### Fixed — Agent approve orphaned-run watchdog (2026-06-06)

- **`AINDY/platform_layer/scheduler_service.py`**: `_recover_orphaned_approved_runs` scheduler
  job added — 5-minute sweep that finds `AgentRun` rows stuck in `approved` state without an
  `executing_since` timestamp for more than 2 minutes and re-dispatches them.
- **`tests/unit/test_agent_approve_watchdog.py`** (new): 4 tests — no-op, orphan re-dispatch,
  TTL threshold, exception isolation.

### Fixed — Routes audit: ROUTES-CONSUMER-SPLIT-1, API-MODULE-DRIFT-1, AGENT-API-001 (2026-06-06)

- **ROUTES-CONSUMER-SPLIT-1**: `@aindy/ui-kit` ROUTES table restored to universal shape.
  Runtime SPA gates features via `FEATURE_FLAGS` at NavLink/route level.
  `@aindy/ui-kit@1.0.5` verified safe to publish.
- **API-MODULE-DRIFT-1**: `rippletrace.js` (×16), `analytics.js` (×19), `platform.js` (×4)
  constants restored. `/trace` route gated on `FEATURE_FLAGS.RIPPLETRACE_VIEWER`.
- **AGENT-API-001**: `getAgents`, `recallFromAgent`, `getFederatedMemory` corrected to use
  `ROUTES.MEMORY.*` constants; recover/replay endpoints added.

### Fixed — AUTH-V2/V3: API key scope enforcement wired (2026-06-07)

- **`AINDY/services/auth_service.py`**: `enforce_api_key_scope(key, required_scope)` added.
  Wired as a FastAPI dependency to flows routes, memory routes, and `dispatch_syscall`.
  API keys without the required scope now return 403.

### Fixed — AUTH-V3/V5: dead auth path + SECRET_KEY export removed (2026-06-07)

- **AUTH-V3** (`AINDY/routes/api_key_auth.py`): `get_authenticated_principal`, `require_scope`,
  and `AuthPrincipal` removed — dead parallel auth path that duplicated the real auth with
  weaker guarantees.
- **AUTH-V5** (`AINDY/services/auth_service.py`): `SECRET_KEY` module-level export removed.
  `global` assignments in `rotate_signing_key` and `_reload_key_on_sighup` also removed.

### Fixed — TIER3-8 + TIER3-9: memory drop logging + flush scope (2026-06-07)

- **TIER3-8** (`AINDY/core/distributed_queue.py`): `enqueue()` drop paths now emit
  `logger.warning` — dropped items are visible in production logs.
- **TIER3-9** (`AINDY/core/execution_pipeline/pipeline.py`): `db.flush()` replaced with
  `db.flush([event])` to scope the flush to the new event row only, preventing in-flight ORM
  changes from the handler from being committed as a side effect.

### Fixed — SYSMAX-2: autonomous scheduler queue back-pressure (2026-06-07)

- **`AINDY/agents/autonomous_controller.py`**: `submit_autonomous_async_job` raises
  `QueueSaturatedError` when the scheduler is full; `evaluate_trigger()` maps this to a 60 s
  defer rather than swallowing it silently.

### Fixed — AGENT-RESLIMIT-001: wall_time_ms rename + migration 0005 (2026-06-07)

- **`AINDY/db/models/agent_run.py`** + **`alembic/versions/0005_wall_time_ms.py`**:
  `cpu_time_ms` field renamed to `wall_time_ms`. `MAX_CPU_TIME_MS` → `MAX_WALL_TIME_MS`.
  Name now accurately reflects that the limit measures monotonic wall-clock elapsed time.
  Migration 0005 handles the column rename idempotently.

### Added — Platform: admin user management, starter templates, dashboard UX (2026-06-07)

- Admin user management panel in the platform SPA — list users, promote/demote admin,
  search by email.
- Starter flow and agent templates available on first login for new operators.
- Dashboard UX improvements across `AgentConsole`, `FlowEngineConsole`, and
  `ObservabilityDashboard`.
- `docs/runtime/DEPLOYMENT_TARGETS.md` and `docs/runtime/MONETIZATION_AUDIT.md` added.

### Added — Kernel hardening tests + REPLAY-1 debt filing (2026-06-07)

- **`tests/unit/test_kernel_hardening.py`** (new, 3 tests): `SyscallDispatcher` contract
  edge cases filed as REPLAY-1 prerequisites.
- **`TECH_DEBT.md`**: REPLAY-1 filed — `Clock` injection required at ~12 `datetime.now()`
  call sites before deterministic replay is possible; deferred post-PyPI + OpenClaw spike.

### Added — C3 Phase 2: macOS Docker Desktop Linux backend detection + policy (2026-06-06)

- **`AINDY/platform_layer/sandbox_runner.py`**: Extended `_detect_wsl2()` to handle macOS.
  New `docker_macos_backend` field: detects Docker Desktop running a Linux container backend
  via Apple Virtualization Framework (macOS 12+) or HyperKit (older). `wsl2_kernel_available`
  is now True on macOS + Docker Desktop Linux containers mode.

- **Static platform matrix** (`sandbox_platform_capability_matrix()`): Updated `PLATFORM_WINDOWS`
  and `PLATFORM_MACOS` static entries to `linux_container_backend_available=True`. Docker Desktop
  on both platforms supports Linux containers. Both now correctly show `no_new_privileges`,
  `drop_all_capabilities`, and `pids_limit` as available hardening controls.

- **`docs/runtime/MACOS_CONTAINER_POLICY.md`** (new): Policy document recording what IS and is
  NOT claimed for macOS + Docker Desktop Linux containers. Assurance tier: `container-grade-sandbox`
  (same as Windows + Docker Desktop). Seccomp/AppArmor/SELinux not claimed — not tested. Strong
  sandbox VM still requires native Linux. Escape suite certification pending.

- **2 new unit tests** in `test_sandbox_runner.py` (64 total): `test_macos_with_linux_container_backend_is_docker_macos`,
  `test_macos_without_linux_container_backend_not_detected`. `test_result_has_required_keys` updated
  to check `docker_macos_backend` field.

### Added — C3 Phase 1: WSL2/Linux backend detection for OCI sandbox runner (2026-06-06)

- **`AINDY/platform_layer/sandbox_runner.py`**: Added `_detect_wsl2(container_runtime)`.
  Detects two cases: Python process running inside WSL2 (Linux host + `/proc/version` contains
  "microsoft"); or Windows host with Docker Desktop in Linux containers mode (via `docker info`
  `OSType=linux`). Returns `{is_inside_wsl2, docker_wsl2_backend, wsl2_kernel_available, ...}`.

- **`_supports_linux_container_kernel_controls()`** now accepts a `linux_container_backend`
  keyword argument. When `True`, the function returns `True` even on non-native-Linux hosts,
  enabling `no_new_privileges`, `drop_all_capabilities`, and `pids_limit` to be treated as
  available controls for OCI containers running on Docker Desktop Linux backends.

- **`inspect_container_kernel_controls()`** has a new `linux_container_backend: bool = False`
  parameter. Basic kernel controls (cap_drop, pids_limit, no_new_privileges) are now correctly
  reported as supported/active on Windows + Docker Desktop Linux containers mode.
  Profile-based controls (seccomp, AppArmor, SELinux) remain native-Linux-host-only — they
  were not tested in Phase 0 and are not claimed.

- **`ContainerizedOciSandboxRunner`** caches `_detect_linux_container_backend()` at
  construction time (`self._linux_container_backend`) and passes it to all
  `inspect_container_kernel_controls()` calls so runner metadata is accurate.

- **`sandbox_platform_capability_matrix()`** now includes a `current_wsl2_detection` field
  in its return dict, alongside the existing `current_container_backend_detection`.

- **Platform matrix hardening controls split**: `_platform_matrix_entry()` now correctly
  separates controls. When `linux_container_backend_available=True` on Windows: basic kernel
  controls added to available list; degraded modes updated to name seccomp/AppArmor/SELinux
  (not no_new_privileges/pids_limit) as requiring native Linux.

- **21 new unit tests** in `tests/unit/test_sandbox_runner.py` across four new classes:
  `TestDetectWsl2` (6), `TestInspectContainerKernelControlsLinuxBackend` (8),
  `TestPlatformMatrixHardeningControlsWithLinuxBackend` (6), `TestSandboxPlatformCapabilityMatrixWsl2Field` (1).

### Added — C3 Phases 3+4: threat model, posture API, release gate (2026-06-05)

- **`docs/runtime/SANDBOX_ESCAPE_AUDIT.md`** (new, append-only): Formal threat model mapping
  each of the 6 escape vector categories to the specific threat it blocks, the Docker/kernel
  control that prevents it, and the failure interpretation. Includes Entry 001 — the first
  live audit run (2026-06-05, Windows + Docker Desktop, 17/17 PASS).

- **`AINDY/platform_layer/sandbox_runner.py`**: Added `sandbox_escape_test_posture()` function.
  Reads `tests/sandbox/sandbox_escape_results.json` and returns a structured posture dict:
  `posture` (`"all_pass"` / `"has_failures"` / `"not_run"`), `last_run`, `host_platform`,
  `summary`, `coverage` (list of passing vectors), `gaps` (failing vectors), `operator_note`.
  Returns `"not_run"` gracefully when the artifact is absent (production install without tests/).
  Path is configurable via `SANDBOX_ESCAPE_RESULTS_PATH` env var.

- **`docs/runtime/RELEASE_CHECKLIST.md`**: Added Step 16 — Sandbox Escape Gate. Gate condition:
  `sandbox_escape_test_posture()["posture"] == "all_pass"`. Skips acceptable; FAILs block release.
  Includes audit trail instruction: append to `SANDBOX_ESCAPE_AUDIT.md` after each pre-release run.

### Added — C3 Phase 0: adversarial sandbox escape test suite (2026-06-04)

- **`tests/sandbox/`** (8 new files, 17 tests): Adversarial escape test suite that proves
  the existing Linux container-grade sandbox claim with real Docker invocations. No mocking.
  Each test documents exactly what attack vector is tested and why it matters.

  Test categories:
  - **Filesystem** (`test_filesystem_escape.py`, 3 tests): read-only rootfs blocks writes,
    plugin bind mount is read-only, tmpfs at `/tmp` is writable while `/etc` remains frozen.
  - **Network** (`test_network_escape.py`, 3 tests): `--network none` blocks outbound TCP
    and UDP; kernel-observable proof that only loopback interface is present.
  - **Process** (`test_process_escape.py`, 2 tests, Linux-only): `--pids-limit` enforcement
    via fork-bomb attempt; cgroup kernel evidence via `/sys/fs/cgroup/pids.max`.
  - **Privilege escalation** (`test_privilege_escalation.py`, 4 tests, Linux-only):
    `--cap-drop ALL` removes `CAP_NET_RAW` (raw socket blocked) and `CAP_CHOWN`;
    `--security-opt no-new-privileges` reflected in `/proc/self/status` (`NoNewPrivs: 1`);
    combined controls verified together.
  - **Host env leak** (`test_host_env_leak.py`, 2 tests): `SECRET_KEY`, `DATABASE_URL`,
    `OPENAI_API_KEY` and other production secrets absent from container; allowed key
    (`PYTHONIOENCODING`) present (confirms `--env` was transmitted).
  - **Path boundary** (`test_allowed_path_boundary.py`, 3 tests): unmounted host directory
    with canary file is inaccessible; plugin root is accessible at `/plugin-root`; path
    traversal (`/plugin-root/../../../etc/passwd`) resolves to container's own `/etc/passwd`.

- **`tests/sandbox/sandbox_escape_results.json`** (runtime artifact): written after each
  escape test session with schema_version, tested_at, host_platform, per-test results
  (status, evidence, docker_args, cmd), and pass/fail summary.

- **`pytest.ini`** + **`pytest.integration.ini`**: registered `sandbox_escape` marker.

  To run: `pytest -m sandbox_escape -v`
  Requires: Docker with Linux containers mode, internet access to pull `python:3.11-alpine`.
  Override image: `SANDBOX_ESCAPE_IMAGE=python:3.12-alpine pytest -m sandbox_escape -v`

### Fixed — PACK-DEBT-5: FastAPI/starlette CVE PYSEC-2026-161 (2026-06-05)

- Upgraded `fastapi` 0.121.0 → 0.135.0, `starlette` 0.49.1 → 1.0.1, and
  `prometheus-fastapi-instrumentator` 7.1.0 → 8.0.0 (8.x requires starlette ≥ 1.0).
  Resolves host-header injection CVE PYSEC-2026-161. `--ignore-vuln PYSEC-2026-161` removed
  from `security-audit.yml`; accepted-findings entry removed from `SECURITY_POLICY.md`.

### Added — CLI-SANDBOX-FORMAT-1: human-readable sandbox output (2026-06-05)

- **`AINDY/runtime_only.py`**: `aindy-runtime sandbox` now renders a ~25-line human-readable
  summary by default: platform, highest assurance tier, production-safe status, container
  backend detection, active runner/certification, verification method, escape test posture,
  trusted Python extension count, and degraded modes.
- **`aindy-runtime sandbox --json`**: new flag restores the full machine-readable JSON output
  (also includes `escape_test_posture` key alongside the original five fields). 9 tests pass
  in `test_runtime_cli.py`.

### Fixed — IDEM-6: advisory lock on blank-DB bootstrap (2026-06-05)

- **`AINDY/db/schema_contract.py`**: `reconcile_runtime_schema()` acquires
  `pg_advisory_lock(_BOOTSTRAP_ADVISORY_LOCK_KEY)` before the blank-DB `create_all` path.
  A second instance that wins the wait finds the DB already bootstrapped and skips `create_all`.
  Lock released in a `finally` block. SQLite paths unaffected. Lock key: `4149443900` (stable).
  3 new unit tests in `test_runtime_schema_contract.py`.

### Added — MONITORING-GRAFANA-1: Grafana monitoring profile (2026-06-05)

- **`monitoring/grafana/`** (new): Prometheus datasource provisioning, dashboard file provider,
  and `aindy-runtime.json` starter dashboard with 8 panels: health tier, active executions,
  execution rate, DB pool pressure, AI circuit breaker state, async queue depth, duration
  p50/p95/p99 timeseries, execution total by status.
- **`docker-compose.yml`**: `grafana` service added under the `monitoring` profile
  (`grafana/grafana:11.6.1`, port 3000, `grafana_data` volume, depends on Prometheus).
- Usage: `docker compose --profile monitoring up -d` → Grafana at `http://localhost:3000`.

### Added — COMPOSE-PROD-PORTS-1 + PROMETHEUS-PIN-1: Docker hardening (2026-06-05)

- **`docker-compose.prod.yml`** (new): Compose v2 override using `!reset []` to clear host
  port bindings on `postgres`, `redis`, and `mongo`. DB services remain reachable within
  the compose network; only `api` (8000) and `worker` (8001) publish to the host.
  Requires Docker Compose v2.24+.
- **`docker-compose.yml`**: `prom/prometheus:latest` pinned to `prom/prometheus:v3.4.1`.

### Added — Env-example tooling + LOCAL-1: upgrade path documented (2026-06-05)

- **`scripts/check_env_example_coverage.py`** (new): AST-parses all `AINDY/**/*.py` for
  `os.getenv()` / `os.environ.get()` calls and `Settings` field names; reports variables not
  in `AINDY/.env.example`. Run `--strict` to exit 1 on gaps. Added as advisory CI step in
  `runtime-ci.yml`.
- Root `.env.example` forwarding stub deleted; `AINDY/.env.example` is the sole canonical
  reference (docker-compose.yml's `env_file:` already pointed there).
- **`README.md`**: `## Upgrading` section added — `pip install --upgrade`, version verification,
  `AINDY_SCHEMA_RECONCILE=true` restart sequence for schema-bumping releases, Docker Compose
  pull-and-up flow, rollback guidance.

### Fixed — AGENT-APPROVE-001b: async approve dispatch (2026-06-04)

- **`AINDY/agents/agent_runtime/approvals.py`**: `approve_run()` now fires `execute_run`
  in a daemon background thread with its own `SessionLocal` session instead of blocking
  the request thread. The approve endpoint returns immediately with `status: APPROVED`;
  clients poll `GET /apps/agent/runs/{id}` for execution progress. Eliminates the
  client-side timeout on slow or multi-step tool execution.

- **`tests/unit/test_agent_approve_idempotency.py`**: All three shapes updated to use
  `threading.Event` for deterministic background-thread coordination, preventing the
  race condition between the background execute_run and the call-count assertion.

### Added — CLI artifact validation tests (2026-06-04)

- **`tests/unit/test_runtime_packaging.py`**: Added `test_installed_cli_help` and
  `test_installed_cli_help_without_database_url`. Both invoke the `main()` entrypoint in
  a subprocess with `--help`, asserting exit 0 and presence of the program name. The
  second test strips `DATABASE_URL` from the subprocess environment, validating that the
  lazy-import guard in `runtime_only.py` (CLI-1 mitigation) prevents database engine
  creation on help invocation. Covers RELEASE_CHECKLIST.md step 5 automatically.

### Added — Phase 3 hardening: cross-repo compatibility, release discipline, core debt (2026-06-04)

- **`tests/unit/test_cross_repo_compatibility.py`** (new, 7 tests): Regression suite for
  aindy-sdk and aindy-ui-kit compatibility assumptions. SDK tests (`-k sdk`): version
  envelope shape, stable syscall names present, watcher endpoint (`/watcher/signals`)
  registered in ROOT_ROUTERS. UI tests (`-k ui`): `boot_mode` field in
  `RuntimeSurfaceResponse`, `runtime_ui_surface_state()` returns non-empty `boot_mode`,
  all expected platform route prefixes served.

- **`tests/unit/test_runtime_readiness_contract.py`** (new, 7 tests): Covers IDEM-7
  (syscall registry floor), `_check_syscall_registry_status()` ok/incomplete paths,
  `/health/deep` includes `syscall_registry` check, and SCHED-001/002/003 (scheduler
  status graceful when tasks domain absent, stuck-run-watchdog fields always present).

- **`docs/runtime/SDK_CONTRACT.md`** (new): Defines what `aindy-sdk` can rely on from
  `aindy-runtime` — version envelope shape, auth contract, watcher endpoint paths, memory
  API, stable syscall table, health/readiness HTTP semantics, and known leakage risks.

- **`docs/runtime/UI_CONTRACT.md`** (new): Defines what the platform SPA
  (`@aindy/ui-kit` + `platform/src/`) can rely on — boot mode detection path
  (`/api/version → data.system.runtime.boot_mode`), auth flow fields, ROUTES table
  invariants, SPA asset 404 discrimination, operator endpoint availability, leakage risks.

- **`docs/runtime/CROSS_REPO_COMPATIBILITY.md`** (new): Policy document listing the
  5 obligations that must hold before any release touching stable surfaces, dependency
  tables for aindy-sdk and aindy-ui-kit, and the breaking-change policy.

- **`docs/runtime/RELEASE_CHECKLIST.md`** (new): 15-step operator verification checklist
  covering schema contract, unit tests, build artifacts, installed-artifact smoke, Docker
  compose stack, health endpoints, syscall registry count, watcher endpoint, platform SPA,
  and cross-repo compatibility assertions.

- **`AINDY_RUNTIME_90_DAY_CHECKLIST.md`**: Phase 3 complete — Runtime Core Debt
  Reduction, Verification Standards, Release Discipline, and Cross-Repo Boundary Proof
  items checked off. Final score: **77.5 / 100** (target was 76-80). Category deltas,
  blockers to 80+, and blockers to 85+ recorded in the Final 90-Day Review section.

### Fixed — IDEM-7: syscall registry completeness now visible in `/health/deep` (2026-06-04)

- **`AINDY/kernel/syscall_registry.py`**: Added `SYSCALL_REGISTRY_MIN_COUNT = 17` — the
  floor for expected static built-in syscalls. Serves as a canary: if Phase 8
  `_register_domain_handlers()` crashes, the count drops and `/health/deep` reports it.
- **`AINDY/routes/health_router.py`**: Added `_check_syscall_registry_status()` and wired
  it into `_build_deep_health_payload()`. The `checks.syscall_registry` field now appears
  in every `/health/deep` response with `status`, `count`, and `minimum_expected`.

### Fixed — SCHED-001/002/003: scheduler status no longer returns 500 in platform-only profile (2026-06-04)

- **`AINDY/routes/observability_router.py`**: Replaced the flow-engine-dependent
  `observability_scheduler_status_node` flow with a direct `_build_scheduler_status_payload(db)`
  helper. The new helper checks `get_symbol("task_is_background_leader")` and returns
  `tasks_domain_available: false` (not a 500) when the tasks domain plugin is absent.
  `FEATURE_FLAGS.OPERATOR_SCHEDULER_STATUS` in `platform/src/api/_routes.js` updated to
  `true` — the scheduler status NavLink is now enabled for all deployments.

### Fixed — PERMISSION-SECRET-CLEANUP-1: vestigial `PERMISSION_SECRET` scaffolding removed (2026-06-04)

- **`tests/conftest.py`**, **`alembic/env.py`**, **`scripts/check_schema_version.py`**:
  Removed `os.environ.setdefault("PERMISSION_SECRET", ...)` from all three sites. The
  field has `default=""` in `Settings` (no validator), so it requires no env var. Removing
  these defaults has no runtime effect but eliminates confusion about whether
  `PERMISSION_SECRET` is a required secret.

### Added — Phase 2 hardening: operability contracts and security isolation (2026-06-03)

- **`tests/unit/test_operability_contracts.py`** (new, 14 tests): Operability contract
  coverage for the three stable runtime surfaces (`GET /health`, `GET /ready`,
  `GET /api/version`). Covers `derive_public_status` tier mapping (critical →
  unhealthy, degraded database → unhealthy, non-critical degraded → 200),
  `_build_health_response` HTTP 503 path, `/ready` response body shape for
  `restore_pending` and `registry_restore_incomplete` 503 cases, and `/api/version`
  stable envelope fields.

- **`tests/unit/test_security_isolation.py`** (new, 25 tests): Security isolation
  regression coverage. Covers all 11 `_BLOCKED_ROOT_KEYS` stripped from extension
  context, `AINDY.*` object redaction in extension payloads, extension tenant mismatch
  rejection via `_validate_runtime_owned_call_metadata`, quota backend fail-open in
  dev/test and fail-closed in production.

- **`docs/runtime/SECURITY_MATRIX.md`** (new): Runtime security matrix mapping five
  dimensions (trusted internal execution, extension capability boundaries, tenant
  enforcement, deployment profile differences, degraded security posture) to their
  enforcement paths, test coverage, and known limitations. Includes explicit
  safe/unsafe/unsupported table for extension execution.

- **`AINDY_RUNTIME_90_DAY_CHECKLIST.md`**: Phase 2 complete — all Operability Review
  and Security Hardening items checked off; Phase 2 Exit Criteria met.

### Fixed — `watcher_router` and `db_verify_router` were never registered (2026-06-03)

- **`AINDY/routes/__init__.py`**: `watcher_router` added to `ROOT_ROUTERS` — `POST /watcher/signals`
  and `GET /watcher/signals` were returning 404 in all deployments. `db_verify_router` added to
  `PLATFORM_ROUTERS` — `GET /platform/db/verify` (live schema inspection) was also unreachable.
  Both routers existed and were correctly implemented but were never imported or mounted.

### Changed — Watcher client process extracted to aindy-sdk (2026-06-03)

- **`AINDY/watcher/`**: Client-process files (`classifier.py`, `window_detector.py`,
  `session_tracker.py`, `signal_emitter.py`, `config.py`, `watcher.py`) moved to
  `aindy_sdk/watcher/` in the `aindy-sdk` repo. Run the watcher client with
  `python -m aindy_sdk.watcher.watcher`. The server-side signal constants
  (`constants.py`) remain in `AINDY/watcher/` — `watcher_router.py` and
  `watcher_contract.py` continue to import from `AINDY.watcher.constants` unchanged.
- **`signal_emitter.py` (SDK)**: Rewritten to use stdlib `urllib.request` in place
  of `httpx` and the runtime-internal `perform_external_call` wrapper. The SDK
  watcher module has no runtime dependency and no new external dependencies.
- **`tests/unit/test_watcher_contract.py`**: Trimmed to constants-only assertions
  (signal types, activity types, timestamp parsing). Classifier and session-tracker
  tests migrated to `aindy-sdk/tests/test_watcher.py`.

### Changed — Default resource quota raised for real agent workloads (2026-06-03)

- **`AINDY/kernel/resource_manager.py`**: Default `AINDY_QUOTA_CPU_MS` raised from
  30 000 ms to 300 000 ms (5 minutes). A realistic single agent step — one
  `memory.recall` call with three OpenAI embedding round-trips — consumes ~34 s of
  wall-clock time (trace 4cc32073; see AGENT-RESLIMIT-001). The prior 30 s default
  caused `RESOURCE_LIMIT_EXCEEDED` on the very first approve of any non-trivial agent,
  a first-user experience cliff. The new 5-minute cap accommodates multi-step runs.
  **Note:** `cpu_time_ms` measures monotonic wall-clock elapsed time (including all
  network I/O wait), not actual CPU time. This is a known misnomer documented in
  `AINDY/.env.example` (Group 12) and tracked as AGENT-RESLIMIT-001 for post-GA fix.
  Configure via `AINDY_QUOTA_CPU_MS=<ms>`.
- **`AINDY/.env.example`**: New Group 12 "Resource quotas" documents all four
  `AINDY_QUOTA_*` variables with sizing guidance. The `AINDY_QUOTA_CPU_MS` entry
  carries an explicit warning that the field measures wall-clock time.
- **`tests/unit/test_resource_quota_defaults.py`**: New test pins the 300 000 ms
  default so it cannot silently drift.

### Added — PLATFORM-AUTH-ACQUISITION-1: platform SPA login + admin bootstrap (2026-05-28)

- **`platform/src/LoginPage.tsx`** (new): Login form calling `useAuth().login()`. On success,
  stores token via `AuthContext` and navigates within the router tree.
- **`platform/src/NotAdmin.tsx`** (new): Terminal "access denied" component with logout button.
  Rendered (not navigated to) when authenticated but `is_admin=false` — prevents redirect loop.
- **`platform/src/PlatformApp.tsx`**: Rewritten — `/login` lives outside `PlatformGuard`;
  guard uses `<Navigate to="/login" replace />` (React Router, respects `basename="/platform"`);
  `VITE_APP_BASE_URL` / `window.location.href` / `redirectToApp` dependency removed entirely.
- **`AINDY_BOOTSTRAP_ADMIN_EMAIL`**: New env var. Grant-only, idempotent. Processed in
  `startup.py` Phase 5.5 (after schema guard). Never revokes admin on var removal.
- **`aindy-runtime auth promote-admin <email>`**: New CLI subcommand. Grant-only, no restart
  needed. Exits 0 if already admin, exits 1 with guidance if user not found.
- **`AINDY/routing.py` — `_SPAStaticFiles`**: Falls back to `index.html` only for paths that
  do NOT start with `assets/`; `assets/` misses correctly return 404.

### Added — PLATFORM-UI-KIT-1: Docker self-contained build (2026-05-28)

- **`Dockerfile`**: `ui-builder` stage added — runs `npm ci` + `npm run build` from the
  registry-pinned `@aindy/ui-kit`. `docker compose build --no-cache` from a clean clone is
  now fully self-contained with no prior local UI build required.
- **`.dockerignore`**: `AINDY/platform/dist/` and `platform/node_modules/` excluded to prevent
  stale local state from leaking into the Docker build context.
- `@aindy/ui-kit@1.0.1` published — `loginUser`, `registerUser`, and `bootIdentity` all call
  `.then(unwrapEnvelope)`. Fixes the silent post-login redirect misfire in `PlatformHomeRedirect`.

### Fixed — Event bus now honors REDIS_URL (2026-05-27)

- **`AINDY/kernel/event_bus.py`**: Event bus now honors `REDIS_URL` as a
  fallback when `AINDY_REDIS_URL` is unset. Previously, setting only `REDIS_URL`
  produced a silently misconfigured event bus that connected to
  `redis://localhost:6379/0` regardless of the configured URL.
  `AINDY_REDIS_URL` is still honored and takes precedence when both variables
  are set, preserving deployments that intentionally route the event bus and
  cache to different Redis instances. `AINDY_REDIS_URL` is now deprecated;
  new deployments should use `REDIS_URL` only. The resolution logic is
  extracted into `resolve_event_bus_redis_url()` for testability.
  `get_redis_client()` (auxiliary wait-registry path) receives the same fix
  but does not fall through to the localhost default — it returns `None` when
  neither variable is set.
- **`AINDY/config.py`**: `AINDY_REDIS_URL` added as a `Settings` field with
  a deprecation comment, making it discoverable via settings introspection.

### Fixed — Docker compose infrastructure: blank-DB safety, pgvector, packaging, host binding (2026-05-27)

- **ALEMBIC-FRESH-DB-1**: Migrations 0002–0004 wrapped in
  `DO $$ BEGIN IF EXISTS (pg_catalog.pg_tables WHERE tablename=...) THEN ... END IF; END $$`
  blocks. On a blank database the blocks skip and Phase 5 `_enforce_schema_guard` bootstraps
  via `create_all`. On existing deployments the blocks run normally. `IF NOT EXISTS` on the
  index name alone is not sufficient — `CREATE INDEX ... ON missing_table` still raises
  `UndefinedTable` even with it.
- **COMPOSE-PGVECTOR-1**: Switched from `postgres:16-alpine` to `pgvector/pgvector:pg16`.
  Added `docker/init-pgvector.sql` (mounted to `/docker-entrypoint-initdb.d/`) running
  `CREATE EXTENSION IF NOT EXISTS vector`. Required for `memory_nodes` `VECTOR(1536)` column.
- **PACKAGING-DEP-1**: Added `"packaging>=24.0"` as an explicit dep in `pyproject.toml` and
  forced it into the Docker `/install` prefix. The multi-stage build was not propagating it
  from the builder stage, causing `import packaging` to fail at container startup.
- **COMPOSE-HOST-1**: Added `AINDY_HOST: "0.0.0.0"` to the compose `api` service environment.
  The runtime correctly defaults to `127.0.0.1` for bare installs; this override is required
  inside Docker for the published port to be reachable from the host.

---

## 1.0.0 — 2026-05-25

### Added — CLI subcommand structure (2026-05-26)

- **`aindy-runtime serve`**: New subcommand that starts the HTTP API server.
  Use this in place of the bare `aindy-runtime` invocation.
- **`aindy-runtime sandbox`**: Existing sandbox check promoted to a named subcommand.
- **`aindy-runtime --help`** and **`aindy-runtime --version`**: Now work without any
  environment configuration. Previously crashed on import if `DATABASE_URL` was absent.

### Fixed — CLI import crash without DATABASE_URL (2026-05-26)

- **`AINDY/config.py`**: `DATABASE_URL` now defaults to `""` instead of being required
  at import time. `Settings()` no longer raises `ValidationError` when `DATABASE_URL`
  is absent; validation defers to the point of actual server startup. `aindy-runtime serve`
  checks for a missing URL and exits with a human-readable error before attempting to
  start uvicorn.

### Removed — `aindy-runtime-api` entry point (2026-05-26)

- **`pyproject.toml`**: `aindy-runtime-api` console script removed. The underlying module
  (`AINDY.main`) is unchanged and remains importable. The boot-mode distinction that
  `aindy-runtime-api` encoded (`AINDY_BOOT_MODE`) is a monolith-internal concern not
  relevant for the extracted package. For advanced boot mode control, set
  `AINDY_BOOT_MODE=runtime-only` explicitly before calling `aindy-runtime serve`.

Initial PyPI release. Covers the full runtime stack: platform layer with
sandbox runner and OCI container detection, two-tier extension execution model,
idempotency gate (NF-1 through NF-5) with EffectRecord persistence and TTL
cleanup, Alembic migration chain (0001–0004), APScheduler job framework,
nodus-lang VM integration via `AINDYMemoryBridge`, platform UI (Vite + React
SPA bundled into the wheel), health and sandbox status HTTP surfaces, and a
weekly pip-audit CVE workflow. Extracted from aindy-apps-monolith; SDK
extracted as standalone `aindy-sdk`.

### Added — Auth dependency CVE monitoring and security policy (2026-05-25)

- **`pyproject.toml`**: New `security` optional-dependencies group — `pip-audit>=2.7.0`
  plus auth-adjacent floor pins (`bcrypt>=4.0.1`, `passlib>=1.7.4`,
  `python-jose>=3.5.0`). Install with `pip install -e .[security]`.
- **`.github/workflows/security-audit.yml`**: New workflow. pip-audit (OSV-backed)
  runs on every PR and weekly (Mondays 08:00 UTC). Fails on any detected CVE; prints
  advisory detail and SLA reminder. Exemptions documented in SECURITY_POLICY.md.
- **`.github/dependabot.yml`**: New file. Enables Dependabot for `pip` and
  `github-actions` ecosystems (weekly, Mondays). Secondary CVE signal for transitive
  deps pip-audit may miss against a stale lockfile.
- **`docs/runtime/SECURITY_POLICY.md`**: New file. Defines CVE response SLA
  (Critical: 7 days, High: 14 days, Medium: next minor, Low: next major), exemption
  procedure, and accepted-findings register. Closes PACK-DEBT-2.

### Changed — Integration CI now gates on failures (2026-05-25)

- **`.github/workflows/runtime-ci.yml`**: Removed `continue-on-error: true` from the
  `integration-postgres` job. Integration failures now block CI green. Closes PACK-DEBT-4.

### Decided — mypy not adopted (2026-05-25)

- **`TECH_DEBT.md`**: Closed PACK-DEBT-3. Decision: do not pursue mypy on
  `aindy-runtime` or `aindy-sdk`. Observed bug class is cross-module/cross-repo
  contract drift, which audit-arc and contract tests address directly. Reopen triggers
  documented (second engineer joins, or signature-drift bug missed by audit-arc).

### Added — Local+cloud distribution audit (2026-05-25)

- **`docs/runtime/LOCAL_AND_CLOUD_AUDIT.md`**: Full audit pass across seven areas
  surfacing gaps the local+cloud framing makes newly visible. Areas: multi-tenancy
  readiness (TENANT-1 through TENANT-4), cross-version compatibility beyond the SDK
  (COMPAT-2, COMPAT-3), operator "where am I running" clarity (CLOUD-1, CLOUD-2),
  data residency (DATA-1, DATA-2), self-update for local installs (LOCAL-1, LOCAL-2),
  cloud control plane placeholders (CLOUD-3, CLOUD-4), and open findings (G-1, G-2).
  Findings surface only — nothing fixed.
- **`TECH_DEBT.md`**: Four new entries from the audit: `TENANT-2` (quota group
  enforcement gap), `COMPAT-2` (no ABI deprecation policy), `DATA-1` (no data
  residency mechanism), `LOCAL-1` (no production upgrade path documented).

### Added — Local+cloud architecture framing documented (2026-05-25)

- **`docs/runtime/ARCHITECTURE.md`**: New top-level architecture document
  establishing the local+cloud distribution model as the explicit framing for
  the runtime. Covers the three layers (runtime data plane, SDK universal
  interface, cloud control plane not yet built), five concrete examples of how
  the framing shapes architectural decisions, what the framing does not commit
  to, and pointers to all related docs.
- **`docs/runtime/PUBLIC_API_CONTRACT.md`**: Added SDK Bridge Role section
  naming `aindy-sdk` as the universal interface targeting both local-install
  and cloud-hosted deployment contexts. Bumped `last_verified` to 2026-05-25.
- **`TECH_DEBT.md`**: Added `DEBT-COMPAT-1` — cross-version compatibility
  story between runtime and SDK. Deferred; trigger condition is when two
  runtime versions exist simultaneously in the wild.

### Fixed — DRIFT-1 + reordering guard: first-party bootstrap allowlist ratified, list_supported_sandbox_runners ordering frozen (2026-05-25)

**DRIFT-1 (docs-only, no runtime behavior change):**

- **`AINDY/platform_layer/extension_execution_model.py`** —
  `manifest-bootstrap:first-party-app` surface entry: `execution_path`
  updated from `"...restricted runtime-owned registration allowlist"` to
  `"...runtime-owned registration capability gate"`; `notes` updated from
  `"Registration-time capability checks use a narrower allowlist than
  runtime-built-in"` to `"...use the same allowlist as runtime-built-in —
  both are Tier 1 trusted kernel code under the isolation model."` This
  ratifies what the code has always done: `registry.py` lines 235–238
  explicitly assign `_FIRST_PARTY_ALLOWED_INPROC_EXTENSION_CAPABILITIES =
  _ALL_INPROC_EXTENSION_CAPABILITIES` with a three-line comment documenting
  the intent. `test_first_party_bootstrap_allows_all_registry_capabilities`
  in `tests/unit/test_extension_ownership.py` (unchanged) is the live
  evidence for this claim.
- **`AINDY/platform_layer/public_contract.py`** —
  `trusted_in_process_python.capability_boundary.first_party_bootstrap_default`
  updated from `"restricted-allowlist"` to `"full-runtime-owned-allowlist"`,
  matching `runtime_built_in_bootstrap_default`. Both fields now report the
  same value.
- **`docs/runtime/EXTENSION_TRUST_MODEL.md`** — Tier 1 first-party execution
  model bullet updated from "smaller default allowlist for `first-party-app`
  than for `runtime-built-in`" to the correct equivalence statement. Module-prefix
  restrictions (which modules may bootstrap, e.g. `AINDY.` vs `apps.`) are
  unchanged — those are distinct from capability allowlists. `last_verified`
  bumped to 2026-05-25.
- **`tests/unit/test_runtime_public_contract.py:354`** — updated assertion from
  `"restricted-allowlist"` to `"full-runtime-owned-allowlist"`.

**Reordering guard (preventative test, no code change):**

- **`TestListSupportedSandboxRunnersOperatorNote`**
  (`tests/unit/test_sandbox_runner.py`): 3 new tests asserting that each of
  the three `list_supported_sandbox_runners()` entries carries its explicit
  per-runner `operator_note`, not the posture-derived one from the
  `**sandbox_runner_assurance_posture(runner_type)` spread. Guards against a
  future dict-literal reordering that would silently place the spread after the
  explicit key, causing the posture note to override the per-runner one.

### Added — Sandbox status surfaces: HealthDashboard, /health/sandbox, CLI subcommand, platform_layer boundary (2026-05-25)

- **`platform/src/components/platform/HealthDashboard.jsx`**: Rewritten to
  render sandbox data the backend was already sending but the frontend was
  silently discarding. Added four new sections: Sandbox Posture (runner type,
  assurance class, requirement satisfaction, trust status, cert tier,
  platform/equivalence), Verification (method, kernel_observable, ceiling),
  Trusted Python (present flag, count, owner classes), and Runtime Conditions
  (conditional, shows code/classification/detail/component). Data paths:
  `health.plugin_sandbox_posture`, `health.sandbox_verification_posture`,
  `health.trusted_python_execution`, `health.runtime_conditions`.
- **`GET /health/sandbox`** (`AINDY/routes/health_router.py`): New dedicated
  endpoint (60/minute rate limit) returning 7 fields:
  `plugin_sandbox_posture`, `plugin_sandbox_platform`,
  `sandbox_verification_posture`, `trusted_python_execution`, `plugin_hosts`,
  `plugin_sandbox_attestation`, `runtime_conditions`. Integrators no longer
  need to parse the full `/health` blob. Test:
  `tests/api/test_version_api.py::test_health_sandbox_route_returns_posture`.
- **`aindy-runtime sandbox` CLI subcommand** (`AINDY/runtime_only.py`):
  `main()` now dispatches `sys.argv[1] == "sandbox"` to `_run_sandbox_check()`,
  which prints the full sandbox posture as JSON to stdout, exits 0 when
  requirements satisfied, 1 when not, 2 on unexpected error. 8 new tests in
  `tests/unit/test_runtime_cli.py` covering dispatch routing, exit codes, JSON
  validity, payload content, and error handling.
- **`AINDY/platform_layer/__init__.py` boundary enforcement**: `PUBLIC_MODULES`
  frozenset added as the machine-readable public surface. Enforced by
  `tests/unit/test_platform_layer_boundary.py` (3 tests: `PUBLIC_MODULES`
  matches `PUBLIC_API_CONTRACT.md`, `__all__` derives from `PUBLIC_MODULES`,
  every declared module has a `.py` file on disk). Prevents the three sources
  of truth — contract doc, `__init__.py`, and filesystem — from drifting
  independently.
- **`GET /health/sandbox` added to `docs/runtime/PUBLIC_RUNTIME_SURFACES.md`**
  under Experimental HTTP Surfaces.

### Changed — Non-breaking: `operator_note` field added to all `sandbox_runner_assurance_posture` branches (2026-05-25)

- **`AINDY/platform_layer/sandbox_runner.py` — `sandbox_runner_assurance_posture()`**: Added
  `operator_note` field to all four return branches. The note clarifies the relationship
  between `assurance_ceiling` (the highest tier the runner is structurally capable of
  reaching) and `verification_method` (the evidence method actually used), which are
  semantically distinct and easy to conflate when reading `/health/sandbox` or
  `/api/version` output.
  - `RUNNER_STRONG_SANDBOX_VM` kernel-observable branch: note states that both fields
    reflect achieved kernel-observable evidence.
  - `RUNNER_STRONG_SANDBOX_VM` worker-self-report branch: note states that both fields
    reflect live authenticated-RPC probe evidence.
  - `RUNNER_CONTAINERIZED_OCI`: note clarifies that `verification_method` is `"none"`
    because only `strong_sandbox_vm` runs post-launch probes; the ceiling reflects
    structural capability, not active probing.
  - `RUNNER_INSECURE_DEV_SUBPROCESS` (fallback): note states both fields reflect the
    absence of any sandbox boundary or isolation evidence.
- **`RUNNER_CONTAINERIZED_OCI` `ceiling_note` rewritten**: Previous text read
  `"Same limitation as strong_sandbox_vm."` — replaced with an accurate description:
  `"Container runner reaches worker-self-report-verified when probed; kernel-observable
  evidence is unavailable for shared-kernel container sandboxes."` No existing
  `assurance_ceiling` or `verification_method` values changed.
- **Non-breaking**: `operator_note` is an additive new field. No existing field values
  were modified (except `ceiling_note` on `RUNNER_CONTAINERIZED_OCI` which was
  corrected). Consumers reading only `assurance_ceiling` and `verification_method`
  are unaffected.

### Added — C2/NF-2, NF-8: Contract decision recorded and trust model matrix rewritten (2026-05-24)

- **`docs/runtime/EXTENSION_TRUST_MODEL.md` — Supported Platform Sandbox Matrix**
  (NF-8): Windows and macOS entries rewritten. Both now read
  `production-safe third-party plugin sandbox support: yes, when the configured
  container runtime is in Linux-containers mode`. Previous entries read `no`. New
  `container hardening` bullet for each platform describes that Linux kernel
  hardening controls (`no_new_privileges`, `drop_all_capabilities`, `pids_limit`,
  `seccomp`, `apparmor`, `selinux_label`) run inside the container's Linux kernel
  under the host virtualization layer and are not host-introspectable. New
  `degraded mode` entry for Windows describes Windows-containers mode fail-closed
  behavior. Linux and Other entries are unchanged.
- **`docs/runtime/EXTENSION_TRUST_MODEL.md` — Important Implications** (NF-8):
  Rewritten. "documented Linux containerized guarantees" → "documented Linux
  container guarantees, detected by querying `OSType`." Removed Linux-only
  framing for production-safe container support. Added explicit statement that
  non-Linux hosts can reach container-grade certification but not strong-sandbox
  or `hostile-third-party` certification. Added statement that `containerized_oci`
  on Windows and macOS in Linux-containers mode is production-safe for
  `single-instance`, `distributed-api`, and `distributed-worker` profiles.
- **`docs/runtime/EXTENSION_TRUST_MODEL.md` — Production-Safe Third-Party Plugin
  Sandbox Semantics** (NF-2): New subsection documenting the contract decision.
  Defines "production-safe third-party plugin sandbox" as a property of the
  container backend (`OSType=linux`), not the host OS. Documents the two
  detection conditions and their evaluation via `_detect_linux_container_backend`.
  Confirms strong-sandbox guarantees remain Linux-host-bound. Cross-references
  live verification evidence: `sandbox_certification_profile` returned
  `tier_status: certified` at `container-sandbox-certified` on Windows + Docker
  Desktop with all five hardening controls accepted by the container kernel.
- **`ISOLATION_MODEL_PLAN.md` Gap 4 and C2 reopen entries**: Annotated as
  CLOSED. Gap 4 retitled "PARTIALLY CLOSED — container-grade closed (C2,
  2026-05-24); strong-sandbox remains deferred (C3)." C2 reopen entry updated
  with closure evidence and C3 forward pointer.
- **`TECH_DEBT.md`**: Added closed C2 entry with live verification evidence.
  Added open C3 entry for cross-platform strong-sandbox as the appropriate
  follow-up gap, with its own reopen condition
  (`tier_status: certified` at `strong-sandbox-certified` on a non-Linux host).
- **This closes C2.** The C2 reopen condition — "a non-Linux host platform
  produces a sandbox runner type passing the shared worker policy certification
  suite with assurance class at or above `container-grade-sandbox`" — is met.
  C3 (strong-sandbox cross-platform parity) is now the tracked follow-up.

### Added — C2/NF-5: Certification suite proves container-sandbox-certified is platform-neutral (2026-05-24)

- **`TestContainerSandboxCertificationCrossPlatform`**
  (`tests/unit/test_plugin_sandbox_certification.py`): 11 new test cases proving
  `sandbox_certification_profile` reaches `tier_status: "certified"` at
  `container-sandbox-certified` on simulated Windows and macOS hosts (positive: Linux,
  Windows + Linux backend, macOS + Linux backend), and stays uncertified when conditions
  are not met (negative: Windows-containers mode, no runtime, no pinned digest,
  wall-clock-only limits). One parametrized diagnostic case (4 sub-cases) confirms each
  of the four `launch_attestation` verified fields — `backend_identity`, `runtime_identity`,
  `mount_mode`, `resource_limit_mode` — is independently required.
- **`sandbox_certification.py` required zero changes** — confirmed by audit: the function
  contains no `platform.system()` calls and reads `platform_matrix["current_environment"]`
  which is now a dynamic runtime-resolved dict produced by `_detect_linux_container_backend`.
  Tests inject both `runner_metadata` and `platform_matrix` as synthetic dicts so no
  subprocess calls or Docker invocations are made.

### Added — C2/NF-1, NF-4, NF-7: Non-Linux hosts with Docker Desktop in Linux mode become production-safe (2026-05-24)

- **`_platform_matrix_entry`** (`AINDY/platform_layer/sandbox_runner.py`): new
  `linux_container_backend_available: bool` parameter. The
  `production_safe_third_party_plugin_execution` field is now computed as
  `(linux AND runtime_available) OR (runtime_available AND linux_container_backend_available)`,
  allowing Windows and macOS hosts running Docker Desktop or Podman in Linux-container
  mode to receive `production_safe=True`. The Linux kernel-control reporting
  (`available_hardening_controls`) remains Linux-host-only (honesty principle: those
  controls are active inside the container VM but are not host-introspectable). New
  `degraded_modes` entries distinguish "Linux containers via host virtualization" from
  "container runtime present but not a Linux-container backend". `operator_note` updated.
- **`sandbox_platform_capability_matrix`**: calls `_detect_linux_container_backend` once
  and threads `linux_container_backend_available` into the `current_environment` entry;
  static platform entries (`supported_platforms`) pass `True` only for the Linux entry
  and `False` for all others (declared support model, not detection-dependent). New
  top-level key `current_container_backend_detection` surfaces the full detection result
  dict for operator visibility (e.g., `GET /api/version`). `support_contract` gains the
  new `production_safe_third_party_supported_host_platforms` key — the dynamically resolved
  list of platforms where the running runtime can deliver production-safe third-party plugin
  execution; starts as `["linux"]` and grows to include the current non-Linux platform when
  backend detection returns `linux_container_backend: True`. Existing
  `production_safe_container_supported_host_platforms` key is unchanged.
- **NF-6 auto-resolved**: `extension_execution_model_contract()` queries
  `production_safe_third_party_supported_host_platforms` from `support_contract` to
  populate `platform_support.production_safe_host_platforms` on the
  `dynamic-plugin-node:external-third-party` surface. That field now correctly reflects the
  active backend rather than silently resolving to `[]`.
- **`PRODUCTION_SAFE_CONTAINER_SUPPORTED_HOST_PLATFORMS`** constant is **unchanged** —
  it remains `(PLATFORM_LINUX,)` as the static declared support set.
- **Note**: `deployment_contract.py` validation paths continue to read the same
  `production_safe_third_party_plugin_execution` matrix field — they will benefit from this
  change automatically. NF-5 (certification suite tests on non-Linux platforms) is the next
  step to exercise that end-to-end path.
- **Unit tests** (`tests/unit/test_sandbox_runner.py`, `TestPlatformMatrixWithLinuxContainerBackend`):
  7 cases; plus 1 NF-6/NF-7 integration case confirming `extension_execution_model_contract()`
  populates `production_safe_host_platforms` on Windows with a Linux backend. Two existing
  platform-matrix tests updated to patch `subprocess.run` (determinism — `_detect_linux_container_backend`
  now shells out on non-Linux hosts).

### Added — C2/NF-3: Linux container backend detection helper (2026-05-24)

- **`_detect_linux_container_backend(container_runtime)`**
  (`AINDY/platform_layer/sandbox_runner.py`): new module-level helper that
  determines whether the configured container runtime is currently operating
  as a Linux-containers backend. Returns a structured result dict with
  `runtime`, `runtime_available`, `linux_container_backend`, `os_type`,
  `detection_method`, `detection_error`, and `operator_note` keys.
  Detection logic: on Linux hosts the binary presence alone is sufficient
  (`detection_method: "shutil_which_only"`); on non-Linux hosts the helper
  shells out to `{runtime} info --format '{{json .}}'` and inspects the
  `OSType` field (`detection_method: "docker_info_json"`). Fails closed on
  timeout, non-zero exit, `FileNotFoundError`, or JSON parse failure
  (`linux_container_backend: False`). Not yet wired into
  `_platform_matrix_entry` — that is NF-1 / NF-4.
- **Unit tests** (`tests/unit/test_sandbox_runner.py`,
  `TestDetectLinuxContainerBackend`): 9 cases covering Linux/Windows/macOS
  host paths, timeout, invalid JSON, non-zero exit, and unavailable runtime.

### Added — IDEM-9: EffectRecord TTL cleanup (2026-05-24)

- **Cleanup job** (`AINDY/platform_layer/scheduler_service.py`):
  `_cleanup_expired_effect_records()` registered as a 24-hour interval APScheduler job.
  Deletes finalized `effect_records` rows (status ≠ `pending`, `completed_at IS NOT NULL`)
  older than 90 days in batches of 10,000 rows per commit. Pending rows are never deleted.
  Stale pending rows (older than 1 hour) trigger a `WARNING` for operator visibility.
  Constants: `EFFECT_RECORD_TTL_DAYS=90`, `EFFECT_RECORD_CLEANUP_INTERVAL_HOURS=24`,
  `EFFECT_RECORD_DELETE_BATCH_SIZE=10_000`.
- **Migration 0004** (`alembic/versions/0004_effect_records_completed_at_index.py`):
  Adds `ix_effect_records_completed_at_status` — composite partial index on
  `(completed_at, status) WHERE completed_at IS NOT NULL` — to support the cleanup query
  at production volume. Idempotent (`IF NOT EXISTS`).
- **ORM model** (`AINDY/db/models/effect_record.py`): `ix_effect_records_completed_at_status`
  added to `EffectRecord.__table_args__`. `SCHEMA_CONTRACT_VERSION` bumped to "2026-05-24.1".
  `scripts/schema_version_baseline.json` regenerated.
- **Unit tests** (`tests/unit/test_effect_record_cleanup.py`): 6 tests — no-op path,
  finalized-row deletion, multi-batch loop, single-full-batch boundary, stale-pending
  warning, exception isolation.
- **Integration test** (`tests/integration/test_effect_record_cleanup_e2e.py`): 1 test —
  verifies expired row deleted, pending row preserved, recent row preserved against real
  Postgres. IDEM-9 closed in `TECH_DEBT.md`.

### Added — Idempotency layer: NF-1 through NF-5 (2026-05-24)

- **NF-1** (`AINDY/db/models/effect_record.py`, `alembic/versions/0003_effect_records.py`):
  New `EffectRecord` ORM model and Alembic migration 0003. Table stores per-syscall
  idempotency records keyed by `action_id` (SHA-256). `SCHEMA_CONTRACT_VERSION` bumped
  to "2026-05-24".
- **NF-2** (`AINDY/core/retry_policy.py`): `RetryPolicy` dataclass gains
  `execution_guarantee: str = "AT_LEAST_ONCE"` field. `AGENT_HIGH_RISK` constant sets
  `execution_guarantee="EXACTLY_ONCE"`. `_resolve_policy_for_eu()` in `execution_gate.py`
  serialises the field into `ExecutionUnit.extra["retry_policy"]`.
- **NF-3** (`AINDY/core/execution_gate.py`): `compute_action_id(action_type, input_payload, scope)`
  returns a deterministic SHA-256 hex digest used as the idempotency key.
- **NF-4** (`AINDY/runtime/nodus_adapter.py`, `AINDY/runtime/flow_engine/runner_steps.py`):
  `is_retryable_error()` wired into agent step and flow node retry loops. Non-transient
  errors (permission denied, 404, unauthorized, etc.) skip retry immediately.
- **NF-5** (`AINDY/kernel/syscall_dispatcher.py`): Idempotency gate inserted in
  `SyscallDispatcher._dispatch()` between Step 2e (deprecation check) and Step 3
  (handler execution). For `EXACTLY_ONCE` syscalls the gate checks `effect_records`
  before calling the handler; a cache hit returns the stored result without re-executing.
  AT_LEAST_ONCE syscalls are completely unaffected. Gate EU lookup is try/except wrapped
  (graceful skip if EU unavailable); EffectRecord write is a hard invariant.
  `_resolve_effect_record` and `_complete_effect_record` use `db.commit()` (not `flush()`)
  so pending and final EffectRecord states are durable across session close.
- **NF-1 fix** (`AINDY/db/models/effect_record.py`): `server_default` for the UUID primary
  key uses `text("gen_random_uuid()")` instead of a bare string literal, preventing
  SQLAlchemy from quoting it as a literal UUID value on Postgres.
- **Schema baseline** (`scripts/schema_version_baseline.json`): updated to reflect the
  corrected `effect_record.py` model hash at version "2026-05-24".

### Added — IDEMPOTENCY_CONTRACT.md (2026-05-24)

- `docs/runtime/IDEMPOTENCY_CONTRACT.md`: canonical contract for effect-level
  idempotency. Covers three enforcement layers (DB constraints, Alembic migration
  idempotency, NF-5 effect gate), 8 required invariants, EffectRecord state machine,
  action_id derivation contract, execution guarantee labels, interaction with
  EXECUTION_CONTRACT.md and RETRY_POLICY.md, exclusion scope, enforcement/verification
  matrix, and 5 open operational questions.

### Added — Platform UI sub-project (2026-05-24)

- `platform/` — standalone Vite + React 19 SPA (`@aindy/platform-ui`) that
  consumes `@aindy/ui-kit` for all shared surfaces. Replaces the previous
  arrangement where the monolith served platform components.
- 7 platform components copied and adapted: `AgentConsole`, `FlowEngineConsole`,
  `ObservabilityDashboard`, `HealthDashboard`, `AgentApprovalInbox`,
  `AgentRegistry`, `RippleTraceViewer`. `ExecutionConsole` replaced with a
  runtime-only stub (domain analytics panels are monolith-only).
- API modules (`agent.js`, `operator.js`, `platform.js`, `analytics.js`,
  `rippletrace.js`) present locally; `_core.js` and `_routes.js` re-export
  from `@aindy/ui-kit`.
- `ErrorBoundary.jsx` simplified for runtime: no `reportClientError` call.
- `AINDY/routing.py`: `StaticFiles(html=True)` mounted at `/platform` after
  `enforce_registered_route_execution` so platform API routes keep full
  priority. Mount is skipped gracefully if `platform/dist/` is absent.
- Build: `cd platform && npm install && npm run build` (must run before serving).

### Added — Alembic migration layer + idempotency fixes (2026-05-23)

- Added Alembic to `aindy-runtime` (`alembic==1.17.0` in deps). Runtime uses
  `alembic_version_runtime` table to avoid conflicts with monolith `alembic_version`.
- Migration `0001_runtime_baseline`: empty baseline — stamps existing schema-bootstrapped
  deployments at the Alembic split point.
- Migration `0002_idempotency_constraints`: closes IDEM-2, IDEM-3, IDEM-4, IDEM-5.
  Adds partial unique indexes on webhook_subscriptions, platform_api_keys, execution_units,
  dynamic_flows, dynamic_nodes. Includes deduplication step for existing data.
- `include_object` filter in `alembic/env.py` restricts autogenerate to runtime-owned tables.
- 3 new integration tests in `test_schema_contract.py` verify Alembic version table,
  head revision, and all idempotency indexes.

### Fixed — Idempotency audit findings (2026-05-23)

- **IDEM-1** (`AINDY/kernel/syscall_registry.py`): `VersionedSyscallRegistry.__setitem__`
  now raises `ValueError` on conflicting re-registration with a different handler.
- **IDEM-8** (`AINDY/apscheduler/schedulers/background.py`): Stub `BackgroundScheduler`
  now raises `ConflictingIdError` when `add_job()` is called with a duplicate id and
  `replace_existing=False`, matching real APScheduler behavior.

### Changed

- SDK extraction complete. `AINDY/sdk/` removed from `aindy-runtime`.
  `aindy-sdk` is now a standalone package at
  https://github.com/Masterplanner25/aindy-sdk-
  with its own CI, 47 passing tests, and independent release cycle.
  `aindy-runtime` no longer ships client code.

### Gap C1 Scope B1 - Kernel-Observable Post-Launch Verification On Linux (2026-05-23)

Gap C1 Scope B1 - Kernel-observable post-launch verification on Linux. Added
`AINDY/platform_layer/kernel_proc_reader.py` implementing unprivileged `/proc`
reads for seccomp status, cgroup membership, and namespace IDs.
`_verify_post_launch_state` now layers kernel evidence on top of the existing
RPC probe on Linux hosts. `verification_method` transitions to
`kernel-observable` and `assurance_ceiling` transitions to
`kernel-observable-verified` when evidence is available. Non-Linux hosts remain
at `worker-self-report-verified`. No new processes. No privilege escalation.

### Gap C1 Scope A - Machine-Readable Sandbox Verification Posture (2026-05-23)

Gap C1 Scope A - Sandbox verification posture now machine-readable. Added
`verification_method` (`worker-self-report`) and `assurance_ceiling`
(`worker-self-report-verified`) to `/api/version` sandbox capability metadata
and `/health` `sandbox_verification_posture`. Kernel-observable verification
(Scope B) remains deferred.

### Hygiene Pass — Dev Environment, CI Hardening, Subsystem Contract Tests (2026-05-23)

Four-item hygiene pass covering dev environment reliability, supply chain
security, and contract-level test coverage for three runtime subsystems.

**Item 1 — prometheus_client missing from dev install (no file change)**

`prometheus-fastapi-instrumentator>=6.1.0` was already in `pyproject.toml`
main `dependencies`. The dev environment had been set up with
`pip install -e .[test] --no-deps` (matching CI). Fix: run
`pip install -e .[test]` without `--no-deps` to pick up transitive deps.

**Item 2 — `.env.example` and `.gitignore`**

- `.env.example` created at repo root with five documented groups: required
  boot, boot mode, schema control, optional infrastructure, local smoke test.
- `.gitignore` updated to include `.env` (was missing).

**Item 3 — GitHub Actions SHA-pinning**

All floating action tags replaced with pinned commit SHAs across both
workflow files:

- `.github/workflows/runtime-ci.yml`: `actions/checkout@…# v4` (×4),
  `actions/setup-python@…# v5` (×4), `actions/cache@…# v4` (×1).
- `.github/workflows/release-staging.yml`: `actions/checkout@…# v4`,
  `actions/setup-python@…# v5`, `actions/upload-artifact@…# v4`.

**Item 4 — Subsystem contract tests**

Three new test files under `tests/unit/`, each marked `@pytest.mark.runtime_only`:

- `test_worker_contract.py` — 7 tests for `WorkerHealthServer`: construction,
  check registration, start/stop lifecycle (ephemeral port 0), HTTP 200/503/404
  response correctness, idempotent `start()`.
- `test_watcher_contract.py` — 13 tests for classifier (`classify()` covering
  idle/work/distraction/communication/unknown paths and browser title patterns),
  `VALID_SIGNAL_TYPES`, `VALID_ACTIVITY_TYPES`, `parse_timestamp()`, and
  `SessionTracker` state machine transitions through IDLE → CONFIRMING_WORK →
  WORKING with `session_started` event emission.
- `test_nodus_runtime_contract.py` — 5 tests for `AINDYMemoryBridge`
  (constructor, `_safe_node()` from dict, from object, null-tags default) and
  `AINDYNodusRuntime` subclass assertion (skipped if nodus-lang absent).

**SDK deferred:** `AINDY/sdk/` is a self-contained `aindy-sdk 1.0.0` package
(stdlib-only, own `pyproject.toml`, own `tests/`, own `examples/`). It does
not belong in this repo long-term. Documented in `TECH_DEBT.md`. SDK test
coverage intentionally omitted from this pass.

**Verification:** 245 passed, 1 skipped (up from 220/1). Non-zero coverage on
all three targeted subsystems.

---

### Contract Clarification — Tiered Isolation Model (2026-05-23)

Adopted the Tiered Isolation Contract vocabulary throughout runtime governance
docs. No code or test changes in this pass.

**What changed:**

- `docs/runtime/EXTENSION_TRUST_MODEL.md` — Introduced explicit Tier 1 /
  Tier 2 vocabulary. Renamed "Trusted Extension Classes" to "Tier 1 Trusted
  Kernel Code." Removed all "residual exception," "privileged exception," and
  "explicit privileged exception set" language. Added Tier 1 attestation
  exclusion paragraph to the Assurance Reporting section. Updated Operational
  Guidance to describe first-party manifest bootstrap as intentional Tier 1
  kernel code rather than a transitional exception.

- `docs/runtime/EXTENSION_CAPABILITIES.md` — Added "Tier Model Scope"
  subsection explicitly distinguishing Tier 1 registration gates from Tier 2
  execution confinement. Updated the manifest-bootstrap Enforcement entry to
  name Tier 1 kernel-resident execution.

- `docs/runtime/PUBLIC_RUNTIME_SURFACES.md` — Added Tier 1 / Tier 2 tier
  labels to the Extension Registration Surfaces section. Updated the Ownership
  model subsection to name each ownership class's tier. Removed the
  "extraction-era architecture" transitional framing from the experimental
  classification reason.

**What the tiered model says:**

- Tier 1 (trusted-operator kernel-resident): `runtime-built-in` and
  `first-party-app` bootstrap code, kernel-resident callables, and
  runtime-built-in plugin nodes run in the main interpreter by design.
  They are not exceptions to a more-isolated baseline.
- Tier 2 (third-party externalized): All `external-third-party` execution
  goes through the isolated plugin-host subprocess boundary. No exceptions.

**Deferred (documented in ISOLATION_MODEL_PLAN.md):**

- Strong-sandbox live verification (plan item C1)
- Cross-platform strong sandbox (plan item C2)

---

### Code Change — Two-Tier Execution Model Enforced in Contract and Tests (2026-05-23)

Removed the `capability-confined-in-process-exception` execution model class
and reclassified all Tier 1 surfaces. The published contract now exposes exactly
two execution model classes. The public contract test suite asserts the two-tier
model.

**What changed:**

- `AINDY/platform_layer/extension_execution_model.py` — Removed
  `EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION` constant and the
  corresponding third entry from `execution_model_classes`. Reclassified
  `manifest-bootstrap:runtime-built-in` and `manifest-bootstrap:first-party-app`
  `execution_model_class` from the removed constant to `EXECUTION_MODEL_KERNEL_RESIDENT`.
  Updated `registration_boundary` for all Tier 1 surfaces
  (`manifest-bootstrap:*`, `registry-kernel-callable:*`,
  `runtime-callback-worker:*`, `dynamic-plugin-node:runtime-built-in`) from the
  removed constant to `"registration-capability-gate"`. Updated
  `attestation_scope.plugin_sandbox_attestation.notes` and `operator_note` to
  use Tier 1 / Tier 2 vocabulary.

- `tests/unit/test_runtime_public_contract.py` — Updated
  `test_runtime_public_contract_publishes_extension_execution_model_matrix` to
  assert exactly two execution model classes (`"kernel-resident"` and
  `"isolated-externalized"`), assert `"capability-confined-in-process-exception"`
  does not appear in the contract, assert `manifest-bootstrap:runtime-built-in`
  and `manifest-bootstrap:first-party-app` surfaces have
  `execution_model_class = "kernel-resident"`, and assert
  `registry-kernel-callable:first-party-app` has
  `registration_boundary = "registration-capability-gate"`.

**Verification:** 220 passed, 1 skipped across the full test suite.

---

### Production Hardening - Dependency Pins, Async Context Coverage, and Schema CI (2026-05-23)

Pinned the remaining loose observability dependencies, added import-contract
coverage for the async execution context helper, and enforced schema contract
version bumps in CI when runtime-owned ORM models change.

**What changed:**

- `pyproject.toml` and `AINDY/requirements.txt` - Replaced the six loose
  lower-bound observability constraints with exact pins matching the currently
  installed working versions: `opentelemetry-api==1.42.1`,
  `opentelemetry-sdk==1.42.1`,
  `opentelemetry-instrumentation-fastapi==0.63b1`,
  `opentelemetry-exporter-otlp-proto-grpc==1.42.1`,
  `prometheus-fastapi-instrumentator==7.1.0`, and
  `python-json-logger==4.1.0`.

- `tests/unit/test_async_execution_context.py` - Added runtime-only tests for
  `activate_async_execution_context`, `deactivate_async_execution_context`, and
  `is_async_execution_active`. The module now has explicit coverage for import,
  default inactive state, activation, and token-based restoration.

- `scripts/check_schema_version.py` and
  `scripts/schema_version_baseline.json` - Added a standalone schema contract
  checker that hashes the runtime-owned ORM model sources
  (`AINDY/db/models/*.py` plus `AINDY/memory/memory_persistence.py`), imports
  `SCHEMA_CONTRACT_VERSION`, and fails when ORM definitions change without a
  matching version bump. The initial committed baseline records the current hash
  and version.

- `.github/workflows/runtime-ci.yml` - Added
  `python scripts/check_schema_version.py` to the `runtime-contracts` job after
  dependency installation and before pytest.

**Verification:** `pip install -e .[test]` succeeded. `pytest --tb=short -q`
passed at 249 passed, 1 skipped after the new async context tests. The
runtime-only `/api/version` smoke check still reported `boot_profile =
platform-only` and `app_plugins_loaded = false`. The schema checker created its
baseline, failed with the expected contract message when a model-file hash was
temporarily changed without a version bump, and returned to a clean pass after
reverting the temporary change.
