# Technical Debt

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

Status: Deferred — Verify Before 1.0.0

`pyproject.toml` pins `nodus-lang==1.1.0`. Nodus has shipped through phases 1–7 of
Nodus 2.0, with at least one breaking CLI change. The runtime's Nodus surface must be
audited before the 1.0.0 release to determine whether the pin is still safe.

Action required:
1. Audit all `from nodus*` / `import nodus*` in `AINDY/` to identify what surfaces are used.
2. Review Nodus changelog between 1.1.0 and current for breaking changes to those surfaces.
3. Either bump the pin (update callsites if needed, re-run full test suite) or document
   a deliberate "stay at 1.1.0" decision with rationale in a comment in `pyproject.toml`.

Trigger: must be resolved before tagging 1.0.0.

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
