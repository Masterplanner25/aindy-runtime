---
title: "Real-User Walkthrough Log"
api_version: "1.0"
last_verified: "2026-06-12"
status: current
owner: "platform-team"
---

# Real-User Walkthrough Log

Fresh install path: `pip install aindy-runtime==1.2.0` into a clean venv at `C:\dev\artesting`.
Goal: walk the full onboard flow as a net-new operator, no repo clone.

---

## Issue 1 — `.env` file warning on first boot

**What happened:** Server printed:
```
env file C:\dev\artesting\AINDY\.env not found
```

**Root cause:** `config.py` uses `Path(__file__).parent / ".env"` as the default env file
location. pydantic-settings warns (non-fatally) when the file doesn't exist. Server continues
fine using environment variables set directly in the shell.

**Workaround:**
```powershell
New-Item -ItemType Directory -Force "C:\dev\artesting\AINDY"
@"
DATABASE_URL=postgresql://aindy:aindy@localhost:5432/aindy
SECRET_KEY=dev-secret-change-in-production
"@ | Out-File -Encoding utf8 "C:\dev\artesting\AINDY\.env"
```
Or set `$env:DATABASE_URL` and `$env:SECRET_KEY` directly before `aindy-runtime serve`.

**Gap / follow-up:** A `pip install aindy-runtime` with no repo clone has no `.env` scaffold.
`aindy-runtime init` command (or QUICKSTART pointer in the pip install output) would close this.
Candidate: new CLI subcommand `aindy-runtime init` that writes a `.env.example` to CWD.

---

## Issue 2 — Docker Desktop not running / `docker-compose.yml` not on PATH

**What happened:**
```
unable to get image 'pgvector/pgvector:pg16': failed to connect to the docker API at
npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct and if the daemon
is running
```

