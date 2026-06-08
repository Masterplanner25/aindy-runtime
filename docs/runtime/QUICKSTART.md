---
title: "aindy-runtime Quickstart"
api_version: "1.0"
last_verified: "2026-06-08"
status: current
owner: "platform-team"
---

# aindy-runtime Quickstart

Get a local aindy-runtime server running against a real PostgreSQL database in under five minutes.

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.11+ |
| PostgreSQL | 15 or 16 with the **pgvector** extension |
| Redis | 7+ |
| Docker (optional) | for compose-based setup |

**pgvector** is required for the `VECTOR(1536)` embedding column in `memory_nodes`. The easiest way to get it is via the `pgvector/pgvector:pg16` Docker image.

---

## Option A — Docker Compose (recommended)

Spins up Postgres + Redis + the API server with one command.

```bash
# Clone and enter the repo
git clone https://github.com/Masterplanner25/aindy-runtime.git
cd aindy-runtime

# Copy the example env file
cp AINDY/.env.example AINDY/.env
# Edit AINDY/.env — set at minimum: SECRET_KEY, AINDY_API_KEY

# Start the full stack
docker compose up -d

# Verify the server is healthy
curl -s http://localhost:8000/health/deep | python -m json.tool
```

The compose stack runs `alembic upgrade head` automatically before starting the API service.

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

### 2. Configure

```bash
cp AINDY/.env.example AINDY/.env
```

Edit `AINDY/.env` and set the required variables at minimum:

```dotenv
SECRET_KEY=<random-32-char-string>
AINDY_API_KEY=<your-api-key>
DATABASE_URL=postgresql://aindy:aindy@localhost:5432/aindy
REDIS_URL=redis://localhost:6379
```

See `AINDY/.env.example` for the full reference with descriptions and defaults.

### 3. Start PostgreSQL with pgvector

```bash
# Quickest path — Docker:
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

### 4. Apply migrations

```bash
python -m alembic upgrade head
```

This stamps the alembic version table. Domain tables are bootstrapped by the server on first boot.

### 5. Run the server

```bash
aindy-runtime serve
# or equivalently:
uvicorn AINDY.runtime_only:app --host 0.0.0.0 --port 8000
```

### 6. Verify

```bash
# Basic health — should return HTTP 200
curl http://localhost:8000/health

# Deep health — should return {"status": "healthy", ...}
curl -s http://localhost:8000/health/deep | python -m json.tool

# Version surface
curl -s http://localhost:8000/api/version | python -m json.tool
```

A healthy `/health/deep` response includes:

```json
{
  "status": "healthy",
  "syscall_registry": { "status": "ok", "count": 17 },
  "platform": { "database": "ok", "execution_engine": "ok" }
}
```

---

## Create the first admin user

```bash
# Register a user via the API
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "changeme"}'

# Promote to admin (no server restart needed)
aindy-runtime auth promote-admin admin@example.com
```

Alternatively, set `AINDY_BOOTSTRAP_ADMIN_EMAIL=admin@example.com` in `AINDY/.env` and restart.

---

## Run the test suite

```bash
# Fast unit tests — no database required
pytest tests/unit/ -m runtime_only -q

# Full unit suite (9 pre-existing CLI binary failures are expected without install)
pytest tests/unit/ -q

# Integration tests — require live Postgres + Redis
pytest -c pytest.integration.ini -v
```

---

## Next steps

| What | Where |
|---|---|
| All registered syscalls | `docs/runtime/SYSCALL_REFERENCE.md` |
| Writing Nodus scripts | `docs/runtime/NODUS_DEVELOPER_GUIDE.md` |
| Execution invariants | `docs/runtime/EXECUTION_INVARIANTS.md` |
| Production deployment | `docs/runtime/DEPLOYMENT_TARGETS.md` |
| Security model | `docs/runtime/SECURITY_MATRIX.md` |
| Release checklist | `docs/runtime/RELEASE_CHECKLIST.md` |
