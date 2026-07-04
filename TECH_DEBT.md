# Technical Debt

## CLI-1 — Lazy settings getter deferred (post-1.0)

Status: Deferred — Low Priority

Settings() is called at module level in `AINDY/config.py` (line 316) and is load-bearing
for log initialization on the lines immediately below it. The 1.0.0 fix gave `DATABASE_URL`
a default of `""` so that import succeeds without configuration, but the module-level
instantiation remains. A proper fix (Option 1 from the CLI audit) would introduce a
`get_settings()` lazy getter and defer instantiation until first use, eliminating the
270+ module-level `settings.` call sites as a migration.

Why deferred: 279 usages across 36 files — not scope-appropriate for the 1.0.0 CLI fix.
The `DATABASE_URL = ""` default achieves the user-visible goal (--help works without env)
at zero consumer-side cost.

This pattern already required two workarounds in the 1.0.0 CLI fix:
1. `AINDY/runtime_only.py` uses module-level `__getattr__` to defer `from AINDY.main import app`
   so it doesn't pull in the database engine layer on `--help`.
2. `sandbox_verification_posture()` (in `health_service.py`) is guarded with try/except because
   `health_service` imports `AINDY.db` at module level.

Reopen triggers (any one is sufficient):
- A third "I had to add a try/except guard because a platform module imports settings
  transitively" instance surfaces. Two workarounds is a pattern; three is a signal the
  root cause needs addressing.
- CLI startup time becomes measurably slow — `Settings()` + log initialization run on
  every `--help` invocation including in CI hot loops.
- Multi-tenant or per-request config support requires settings isolation beyond a single
  module-level instance.

Resolution path: introduce `get_settings() -> Settings` that caches on first call; replace
all `settings.` call sites with `get_settings().`; gate log initialization inside a
`configure_logging()` function called from app startup, not module load.

---

## CLI-SANDBOX-FORMAT-1: aindy-runtime sandbox returns raw JSON to terminal

**Status:** CLOSED (2026-06-05)

**Implemented:**
- `_format_sandbox_summary(payload)` in `AINDY/runtime_only.py` — renders the full
  payload as a ~25-line human-readable summary: platform, highest tier, production-safe
  status, container backend detection + operator note, active runner/assurance/certification,
  requirements met, sandbox verification method, escape test posture (from
  `sandbox_escape_test_posture()`), trusted Python extension count, degraded modes list.
- Default `aindy-runtime sandbox` output is now human-readable.
- `aindy-runtime sandbox --json` restores the full machine-readable JSON (now also
  includes `escape_test_posture` key alongside the original five).
- `_run_sandbox_check(output_json=False)` — new parameter; `--json` flag wired through
  argparse `dest="output_json"`.
- Tests updated in `test_runtime_cli.py` (9 total pass): JSON tests updated to pass
  `output_json=True`; new `test_sandbox_check_default_produces_human_readable_summary`
  verifies the human-readable format; patch list extended with `sandbox_escape_test_posture`.

---

## IDEM-6 — Multi-Instance Bootstrap Race

Status: CLOSED (2026-06-05)

Source: `docs/runtime/IDEMPOTENCY_CONTRACT.md` Open Question #1.

Implemented: `pg_advisory_lock(_BOOTSTRAP_ADVISORY_LOCK_KEY)` wraps the blank-DB
bootstrap path in `reconcile_runtime_schema()` (`AINDY/db/schema_contract.py`).
The lock is acquired with a blocking call (waits rather than fails), the schema state
is re-inspected under the lock (TOCTOU guard — a second instance that wins the wait
finds the DB already bootstrapped and skips `create_all`), and the lock is explicitly
released in a `finally` block so it is freed even when `create_all` raises.

Lock key: `_BOOTSTRAP_ADVISORY_LOCK_KEY = 4149443900` (stable bigint, must not change).
SQLite paths are not affected (advisory lock is PostgreSQL-only; the check gates on
`not url.startswith("sqlite")`).

Regression coverage: 3 new unit tests in `tests/unit/test_runtime_schema_contract.py`
(`test_reconcile_blank_db_acquires_advisory_lock_for_postgres`,
`test_reconcile_blank_db_skips_create_all_when_another_instance_bootstrapped`,
`test_reconcile_blank_db_advisory_unlock_called_even_on_create_all_failure`).

---

## IDEM-7 — Syscall Registry Not-Ready Window

Status: CLOSED (2026-06-04)

Implemented: `SYSCALL_REGISTRY_MIN_COUNT = 17` added to `AINDY/kernel/syscall_registry.py`.
`_check_syscall_registry_status()` added to `AINDY/routes/health_router.py` and wired into
`/health/deep` — the response now includes `syscall_registry: {status, count, minimum_expected}`.

The timing-window risk (HTTP traffic before Phase 8) is already covered by the `startup_complete`
check in the readiness report (`get_readiness_report` in `health_service.py:800`) — the ready
endpoint returns 503 `startup_incomplete` until Phase 8 finishes and `publish_api_runtime_state`
sets `startup_complete=True`. The `/health/deep` addition makes the registry count visible to
operators and surfaces an `incomplete` status if a future registration is lost.

Regression coverage: `tests/unit/test_runtime_readiness_contract.py`.

---

## IDEM-9 — EffectRecord Table Growth

Status: CLOSED (2026-05-24)

Note: IDEM-8 is already taken (APScheduler stub fix, closed 2026-05-23 — see IDEMPOTENCY_AUDIT.md).

Implemented: `_cleanup_expired_effect_records()` in `AINDY/platform_layer/scheduler_service.py`.
Runs every 24 hours. Deletes finalized rows (status ≠ `pending`, `completed_at IS NOT NULL`)
older than 90 days in batches of 10,000 rows per commit. Pending rows are never deleted.
Supporting index: `ix_effect_records_completed_at_status` (migration 0004).
`SCHEMA_CONTRACT_VERSION` bumped to "2026-05-24.1".

Remaining operational gap: row-count monitoring must still be set up manually. No automated
alert exists. Add a dashboard panel or startup log line that surfaces `effect_records` total
row count so unbounded growth is detected without polling.

---

## C2 — Cross-Platform Container-Grade Sandbox

Status: CLOSED (2026-05-24)

Source: `ISOLATION_MODEL_PLAN.md` Gap 4 / `C2_SANDBOX_AUDIT.md`.

Reopen condition was: a non-Linux host platform produces a sandbox runner type passing
the shared worker policy certification suite with assurance class at or above
`container-grade-sandbox`.

Implemented: NF-1 through NF-7 in `AINDY/platform_layer/sandbox_runner.py` —
`_detect_linux_container_backend` helper, `linux_container_backend_available`
parameter in `_platform_matrix_entry`, and dynamic
`production_safe_third_party_supported_host_platforms` key in `support_contract`.
On Windows + Docker Desktop in Linux-containers mode,
`sandbox_platform_capability_matrix()` reports
`production_safe_third_party_plugin_execution: True` and
`_detect_linux_container_backend` returns
`linux_container_backend: True, detection_method: docker_info_json`.

Live verification (2026-05-24, Windows + Docker Desktop): `sandbox_certification_profile`
returned `tier_status: certified` at tier `container-sandbox-certified` with all four
attestation fields launch-verified (backend identity, runtime identity, mount mode,
resource limit mode). `docker run` argv included `--cap-drop ALL`,
`--security-opt no-new-privileges`, `--read-only`, `--network none`, and
`--pids-limit`, all accepted by the container kernel.

Documentation: `docs/runtime/EXTENSION_TRUST_MODEL.md` Supported Platform Sandbox
Matrix rewritten (NF-8). NF-2 contract decision documented in the new
"Production-Safe Third-Party Plugin Sandbox Semantics" subsection.

---

## C3 — Cross-Platform Strong Sandbox

Status: PHASE 0 COMPLETE (2026-06-04) — Phases 1-4 open

Source: `C2_SANDBOX_AUDIT.md` "What This Audit Does NOT Cover" / `ISOLATION_MODEL_PLAN.md` Gap 4 (C3 remainder).

Strong-sandbox and `hostile-third-party` profile support remains Linux-only.
`STRONG_SANDBOX_SUPPORTED_HOST_PLATFORMS = (PLATFORM_LINUX,)` and
`HOSTILE_THIRD_PARTY_SUPPORTED_HOST_PLATFORMS = (PLATFORM_LINUX,)` are unchanged.
Non-Linux hosts can reach `container-sandbox-certified` (C2 — closed) but not
`strong-sandbox-certified`. Closing requires platform-specific sandbox runtimes
(Windows Containers, WSL-mediated isolation, macOS Virtualization.framework).

**Phase 0 (2026-06-04) — Adversarial escape test suite: COMPLETE**

Created `tests/sandbox/` with 17 adversarial escape tests across 6 categories,
gated under `pytest -m sandbox_escape`. Tests prove the existing Linux
container-grade sandbox claim with real Docker invocations (no mocking). Each test
documents exactly what was tested and why the specific vector matters.

Categories and test counts:
- Filesystem escape (3): read-only rootfs, read-only bind mount, tmpfs isolation
- Network escape (3): TCP outbound, UDP outbound, loopback-only kernel evidence
- Process escape (2): pids limit enforcement, cgroup kernel evidence (Linux-only)
- Privilege escalation (4): CAP_NET_RAW, CAP_CHOWN, no-new-privileges /proc evidence, combined (Linux-only)
- Host env leak (2): sensitive keys absent, allowlist verification
- Path boundary (3): unmounted dir inaccessible, plugin root accessible, path traversal stays in container

Result artifact: `tests/sandbox/sandbox_escape_results.json` — written at session end.
Marker: `sandbox_escape`. Image: `python:3.11-alpine` (configurable via `SANDBOX_ESCAPE_IMAGE`).
Platform note: all tests run on any platform with Docker Linux containers; Linux-only kernel
control tests (privilege escalation, process/pids) skip on non-Linux backends.

**Phase 1 (2026-06-06) — WSL2/Windows Linux backend detection: COMPLETE**
Implemented `_detect_wsl2()` in `sandbox_runner.py`. Detects two cases: (1) Python
process running inside WSL2 (Linux host, `/proc/version` contains "microsoft"); (2) Windows
host with Docker Desktop Linux container backend (WSL2 or Hyper-V, from `docker info`).
Updated `_supports_linux_container_kernel_controls()` to accept `linux_container_backend`
parameter. Updated `inspect_container_kernel_controls()` to pass it through, enabling
`no_new_privileges`, `drop_all_capabilities`, and `pids_limit` to be reported as supported
and active on Windows + Docker Desktop Linux containers mode. `seccomp_profile`,
`apparmor_profile`, and `selinux_label` remain native-Linux-host-only (not tested in Phase 0).
`ContainerizedOciSandboxRunner` caches backend detection at construction time.
`sandbox_platform_capability_matrix()` now includes `current_wsl2_detection` field.
Platform matrix hardening controls split: basic kernel controls available when
`linux_container_backend_available=True`; profile controls Linux-host-only.
21 new unit tests in `tests/unit/test_sandbox_runner.py`.

Gap remaining: strong sandbox VM (`RUNNER_STRONG_SANDBOX_VM`) still requires native Linux
or WSL2-native Python (when `platform.system() == "Linux"`). A Windows-native path to the
strong sandbox tier requires a Windows `aindy-sandbox-vm` binary that bridges to WSL2 —
out of scope until the launcher exists.

**Phase 2 (2026-06-06) — macOS Docker Desktop Linux backend detection + policy: COMPLETE**
Extended `_detect_wsl2()` to handle macOS: new `docker_macos_backend` field detects
Docker Desktop with Linux container backend (Apple Virtualization Framework or HyperKit)
via `docker info`. `wsl2_kernel_available` is now True on macOS + Docker Desktop Linux mode.
Updated static platform matrix entries for Windows and macOS: both now show
`linux_container_backend_available=True` (Docker Desktop on both platforms supports Linux
containers). Static matrix now correctly reports `no_new_privileges`, `drop_all_capabilities`,
`pids_limit` as available hardening controls for both platforms.
Policy document created: `docs/runtime/MACOS_CONTAINER_POLICY.md`. Records what IS and is
NOT claimed (seccomp/AppArmor/SELinux not claimed — not tested), assurance tier
(container-grade, not strong-sandbox-vm).
2 new unit tests in `tests/unit/test_sandbox_runner.py` (64 total).

**Phase 5 (2026-06-06) — macOS CI certification workflow: COMPLETE**
`.github/workflows/macos-sandbox.yml` added (PR merged 2026-06-06). `workflow_dispatch`
job targets `macos-14` (Apple Silicon), installs Colima as the Linux-backend Docker
provider, and runs `pytest -m sandbox_escape -v` against the full 17-test escape suite.
Uploads `sandbox_escape_results.json` as a workflow artifact. macOS escape suite
certification is now gated through CI — run the workflow before each macOS deployment.

**Phase 3 (2026-06-05) — Formal threat model + sandbox_escape_test_posture(): COMPLETE**
Created `docs/runtime/SANDBOX_ESCAPE_AUDIT.md` (append-only log, Entry 001 committed).
Each escape vector maps to a threat model entry documenting threat, control, and failure
interpretation. `sandbox_escape_test_posture()` added to `sandbox_runner.py` — reads
`tests/sandbox/sandbox_escape_results.json`, returns structured posture dict (posture,
last_run, host_platform, coverage, gaps, operator_note). Returns `"not_run"` gracefully
when artifact is absent (production install without tests/).

**Phase 4 (2026-06-05) — Release gate: COMPLETE**
Step 16 added to `docs/runtime/RELEASE_CHECKLIST.md`. Gate condition:
`sandbox_escape_test_posture()["posture"] == "all_pass"`. Skips acceptable; FAILs block.
Audit trail instruction added: append to SANDBOX_ESCAPE_AUDIT.md after each pre-release run.

Trigger: when there is a platform-specific sandbox runtime delivering strong-sandbox-tier
assurance on a non-Linux host.

Condition to close C3 fully: A non-Linux host platform gains a supported sandbox runner type
with assurance class `strong-sandbox-tier`, verified through the escape test suite and the
shared worker policy certification suite (`tier_status: certified` at `strong-sandbox-certified`).

---

## PACK-DEBT-1 — Nodus Pin Staleness

Status: CLOSED (2026-05-25)

**Resolution:** Pin bumped to `nodus-lang==3.0.2` in `pyproject.toml` and
`AINDY/requirements.txt`. `AINDYNodusRuntime` updated to match the 3.0.2 base class API:
`initial_globals` now forwarded to `load_module_from_source` / `load_module_from_path`
(was silently dropped — caused "Undefined variable" for `state`, `user_id`, etc. in
worker scripts); error handling now returns `Result.failure()` dict instead of raising,
matching the base class contract and preserving captured stdout on script error;
`HostFunctionError` unwrapped before the generic error handler.

The class is retained for AINDY-specific extensions that are not in the base class:
`register_function` stdlib aliases (`recall_from`, `recall_all`, `share`); auto
`project_root` fallback to the bundled stdlib directory; bare `import memory` rewriting.

**Investigation findings (2026-05-25):**

Nodus is at `3.0.2`. The gap spans
v1.1.2, v2.0.0, v2.0.1, v2.1.0, v2.1.1, v3.0.0, v3.0.1 — two full major versions.

**Audit completed 2026-05-25.** Import surface in `AINDY/` is entirely in the
embedding/VM layer, concentrated in `AINDY/nodus/runtime/aindy_runtime.py`:
`NodusRuntime`, `ModuleLoader`, `VM`, `coerce_error`, `BuiltinInfo`, `Result`,
`normalize_filename`, `capture_output`, `configure_vm_limits`.
Additional probe-only imports in `health_router.py` and `runtime/__init__.py`
(hasattr checks only — not affected by any breaking change).

**Breaking changes that require action before bumping the pin:**

1. **v2.1.1 CRITICAL — `allowed_paths` sandbox bypass (SECURITY).**
   Stdlib wrappers (`std:fs`) were not forwarding `allowed_paths` from the calling
   VM, allowing sandboxed scripts to read arbitrary paths via stdlib calls.
   `aindy_runtime.py` constructs `VM(..., allowed_paths=self.allowed_paths)` — the
   sandboxing intent is present but the fix is only in v2.1.1+. Any use of
   `allowed_paths` for security isolation is currently ineffective at the stdlib
   boundary. **Must be resolved before any deployment relying on path sandboxing.**

2. **v2.1.0 BUG-005 — `NodusRuntime.run_source` raises vs. returns divergence.**
   v2.1.0 changed `NodusRuntime.run_source` to return `{"ok": false, "error": ...}`
   on script error instead of raising. `nodus_flow_compiler.py:255` checks
   `result.get("ok")` — written for the post-v2.1.0 contract. On v1.1.0, script
   errors raise before the check is reached; the caller at `nodus_adapter.py:882`
   catches `(ValueError, RuntimeError)`, but Nodus v1.1.0 exception types may not
   match. `AINDYNodusRuntime.run_source()` is unaffected — it overrides the method
   completely and still raises `coerce_error(...)`, which is the correct shape for
   its callers (`nodus_worker.py` catches `Exception`).

3. **v3.0.0 — err.kind taxonomy changed.**
   `coerce_error` in `aindy_runtime.py:155` coerces Python exceptions to Nodus
   errors. The kind taxonomy changed: `"runtime"` splits into `"io_error"`,
   `"parse_error"`, `"runtime_error"`, etc. No code in aindy-runtime currently
   inspects `.kind` on the raised error (confirmed by grep — all `.kind` uses are
   Python `inspect.Parameter.kind` or manifest fields). Low callsite impact; the
   error message strings seen at the HTTP layer will change.

4. **v3.0.0 — Integer type introduced.**
   Nodus scripts that check `type(x) == "number"` will break — integers are now a
   distinct type. This is a script-level concern; the Python embedding API is
   unaffected. User-authored `.nodus` scripts must be audited.

5. **v3.0.1 BUG-E04 — `HostFunctionError` sentinel for host function exceptions.**
   Python exceptions raised by host-registered functions (registered via
   `register_function`) now propagate as `HostFunctionError` (from
   `nodus.runtime.diagnostics`) rather than propagating directly. The `except
   Exception as err` handler in `aindy_runtime.py:154` catches it. `coerce_error`
   on a `HostFunctionError` may produce different error detail than before.
   Verify error messages surfaced to users remain meaningful.

**Cleanup opportunity:** COMPLETED — see OVERRIDE-DRIFT-1 below.

**Resolution path:**
1. Bump `nodus-lang==1.1.0` → `nodus-lang==3.0.1` in `pyproject.toml`.
2. Delete `AINDYNodusRuntime` and update all import sites to `NodusRuntime`.
3. Verify `nodus_flow_compiler.py` error path: test that a bad flow script surfaces a
   `ValueError` with a readable message (not a raw Nodus exception).
4. Audit user-authored `.nodus` scripts for `type(x) == "number"` — rename to
   `type(x) == "integer"` or `type(x) == "float"` as appropriate.
5. Run the full test suite and the Nodus-specific integration tests.
6. Manually verify that `allowed_paths` sandboxing is effective after the bump
   (create a test script that attempts `std:fs` access outside allowed paths).

Trigger: must be resolved before tagging 1.0.0.

---

## OVERRIDE-DRIFT-1 — AINDYNodusRuntime override class deleted

Status: CLOSED (2026-05-25)

Derived from PACK-DEBT-1 cleanup. `AINDYNodusRuntime` in
`AINDY/nodus/runtime/aindy_runtime.py` was a `NodusRuntime` subclass written to patch
BUG-E03 (`host_globals` not forwarded to `ModuleLoader` in nodus-lang 1.1.0). With the
pin bumped to 3.0.2 (PACK-DEBT-1), the subclass provided no upstream-bug-patch value and
was the source of three documented divergences:

1. **initial_globals dropped** — `AINDYNodusRuntime.run_source` constructed the VM with
   `initial_globals` but the value was overwritten by `vm.reset_program` in
   `_execute_module`. Fixed inline 2026-05-25 before this deletion, confirmed working.
2. **Raise vs. return semantics** — `AINDYNodusRuntime.run_source` returned a failure
   dict on error, but the override's error handling had diverged from the base class
   contract. Aligned to base class behavior 2026-05-25; base class now owns the contract.
3. **HostFunctionError double-wrap** — `AINDYNodusRuntime.run_source` included an
   explicit `except HostFunctionError as wrapped: raise wrapped.cause` guard, which
   could have produced inconsistent exception wrapping if not perfectly aligned with the
   base class's own guard. Resolved automatically by this deletion — the base class
   handles it correctly.

