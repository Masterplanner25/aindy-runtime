---
title: "Local and Cloud Distribution Audit"
last_verified: "2026-05-25"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Local and Cloud Distribution Audit

## Purpose

This audit surfaces gaps that the local+cloud framing makes newly visible. The
framing is established in `ARCHITECTURE.md`. The goal here is not to fix gaps —
it is to make them explicit so they are not accidentally re-discovered later.

Findings that warrant tracking are marked with a TECH_DEBT tag. See `TECH_DEBT.md`
for the corresponding entries.

## Scope

Seven areas examined:

- A: Multi-tenancy readiness
- B: Cross-version compatibility surfaces beyond the SDK
- C: Operator "where am I running" clarity
- D: Data residency and sovereignty
- E: Self-update for local installs
- F: Cloud control plane API surface placeholders
- G: Open findings

---

## Area A — Multi-Tenancy Readiness

The runtime today is functionally single-tenant per process in the billing sense:
one operator, one user model, multiple users. A cloud control plane would require
tenant isolation that holds up under adversarial conditions.

### Evidence

**`tenant_id` column exists** in `AINDY/db/models/execution_unit.py:113`:
```python
tenant_id = Column(String(128), nullable=True, index=True)
# Owning tenant. In A.I.N.D.Y.'s single-user-per-tenant model this equals
# str(user_id). Indexed for fast per-tenant quota queries.
```
The concept is seeded and indexed. Today `tenant_id == str(user_id)` by
convention, not enforcement.

**Per-tenant concurrency quota** is enforced in Redis
(`AINDY/kernel/resource_manager.py:57`):
```
aindy:quota:concurrent:{tenant_id}
```
The limit is `MAX_CONCURRENT_PER_TENANT = 5` — a process-global constant
overridable only via env var (`AINDY_QUOTA_MAX_CONCURRENT`), not per-billing-tenant.

**`quota_group` field** exists on `execution_unit.py:133`:
```python
quota_group = Column(String(64), nullable=True)
# Optional policy tag for quota-group overrides (e.g. "premium", "batch").
```
An enforcement path is not yet built for this field.

**Event bus** uses a single global Redis channel (`AINDY/kernel/event_bus.py:70`):
```python
CHANNEL: str = os.getenv("AINDY_EVENT_BUS_CHANNEL", "aindy:scheduler_events")
```
All runtime processes share one pub/sub channel. Per-tenant event stream
separation is absent.

**Sandbox resource limits** (`AINDY/platform_layer/sandbox_runner.py:1173-1174`):
```python
self.pids_limit = int(settings.AINDY_PLUGIN_CONTAINER_PIDS_LIMIT or 0)
self.memory_limit = str(settings.AINDY_PLUGIN_CONTAINER_MEMORY_LIMIT or "").strip()
```
Global env-var configuration, not per-tenant.

**Syscall context** carries `user_id` (not a separate billing `tenant_id`). In
cloud multi-tenant mode, a billing tenant may contain multiple users — the current
context model does not distinguish between them at the syscall level.

### Findings

**TENANT-1** — `tenant_id == str(user_id)` by convention, not enforcement.
The comment in `execution_unit.py` documents this as the current model, but
nothing prevents a future code path from populating it differently. A cloud
control plane needs `tenant_id` to be a durable, independently-issued billing
identity decoupled from `user_id`. Severity: low (no cloud control plane today).
Resolution direction: when cloud onboarding begins, enforce that `tenant_id` is
issued by the control plane and is not derivable from `user_id`.

**TENANT-2** — Per-tenant quota limits are not per-tenant configurable.
`MAX_CONCURRENT_PER_TENANT` is a process-wide constant. In a cloud context,
different tenants would need different concurrency ceilings (premium vs free tiers).
The `quota_group` field is the right hook but has no enforcement path.
Severity: low (single tenant today). Tracked in `TECH_DEBT.md` as `TENANT-2`.
Resolution direction: build enforcement for `quota_group` as a policy lookup key,
or add a per-tenant concurrency limit table driven by control-plane configuration.

