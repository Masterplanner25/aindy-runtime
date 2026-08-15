---
title: "Cloud Deployment Targets"
api_version: "1.0"
last_verified: "2026-06-07"
status: current
owner: "platform-team"
---
# Cloud Deployment Targets

## Purpose

This document records what is already in place that makes hosted deployment viable,
what the shortest path to a single-operator cloud deployment looks like, and what
becomes load-bearing when the target shifts to true multi-tenant SaaS.

Companion documents: `LOCAL_AND_CLOUD_AUDIT.md` (infrastructure gaps) and
`MONETIZATION_AUDIT.md` (billing and commercial architecture).

---

## What Is Already In Place

The following pieces make cloud deployment viable today without code changes:

| Already exists | Why it matters for cloud |
|---|---|
| `VITE_API_BASE_URL = ""` (relative URL) | SPA works on any origin — no localhost baked in. PLATFORM-UI-ENV-1 closed 2026-06-05. |
| `nginx/nginx.tls.conf` + `docker-compose.prod.yml` | TLS termination + internal port sealing. Production-ready reverse proxy config already ships. |
| `deployment_contract_summary()` | Rich version/capability/posture payload — the right content for a cloud control-plane registration handshake. |
| `tenant_id` on `execution_units` | Billing identity anchor is seeded (currently `== str(user_id)` by convention). |
| `hostile-third-party` deployment profile | Already named as the strictest sandbox tier — the expected profile for cloud-marketplace and untrusted-tenant-plugin execution. |
| `AINDY_BOOTSTRAP_ADMIN_EMAIL` + `aindy-runtime auth promote-admin` | Operator admin bootstrap flow is solved. No code change needed for a hosted single-operator deployment. |
| `quota_group` on `execution_units` | Policy tier hook seeded (e.g., `"free"`, `"pro"`, `"enterprise"`). Enforcement path not yet built. |

---

## Deployment Path

### Stage 1 — Hosted for a Single Operator

**Target:** One paying customer deploys aindy-runtime for their own use. Think
"self-hosted but in the cloud" — one org, one Postgres database, one set of API keys.

**What it takes:**

1. **Platform manifest** — translate `docker-compose.prod.yml` into a
   platform-specific deployment spec. The compose file is effectively the spec;
   the work is translation, not architecture.

   Candidates (in order of fit):
   - **Railway** — `railway.json` or `railway.toml`. Native Postgres with pgvector
     available as a plugin. Easiest path for non-infrastructure teams.
   - **Render** — `render.yaml`. Managed Postgres add-on; pgvector enabled on
     request. `docker-compose` import is a first-class feature.
   - **Fly.io** — `fly.toml`. More control over regions; pgvector via Supabase or
     a Fly-hosted Postgres with the extension enabled. Better for latency-sensitive
     or multi-region deployments.
   - **Digital Ocean App Platform** — YAML spec. Managed Postgres, App Platform
     handles TLS, no nginx needed.

2. **Managed Postgres + pgvector** — the only non-trivial infrastructure dependency.
   Most platforms have this as a first-class add-on. Supabase has pgvector natively
   and can serve as the database layer for any of the above platforms.

3. **Required env vars at deploy time:**
   ```
   DATABASE_URL=postgresql://...
   SECRET_KEY=<64-char random>
   OPENAI_API_KEY=sk-...
   AINDY_BOOTSTRAP_ADMIN_EMAIL=operator@example.com
   REDIS_URL=redis://...   # or omit for single-instance (Redis optional)
   ```

   > **Corrected 2026-08-13.** This block said `AINDY_REDIS_URL`. That alias was removed on
   > 2026-06-06 (`EVENTBUS-REDIS-URL-CONSOLIDATION-1`) and the event bus, cache and
   > `ResourceManager` all read **`REDIS_URL`** only. The failure mode is quiet and *partial*:
   > `AINDY/platform_layer/rate_limiter.py:67` still honours the old name as a fallback, so an
   > operator who sets only `AINDY_REDIS_URL` gets a working rate limiter while the event bus
   > and per-tenant concurrency counters silently fall back to `redis://localhost:6379/0` — a
   > half-distributed deployment that looks configured.

4. **Alembic on startup** — `docker-compose.yml` already runs `alembic upgrade head`
   before the server starts. All cloud platforms that run Dockerfiles inherit this.
   No separate migration step needed for a greenfield deployment.

**Estimated effort:** Weeks, not months. The majority is configuration and testing
against a cloud Postgres, not code changes. Tracked as `DEPLOY-TARGET-1`.

---

### Stage 2 — Multi-Tenant SaaS

**Target:** Multiple paying operators each get an isolated runtime environment.
Think "one deployment, many customers."

This is when `LOCAL_AND_CLOUD_AUDIT.md` findings TENANT-1 through TENANT-4
become load-bearing:

| Finding | What it blocks |
|---|---|
| **TENANT-1** — `tenant_id == str(user_id)` by convention | Billing identity must be a durable, control-plane-issued ID decoupled from `user_id` |
| **TENANT-2** — `quota_group` has no enforcement path | Per-tenant concurrency limits, storage caps, and feature gates require this to be built |
| **TENANT-3** — event bus is a single shared channel | WAIT/RESUME events for tenant A must not broadcast to tenant B's processes |
| **TENANT-4** — sandbox resource limits are global | OCI container resource limits must be per-tenant to prevent noisy-neighbor problems |

These are not blocked by architectural debt — the hooks are seeded. They are
deliberate work that should begin only when the first multi-tenant customer
is ready to onboard. Tracked as `DEPLOY-TARGET-2`.

The commercial layer (billing identity, plan tiers, Stripe integration, usage
reporting) is a separate concern documented in `MONETIZATION_AUDIT.md`.

---

## Open Tech Debt

| Entry | Summary | Trigger |
|---|---|---|
| `DEPLOY-TARGET-1` | Cloud deployment manifests not authored (Railway/Render/Fly.io) | When first cloud deployment is planned |
| `DEPLOY-TARGET-2` | Multi-tenant SaaS readiness: TENANT-1/2/3/4 become load-bearing | When first multi-tenant customer onboards |
