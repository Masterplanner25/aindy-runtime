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

**Status:** Tracked, deferred to 1.0.1 or 1.1.

**Discovered:** 2026-05-26 during pre-tag UX visual verification.

**Context:** `aindy-runtime sandbox` currently emits a 297-line structured JSON document directly to stdout. The data is correct and complete (platform detection, container backend status with real failure-mode details, full capability matrix for all four supported platforms, sandbox verification posture, trusted Python inventory). The format is appropriate for machine consumption (API endpoints, audit pipelines, capability-matching engines) but presents friction for a human running the command at a terminal.

The most actionable information — e.g., "Docker Desktop daemon not reachable, start Docker Desktop and enable Linux containers mode" — is nested five levels deep in `plugin_sandbox_platform.current_container_backend_detection.operator_note`. A human user wanting to know "does sandboxing work on my system, and if not what do I do" must mentally parse the JSON to extract that answer.

**Resolution path:**
1. Add a human-readable default output mode that summarizes the JSON document into ~15 lines covering: platform, highest supported sandbox tier, production-safe status, container runtime detection, the most relevant degraded-mode reason and its fix, database verification status, trusted Python summary.
2. Move current JSON output behind a `--json` flag for machine consumers.
3. Keep the underlying posture-collection logic unchanged. This is a presentation-layer fix, not a data-layer change.

**Open question for resolution time:** Should `--json` be the only path to machine output, or should there be other formats (`--format yaml`, `--format compact`)? Defer the decision until use cases surface; YAGNI until then.

**Reopen trigger:** Pre-1.0.1 release work, OR first user report of sandbox output confusion, whichever comes first.

**Estimated effort:** ~1 hour for the human-readable formatter + `--json` flag plumbing. Low regression risk because the change is additive.

**Discovered via:** Pre-tag UX visual verification (the audit-arc that found this also confirmed every other v1.0 surface, and the JSON-wall finding was deemed correct-but-unpolished rather than incorrect — see conversation history for the full reasoning). The "Discovered via" line is intentional — it captures the reasoning for not fixing now, so future-you doesn't reopen this thinking "why was this allowed to ship."

---

## IDEM-6 — Multi-Instance Bootstrap Race

Status: Deferred — Low Priority

Source: `docs/runtime/IDEMPOTENCY_CONTRACT.md` Open Question #1.

First-ever blank-DB deploy with multiple runtime instances starting simultaneously can
race on `CREATE TABLE`. `checkfirst=True` in `create_all` mitigates but does not fully
eliminate the race. Fix is `pg_try_advisory_lock` around the bootstrap path in
`AINDY/db/database.py` (or whichever function calls `Base.metadata.create_all`).

Trigger: revisit before any multi-instance cold-start deployment in production.

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
(container-grade, not strong-sandbox-vm), and that macOS escape suite certification is
pending — first run required before certifying a macOS deployment.
2 new unit tests in `tests/unit/test_sandbox_runner.py` (64 total).

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

Status: Deferred — Low Priority

Source: `docs/runtime/LOCAL_AND_CLOUD_AUDIT.md` Area B, finding COMPAT-2.

`ABI_VERSIONS = frozenset({"v1"})` and the `EXTENSION_ABI.md` policy states
"experimental ABI markers do not imply long-term compatibility" but defines no
forward-compatibility window or deprecation procedure. When the runtime introduces
ABI v2, plugin authors need a documented support window before v1 is dropped.

Resolution path: define a compatibility window in `EXTENSION_ABI.md` — e.g.,
"a stable ABI version is supported for at least two minor runtime releases after
a newer stable version ships."

Trigger: before any ABI version other than v1 is introduced.

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

Status: Deferred — Low Priority

Source: `docs/runtime/LOCAL_AND_CLOUD_AUDIT.md` Area E, finding LOCAL-1.

The README documents only the dev install path (`pip install -e .`). There is no
documented production upgrade procedure: pip upgrade command, environment variable
sequence (`AINDY_SCHEMA_RECONCILE=true`), or rollback guidance. Local-install
operators face this gap at every upgrade.