**TENANT-3** — Event bus is a single shared channel.
In a cloud multi-tenant context, a WAIT/RESUME event for tenant A must not be
broadcast to tenant B's runtime processes. The current single-channel design
works because all processes share a trust boundary today.
Severity: low (single tenant today). Resolution direction: when cloud multi-tenant
deployment begins, either namespace channels per-tenant or move to a queue-based
mechanism that delivers only to the target process.

**TENANT-4** — Sandbox resource limits are global, not per-tenant.
OCI container resource limits (`pids_limit`, `memory_limit`) apply identically to
all plugin executions regardless of tenant. A cloud control plane needs per-tenant
resource quotas at the container boundary.
Severity: low (single operator today). Resolution direction: pass per-tenant
resource limits from control-plane configuration into the container launch argv.

---

## Area B — Cross-Version Compatibility Beyond the SDK

`DEBT-COMPAT-1` in `TECH_DEBT.md` covers the SDK ↔ runtime version axis. This
area looks at other versioned contracts.

### Evidence

**Extension ABI versions** (`AINDY/kernel/syscall_versioning.py:30`):
```python
ABI_VERSIONS: frozenset[str] = frozenset({"v1"})
```
Only `v1` exists. `EXTENSION_ABI.md` states "experimental ABI markers do not
imply long-term compatibility across minor releases" but does not define a
forward-compatibility window or a deprecation procedure for when v2 is introduced.

**Extension execution model schema version** (`AINDY/platform_layer/extension_execution_model.py:14`):
```python
EXTENSION_EXECUTION_MODEL_SCHEMA_VERSION = "2026-05-22"
```
Date-stamped. No documented policy for what happens when the schema version
advances — plugins that serialize this metadata have no defined migration path.

**Platform UI** is bundled inside the installed wheel (`AINDY/platform/dist/`).
No independent UI version is exposed via health endpoints. Cache busting and
UI compatibility depend entirely on the runtime package version. An operator
running a local install has no way to know if the frontend is serving stale
cached assets after a runtime upgrade without checking the runtime version.

**DB schema contract** (`AINDY/db/schema_contract.py`):
```python
SCHEMA_CONTRACT_VERSION = "2026-05-24.1"
```
Date-stamped. Runtime startup validates against this baseline. No documented
compatibility window: an older runtime against a newer schema fails closed, but
the reverse (newer runtime, older schema requiring `AINDY_SCHEMA_RECONCILE=true`)
has no documented maximum safe gap.

### Findings