**What was inlined into `nodus_worker.py` (AINDY/runtime/nodus_worker.py):**
- `project_root` defaulting to `_STDLIB_DIR` (bundled stdlib) — now passed explicitly
  at the `NodusRuntime(project_root=...)` instantiation site.
- `register_function` stdlib aliases (`recall_from` → `__memory_stdlib_recall_from`,
  `recall_all` → `__memory_stdlib_recall_all`, `share` → `__memory_stdlib_share`) —
  now registered as three explicit `register_function` calls in the worker.
  These aliases are load-bearing: `AINDY/nodus/stdlib/memory.nd` calls the `__*` names
  directly.
- Bare `import memory` → `import "memory" as memory` rewriting — now applied to
  `script` before calling `runtime.run_source`.

**Additional change:** `_runtime_emitted_events()` in the worker now reads from
`runtime.last_vm.event_bus.events()` (base class exposes `last_vm`). The override had
populated `runtime.last_emitted_events` as a list of dicts; the base class never set
that attribute, so we switched to the standard event bus path.

**Files changed:**
- `AINDY/runtime/nodus_worker.py` — import + instantiation + aliases + rewriting + event collection
- `AINDY/nodus/runtime/embedding.py` — AINDYNodusRuntime removed from re-export shim
- `AINDY/nodus/runtime/aindy_runtime.py` — class body replaced with deprecation doc comment
- `tests/unit/test_nodus_runtime_contract.py` — `test_aindy_nodus_runtime_subclasses_nodus_runtime` removed (tested class existence, not behavior)

---

## PACK-DEBT-2 — Auth Dependency CVE Policy

Status: CLOSED (2026-05-25)

Implemented:
- `security` optional-dependencies group added to `pyproject.toml` — declares
  `pip-audit>=2.7.0` plus floor pins for `bcrypt>=4.0.1`, `passlib>=1.7.4`,
  `python-jose>=3.5.0`.
- `.github/workflows/security-audit.yml` — pip-audit (OSV-backed) runs on every
  PR and on a weekly cron schedule (Mondays 08:00 UTC). Fails CI on any detected CVE.
  Produces an `audit-results.json` artifact. Exemptions via `--ignore-vuln <GHSA-ID>`
  with mandatory comment documentation.
- `.github/dependabot.yml` — enabled for `pip` and `github-actions` ecosystems,
  weekly cadence. Secondary signal for transitive deps and stale SHA pins.
- `docs/runtime/SECURITY_POLICY.md` — new file. Documents SLA (Critical: 7 days,
  High: 14 days, Medium: next minor, Low: next major), exemption process, and
  accepted-findings register.

---

## PACK-DEBT-3 — No mypy Baseline

Status: CLOSED (2026-05-25) — Decision: do not pursue mypy at this time.

The dominant bug class observed across this codebase is contract drift between
modules, repos, and layers — registry implementation vs execution-model docs,
frontend vs backend sandbox fields, SDK vs runtime surfaces. The audit arc and
contract test suite address this class directly. mypy's primary value is signature
drift within a module, which has not been the observed failure mode. Adopting mypy
now would impose ongoing annotation maintenance cost (plugin-host dynamic dispatch
friction, capability registry typing) for marginal coverage of the bugs actually
being shipped.

Reopen triggers:
- A second engineer joins the project, OR
- A contributor PR introduces a signature-drift bug that audit-arc misses and a
  type-checker would have caught.

On reopen: start with `aindy-sdk` (smaller surface, cleaner boundaries) before
`aindy-runtime`. Use `--strict` on new code only; document a phased adoption plan.

---

## PACK-DEBT-4 — Integration Tier Uses `continue-on-error: true`

Status: CLOSED (2026-05-25)

`continue-on-error: true` removed from the `integration-postgres` job in
`runtime-ci.yml`. Integration failures now block CI green.

Rationale: advisory-only integration tests provide weak signal. If integration
coverage is worth running, it is worth gating on. If flakes materialize, they are
investigated as real signals rather than silenced by restoring the bypass.

Followup posture: if a flake appears within the first two weeks, investigate root
cause (test isolation, container startup race, fixture cleanup) rather than restoring
`continue-on-error`. If genuinely environmental and unfixable, open a new TECH_DEBT
entry rather than re-disabling the gate.

---

## PACK-DEBT-5 — starlette 0.49.1 / FastAPI 0.121.0: PYSEC-2026-161 host-header CVE deferred

**Status:** CLOSED (2026-06-05)

**Implemented:** Upgraded `fastapi` 0.121.0 → 0.135.0, `starlette` 0.49.1 → 1.0.1, and
`prometheus-fastapi-instrumentator` 7.1.0 → 8.0.0 (7.x required `starlette<1.0.0`; 8.0.0
requires `starlette>=1.0.0,<2.0.0`). Pins updated in both `AINDY/requirements.txt` and
`pyproject.toml`. `--ignore-vuln PYSEC-2026-161` removed from `security-audit.yml`.
PYSEC-2026-161 Accepted Findings entry removed from `docs/runtime/SECURITY_POLICY.md`.
Unit tests pass; no API-level breakage detected (direct starlette usage in the codebase
is limited to `starlette.exceptions.HTTPException` — a stable import).

---

## DEBT-COMPAT-1 — Cross-version compatibility story between runtime and SDK

**Status:** Deferred — Low Priority
**Trigger condition:** When two runtime versions exist in the wild
simultaneously (e.g., a 1.0 cloud runtime serving users whose local
SDKs are still on a 0.x version, or vice versa).

**Context:** Today, the runtime and SDK ship at matching versions and
the compatibility contract is implicit. Under the local + cloud
distribution model (see ARCHITECTURE.md), this implicit contract
becomes load-bearing: a cloud runtime at v1.1 may serve users whose
local SDKs are v1.0, and the runtime's declared HTTP surface
(`/health/sandbox`, `/flow/run`, etc.) must remain compatible across
those versions.

