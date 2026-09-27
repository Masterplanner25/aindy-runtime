---
title: "aindy-runtime Quickstart"
api_version: "1.0"
last_verified: "2026-09-27"
status: current
owner: "platform-team"
---

# aindy-runtime Quickstart

Get a local aindy-runtime server running against a real PostgreSQL database.

> **Option A is the same path as the [README's Quickstart](../../README.md#quickstart)**, which
> also covers API keys, scopes and the first SDK call. This page adds **Option B**, a local
> editable install against a database you run yourself, for working on the runtime itself.

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.11+ (Option B) |
| PostgreSQL | 15 or 16 with the **pgvector** extension |
| Redis | 7+ (optional for a single-instance server; required for distributed mode) |
| Docker | Compose v2.20+ for Option A; also the easiest way to run Postgres for Option B |

**pgvector** is required for the `VECTOR(1536)` embedding column in `memory_nodes`. The easiest
way to get it is the `pgvector/pgvector:pg16` Docker image.

---

## Option A — Docker Compose (recommended)

Starts Postgres and the API server with one command.

```bash
git clone https://github.com/Masterplanner25/aindy-runtime.git
cd aindy-runtime

cp AINDY/.env.example AINDY/.env
# Edit AINDY/.env and set at minimum:
#   SECRET_KEY      — python3 -c "import secrets; print(secrets.token_hex(32))"
#   OPENAI_API_KEY

docker compose up -d
docker compose logs -f api          # wait for startup to finish

curl http://localhost:8000/ready    # → {"status": "ok", ...}
```

The api container runs `alembic upgrade head` before it starts serving. Add Redis and the
distributed worker with `docker compose --profile full up -d`.

---

## Option B — Editable install (local development)

### 1. Install

```bash
git clone https://github.com/Masterplanner25/aindy-runtime.git
cd aindy-runtime

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e ".[test]"
```

Run every `aindy-runtime` command below **from this venv**. A global `aindy-runtime` elsewhere
on your PATH may be an older release. `python -m AINDY.runtime_only <command>` always runs the
checkout you are in.

### 2. Configure

```bash
cp AINDY/.env.example AINDY/.env
```

Edit `AINDY/.env` and set at minimum:

```dotenv
SECRET_KEY=<64 hex chars: python -c "import secrets; print(secrets.token_hex(32))">
OPENAI_API_KEY=<your key>
DATABASE_URL=postgresql://aindy:aindy@localhost:5432/aindy
```

`REDIS_URL` is needed only for distributed mode. `AINDY_API_KEY` is optional: it enables a
bearer token for machine-to-machine calls, and leaving it empty disables that path. See
`AINDY/.env.example` for every variable, with descriptions and defaults.

### 3. Start PostgreSQL with pgvector

```bash
docker run -d \
  --name aindy-postgres \
  -e POSTGRES_USER=aindy \
  -e POSTGRES_PASSWORD=aindy \
  -e POSTGRES_DB=aindy \
  -p 5432:5432 \
  pgvector/pgvector:pg16

# Enable the extension (first run only):
docker exec aindy-postgres psql -U aindy -d aindy \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### 4. Build the schema

```bash
aindy-runtime bootstrap-schema
```

This builds the runtime-owned tables from the packaged models and stamps the runtime's Alembic
baseline (`alembic_version_runtime`). It is idempotent. The server also builds missing runtime
tables on a blank database at first boot, so this step is optional locally, but it is the
explicit form a deploy should use. See the README's
[Runtime Schema Bootstrap](../../README.md#runtime-schema-bootstrap) section.

### 5. Run the server

```bash
aindy-runtime serve
# or equivalently:
uvicorn AINDY.runtime_only:app --host 0.0.0.0 --port 8000
```

### 6. Verify

```bash
curl http://localhost:8000/ready                              # readiness → {"status": "ok", ...}
curl -s http://localhost:8000/health/deep | python -m json.tool   # per-dependency checks
curl -s http://localhost:8000/api/version | python -m json.tool   # versions + compatibility
```

`/health/deep` reports each dependency (database, Redis, the syscall registry and others) with
its own status. On a single-instance server with no Redis, Redis reads as not configured or
unavailable, which is expected.

---

## Create the first admin user

```bash
# Register. Returns 202 with NO token: registration does not log you in.
# Passwords must be at least 8 characters.
curl -s -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "changeme1"}'

# Log in for a token (access_token in the response).
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "changeme1"}'

# Promote to admin (no restart needed).
aindy-runtime auth promote-admin admin@example.com                     # Option B, from the venv
docker compose exec api aindy-runtime auth promote-admin admin@example.com   # Option A
```

The register response is the same whether the account was created or already existed, so the
endpoint cannot be used to find out which emails have accounts. If an email channel is configured,
it also sends a verification link. Verification is not required to log in unless you set
`AINDY_REQUIRE_VERIFIED_LOGIN=true`.

Alternatively, set `AINDY_BOOTSTRAP_ADMIN_EMAIL=admin@example.com` in `AINDY/.env` and restart.
That only grants admin to an account that already exists. The runtime never makes the first
registered user an admin.

Next, create a Platform API key and make your first SDK call: see
[After the server starts](../../README.md#after-the-server-starts) in the README.

---

## Run the test suite

```bash
pytest tests/unit/ -v                  # unit: SQLite in-memory, no services needed
pytest -m runtime_only -q              # exactly what CI's Runtime Contracts job runs
pytest -c pytest.integration.ini -v    # integration: needs live Postgres + Redis
docker compose -f docker-compose.test.yml up -d   # test Postgres + Redis for the line above
```

---

## Next steps

| What | Where |
|---|---|
| Runnable tutorials | `docs/tutorials/index.md` |
| All registered syscalls | `docs/runtime/SYSCALL_REFERENCE.md` |
| Writing Nodus scripts | `docs/runtime/NODUS_DEVELOPER_GUIDE.md` |
| Execution invariants | `docs/runtime/EXECUTION_INVARIANTS.md` |
| Production deployment | `docs/operations/DEPLOYMENT_TARGETS.md` |
| Security model | `docs/runtime/SECURITY_MATRIX.md` |
| Release checklist | `docs/governance/RELEASE_CHECKLIST.md` |