Resolution path: add an "Upgrading" section to `README.md` and/or
`RUNTIME_ONLY_DEPLOYMENT.md` covering:
1. `pip install --upgrade aindy-runtime`
2. Verify new version: `aindy-runtime version` (or `/api/version` while running)
3. Set `AINDY_SCHEMA_RECONCILE=true` before restart when a schema bump is expected
4. Rollback: reinstall the previous version and restart without reconcile

Trigger: before the 1.0.0 release.

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

**Status:** 1.x step CLOSED (2026-06-05) — 2.0 removal still pending.

**1.x implemented:** `resolve_event_bus_redis_url()` now emits `DeprecationWarning`
when `AINDY_REDIS_URL` is set, directing operators to migrate to `REDIS_URL`.
`import warnings` added to `event_bus.py`; 2 regression tests added to
`tests/unit/test_event_bus_redis_url.py` (9 total pass).

**Remaining (2.0 step):** Remove `AINDY_REDIS_URL` from `event_bus.py`, `config.py`,
and `.env.example`. Before that removal, grep for other `AINDY_*` aliases that shadow
standard env vars (e.g., `AINDY_SKIP_MONGO_PING` vs `SKIP_MONGO_PING`) and consolidate
them in a single pass.

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

**Status:** Deferred — Low Priority

**Discovered:** 2026-05-27 during `.env.example` rewrite.

**Context:** The runtime reads `AINDY/.env` at startup (`Settings env_file =
Path(__file__).parent / ".env"`). The canonical operator reference is therefore
`AINDY/.env.example` (written 2026-05-27). The repo-root `.env.example` was
replaced with a forwarding stub that explains the split and routes operators to
the canonical file.

Two `.env.example` files — one real, one a sign pointing to the real one — is
documentation debt. The stub earns its keep during the transitional period before
the Docker Compose port lands, because Compose defaults to reading `root/.env`
and operators expect to find an example there.

**Resolution condition:** When `docker-compose.yml` is authored with an explicit
`env_file: AINDY/.env` directive (or the Compose port otherwise makes the
canonical location self-evident), delete `root/.env.example` entirely. Operators
copying from `AINDY/.env.example` will have the correct file; Compose's explicit
`env_file:` directive will confirm the location.

**Do not resolve early** by pointing Compose at root `.env` — that couples this
cleanup to the wrong decision (root `.env` bypasses the runtime's own `env_file`
setting and creates a second source of truth for Settings values).

---

## CONFIG-ENV-EXAMPLE-DRIFT-1 — No automated check for .env.example / Settings drift

**Status:** Deferred — Low Priority

**Discovered:** 2026-05-27 during `.env.example` drift audit.

**Context:** The drift audit (2026-05-27) identified ~40 environment variables
active in the codebase that were absent from the then-current `.env.example`.
The audit was manual: grep `os.getenv(...)` calls across `AINDY/**/*.py`,
cross-reference against `Settings` fields in `config.py`, diff against
`.env.example`. This process is not reproducible on demand without repeating the
manual work.

Every new `os.getenv("NEW_VAR")` call or `Settings` field added without a
corresponding `.env.example` entry silently widens the drift gap.

**Resolution path:**
1. Write `scripts/check_env_example_coverage.py` that:
   - Extracts all `os.getenv("VAR")` string literals from `AINDY/**/*.py` via AST
     parse (not regex, to handle multi-line calls).
   - Extracts all field names from `Settings` in `config.py`.
   - Parses `AINDY/.env.example` for defined variable names (both uncommented and
     commented-out forms).
   - Reports variables present in code but absent from `.env.example`.
2. Add the script to CI as an advisory check (warn, not fail) initially; promote
   to blocking after a false-positive-free run period.

**Intentional exclusions the script must handle:**
- Test-only vars: `PYTEST_CURRENT_TEST`, `TESTING`, `TEST_MODE`,
  `AINDY_TEST_STRICT_SYSTEM_EVENTS`, `AINDY_DEBUG_SYSTEM_EVENTS`.
- System/OS vars: `HOSTNAME`, `PATH`, `SYSTEMROOT`.
- Deprecated aliases already documented in `.env.example`: `AINDY_REDIS_URL`,
  `AINDY_STUCK_RUN_THRESHOLD_MINUTES`.