**Resolution path when reopened:** Define a compatibility window
policy (e.g., "the SDK at version N is supported against runtimes
at versions N through N+2"). Add automated cross-version testing
that exercises older SDK versions against newer runtime versions.
Document the policy in PUBLIC_API_CONTRACT.md.

**Why deferred:** Only one version of each exists today. The
infrastructure to test cross-version compatibility is non-trivial,
and the policy needs to be informed by actual release cadence and
deprecation philosophy, neither of which is settled.

---

## TENANT-2 — Per-tenant quota limits not configurable; `quota_group` has no enforcement

Status: Deferred — Low Priority

Source: `docs/runtime/LOCAL_AND_CLOUD_AUDIT.md` Area A, finding TENANT-2.

`MAX_CONCURRENT_PER_TENANT = 5` is a process-wide constant overridable only via
`AINDY_QUOTA_MAX_CONCURRENT` env var, not per-billing-tenant. The `quota_group`
column on `execution_unit` accepts policy tags ("premium", "batch") but nothing
reads this field to adjust quota behavior. In a cloud multi-tenant context,
different tenants need independently configured concurrency ceilings.

Resolution path:
- Build enforcement for `quota_group` as a policy lookup key, OR
- Add a per-tenant concurrency limit table driven by control-plane configuration.

Trigger: when cloud onboarding begins.

---

## COMPAT-2 — No deprecation or forward-compatibility policy for extension ABI

**Status:** CLOSED (2026-06-15)

**Resolution:** Added "Deprecation and Forward-Compatibility Policy" section to
`docs/runtime/EXTENSION_ABI.md`. Stable ABI versions get a minimum two-minor-release
support window after a newer stable version ships, with the deprecated version flagged
in `GET /api/version` under `public_contract.extensions.abi.deprecated_versions`.
Experimental ABI versions (`v1alpha*`) explicitly carry no support window and may be
removed in any release. Policy triggers on first stable ABI promotion or experimental
surface promotion to stable.

---

## DATA-1 — No data residency mechanism

Status: Deferred — Low Priority

Source: `docs/runtime/LOCAL_AND_CLOUD_AUDIT.md` Area D, finding DATA-1.

No `AINDY_DATA_REGION` env var or equivalent exists. Cloud operators in regulated
industries (GDPR, HIPAA, SOC 2 Type II) need to declare which region data is stored
in and enforce that writes stay within that boundary.

Resolution path:
- Define an `AINDY_DATA_REGION` env var and expose it in the deployment contract.
- Actual region-routing enforcement requires control-plane work outside this repo.

Trigger: when cloud onboarding begins or when a regulated operator requires it.

---

## LOCAL-1 — No documented production upgrade path for local installs

Status: CLOSED (2026-06-05)

Added `## Upgrading` section to `README.md` covering: `pip install --upgrade`,
version verification via `aindy-runtime --version` / `/api/version`, the
`AINDY_SCHEMA_RECONCILE=true` restart sequence for schema-bumping releases,
Docker Compose pull-and-up flow, and rollback guidance (reinstall previous
version; note that rolling back across a schema change requires a DB restore).

---

## DEPLOY-TARGET-1 — Cloud deployment manifests not authored

**Status:** Deferred — pre-cloud-launch

The shortest path to a single-operator hosted deployment is translating
`docker-compose.prod.yml` into a platform-specific deployment manifest. The compose
file is effectively already the spec; the work is translation and cloud-Postgres
integration testing, not architecture.

Candidate platforms (in order of fit):
- **Railway** — `railway.json` / `railway.toml`; native Postgres with pgvector plugin
- **Render** — `render.yaml`; managed Postgres add-on; docker-compose import supported
- **Fly.io** — `fly.toml`; more regional control; pgvector via Supabase or Fly Postgres
- **Digital Ocean App Platform** — YAML spec; managed Postgres; no nginx needed (TLS built-in)

Required env vars at deploy time: `DATABASE_URL`, `SECRET_KEY`, `OPENAI_API_KEY`,
`AINDY_BOOTSTRAP_ADMIN_EMAIL`, optionally `AINDY_REDIS_URL`.

Source: `docs/runtime/DEPLOYMENT_TARGETS.md`.

**Reopen trigger:** When first cloud deployment is planned.

---

## DEPLOY-TARGET-2 — Multi-tenant SaaS readiness gate

**Status:** Deferred — trigger when first multi-tenant customer onboards

When the deployment target shifts from "hosted for a single operator" to "multiple
paying operators sharing one runtime deployment," the following TENANT-* findings
from `LOCAL_AND_CLOUD_AUDIT.md` become load-bearing and must be resolved in sequence:

1. **TENANT-1** — `tenant_id == str(user_id)` by convention; must be rebased onto a
   control-plane-issued `billing_account_id` before billing isolation is meaningful.
2. **TENANT-2** — `quota_group` on `execution_units` has no enforcement path; per-tenant
   concurrency limits and feature gates require this to be built.
3. **TENANT-3** — event bus is a single shared Redis channel; WAIT/RESUME events for
   tenant A must not broadcast to tenant B's processes.
4. **TENANT-4** — OCI container resource limits are global env vars; must become
   per-tenant to prevent noisy-neighbor problems.

None of these are blocked by architectural debt — the hooks are seeded. This is
deliberate work that begins only when the first multi-tenant customer is ready.

Source: `docs/runtime/DEPLOYMENT_TARGETS.md`. Related: `BILLING-1` (billing identity).

**Reopen trigger:** When first multi-tenant operator onboards.

---

## BILLING-1 — Billing identity: tenant_id not decoupled from user_id

**Status:** Deferred — trigger when first multi-seat customer onboards

`tenant_id` on `ExecutionUnit` is set by convention: `str(user_id)`. A commercial
billing model requires a billing account identity that is independent of individual
users — one paying account may contain multiple users (team seats). The `User`
model has no `billing_account_id`, `plan_tier`, or external billing reference field.

Resolution direction: introduce a `billing_account_id` field on `User` (or a
`BillingAccount` model) issued by the control plane at registration. Rebase
`tenant_id` onto this identifier. This unblocks BILLING-3 (plan enforcement) and
DEPLOY-TARGET-2 (multi-tenant SaaS).

Source: `docs/runtime/MONETIZATION_AUDIT.md` Area A, finding BILLING-1.

**Reopen trigger:** When first multi-seat team plan or control-plane integration begins.

---

## BILLING-2 — Metering model not chosen

**Status:** Deferred — decision required before billing infrastructure is built

Three viable billing models exist (per-seat, per-agent-run, usage-based compute),
each with different data sources and customer-facing complexity. The `AgentRun`
table is the clearest natural unit; the recommendation in the monetization audit
is per-agent-run with a seat-based floor for team plans. This decision must be
made before any billing backend is integrated.

Source: `docs/runtime/MONETIZATION_AUDIT.md` Area B, finding BILLING-2.

**Reopen trigger:** Before billing infrastructure or Stripe integration begins.

---

## BILLING-3 — No plan-tier enforcement path

**Status:** Deferred — trigger when first paid plan is defined

Even when a billing model is chosen and a control plane issues plan tiers, the
runtime has no enforcement mechanism. Every operator has identical access regardless
of plan. The enforcement path requires:

1. A `plan_tier` field on the user (populated from the control plane)
2. A `require_plan(tier)` FastAPI dependency factory (analogous to `require_admin_principal`)
3. A quota policy lookup that translates `quota_group` into concrete per-tenant limits

The `quota_group` field on `execution_units` is the right enforcement hook (seeded
but unread). `TENANT-2` in `TECH_DEBT.md` tracks the enforcement-path gap at the
infrastructure level; BILLING-3 extends it into the commercial billing context.

Source: `docs/runtime/MONETIZATION_AUDIT.md` Area C, finding BILLING-3.

**Reopen trigger:** When the first paid plan tier is defined.

---

## BILLING-4 — No self-service acquisition funnel

**Status:** Deferred — trigger before first paid customer onboards

Current onboarding requires direct operator involvement (register → manual admin
promotion). A commercial funnel requires: register → plan selection → Stripe payment
→ control plane webhook → auto-promotion with plan tier set. Steps 1 and the final
SPA redirect already work; steps 2-4 require a separate control plane service (not
in this repo) that calls internal runtime admin APIs.

The runtime's side of this contract: a `set-plan-tier` internal admin endpoint
(analogous to `auth promote-admin`) callable by the control plane via internal API
key. The commercial logic (Stripe, webhooks, pricing pages) lives outside this repo
to preserve self-hostability.

Source: `docs/runtime/MONETIZATION_AUDIT.md` Area D, finding BILLING-4.

**Reopen trigger:** Before first paid customer onboards.

---

## BILLING-5 — No usage reporting surface

**Status:** Deferred — trigger when first plan with usage limits ships

Customers on any metered or capped plan need a usage view before they are surprised
by an overage or renewal invoice. The data is available (`AgentRun` count,
`ExecutionUnit.wall_time_ms` aggregate, `memory_nodes` count), but no
`GET /platform/billing/usage` endpoint or billing-period concept exists.

Minimum viable: a read-only admin endpoint returning current-period agent run count,
compute wall time, and memory record count relative to plan limits. Requires a
billing period start date on the billing account model (BILLING-1 dependency).

Source: `docs/runtime/MONETIZATION_AUDIT.md` Area E, finding BILLING-5.

**Reopen trigger:** When first metered plan with usage limits ships.

---

## LINT-VERSION-GAP-1: eslint major version asymmetry across ui-kit and apps-monolith

**Status:** Tracked, accepted. Soft commitment to align on next maintenance pass.

**Context:** `@aindy/ui-kit` is on `eslint@^10.4.0`. `aindy-apps-monolith` (the primary consumer) is on `eslint@^9.36.0`. Both use flat config and share the `eslint-plugin-react-hooks` plugin (ui-kit on `^7.1.1`, apps-monolith on `^5.2.0` — independent version tracks).

**Posture:** Library leads consumer by one major version. This is the structurally correct direction (library lagging consumer is the bad shape — it would block consumer upgrades). The asymmetry is currently cosmetic; no rules in ui-kit's eslint 10 config are unavailable in eslint 9, and no plugin in the shared set has a peer-deps conflict.

**Cross-ref:** Same finding tracked in `aindy-apps-monolith/TECH_DEBT.md` as LINT-VERSION-GAP-1 (apps-monolith side).

**Commitment:** ui-kit will not adopt a lint rule that fails to express under eslint 9 until apps-monolith is aligned. If a desired rule is eslint-10-only, that triggers the apps-monolith upgrade rather than a divergent ui-kit config.

**Reopen trigger:** (a) apps-monolith next maintenance pass — bump to eslint 10 as a side-task, OR (b) ui-kit wants an eslint-10-only rule, OR (c) `eslint-plugin-react-hooks` 7.x backports a rule that apps-monolith wants and requires the eslint major bump.

**Estimated effort on apps-monolith bump:** ~30 minutes (verified: react-hooks 5.x supports eslint 9 and 10; no forced plugin bumps; `eslint-plugin-react-refresh@^0.4.22` is the main compatibility verification needed).

---

## EVENTBUS-REDIS-URL-CONSOLIDATION-1 — Deprecate AINDY_REDIS_URL alias

**Status:** CLOSED (2026-06-06)

Removed `AINDY_REDIS_URL` from `event_bus.py` (function simplified, `import warnings`
dropped), `config.py` (field removed), and `.env.example`. `get_redis_client()` now
reads `REDIS_URL` only. In the same pass: `AINDY_SKIP_MONGO_PING` alias removed from
`config.py` `ensure_mongo_url` validator (now reads `SKIP_MONGO_PING` directly);
`tests/conftest.py` setdefault cleaned up; `.env.example` updated to `SKIP_MONGO_PING`.
Test file reduced from 9 to 5 tests — AINDY_REDIS_URL-specific cases removed.

---

## PERMISSION-SECRET-CLEANUP-1 — Remove vestigial PERMISSION_SECRET scaffolding

**Status:** CLOSED (2026-06-04)

**Discovered:** 2026-05-27 during `.env.example` drift audit.

**Context:** `PERMISSION_SECRET` was originally a required HMAC field. It has since been
deprecated (`AINDY/config.py:36`: "HMAC removed; kept for backward compat"). The field now
has `default=""` and no validator. Three scaffolding call sites remain:

- `tests/conftest.py:65` — `os.environ.setdefault("PERMISSION_SECRET", "test-...")`
- `alembic/env.py:24` — `os.environ.setdefault("PERMISSION_SECRET", "alembic-...")`
- `scripts/check_schema_version.py:24` — dict literal with dummy value

All three `setdefault` calls are vestigial — they satisfied the old required-field constraint
but are no-ops now that the field defaults to `""`. Removed from `.env.example` in 1.0.0.

**Cleanup path (1.x hygiene pass):**
1. Remove the three `setdefault` / dict-literal call sites.
2. Remove the `PERMISSION_SECRET` field from `Settings` in `config.py`.
3. Verify `model_config` uses `extra="ignore"` (confirmed: line 251) so existing `.env` files
   with `PERMISSION_SECRET=` set do not break after the field is removed.
4. No migration needed — pydantic ignores unknown fields; operators with stale `.env` files
   are unaffected.

**Verified during investigation (2026-05-25):**
- ui-kit `tsconfig.json` has `"strict": true` — TypeScript null-safety guardrails are active.
- The `safeMap()` invariant in apps-monolith addresses a problem ui-kit's strict-mode TypeScript already prevents at compile time. No need to port the lint rule to ui-kit.
- `eslint-plugin-react-refresh` correctly absent from ui-kit (Vite HMR dev-server guard, not relevant for a published library).

---

## ENV-EXAMPLE-CONSOLIDATION-1 — Remove root .env.example forwarding stub

**Status:** CLOSED (2026-06-05)

**Implemented:** Deleted root `.env.example`. The unblock condition was already met:
`docker-compose.yml` uses `env_file: AINDY/.env`, making `AINDY/.env.example` the
self-evident canonical reference. The forwarding stub was no longer earning its keep.

---

## CONFIG-ENV-EXAMPLE-DRIFT-1 — No automated check for .env.example / Settings drift

**Status:** CLOSED (2026-06-05)

**Implemented:**
- `scripts/check_env_example_coverage.py` — AST-parses all `AINDY/**/*.py` for
  `os.getenv()` / `os.environ.get()` calls and `Settings` field names; parses
  `AINDY/.env.example` for all variable names (commented-out and uncommented);
  reports uncovered gaps. Exclusion list covers test-only, OS/system, deprecated
  aliases, Docker Compose infra, and computed/internal vars.
- `python scripts/check_env_example_coverage.py --verbose` for full counts.
- `python scripts/check_env_example_coverage.py --strict` exits 1 on any gap
  (for future enforcement).
- Added as advisory CI step in `.github/workflows/runtime-ci.yml` ("Check
  env-example coverage (advisory)") — runs, reports, exits 0 until gap list is
  resolved. Comment in CI step explains how to promote to `--strict`.

**First-run result (2026-06-05):** 68 gaps found — mostly `AINDY_PLUGIN_CONTAINER_*`,
`AINDY_PLUGIN_STRONG_SANDBOX_*`, `OPENAI_*` timeout/retry tuning, and `MONGO_*`
connection pool tuning fields not yet in `.env.example`. Gaps are advisory; each
should be reviewed and either added to `.env.example` or to the EXCLUSIONS list in
the script with a reason comment.

---

## STRIPE-SETTINGS-CLEANUP-1 — Stripe Settings fields with no readers

**Status:** CLOSED (2026-06-15)

**Discovered:** 2026-05-27 during `.env.example` drift audit.

**Resolution:** State 2 confirmed — fields are intentional placeholders for the
planned Stripe integration, not vestigial. `STRIPE_SECRET_KEY` and
`STRIPE_WEBHOOK_SECRET` added to `AINDY/.env.example` Group 18 (Payments) with
a forward-pointer to PAYMENTS-ARCHITECTURE-1. Fields remain in `config.py`.

---

## PAYMENTS-ARCHITECTURE-1 — No payments implementation behind Stripe Settings fields

**Status:** Deferred — Low Priority

**Discovered:** 2026-05-27, derived from STRIPE-SETTINGS-CLEANUP-1.

**Context:** `Settings` declares `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`
but no code reads them (confirmed by grep). If payments are part of the product
roadmap, the architecture question needs an answer before implementation begins:

1. **In this repo vs. separate service.** The runtime is the execution engine.
   Billing and subscription management are typically a separate concern (separate
   service, separate datastore, separate audit trail). Embedding Stripe logic in
   `aindy-runtime` couples billing failures to runtime availability.

2. **Which layer owns the webhook handler.** Stripe webhooks require idempotent
   handling (Stripe retries on non-200). The runtime already has an idempotency
   gate (EffectRecord / NF-1–NF-5). A webhook handler that routes through
   `SyscallDispatcher` gets idempotency for free; a standalone FastAPI route does
   not.

3. **Multi-tenant billing identity.** Who is the billing subject — the operator
   deploying the runtime, or end-users of the operator's product? Determines
   whether the Stripe customer ID lives on the operator config or on a User row.

**Resolution:** Answer the three questions above before writing any Stripe
integration code. If the answer to question 1 is "separate service," remove the
Settings fields from this repo immediately (see STRIPE-SETTINGS-CLEANUP-1).

---

## MEMORY-EMBEDDING-PROVIDER-1 — OpenAI is the sole embedding provider; no abstraction layer

**Status:** Deferred — Low Priority

**Discovered:** 2026-05-27 during `.env.example` drift audit (OpenAI timeout /
retry settings surfaced as the only tunable LLM parameters).

**Context:** `config.py` declares OpenAI-specific embedding and LLM settings
(`OPENAI_CHAT_TIMEOUT_SECONDS`, `OPENAI_EMBEDDING_TIMEOUT_SECONDS`,
`OPENAI_MAX_RETRIES`, `OPENAI_RETRY_BACKOFF_BASE_SECONDS`) with no equivalent
for other providers. `DEEPSEEK_API_KEY` is present but without corresponding
timeout/retry controls, suggesting DeepSeek was added as a key-only credential
without a full client integration.

The memory and embedding subsystem (`AINDY/memory/`) appears to be hardwired to
OpenAI embeddings. There is no provider-abstraction interface (e.g.,
`EmbeddingProvider` protocol) that would allow swapping to a local model, another
API provider, or a self-hosted embedding server.

The runtime already has `llm_client.py` as the provider-dispatch facade for
chat-completion calls. An `EmbeddingProvider` protocol with the same dispatch
shape (and `AINDY_EMBEDDING_PROVIDER` env var alongside the existing
`AINDY_AGENT_PLANNER_BACKEND`) keeps the architecture symmetric rather than
introducing a second dispatch pattern.

**Impact:** Operators running in air-gapped environments, cost-sensitive
deployments, or regulated environments that prohibit external API calls for memory
content cannot use the memory subsystem without code changes.

**Resolution path (when prioritized):**
1. Audit `AINDY/memory/` and `AINDY/memory/embedding_jobs.py` to confirm the
   hardwiring (grep for `openai` import and embedding API calls).
2. Define an `EmbeddingProvider` protocol with `embed(texts: list[str]) ->
   list[list[float]]`.
3. Implement `OpenAIEmbeddingProvider` as the default. Add a
   `LocalEmbeddingProvider` stub (sentence-transformers or similar) as the
   offline alternative.
4. Add `AINDY_EMBEDDING_PROVIDER: str = "openai"` to `Settings` and
   `AINDY/.env.example` Group 11 (Agent planner, or a new Embedding group).
5. Wire `AINDY_AGENT_PLANNER_BACKEND` and `AINDY_AGENT_PLANNER_MODEL` to the
   same abstraction if the planner and embedding backends should be independently
   configurable.

**Trigger:** First operator request for a non-OpenAI embedding backend, or when
the offline / air-gapped deployment profile is formally supported.

**Upstream unlock:** Resolving this entry also unblocks the planned pgvector
semantic similarity work. At pgvector integration time, the deployment needs to
choose an embedding provider per-deployment (OpenAI `text-embedding-3-small`,
a local sentence-transformers model, etc.) to match the vectors stored in
the index. Without a provider abstraction, pgvector support locks every
deployment to whatever embedding model is hardwired at that moment.

---

## CI-SMOKE-1 — Boot smoke workflow uses editable install; switch to PyPI wheel post-publish

**Status:** CLOSED (2026-06-15)

The workflow already installs from PyPI (`pip install "aindy-runtime==$AINDY_VERSION"`, reading
the version from `AINDY/_version.py` in the checkout). `install_mode: "pypi"` is recorded in
the TTFA artifact. The editable-install step was replaced when the workflow was authored
(2026-06-08); PYPI-PUBLISH-1 was the remaining blocker and is now closed (2026-06-14).

---

## PYPI-PUBLISH-1 — Dockerfile uses local wheel build pending PyPI publish

**Status:** CLOSED (2026-06-14)

`aindy-runtime` published to PyPI at v1.3.1. Dockerfile updated: the
ui-builder (SPA compile) and local `python -m build` stages removed;
Stage 1 now runs `pip install --prefix=/install "aindy-runtime==1.3.1"`.
`build-essential` and `libpq-dev` retained — psycopg2 still compiles from
source. The published wheel includes the Platform SPA dist via package-data.
To update the pinned version after a new release, bump the version string in
the builder stage `pip install` line.

---

## NODUS-UPGRADE-1 — nodus-lang pinned at 3.0.2; v4.0.0 available

**Status:** CLOSED (2026-06-11); pin last updated 2026-06-19 (4.0.3 → 4.0.5)

**Implemented:** Bumped `pyproject.toml` + `AINDY/requirements.txt` pin from `nodus-lang==3.0.2`
to `nodus-lang==4.0.3` (latest). One embedding API fix required: `nodus_worker.py` accessed
`runtime.last_vm` (removed in v4) — updated to `runtime._get_active_vm()`. No Nodus script
changes needed. `NODUS_DEVELOPER_GUIDE.md` §6 heading and §8 upgrade notes updated to reflect v4.

**2026-06-19:** Bumped to `nodus-lang==4.0.5`. No code changes required — 4.0.4 fixed
`identity.session_id()` propagation to child VMs and retry trace bleed; 4.0.5 is stability
graduations and companion tooling only. All 504 unit tests green.

---

## MONITORING-GRAFANA-1 — Grafana excluded from compose monitoring profile

**Status:** CLOSED (2026-06-05)

**Implemented:**
- `monitoring/grafana/provisioning/datasources/prometheus.yml` — auto-registers the compose
  Prometheus instance as the default Grafana datasource (proxy mode, `http://prometheus:9090`,
  15 s scrape interval).
- `monitoring/grafana/provisioning/dashboards/aindy.yml` — file-provider provisioning config,
  reads dashboards from `/etc/grafana/dashboards` every 30 s.
- `monitoring/grafana/dashboards/aindy-runtime.json` — starter dashboard with 8 panels:
  System Health Tier (stat, threshold-colored), Active Executions (stat), Execution Rate 5m
  (stat, reqps), DB Pool Pressure (gauge, 0–1 with yellow at 0.7 / red at 0.9), AI Circuit
  Breaker State (stat per provider), Async Queue Depth (stat), Execution Duration p50/p95/p99
  (timeseries, seconds), Execution Total by Status (timeseries, reqps).
- `grafana` service added to `docker-compose.yml` monitoring profile: `grafana/grafana:11.6.1`,
  `GF_SECURITY_ADMIN_USER/PASSWORD` from env (default `admin/admin`), `GF_USERS_ALLOW_SIGN_UP=false`,
  provisioning + dashboards bind-mounted read-only, `grafana_data` volume, depends on Prometheus, port 3000.
- `grafana_data` volume added to compose volumes block.
- Compose header comment updated to mention Grafana.

**Usage:** `docker compose --profile monitoring up -d` → Grafana at `http://localhost:3000`.

---

## COMPOSE-PROD-PORTS-1 — Database ports published for dev convenience

**Status:** CLOSED (2026-06-05)

**Implemented:** `docker-compose.prod.yml` — Compose v2 override file that uses the
`!reset []` merge tag to clear the host port bindings on `postgres`, `redis`, and `mongo`.
All three DB services remain reachable within the compose network; only the `api` service
(8000) and `worker` service (8001) remain published to the host.

**Usage:**
```
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile full up -d
```

Requires Docker Compose v2.24+ (`!reset` merge tag). Version noted in the file header.

---

## PROMETHEUS-PIN-1 — prom/prometheus uses :latest tag

**Status:** CLOSED (2026-06-05)

Pinned `prom/prometheus:latest` → `prom/prometheus:v3.4.1` in `docker-compose.yml`
(current stable at close time). Consistent with pin-everything discipline elsewhere.

---

## MCP-BEHAVIOR-1 — MCP tool errors return isError result, not Python exceptions

**Status:** Tracked — Protocol fact. No code change needed; required knowledge for any MCP integration work.

**Discovered:** 2026-05-30 during `nodus-mcp` library implementation (`C:\dev\nodus-mcp`).

**Behavior:** When a tool handler raises a Python exception inside an MCP server, the `mcp` SDK (v1.x) catches it and returns a `CallToolResult(isError=True, content=[...])` to the client. It does **not** propagate a Python exception on the client side. `ClientSession.call_tool()` always returns successfully; callers must check `result.isError` to detect failures.

**Implication for tests:** Any test asserting that `call_tool()` raises on handler failure must instead assert `result.isError is True`:
```python
# WRONG — call_tool never raises on tool errors
with pytest.raises(Exception):
    await session.call_tool("bad_tool", {})

# CORRECT
result = await session.call_tool("bad_tool", {})
assert result.isError is True
```

**Implication for production callers:** Any AINDY route or service that calls an MCP server must explicitly check `result.isError` and handle the error content — it cannot rely on exception propagation.

**Scope:** Applies to both `NodusServer` (nodus-mcp) and any external MCP server called via `MCPClientAdapter`. Confirmed against mcp SDK 1.x on 2026-05-30.

---

## SDK Extraction

Status: COMPLETE (2026-05-23)

`aindy-sdk` extracted to standalone repo:
https://github.com/Masterplanner25/aindy-sdk-

First green CI run:
https://github.com/Masterplanner25/aindy-sdk-/actions/runs/26343161733

`AINDY/sdk/` removed from `aindy-runtime` in this commit.

47 SDK tests pass in the standalone repo.

`aindy-runtime` packaging config confirmed - no explicit sdk include
required removal. `pyproject.toml` already used `include = ["AINDY*"]`,
so removing the directory was sufficient.

---

## ALEMBIC-FRESH-DB-1 — Alembic migrations assume tables exist (non-idempotent on blank database)

Status: CLOSED (2026-05-27)

**Root cause:** Migrations 0001–0004 were written assuming the schema had already been
bootstrapped by `schema_contract.py`'s `create_all` path (the original deployment model).
On a fresh Docker deployment where `alembic upgrade head` runs before the server creates
any tables, migrations 0002–0004 failed with `UndefinedTable` because they referenced
tables (`platform_api_keys`, `execution_units`, `effect_records`, `webhook_subscriptions`,
`dynamic_flows`, `dynamic_nodes`) that didn't exist yet.

**Fix applied (2026-05-27):** Wrapped all DML (UPDATE, DELETE) and DDL (CREATE TABLE,
CREATE INDEX) statements in 0002–0004 in `DO $$ BEGIN IF EXISTS (pg_catalog.pg_tables
WHERE tablename=...) THEN ... END IF; END $$` blocks. On a blank database, the blocks
skip silently; the server's Phase 5 `_enforce_schema_guard` then calls `create_all`
which creates all runtime-owned tables with the current ORM-defined constraints.

On existing deployments the blocks run normally: dedup DML cleans up duplicate active
rows before the unique indexes are created, and effect_records is created if the migration
was authored after the original deployment.

**Remaining gap:** The hybrid `create_all` + alembic approach means a fresh deployment's
alembic revision history (stamped at `0004`) doesn't reflect that alembic actually ran
the migrations — the tables were created by `create_all`. This is operationally correct
but conceptually impure. A proper fix would be to write `0001` as a full `CREATE TABLE`
migration for all 32 runtime-owned tables, making alembic the single source of truth.
Deferred: the monolith alembic history and the runtime history use separate version
tables, so there's no collision risk; the hybrid approach is sustainable for 1.x.

---

## COMPOSE-PGVECTOR-1 — postgres image must be pgvector/pgvector:pg16

Status: CLOSED (2026-05-27)

The compose file originally used `postgres:16-alpine`. The runtime's `memory_nodes` table
has an `embedding VECTOR(1536)` column (from `pgvector.sqlalchemy.Vector`), which requires
the PostgreSQL `pgvector` extension. The stock image does not ship it; schema bootstrap
fails with `type "vector" does not exist`.

**Fix applied (2026-05-27):**
- Switched to `pgvector/pgvector:pg16` in `docker-compose.yml`.
- Added `docker/init-pgvector.sql` (mounted to `/docker-entrypoint-initdb.d/`) to run
  `CREATE EXTENSION IF NOT EXISTS vector` on first database initialization.
- Added a Quickstart note in `README.md` explaining the requirement for operators who
  bring their own PostgreSQL instance.

---

## PACKAGING-DEP-1 — `packaging` not propagated to Docker runtime stage

Status: CLOSED (2026-05-27)

`limits` (a transitive dependency via `slowapi`) declares `packaging` as a runtime
requirement. In the multi-stage Dockerfile, `pip install --prefix=/install` skips
`packaging` because it is already satisfied at the builder-stage system level (installed
as a build tool peer of `pip`/`setuptools`). The runtime stage only copies `/install`,
so `packaging` was absent and `import packaging` failed at server startup.

**Fix applied (2026-05-27):**
- Added `"packaging>=24.0"` and `"limits==5.8.0"` as explicit dependencies in
  `pyproject.toml` (pinning `limits` prevents a silent upgrade to a future version that
  may change `packaging` requirements).
- Added `pip install --prefix=/install --ignore-installed "packaging>=24.0"` after the
  wheel install in the Dockerfile builder stage to force `packaging` into the `/install`
  prefix regardless of system-level satisfaction.

---

## COMPOSE-HOST-1 — aindy-runtime serve defaults to 127.0.0.1; breaks Docker port mapping

Status: CLOSED (2026-05-27)

`runtime_only.py`'s `_serve()` defaults `AINDY_HOST=127.0.0.1`. Inside a Docker container
this means the server only accepts connections from within the container, making the
published port (`0.0.0.0:8000 → 8000/tcp`) unreachable from the host.

**Fix applied (2026-05-27):** Added `AINDY_HOST: "0.0.0.0"` to the compose `api` service
environment block. This is compose-only: bare `aindy-runtime serve` outside compose
correctly defaults to localhost for security.

---

## PLATFORM-UI-ENV-1 — VITE_API_BASE_URL bakes localhost into the production bundle

**Status:** CLOSED (2026-06-05)

**Discovered:** 2026-05-28 during PLATFORM-AUTH-ACQUISITION-1 implementation.

**Resolution:** Changed the fallback in `@aindy/ui-kit` `src/api/_core.js` from
`"http://localhost:8000"` to `""`. When `VITE_API_BASE_URL` is unset, `API_BASE`
is now an empty string. `buildApiUrl()` already had a falsy guard (`API_BASE ?
... : path`) so all API calls become relative paths (e.g. `/auth/login`) that the
browser resolves against the current origin — correct since the SPA and API are
always co-served.

Local dev gap (Vite on port 5173, API on 8000) is closed by `server.proxy` entries
added to `platform/vite.config.ts` — no `VITE_API_BASE_URL` env var required for
local dev. `VITE_API_BASE_URL` still works as an explicit override for non-standard
host configurations.

Bundle verified: `grep localhost:8000` returns no matches in the rebuilt
`AINDY/platform/dist/assets/*.js`.

---

## PLATFORM-AUTH-ACQUISITION-1 — Platform SPA login + admin bootstrap

**Status:** CLOSED (2026-05-28)

**What was implemented:**

*Frontend (`platform/src`):*
- `LoginPage.tsx` — form calling `useAuth().login()` against `VITE_API_BASE_URL/auth/login`.
  On success, stores token via `AuthContext` and navigates to `/` within the router tree.
- `NotAdmin.tsx` — terminal "access denied" view with logout button. Rendered (not navigated
  to) when authenticated but `is_admin=false`. No redirect loop possible.
- `PlatformApp.tsx` rewritten — `/login` route lives outside `PlatformGuard`; guard uses
  `<Navigate to="/login" replace />` for unauthenticated (React Router, respects
  `basename="/platform"`); authenticated-but-not-admin renders `<NotAdmin />` in-place.
  `redirectToApp` / `window.location.href` / `VITE_APP_BASE_URL` dependency removed.

*Backend:*
- `AINDY_BOOTSTRAP_ADMIN_EMAIL` env var (config.py + .env.example) — grant-only, idempotent,
  no-op if user not yet registered, logged at INFO if absent.
- `startup.py` Phase 5.5 — `_bootstrap_admin_email()` runs after schema guard, before dev
  key bootstrap.
- `aindy-runtime auth promote-admin <email>` CLI subcommand — grant-only, exits 0 if already
  admin, exits 1 with guidance if user not found, requires DATABASE_URL.

*Routing (`routing.py`):*
- `_SPAStaticFiles.get_response()` now discriminates route misses from asset misses:
  paths under `assets/` return 404 (not index.html); all other unmatched paths fall back
  to index.html.

**Verified end-to-end (2026-05-28):**
- `GET /platform/` → 200 index.html
- `GET /platform/login` → 200 index.html (SPA handles the route)
- `GET /platform/assets/does-not-exist.js` → 404 (not HTML fallback)
- `GET /platform/assets/index-BGunogPh.js` → 200 application/javascript
- `POST /auth/register` → 201 with JWT (`is_admin: false`)
- `AINDY_BOOTSTRAP_ADMIN_EMAIL=admin@aindy.local` + restart → `granted is_admin=True` in logs
- Second restart → `already admin, no-op` (idempotency confirmed)
- `aindy-runtime auth promote-admin ops@aindy.local` → `ok: granted` (no restart needed)
- Second run → `ok: already admin. No change made.`
- Unknown email → exit 1, clear guidance
- `POST /auth/login` after promotion → JWT with `is_admin: true`

PLATFORM-UI-ENV-1 (localhost baked into bundle) closed 2026-06-05 — relative-URL fix.

---

## PLATFORM-UI-KIT-1 — @aindy/ui-kit npm publish gap

**Status:** CLOSED (2026-05-28)

**What was implemented:**

- `src/api/auth.js` in `aindy-ui-kit`: added `.then(unwrapEnvelope)` to `loginUser`,
  `registerUser`, and `bootIdentity`. All three were returning the raw
  `{ data: {...} }` envelope; callers expecting unwrapped payloads silently received
  the wrong shape. Second-order effect: `bootIdentity` now correctly surfaces
  `system.runtime.boot_mode`, fixing the silent post-login redirect misfire in
  `PlatformHomeRedirect`.
- Published `@aindy/ui-kit@1.0.1` to npm. `platform/package.json` bumped to
  `^1.0.1` — `npm install` now pulls the corrected version from the registry.
- Dockerfile `ui-builder` stage added: `npm ci` + `npm run build` runs inside the
  image build from the registry-pinned ui-kit. `docker compose build --no-cache`
  from a fresh clone is now self-contained — no prior local UI build required.
- `.dockerignore` updated: `AINDY/platform/dist/` and `platform/node_modules/`
  excluded from build context to prevent stale local state from leaking in.

**Verification gate:** fresh clone → `docker compose build --no-cache` →
`docker exec aindy-runtime-api-1 ls .../AINDY/platform/dist/` shows non-empty dist →
`curl -I http://localhost:8000/platform/` returns 200.

---

## RIPPLE-ROUTES-001 — RippleTraceViewer load-trace issues bare monolith-era path; no served runtime route

**Status:** Open — deferred until runtime serves a per-trace load route.

**Discovered:** 2026-06-03 during RippleTraceViewer walk (ROUTES audit follow-on).

**Symptom — Bare path, no prefix:**
`RippleTraceViewer`'s "Load Trace" button calls `getRippleTraceGraph(traceId)` in
`platform/src/api/rippletrace.js`. That function correctly reads
`ROUTES.RIPPLETRACE.TRACE_GRAPH(traceId)` from the route table. However, `TRACE_GRAPH`
was defined in the monolith-era RIPPLETRACE group with `BASE = ""`, so it resolves to
`GET /rippletrace/${traceId}` — a bare top-level path with no `/platform` or `/apps` prefix.
The route is unserved by the runtime (returns 404). The route-table abstraction is honoured;
the problem is that the constant itself pointed at a monolith path that was never ported to
the runtime.

**Disposition:** Flag-off. `FEATURE_FLAGS.RIPPLETRACE_VIEWER = false` hides the RippleTrace
sidebar NavLink. The `/trace` route in `PlatformApp.tsx` remains mounted; only the NavLink is
hidden. No runtime fix is possible because there is no runtime route to repoint at.

**Two-condition unblock:**
1. The runtime serves a per-trace load route (e.g., `/platform/observability/rippletrace/{id}`)
   visible in the runtime OpenAPI.
2. A served ROUTES constant (e.g., `ROUTES.OPERATOR.RIPPLETRACE_TRACE`) is added for that path
   and `rippletrace.js:getRippleTraceGraph` is updated to use it.

The component already reads from ROUTES correctly — no architectural rewire needed, only a
new served constant and a one-line update in `rippletrace.js`.

---

## OPER-DEFER-001 — `/platform/flows/strategies` not served by runtime

**Status:** CLOSED (2026-06-15)

`GET /platform/flows/strategies` implemented in `AINDY/routes/platform/flows_router.py`.
Returns registered flow strategies from the plugin registry plus scheduling metadata
(priority tiers, max per cycle, dispatch model) and all retry policy definitions.
`get_all_flow_strategies()` added to `AINDY/platform_layer/registry.py`.
`FEATURE_FLAGS.OPERATOR_FLOW_STRATEGIES` flipped to `true` in `platform/src/api/_routes.js` —
the "Strategies" tab in `FlowEngineConsole` is now live.
6 unit tests in `tests/unit/test_flow_strategies_endpoint.py`.

---

## OPER-DEFER-002 — `/automation/logs` group not served by runtime

**Status:** CLOSED (2026-06-15)

Three routes implemented in `AINDY/routes/automation_router.py` and registered directly in
`AINDY/routing.py` (bypassing `require_execution_context`, auth via `require_admin_principal`):
- `GET /automation/logs` — list with status/source/limit filters; response `{ logs, count }`
- `GET /automation/logs/{log_id}` — detail; 404 on unknown id
- `POST /automation/logs/{log_id}/replay` — calls `replay_task()`; 404/409 on failure

`JobLog` model (`AINDY/db/models/job_log.py`) was already present in the runtime.
`FEATURE_FLAGS.OPERATOR_AUTOMATION_LOGS` flipped to `true` in `platform/src/api/_routes.js` —
the "Automation" tab in `FlowEngineConsole` is now live.
10 unit tests in `tests/unit/test_automation_logs_endpoint.py`.

---

## AGENT-EVAL-001 — Swallowed trigger-evaluator exception + SUCCESS-on-defer envelope contract

**Status:** CLOSED (2026-06-03)

**Location:** `AINDY/agents/autonomous_controller.py` — `evaluate_trigger()`.

**What was implemented:**

Removed the `try/except Exception` block in `evaluate_trigger()` (lines 33-37 pre-fix). The
evaluator call is now bare — any exception propagates through `evaluate_live_trigger` →
`_decision_or_defer_response` → `create_agent_run_runtime` → `ExecutionPipeline.run()`, which
catches it at its generic handler and returns `ExecutionResult(success=False, status_code=500)`.
`_execute_agent` raises `HTTPException(500, detail=str(exc))`, which the runtime exception
handler formats as `{"error": "http_error", "message": "<exception message>", "details": null}`.

Legitimate `"defer"` decisions (evaluator returning `{"decision": "defer", ...}`) are unaffected:
`_decision_or_defer_response` processes them as before → 202 DEFERRED with the evaluator's
actual reason. The no-evaluator path (`evaluator is None → _decision("defer", ..., "no trigger
evaluator registered")`) is also unaffected.

**Evidence:** `tests/unit/test_agent_eval_contract.py`:
- `test_evaluator_crash_surfaces_as_500` — regression test; injected exploding evaluator → 500
  with exception message; zero AgentRun rows written.
- `test_evaluator_genuine_defer_returns_202` — legitimate defer path preserved; 202 DEFERRED
  with evaluator's reason.
- `test_happy_path_evaluator_execute_calls_create_run` — approve path flows correctly → 200.

**Remaining gap (not in scope):** The `execution_envelope.status = SUCCESS` on the 202 DEFERRED
path is a separate envelope-contract issue shared with SCHED-001/002/003 (same swallow family —
this fix is the pattern; apply to SCHED-* in a future pass).

---

## AGENT-APPROVE-001a — Approve idempotency: concurrent race guard (CAS)

**Status:** CLOSED (2026-06-03)

**Discovered:** 2026-06-03 during AGENT-APPROVE-001 idempotency audit.

**Problem:** `approve_run()` (`AINDY/agents/agent_runtime/approvals.py`) used a non-atomic
read-then-act pattern to guard the `pending_approval → approved` transition. Under PostgreSQL
READ COMMITTED, two concurrent sessions could both read `status = "pending_approval"` before
either committed, both pass the Python check, and both call `execute_run` — doubling execution.

**Fix:** Replaced the Python-level check with an atomic `UPDATE ... WHERE status =
'pending_approval'` CAS. Only one concurrent caller gets `rowcount = 1`; all others see
`rowcount = 0` and return early without calling `execute_run`. The DB row lock ensures
atomicity under PostgreSQL READ COMMITTED.

**Tests:** `tests/unit/test_agent_approve_idempotency.py` — three shapes:
- `test_sequential_double_approve_executes_once` — second approve returns run state, no re-execute
- `test_repro_cancel_retry_executes_once` — second approve sees "executing" status, CAS rowcount=0
- `test_concurrent_race_repro_cas_rowcount` — proves CAS returns rowcount=0 after first commit

**Remaining gap:** The async refactor (return 202 immediately, dispatch execution to background)
is tracked separately in AGENT-APPROVE-001b.

---

## AGENT-APPROVE-001b — Approve endpoint blocks on synchronous execution; exceeds client timeout on slow tools

**Status:** CLOSED (2026-06-04)

**Implemented:** `approve_run()` (`approvals.py`) now fires `execute_run` in a daemon
background thread with its own `SessionLocal` session, returning `_run_to_dict(run)`
immediately. The HTTP approve request returns with `status: APPROVED` in milliseconds;
clients poll `GET /apps/agent/runs/{id}` for status transitions. Tests updated to use
`threading.Event` for deterministic background-thread coordination.

**Watchdog implemented (2026-06-06):** `_recover_orphaned_approved_runs()` in
`scheduler_service.py` runs every 5 minutes. It queries `AgentRun` rows where
`status='approved'` and `approved_at < now - 10 min` (cap 50 per sweep), then
re-dispatches `execute_run` in a fresh daemon thread for each. `execute_run` guards on
`status == 'approved'` at entry so re-dispatch is safe if the original thread recovered
late. Tests: `tests/unit/test_agent_approve_watchdog.py` (4 shapes). All gaps closed.

**Discovered:** 2026-06-03 during agent walkthrough (Phase 2).

**Symptom (repro-specific):** Approving a `pending_approval` run with a `memory.recall` step
held the HTTP request open through the full tool execution. The execution exceeded the browser's
default 30-second fetch timeout — the approve request was cancelled client-side
(`(cancelled)`, 30.02 s in the network panel) while the server completed the approval and
execution successfully. The UI showed a false failure / "needs retry"; the retry immediately
succeeded because server state was already correct (run already `COMPLETED`). The
response-vs-reality mismatch: the client believes the operation failed; the server knows it
succeeded.

**Root cause:** `approve_agent_run_runtime` calls `_decision_or_defer_response` (trigger
evaluation, synchronous subprocess), then immediately calls `approve_run` → `execute_run` —
running the full tool-execution loop on the request thread. "Approve to start execution
immediately" is implemented as a synchronous call, so request duration scales linearly with
tool runtime. One slow tool (or a multi-step plan) pushes the request past any client timeout.

**Severity:** No data loss observed; AGENT-APPROVE-001a CAS fix ensures retries are safe.
But UX is broken: a client-cancelled request leaves the user uncertain whether approval
landed. The gap widens with slower tools and multi-step plans — a long plan would always
false-timeout regardless of client configuration.

**Fix direction:** Ack-then-execute-async. `POST /apps/agent/run/{id}/approve` returns
promptly (`202 Accepted`, `"approved; execution started"`) and dispatches execution to a
background thread or task queue. The UI polls `GET /apps/agent/runs/{id}` (or subscribes to
an event stream) for status changes. Decouples request duration from tool runtime entirely;
client always gets a definitive success/failure for the approve action itself within
milliseconds.

**Liveness gap — orphaned `approved` state:** The CAS fix (001a) only fires from
`pending_approval`. If the winning caller's execution dies mid-flight (process crash, OOM,
SIGKILL — any unhandled termination, not a caught failure), the run is stranded in `approved`
forever: `execute_run` never ran to completion, but no subsequent caller can re-trigger it
because `status != "pending_approval"`. No retry, no recovery path. The async design **must**
include a watchdog/reaper that detects runs stuck in `approved` beyond a deadline and either
re-enqueues them for execution or marks them `failed` with a recoverable reason. This is a
liveness gap, not a correctness gap — but it means the CAS fix alone is not a complete
solution in the presence of process crashes.

**Family:** Same response-vs-reality mismatch class as AGENT-EVAL-001 (client receives
wrong status relative to actual server outcome). Cross-reference AGENT-EVAL-001 and any
EXEC-CONTRACT entry when fixing — all three share the "envelope status diverges from actual
server outcome" root shape.

---

## AGENT-RESLIMIT-001 — cpu_time_ms accounting semantics: field measures wall-clock, not CPU time

**Status:** CLOSED (2026-06-05) — field renamed to `wall_time_ms` across all layers; schema migration 0005 added; `SCHEMA_CONTRACT_VERSION` bumped to "2026-06-05"; `MAX_CPU_TIME_MS` → `MAX_WALL_TIME_MS` (env var `AINDY_QUOTA_CPU_MS` unchanged for operator compatibility).

**Discovered:** 2026-06-03 during AGENT-APPROVE-001a live smoke test.

**Symptom:** A single-step agent run (`memory.recall` with OpenAI embedding API calls)
hit `RESOURCE_LIMIT_EXCEEDED: eu exceeded cpu_time_ms limit (34021 > 30000)`. The run was
marked `failed`; the step itself completed successfully. Total request duration: 65s — the
execution thread blocked the approve request for 65s before returning a `FAILED` result.
The 65s duration is also another data point for AGENT-APPROVE-001b's priority.

**Root cause:** `cpu_time_ms` measures monotonic wall-clock elapsed time (not CPU time).
Every timing path — `runner_steps.py:112–143`, `execution_pipeline/resources.py:120`,
`syscall_dispatcher.py:666` — uses `time.monotonic()`. Network I/O wait (OpenAI embedding
calls, database round-trips) is counted in full. A realistic single agent step with three
embedding round-trips is ~34 s wall-clock time. The field name is a misnomer.

**Scope:** Per-run, accumulated across all steps. Each node's elapsed wall-clock time is
added to `UsageSnapshot.cpu_time_ms` via `+=`. `check_quota` compares the accumulated
total before each step.

**Mitigation applied (v1.0.0):** Default raised from 30 000 ms to 300 000 ms (5 minutes)
via `AINDY_QUOTA_CPU_MS`. Documented in `AINDY/.env.example` (Group 12) with a clear
warning that the field measures wall-clock time. Default pinned by
`tests/unit/test_resource_quota_defaults.py`. Note: raising the cap makes synchronous
approve (AGENT-APPROVE-001b) more likely to exceed client timeouts on slow workloads —
that is the correct trade-off until 001b lands.

**Remaining fix:** Accounting semantics — either:
1. Exclude network I/O wait from the timer (measure actual CPU time, e.g. via
   `os.times()` or by wrapping only CPU-bound segments).
2. Rename the field to `wall_time_ms` (or split into `wall_time_ms` + `cpu_time_ms`)
   so the name matches what is measured.

This requires changes to `ResourceManager.record_cpu`, all three timing call sites,
the `UsageSnapshot` dataclass, the DB column in `ExecutionUnit`, and the API envelope.
Schema change → SCHEMA_CONTRACT_VERSION bump required.

**Coupling:** `AINDY/runtime/nodus_worker.py:209` and `nodus_runtime_adapter.py` have
a parallel per-script subprocess timeout (also defaulting to 30 000 ms via
`max_execution_ms`). That is a separate Nodus VM execution limit, not the ResourceManager
quota. Operators who need individual Nodus script steps > 30 s must also configure that
timeout — it is not controlled by `AINDY_QUOTA_CPU_MS`.

---

## ROUTES-CONSUMER-SPLIT-1 — Shared `@aindy/ui-kit` ROUTES table serves both monolith and runtime; quarantine as committed breaks monolith on next publish

**Status:** CLOSED (2026-06-03) — resolved: Option B, shared table universal, policy consumer-local, annotations carry the audit map.

**Discovered:** 2026-06-03 during blast-radius check following `_routes.js` quarantine audit.

**Root cause:** `@aindy/ui-kit/src/api/_routes.js` is the single ROUTES source of truth for
**both** consumers. Both shims are identical:

```js
// platform/src/api/_routes.js (aindy-runtime)
export { ROUTES, FEATURE_FLAGS } from "@aindy/ui-kit";

// client/src/api/_routes.js (aindy-apps-monolith)
export { ROUTES } from "@aindy/ui-kit";
```

The quarantine commits (`002de1e`, `77d9956`) removed ANALYTICS, SOCIAL, TASKS, RIPPLETRACE,
ARM, MASTERPLAN, FREELANCE, IDENTITY, and SEARCH from `ROUTES` in ui-kit source. The monolith
has **94 callsites** across these groups that will `TypeError: Cannot read properties of
undefined` at call-time the moment the monolith upgrades to a version of `@aindy/ui-kit`
that includes the quarantine:

| Group | Callsites |
|---|---|
| RIPPLETRACE | 27 |
| ANALYTICS | 21 |
| MASTERPLAN | 13 |
| ARM | 8 |
| SEARCH | 8 |
| SOCIAL | 6 |
| IDENTITY | 4 |
| TASKS | 4 |
| FREELANCE | 3 |
| **Total** | **94** |

**Current safety window:** The monolith is pinned to `@aindy/ui-kit@^1.0.0`, installed at
`1.0.0`. Quarantine commits are post-`1.0.4`. As long as `1.0.5+` (or any version including
the quarantine) is not published to npm, the monolith is unaffected. Publishing triggers the
break.

**Two architectural options:**

1. **Per-consumer route overlay** — Keep all routes in the shared table (un-quarantine in
   ui-kit source). Each consumer applies its own filter. The runtime platform SPA applies
   the "only served routes" filter locally; the monolith keeps all groups. The quarantine
   comment block currently in ui-kit source moves to `platform/src/api/_routes.js` as a
   runtime-side filter — not applicable because the platform SPA's shim is a one-liner
   re-export; it would need to become an explicit re-export with the monolith-group keys
   omitted.

2. **Un-quarantine from shared + gate runtime-side** — Restore the commented-out route
   groups in `@aindy/ui-kit/src/api/_routes.js` (making the quarantine transparent to both
   consumers), and gate runtime access at the platform SPA level only (FEATURE_FLAGS,
   NavLink hiding, API module guards). The monolith retains its routes; the runtime SPA
   never renders NavLinks for unserved groups; API module functions in the runtime SPA are
   guarded individually. More code in the platform SPA; zero monolith breakage.

**What API-MODULE-DRIFT-1 depends on:** The fix shape for platform SPA API modules
(`rippletrace.js`, `analytics.js`, `platform.js`) referencing quarantined ROUTES groups
is determined by which option is chosen here.

~~**Do not publish `@aindy/ui-kit@1.0.5+`**~~ — restriction lifted 2026-06-03. Option B is
implemented: all route groups are in the shared table; publish is safe. Monolith compatibility
verified 2026-06-06 against full import surface (ROUTES + 20 other symbols). See closure note.

---

## API-MODULE-DRIFT-1 — Quarantined route groups left platform SPA API modules reading `undefined` → `TypeError`

**Status:** CLOSED (2026-06-03) — dissolved by Option B: all quarantined groups restored to the shared table; all 64 module ROUTES.* references now resolve; graceful-404 behavior restored.

**Discovered:** 2026-06-03 during `_routes.js` audit follow-on.

**Root cause:** When route groups are quarantined (commented out) in
`@aindy/ui-kit/src/api/_routes.js`, any API-module function in the platform SPA that
reads `ROUTES.<QUARANTINED_GROUP>.*` receives `undefined` instead of a path string.
Calling `undefined(args)` or using `undefined` as a URL in `authRequest()` throws
`TypeError` at call-time — a regression from the pre-audit behavior of silently returning
a graceful 404 (wrong but non-crashing).

**Affected modules and callsite counts:**

| File | Quarantined group | Function count |
|---|---|---|
| `platform/src/api/rippletrace.js` | `ROUTES.RIPPLETRACE` | 16 |
| `platform/src/api/analytics.js` | `ROUTES.ANALYTICS` | 19 |
| `platform/src/api/platform.js` (unserved subset) | `ROUTES.PLATFORM.*` (quarantined constants) | 4 |

**Disposition principle:** API module functions follow their route group. When a route group
is quarantined, its API module must be either (a) quarantined alongside it (module functions
guarded or removed), or (b) the route group must be restored (un-quarantine).

**Why not implemented now:** The correct fix shape depends on the ROUTES-CONSUMER-SPLIT-1
architectural decision:
- If Option 1 (per-consumer overlay): route groups remain in the shared table; platform SPA
  API modules continue reading them; no TypeError.
- If Option 2 (un-quarantine shared + gate runtime-side): same — modules keep their routes.
- If quarantine stays in the shared table: each affected module must be guarded (either
  deleted, `if (ROUTES.RIPPLETRACE)` guarded, or the consuming component gated via
  FEATURE_FLAGS before calling the module).

**Interim risk:** Any platform SPA component that calls a function from `rippletrace.js`,
`analytics.js`, or the unserved-subset functions in `platform.js` will throw `TypeError` at
call-time, not at import time. Components that are never navigated to are safe; components
reachable via the router but whose NavLink is hidden (e.g., RIPPLETRACE_VIEWER gated) are
safe as long as the user does not navigate directly to the route. The quarantine does not
affect the runtime's primary flows.

---

## AGENT-API-001 — `getAgents` / `recallFromAgent` / `getFederatedMemory` in platform SPA reference never-existed ROUTES constants

**Status:** CLOSED (2026-06-03) — fixed in `platform/src/api/agent.js`; all three functions now use correct `ROUTES.MEMORY.*` constants. Consumer `AgentRegistry.jsx` (lines 4–6, 58/267/455) unaffected — no component changes needed.

**Discovered:** 2026-06-03 during `_routes.js` audit, agent.js review pass.

**Location:** `platform/src/api/agent.js`

**Bug:** Three exported functions reference `ROUTES.AGENT.*` constants that were never
defined in any version of `@aindy/ui-kit`:

| Function | Uses | Should use |
|---|---|---|
| `getAgents()` | `ROUTES.AGENT.LIST` | `ROUTES.MEMORY.AGENTS` |
| `recallFromAgent(agentId, query)` | `ROUTES.AGENT.RECALL(agentId)` | `ROUTES.MEMORY.AGENT_RECALL(agentId)` |
| `getFederatedMemory(query)` | `ROUTES.AGENT.FEDERATED_MEMORY` | `ROUTES.MEMORY.FEDERATED_RECALL` |

`ROUTES.AGENT.LIST`, `ROUTES.AGENT.RECALL`, and `ROUTES.AGENT.FEDERATED_MEMORY` do not exist
— not in the audited 1.0.0–1.0.4 builds, not in any reconcile state. All three were always
`undefined`. All three calls throw `TypeError` at call-time.

The correct constants (`ROUTES.MEMORY.AGENTS`, `ROUTES.MEMORY.AGENT_RECALL`,
`ROUTES.MEMORY.FEDERATED_RECALL`) are served, correctly defined, and used correctly in the
monolith's `client/src/api/agent.js`.

**Consumer:** `platform/src/components/platform/AgentRegistry.jsx`
- Import: lines 4–6 (`import { getAgents, recallFromAgent, getFederatedMemory }`)
- Call sites: line 58 (`getAgents()`), line 267 (`recallFromAgent(...)`), line 455 (`getFederatedMemory(...)`)

**Fix:** Update the three function bodies in `platform/src/api/agent.js` to use the correct
`ROUTES.MEMORY.*` constants. This is a one-file fix independent of the ROUTES-CONSUMER-SPLIT-1
architectural decision (the target constants are in the served MEMORY group, unaffected by
quarantine). No ui-kit change needed.

**Follow-on (2026-06-06):** `ROUTES.AGENT.RECOVER` and `ROUTES.AGENT.REPLAY` constants added
to ui-kit; `recoverAgentRun()` and `replayAgentRun()` added to `platform/src/api/agent.js`.
No SPA component consumes recover/replay yet — first component that needs orphan recovery or
run replay will drive the UI work.

---

## SCHED-001/002/003 — `/platform/observability/scheduler/status` returns 500 in platform-only profile

**Status:** CLOSED (2026-06-04)

**Root cause:** The endpoint called `_run_flow_observability("observability_scheduler_status", ...)` which
invoked the `observability_scheduler_status` flow. That flow node checks for `task_is_background_leader`
via the plugin registry and for `BackgroundTaskLease` rows. In the platform-only profile, neither the tasks
domain nor the `background_task_lease` table is available, so the node returned `{"status": "FAILURE"}`,
propagating as HTTP 500.

**Fix (2026-06-04):** Replaced the flow engine call with `_build_scheduler_status_payload(db)` in
`AINDY/routes/observability_router.py`. The new helper:
- Reads `scheduler_running` directly from `scheduler_service.get_scheduler()`
- Looks up `task_is_background_leader` from the plugin registry; sets `is_leader=null` and
  `tasks_domain_available=false` when the tasks domain is absent (platform-only profile)
- Populates `stuck_run_watchdog` directly from `get_last_scan_result()`
- Never calls the flow engine — zero flow dependency

`FEATURE_FLAGS.OPERATOR_SCHEDULER_STATUS` flipped to `true` in `platform/src/api/_routes.js`.

---

## ROUTE-REG-001 — `watcher_router` and `db_verify_router` are never registered; their endpoints return 404

**Status:** CLOSED (2026-06-03)

**Discovered:** 2026-06-03 during `PUBLIC_RUNTIME_SURFACES.md` review.

**Location:**
- `AINDY/routes/watcher_router.py` — `APIRouter(prefix="/watcher", ...)`
- `AINDY/routes/db_verify_router.py`

**Bug:** Both router files exist and define endpoints, but neither is included in
`ROOT_ROUTERS`, `PLATFORM_ROUTERS`, `APP_ROUTERS`, or any other group in
`AINDY/routes/__init__.py`. Neither is imported or registered anywhere in
`AINDY/routing.py`, `AINDY/startup.py`, or `AINDY/main.py`. All defined endpoints
return 404 in production.

**Impact:**
- `POST /watcher/signals` and `GET /watcher/signals` — used by the watcher client
  (`aindy_sdk/watcher/signal_emitter.py`). The watcher client cannot deliver signals
  until this router is registered.
- `db_verify_router` endpoints — unknown; the file's purpose needs investigation
  before registration.

**Fix for watcher_router:** Add `watcher_router` to `ROOT_ROUTERS` in
`AINDY/routes/__init__.py`. The router uses `dependencies=[Depends(verify_api_key)]`
(API key auth, correct for a headless client process) and its prefix `/watcher` gives
the final paths `/watcher/signals`. Mounting in ROOT_ROUTERS (no `/apps` prefix)
matches the URL the watcher client already targets.

```python
# AINDY/routes/__init__.py
from AINDY.routes.watcher_router import router as watcher_router

ROOT_ROUTERS = [
    health_router,
    auth_router,
    watcher_router,   # add here
]
```

**Fix for db_verify_router:** Investigate intended audience and prefix before mounting.

---

## OPER-EXEC-001 — Thread-mode async is not durable; distributed mode not wired as production default

**Status:** CLOSED (2026-06-06)

**Problem:** `EXECUTION_MODE=thread` (the default) uses a `ThreadPoolExecutor` with a 100-job in-memory queue. Any job in-flight or queued when the API process restarts is permanently lost — no DLQ, no recovery. The distributed mode (Redis queue + separate worker process, `--profile full`) is fully implemented and handles restarts correctly via `requeue_stale_jobs()`, but operators could spin up the worker without the API ever routing to it if `EXECUTION_MODE=thread` remained in `.env`.

**Root cause:** The `docker-compose.yml` worker service did not set `EXECUTION_MODE=distributed`, so the compose `--profile full` command brought Redis and the worker online while the API continued dispatching to the in-process thread pool. Worker was idle; jobs remained ephemeral.

**Fix applied:**
- `docker-compose.yml` worker service: added `EXECUTION_MODE: distributed` to the `environment:` block — overrides `.env` so the worker is never silently idle.
- `docker-compose.yml` header: updated the "Production-shaped" comment to explicitly state `EXECUTION_MODE=distributed` must also be set in `.env` for the API.
- `AINDY/.env.example`: added a WARNING under the `EXECUTION_MODE=thread` line documenting that thread mode has no durability and directing operators to `distributed` + `--profile full` for production.

**No code change required.** The distributed queue backend, worker process, DLQ, stale-job recovery, and retry backoff were already production-grade; the gap was purely an operational default.

---

## OPER-EXEC-002 — ContextVar state not propagated to ThreadPoolExecutor worker threads

**Status:** CLOSED (2026-06-06)

**Problem:** `ThreadPoolExecutor.submit(fn)` runs `fn` in a fresh context where all `ContextVar` values revert to their defaults. The trace context (`trace_id`, `parent_event_id`, `pipeline_active` in `platform_layer/trace_context.py`) and syscall context (`syscall_trace_id`, `syscall_eu_id` in `kernel/syscall_dispatcher.py`) were lost at every async thread boundary. Events and logs emitted from worker threads had no trace_id / eu_id — cross-thread correlation was impossible. Distributed mode already restored context from `QueueJobPayload.context` on the worker; thread mode had no equivalent.

**Fix applied:**
- `AINDY/core/execution_dispatcher.py:453` — `copy_context()` snapshot captured before submit; `_ctx.run` passed as the callable so the worker thread inherits the full context.
- `AINDY/platform_layer/async_job_service.py:620` — same pattern for the `submit_async_job()` thread-pool path.
- `tests/unit/test_contextvar_thread_propagation.py` — 3 shapes verifying `trace_id`, `eu_id`, and `pipeline_active` each propagate correctly.

**`copy_context()` is Python stdlib (3.7+), zero new dependencies.**

---

## SYSMAX-1 — Thread-mode queue hard cap is still the .env.example default

**Status:** Partially mitigated (2026-06-07)

**Problem:** `EXECUTION_MODE=thread` defaults a 10-worker `ThreadPoolExecutor` + 100-job in-memory queue (hard cap). At ~15s/job this sustains 0.67 jobs/second. Any burst beyond 100 queued jobs returns `QueueSaturatedError` (503). Jobs are dropped outright — no overflow, no DLQ, no retry. An automated trigger scheduler hitting this ceiling gets 503 permanently (back-pressure gap also mitigated below).

**Mitigation applied (2026-06-07):**
- `docker-compose.prod.yml` now sets `EXECUTION_MODE: distributed` on the `api` service, so anyone running the production overlay gets distributed mode without needing to edit `.env`.
- `AINDY/.env.example` already carries a `WARNING: Do NOT use in production deployments where uptime matters` comment under `EXECUTION_MODE=thread` (OPER-EXEC-001, 2026-06-06).
- The worker service in `docker-compose.yml` already sets `EXECUTION_MODE: distributed` (OPER-EXEC-001, 2026-06-06).

**Additional mitigation (2026-06-15):** `startup.py:_log_async_job_capacity_advisory()` now emits `logger.error` when `ENV=production` and `EXECUTION_MODE=thread`, firing unconditionally regardless of `AINDY_JOB_WARN_CAPACITY`. The prod escalation returns early so the normal advisory path is skipped. This surfaces the misconfiguration prominently in production logs and monitoring.

**Remaining gap:** `AINDY/.env.example` still ships `EXECUTION_MODE=thread` as the literal default value — a developer who copies `.env.example` directly to `.env` and doesn't run the prod overlay still gets thread mode. Changing the default to `distributed` breaks local dev without Redis. Resolution direction: separate dev and prod `.env` templates, or a first-run wizard that detects the deployment context. Deferred until DEPLOY-TARGET-1 is addressed.

---

## SYSMAX-2 — Autonomous trigger scheduler has no queue back-pressure

**Status:** CLOSED (2026-06-07)

**Problem:** `submit_autonomous_async_job()` in `async_job_service.py` called `submit_async_job()` bare — any `QueueSaturatedError` propagated up to the route handler as 503. The trigger scheduler had no mechanism to slow down on saturation: it received 503 and could keep retrying, hammering the queue rather than backing off.

**Fix applied:** Added a `try/except QueueSaturatedError` block around the `submit_async_job()` call in `submit_autonomous_async_job()`. On saturation the submission is converted to a 60-second deferred job via `defer_async_job()` — the same path as a trigger-evaluator `"defer"` decision. The caller receives `status: DEFERRED` with `reason: "Execution queue saturated — automatically deferred for retry."` and a `defer_seconds: 60` signal. A `logger.warning` fires so operators see the saturation event.

**Effect:** Autonomous triggers that hit a full queue now self-regulate at 60s intervals instead of producing a stream of 503s. The deferred job re-enters `process_deferred_jobs()` after the cooldown, where `evaluate_live_trigger()` is called again before re-submission.

---

## SYSMAX-3 — Memory bytes not enforced per execution unit

**Status:** Deferred — requires OS integration

**Problem:** `check_quota()` in the syscall dispatcher tracks memory bytes consumed but does not enforce a hard cap. The comment in the source reads "requires OS integration." A memory-heavy node (large embedding batch, large LLM context) can OOM the API process with no prior warning or quota enforcement.

**Gap:** No `/proc/{pid}/status` or `resource.getrusage()` integration exists. The value tracked is the syscall-reported estimate, not actual process RSS.

**Resolution direction:** When per-EU memory limits become production-critical (multi-tenant SaaS, hostile-third-party profile with untrusted extensions), wire `resource.getrusage(RUSAGE_SELF).ru_maxrss` into the quota check and enforce `MAX_MEMORY_BYTES_PER_EXECUTION`. On Linux, `ru_maxrss` is kilobytes; on macOS, bytes — the platform difference must be normalized.

**Reopen trigger:** First OOM incident in a production deployment, or when `hostile-third-party` deployment profile becomes the active default.

---

## SYSMAX-4 — Per-EU syscall cap (100) and wall-time cap (5min) may be tight for LLM-heavy flows

**Status:** Tracked — advisory

**Context:** `MAX_SYSCALLS_PER_EXECUTION = 100` (hard, mid-execution termination on breach) and `MAX_WALL_TIME_MS = 300_000` (5 minutes) are the per-execution-unit caps. A single flow node calling an LLM 3 times, doing 5 memory reads, and writing back results across multiple iterations can approach 100 syscalls non-trivially. A slow model with multiple round trips can exceed 5 minutes.

**Not a bug:** The caps are correct safety defaults for single-process deployments. A multi-node DAG flow bypasses per-EU caps by design (each WAIT/RESUME creates a new EU). The risk is a developer building a complex single-node flow who hits a mid-execution `RESOURCE_LIMIT_EXCEEDED` with no retry path.

**Resolution direction:** Both caps are tunable via env vars (`AINDY_MAX_SYSCALLS_PER_EXECUTION`, `AINDY_MAX_WALL_TIME_MS`). Document the advisory in `NODUS_DEVELOPER_GUIDE.md` §3 ("Design complex flows as multi-node DAGs rather than single nodes with many syscalls"). Raise caps only when a real workload requires it — do not raise speculatively.

**Reopen trigger:** First production `RESOURCE_LIMIT_EXCEEDED` from a legitimate (non-runaway) flow.

---

## AUTH-V1 — AINDY/auth/__init__.py was a verbatim duplicate of api_key_auth.py

**Status:** CLOSED (2026-06-06)

**Problem:** `AINDY/auth/__init__.py` and `AINDY/auth/api_key_auth.py` were byte-for-byte identical (211 lines each). Any change to one had to be mirrored to the other or behavior would silently diverge. The `__init__.py` was not re-exporting — it was fully re-implementing.

**Fix applied:** Replaced `AINDY/auth/__init__.py` with a 7-line re-export shim. Canonical implementation lives exclusively in `api_key_auth.py`.

---

## AUTH-V4 — Frontend logout() never called POST /auth/logout

**Status:** CLOSED (2026-06-06)

**Problem:** `AuthContext.jsx:logout()` called `clearStoredToken()` and `setToken(null)` only. `POST /auth/logout` increments `User.token_version`, invalidating the JWT on all subsequent requests. Without the backend call, a "logged-out" user's token remained valid on the server for up to 24 hours — enough for replay or session-fixation if the token was captured.

**Fix applied:**
- Added `ROUTES.AUTH.LOGOUT` to `@aindy/ui-kit` `_routes.js`.
- Added `logoutUser()` function to `auth.js` (best-effort: `.catch(() => null)` so network failure never blocks local state clear).
- Updated `AuthContext.jsx:logout()` to call `logoutUser()` before clearing local state.
- Rebuilt `@aindy/ui-kit` and platform SPA dist.

---

## AUTH-V6 — require_platform_admin_access() passed ALL API keys regardless of scope

**Status:** CLOSED (2026-06-06)

**Problem:** `auth_service.py:require_platform_admin_access()` checked `is_admin` for JWT users but returned immediately for any `auth_type == "api_key"` user with no scope check. An API key with `flow.read` scope could call any admin route (flow management, session invalidation) guarded by this dependency. `admin_invalidate_sessions` in `auth_router.py` had a manual in-handler copy of the correct logic instead of using the shared dependency — the two drifted.

**Fix applied:**
- `AINDY/services/auth_service.py`: `require_platform_admin_access()` now checks `"platform.admin" in api_key_scopes` for API key callers, 403 if absent.
- `AINDY/routes/auth_router.py`: `admin_invalidate_sessions` dependency changed from `Depends(get_current_user)` to `Depends(require_platform_admin_access)`; manual in-handler guard removed.
- `tests/unit/test_auth_wiring.py`: 11 tests covering V1 re-exports (5 shapes) and V6 guard (6 shapes).

---

## TIER3-8 — MemoryIngestQueue.enqueue() dropped writes were silent at the queue level

**Status:** CLOSED (2026-06-07)

**Problem:** `MemoryIngestQueue.enqueue()` incremented Prometheus metrics on queue-full and not-accepting drops, but emitted no log. The service wrapper (`memory_ingest_service.py`) warned on drops, but direct callers had no visibility.

**Fix applied:** Added `logger.warning` in both drop paths inside `enqueue()` — queue-full (with depth/capacity) and not-accepting — so all drop paths produce a WARNING log regardless of call site.

---

## TIER3-9 — db.flush() in event emission committed pending handler ORM changes

**Status:** CLOSED (2026-06-07)

**Problem:** `_persist_system_event()` called bare `db.flush()` after `db.add(event)`. SQLAlchemy's `session.flush()` pushes ALL pending identity-map changes to the DB — not just the event. Any ORM object a route handler had staged with `db.add()` but not yet committed would be flushed as a side effect of event emission and committed by the subsequent `db.commit()`. Data and event writes were not atomic, and handler data could be committed by a different code path than the handler itself.

**Fix applied:** Changed `db.flush()` to `db.flush([event])` — SQLAlchemy supports flushing a specific object list. The event gets its DB-assigned `id` for use in `link_events()` while all other pending session changes stay unflushed until the handler's own commit.

---

## AUTH-V5 — SECRET_KEY module-level string exported from auth_service.py

**Status:** CLOSED (2026-06-07)

**Fix:** Removed `SECRET_KEY: str = settings.SECRET_KEY` from line 94. Removed `global SECRET_KEY` + assignment in `rotate_signing_key()` and `_reload_key_on_sighup()`. JWT encode already used `_get_signing_key()`; decode already used `_key_ring.verify_keys()`. Zero external consumers confirmed by grep before deletion.

---

## REPLAY-1 — Deterministic replay harness requires Clock injection refactor

**Status:** CLOSED (2026-06-11)

Added `AINDY/kernel/clock.py`: ContextVar-backed `utcnow()` + `frozen_at(t)` context manager. No signature changes required — each call site imports `utcnow` directly and the ContextVar override is async-safe and thread-safe.

Sites updated (12): `kernel/syscall_dispatcher.py` (EffectRecord gate — 3 sites), `kernel/circuit_breaker.py` (`_now()` body), `kernel/scheduler/waits.py` (time-wait tick), `core/execution_unit_service.py` (`_now()` body), `core/system_event_service.py` (event timestamp + 5 cutoff queries), `runtime/flow_engine/runner_completion.py`, `runtime/flow_engine/runner_failure.py`, `runtime/flow_engine/shared.py` (`_default_wait_deadline`).

12 tests in `tests/unit/test_clock.py` — all green. ORM model `default=lambda:` columns intentionally excluded (SQLAlchemy concerns, not business logic).

---

## TIER3-V2V3 — require_scope() / enforce_api_key_scope() wired to platform routes

**Status:** CLOSED (2026-06-07)

**Problem:** `require_scope()` and `AuthPrincipal` in `AINDY/auth/api_key_auth.py` were fully implemented but wired to zero routes. Any API key with any scope (or no scope) could call flows, memory, and syscall routes as if it had full access — the stored scope list was consulted only at key creation for validation, never at request time.

**Fix applied:**
- Added `enforce_api_key_scope(scope)` to `auth_service.py` — a FastAPI dependency factory using the already-resolved `current_user` dict (no second DB lookup). JWT users always pass; API keys must have the required scope or `platform.admin`.
- `flows_router.py`: `list_flows`/`get_flow` → `flow.read`; `run_flow_endpoint` → `flow.execute`.
- `platform_ops_router.py`: `list_memory_path`/`memory_tree`/`memory_trace` → `memory.read`.
- `platform_ops_router.py:dispatch_syscall`: inline domain-level scope enforcement for API key callers — maps syscall name prefix to required scope (`sys.v1.memory.*` → `memory.write`, `sys.v1.flow.*` → `flow.execute`, `sys.v1.agent.*` → `agent.run`, `sys.v1.webhook.*` → `webhook.manage`); `platform.admin` bypasses all.
- 13 new unit tests in `tests/unit/test_tier3_structural.py`.

**V3 — CLOSED 2026-06-07:** Removed the dead parallel auth path (`get_authenticated_principal`, `require_scope`, `AuthPrincipal`, header extractors) from `api_key_auth.py`. File now contains only `Scopes`. `__init__.py` re-exports `Scopes` only. Three dead export-check tests removed from `test_auth_wiring.py`.

---

## LAYER-1 — execution_dispatcher.py opens its own SessionLocal() for event emission

**Status:** Deferred — Known architectural gap

**Problem:** `AINDY/core/execution_dispatcher.py:_enqueue_distributed()` opens a `SessionLocal()` directly at lines 305–307 and 368–370 to emit a `job_enqueued` observability event. The execution dispatcher layer is directly managing DB sessions — a responsibility that belongs to the service or event layer. This violates the "one session per request" convention and places session lifecycle management in the wrong layer.

**Why deferred:** The dispatcher runs outside the request context in the distributed path; no request-scoped session is available. Fixing this properly requires routing the event emission through an injected event service or background event queue rather than opening a raw session. That refactor touches the dispatcher/event boundary across multiple call sites and is a non-trivial scope change.

---

## LAYER-2 — auth_router.py routes auth primitives through execute_with_pipeline_sync

**Status:** Deferred — Known architectural gap

**Problem:** `AINDY/routes/auth_router.py` sends all four handlers (login, register, logout, admin_invalidate_sessions) through `execute_with_pipeline_sync`. Auth requests create an `ExecutionUnit`, emit `execution.started`/`execution.completed` events, and go through quota checks. Every login and register is an "execution" with full resource-tracking overhead. The pipeline was not designed for auth primitives — this creates event noise and DB writes on every unauthenticated request.

**Why deferred:** Removing auth routes from the pipeline requires a lighter-weight route wrapper that still provides tracing and error normalization without ExecutionUnit creation. That wrapper doesn't exist yet. The overhead is real but not a correctness issue at current load.

---

## LAYER-3 — exception_handlers.py falls back to decode_access_token for user attribution

**Status:** CLOSED (2026-06-15)

**Problem:** `AINDY/exception_handlers.py:_extract_user_id_from_request()` called `decode_access_token` as a fallback — full signature verification + key ring walk — for logging attribution only. Cross-layer dependency: exception handler doing auth work.

**Resolution:** Replaced `decode_access_token` fallback with the same unverified sub-claim extraction pattern used by the rate-limiter (`jose.jwt.decode` with `verify_signature/aud/exp: False`). Attribution is for logging only — no access-control decision is made — so unverified decode is correct. The `request.state.user_id` fast path (set by the pipeline for all authenticated requests) remains the primary path; the unverified decode fires only for requests that failed before the pipeline set state (e.g. 401s on unauthenticated routes).

---

## LAYER-4 — memory_ingest_service.py opens SessionLocal() outside request context

**Status:** Deferred — Known architectural gap, intentional by design

**Problem:** `AINDY/memory/memory_ingest_service.py` imports and opens `SessionLocal()` at construction time (line 40), outside any request context. This creates a second concurrent session to the same tables as the request session. It breaks the "one session per request" convention and creates independent transaction boundaries.

**Why deferred:** Memory ingestion is intentionally decoupled from the request session — writes are queued and flushed after the script finishes, not within the request transaction. The independent session is architecturally correct for this use case (deferred background writes shouldn't be rolled back if the request session rolls back). The violation is a convention mismatch, not a correctness bug. Resolving it would require a formal background-session pattern or session factory abstraction.

---

## LAYER-5 — execute_with_pipeline_sync uses asyncio.run(); coordination_router calls it on every endpoint

**Status:** Deferred — Known performance gap

**Problem:** `execute_with_pipeline_sync()` (`AINDY/core/execution_helper.py:69`) bridges synchronous routes into the async pipeline via `asyncio.run()`. `coordination_router.py` calls this on 9+ endpoints — each call creates and tears down a new event loop. No technical correctness issue in FastAPI's threadpool model, but it introduces non-trivial async machinery overhead on every coordination route invocation.

**Why deferred:** Fixing this requires either making the coordination routes fully async (straightforward but high-churn across all endpoints) or providing a sync-native pipeline path that doesn't use `asyncio.run()`. The coordination domain is a candidate for extraction (see ROUTE-EXTRACT-001 remaining candidates), so the refactor may be moot if those routes move to the monolith.

---

## EXEC-EU-1 — _safe_finalize_eu not called from the finally block in ExecutionPipeline.run()

**Status:** CLOSED (2026-06-07)

**Implementation:** `_safe_finalize_eu` (resources.py) now has an idempotency guard: `ctx.metadata["eu_finalized"]` is checked at entry and set to `True` before the DB write attempt — preventing double-finalization on normal paths. The `finally` block in `ExecutionPipeline.run()` (pipeline.py) now calls `self._safe_finalize_eu(ctx, "failed")` gated by `ctx.metadata.get("eu_status") != "waiting"`, which is a no-op on every normal path (guard fires) and closes the EU as `failed` on any `BaseException` escape path. Waiting EUs are excluded by the `eu_status` guard.

---

## EVENT-1 — Emission error loop prevention is implicit, not explicit

**Status:** CLOSED (2026-06-08)

**Implementation:** Added explicit `_emission_failed` flag to `ctx.metadata` in `_safe_emit_event` (`pipeline.py`). On any emission exception the flag is set to `True`. On every subsequent call to `_safe_emit_event` for the same context, the guard short-circuits and returns `None` immediately — before touching the DB or calling `emit_system_event`. This makes loop prevention a first-class invariant rather than a side effect of the broad `except` catch.

- The guard fires only on the second+ call in a failure sequence; it does not affect the first failed call.
- A successful emission never sets the flag.
- 4 new regression tests in `tests/unit/test_memory1_event1_fixes.py` (flag-set-on-failure, skip-on-flag, no-flag-on-success, loop-terminates-after-one-real-call).

---

## MEMORY-1 — persist_memory_ingest_payload can produce orphaned nodes on partial write failure

**Status:** CLOSED (2026-06-08)

**Implementation:**
- Added `commit: bool = True` parameter to `MemoryTraceDAO.create_trace()`, `MemoryTraceDAO.append_node()`, and `MemoryNodeDAO.save()`. When `commit=False` each method uses `db.flush()` instead of `db.commit()`, leaving the changes pending in the caller's transaction.
- `persist_memory_ingest_payload()` now passes `commit=False` to all three DAO calls and issues a single `db.commit()` on success. On any exception it calls `db.rollback()` and returns `IngestResult(status="failed")` rather than silently continuing with partial state.
- 4 new regression tests in `tests/unit/test_memory1_event1_fixes.py` (success path commits once, append failure rolls back, create failure rolls back, session always closed).

---

## MEM-NODETYPE-1 — Memory write defaults to a node_type the validator rejects

**Status:** CLOSED (2026-06-27)

**Problem:** Two `memory.write` paths defaulted `node_type="execution"`, but
`VALID_NODE_TYPES` in `AINDY/memory/memory_persistence.py` (`{decision, outcome, insight,
relationship}`) omits "execution". The `before_insert`/`before_update` validator
(`validate_node_type`) therefore raised `ValueError` on every default write — and since
`memory_type` falls back to `node_type` (line 122), it failed `VALID_MEMORY_TYPES` too.
This blocked the execute half of the `runtime_local` planner loop, which almost always
plans a memory tool first. Surfaced during live-stack verification from the monolith
(`LIVE_VERIFICATION_SCOPE.md`). The syscall docstring even documented `default "execution"`,
so the runtime advertised a default its own model rejected.

**Why it was an outlier, not a missing type:** every *other* write path already defaulted
to a valid type — `memory_ingest_service.py` → "insight", `nodus_memory_bridge.py` →
"outcome". Only the syscall handler and the Nodus builtin defaulted to "execution". The
scorer (`memory_scoring_service.py`) also falls back to "insight" when type is unspecified,
so "execution" nodes silently floored at the 0.8 default weight.

**Fix applied (two passes):** Changed every write-path default to "insight" (matches the
scorer fallback, so a defaulted write ranks identically to an untyped one).

Pass 1 (PR #98) — the two sites in the original report:
- `AINDY/kernel/syscall_registry.py` — `_handle_memory_write` default + docstring.
- `AINDY/runtime/nodus_builtins.py` — `NodusMemoryBuiltins.write` signature + docstring.
- 3 regression tests in `tests/unit/test_mem_nodetype_default.py`.

Pass 2 — **execute-to-completion verification on the Postgres stack revealed PR #98 was
incomplete**: the *deferred* path the flow engine actually runs still defaulted to
"execution", as did the extension ABI. In the script paths the rejected save is swallowed
(`logger.warning` + `continue` / `return None`), so the script reported completion while the
node silently vanished. Six more sites, all → "insight":
- `AINDY/runtime/nodus_worker.py` — `DeferredMemoryBuiltins.write` + `_remember_factory`.
- `AINDY/runtime/nodus_runtime_adapter.py` — `_apply_deferred_memory_writes` dao.save.
- `AINDY/nodus/runtime/memory_bridge.py` — `AINDYMemoryBridge.remember` (the VM's `remember`
  builtin; persists in-subprocess on its own session).
- `AINDY/platform_layer/extension_runtime_api.py` + `extension_worker.py` — extension memory ABI.
- `tests/integration/test_planner_loop_execute_to_completion.py` — 4 integration tests driving
  each real write path (dispatcher syscall, adapter deferred persist, `remember` builtin, full
  subprocess VM run) with a default node_type against real PostgreSQL, asserting the node
  persists as "insight". All green. A clean tree-wide sweep confirms no `"execution"` node_type
  default remains.

No `VALID_NODE_TYPES` change → `memory_persistence.py` untouched → schema contract protocol
not triggered.

**Distinct from `ECOGAP-1` (event-sourced durable execution / replay):** that is a
kernel/flow-engine durability gap (append-only event log for crash continuation), a
different subsystem from the memory-node taxonomy. An episodic "execution"/"action" memory
type could be introduced later *if* ECOGAP-1 mirrors execution events into the memory graph —
but that is deferred and out of scope here.

---

## PLANNER-SUBPROC-1 — Agent planner broken on Linux/Docker (run-tool provider isolated into a stateless subprocess)

**Status:** CLOSED (2026-06-27)

**Problem:** `POST /apps/agent/run` → `generate_plan` → `get_tools_for_run` resolves the
registered run-tool provider, which `registry._maybe_wrap_runtime_callback` routed through
an isolated subprocess (`runtime_callback_worker.py`). First-party-app providers (and the
planner-context provider) read **live in-process registration state** — the agent
`TOOL_REGISTRY` and planner context populated during app bootstrap. A bare subprocess can't
reconstruct that: its `cwd` is the read-only site-packages dir (`runtime_callback_host.py:62`),
so the provider's `load_plugins()` finds no app manifest and returns zero tools → planner
raises `requires at least one registered tool` → **500**. Masked in local dev because Windows
resolves the manifest; only surfaced on Linux (CI + a `python:3.11-slim` non-editable repro).
Same class of bug also affects app-provided trigger evaluators (the documented silent-defer in
`_maybe_wrap_runtime_callback`).

**Fix applied:** Registry-state-dependent surfaces now run **in-process**. Added
`_STATEFUL_IN_PROCESS_CALLBACK_SURFACES = {"run_tool_provider", "planner_context"}` in
`AINDY/platform_layer/registry.py`; `_runtime_callback_spec` returns `None` (in-process) for
those. Self-contained surfaces (startup hooks, capability providers, trigger evaluators) keep
subprocess isolation. Context is still sanitized at the registry boundary
(`get_planner_context` / `get_tools_for_run`), so no extra state crosses any boundary. Updated
`tests/unit/test_extension_ownership.py` (planner_context now in-process, not recorded as an
isolated invocation; startup_hook stays isolated). Shipped in 1.4.3.

**Remaining gap:** app-provided **trigger evaluators** still run isolated; if a deployment
relies on app-state-dependent trigger evaluators they will silently defer on Linux. Add
`trigger_evaluator` to the in-process set when that becomes a real workload.

---

## OBS-1 — Pipeline _safe_* failures log at DEBUG, invisible in production

**Status:** CLOSED (2026-06-07)

**Implementation:** Promoted all three failure-path logs from `logger.debug` to `logger.warning`:
- `resources.py` — `_safe_require_eu` exception handler (was line 53)
- `resources.py` — `_safe_finalize_eu` exception handler (was line 160, now shifted by EXEC-EU-1 guard)
- `pipeline.py` — `_safe_emit_event` exception handler (was line 347)

Success-path debug logs (`[Pipeline] EU registered`, `[Pipeline] EU finalised`) remain at DEBUG — not failures.

---

## LEASE-1 — `lease-elected` background leadership was advertised but not enforced

**Status:** CLOSED (2026-06-24)

**Source:** Audit finding — *"advertising a guarantee the code doesn't implement."*

**The gap (what the audit found):** The deployment contract advertises
`background_leadership_mode: "lease-elected"` for the `distributed-api`,
`distributed-worker`, and `hostile-third-party` profiles, and
`DEPLOYMENT_PROFILES.md` stated *"Lease-elected means exactly one participating
runtime process becomes leader at a time."* The code did not implement it.
Leadership was decided locally in `_start_background_services` (and the worker
entrypoints) by:

```python
is_leader = enable_background and all(result is not False for result in startup_results)
```

— a per-process boolean with no cross-instance coordination. Every API/worker
replica whose local `system.startup` hooks succeeded self-elected, so N replicas
ran N schedulers (duplicate stuck-run watchdog, EffectRecord TTL cleanup,
orphaned-approved recovery, db-pool metrics). The `background_task_leases` table
existed in the ORM model and was *read* by two observability endpoints (which
therefore always saw `None`) but the runtime never wrote/acquired any row. (The second half of the same
audit — "silent durable→in-memory queue degradation on Redis loss" — was checked
and **refuted**: that path fails fast in prod/distributed/`AINDY_REQUIRE_REDIS`
and otherwise degrades loudly with a metric + warning + `system.queue.backend_degraded`
event + `UNSAFE_DEGRADED` runtime condition. No code change needed there.)

**Implemented:**
- `AINDY/platform_layer/leadership.py` — atomic lease claim/renew/takeover/release
  on `background_task_leases` (`SELECT … FOR UPDATE` serialises contenders on
  PostgreSQL; `UNIQUE(name)` resolves the fresh-insert race), plus a
  `BackgroundLeadershipElector` daemon thread that runs on every lease-electing
  process: the leader renews each tick; a follower takes over once the leader's
  lease lapses (TTL 60s, heartbeat 20s); a leader that loses the lease stands
  down via `on_lose` to prevent split-brain.
- `AINDY/startup.py` `_start_background_services` — for `lease-elected` profiles,
  `is_leader` is now gated on winning the lease; scheduler start/stop are wired
  to the elector's acquire/lose callbacks. The `in-process` (single-instance)
  profile keeps the local-boolean guard — that profile never promised cross-process
  exclusion. Lease is released on shutdown so a standby takes over promptly rather
  than waiting the full TTL.
- `AINDY/worker/__init__.py` and `AINDY/worker/__main__.py` — both worker
  entrypoints route leadership through the same elector.
- Tests: `tests/unit/test_background_leadership.py` (10) — claim/renew/takeover/
  release semantics + elector acquire/lose/disabled/exception transitions.

**Layering — runtime lease vs tasks-domain symbols (deliberate non-goal):** the
runtime claims its own lease row named `background_runner`. This is *distinct*
from the apps-monolith `tasks` domain, which owns the `task_is_background_leader`
/ `task_background_lease_name` registry symbols and a separate lease row named
`task_background_runner` (`apps/tasks/bootstrap.py`). The two coexist as different
rows in the same table. The runtime deliberately does **not** register those
symbols (doing so would collide with the tasks domain and corrupt its
observability), so the `/platform/observability` scheduler-status `is_leader`
field stays app-domain-owned. Surfacing the runtime's own lease state in
observability/health is a separate future enhancement (`leadership.background_leader_status()`
is the ready accessor), not part of this fix.

**Clock assumption (documented, not a gap):** lease expiry is evaluated against
each process's kernel clock (`utcnow`), not the DB clock. The 60s TTL vs 20s
heartbeat margin tolerates the skew expected between co-deployed instances. If a
future deployment spans hosts with unbounded skew, switch the expiry comparison
to a DB-side `now()` predicate.

**No schema-contract bump:** `background_task_leases` was already in the ORM
metadata (created by `create_all` / the Phase 5 schema guard); no model file
changed, so `SCHEMA_CONTRACT_VERSION` is untouched.

---

## NODUS-SYS-SURFACE-1 — Idiomatic `std:sys` bypasses the AINDY SyscallDispatcher

**Status:** Open — deferred (latent footgun, no current incident)

A `.nd` script has **two name-disjoint ways to issue a syscall**, and only one of
them reaches AINDY. They look interchangeable but route to entirely different
backends:

**Surface A — nodus-lang native `std:sys` (the idiomatic path):**
```
import "std:sys"
sys.call("sys.v1.memory.put", { ... })
```
resolves to `site-packages/nodus/stdlib/sys.nd`, whose `call()` invokes the native
VM builtin `syscall(name, payload)` (`nodus/vm/vm.py:262`, `builtin_syscall` at
`vm.py:1389`) → `nodus.services.syscall_runtime.call_syscall`. That runtime has its
**own hardcoded registry of exactly four syscalls** — `sys.v1.memory.{get,put,delete}`
and `sys.v1.memory.recall_from` — backed by `nodus.services.memory_runtime`, an
**in-process, ephemeral key/value store**. It never touches AINDY's
`SyscallDispatcher`, capability enforcement, quota, idempotency, kernel, or Postgres.

**Surface B — the AINDY-injected `sys` builtin (the path AINDY actually wires):**
```
sys("sys.v1.memory.put", { ... })        # bare builtin, NOT the std:sys module
```
`AINDY/runtime/nodus_worker.py:167` registers a host function literally named `sys`
(`register_function("sys", _sys_dispatch, arity=2)`) whose body
(`nodus_worker.py:136-162`) calls `AINDY.kernel.syscall_dispatcher.dispatch_syscall`
→ the real dispatcher → kernel + Postgres, scoped to `user_id`.

**The gap:** the builtins are named differently (`syscall` vs `sys`), so there is no
shadowing in either direction — but also **no guard**. aindy-runtime's worker does
**not** register `syscall` or `syscall_list`, so it cannot override Surface A. A
developer who writes the conventional `import "std:sys"; sys.call(...)` silently gets
nodus's four-syscall in-process stub with throwaway memory instead of AINDY's
capability-enforced, durable dispatcher — no error, no warning, wrong backend.

This reconciles two prior audit claims that appeared to conflict: "Nodus `std:sys`
routes to local in-process handlers, not AINDY syscalls" (true of **Surface A**) and
"Nodus `sys()` reaches the AINDY SyscallDispatcher" (true of **Surface B**). Both are
correct; they describe different builtins. The integration is real and live, but it
does **not** work by intercepting the idiomatic stdlib entry point.

**Options (not yet chosen):**
- **Guard/alias** — register a `syscall` (and `syscall_list`) host builtin in
  `nodus_worker.py` that forwards to `_sys_dispatch`, so `std:sys` also lands on
  AINDY. Risk: must match the native envelope shape and arity exactly, and verify it
  overrides the VM builtin rather than colliding.
- **Fail-loud** — register `syscall` to raise a clear "use the `sys()` builtin under
  AINDY" error, so the wrong path is caught at runtime instead of silently stubbed.
- **Doc-only** — document in `NODUS_DEVELOPER_GUIDE.md` that under aindy-runtime,
  scripts must call the bare `sys(...)` builtin and must not `import "std:sys"`.

Key files: `AINDY/runtime/nodus_worker.py` (`_sys_dispatch`, `register_function`),
`site-packages/nodus/stdlib/sys.nd`, `nodus/vm/vm.py:262` (`builtin_syscall`),
`nodus/services/syscall_runtime.py`, `nodus/services/memory_runtime.py`.

**Reopen/resolve trigger:** before any `.nd` script or agent objective is authored
that relies on `import "std:sys"`, or before exposing Nodus authoring to external
users.

---

## ECOGAP-* — Ecosystem capability gaps (corrected lens)

Derived from the 12-project ecosystem re-audit, re-judged against source-verified
aindy-runtime/Nodus facts. These are **capability/roadmap gaps**, not classic debt
(a shortcut in existing code) — except `ECOGAP-6` (and the narrow `ECOGAP-5a`), which
are debt-shaped. Full analysis: `docs/runtime/ECOSYSTEM_CAPABILITY_GAPS.md`. Several
map onto existing entries (noted per item); do not double-track.

### ECOGAP-1 — Event-sourced durable execution / transparent crash continuation

**Status:** Deferred — roadmap (P0 among these gaps)

aindy-runtime marks non-waiting `running` flows FAILED on restart; there is no replay log.
WAIT/RESUME + `flow_run_rehydration` + ResumeWatchdog already cover *suspended* flows — the
gap is specifically mid-run, non-waiting work. Field bar: Temporal (event-sourced replay);
LangGraph (pending-writes-then-checkpoint, partial); ADK/OpenHands/Open Interpreter ship
event logs. Absorb targets: ADK append-event fold, LangGraph `versions_seen` vector clock,
Temporal at-least-once idempotent-start. **Do not import weaker JSON-snapshot models.**

**Reopen trigger:** when crash-continuation of in-flight non-waiting flows is scheduled.

### ECOGAP-2 — Hostile-safe sandboxing (strong-VM tier on non-Linux) — SEE C2/C3

**Status:** Owned by existing entries — **C2 (CLOSED 2026-05-24)** and **C3 (open, Phases 1–4)**.

The ecosystem audit flagged sandboxing as a leading P0 gap; that **overstates** the real state.
Container-grade isolation is closed, certified cross-platform (Linux/Windows/macOS reach
`container-grade-sandbox`), and adversarially escape-tested (17 tests, real Docker, all PASS;
`tests/sandbox/`, `SANDBOX_ESCAPE_AUDIT.md`). Auto-selection is environment-aware:
distributed/production profiles default to `containerized_oci` (the certified tier); only dev
falls back to `insecure_dev_subprocess`. The genuine residual — `strong_sandbox_vm`
(dedicated-VM, hostile-third-party tier) being Linux-only — is **already tracked as C3**. No new
debt. Reconcile the external v2 aggregate + OpenHands/OI/SWE per-project audits against C2/C3.

### ECOGAP-3 — Provider breadth + embedding SPOF — extends MEMORY-EMBEDDING-PROVIDER-1

**Status:** Deferred — roadmap (P1)

Only OpenAI + DeepSeek concretely in tree; OpenAI hard-required for embeddings. The embedding
half is **MEMORY-EMBEDDING-PROVIDER-1**; this entry adds LLM-client breadth (Azure/Anthropic/
Gemini/Bedrock/local) behind `CircuitBreakerLLMClient`. Absorb: CrewAI native multi-SDK +
cross-loop cache-breakpoint, Devika 7-backend registry, litellm reach (Aider/SWE/ADK). Most
broadly cited concrete weakness (9/12 projects). Mechanically straightforward behind the
existing client seam.

**Reopen trigger:** when a non-OpenAI provider or local-model path is scheduled.

### ECOGAP-4 — MCP/A2A: gated-egress boundary (runtime) + wire adapters (plugin)

**Status:** Deferred — roadmap (P1 for the runtime half)

Two altitudes, split deliberately. **G4a (runtime):** a capability-gated egress boundary +
secret-broker so executed/sandboxed code never holds keys (OpenHands' control-plane pattern) —
trusted/enforced at the syscall boundary, a real runtime concern. **G4b (plugin):** the concrete
MCP/A2A wire clients (JSON-RPC envelopes, handshake, SSE framing) are hosted adapters registered
via the plugin ABI — *not* kernel primitives (the kernel owns the socket + the gate, not the
protocol client). `nodus-mcp`/`nodus-a2a` graduate from out-of-tree to registered plugins.

**Reopen trigger:** when first external MCP/A2A interop is scheduled, or when a sandbox needs
mediated egress without holding credentials.

### ECOGAP-5 — Durable timer (5a) + workflow-as-data (5b)

**Status:** 5a — Deferred, P3 (debt-shaped, narrow). 5b — Deferred, P2 (Nodus/language layer).

**5a (runtime, narrow):** user schedules are already durable-as-data (`NodusScheduledJob`) and
rehydrated into APScheduler on boot via `restore_nodus_scheduled_jobs()`, so the in-memory
jobstore is rebuilt from DB each start. Residual = misfire/missed-window handling for fires due
*during* downtime, plus one unifying durable timer/FireTime primitive. **Not** "schedules lost."

**5b (Nodus):** `FLOW_REGISTRY` is in-process Python — business structure compiled into runtime
code. The fix is a loadable graph artifact (Nodus `.nodus/graphs/<id>.json`) the runtime
interprets — an **anti-creep** mechanism that lifts business logic *out* of the kernel, on the
language layer, not a runtime gap. Absorb: ADK frontier/JoinNode scheduling, MS typed-message
actor graph, LangGraph reducer-cell/serde discipline.

**Reopen trigger:** 5a — when misfire-on-downtime is reported. 5b — when data-defined flow
execution (beyond code-defined `FLOW_REGISTRY`) is scheduled.

### ECOGAP-6 — Execution-path test coverage

**Status:** Deferred — **debt** (P2)

`AINDY/worker/` and the Surface-B Nodus execution path are at low/zero coverage; the integration
tier is mocked-only in CI. This is genuine hygiene debt (a shortcut in existing code), not a
capability gap. Coverage claims for the durable-execution and Nodus-dispatch paths are therefore
under-tested. Pairs with `ECOGAP-1` (the replay path most needs real coverage).

**Reopen trigger:** before relying on `worker/` or Surface-B behavior in a release claim.

---

## DOCS-BUCKET-A-1 — Runtime docset relocation (Bucket A) residuals

**Status:** Open — Low Priority (relocation landed 2026-06-27)

The Bucket A migration relocated runtime-owned docs that were left behind in the
pre-split monolith archive (`C:\dev\masterplan-infiniteweave-monday-node-2025-0411\docs`)
into this repo, mirroring the archive's category dirs:

- `docs/architecture/MODEL_OWNERSHIP_POLICY.md`
- `docs/platform/governance/{AGENT_WORKING_RULES,ERROR_HANDLING_POLICY,CHANGELOG}.md`
- `docs/tutorials/{index,01-memory-driven-workflow,02-event-driven-automation,03-scheduled-execution}.md`

File-path tokens were verified against `AINDY/**` and `aindy-apps-monolith/apps/**`
and rewritten to canonical post-split locations (runtime-moved paths repointed
within `AINDY/`; app-owned modules repointed to `aindy-apps-monolith` with notes).
`RUNTIME_DOC_INDEX.md` gained a "Sibling Docsets" section.

**Residuals / deferred work:**

1. **`DATA_MODEL_MAP.md` Tier-2 surgery — DONE 2026-06-28.** Landed at
   `docs/architecture/DATA_MODEL_MAP.md`, runtime-scoped ("surgery only,
   faithful"). The archive's ~902-line **combined** pre-split schema was
   collapsed: app-domain tables (`freelance`, `masterplan`, `task`, `social`,
   `author`, `leadgen`, `research`, `arm`, `rippletrace`, analytics/`metrics_*`,
   `network_bridge`) reduced to a single ownership-pointer table → `aindy-apps-monolith`
   (canonical list: `DB_OWNERSHIP_CONTRACT.md`). The runtime tables it documented
   (agent, background_task_lease, memory_metrics, memory_trace, memory_trace_node,
   request_metric, system_health_log, user, user_identity + Memory Bridge
   `memory_nodes`/`memory_links`/`memory_node_history`) were **re-verified
   against current source** — corrected several stale/copy-paste claims
   (`Agent.owner_user_id` is a UUID FK not a plain String; bogus `user_id->users.id`
   FK lines on `background_task_leases`/`system_health_logs`/`users` removed;
   `RequestMetric.trace_id`, `User.is_admin`/`token_version`/`api_keys` added;
   `memory_nodes` brought up to date — `visibility`, `source_event_id`/`root_event_id`,
   `causal_depth`, `impact_score`, `memory_type`+`VALID_MEMORY_TYPES`,
   `embedding_pending`/`embedding_status`). Paths repointed
   (`memory_persistence.py` → `AINDY/memory/`; `memory_ingest_service.py` → `AINDY/memory/`).
   §3 Alembic rewritten to the runtime's own tree (`alembic_version_runtime`,
   `0001`–`0005`) with the combined-monolith history pointed to the app repo; §4
   MongoDB collapsed to app-owned. A **Coverage** note enumerates the ~18
   runtime models not individually detailed (kept faithful to the archive's
   table set rather than expanding to the full current model set — deliberate
   scope choice). Deferral references in `AGENT_WORKING_RULES.md` and
   `RUNTIME_DOC_INDEX.md` updated to live links. **Residual:** the doc is
   accurate-but-not-exhaustive — the ~18 Coverage-listed runtime models
   (`effect_record`, `execution_unit`, `flow_run`, `event_edge`, `agent_run`,
   `capability`, `dynamic_*`, `system_event`, `system_state_snapshot`,
   `waiting_flow_run`, `webhook_subscription`, `api_key`, …) are pointered to
   `DB_OWNERSHIP_CONTRACT.md` + source, not field-mapped here. Expand only if a
   full current data-model reference is needed.

2. **`ERROR_HANDLING_POLICY.md` is a combined-monolith audit.** Its "Current
   Implementation" sections are ~90% app-owned routers/services (genesis, arm,
   social, dashboard, rippletrace, network_bridge, search/seo, tasks). Paths were
   repointed to `apps/...` with a scope banner, but the doc is a candidate for a
   later runtime-only / app-only editorial split. The **Policy Rules** sections
   are repo-agnostic and remain valid.

3. **Unverified path tokens — RESOLVED 2026-06-28.** The lone dangling token,
   `deepseek_arm_service.py`, was an **app-owned** ARM concern (not a runtime
   concern). The pre-split file was refactored into the
   `apps/arm/services/deepseek/` package in `aindy-apps-monolith` (analyzer:
   `deepseek_code_analyzer.py`; config/file/security siblings), wired via
   `apps/arm/bootstrap.py` + `apps/arm/syscalls.py`. `ERROR_HANDLING_POLICY.md`
   §2 repointed to that package; the "path unverified" annotation is removed. No
   remaining unverified tokens in the migrated docs.

4. **Pre-split governance docs.** `INVARIANTS.md` has been **split and authored**:
   the runtime-owned half is now `docs/platform/governance/INVARIANTS.md` (this
   repo; PostgreSQL/UTC/memory-graph/auth/startup invariants, enforcement sites
   re-verified against the current tree), companion to the app-owned half in
   `aindy-apps-monolith`. References that previously annotated it as "not migrated"
   were repointed. `SYSTEM_SPEC.md` and `GOVERNANCE_INDEX.md` remain absent in both
   split repos; references retained as historical pointers. Not part of Bucket A.

5. **CHANGELOG relocated verbatim.** The pre-split monolith `CHANGELOG.md` is an
   audit trail; its hundreds of historical path references were intentionally
   **not** rewritten (rewriting would falsify the record). A scope banner marks it
   as pre-split history; current runtime history lives in
   `docs/runtime/DOCSET_CHANGELOG.md`.

6. **Tutorial surface drift** (validated against the live runtime, annotated with
   **Runtime note** callouts, examples left intact so worked outputs stay
   coherent): `sys.v1.event.wait` is not a registered syscall — WAIT/RESUME is the
   Nodus `event.wait()` builtin; `sys.v1.flow.run` field is `initial_state` not
   `input`; trace endpoint param is `{trace_id}`; delete-schedule param is
   `{job_id}`; `extra` is SDK-only (not in the v1 `memory.write` schema). The
   `AINDY.sdk.aindy_sdk` client and the `docs/sdk/` docset are not in this repo
   (published separately as **aindy-sdk**).

7. **`RUNTIME_DOCSET_BOUNDARY.md` relative links** to `../architecture/`,
   `../platform/`, `../apps/` now have the parent dirs present, but several
   *specific* targets it lists (`BOOT_PROFILES.md`, `ARCHITECTURE_MAP.md`,
   `PLUGIN_REGISTRY_PATTERN.md`, `platform/interfaces/API_CONTRACTS.md`,
   `apps/*`) are **not** Bucket A docs and remain unresolved by design.

**Close trigger:** when `DATA_MODEL_MAP.md` surgery lands (residual 1) and the
`ERROR_HANDLING_POLICY.md` runtime/app split (residual 2) is decided.

---

## RTR-* — Runtime Roadmap (Nodus-first execution & runtime primitives)

**Status:** Open — roadmap (not classic debt). Priorities per item below.

**Provenance.** Consolidated from the five app-side evolution docs (AGENTICS,
Reasoning, RippleTrace, …) where work was flagged "runtime-gated" or
"runtime-owned" while the app layer was built. Every claim below was
**validated against the live source tree on 2026-06-29** by three code-mapping
passes (Nodus substrate; worker model + agent execution; multi-agent / autonomy
/ memory / causality). File:symbol evidence is cited inline.

**Ownership lens.** The runtime is "kernel primitives + registration surfaces;
apps extend without editing runtime" (see `DB_OWNERSHIP_CONTRACT.md`,
`MODEL_OWNERSHIP_POLICY.md`). Discriminator used throughout: **runtime owns the
mechanism / primitive / registration surface; the app owns the policy /
semantics** (which events are significant, which triggers fire, ranking weights,
the content-domain causal graph). Every item below passes that test as
runtime-owned (or runtime-half of a split). Validation confirmed several items
are **"finish/promote what exists," not "build from scratch"** — flagged per
item as **[BUILD]** (mostly greenfield) vs **[HARDEN]** (substantial prior work
in-tree).

### RTR-1 — Nodus as primary execution substrate — **[BUILD], highest, cross-cutting**

The single biggest item; blocks the most. "via_nodus" is currently a misnomer.

**Design (2026-06-29):** the `register_nodus_workflow` registration surface
(item (a) below) is specified in `docs/runtime/NODUS_WORKFLOW_CONTRACT.md` —
Phase 1 = registration surface + `nodus_workflows` table + boot rehydration +
run-by-name (both `flow-graph` and `script` kinds); Phase 2 = agent-plan→`.nd`
+ VM-backed agent adapter; Phase 3 = bytecode cache + `NodusTraceEvent`
wire-or-drop. Implementation pending against that contract.

**Phase 1 landed (2026-06-29):** `register_nodus_workflow` surface
(`AINDY/runtime/nodus_workflow_registry.py`) — imperative + declarative
(`nodus-workflow` manifest kind), `nodus_workflows` source table (schema-contract
bumped to `2026-06-29`, Alembic `0006`), boot rehydration in `startup.py`,
`run_nodus_workflow` by name, both kinds. Mirrors `register_dynamic_flow`
(owner-class + provenance gating). 14 unit tests.

**Phase 2a landed (2026-06-30) — tool-calling seam.** Discovered the foundational
gap: AINDY tools were **not reachable from inside a Nodus workflow** at all. The
native `action tool "x"` construct lowers to nodus's built-in `__action_tool` →
its own 4-tool stub registry with **zero** capability enforcement, and those VM
builtins **cannot be overridden** (`register_function` raises). Fix: a new
`call_tool(name, args)` host function (`AINDY/runtime/nodus_worker.py` →
`run_agent_tool`) bridges the VM to `tool_registry.execute_tool` with full
capability-token enforcement — **fail-closed** (no token → refused before the
tool). `run_id` + `execution_token` thread through `NodusExecutionContext` →
worker payload (`nodus_runtime_adapter.py`). Now any Nodus workflow/script (RTR-1
flow-graph/script *and* the future agent path) can call AINDY tools with
enforcement. 7 unit tests (in-process — helper enforcement + payload threading;
no subprocess dependency). Docs: `NODUS_DEVELOPER_GUIDE.md` (`call_tool`).
**Phase 2b landed (2026-06-30) — agent-plan → `.nd` compiler.**
`compile_agent_plan(plan)` (`AINDY/runtime/agent_plan_compiler.py`) turns a flat
agent plan into a native Nodus `workflow {}` — one `step step_N after step_{N-1}`
per plan step — each calling `call_tool(get_state("__step_N_tool"),
get_state("__step_N_args"))` (the Phase 2a seam). Tool names + args pass via run
**state**, never embedded as source, so no planner/LLM-derived value becomes code
(**injection-safe**). Returns `{source, workflow_name, state_inputs, steps}`; the
`steps` metadata (index, tool, risk_level, description, `result_key`) is the
contract for 2c to map each `__step_N_result` from output state back to an
`AgentStep` row. Standalone module — **not** wired into `execute_run` yet (that's
2c). 11 unit tests incl. injection-safety + end-to-end VM execution in order.
(NB: 2c renamed the compiler's `state_inputs` → `input_payload` — args ride the
`nodus.execute` node's `input_payload` channel, which is forwarded to the script;
the `state` namespace is isolated.)

**Phase 2c landed (2026-06-30) — opt-in VM-backed agent path (Core MVP).**
`execute_agent_run_via_workflow` (`nodus_execution_service.py`) compiles the plan
(`compile_agent_plan`) and runs it via the canonical flow-backed Nodus path
(`run_nodus_script_via_flow`), so each step's tool call goes through the
capability-enforced `call_tool` seam. `execution_token` + `agent_run_id` thread
via `extra_initial_state` → flow state → the `nodus.execute` node → the execution
**context** (never the script namespace); `execute_nodus_runtime` gained
`execution_token`/`run_id` params. `AgentStep` rows, status/counters, result, and
capability/completion events are reconstructed from the workflow's output state
(`reconstruct_agent_step_results`). Selected via
`AINDY_AGENT_EXECUTION_BACKEND=nodus_vm`; **`AGENT_FLOW` stays the default**. 8
unit tests (reconstruction, selector routing, e2e run with mocked flow+capability
+ real sqlite AgentRun).

**Phase 2d landed (2026-07-03) — per-step retry + halt-on-first-failure.**
`compile_agent_plan` now emits, per step: a tool call, an in-step **retry loop**
(`max_attempts` resolved at compile time from `risk_level` via
`resolve_retry_policy` — low/med 3, high 1), a non-transient short-circuit
(`is_retryable_error` host function, new in `nodus_worker.py`), and a
**`throw`-on-final-failure**. The throw is what gives halt: a native `workflow {}`
step that raises fails its task, and the task graph never schedules the dependent
`after` steps — so no step runs on a predecessor's bad output (parity with
AGENT_FLOW's `FAILURE`-halts-the-flow). The failing step still records its result
via `set_state` before throwing, so reconstruction sees it; a trailing absent
`__step_N_result` now means *halted*, not *dropped*. **Design note:** the native
step `retries` option is deliberately NOT used — in nodus's runner it is a
*durable* retry (`status: retry_scheduled`, needs a resume call) that would strand
the single-shot VM path; the in-step loop keeps retry synchronous. Validated
in-process (VM semantics identical to the subprocess path, which the Windows dev
box blocks — WinError 4551): 12 new/updated unit tests covering attempt budgets,
halt, retry-to-exhaustion, retry-recovery, and non-retryable short-circuit.
**Phase 2e landed (2026-07-03) — mid-plan WAIT/RESUME (segment-split, live-process).**
Mid-plan wait is **net-new** — the default AGENT_FLOW path has no wait at all
(steps only return SUCCESS/FAILURE; no `waiting` AgentRun status). Chosen design:
**segment-split**, not single-workflow-suspend. A plan may now carry WAIT steps
(`{"wait_for": "<event.type>", "correlation_key"?: str}`); `split_agent_plan`
cuts the plan into segments at those boundaries (`compile_agent_segment` keeps
global `step_N`/`__step_N_result` indices contiguous across segments). The
executor runs one segment per invocation: on success with a trailing wait it
parks the run at `status="waiting"` and registers a scheduler wait whose resume
callback runs the *next* segment when the event fires. **Completed segments are
never re-run** — their `AgentStep` rows are durable, so tool calls never fire
twice (this is why segment-split was chosen over the flow engine's
re-execute-from-top resume, which would replay prior tool calls). Resume is
idempotent via a `waiting → executing` check-and-set. Why not the two obvious
mechanisms: plain-nodus `event.wait()` raises inside a native `workflow {}` step
→ caught by the task graph as a step *failure*, not a wait; and a native workflow
wait returns a normal dict → invisible to the worker/flow engine.

Initial increment was **live-process durability**: the wait rides the scheduler's
in-memory `_waiting`/`notify_event` path; `_persist_wait_backup` skips the
`WaitingFlowRun` FK-backup for `eu_type != "flow"` (agent waits). New AgentRun
status `"waiting"` (added to `ACTIVE_AGENT_RUN_STATUSES`); the EU is mirrored to
completed/failed on resume-terminal via `_sync_agent_eu_status`.

**Cross-restart durability landed (2026-07-04).** A waiting agent run now survives
a process restart. New durable `AgentRun.wait_state` JSONB column (schema bump
`2026-07-04`, Alembic `0007`) holds `{event_type, correlation_key,
resume_segment_index}`, set on park and cleared on resume/terminal. Everything
else needed to rebuild the resume is already durable: `plan` → segments,
`result["steps"]` → accumulated results, `capability_token` → the self-verifying
scoped token (reloaded; re-mint only needed past its 23h TTL). `rehydrate_waiting_agent_runs`
(`AINDY/core/agent_run_rehydration.py`) mirrors `rehydrate_waiting_flow_runs` —
queries `status="waiting"` runs and re-registers each scheduler wait from durable
state; hooked into `startup.py` Phase 14 between FlowRun and Nodus rehydration
(before `mark_rehydration_complete`/`drain_buffered_events`, so boot-buffered
events reach the fresh callbacks), guarded by `RuntimeConditionCode.AGENT_RUN_REHYDRATION_FAILED`.
The live-register and rehydration paths share one resume builder
(`_build_agent_resume_callback`) whose closure does an **atomic** `waiting →
executing` claim (`UPDATE … WHERE status='waiting'`), so a duplicate event-fire,
watchdog re-trigger, second rehydration, or multiple instances can't resume twice.
Validated in-process: wait→resume cycle (no re-run of prior steps), resume-failure,
double-fire idempotency, no-wait regression, durable `wait_state` persist/clear,
and restart-rehydration (fresh scheduler → re-register → event → resume →
complete, step 0 not re-run) + skip-guards.

**Planner WAIT steps + resume/approval landed (2026-07-04).** `planning.py`
documents the WAIT-step schema (`{"wait_for": "<event.type>"}`), excludes WAIT
steps from `overall_risk` aggregation, and `apply_wait_policy` reconciles them to
the execution backend: **stripped** on `agent_flow` (which can't execute a wait —
safety), and on `nodus_vm` with the new opt-in `AINDY_AGENT_WAIT_BEFORE_HIGH_RISK`
setting, a human-approval WAIT (`AGENT_APPROVAL_EVENT = "agent.approval.granted"`)
is **inserted before the first high-risk step**. The resume/approval action is
`resume_agent_run_runtime` (`AINDY/agents/runtime_api.py`): it reads the run's
`wait_state`, resolves the correlation the same way the register/rehydrate paths
do (`wait_state.correlation_key or run.correlation_id` — a latent trace_id-fallback
mismatch in the rehydrator was fixed to match), and calls `publish_event` to fire
the resume. A reference route `POST /agent/runs/{id}/resume` was added to the
(deprecated) runtime `agent_router.py`. Tests: policy strip/insert/disabled +
resume publish/correlation/404/409 (`test_agent_wait_policy.py`).

**Capability token refresh on resume landed (2026-07-04).** A run parked on a WAIT
across a long wait / restart could have a `capability_token` past its 24h TTL by
the time the event fires — its tools would then fail validation. The resume
callback (`_build_agent_resume_callback`) now, after the atomic claim, checks
`capability_service.token_is_expired` and, if lapsed, calls
`capability_service.refresh_token`: it rebuilds the token on a fresh clock (new
`execution_token`/`issued_at`/`expires_at`/`token_hash`) while **reusing** the
token's existing `granted_tools`/`allowed_capabilities`/`approval_mode` verbatim —
no plan re-derivation, no policy re-evaluation, no escalation — and persists it to
`AgentRun.capability_token`/`execution_token`. Applies to both the rehydration and
long-lived live-wait cases (it runs in the shared resume closure). Non-fatal: if
refresh can't rebuild the token it falls back to the original (fails cleanly as
before). Tests: `test_capability_token_refresh.py` (expiry/refresh/validate) +
resume-refreshes-expired-token e2e in `test_agent_vm_execution.py`.

**Real-Postgres parity validation landed (2026-07-04) — and uncovered + fixed THREE
latent bugs that the mocked unit tests + Windows subprocess block had hidden since
2c.** The `nodus_vm` agent path had never actually run end-to-end (unit tests mock
`run_nodus_script_via_flow`; Windows blocks the subprocess). Driving it on real PG
exposed:

1. **Engine-boundary reject (the path never ran at all).** `run_nodus_script_via_flow`
   calls `enforce_engine_boundary(entrypoint="nodus.run")`, which rejects any
   `workflow_type` without "nodus" in it as a Python-DAG flow. The nodus_vm path
   passed `workflow_type="agent_execution"` → **every real invocation raised**. Fix:
   the nodus_vm path (which IS nodus-backed) now uses `"nodus_agent_execution"`.
   (AGENT_FLOW keeps `"agent_execution"` — it uses the `flow.run` entrypoint.)
2. **No runtime tools in the subprocess.** `execute_tool` → `_ensure_tools_loaded` →
   `load_plugins()` registers nothing for runtime tools (the runtime manifest has no
   plugins); `memory.read`/`memory.write` are only registered by
   `_ensure_runtime_agent_defaults`, which fired in the parent at startup but never in
   the subprocess → every runtime-tool call returned "Tool not found". Fix:
   `_ensure_tools_loaded` now also calls `_ensure_runtime_agent_defaults` (idempotent;
   app deployments unaffected).
3. **Wait-plans couldn't be approved.** `get_grantable_tools` returned `[]` on a WAIT
   step (`tool=None`) → `mint_token` returned None → no wait-containing plan could be
   approved. Fix: skip non-tool steps in `get_grantable_tools`
   (`get_plan_required_capabilities` already did).

Validation: `tests/integration/test_agent_vm_parity.py` (marker `integration`, real PG
+ real subprocess) — success parity (both backends complete a `memory.recall` plan
with identical AgentRun/AgentStep outcomes), failure parity (invalid token denied
identically at the flow gate), and a `nodus_vm`-only durable **WAIT→RESUME** cycle on
Postgres (segment 0 executes, run parks with `wait_state`, fired resume runs segment
1, step 0 not re-run). Windows blocks the subprocess, so this suite is authoritative
on Linux CI. Regression guards for #1 and #3 added to the unit suite.

**Soak — real-PG retry/halt validation (2026-07-04).** The parity suite's failure
case was only capability-denial at the flow gate; a real mid-plan TOOL failure had
never run through the subprocess. Added a runtime **diagnostic tool**
`runtime.selftest` (`runtime_agent_defaults.py`) — executable + capability-wired
(`runtime_selftest` cap) but excluded from the planner catalog (`category="diagnostic"`)
— that echoes a caller-requested outcome and, on failure, raises an error carrying an
`(attempt N)` counter (module-level per process = per subprocess/segment). New
integration tests (`test_agent_vm_parity.py`) drive, on real PG through the real
subprocess: tool-failure parity (both backends → run failed, failed AgentStep),
halt-on-first-failure parity (downstream step never runs), and nodus_vm retry
behavior — retryable → 3 attempts, non-retryable ("permission") → 1 attempt,
high-risk → 1 attempt (all read from the recorded step error). Unit guards for the
tool + its catalog exclusion added.

**Remaining follow-ups:** (a) **wire the LIVE resume route in the monolith**
(`aindy-apps-monolith` `apps/agent/routes/agent_router.py`) calling
`resume_agent_run_runtime` — the runtime `agent_router.py` is deprecated/unregistered,
so the app-owned route is the real surface; must land AFTER the runtime package is
bumped/reinstalled in the monolith so the import resolves. (b) Remaining soak before
making `nodus_vm` the default / retiring `AGENT_FLOW`: real scheduler-driven resume +
rehydration-across-restart on PG (the wait/resume test still patches the scheduler to
fire the callback); **app-tool** execution under `nodus_vm` in the monolith (validated
here only with runtime-native tools); multi-instance resume; and subprocess-per-segment
perf. The VM path stays opt-in/non-default until then.

> **RTR-1a — CLOSED 2026-06-29.** The pre-4.x `flow.step()` host-object DSL
> collided with nodus-lang 4.0.5's reserved `step` keyword (and 4.x doesn't
> support host-object method calls at all), so the `flow-graph` kind couldn't
> compile real scripts. **Resolution:** adopt nodus-lang's **native
> `workflow {}` / `goal {}` construct** (4.x ships a first-class `orchestration/`
> workflow feature). `compile_nodus_flow` (`AINDY/runtime/nodus_flow_compiler.py`)
> now parses that construct and extracts the step dependency DAG (no execution);
> `flow-graph` workflows execute natively by appending `run_workflow(<name>)` /
> `run_goal` and running through the shared `nodus_execute` flow. The retired
> `flow.step()` node-wiring intent is already covered by `register_dynamic_flow`,
> so nothing is lost. The dead `nodus.flow.compile` node + `POST /platform/nodus/flow`
> route were repointed to the new model; the `nodus.flow.compile→run` chain is
> deprecated. Both kinds now fully working. Tests: `test_nodus_flow_compiler.py`
> (incl. end-to-end VM execution in dependency order) + updated
> `test_nodus_workflow_registry.py`.