**COMPAT-2** — No deprecation or forward-compatibility policy for extension ABI.
When the runtime introduces ABI v2, plugins built for v1 need a documented support
window before v1 is dropped. Without a policy, plugin authors cannot safely plan
upgrades. Severity: medium (affects third-party plugin authors). Tracked in
`TECH_DEBT.md` as `COMPAT-2`. Resolution direction: define a compatibility window
in `EXTENSION_ABI.md` (e.g., "a stable ABI version is supported for at least two
minor runtime releases after a newer stable version ships").

**COMPAT-3** — Platform UI has no independent version surface.
In a cloud-hosted context the control plane might want to confirm the UI version
being served to operators without parsing runtime version strings.
Severity: low. Resolution direction: expose a `ui_version` key in `/health` or
`/api/version` (can be the runtime version initially, with room to diverge later).

---

## Area C — Operator "Where Am I Running" Clarity

### Evidence

**`/health` exposes** `deployment_profile` and `deployment_profile_source`
(`AINDY/routes/version_router.py:18-19`). The profile tells the operator which
infrastructure topology they are in (single-instance, distributed-api, etc.).

**No field distinguishes local install from cloud-hosted.** The four profiles
(single-instance, distributed-api, distributed-worker, hostile-third-party) map
to infrastructure shapes, not to the question "does the operator own this runtime
or does the provider?" An SDK user connecting to a cloud runtime sees the same
health payload structure as a local-install user.

**The `hostile-third-party` profile** (`deployment_contract.py`) is the
cloud-marketplace shape — it enforces the strictest sandbox tier. However,
the connection between this profile and the cloud distribution model is not
documented anywhere; operators must infer it from context.

**No SDK introspection method.** `AINDYClient` has no method to ask "what context
am I connected to?" The SDK is stateless beyond `base_url` and `api_key`; there
is no `client.runtime_info()` or `client.deployment_profile()`.

### Findings

**CLOUD-1** — No health surface distinguishes local-install from cloud-hosted.
In the cloud model, the SDK operator needs to know they are talking to a
provider-managed runtime (and what guarantees that implies) vs. their own local
install. Severity: low (no cloud runtime today). Resolution direction: add a
`distribution_context` field to the deployment contract payload (values:
`"local-install"`, `"cloud-hosted"`) that the cloud control plane can inject at
registration time. Optionally expose in `/health`.

**CLOUD-2** — `hostile-third-party` profile's cloud-marketplace role is implicit.
The profile name is accurate but the connection to "this is what the cloud runtime
uses for multi-tenant plugin execution" is not documented in any operator-facing
doc. Severity: low. Resolution direction: add a sentence to `DEPLOYMENT_PROFILES.md`
naming this profile as the expected cloud-marketplace and untrusted-tenant-plugin
profile.

---

## Area D — Data Residency and Sovereignty

### Evidence

**No `AINDY_DATA_REGION` env var** or equivalent was found anywhere in the
codebase (`grep` across all `*.py` and config files, zero matches). There is no
mechanism for operators to declare or enforce data residency.

**Memory writes** in `AINDY/memory/memory_persistence.py` are user-scoped by
path namespace (e.g., `/memory/{user_id}/...`) but carry no geographic or
jurisdictional metadata. There is no per-tenant namespace boundary that would
prevent cross-tenant path reads in a multi-tenant future.

**No audit log surface.** Write operations (memory writes, flow runs, syscall
effects) are observable via the EffectRecord table and scheduler logs, but there
is no separate audit trail — a structured log of "which operator/user/tenant
wrote what, when" — accessible to operators for compliance purposes.

### Findings

**DATA-1** — No data residency mechanism.
Cloud operators in regulated industries (GDPR, HIPAA, SOC 2 Type II) need to
declare which region data is stored in and enforce that writes stay within that
boundary. No such mechanism exists. Severity: low (no cloud runtime today).
Tracked in `TECH_DEBT.md` as `DATA-1`. Resolution direction: define an
`AINDY_DATA_REGION` env var and a corresponding metadata field in the deployment
contract. Actual region-routing enforcement requires control-plane work outside
this repo.

**DATA-2** — No audit log surface for write operations.
An audit trail ("who wrote what, when, from which tenant") is a compliance
prerequisite for cloud-hosted multi-tenant deployments. The EffectRecord table
provides durability for idempotency but is not structured as an operator-accessible
audit log. Severity: low (no multi-tenant cloud today). Resolution direction: when
cloud onboarding begins, evaluate whether EffectRecord can be promoted to the audit
surface or if a separate `AuditRecord` model is needed.

---

## Area E — Self-Update for Local Installs

### Evidence

**CLI subcommands**: Only `aindy-runtime sandbox` dispatches to a non-server
function (`AINDY/runtime_only.py:50`). There is no `aindy-runtime upgrade`,
`aindy-runtime version`, or `aindy-runtime migrate` subcommand.

**README upgrade path**: `README.md` documents only the dev install path
(`pip install -e .`). There is no documented production upgrade procedure —
pip upgrade command, environment variable sequence (`AINDY_SCHEMA_RECONCILE=true`),
or rollback guidance.

**Schema reconciliation**: `AINDY_SCHEMA_RECONCILE=true` is the mechanism to
reconcile an out-of-date schema on startup. It is documented in `README.md` but
is a raw env var with no CLI affordance. An operator upgrading a local install
must know to set this before restarting.

### Findings

**LOCAL-1** — No documented production upgrade path for local installs.
Local-install operators have no single reference for "how to upgrade the runtime
from version N to N+1 safely." The schema reconciliation mechanism exists but is
not surfaced as a procedure. Severity: medium (affects all local install operators
at upgrade time). Tracked in `TECH_DEBT.md` as `LOCAL-1`. Resolution direction:
add a "Upgrading" section to `README.md` and/or `RUNTIME_ONLY_DEPLOYMENT.md`
covering pip upgrade, schema reconciliation, and rollback steps.

**LOCAL-2** — No `aindy-runtime version` CLI subcommand.
Local-install operators commonly need to confirm which version is running, especially
after upgrades. The running version is accessible via `GET /api/version` but requires
the server to be running. A `aindy-runtime version` subcommand would provide this
without starting uvicorn. Severity: low. Resolution direction: add a `version`
subcommand to `AINDY/runtime_only.py:main()` that prints the package version from
`AINDY/_version.__version__` and exits.

---

## Area F — Cloud Control Plane API Surface Placeholders

The cloud control plane does not exist yet. This area audits whether the runtime
has the API hooks that a future control plane would call.

### Evidence

**No tenant registration endpoint.** No route in `AINDY/routes/` accepts tenant
onboarding requests (tenant identity, billing credentials, resource policy). The
dynamic node registration endpoint (`POST /platform/nodes/register`) is for
registering extension nodes within a running runtime, not for registering a runtime
process with a control plane.

**No runtime node registration.** There is no endpoint for a runtime node to
announce itself to a control plane (heartbeat, capability declaration, fleet
membership). The `deployment_contract_summary()` function produces a rich payload
that would be appropriate for such a registration, but there is no push mechanism.

**No observability aggregation surface.** Per-node health is exposed at
`GET /health` and `GET /health/sandbox`. There is no batch or streaming surface
for aggregating health across a fleet of runtime nodes.

### Findings

**CLOUD-3** — No runtime node registration mechanism.
A cloud control plane needs to know which runtime nodes exist, what version they
are running, and what their capability posture is. The deployment contract payload
is the right content — the missing piece is a push mechanism (outbound heartbeat
or pull endpoint). Severity: low (no control plane today). Resolution direction:
when cloud work begins, consider a `POST /platform/control-plane/register` endpoint
that the runtime calls at startup with its deployment contract payload, or a
reverse-registration pull by the control plane.

**CLOUD-4** — `deployment_contract_summary()` is internal, not a stable API surface.
It is exposed via the smoke test import contract but not listed in
`PUBLIC_RUNTIME_SURFACES.md`. If a control plane were to depend on it today, it
would be consuming an undeclared surface. Severity: informational. Resolution
direction: when CLOUD-3 is addressed, promote the relevant deployment contract
fields to a stable surface in `PUBLIC_RUNTIME_SURFACES.md`.

---

## Area G — Open Findings

**G-1** — `quota_group` field is an orphan hook.
`execution_unit.quota_group` (`AINDY/db/models/execution_unit.py:133`) accepts
policy tags ("premium", "batch") but no enforcement path exists — nothing reads
this field to adjust quota behavior. It is either the right foundation for
per-tenant policy or a future cleanup candidate. Should be decided before cloud
onboarding, since building on it at that point without deliberate intent would
be a gap. Tracked in `TECH_DEBT.md` as `TENANT-2` (resolution path includes
evaluating this field). Severity: low.

**G-2** — Local+cloud bridge role of the SDK is not reflected in `REPO_COMPATIBILITY_POLICY.md`.
`REPO_COMPATIBILITY_POLICY.md` defines the runtime ↔ apps-monolith pip dependency
range but does not cover SDK ↔ runtime HTTP compatibility. This is the primary
gap addressed by `DEBT-COMPAT-1` in `TECH_DEBT.md`. Documenting it here for
cross-reference completeness.

---

## TECH_DEBT Entries Added

The following new entries were added to `TECH_DEBT.md` as a result of this audit:

| Entry | Area | Summary |
|---|---|---|
| `TENANT-2` | A | Per-tenant quota limits not configurable; `quota_group` field has no enforcement |
| `COMPAT-2` | B | No deprecation/forward-compat policy for extension ABI versions |
| `DATA-1` | D | No data residency mechanism or `AINDY_DATA_REGION` env var |
| `LOCAL-1` | E | No documented production upgrade path for local installs |

Lower-severity findings (TENANT-1, TENANT-3, TENANT-4, COMPAT-3, CLOUD-1,
CLOUD-2, CLOUD-3, CLOUD-4, LOCAL-2, G-1, G-2) are documented here and in the
individual area findings above. They are not tracked separately in `TECH_DEBT.md`
to avoid noise before a cloud control plane exists.
