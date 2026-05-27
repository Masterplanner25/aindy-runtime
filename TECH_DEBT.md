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

Status: Deferred — Low Priority

Source: `docs/runtime/IDEMPOTENCY_CONTRACT.md` Open Question #2.

Syscall registration is not complete until Phase 8 of startup. HTTP traffic that arrives
between DB-ready and syscall-registry-ready may dispatch against an incomplete registry
and receive spurious "syscall not found" errors. The health endpoint (`/health`) does not
currently assert registry completion, so load balancers may route traffic too early.

Fix is small: extend the health check to assert that `len(SYSCALL_REGISTRY) >= N` (where
N is the expected count of registered syscalls after full boot). See
`AINDY/kernel/syscall_registry.py` and whichever route/service exposes `/health`.

Trigger: revisit the next time the health endpoint is touched for any reason.

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

Status: Deferred — Low Priority

Source: `C2_SANDBOX_AUDIT.md` "What This Audit Does NOT Cover" / `ISOLATION_MODEL_PLAN.md` Gap 4 (C3 remainder).

Strong-sandbox and `hostile-third-party` profile support remains Linux-only.
`STRONG_SANDBOX_SUPPORTED_HOST_PLATFORMS = (PLATFORM_LINUX,)` and
`HOSTILE_THIRD_PARTY_SUPPORTED_HOST_PLATFORMS = (PLATFORM_LINUX,)` are unchanged.
Non-Linux hosts can reach `container-sandbox-certified` (C2 — closed) but not
`strong-sandbox-certified`. Closing this requires platform-specific sandbox runtimes
(Windows Containers, WSL-mediated isolation, macOS Virtualization.framework) —
infrastructure work outside current scope.

Trigger: when there is a platform-specific sandbox runtime delivering strong-sandbox-tier
assurance on a non-Linux host.

Condition to reopen: A non-Linux host platform gains a supported sandbox runner type
with assurance class `strong-sandbox-tier`, verified through the shared worker policy
certification suite (`tier_status: certified` at `strong-sandbox-certified`).

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

**Status:** Deferred — Medium Priority (next minor release window)

**Context:** pip-audit flags PYSEC-2026-161 (starlette reconstructs the requested URL
from the HTTP `Host` header without sanitization). The fix is starlette 1.0.1, which
requires fastapi >= 0.135.0. The 2026-05-25 security pass bumped us from fastapi 0.119.0
to 0.121.0 + starlette 0.49.1 (the minimum compatible pairing that fixed CVE-2025-62727,
the Range-header DoS). Going further to starlette 1.0.1 + fastapi 0.135.0 is a
14-minor-version FastAPI jump and is deferred to avoid unplanned scope creep.

PYSEC-2026-161 is exempted in `.github/workflows/security-audit.yml` with a comment;
documented in `docs/runtime/SECURITY_POLICY.md` under Accepted Findings.

**Resolution path:**
1. Upgrade `fastapi` from `0.121.0` to `0.135.0` in `requirements.txt` and `pyproject.toml`.
2. Upgrade `starlette` from `0.49.1` to `1.0.1` in the same files.
3. Remove `--ignore-vuln PYSEC-2026-161` from `security-audit.yml`.
4. Remove the PYSEC-2026-161 entry from `docs/runtime/SECURITY_POLICY.md` Accepted Findings.
5. Run full integration tests — FastAPI 0.135 may have breaking changes vs 0.121.

**Reopen trigger:** Any planned maintenance window with ~1 hour budget; or if deployment
posture shifts to include direct public internet exposure (elevates urgency to High SLA).

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

**Verified during investigation (2026-05-25):**
- ui-kit `tsconfig.json` has `"strict": true` — TypeScript null-safety guardrails are active.
- The `safeMap()` invariant in apps-monolith addresses a problem ui-kit's strict-mode TypeScript already prevents at compile time. No need to port the lint rule to ui-kit.
- `eslint-plugin-react-refresh` correctly absent from ui-kit (Vite HMR dev-server guard, not relevant for a published library).

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
