# Changelog

## Unreleased

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
