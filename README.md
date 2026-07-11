# aindy-runtime

`aindy-runtime` is a self-hostable AI agent execution runtime. It provides the
infrastructure layer for building and operating AI-powered systems: a syscall-based
execution contract, a DAG flow engine, persistent vector memory, structured agent runs
with approval gates, and an extensible plugin architecture for mounting domain-specific
app layers.

Deployable in minutes via Docker Compose. Extensible via a Python plugin registry.
Operable via a built-in platform UI and a REST API backed by the `aindy-sdk`.

**What it gives you:**
- **Flow engine** — DAG-based execution with WAIT/RESUME semantics, priority scheduling, and dead-letter recovery
- **Agent runtime** — structured goal → plan → approval → execute loop with capability tokens and trigger evaluation
- **Memory system** — persistent `MemoryNode` storage with pgvector embeddings, hybrid retrieval, and memory traces
- **Syscall contract** — single `SyscallDispatcher` entry point with schema validation, idempotency gates, and tenant isolation
- **Platform UI** — operator dashboard for flows, agents, scheduler, and observability (served at `/platform`)
- **Plugin registry** — mount routers, flows, jobs, syscalls, and event handlers from external Python packages at boot time
- **Nodus script execution** — embedded execution service for the Nodus DSL (`.nodus` / `.nd`), with memory builtins and WAIT/RESUME propagation back into the flow engine
- **Distributed operation** — Redis-backed distributed job queue, lease-based leadership election for background schedulers, and orphan-run recovery watchdogs
- **Effect compensation** — append-only effect-reversal ledger with `sys.v1.agent.undo` to walk back a run's recorded side effects, plus `sys.v1.agent.simulate` for zero-side-effect rehearsal against virtual tools
- **Sandbox certification** — Docker-backed extension sandbox with an escape-test suite, posture reporting (`aindy-runtime sandbox`), and an append-only audit log
- **Webhooks** — subscription CRUD on the platform API for pushing runtime events to external endpoints
- **Federated memory recall** — cross-agent recall via `POST /memory/federated/recall`, dispatched through the syscall contract

**Stability:** public surfaces declared under `docs/runtime/` are stable. Extension and
orchestration surfaces marked experimental may change between minor versions. In-process
extensions require trusted code — this is not a sandboxed third-party plugin host.

## Quickstart

**Prerequisites:** Docker Desktop (or Docker Engine + Compose plugin v2.20+).

```bash
# 1. Clone
git clone https://github.com/Masterplanner25/aindy-runtime.git
cd aindy-runtime

# 2. Configure
cp AINDY/.env.example AINDY/.env
#    Open AINDY/.env and set at minimum:
#      SECRET_KEY  — generate: python3 -c "import secrets; print(secrets.token_hex(32))"
#      OPENAI_API_KEY

# 3. Start
docker compose up -d

# 4. Run migrations + wait for ready
#    (alembic upgrade head runs automatically inside the api container on boot)
#    Watch progress:
docker compose logs -f api

# 5. Verify
curl http://localhost:8000/ready    # → {"status": "ok", ...}

# 6. Visit the platform UI
#    http://localhost:8000/platform
```

**Production-shaped deployment** (Redis + distributed worker):
```bash
docker compose --profile full up -d
```

**With metrics** (Prometheus on port 9090):
```bash
docker compose --profile full --profile monitoring up -d
```

**Cloud / remote VM with nginx + TLS** (ports locked down, HTTPS):
```bash
NGINX_CONF=nginx.tls.conf \
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --profile full --profile proxy up -d
```