**Two problems:**
1. Docker Desktop was not running — must be started before `docker compose up -d`.
2. `docker-compose.yml` is in the repo (`C:\dev\aindy-runtime\`) but NOT in the PyPI
   package. A real user who only does `pip install aindy-runtime` has no compose file.

**Workaround (Docker not running):** Start Docker Desktop, wait for the whale icon to stop
animating, then re-run `docker compose up -d`.

**Workaround (no compose file):** Copy `docker-compose.yml` + `docker/init-pgvector.sql`
from the repo, or clone the repo for the compose config only:
```powershell
# Option A — copy from local repo
Copy-Item C:\dev\aindy-runtime\docker-compose.yml C:\dev\artesting\
Copy-Item -Recurse C:\dev\aindy-runtime\docker C:\dev\artesting\docker

# Option B — shallow clone just for compose config (future)
# git clone --depth 1 https://github.com/Masterplanner25/aindy-runtime compose-config
```

**Gap / follow-up:** `docker-compose.yml` should ship as a published artifact or be
fetchable without a full repo clone. Options:
- Publish a `docker-compose.yml` as a GitHub release asset alongside the wheel.
- Add a `aindy-runtime init` command that writes a minimal compose file to CWD.
- Include a `compose/` directory in the PyPI sdist (currently excluded by `include = ["AINDY*"]`).

---

## Issue 3 — `SECRET_KEY` placeholder rejected at startup

**What happened:** `docker compose up -d` showed api as "Started" but it immediately exited.
`docker compose logs api` showed:

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
SECRET_KEY
  Value error, SECRET_KEY is set to an insecure placeholder. Generate a real key with:
  python3 -c "import secrets; print(secrets.token_hex(32))"
  [input_value='REPLACE_THIS_with_a_32_char_hex_string']
```

**Root cause:** `docker-compose.yml` ships with `SECRET_KEY=REPLACE_THIS_with_a_32_char_hex_string`
as a safety sentinel. pydantic hard-rejects it at startup — intentional security behaviour.
But there is no prompt telling the operator to replace it before running `docker compose up`.

**Workaround:** Generate a real key and write it to `.env` next to `docker-compose.yml`
(compose automatically reads a sibling `.env`):

```powershell
$key = python -c "import secrets; print(secrets.token_hex(32))"
"SECRET_KEY=$key" | Out-File -Encoding utf8 "C:\dev\artesting\.env"
docker compose down && docker compose up -d
```

**Gap / follow-up:** QUICKSTART and `aindy-runtime init` must include a mandatory
"generate SECRET_KEY first" step before `docker compose up`. `aindy-runtime init` should
generate and write a real key automatically so the operator never hits this on first boot.

---

## Issue 4 — `env_file` path: compose reads `AINDY/.env`, not root `.env`

**What happened:** After writing the SECRET_KEY to `C:\dev\artesting\.env` (root), the api
container still crashed — this time with `DATABASE_URL` parse failure. The root `.env` was
never read by compose.

**Root cause:** `docker-compose.yml` has:
```yaml
env_file:
  - AINDY/.env
volumes:
  - ./AINDY/.env:/etc/aindy/.env:ro
```
Both the `env_file` directive and the volume mount point to `AINDY/.env` relative to the
compose file. A root-level `.env` is only picked up for variable substitution inside
`docker-compose.yml` itself (e.g. `${POSTGRES_DB}`), not as the application env file.

**Workaround:**
```powershell
New-Item -ItemType Directory -Force "C:\dev\artesting\AINDY"
@"
DATABASE_URL=postgresql://aindy:aindy@postgres:5432/aindy
SECRET_KEY=<generated-hex>
"@ | Out-File -Encoding utf8 "C:\dev\artesting\AINDY\.env"
```

**Gap / follow-up:** The operator needs to know to create `AINDY/.env` (not root `.env`)
and that `DATABASE_URL` must use the service name `postgres` (not `localhost`) for
container-to-container networking. `aindy-runtime init` should write this file to the
correct location with the right hostname automatically.

---

## Issue 5 — Docker image version mismatch (`aindy-runtime:1.0.0` vs PyPI `1.2.0`)

**What happened:** `/health/deep` reports `"nodus": {"version": "3.0.2"}` but PyPI 1.2.0
ships nodus-lang 4.0.3. The running image is `aindy-runtime:1.0.0` (pre-built, cached).

**Root cause:** `docker-compose.yml` pins `image: aindy-runtime:1.0.0`. After copying the
compose file to `C:\dev\artesting\` (with no Dockerfile), Docker reused the locally cached
`aindy-runtime:1.0.0` image from prior builds rather than building a fresh one. The cached
image predates the nodus upgrade.

**Real user path (correct):**
```powershell
git clone <repo>
cd aindy-runtime
docker compose build --no-cache
docker compose up -d
```
Without `--no-cache`, a cached image at the same tag silently runs stale code.

**Gap / follow-up:** The compose `image:` tag should track the runtime version (e.g.
`aindy-runtime:1.2.0`). That forces a rebuild when the version bumps and makes it obvious
when the running image is stale. Opening as a follow-up for the next maintenance cycle.

---

## Stack healthy — proceeding with registration flow

Both containers `Up (healthy)` after the AINDY/.env fix. Deep health endpoint confirms:
- database: ok
- scheduler: ok
- flow_registry: ok (29 nodes, 31 flows)
- nodus: ok (3.0.2 in cached image — see Issue 5)
- ai_providers: ok (circuits closed)

---

## Issue 6 — `system_event.execution.started` degraded side-effect on `/auth/register`

**What happened:** `POST /auth/register` returned HTTP 200 with a successful JWT, but the
response envelope included:
```json
"degraded_side_effects": ["system_event.execution.started"]
"error": "ExecutionContract violation: execution event 'execution.started' emitted outside pipeline"
```

**Root cause (known):** The auth register route handler emits `execution.started` as a side
effect but the call happens outside the `ExecutionPipeline` wrapper. This is a non-fatal
cosmetic issue — the user receives a valid token and the registration succeeds. The event
just isn't captured in the system event log.

**Impact:** Registration works; login works; JWT is valid and carries `is_admin` claims.
No user-visible failure.

**Gap / follow-up:** Auth routes should either be wrapped in `ExecutionPipeline` or suppress
the side-effect emission — the degraded warning is noise for operators monitoring logs.

---

## Walkthrough checkpoint — full auth flow verified

Actions completed and results:

| Step | Command | Result |
|---|---|---|
| Register | `POST /auth/register` | 200 — JWT issued |
| Promote admin | `docker exec aindy-runtime auth promote-admin <email>` | `ok: granted is_admin=True` |
| Login | `POST /auth/login` | JWT with `"is_admin": true` |
| Platform UI | `GET /platform/` | 200 — `<title>A.I.N.D.Y. Platform</title>` + Vite assets |

Note: Promote admin requires `docker exec` into the api container or access to the host
with `DATABASE_URL` set. A real operator without a shell into the container must use
`AINDY_BOOTSTRAP_ADMIN_EMAIL` env var + restart. That flow was not tested in this
walkthrough — log separately.

---

## Issue 9 — System health screen reports build `1.0.0` instead of `1.2.0`

**What happened:** The Platform UI system health screen displays the running image version
as `1.0.0`.

**Root cause:** This is a direct consequence of Issue 5 — the Docker image cached as
`aindy-runtime:1.0.0` is what's actually running. The version reported by the health
endpoint comes from the image, not the PyPI package version. Since we never rebuilt the
image after the 1.2.0 bump, the running container is genuinely 1.0.0 code.

**Gap / follow-up:** Image tag should track the runtime semver. On a clean operator
install from a 1.2.0 release, `docker compose build` would produce `aindy-runtime:1.2.0`
and the health screen would show the correct version. See Issue 5.

---

## Design note — Runtime capabilities should be surfaceable from the Platform UI

**Observation:** Features like MongoDB and Redis are currently on/off based purely on
backend wiring (env vars, Docker profiles). The Platform UI has no way to show or toggle
these. For a runtime product, an operator expects to see "MongoDB: not configured —
connect one" rather than discovering it only through degraded health JSON.

**This is not a required fix** — it's a design direction. The runtime UI should eventually
expose capability surfaces: what's connected, what's degraded, what can be enabled. Not
application features — runtime toggles and service connections. Think of it as an operator
panel rather than an app dashboard.

**Scope boundary:** The app layer (flows, agents, memory features) stays in the SPA as-is.
What belongs here is the infrastructure layer: storage backends, queue mode (in-memory vs
distributed), LLM provider status, extension trust settings.

---

## Issue 7 — Agent Registry screen crashes with no agents registered

**What happened:** Navigating to the Agent Registry screen in the Platform UI produced an
error rather than an empty state.

**Root cause (suspected):** The screen likely makes an API call that returns an empty list
or a 404, and the component throws an unhandled error instead of rendering an empty-state
message ("No agents registered yet").

**Impact:** First-time operators see a crash screen where they should see a helpful empty
state with next-step guidance (e.g. "Register your first agent via the SDK").

**Gap / follow-up:** The Agent Registry component needs an empty-state guard: if the agents
list is empty (or the fetch returns 0 results), render a "No agents registered" placeholder
instead of throwing. This is a UI polish item — the underlying API is fine.

---

## OpenClaw runner results against live stack

All three nodus branches executed. Two fixes were needed during this run:

**Fix A — `or` fallback pattern in nodus script (lines 35, 79)**
`x or "fallback"` is not valid Nodus 4.0.3 syntax — `or` is treated as a variable name
at runtime. Both occurrences replaced with explicit nil-check pattern:
```
let val = expr
if (val == nil) { val = default_value }
```

**Fix B — `result.get("extras").get("globals")` → `result.get("agent_state")`**
The runner was reading state from the wrong key. `set_state` writes to `agent_state` dict
in the runner, not to the nodus runtime globals. Fixed the CLI printout accordingly.

**Fix C — `task_name` field missing from `sys.v1.job.submit` payload**
The syscall validator requires `task_name`. Added `"task_name": "openclaw.reminder"` to the
`tool_schedule_reminder` dispatch payload.

**Branch results:**

| Message | Branch | Output | Status |
|---|---|---|---|
| `"Hello! Who are you?"` | default | `I received your message: Hello! Who are you?` | ✅ clean |
| `"search for the latest Python news"` | search | `Here is what I found: [stub] Web result for: ...` | ✅ clean |
| `"remind me to check my email tomorrow"` | remind | `Could not schedule: submit_async_job() got an unexpected keyword argument 'db'` | ⚠️ graceful error |

The remind branch error (`submit_async_job() got unexpected keyword argument 'db'`) is a
handler signature mismatch in `sys.v1.job.submit` — the runner passes `db=` to
`dispatch_syscall` but the internal handler doesn't accept it. Non-blocking for the demo;
the error path is handled gracefully. Fix is a runner-level concern for the production path.

`persona_loaded=False, history_turns=0` on all runs is expected — no workspace files
(SOUL.md / IDENTITY.md) exist in the current working directory and the DB auth from
host→container uses `127.0.0.1:5432` but the `aindy` user password auth via psycopg2
failed (Postgres pg_hba.conf or auth method mismatch). Memory stubs handled gracefully.

---

## Issue 8 — Single crashed screen poisons subsequent navigation until refresh

**What happened:** After the Agent Registry error (Issue 7), clicking through to other
screens also failed to render. A full page refresh restored normal navigation.

**Root cause (suspected):** An unhandled promise rejection or React error boundary gap
leaves the router or a shared context provider in a broken state. Other screens share the
same layout shell — once it's poisoned, child routes can't mount cleanly.

**Impact:** A single bad screen effectively locks the operator out of the rest of the UI
until they manually refresh. This compounds any individual screen error.

**Gap / follow-up:** Wrap each route-level component in an `<ErrorBoundary>` that catches
render errors and shows an inline "Something went wrong — reload this section" recovery UI
without blowing up the nav shell or other routes. React Router v6 supports per-route error
elements via `errorElement`.

---
