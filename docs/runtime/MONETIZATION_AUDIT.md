---
title: "Monetization Audit"
api_version: "1.0"
last_verified: "2026-06-07"
status: current
owner: "platform-team"
---
# Monetization Audit

## Purpose

This audit surfaces the gaps between the current runtime architecture and a
billable commercial product. The goal is not to build billing infrastructure now —
it is to make the gaps explicit so they are not rediscovered under deadline pressure
when the first paying customer is ready to onboard.

Companion documents: `LOCAL_AND_CLOUD_AUDIT.md` (infrastructure gaps) and
`DEPLOYMENT_TARGETS.md` (hosted deployment path).

Findings that warrant tracking are marked with a TECH_DEBT tag. See `TECH_DEBT.md`
for the corresponding entries (`BILLING-*` prefix).

---

## Raw Material Already Present

The runtime inadvertently built most of what a billing system needs as
operational infrastructure. Before auditing gaps, it is worth recording what
already exists:

| Asset | Billing signal it represents |
|---|---|
| `EffectRecord` | Every syscall execution — durable, idempotent, timestamped, by user |
| `ExecutionUnit` + `wall_time_ms` | Compute time per request, per tenant |
| `memory_nodes` + embedding count | Storage consumed (vector records, per user namespace) |
| `AgentRun` | Per-agent-run event — the clearest natural billing unit |
| `quota_group` on `execution_units` | Already seeded as a tier enforcement hook (`"free"`, `"pro"`, `"enterprise"`) |
| `tenant_id` on `execution_units` | Billing identity anchor (currently `== str(user_id)` by convention) |
| `RequestMetricWriter` | Per-request metrics already being written to DB |
| `MAX_CONCURRENT_PER_TENANT` + Redis quota key | Concurrency limit already enforced per-tenant in Redis |

---

## Area A — Billing Identity

### Evidence

**`tenant_id`** exists on `AINDY/db/models/execution_unit.py:113` and is indexed
for quota queries. Today it is set by convention: `tenant_id = str(user_id)`.
Nothing in the codebase issues or validates tenant IDs from an external authority.

**`User` model** (`AINDY/db/models/user.py`) has `email`, `is_admin`, `is_active`,
and `hashed_password`. No `plan_tier`, `billing_customer_id`, `subscription_status`,
or external billing reference field exists.

**`quota_group`** on `execution_units` is an orphan policy tag — the field exists,
accepts values, but nothing reads it to adjust quota behavior at request time.
See TECH_DEBT `TENANT-2`.

### Findings

**BILLING-1** — Billing identity is not decoupled from user identity.

The paying entity in a commercial model is an *account* or *organization* — not an
individual user. A single paying customer may have multiple users (team seats).
Currently `tenant_id == str(user_id)`, meaning the billing boundary is a single
user, which cannot represent a team. The `User` model has no billing reference field
(e.g., a Stripe `customer_id`).

Resolution direction: add a `billing_account_id` field to the `User` model (or
introduce a `BillingAccount` model that users belong to) and issue `billing_account_id`
from the control plane at registration time. `tenant_id` should be rebased onto
this identifier. Severity: medium. Tracked as `BILLING-1`.

Trigger: when the first multi-seat or team-plan customer onboards.

---

## Area B — Metering Surface

### Evidence

**Agent runs** (`AgentRun`) carry a `goal`, `status`, `created_at`, and
`wall_time_ms` (post AGENT-RESLIMIT-001). An agent run is the clearest natural
billing unit — it maps 1:1 to a user-initiated action with measurable cost.

**Syscall executions** (`EffectRecord`) are durable and per-user but too granular
for most billing models. An agent run may produce dozens of `EffectRecord` rows.
Useful for internal cost attribution but not for customer-facing line items.

**Memory storage** (`memory_nodes`) carries a per-user namespace path. A record
count or embedding count per user is extractable. No size field per record exists
(embeddings are `Vector(1536)` — fixed size); total storage is a count, not a byte
figure.

**Request volume** — `RequestMetric` rows exist but carry no billing tier or
tenant reference. Useful for overage detection on high-throughput API key callers.

**Wall time** — `ExecutionUnit.wall_time_ms` captures compute time per request.
Useful for compute-based billing but requires aggregation across all execution
units for a given billing period.

### Findings

**BILLING-2** — Metering model is not chosen.