- **Evidence (current state):** `AINDY/runtime/nodus_adapter.py` —
  `NodusAgentAdapter.execute_with_flow` is a compat shim
  (`__aindy_compat_wrapper__ = True`) delegating to
  `execute_agent_flow_orchestration`; the agent path runs a **static Python
  `AGENT_FLOW` DAG** on `PersistentFlowRunner` — **no Nodus VM**. A real
  VM-backed path exists only as the opt-in `@register_node("nodus.execute")`
  node → `nodus_runtime_adapter.NodusRuntimeAdapter._execute()`, which runs
  `nodus_worker.py` as a **subprocess** into the pip `nodus-lang` package. Agent
  plans (`execute_agent_run_via_nodus`, `nodus_execution_service.py`) interpret
  `plan["steps"]` directly in `agent_execute_step`; **no `.nd` is generated,
  templated, or precompiled.**
- **No registration surface:** searches for `register_nodus_workflow` /
  `.nd` discovery return nothing. What exists: `FLOW_REGISTRY` (in-memory dict,
  `flow_engine/registry.py`), data-only `register_dynamic_flow`
  (`flow_registry.py:133` — wires pre-registered nodes, **no conditions over
  HTTP**), and name-keyed `.nodus` script upload (`nodus_script_store.py`,
  `POST /platform/nodus`). `nodus_flow_compiler.compile_nodus_flow` can turn a
  Nodus *flow-script* into a flow dict but is **not fed by agent plans** and its
  conditions are in-memory only.
