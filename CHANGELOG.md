# Changelog

## Unreleased

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