> See [Remote deployment](#remote-deployment) below for the full TLS checklist.

### After the server starts

Once `curl http://localhost:8000/ready` returns `{"status": "ok"}`, create your first account and API key:

```bash
# Register
curl -s -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "yourpassword", "display_name": "You"}' \
  | python -m json.tool

# Log in — copy access_token from the response
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "yourpassword"}' \
  | python -m json.tool

# Promote yourself to admin (needed to create Platform API keys)
aindy-runtime auth promote-admin you@example.com

# Create a Platform API key (save the 'key' field — shown only once)
curl -s -X POST http://localhost:8000/platform/keys \
  -H "Authorization: Bearer <your-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-app", "scopes": ["memory.read", "memory.write", "flow.run", "event.emit"]}' \
  | python -m json.tool
```

Then install the SDK and make your first call:

```bash
pip install aindy-sdk
```

```python
from aindy_sdk import AINDYClient

client = AINDYClient("http://localhost:8000", api_key="aindy_your_key")
registry = client.syscalls.list()
print(registry["total_count"], "syscalls available")
```

Full SDK documentation and examples: [aindy-sdk](https://github.com/Masterplanner25/aindy-sdk)

### Building apps on aindy-runtime

Three integration patterns, listed by increasing coupling:

**1. SDK (external HTTP)** — Any service that can make HTTP requests can integrate
via Platform API keys. Install `aindy-sdk`, create a key with the scopes you need,
and use `AINDYClient`. See [After the server starts](#after-the-server-starts) above.

**2. Agent runs** — Submit agent objectives via `POST /apps/agent/run`. The runtime
executes the objective through its flow engine. No in-process code needed — just an
authenticated HTTP call with a capability token. Note: the `/apps/agent/run` route
itself is registered by the app plugin layer (pattern 3), not the bare runtime — a
plugin bootstrap such as `aindy-apps-monolith` must be mounted for this endpoint to
exist.

**3. Trusted Python extensions (in-process)** — Set
`AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS=true` to load a Python package into the
runtime process at startup. Extensions register routers, flows, jobs, syscalls, and
event handlers through the plugin registry. This is the pattern used by
`aindy-apps-monolith`.

**Reference implementation:** [`aindy-apps-monolith`](https://github.com/Masterplanner25/aindy-apps-monolith)
contains 16 working domain apps built on this pattern. The canonical how-to doc is
[`docs/architecture/PLUGIN_REGISTRY_PATTERN.md`](https://github.com/Masterplanner25/aindy-apps-monolith/blob/main/docs/architecture/PLUGIN_REGISTRY_PATTERN.md)
— it covers all 18 registration categories, boot-order dependency declarations, and a
step-by-step guide for adding a new domain app.

> **Trust posture note:** option 3 is a trusted-internal mechanism. It does not sandbox
> extension code. Do not use it to load untrusted third-party packages.

> **Note — database host inside compose:** The `DATABASE_URL` in `AINDY/.env`
> must use the compose service name as the host, not `localhost`:
> ```
> DATABASE_URL=postgresql://aindy:aindy@postgres:5432/aindy
> ```
> `postgres` resolves on the compose network; `localhost` does not.

> **Note — pgvector required:** The compose file uses `pgvector/pgvector:pg16`
> instead of the stock `postgres:16-alpine`. The runtime stores memory embeddings
> as `VECTOR(1536)` columns, which requires the PostgreSQL `pgvector` extension.
> `docker/init-pgvector.sql` runs `CREATE EXTENSION IF NOT EXISTS vector` on
> first initialization. If you bring your own PostgreSQL instance, run that
> statement once before first boot.

> **Note — published database ports:** `postgres` (5432), `redis` (6379), and
> `mongo` (27017) publish to the host for local development convenience. For
> production deployments on a cloud VM, remove the `ports:` blocks from those
> services or use a compose override file. See `TECH_DEBT: COMPOSE-PROD-PORTS-1`.

## Install

```bash
pip install aindy-runtime
```

**Import name:** The distribution name is `aindy-runtime` but the importable module is `AINDY`
(uppercase — it is an acronym). `import aindy_runtime` will not work.

```python
from AINDY._version import __version__  # correct
from AINDY.platform_layer.deployment_contract import deployment_contract_summary
# import aindy_runtime  ← ImportError
```

For local development (editable install from source):

```bash
python -m pip install -e .
```

For staged release builds:

```bash
python -m pip install -e .[release]
```

## CLI

```
aindy-runtime init       Scaffold AINDY/.env (with generated SECRET_KEY), Dockerfile,
                         docker-compose.yml, and docker/init-pgvector.sql for a new install
aindy-runtime serve      Start the HTTP API server (requires DATABASE_URL)
aindy-runtime sandbox    Report sandbox capabilities and exit
aindy-runtime auth promote-admin <email>   Grant admin to a registered user (grant-only)
aindy-runtime --help     Show help and exit
aindy-runtime --version  Show version and exit
```

## Run

Runtime-only API boot:

```bash
aindy-runtime serve
```

Minimum runtime environment:

```bash
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME
SECRET_KEY=...
OPENAI_API_KEY=sk-...
```

For local smoke tests only, SQLite remains opt-in and must be declared
explicitly:

```bash
DATABASE_URL=sqlite://
AINDY_ALLOW_SQLITE=1
SECRET_KEY=runtime-local-secret-key
OPENAI_API_KEY=sk-test-placeholder
```

Equivalent module and ASGI forms:

```bash
python -m AINDY.runtime_only serve
uvicorn AINDY.runtime_only:app
```

## Upgrading

### pip (local install)

```bash
pip install --upgrade aindy-runtime
```

Verify the new version:

```bash
aindy-runtime --version
# or, while the server is running:
curl http://localhost:8000/api/version
```

If this release includes a schema change, set `AINDY_SCHEMA_RECONCILE=true`
before restarting. The startup log will tell you whether reconciliation is
needed; if `AINDY_ENFORCE_SCHEMA=true` is set, the server will refuse to start
rather than silently run against a mismatched schema.

```bash
AINDY_SCHEMA_RECONCILE=true aindy-runtime serve
```

Once the server confirms a clean startup you can unset the flag.

### Docker Compose

```bash
docker compose pull          # fetch the new image
docker compose up -d         # recreate containers with zero-downtime rolling update
```

If the release bumps the schema, set the reconcile flag in `AINDY/.env` before
restarting, then remove it after the first clean boot.

### Rollback

```bash
pip install "aindy-runtime==<previous-version>"
# or, for Docker:
docker compose down && docker compose up -d   # after reverting the image tag in docker-compose.yml
```

Rolling back across a schema change requires a database restore — schema
migrations are not automatically reversed on downgrade.

## Remote deployment

For cloud VM or any deployment where a domain name and HTTPS are required.
The `proxy` profile brings up an nginx container on ports 80 and 443 that
forwards all traffic to the api container. The `docker-compose.prod.yml`
overlay closes all internal port bindings so nothing is reachable from
outside the host except nginx.

### Plain HTTP (behind a TLS-terminating load balancer)

Suitable for AWS ALB, GCP Load Balancer, Cloudflare Proxy, etc. that
terminate TLS and forward plain HTTP to the backend.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --profile full --profile proxy up -d
```

Set `ALLOWED_ORIGINS=https://yourdomain.com` in `AINDY/.env`.

### HTTPS with Let's Encrypt (direct VM, no load balancer)

```bash
# 1. On the host — obtain a certificate (certbot must be installed)
certbot certonly --standalone -d yourdomain.com

# 2. Edit nginx/nginx.tls.conf — replace `server_name _;` with your domain:
#      server_name yourdomain.com;

# 3. Mount your certs — create docker-compose.override.yml:
cat > docker-compose.override.yml << 'EOF'
services:
  nginx:
    volumes:
      - /etc/letsencrypt/live/yourdomain.com/fullchain.pem:/etc/nginx/certs/fullchain.pem:ro
      - /etc/letsencrypt/live/yourdomain.com/privkey.pem:/etc/nginx/certs/privkey.pem:ro
EOF

# 4. In AINDY/.env set:
#      ALLOWED_ORIGINS=https://yourdomain.com

# 5. Start
NGINX_CONF=nginx.tls.conf \
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --profile full --profile proxy up -d
```

**Certificate renewal** — add to host cron (`crontab -e`):
```
0 3 * * * certbot renew --quiet && docker compose exec nginx nginx -s reload
```

### Port exposure summary

| Profile combination | Ports exposed to host |
|---|---|
| Default (no overlay) | 8000 (api), 5432, 6379, 27017 |
| `+ docker-compose.prod.yml` | 8000 (api) only |
| `+ proxy profile` | 8000 (api), 80, 443 |
| `+ proxy + docker-compose.prod.yml` | 80, 443 only |

## What lives here

`aindy-runtime` owns the execution substrate: the runtime kernel, flow engine, memory
system, agent runtime, syscall registry, platform UI, and all stable operator surfaces
declared under `docs/runtime/`.

App-layer code (`apps/`, `aindy_plugins.json`, app-profile Alembic migrations) belongs
in [`aindy-apps-monolith`](https://github.com/Masterplanner25/aindy-apps-monolith), which
demonstrates the plugin pattern at scale across 16 domain apps.

Full boundary definition: [`docs/runtime/RUNTIME_BOUNDARY.md`](docs/runtime/RUNTIME_BOUNDARY.md)

## Branch And PR Model

Active contribution model for this repo:

- protected branch: `main`
- pull requests should target: `main`
- feature work should branch from the current `main`

This repo does not use the archived monolith `develop`-targeting flow.

## Verify

```bash
python -m pytest \
  tests/unit/test_runtime_only_test_fixtures.py \
  tests/unit/test_platform_only_startup.py \
  tests/unit/test_runtime_packaging.py \
  tests/unit/test_runtime_boundary.py \
  tests/unit/test_runtime_compatibility_metadata.py \
  tests/api/test_version_api.py \
  -m runtime_only -q
```

Runtime CI scope in `.github/workflows/runtime-ci.yml` now covers the
runtime-owned push/PR baseline:

- lint runtime-owned Python code with Ruff
- validate runtime-doc frontmatter under `docs/runtime/`
- install the runtime package and test extras in editable mode
- assert runtime code does not import `apps.*`
- verify the `aindy-runtime` console script
- smoke `GET /health` and `GET /api/version` in runtime-only mode
- run the full extracted runtime-owned pytest suite (`tests -m runtime_only`)
- build wheel and sdist artifacts and run `twine check`

GitHub Actions note:

- `runtime-ci.yml` is the automatic push/PR check for `main`
- `release-staging.yml` is intentionally manual-only (`workflow_dispatch`) and
  will not appear as a normal push/PR status check until it is dispatched

Staged release flow in `.github/workflows/release-staging.yml` is intentionally
non-publishing:

- verify runtime version and compatibility metadata
- build wheel and sdist artifacts
- run `twine check`
- upload artifacts for inspection

Checks intentionally left out of the runtime repo because they remain
app- or monolith-owned:

- app bootstrap and app-profile tests
- cross-app import boundary checks
- app-database Alembic migration execution for app-owned tables
- frontend, Playwright, and client build checks
- Docker image and full monolith service-matrix validation

## Runtime Schema Bootstrap

The extracted runtime is self-hostable for its own database surface.

- startup, worker boot, and readiness checks use packaged runtime ORM metadata
  as the schema contract
- on a blank database, the runtime bootstraps runtime-owned tables directly from
  that packaged metadata
- on an additive-safe but out-of-date schema, startup requires explicit
  `AINDY_SCHEMA_RECONCILE=true` before mutating an initialized database
- on incompatible drift, startup fails closed when `AINDY_ENFORCE_SCHEMA=true`
- app-owned tables and the monolith Alembic history remain app-repo concerns

## Docs

Runtime-owned documentation lives under `docs/runtime/`.

Release staging guidance lives in `docs/runtime/RELEASE_STAGING.md`.

CI ownership guidance lives in `docs/runtime/CI_OWNERSHIP.md`.

Deployment topology guidance lives in `docs/runtime/DEPLOYMENT_PROFILES.md`.

Manual GitHub branch-protection and review settings guidance lives in
`docs/runtime/GITHUB_SETTINGS_CHECKLIST.md`.