- **The gap (runtime work):** (a) a real `register_nodus_workflow` / `.nd`
  registry + discovery surface so apps register/select workflows without runtime
  edits — this is the missing hook that forced the "runtime-gated" verdicts;
  (b) replace/wrap the agent shim with a VM-backed adapter + an agent-plan → `.nd`
  mapping (generated/templated/precompiled); (c) `.nd` asset storage + versioning
  in-repo and a managed compile/bytecode cache (today: trivial `memory.nd`, **no
  versioning**; `.nbc` is the nodus-lang VM's own path+mtime cache, library-written
  and **stale/cross-machine** — see note below); (d) wire or drop the dead trace
  path: `NodusTraceEvent` (`db/models/nodus_trace_event.py`) + reader
  `nodus_trace_service.query_nodus_trace` + `GET /platform/nodus/trace/{trace_id}`
  exist, but the writer `_flush_nodus_traces()` (`nodus_runtime_adapter.py`) has
  **no call sites** — no rows are ever written.
- **What already works:** live Nodus VM runs are **not** a side path — they go
  through `PersistentFlowRunner` → create a `FlowRun`, link `AgentRun.flow_run_id`,
  and emit `SystemEvent`s (`source="nodus"`) on the canonical bus.

### RTR-2 — Durable worker model — **[HARDEN], high**