Before any billing infrastructure can be built, the following question must be
answered: what is the primary billing unit?

Candidates, in order of implementation simplicity:

1. **Per-seat** — flat fee per active user per month. Simplest to implement
   (count `User` rows with `is_active=True` per billing account). Maps poorly to
   usage patterns but is easiest to reason about for customers.

2. **Per-agent-run** — charge per `AgentRun` submitted. Natural unit; customers
   understand "I ran the agent 100 times this month." The `AgentRun` table is the
   exact data source. Most AI-adjacent products use this model (OpenAI charges per
   API call; this is the agent-level equivalent).

3. **Usage-based (compute)** — aggregate `wall_time_ms` across `ExecutionUnit`
   rows per billing period. Accurate cost attribution but harder for customers to
   predict their bill.

4. **Hybrid** — base fee (per seat or flat) + overage on agent runs or compute
   above a tier limit. Most cloud infra products converge here.

**Recommendation:** per-agent-run as the primary unit, with a seat-based floor for
team plans. `AgentRun` is the clearest boundary; customers can reason about it
without understanding syscall internals.

Tracked as `BILLING-2`. Trigger: before billing infrastructure is built.

---

## Area C — Plan and Quota Enforcement

### Evidence

**`quota_group`** on `execution_units` accepts policy tags but nothing reads it.
`AINDY/kernel/resource_manager.py:57` enforces `MAX_CONCURRENT_PER_TENANT` from
a process-wide env var — a single constant for all tenants.

**`AINDY_QUOTA_MAX_CONCURRENT`** is the only runtime quota knob. There is no
per-user or per-plan concurrency ceiling, no agent run cap, no memory storage limit,
and no API request rate limit tied to a plan tier.

**`require_admin_principal`** is the only access gate. There is no
`require_plan("pro")` or `require_feature_flag("advanced_memory")` dependency
factory.

### Findings

**BILLING-3** — No plan-tier enforcement path exists.

Even if a billing model is chosen and a control plane issues plan tiers, the runtime
has no way to enforce them at request time. An operator on a "free" plan has
identical access to an operator on an "enterprise" plan.

The enforcement path needs three pieces:

1. A `plan_tier` field on the user (or billing account) — populated from the
   control plane at registration or subscription change.
2. A `require_plan(tier)` FastAPI dependency factory analogous to
   `require_admin_principal` — reads `plan_tier` from the resolved principal and
   raises `HTTP 402 Payment Required` or `HTTP 403 Forbidden` if the tier is
   insufficient.
3. A quota enforcement hook that translates `quota_group` (the seeded field) into
   concrete per-tenant limits: max concurrent executions, max agent runs per month,
   max memory records. The `quota_group` field is the right hook; it needs an
   enforcement path that looks up limits from a policy table rather than a
   process-wide constant.

Tracked as `BILLING-3`. Trigger: when the first paid plan is defined.

---

## Area D — Self-Service Acquisition Funnel

### Evidence

**Current onboarding** requires: `POST /auth/register` → operator emails Shawn →
`aindy-runtime auth promote-admin <email>` on the server → operator can use the
platform. There is no self-service path.

**`AINDY_BOOTSTRAP_ADMIN_EMAIL`** is the automated form of admin promotion, but it
requires an env var set before server restart — an operator action, not a customer
action.

**No Stripe (or equivalent) integration** exists anywhere in the codebase.

**No plan selection surface** exists in the platform SPA. `POST /auth/register` has
no `plan_tier` parameter.

### Findings

**BILLING-4** — No self-service acquisition funnel.

The gap between "I want to use A.I.N.D.Y." and "I am using A.I.N.D.Y." currently
requires direct operator involvement. For a commercial product, the funnel must be:

1. Customer visits landing page → clicks "Get started"
2. Registers at `POST /auth/register` (already public and unauthenticated)
3. Chooses a plan tier (new: `plan_tier` param or post-registration selection flow)
4. Completes payment via Stripe Checkout or embedded Elements
5. Control plane receives `checkout.session.completed` webhook → sets `plan_tier`
   on the user, fires `aindy-runtime auth promote-admin` equivalent via API
6. Customer is redirected to the platform SPA, already admin, already on their plan