- Infrastructure-only Docker Compose vars: `POSTGRES_*`, `MONGO_INITDB_*`.
- Computed/internal fields: `VERSION`, `API_VERSION`, `API_MIN_CLIENT_VERSION`.

**Design note for implementation:** Consider a sentinel-comment annotation in
`config.py` itself (e.g., `# env_example: skip`) rather than maintaining a
separate external exclusion list that drifts from the code. Adding a new
`Settings` field then forces a conscious decision — annotate it as skip-worthy
or accept that it needs a `.env.example` entry — rather than silently inheriting
exclusion-list coverage it never earned.

---

## STRIPE-SETTINGS-CLEANUP-1 — Stripe Settings fields with no readers

**Status:** Deferred — Low Priority

**Discovered:** 2026-05-27 during `.env.example` drift audit.

**Context:** `AINDY/config.py` declares two `Settings` fields:

```python
STRIPE_SECRET_KEY: str | None = None
STRIPE_WEBHOOK_SECRET: str | None = None
```

A codebase-wide grep for `STRIPE_` returned zero hits outside `config.py`. No
route, service, or worker reads these values. They were excluded from
`AINDY/.env.example` on that basis.

**Two possible states — confirm before closing:**
1. **Vestigial:** Payments were prototyped and the code was removed but the
   Settings fields were not. → Remove both fields from `config.py`. File
   PAYMENTS-ARCHITECTURE-1 as a future arc if payments are still planned.
2. **Planned but unimplemented:** Payments are on the roadmap and someone added
   the fields in anticipation. → Fields stay; add `STRIPE_SECRET_KEY` and
   `STRIPE_WEBHOOK_SECRET` to `AINDY/.env.example` Group 12 (Observability)
   or a new Group 13 (Payments) once the implementation arc begins.

**Resolution:** Determine which state applies. If vestigial, remove in the same
hygiene pass as PERMISSION-SECRET-CLEANUP-1.

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

## PYPI-PUBLISH-1 — Dockerfile uses local wheel build pending PyPI publish

**Status:** Deferred — blocks on PyPI publish decision.

**Discovered:** 2026-05-27 during Dockerfile authoring (`pip index versions
aindy-runtime` returned no matching distribution).

**Context:** `Dockerfile` Stage 1 builds a wheel from local source
(`python -m build --wheel`) and installs it into a relocatable prefix.
This works correctly for compose-port and local deployments but means
every `docker build` rebuilds the wheel from scratch. The intent at 1.0.0
is that operators `pip install aindy-runtime` from PyPI, not build from
source.

**Transition path (when aindy-runtime is published to PyPI):**
1. In `Dockerfile` Stage 1, replace:
   ```dockerfile
   WORKDIR /src
   COPY . /src
   RUN pip install build \
       && python -m build --wheel --outdir /dist /src \
       && pip install --prefix=/install /dist/*.whl
   ```
   with:
   ```dockerfile
   RUN pip install --prefix=/install "aindy-runtime==1.0.0"
   ```
2. Remove the `build-essential` and `libpq-dev` apt packages from Stage 1
   (no longer needed to compile wheels from source — PyPI ships pre-built
   wheels for the target platform). Keep `libpq-dev` only if any transitive
   dependency still requires it at build time.
3. Stage 2 is unchanged.
4. Close this entry.

**Reopen trigger:** PyPI publish of `aindy-runtime` at any version.

---

## MONITORING-GRAFANA-1 — Grafana excluded from compose monitoring profile

**Status:** Deferred — Low Priority

**Discovered:** 2026-05-27 during `docker-compose.yml` authoring.

**Context:** The runtime ships a fully instrumented Prometheus `/metrics`
endpoint (`routing.py:29`, `platform_layer/metrics.py`) with 40+ named metric
families covering execution pipeline, DB pool, scheduler, async queue, LLM
clients, embedding generation, circuit breakers, and system health tier. The
compose `monitoring` profile includes a Prometheus service scraping
`runtime:8000/metrics`. Grafana is excluded because no dashboards or
provisioning config exist in the repo — a blank Grafana with no datasources
or panels is noise, not value.