- **Evidence:** `core/distributed_queue.py` — `RedisQueueBackend` is real,
  production-grade (atomic `LPUSH`/`BRPOP`, `aindy:jobs:inflight` visibility-timeout
  hash, delayed ZSET + Lua promotion, DLQ, circuit breaker, capacity Lua,
  `requeue_stale_jobs`). `worker/worker_loop.py` is a real separate-process
  consumer with a DB-side atomic claim (`_try_claim_job`) preventing
  double-execution. `platform_layer/leadership.py` `BackgroundLeadershipElector`
  (lease in `background_task_leases`) is enforced (LEASE-1, closed). `JobLog` +
  `ExecutionUnit` rows persist job intent **before** submission.
- **Current state:** durable path **exists and is well-built**, but is **opt-in**
  behind `EXECUTION_MODE=distributed` + `REDIS_URL`. Default is in-process
  `ThreadPoolExecutor` (`async_job_service._distributed_execution_enabled()` →
  `"thread"`). Prod guards already raise without Redis (`settings.is_prod`).
- **The gap:** flip distributed to the prod default (partial mitigation already
  via prod overlay — see **SYSMAX-1**); add per-tenant queue isolation (today is
  count-based admission via `AINDY_ASYNC_MAX_CONCURRENT_*`, **not** isolated
  lanes); close the thread-mode in-flight loss (record survives, execution does
  not). **Related:** SYSMAX-1, TIER3-10 (`async_job_service` coupling), LEASE-1.