Steps 1-2 and 6 already work. Steps 3-5 require:
- A Stripe account and product/price catalog
- A control plane service (separate from this repo) that handles the webhook
- A `set_plan_tier` internal API (not public) callable by the control plane

The control plane is deliberately kept outside this repo — the runtime stays
self-hostable. The commercial layer is the control plane's responsibility.

Tracked as `BILLING-4`. Trigger: before first paid customer onboards.

---

## Area E — Usage Reporting Surface

### Evidence

**`/platform/observability/dashboard`** provides operational metrics (error rate,
request volume, flow state). It is designed for an operator watching system health,
not for a customer reviewing their bill.

**No `/billing/usage` endpoint** exists. The data to serve it is available
(`AgentRun` count, `ExecutionUnit` wall time, `memory_nodes` count), but the
endpoint and the aggregation query do not exist.

**No billing period concept** exists. Usage data is queryable by time range but
there is no notion of a billing cycle start/end, a carry-forward balance, or a
per-period cap.

### Findings

**BILLING-5** — No usage reporting surface.

Customers on any plan beyond free-tier need to see what they are consuming relative
to their plan limits before they are surprised by an overage or a renewal invoice.

Minimum viable surface:
- `GET /platform/billing/usage` — returns current-period agent run count, compute
  wall time, memory record count, and comparison against plan limits.
- Driven by `AgentRun`, `ExecutionUnit`, and `memory_nodes` with a time filter
  anchored to the billing period start date (stored on the billing account).

This endpoint should be read-only and gated by `require_admin_principal` — it is
an operator-facing surface, not a raw API consumer surface.

Tracked as `BILLING-5`. Trigger: when the first plan with usage limits ships.

---

## Area F — Commercial Architecture

### The "who is the customer" question

This must be answered before any billing code is written, because the answer
determines what gets built:

| Customer model | Description | What it implies |
|---|---|---|
| **Operator** (self-hosted license) | A developer/team deploys their own runtime. Pays for a license key that unlocks plan-tier features. No usage metering on our side. | Simple. One-time or annual payment. No per-agent billing. Control plane is minimal (license validation only). |
| **Operator** (hosted by us) | We run the runtime; the operator gets a URL. Pays per agent run or per seat. Full usage metering. | Full metering + quota enforcement required. Control plane is load-bearing. |
| **End-user** (operator's product) | The operator builds a product on top of aindy-runtime and charges their own customers. We charge the operator wholesale. | Operator is our customer. End-user billing is the operator's problem. Same as "hosted by us" from our side. |

**Recommendation:** start with the hosted-by-us operator model (pay per agent run,
tier plan). It is the fastest path to revenue and the most defensible moat (runtime
quality drives retention). Self-hosted license keys can follow once the usage
metering infrastructure is in place — it is a simpler billing model built on the
same foundation.

### The control plane split

The commercial layer (Stripe integration, plan management, webhook handling,
license validation) should live in a separate service — not in this repo. Reasons:

1. `aindy-runtime` must remain self-hostable. Embedding Stripe credentials or
   license-check API calls in the runtime would make self-hosted deployments
   dependent on the commercial control plane being reachable.
2. The control plane calls the runtime's *internal* admin APIs (`promote-admin`,
   future `set-plan-tier`), not the reverse. The runtime does not need to know
   about Stripe.
3. Separation allows the control plane to be iterated on independently without
   touching the runtime's release cycle.

The interface between the control plane and the runtime is:
- A set of internal admin endpoints (already partially there: `promote-admin`,
  future `set-plan-tier`, future `set-quota-group`)
- A shared `SECRET_KEY` or internal API key for control-plane-to-runtime calls

---

## Open Tech Debt

| Entry | Area | Summary | Trigger |
|---|---|---|---|
| `BILLING-1` | A | Billing identity: `tenant_id` not decoupled from `user_id`; no `billing_account_id` | When first multi-seat customer onboards |
| `BILLING-2` | B | Metering model not chosen (per-run vs per-seat vs usage-based) | Before billing infrastructure is built |
| `BILLING-3` | C | No plan-tier enforcement: `quota_group` has no enforcement path, no `require_plan()` | When first paid plan is defined |
| `BILLING-4` | D | No self-service acquisition funnel; admin promotion requires operator action | Before first paid customer onboards |
| `BILLING-5` | E | No usage reporting surface (`GET /platform/billing/usage`) | When first plan with usage limits ships |