**Resolution path (when dashboards are authored):**
1. Create `monitoring/grafana/provisioning/datasources/prometheus.yml`
   (auto-registers the compose Prometheus as a datasource).
2. Create `monitoring/grafana/dashboards/aindy-runtime.json` (dashboard JSON,
   can be exported from a running Grafana instance after manual setup).
3. Add `grafana` service to the `monitoring` profile in `docker-compose.yml`:
   ```yaml
   grafana:
     image: grafana/grafana:10.x.x
     profiles: [monitoring]
     ports: ["3001:3000"]
     volumes:
       - ./monitoring/grafana/provisioning:/etc/grafana/provisioning:ro
       - grafana-data:/var/lib/grafana
     environment:
       GF_AUTH_ANONYMOUS_ENABLED: "true"
       GF_AUTH_ANONYMOUS_ORG_ROLE: Viewer
     depends_on: [prometheus]
   ```
4. Add `grafana-data` to the compose `volumes:` block.
5. Close this entry.

**Suggested first dashboards** (derived from the metric families in
`platform_layer/metrics.py`): execution rate/latency by route, DB pool
pressure gauge, async queue depth + DLQ depth, AI circuit breaker state,
system health tier.

---

## COMPOSE-PROD-PORTS-1 — Database ports published for dev convenience

**Status:** Deferred — Low Priority

**Discovered:** 2026-05-27 during `docker-compose.yml` authoring.

**Context:** `docker-compose.yml` publishes host ports for `postgres` (5432),
`redis` (6379), and `mongo` (27017). This is deliberate for local development
and debugging — operators can connect a DB client from the host without
exec'ing into a container. The `api` service is the only service intended for
external traffic; DB services should only be reachable on the internal compose
network in production.

**Risk:** If `docker-compose.yml` is used unchanged on a cloud VM with a
public IP, `postgres:5432`, `redis:6379`, and `mongo:27017` are exposed to the
internet. This is a real security footgun.

**Mitigations already in place:**
- `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `MONGO_INITDB_ROOT_PASSWORD` must
  be set; the services will not start with empty passwords in production.
- `README.md` quickstart notes that published DB ports are for local
  development only.

**Resolution path:**
1. Author `docker-compose.prod.yml` as a Compose override file that removes
   `ports:` blocks from `postgres`, `redis`, and `mongo`. Operators deploy
   with `docker compose -f docker-compose.yml -f docker-compose.prod.yml up`.
2. Alternatively, document the `ports:` blocks with a `# dev only` comment and
   gate them on a profile (e.g., `profiles: [dev]`) so the default compose up
   does not publish them.

**Trigger:** Before any production cloud deployment of the compose stack.

---

## PROMETHEUS-PIN-1 — prom/prometheus uses :latest tag

**Status:** Deferred — Low Priority

**Discovered:** 2026-05-27 during `docker-compose.yml` authoring.

**Context:** `docker-compose.yml` uses `prom/prometheus:latest` for the
monitoring profile. This is inconsistent with the pin-everything discipline
elsewhere in the stack (SHA-pinned CI actions, fully pinned Python deps,
`pip-audit` CVE gating). `:latest` means a `docker compose pull` can silently
change the Prometheus version.

**Prometheus is an optional monitoring add-on** (not a core dependency), so
the inconsistency is low-risk — a Prometheus version bump is unlikely to break
the runtime. But for reproducibility and audit purposes, pinning is correct.

**Resolution:** Replace `prom/prometheus:latest` with a pinned version, e.g.:
```yaml
image: prom/prometheus:v2.54.1
```
Check https://hub.docker.com/r/prom/prometheus/tags for the current stable
release at pin time.

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

**Status:** Deferred — Low Priority

**Discovered:** 2026-05-28 during PLATFORM-AUTH-ACQUISITION-1 implementation.