### RTR-3 — Agent execution integrity — **[HARDEN/BUILD split], high**

- **Evidence:** two records with a one-directional, **nullable, post-hoc** link.
  `AgentRun` (`db/models/agent_run.py`) `flow_run_id` is
  `ForeignKey(..., ondelete="SET NULL"), nullable=True`; `execute_run`
  (`agents/agent_runtime/execution.py`) → `execute_agent_run_via_nodus`
  (`nodus_execution_service.py:368`) creates the `FlowRun` first and
  **back-patches** `agent_run.flow_run_id` after. Reconciliation is convention-
  based and guarded on the literal string `status == "executing"` (both forward
  and in `stuck_run_service._recover_agent_run`, which drives from the FlowRun
  side). **`FlowRun` is the de-facto authority; `AgentRun` is a mirrored projection.**
- **Current state:** lifecycle hardening is **substantially done** — multiple
  DB-driven recovery scanners (`stuck_run_service.scan_and_recover_stuck_runs`,
  `core/flow_run_rehydration.rehydrate_waiting_flow_runs` with atomic-claim
  `UPDATE flow_runs SET status='executing' WHERE id=? AND status='waiting'`,
  `core/resume_watchdog`, scheduler orphaned-approved recovery). Queued/waiting/
  failed states are fully inspectable and resumable without process memory.
