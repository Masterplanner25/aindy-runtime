# Changelog

## Unreleased

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