**Context:** `platform/src/api/_core.js` resolves the API base at Vite build time:
```javascript
const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
```
When `VITE_API_BASE_URL` is unset (which it is in the current Docker build), `http://localhost:8000`
is baked into `AINDY/platform/dist/assets/index-*.js`. This works for local dev
(`localhost:8000` resolves to the runtime), but on any remote host the browser's `fetch`
goes to localhost on the *client machine*, not the server, silently failing all auth and
API calls.

**Why deferred:** The runtime is currently tested in local/Docker-compose deployments only.
Remote hosting is not yet in scope for 1.0.x.

**Resolution path (before any remote/cloud deployment):**
1. Add `ARG VITE_API_BASE_URL` and `ENV VITE_API_BASE_URL` to the Dockerfile platform
   build stage (if platform is built in Docker) or document it as a required build arg.
2. Pass it in `docker-compose.yml` under `build.args` keyed to the public-facing runtime URL.
3. For operator-built deployments, document in README that `VITE_API_BASE_URL` must be set
   before `npm run build` in `platform/`.
4. Close PYPI-PUBLISH-1 and PLATFORM-UI-ENV-1 in the same pass — at PyPI publish time,
   platform/dist will need to be rebuilt with a real URL or ship without the embedded base.

**Reopen trigger:** Any deployment where the runtime is accessed from a hostname other
than `localhost`.

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

**Remaining open item:** PLATFORM-UI-ENV-1 (localhost baked into bundle for remote hosts).

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

**Status:** Open — deferred until backend route lands.

**Discovered:** 2026-06-03 during `_routes.js` audit (ROUTES reconcile pass against live OpenAPI).

**Context:** `ROUTES.OPERATOR.FLOW_STRATEGIES` resolves to `/platform/flows/strategies`. No
handler for this path exists in the runtime OpenAPI (verified 2026-06-03). The constant is
syntactically live in `@aindy/ui-kit/src/api/_routes.js`; any NavLink referencing it must
check `FEATURE_FLAGS.OPERATOR_FLOW_STRATEGIES` (default `false`) before rendering.

The route likely belongs in `AINDY/routes/platform/flows_router.py` alongside the existing
`/platform/flows/registry` handler. It would expose the set of registered flow strategies
(priority tier configuration, scheduling policies, etc.) for operator visibility.

**What unblocks it:** A `GET /platform/flows/strategies` handler is registered and visible
in the runtime OpenAPI. Flip `FEATURE_FLAGS.OPERATOR_FLOW_STRATEGIES` to `true` in
`@aindy/ui-kit/src/api/_routes.js`.

---

## OPER-DEFER-002 — `/automation/logs` group not served by runtime

**Status:** Open — lives in monolith (aindy-apps); migration path TBD.

**Discovered:** 2026-06-03 during `_routes.js` audit (ROUTES reconcile pass against live OpenAPI).

**Context:** Three constants are deferred behind `FEATURE_FLAGS.OPERATOR_AUTOMATION_LOGS`
(default `false`):

| Constant | Path |
|---|---|
| `AUTOMATION_LOGS` | `GET /automation/logs` |
| `AUTOMATION_LOG` | `GET /automation/logs/{logId}` |
| `AUTOMATION_REPLAY` | `POST /automation/logs/{logId}/replay` |

None resolve in the runtime OpenAPI. These routes currently live in the aindy-apps monolith.
All three constants remain syntactically live in `ROUTES.OPERATOR`; NavLinks must check
`FEATURE_FLAGS.OPERATOR_AUTOMATION_LOGS` before rendering.

**What unblocks it:** Either:
1. The automation logging subsystem is migrated to aindy-runtime (adds the three paths to the
   runtime OpenAPI), or
2. A monolith-proxy pattern is established that routes `/automation/logs` from the runtime
   SPA to the monolith host.

When the backend paths land, flip `FEATURE_FLAGS.OPERATOR_AUTOMATION_LOGS` to `true` in
`@aindy/ui-kit/src/api/_routes.js`.

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

**Status:** Open (default raised to 300 000 ms in v1.0.0 as mitigation; accounting fix deferred)

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

**Do not publish `@aindy/ui-kit@1.0.5+` until the architectural option is selected and
implemented.** The quarantine commits are safe in source history but must not reach npm.

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