- **The gap:** one authoritative execution-record path — unify the
  `AgentRun` ↔ `FlowRun` state machines (single authority / shared enum;
  non-nullable, non-post-hoc link) so divergence is impossible (today an AgentRun
  in any state other than `"executing"` silently no-ops recovery). Exact-position
  resume of mid-node `running` work is out of scope (today: fail + reconstruct
  from `AgentStep`, or replay fresh via `replayed_from_run_id`); thread-mode
  in-flight overlaps RTR-2.

### RTR-4 — Multi-agent delegation core — **[HARDEN], medium**

- **Evidence:** **working core, not scaffolding.** `db/models/agent_registry.py`
  `AgentRegistry` is a persisted table; `agents/agent_coordinator.py` has real
  `register_or_update_agent`, `_rank_candidate_agents` (weighted
  `coordination_score`), and `dispatch_delegated_run` (creates child `AgentRun`
  with `parent_run_id` / `spawned_by_agent_id`, sets parent `status="delegated"`).
  `agents/runtime_guardrails.enforce_delegation_guardrails` enforces max depth
  (3), max children (8), cycle detection. `capability_service.mint_token` mints a
  **fresh scoped, hash-sealed, TTL-bounded token per child run**.
  `agents/agent_message_bus.py` is a SystemEvent-backed bus
  (`operation_request`/`operation_result`/`memory_share`) with
  `acknowledge_message`.
- **The gap:** (a) inter-agent **approval handshake** — today it's
  acknowledgement-only, no accept/reject/negotiate contract; (b) **independent
  per-delegate capability narrowing** — the child token currently inherits the
  parent's plan/agent_type rather than re-deriving a tighter scope; (c)
  **delegation-token-scoped private memory** — boundaries today are namespace +
  `is_shared` flags + MAS path isolation, not bound to the delegation token.

### RTR-5 — Autonomous closed loop — **[BUILD], medium (split)**

- **Ownership:** runtime owns the missing execution-window primitive; the
  decision **policy** is app-owned and already tested.
- **Evidence:** `agents/autonomous_controller.py` is **evaluate-and-gate only** —
  `evaluate_trigger` returns `{execute|defer|ignore}` via an app-registered
  evaluator (`get_trigger_evaluator`); there is **no planning and no execution
  call** in the controller. `async_job_service` does the gating/scheduling
  (`defer_async_job` / `submit_async_job` + saturation→60s defer;
  `process_deferred_jobs` re-evaluates). `AutonomyDecision` is an app-layer model
  (absent in standalone runtime).
- **The gap:** a bounded, **runtime-driven trigger → plan → execute window** with
  policy enforcement and loop-scheduling primitives in the controller. Today apps
  raise triggers and the runtime only evaluates/defers/queues — there is no
  controlled runtime-driven execution window.

### RTR-6 — Reasoning at the memory layer — **[BUILD], medium**

- **Evidence:** recall/capture are real (`runtime/memory/orchestrator.py`
  `MemoryOrchestrator.get_context` recall pipeline; `memory/memory_capture_engine.py`
  `evaluate_and_capture` significance-scored capture). But memory-derived signals
  are emitted as **ordinary `SystemEvent`s** (`MEMORY_WRITE`, `AUTONOMY_DECISION`)
  carrying `impact_score` / `memory_type` in the payload, plus columns on
  `MemoryNode`. There is **no `ReasoningEvent` model and no `reasoning.*` event
  type** (grep of `core/system_event_types.py` is empty for reasoning).
- **The gap:** standardize memory-derived signals as **first-class reasoning
  inputs** in `runtime/memory/orchestrator.py` + `memory_capture_engine.py`;
  optionally add a dedicated reasoning event model and richer emission from
  `agent_runtime` / `nodus_adapter` if `SystemEvent` payload conventions get too
  loose.

### RTR-7 — Execution-causality as unified intelligence layer ("RippleTrace") — **[HARDEN], medium/low (split)**

- **Naming note:** "RippleTrace" does **not** appear in the runtime by design —
  as primitives were discovered, the content-domain ripple concept crystallized
  into the runtime's `SystemEvent` + `EventEdge` causal graph. The name dissolving
  into the primitive is expected, not a gap.
- **Evidence (runtime half already canonical):** `db/models/event_edge.py`
  `EventEdge` (`source_event_id` / `target_event_id` / `target_memory_node_id`,
  `relationship_type`, CHECK exactly-one-target). `platform_layer/event_trace_service.py`
  provides real graph algebra: `link_events`, `link_event_to_memory`,
  `build_trace_graph`, `get_downstream_effects` / `get_upstream_relationships`,
  `detect_root_event` / `detect_terminal_events`, `calculate_depth` (BFS),
  `calculate_impact_score`. `MemoryNode.causal_depth` / `root_event_id` /
  `source_event_id` are **first-class persisted columns**, populated by
  `MemoryCaptureEngine._build_causal_context`; the execution-event graph and the
  memory graph are unified via `link_event_to_memory("stored_as_memory")`.
- **The gap:** the **app-side** legacy content-domain causal graph is still
  heuristic; promoting/migrating it onto the canonical execution-event layer is
  app-owned. Heavy causal computation depends on the RTR-2 worker model. The
  runtime half is largely complete.

### RTR-8 — PyPI publication — **CLOSED / stale (do not re-track)**

This backlog item is already done: **PYPI-PUBLISH-1 closed 2026-06-14**; the
runtime is published to PyPI and `AINDY/_version.py` is `1.4.3`. The only live
sub-question — whether `aindy-apps-monolith` pins the published package vs.
installing from source — is **apps-side config**, not a runtime gap.

---

**Side finding (cleanup opportunity, not roadmap):** the two **tracked** `.nbc`
files under `AINDY/nodus/stdlib/.nodus/cache/` are **stale cross-machine caches**
— they embed the absolute path `C:\dev\masterplan-infiniteweave-...`, so the
nodus-lang VM treats them as misses and regenerates. They are build droppings
committed by accident, **not** load-bearing precompiled assets, and are safe to
remove from the repo (the dir is now gitignored). Tracked-file removal is a
separate decision.

**Close/advance triggers:** RTR-1 — when a `register_nodus_workflow` surface is
scheduled (keystone for the "apps finish phases without editing runtime"
pattern). RTR-2 — when distributed execution is made the prod default or
per-tenant lanes are required. RTR-3 — when AgentRun↔FlowRun divergence is
observed in production. RTR-5 — when runtime-driven autonomous execution windows
are scheduled. RTR-4/6/7 — when their named gaps block an app phase.
