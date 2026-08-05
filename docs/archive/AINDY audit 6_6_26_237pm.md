Power Level Audit -aindy-runtime-6/6/26 1:41pm 

System Classification

  ┌──────────────────┬────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
  │       Axis       │       Level        │                                                  Evidence                                                   │
  ├──────────────────┼────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Execution        │ Orchestration      │ DAG executor, multi-agent delegation via AgentCoordinator, WAIT/RESUME semantics across distributed         │
  │ Capability       │ System             │ instances, AutonomousController with trigger evaluation                                                     │
  ├──────────────────┼────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Reliability      │ Production-Stable  │ EXACTLY_ONCE idempotency gate (EffectRecord), CircuitBreaker, RetryPolicy, ResumeWatchdog (flow rehydration │
  │ Level            │                    │  on restart), OTel + Prometheus, schema contract versioning                                                 │
  ├──────────────────┼────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Autonomy Level   │ Semi-Autonomous    │ Trigger evaluation, scheduled jobs, and flow execution are fully autonomous; agent execution requires an    │
  │                  │                    │ explicit human approve_run() gate before execute_run() fires                                                │
  ├──────────────────┼────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Developer        │ Platform           │ Extension ABI, sandbox runner, SyscallRegistry (capability dispatch), Nodus language with stdlib,           │
  │ Leverage         │                    │ plugin-layer router extraction, tenant isolation, multi-repo architecture                                   │
  └──────────────────┴────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  ---
  Explanation

  Execution: This isn't a workflow engine — it's one step below fully autonomous. It has a DAG executor, a priority-lane scheduler, multi-agent routing, and
  a compiled scripting language (nodus-lang) as the execution substrate. The syscall abstraction makes every capability call a first-class, traceable,
  quota-enforced operation. That's orchestration-tier design.

  Reliability: The EffectRecord idempotency gate, flow rehydration on restart, and circuit breaker put this at production-stable. It falls short of
  fault-tolerant at one specific seam: the approve_run() path bypasses SyscallDispatcher entirely, meaning concurrent approval races and orphaned approved
  states have no recovery path (AGENT-APPROVE-001b is open). One gap, well-understood.

  Autonomy: The AutonomousController + evaluate_trigger() + SchedulerEngine give the system genuine autonomous scheduling and flow execution. But the agent
  execution model requires a human checkpoint — approve_run() is an explicit design decision, not a missing feature. Semi-autonomous is accurate and
  intentional.

  Leverage: Multi-repo with published packages (@aindy/ui-kit, PyPI pending), extension ABI, plugin layer for router injection, and tenant isolation make
  this a platform. It's not yet an ecosystem only because the SDK surface is still consolidating (memory_router split pending, coordination_router ownership
  gap open).

  ---
  Ceiling

  Autonomous orchestration ecosystem — the structural ceiling without redesign. The primitives are already present: capability dispatch, idempotent
  execution, multi-agent delegation, trigger evaluation, Nodus as a programmable execution substrate, and plugin-layer extensibility. Removing the hard
  approval gate (or making it policy-configurable per agent trust tier) and closing the orphan watchdog gap would promote autonomy to full without touching
  the architecture. Publishing aindy-runtime to PyPI and finishing the memory_router / coordination_router splits would push leverage from platform to
  ecosystem.

  ---
  Constraint

  The approve-path has no idempotency and no orphan recovery.

  approve_run() bypasses SyscallDispatcher entirely — no EffectRecord, no EXACTLY_ONCE gate. A process crash mid-execution leaves an AgentRun stranded in
  approved with no reaper. This single gap: (a) caps reliability below fault-tolerant, (b) caps autonomy below autonomous (operators can't trust unattended
  execution), and (c) is the only reason the system can't be classified one tier higher on two of the four axes simultaneously.












Real User Reality audit - Same day - 1:46pm

User Perspective: What Can You Actually Do With This Today?

---
Developer (technical)

Can successfully do:
  - Spin up the full stack in one command: docker compose up -d (Postgres + pgvector + Redis + MongoDB + API)
  - Bootstrap an admin account via AINDY_BOOTSTRAP_ADMIN_EMAIL, log into /platform, and have a functional admin console
  - Create agent runs through the API or UI, inspect step-by-step execution with risk badges and tool traces
  - Submit flows, watch them transition through the state machine (pending → executing → waiting → completed), and resume WAIT-suspended runs
  - Write Nodus scripts that call LLM backends (OpenAI, DeepSeek), read/write memory nodes, and wire into the flow engine
  - Query memory via vector similarity + tag filters across /memory/*
  - Use the Approvals inbox to gate agent execution — the approve → execute path works end-to-end
  - Read real-time scheduler status, circuit breaker state, and effect record counts from the Observability dashboard
  - Register custom syscalls via SyscallRegistry and extend the platform with plugin-layer routers

Will struggle with:
  - Writing Nodus scripts without language documentation — the stdlib exists (nodus/memory.nd) but there is no developer guide in the repo explaining
  syntax, built-ins, or error semantics
  - Understanding the ExecutionPipeline → SyscallDispatcher → handler chain without reading the source; the syscall abstraction is powerful but has no
  external API reference
  - Knowing which env vars are required vs. optional — AINDY/.env.example exists, but some runtime behaviors (Redis URL, LLM keys, secret rotation) are only
  fully explained in CLAUDE.md
  - Installing without Docker: PyPI publish is open (PYPI-PUBLISH-1), so pip install aindy-runtime doesn't work today

What will break or block:
  - Any agent workflow running overnight unattended: a server crash mid-execution strands the AgentRun in approved status permanently — no reaper, no retry,
  no UI alert (AGENT-APPROVE-001b open). Manual DB intervention is the only recovery
  - /platform/flows/strategies returns 404 (OPER-DEFER-001: route not yet served)
  - Automation logs panel in Flow Engine Console calls /automation/logs, which lives in the monolith, not this runtime — that section is silently empty in
  runtime-only deployments (OPER-DEFER-002)

  ---
Power User (semi-technical)

Can successfully do:
  - Log into the /platform SPA (if someone has deployed the backend and promoted their account)
  - Use the Agent Console to create runs, see risk classifications per step, and approve/reject from the Approvals inbox
  - Monitor flow run states and manually resume a stuck WAITING flow
  - View scheduler job status, health checks, and execution metrics from the dashboards
  - Submit feedback scores on agent outputs via the analytics API (wired into the UI)
  - Inspect memory traces and agent registry entries

Will struggle with:
  - Getting past the first wall: every account must be manually promoted to admin by someone with CLI or env access — there is no self-service admin path. A
  power user who registers themselves is permanently locked at the NotAdmin screen with no recourse
  - Understanding what to PUT in an agent run creation form — the "objective" field expects Nodus-compatible instructions; submitting natural language with
  no Nodus awareness produces a failed or empty execution
  - Knowing the difference between runtime-only and full boot mode, which changes which nav items are greyed out and which routes actually work

  What will break or block:
  - The admin gate. PlatformGuard renders a terminal NotAdmin component for non-admin users — no navigation, no error message explaining how to get
  promoted. It's a dead end for anyone without a CLI-side operator
  - Any attempt to create agents without Nodus knowledge produces a run that enters pending_approval, gets approved, executes for 0ms, and shows completed
  with empty steps — visually successful, practically useless

  ---
Non-Technical User

Can successfully do:
  - Log in at /platform/login (if an operator has pre-promoted their account)
  - Click through the Agent Console, Approvals Inbox, and Flow Engine panels to view state
  - Approve or reject a pre-submitted agent run from the Approvals inbox — this is the one meaningful action with zero technical prerequisite

Will struggle with:
  - Everything before login: there is no self-service deployment, no hosted version, no install wizard. Getting to a running instance requires Docker,
  Postgres, and environment configuration
  - Creating anything — every creation surface (new agent run, new flow) requires structured input that maps to internal models. No templates, no guided
  creation flow, no defaults that "just work"
  - Interpreting the Observability and Health dashboards — the data is raw (circuit breaker states, effect record TTL counts, syscall registry depth) with
  no contextual explanation

What will break or block:
  - The entire system before they reach a browser URL — there is no path from "I want to use this" to "I am looking at the platform UI" without developer
  involvement
  - Even with access, the NotAdmin wall blocks non-promoted accounts completely

  ---
First Real-World Use Case That Would SUCCEED

  A developer builds a supervised agent workflow for content processing.

  Steps that work today:
  1. docker compose up -d — stack is up in ~60 seconds
  2. POST /auth/register → AINDY_BOOTSTRAP_ADMIN_EMAIL=dev@company.com → restart → admin active
  3. Login at /platform — full UI available
  4. Submit an agent run via the API with a Nodus script that calls the LLM client, writes a result to memory, and returns a structured output
  5. The run enters pending_approval — visible in the Approvals inbox
  6. Developer clicks Approve — execute_run() fires in a daemon thread
  7. Execution completes, steps are visible in AgentConsole with risk badges, output stored in memory
  8. Developer queries /memory/* to retrieve the result

  This path is end-to-end functional, every component is wired, and the approval gate is a feature not a bug in a supervised workflow.

  ---
  First Real-World Use Case That Would FAIL

  An operator deploys autonomous agent workflows that run overnight without human supervision.

  What happens:
  1. Agents are approved and executing
  2. The API container restarts (deploy, OOM, crash — doesn't matter)
  3. AgentRun.status is stuck at approved in the database
  4. ResumeWatchdog handles FlowRun rehydration — but AgentRun is not a FlowRun. There is no equivalent reaper for orphaned approved agent states
  5. The Approvals inbox shows the run as "Approved" — not failed, not retrying, not alarming
  6. The agent never executes again unless someone manually resets the DB row
  7. The operator has no alert, no log entry that surfaces the problem, and no UI affordance to force re-execution

  This is AGENT-APPROVE-001b. It is documented, understood, and open. Any use case that requires unattended agent execution at production uptime will hit
  it.





SYSTEM LIMIT/Leverage boundry audit - same day -2:03pm

System Limit

  ---
1. Max Workload

  Thread mode (default): 10 worker threads, 100-job queue hard cap. System's own advisory: "At ~15s/job this sustains 0.67 jobs/second." At that rate, a
  burst fills the queue in ~150 seconds, at which point every new job submission returns a QueueSaturatedError (HTTP 503, Retry-After: 5). There is no
  overflow buffer — jobs are dropped outright.

  Distributed mode (opt-in): EXECUTION_MODE=distributed + --profile full moves dispatch to Redis, removes the 100-job ceiling, and allows multiple worker
  processes. This is fully built but not the default.

  Rate limiter: 300 req/min per authenticated identity (Redis-backed). This is the API surface ceiling, independent of execution capacity.

  ---
2. Max Workflow Complexity

  Per execution unit (one flow node):
  - 100 syscalls hard cap (MAX_SYSCALLS_PER_EXECUTION). A node calling an LLM 3 times, reading memory 5 times, and writing back across multiple steps hits
  this in a non-trivial flow. The quota check is mid-execution — the run terminates in failed with RESOURCE_LIMIT_EXCEEDED, no retry.
  - 5 minutes wall clock (MAX_WALL_TIME_MS = 300,000ms). Any LLM chain with multiple round trips on a slow model will hit this.

  Across an entire DAG: each WAIT node suspends and resumes as a new EU, so multi-node flows bypass the per-EU caps. A 10-node flow with 50 syscalls per
  node works. A 2-node flow where one node does 101 syscalls fails at node 1.

  Memory bytes are tracked but not enforced — the comment in check_quota says "requires OS integration." A memory-heavy node won't be killed by quota; it
  can OOM the process.

  ---
  3. Max Users

  Registered users: No cap. Auth registration is open.

  Concurrent active users: MAX_CONCURRENT_PER_TENANT = 5 executions per user. With a 10-worker thread pool, 2 fully-loaded tenants exhaust all workers. A
  third tenant's jobs queue behind them and don't execute until a slot opens. At 20 concurrent tenants each running 1 job: the pool can serve 10 at a time,
  the other 10 queue, and all complete eventually assuming queue depth stays under 100.

  Practical active users before degradation: ~5–8 tenants doing moderate workloads in thread mode. Beyond that, queue saturation or per-tenant quota
  rejection becomes the user-visible experience.

  ---
  4. Max Automation Before Failure

  Failure mode 1 — process restart: Any AgentRun in approved status at the moment the API process restarts is permanently orphaned. No watchdog, no reaper,
  no re-enqueue. Automated pipelines that run agents overnight will accumulate stalled runs silently. (AGENT-APPROVE-001b, open.)

  Failure mode 2 — trigger evaluator exceptions: evaluate_trigger() in AutonomousController wraps all exceptions and collapses them to _decision("defer",
  0.0, "trigger evaluator failed"). The HTTP response is still 202. A broken evaluator produces permanent silent deferral — no alert, no error surfaced to
  the operator. Autonomous scheduling becomes invisible at this point.

  Failure mode 3 — queue saturation under automation: Automated triggers submitting jobs faster than 0.67/s fill the queue in 150 seconds and start
  receiving 503. There is no back-pressure mechanism that signals the trigger scheduler to slow down — it will keep retrying and keep failing.

  Safe automation ceiling: Supervised, low-frequency agent execution (< 1 job/10s) with a human watching the Approvals inbox and the Executions console.
  Beyond that, reliability drops.

  ---
  Hard Boundary

  The queue hard cap of 100 jobs combined with a single-process thread pool of 10 workers.

  Specifically: submit job 101 while the queue is full → QueueSaturatedError is raised → the calling route returns 503. This is not a warning; it is a hard
  rejection. The queue never self-heals faster than the workers drain it (0.67 j/s). Any real automation workload producing more than ~40–60
  submissions/minute will hit this ceiling within minutes of startup.

  ---
  Root Cause

  Architecture: EXECUTION_MODE=thread is the default and the common path.

  The distributed worker architecture (EXECUTION_MODE=distributed, Redis queue, separate worker process with DLQ) is fully implemented and documented. It
  removes the 100-job ceiling, decouples the execution plane from the API process, adds DLQ, and allows horizontal scaling. But it is not the default — it
  requires --profile full in Docker and explicit env var configuration. Every deployment that doesn't opt into it is running single-process, single-host,
  with a 100-job hard wall.

  The secondary cause: the orphan watchdog gap means reliability cannot be guaranteed regardless of throughput, because a single crash during execution
  creates permanent silent failures.

  ---
  Upgrade Path

  One change: make --profile full + EXECUTION_MODE=distributed the documented and default production configuration.

  This means:
  - API process handles HTTP, not execution
  - Worker process(es) consume from Redis queue — horizontally scalable by adding replicas
  - DLQ catches failed jobs instead of dropping them silently
  - 100-job queue cap replaced by Redis queue depth (unbounded for practical purposes)
  - Per-tenant quota enforcement (MAX_CONCURRENT_PER_TENANT) becomes cross-process via Redis backend, which is already implemented

  The code is already written. The compose profile is already defined. The distributed queue backend is already built with LPUSH/BRPOP, in-flight tracking,
  and requeue_stale_jobs(). The constraint is purely operational default — not architecture.





AUTH SYSTEM AUDIT — A.I.N.D.Y.-runtime - same day- 2:14pm 

  ---
  1. AUTH SYSTEM COMPONENTS

  ┌─────────────────────────────────────┬────────────────────────────────────────────┬──────────────────────────────────────────────────────────────────┐
  │              Component              │                    File                    │                               Type                               │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ KeyRing                             │ AINDY/services/auth_service.py:31          │ Two-slot JWT key ring with rotation                              │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ create_access_token()               │ auth_service.py:131                        │ JWT signing (HS256)                                              │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ decode_access_token()               │ auth_service.py:163                        │ JWT verification against KeyRing                                 │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ _resolve_authenticated_jwt_user()   │ auth_service.py:178                        │ DB lookup + is_admin/is_active/token_version check               │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ get_current_user()                  │ auth_service.py:237                        │ Primary FastAPI dependency — accepts Bearer JWT or               │
  │                                     │                                            │ X-Platform-Key                                                   │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ require_platform_admin_access()     │ auth_service.py:276                        │ Dependency wrapping get_current_user(), enforces is_admin for    │
  │                                     │                                            │ JWT users                                                        │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ _resolve_platform_key_as_user()     │ auth_service.py:290                        │ X-Platform-Key → user dict (no is_admin set)                     │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ get_optional_user()                 │ auth_service.py:334                        │ Returns user or None — used for auth-optional endpoints          │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ verify_api_key()                    │ auth_service.py:384                        │ X-API-Key header; service-to-service only (watcher, db_verify)   │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ SECRET_KEY module-level export      │ auth_service.py:94                         │ Backward-compat string; lives alongside _key_ring                │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ AuthPrincipal dataclass             │ AINDY/auth/api_key_auth.py:86              │ Typed principal (jwt | api_key) with scope list                  │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ get_authenticated_principal()       │ AINDY/auth/api_key_auth.py:118             │ Second dependency doing same job as get_current_user()           │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ require_scope()                     │ AINDY/auth/api_key_auth.py:187             │ Factory that enforces API key scope                              │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ AINDY/auth/__init__.py              │ same as above                              │ Exact duplicate of api_key_auth.py — identical content           │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ Raw jwt.decode() (unverified)       │ AINDY/platform_layer/rate_limiter.py:45    │ Signature-unverified decode for rate-limit bucketing only        │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ decode_access_token() call          │ AINDY/exception_handlers.py:158            │ JWT decode for error-log user attribution                        │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ parseJwtPayload() + isAdmin         │ aindy-ui-kit/src/context/AuthContext.jsx:8 │ Client-side JWT parsing; determines PlatformGuard gate           │
  ├─────────────────────────────────────┼────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ TOKEN_STORAGE_KEY /                 │ aindy-ui-kit/src/api/_core.js:108          │ localStorage token persistence                                   │
  │ setStoredToken()                    │                                            │                                                                  │
  └─────────────────────────────────────┴────────────────────────────────────────────┴──────────────────────────────────────────────────────────────────┘

  There is no auth middleware. Auth is entirely dependency-injection via Depends().

  ---
  2. AUTH FLOW (ACTUAL)

  JWT path (human user)

  Browser POST /auth/login {email, password}
  → auth_router.py:login()
    → authenticate_user() — bcrypt verify + is_active check
    → create_access_token({sub, email, is_admin}, token_version) — HS256 via KeyRing
    → returns {access_token, token_type: "bearer"}

  Frontend AuthContext.jsx:login()
    → loginUser() calls POST /auth/login
    → unwrapEnvelope() returns {access_token}
    → setStoredToken(access_token) → localStorage["token"] + localStorage["aindy_token"]
    → setToken(access_token)

  Subsequent API request
    → _core.js:request() — injects Authorization: Bearer {token} on every request
    → Backend route: current_user: dict = Depends(get_current_user)
      → bearer_scheme extracts credentials
      → decode_access_token(token)
        → _key_ring.verify_keys() — tries active key, then previous if in grace period
        → jwt.decode(token, key, algorithms=["HS256"])
      → _resolve_authenticated_jwt_user(payload, db)
        → parse_user_id(payload["sub"]) — UUID normalization
        → db.query(User).filter(User.id == user_uuid)
        → checks: user exists, user.token_version == payload["tv"], user.is_active
        → returns dict: {sub, user_id, email, username, is_admin, ...}
    → route handler extracts user_id = str(current_user["sub"])
    → passes to execute_with_pipeline() as user_id param
    → execution_helper.py: ctx.user_id = resolved_user_id; request.state.user_id = user_id

  X-Platform-Key path (API key)

  External caller → request with X-Platform-Key: aindy_<token>
  → get_current_user()
    → platform_key branch detected
    → _resolve_platform_key_as_user(raw_key, db)
      → sha256(raw_key) → key_hash
      → db.query(PlatformAPIKey).filter(key_hash == key_hash)
      → record.is_valid() — checks is_active + expiry + not revoked
      → raw SQL to read scopes (ARRAY/JSON compat)
      → returns {sub, user_id, auth_type: "api_key", api_key_id, api_key_scopes}

  X-API-Key path (service-to-service)

  Watcher / db_verify process → X-API-Key: <key>
  → verify_api_key()
    → checks against settings.AINDY_API_KEY + settings.AINDY_SERVICE_KEY
    → returns raw key string (no user dict)
    → router-level dependency only — no user_id propagation

  Rate limiter JWT parse (NOT auth)

  Every request → _identity_key() in rate_limiter.py
    → tries X-Platform-Key first (raw key string as bucket)
    → tries jwt.decode(token, key="", options={verify_signature: False}) — for sub claim only
    → falls back to IP
    (This is bucketing only — no verification)

  Frontend PlatformGuard

  Page load → SystemContext.jsx:bootIdentity(token)
    → GET /identity/boot with Authorization: Bearer {token}
    → AuthContext.jsx: isAdmin = parseJwtPayload(token)?.is_admin
      (client-side parse, no server round-trip for the guard decision)
    → PlatformGuard: if !isAdmin → renders <NotAdmin /> (terminal)

  ---
  3. LAYER CLASSIFICATION

  ┌─────────────────────────────────────────────────────────┬─────────────────────────────────────────┬────────────────────────────────────────────────┐
  │                        Component                        │                  Layer                  │               Correct placement?               │
  ├─────────────────────────────────────────────────────────┼─────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ KeyRing, create_access_token, decode_access_token,      │ Platform layer                          │ Yes                                            │
  │ get_current_user, verify_api_key                        │ (services/auth_service.py)              │                                                │
  ├─────────────────────────────────────────────────────────┼─────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ require_platform_admin_access                           │ Platform layer                          │ Yes                                            │
  ├─────────────────────────────────────────────────────────┼─────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ AuthPrincipal, get_authenticated_principal,             │ Platform layer (auth/api_key_auth.py)   │ Yes, but unused by routes                      │
  │ require_scope                                           │                                         │                                                │
  ├─────────────────────────────────────────────────────────┼─────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ AINDY/auth/__init__.py                                  │ Platform layer                          │ Duplicate of api_key_auth.py — redundant       │
  ├─────────────────────────────────────────────────────────┼─────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ Raw jwt.decode() in rate_limiter.py                     │ Platform layer                          │ Yes (platform layer using JWT as rate-limit    │
  │                                                         │                                         │ key is appropriate)                            │
  ├─────────────────────────────────────────────────────────┼─────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ decode_access_token in exception_handlers.py            │ Platform layer                          │ Borderline — exception handler calling auth    │
  │                                                         │                                         │ service is a cross-layer dependency            │
  ├─────────────────────────────────────────────────────────┼─────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ parseJwtPayload in AuthContext.jsx                      │ Frontend app layer                      │ This is client-side auth state management, not │
  │                                                         │                                         │  verification                                  │
  ├─────────────────────────────────────────────────────────┼─────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ mint_token / capability_token                           │ Agent runtime layer                     │ Authorization, not authentication — separate   │
  │                                                         │ (agents/capability_service.py)          │ concern                                        │
  └─────────────────────────────────────────────────────────┴─────────────────────────────────────────┴────────────────────────────────────────────────┘

  ---
  4. VIOLATIONS

  V1 — AINDY/auth/__init__.py is an exact duplicate of AINDY/auth/api_key_auth.py

  Both files contain identical content: AuthPrincipal, Scopes, get_authenticated_principal, _resolve_jwt, _resolve_api_key, require_scope. This is not
  __init__.py re-exporting api_key_auth.py — it is the same code verbatim in two files. Any change to one must be mirrored to the other or behavior diverges
  silently.

  V2 — require_scope() and get_authenticated_principal() are defined but never used by any route

  AINDY/auth/api_key_auth.py defines the entire scoped-auth system (AuthPrincipal, Scopes, require_scope). Grep across all route files returns zero hits.
  The system is complete in isolation and unreachable from the actual API surface. API key scope is enforced in get_current_user() only at the admin level
  via require_platform_admin_access() — the per-scope granularity (flow.read, memory.write, agent.run, etc.) is not enforced on any route.

  V3 — Two parallel authentication systems exist simultaneously

  get_current_user() (returns dict) and get_authenticated_principal() (returns AuthPrincipal) do the same thing via different code paths. Routes use
  get_current_user() universally. get_authenticated_principal() is a second full implementation with its own header extractors (_bearer_scheme,
  _platform_key_header) that are separate instances from those in auth_service.py. FastAPI will register both sets in the OpenAPI schema.

  V4 — Frontend logout() does not call POST /auth/logout

  AuthContext.jsx:logout() calls clearStoredToken() and setToken(null). It never calls the backend. The backend's POST /auth/logout increments
  User.token_version, which invalidates the current token on any subsequent verified request. The frontend skips this entirely. A logged-out user's token
  remains valid on the backend until it expires (24 hours) or the user re-logs in and the version increments naturally.

  V5 — SECRET_KEY module-level string lives alongside _key_ring

  auth_service.py:94: SECRET_KEY: str = settings.SECRET_KEY. This is exported as a module attribute. rotate_signing_key() updates both the ring and this
  string, but any code that imported SECRET_KEY before rotation holds a stale copy. The string is labeled "backward compat" — it is still referenced in
  _reload_key_on_sighup() and in any external code that imports it directly.

  V6 — Admin authorization check is inconsistent across routes

  flow_router.py and routes/platform/__init__.py apply require_platform_admin_access at the router level (correct — applies to all routes).
  auth_router.py:admin_invalidate_sessions does a manual in-handler check:
  if current_user.get("auth_type") == "api_key":
      scopes = set(current_user.get("api_key_scopes") or [])
      if "platform.admin" not in scopes:
          raise HTTPException(...)
  elif not current_user.get("is_admin"):
      raise HTTPException(...)
  This is duplicate admin-enforcement logic that should use require_platform_admin_access as a dependency. Manual scope string "platform.admin" is hardcoded
  here rather than using Scopes.PLATFORM_ADMIN from auth/api_key_auth.py.

  ---
  5. USER CONTEXT PROPAGATION

  ┌──────────────────────────────┬─────────────────────────────────────────────────────────────────────────────────┬────────────────────────────────────┐
  │           Surface            │                               How user_id arrives                               │            Consistent?             │
  ├──────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────┤
  │ Routes                       │ current_user["sub"] via Depends(get_current_user)                               │ Yes — all routes use this          │
  ├──────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────┤
  │ execute_with_pipeline()      │ user_id=str(current_user["sub"]) passed explicitly                              │ Yes                                │
  ├──────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────┤
  │ execute_with_pipeline_sync() │ Same                                                                            │ Yes                                │
  ├──────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────┤
  │ ExecutionContext.user_id     │ Set in execution_helper.py:37 from param or request.state.user_id               │ Yes                                │
  ├──────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────┤
  │ request.state.user_id        │ Set at pipeline entry in execution_helper.py:43                                 │ Yes                                │
  ├──────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────┤
  │ Async jobs (QueueJobPayload) │ context["user_id"] from submission                                              │ Yes                                │
  ├──────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────┤
  │ Memory system                │ user_id=str(current_user["sub"]) passed to DAO layer                            │ Yes                                │
  ├──────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────┤
  │ Watcher/db_verify routes     │ No user_id — verify_api_key() returns a key string, not a user dict             │ Diverges from all other routes     │
  ├──────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────┤
  │ log_requests middleware      │ _extract_user_id_from_request() — reads request.state.user_id first, then       │ Redundant — could read state set   │
  │                              │ re-decodes the JWT token                                                        │ by pipeline                        │
  └──────────────────────────────┴─────────────────────────────────────────────────────────────────────────────────┴────────────────────────────────────┘

  The key extraction pattern is: str(current_user["sub"]). Since _resolve_platform_key_as_user() sets "sub" identically to JWT users, this is consistent.
  One inconsistency: some routes use current_user["sub"] (direct subscript — will KeyError if key missing), others use current_user.get("sub").
  agent_router.py:50 wraps it in a helper normalize_uuid(current_user["sub"]). No route currently breaks because "sub" is always set in both auth paths, but
  the style is uneven.

  ---
  6. DUPLICATION / FRAGMENTATION

  1. AINDY/auth/__init__.py = AINDY/auth/api_key_auth.py — identical files (see V1)
  2. Three HTTPBearer instances exist simultaneously:
    - bearer_scheme at module-level in auth_service.py:102
    - _bearer_scheme inside auth/api_key_auth.py:111
    - _bearer_scheme inside auth/__init__.py:111 (same again)
  3. Two APIKeyHeader("X-Platform-Key") instances:
    - _platform_key_header in auth_service.py:103
    - _platform_key_header in auth/api_key_auth.py:110
  4. JWT decode appears in four separate locations:
    - auth_service.py:163 — decode_access_token() canonical implementation
    - auth/api_key_auth.py:143 — calls decode_access_token (delegates, OK)
    - platform_layer/rate_limiter.py:45 — raw jwt.decode() with no signature verification
    - exception_handlers.py:158 — calls decode_access_token for error-log attribution
  5. is_admin checked in three ways:
    - Backend: _resolve_authenticated_jwt_user() reads it from user.is_admin DB field
    - require_platform_admin_access(): checks current_user.get("is_admin")
    - Frontend AuthContext.jsx: client-side parses JWT and reads payload.is_admin claim

  ---
  7. FRONTEND CONNECTION

  Token lifecycle:
  - POST /auth/login → backend issues JWT → frontend stores in localStorage["token"] + localStorage["aindy_token"] (dual-write for legacy compat)
  - All subsequent API calls: Authorization: Bearer {token} injected by _core.js:request()
  - Token expiry: frontend checks exp claim every 60 seconds; fires aindy:session-expired custom event on 401 responses
  - Token rotation: none — frontend has no mechanism to refresh without re-login

  Alignment gaps:
  - PlatformGuard.isAdmin is set from parseJwtPayload(token).is_admin — client-side only. Does not revalidate against the server. If an admin is promoted
  after their token was issued, is_admin in the JWT is false until they re-login. If a user is demoted (token_version bump via admin_invalidate_sessions),
  their next request is rejected by the backend, but the frontend UI shows them as admin until that request fails.
  - logout() in AuthContext.jsx is client-side only (clears localStorage). Never calls POST /auth/logout. Backend token remains valid for up to 24 hours
  after frontend logout.
  - bootIdentity() calls GET /identity/boot with the token for system context, but PlatformGuard does not wait for this result — isAdmin is already
  determined from the JWT payload before boot completes.

  ---
  8. CURRENT STATE SUMMARY

  ┌───────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
  │       Dimension       │                                                            State                                                            │
  ├───────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Auth centralized?     │ PARTIAL — Core JWT logic is in auth_service.py but a parallel system (auth/api_key_auth.py) exists unused, and __init__.py  │
  │                       │ is a full duplicate                                                                                                         │
  ├───────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Auth correctly        │ PARTIAL — Platform-layer dependency injection is correct, but the frontend auth enforcement (PlatformGuard) is client-side  │
  │ placed?               │ JWT parsing, not a server-verified gate                                                                                     │
  ├───────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Auth consistent       │ PARTIAL — user_id propagation via current_user["sub"] is consistent across routes. Admin enforcement is not (router-level   │
  │ across the system?    │ vs. in-handler). verify_api_key() creates an auth path with no user_id. Logout is client-only while the backend has         │
  │                       │ server-side invalidation.                                                                                                   │
  └───────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  ---
  9. FINAL VERDICT

  The core JWT auth machinery is sound and correctly centralized in auth_service.py, but the system carries a complete second auth implementation
  (AINDY/auth/) that is fully built, correctly wired internally, and never called by a single route — making per-scope API key enforcement entirely dead
  code while the main codebase continues to use untyped dict returns and manual is_admin string checks.


# SYSTEM INTEGRITY AUDIT — A.I.N.D.Y. - Smae Day-2:35pm 

  ---

  ## 1. END-TO-END FLOW MAP

  ```
  HTTP Request
    → CORSMiddleware + SlowAPIMiddleware (rate limit per identity)
    → _guard_metrics_endpoint (IP/Bearer check for /metrics only)
    → enforce_execution_contract (marks pipeline entry on request.state)
    → log_requests (assigns trace_id UUID, sets request.state.trace_id)

  Auth
    → Depends(get_current_user) on route
      → HTTPBearer extracts token OR APIKeyHeader extracts X-Platform-Key
      → decode_access_token(token) → KeyRing.verify_keys() → jwt.decode(HS256)
      → _resolve_authenticated_jwt_user(payload, db)
        → db.query(User) — checks exists, is_active, token_version match
        → returns dict {sub, user_id, email, is_admin}
      (OR _resolve_platform_key_as_user → SHA256 lookup against PlatformAPIKey table)

  Route Handler
    → extracts user_id = str(current_user["sub"])
    → calls execute_with_pipeline() [async] or execute_with_pipeline_sync() [sync → asyncio.run()]

  Pipeline Entry (ExecutionPipeline.run)
    → ExecutionContext.from_request() — captures trace_id, route_name
    → ctx.user_id = user_id; request.state.user_id = user_id
    → mark_execution_pipeline_entered(request)
    → _safe_emit_event("execution.started") → db.flush() + db.commit() [Transaction 1]
    → _safe_set_parent_event(started_event_id) → ContextVar
    → _safe_require_eu() → require_execution_unit() → db INSERT ExecutionUnit → db.commit() [Transaction 2]
    → _safe_check_quota() → ResourceManager.can_execute() (Redis or local counter)
    → _safe_rm_mark_started() → ResourceManager.mark_started()
    → handler(ctx) [route logic executes here, may do its own db commits — Transaction 3+]
    → extract result + signals (memory hints, injected events, log signal)
    → _apply_execution_signals()
        → _apply_memory_signals() → MemoryIngestQueue.enqueue() [fire-and-forget, may drop silently]
        → _apply_event_signals() → emit_system_event() per signal [separate commits]
    → _safe_rm_record_and_complete() → usage + mark_completed()
    → _inject_execution_envelope() — wraps result in standard envelope
    → _safe_emit_event("execution.completed") → db.flush() + db.commit() [Transaction N]
    → _safe_finalize_eu() → ExecutionUnitService.update_status("completed")
    → ExecutionResult → adapt_response() → JSONResponse

  Async/Inline split (within handler if job dispatch occurs):
    → async_heavy_execution_enabled()? → dispatch() → INLINE or thread pool or Redis queue
    → INLINE: handler_fn() blocks; result returned immediately
    → THREAD: ThreadPoolExecutor.submit(handler_fn) — no ContextVar propagation
    → DISTRIBUTED: QueueJobPayload → Redis LPUSH; worker BRPOP; re-queues stale on restart

  Memory Persistence (background):
    → MemoryIngestQueue daemon thread → persist_memory_ingest_payload()
      → SessionLocal() [own session] → MemoryNodeDAO.save() → embedding queue → pgvector upsert

  Response
    → adapt_response() → JSONResponse with execution envelope
    → log_requests finishes: RequestMetricWriter.enqueue() [metrics, async]
    → add_api_version_headers adds X-API-Version
  ```

  ---

  ## 2. LAYER INTEGRITY

  **Runtime layer** (`kernel/`, `core/`, `runtime/`): Handles syscall dispatch, scheduler, flow execution, resource quotas, EffectRecord idempotency.
  Generally clean.

  **Platform layer** (`platform_layer/`, `services/`, `routes/`): Auth, metrics, rate limiting, async job service, event system, pipeline.

  **Violations:**

  - `AINDY/core/execution_dispatcher.py` opens its own `SessionLocal()` inside `_enqueue_distributed` to emit a `job_enqueued` event. This is the execution
  dispatcher layer directly managing DB sessions — a responsibility that belongs to the service or event layer.

  - `AINDY/routes/auth_router.py` runs through `execute_with_pipeline_sync`. Auth requests create an ExecutionUnit, emit execution.started/completed events,
  and go through quota checks. Every login and register is an "execution" with resource tracking overhead. The pipeline was not designed for auth
  primitives, and this choice creates event noise and DB writes on every unauthenticated request.

  - `AINDY/exception_handlers.py` calls `decode_access_token` directly from the exception handling layer. The exception handler is doing auth work for the
  purpose of logging. This is a cross-layer dependency without a justified reason — it could read `request.state.user_id` instead.

  - `AINDY/memory/memory_ingest_service.py` opens `SessionLocal()` outside any request context. This memory write bypasses the route session, creating a
  second concurrent session to the same tables. This is correct from an isolation standpoint but breaks the "one session per request" convention and creates
  independent transaction boundaries.

  - `execute_with_pipeline_sync()` calls `asyncio.run()`. This bridges synchronous routes into an async pipeline. It works in FastAPI's threadpool model,
  but `coordination_router.py` calls this on every one of its 15+ endpoints — each call creates and tears down a new event loop. No technical correctness
  issue, but it introduces platform-layer async machinery into what should be a transparent route invocation.

  **Boundary verdict:** The runtime/platform boundary is mostly clean. The platform/routes boundary has consistent violations where routes directly manage
  sessions and where the execution pipeline absorbs concerns it shouldn't own (auth events, cross-layer session management).

  ---

  ## 3. EXECUTION GUARANTEES

  **Do all executions reach terminal state?**

  For flows: Yes — `ResumeWatchdog` rehydrates all `waiting` FlowRun rows on restart. The flow state machine is durable.

  For agent runs: **No.** A process crash during `execute_run()` leaves `AgentRun.status = "approved"` with no recovery. No reaper, no re-enqueue. This is
  AGENT-APPROVE-001b — documented, open.

  For pipeline executions: The `finally` block in `ExecutionPipeline.run()` always calls `_safe_rm_mark_completed` if `rm_started=True`, so the
  ResourceManager counter always decrements. But `_safe_finalize_eu` is not in the `finally` block — it's called on the happy path and on each specific
  exception branch. If a novel exception escapes the `except Exception` catch (which should be impossible but is architecturally possible), the EU stays in
  `executing` indefinitely.

  **Can jobs be lost?**

  - Thread mode: Yes. A process crash with jobs in the thread pool (or pending in the semaphore queue) loses those jobs completely. No persistence, no DLQ,
  no recovery on restart.

  - Distributed mode: No. The queue's in-flight tracking and `requeue_stale_jobs()` on worker startup recover crashed jobs. DLQ captures terminal failures.

  - Memory writes: Yes, always. `MemoryIngestQueue.enqueue()` is fire-and-forget. A full queue silently drops writes. A process crash drops the entire
  queue. No retry, no DLQ.

  **Async vs inline behavior:**

  `async_heavy_execution_enabled()` gates inline vs. async. In TEST_MODE/TESTING this is always False — all jobs run inline. In production with
  `AINDY_ASYNC_HEAVY_EXECUTION=0` (the default), jobs also run inline. Only when `AINDY_ASYNC_HEAVY_EXECUTION=1` do jobs go to the thread pool or Redis
  queue. This means:

  - In the default production configuration, async jobs run inline, blocking the request until complete
  - Inline execution returns the result; async execution returns immediately with a job_log_id
  - These are not semantically equivalent — callers get a complete result vs. a poll URL
  - A handler written for inline semantics may not behave correctly in async mode (e.g., it may rely on the result being present before the response
  returns)

  ---

  ## 4. DATA CONSISTENCY

  **Transaction structure:**

  Every pipeline execution performs a minimum of 4 independent DB commits against the same session:

  1. `execution.started` event — `db.flush()` + `db.commit()` in `_persist_system_event`
  2. ExecutionUnit creation — committed in `require_execution_unit` / `execution_gate`
  3. Handler-level commits — route logic commits its own data
  4. `execution.completed` or `execution.failed` event — another `db.flush()` + `db.commit()`

  These are **not atomic**. The handler can commit its data (step 3), then the completed event commit (step 4) can fail — leaving committed data without a
  corresponding completion event. The reverse is not possible (event commits before data) because the handler runs between steps 1 and 4.

  **Hidden coupling via `db.flush()`:**

  `_persist_system_event()` calls `db.flush()` then `db.commit()`. `db.flush()` pushes ALL pending ORM changes on the session to the DB (without
  committing). If a route handler has ORM changes in the `db.add()` / pending state that haven't been committed yet, the event emission's `db.flush()` sends
  those to the DB as part of the event flush. The subsequent `db.commit()` commits both the event AND those pending handler changes. This is a hidden
  dependency — event emission can commit route handler data as a side effect.

  **Race conditions:**

  - `approve_run()` CAS: Protected (AGENT-APPROVE-001a closed). The atomic UPDATE WHERE status='pending_approval' is safe.
  - Concurrent flow rehydration: `flow_run_rehydration.py` registers flows in `_waiting` dict. Multiple API instances run separate rehydrations. Redis wait
  registry (`RedisWaitRegistry`) provides cross-instance deduplication.
  - EffectRecord idempotency: `db.flush()` then `db.commit()` for EXACTLY_ONCE handlers. Safe within a single process, but a crash between flush and commit
  leaves a pending EffectRecord — which is explicitly excluded from TTL cleanup. On restart, the pending record blocks re-execution correctly.

  **User data scoping:**

  All memory DAOs pass `user_id` explicitly and filter by it. `MemoryNodeDAO.save()`, `MemoryTraceDAO.create_trace()` all require `user_id`. Route handlers
  extract `user_id` from `current_user["sub"]` and pass it explicitly. Scoping is consistent and correct for the memory system.

  ---

  ## 5. EVENT SYSTEM INTEGRITY

  **Are events always emitted?**

  `execution.started`, `execution.completed`, `execution.failed`, `execution.waiting` are emitted in all execution branches (success, HTTPException,
  Exception, WaitSignal). The `required_side_effects` flag (`ctx.metadata.get("db") is not None`) controls whether emission failures raise or swallow. When
  a DB session is present, failures on required events raise `SystemEventEmissionError` — which is caught by the pipeline's `except Exception` branch, which
  then tries to emit `execution.failed`. This creates a potential infinite loop risk: if event emission is failing due to a DB error, the failure handler
  also tries to emit an event using the same DB session.

  In practice this doesn't loop because `_safe_emit_event` returns `None` on failure without raising for the second emission — but the logic is fragile at
  this boundary.

  **Duplicate events:**

  Route handlers can call `queue_system_event` or `emit_system_event` directly AND return signals that cause the pipeline to emit events. A handler that
  emits `execution.completed` directly AND returns a signal dict with `events: [{event_type: "execution.completed"}]` would produce two events. There's no
  deduplication. The `_has_recent_feedback_event` function exists for feedback signals only.

  **Event ownership:**

  The pipeline owns lifecycle events. Route handlers own domain events. The boundary is enforced by convention, not by mechanism. Nothing prevents a route
  handler from emitting `execution.completed` directly.

  **Missing events:**

  For watcher/db_verify routes (using `verify_api_key`): no user_id, no `execute_with_pipeline` — these routes produce no execution events and no
  ExecutionUnit. They're invisible to the event system.

  ---

  ## 6. MEMORY SYSTEM INTEGRITY

  **Is capture deterministic?**

  Memory capture happens through three paths:

  1. `execution_hints.memory` / `memory_hint` keys in handler return dict — captured via `_extract_execution_result_and_signals`
  2. `_safe_capture_memory_hint` called within signal processing
  3. Direct calls to `MemoryNodeDAO` from route handlers bypassing the signal system

  All paths converge at `MemoryIngestQueue.enqueue()`. Enqueue is `put_nowait` — non-blocking, drops silently on full queue. No acknowledgment, no retry.
  Memory capture is **not deterministic under load** — identical execution paths produce different memory writes depending on queue depth at the moment of
  enqueue.

  **Unwanted capture:**

  Any handler that returns a dict with a `memory_hint` key will trigger memory capture even if that wasn't the handler author's intent. The key name is
  generic enough that accidental matches are possible. There's no explicit opt-in declaration — it's duck-typed.

  **Memory-context relationship:**

  Memory writes in `persist_memory_ingest_payload()` use their own `SessionLocal()`. They are committed independently of the execution that triggered them.
  If the route's handler commit succeeds but the memory ingest worker processes the write and fails (e.g., pgvector unavailable), the memory write fails
  silently — no error surfaced to the user, no compensating action.

  ---

  ## 7. AUTH & USER CONTEXT

  See the full auth audit above. Summary for this context:

  `user_id` propagation is consistent: `current_user["sub"]` → `execute_with_pipeline(user_id=...)` → `ctx.user_id` → `request.state.user_id`. This flows
  correctly through routes, pipeline, and event emission.

  In **async thread-pool jobs** (thread mode), `user_id` is captured in the closure before dispatch. The ContextVar trace context (`trace_id`,
  `parent_event_id`, `pipeline_active`) is **NOT propagated to the thread** — `ThreadPoolExecutor.submit()` does not copy Python ContextVar state. Trace
  continuity breaks at the thread boundary.

  In **distributed mode**, the worker explicitly restores `trace_id` and `eu_id` from `QueueJobPayload.context` before execution. Thread-pool mode has no
  equivalent restoration.

  ---

  ## 8. ASYNC SYSTEM

  **Thread mode (default `AINDY_ASYNC_HEAVY_EXECUTION=0`):**

  Jobs execute inline, blocking the route until complete. This is consistent and correct. No concurrency, no async complexity. Maximum 1 job per route
  invocation.

  **Thread mode with async enabled (`AINDY_ASYNC_HEAVY_EXECUTION=1`):**

  `ThreadPoolExecutor.submit(handler_fn)` — handler_fn is a closure. The Future is returned immediately; the route returns a job_log_id. The handler runs in
  a worker thread. ContextVar state is not propagated. If the process crashes, the job is lost. No DLQ, no stale-job recovery.

  The inline and async paths return **different response shapes**: inline returns the full result; async returns `{job_log_id, poll_url, status: "QUEUED"}`.
  Code calling `execute_with_pipeline` cannot distinguish these without inspecting the result.

  **Distributed mode:**

  DLQ, in-flight tracking, exponential retry backoff, stale-job recovery on startup. Semantically closer to correct async behavior. The worker uses
  `_execute_job_inline` — same code path as inline, ensuring execution parity.

  **Inline fallback in test mode:**

  `async_heavy_execution_enabled()` returns False when `TESTING=1`. All jobs run inline in tests. This means tests never exercise the async dispatch path,
  thread pool contention, or queue saturation. Test coverage does not validate production async behavior.

  ---

  ## 9. FAILURE HANDLING

  | Failure | What happens | Visible? | Recoverable? |
  |---|---|---|---|
  | DB connection failure at auth | `_resolve_authenticated_jwt_user` catches, returns 503 | Yes — HTTP 503 | N/A — request fails cleanly |
  | DB failure during EU creation | `_safe_require_eu` catches, logs debug, continues without EU | No — silent | Not applicable; EU not created |
  | DB failure during event emission | `_emit_system_event_failure_fallback` writes `error.system_event_failure` event | Partial — secondary event if DB
  recovers | No |
  | DB failure during handler | Handler raises, pipeline catches, emits `execution.failed`, returns 500 | Yes | No |
  | Worker crash (thread mode) | In-progress job is lost. Next startup has no recovery | No | No |
  | Worker crash (distributed mode) | Stale job detected by `requeue_stale_jobs()` on next worker startup | Yes — after visibility timeout | Yes |
  | Memory queue full | `enqueue()` returns False, `_dropped_total` increments, Prometheus counter increments | Metrics only — no log, no user error | No —
  write is permanently lost |
  | Memory ingest worker failure | Exception caught in `_process_one`, logged as WARNING | Yes — WARNING log | No — that payload is dropped |
  | Agent execution crash | AgentRun stays `approved` indefinitely | No | Manual DB intervention only |
  | Redis unavailable (quota) | `_get_redis()` falls back to in-process counter | Partial — Prometheus `quota_redis_mode` metric | Yes — degrades gracefully
  |
  | Circuit breaker open | `CircuitOpenError` raised | Yes — `CircuitOpenError` is a known exception type | Yes — when circuit resets |

  **Key gap:** Worker crash in thread mode is invisible and unrecoverable. The system only provides this guarantee in distributed mode.

  ---

  ## 10. OBSERVABILITY

  **Can you trace a request end-to-end?**

  Yes, partially. `trace_id` is assigned in `log_requests` middleware and attached to: request.state, response headers (X-Trace-ID), all SystemEvents,
  ExecutionUnit rows, JobLog rows, and QueueJobPayload. A single trace_id can link: HTTP log line → SystemEvent(started) → ExecutionUnit →
  SystemEvent(completed) → JobLog.

  The break point: trace_id is NOT propagated to the ThreadPoolExecutor thread in async mode. Events emitted from the async thread don't carry the original
  trace_id. They carry whatever trace_id `get_trace_id()` returns from the new thread's empty ContextVar — which is an empty string or a fresh UUID.

  **Can you debug failures without reading code?**

  Partially. The structured JSON log format includes `trace_id`, `user_id`, `route`, `status_code`, `duration_ms`. Failures emit `execution.failed`
  SystemEvents with `detail` payloads. The `side_effects` dict in `ctx.metadata` tracks which pipeline operations succeeded or failed.

  What's missing: most pipeline _safe_* failures log at DEBUG or are silently recorded in `side_effects` without any log emission. A developer reading
  production logs won't see EU creation failures or quota check failures unless they query the DB for `side_effects` in the ExecutionUnit metadata.

  **Are logs sufficient?**

  The structured format is good. The log level choices are not: `_safe_*` failures at DEBUG mean silent degradation in production where DEBUG is off. The
  quota Redis fallback logs at WARNING on first occurrence, then DEBUG after. Memory drops log nothing (only metrics).

  ---

  ## 11. STRUCTURAL RISKS

  **Tight coupling:**

  - `ExecutionPipeline` is tightly coupled to the DB session via `ctx.metadata["db"]`. The pipeline cannot run without a DB session for event emission. A
  route that wants to skip event tracking can't do so without passing `None` explicitly, which changes the `required_side_effects` behavior.

  - `async_job_service.submit_async_job()` has direct knowledge of `AutomationLog`, `JobLog`, `ExecutionUnit`, `SyscallContext`, `AutonomousController`,
  `RetryPolicy`, `DistributedQueue`, `QueueSaturatedError`, `SystemEvent` — it is the most coupled module in the system.

  **Hidden dependencies:**

  - `_persist_system_event` calls `db.flush()` which commits all pending ORM changes on the session (documented above). This is not visible to the caller.

  - `execute_with_pipeline_sync()` calls `asyncio.run()`. If called from an already-running event loop (any async context), it raises `RuntimeError: This
  event loop is already running`. This is a hidden constraint: all callers of `execute_with_pipeline_sync` must be in a non-async context.

  - `rate_limiter._identity_key()` calls `jwt.decode()` with `verify_signature=False`. Any code that calls the rate limiter will trigger JWT parsing even
  for non-JWT requests. This is invisible from the rate limiter's public interface.

  **Circular dependency risk:**

  `system_event_service.py` imports `JobLog` at module level (line 3). `async_job_service.py` calls `emit_system_event`. `execution_signal_helper.py` calls
  `queue_system_event`. The chain `async_job_service → system_event_service → JobLog → db.models.job_log` is a tight coupling from the service layer into
  the DB models. The lazy imports (inside function bodies) throughout the codebase exist specifically because the import graph would otherwise produce
  circular imports — they mask the underlying structural coupling.

  **Unclear ownership:**

  - `execution_dispatcher.py` owns the INLINE/ASYNC decision but also directly opens `SessionLocal()` for event emission.
  - `async_job_service.py` owns job submission but also orchestrates `AutonomousController.evaluate_live_trigger()` — a business-logic concern inside an
  infrastructure module.
  - Memory capture is initiated by the pipeline (via signals), but the pipeline has no visibility into whether the capture succeeded or failed.

  ---

  ## 12. PRODUCTION READINESS

  **Is this system safe for real users? PARTIAL**

  The core request path (auth → route → pipeline → response) is solid. Auth is correctly placed. User data scoping is consistent. The execution contract is
  enforced. Rate limiting is in place.

  **What would fail in production first:**

  1. **Thread-mode job loss on deploy.** A rolling deploy or container restart with in-flight jobs in `ThreadPoolExecutor` loses those jobs permanently. No
  warning, no requeue. This is the first thing that will cause production incidents.

  2. **Agent run orphaning.** Any agent execution that spans a restart produces a permanently stalled run. With automated scheduling, this accumulates over
  time until operators clear them manually.

  3. **Memory write loss under load.** `MemoryIngestQueue` is bounded. Any traffic spike that fills the queue silently drops memory writes. Users don't see
  the error; memory is incomplete. This is invisible until a user reports missing context.

  4. **`asyncio.run()` called from nested event loop.** If any middleware or async route handler calls into code that eventually calls
  `execute_with_pipeline_sync`, the `asyncio.run()` raises `RuntimeError: This event loop is already running`. This would produce a 500 with no obvious root
  cause in logs.

  ---

  ## 13. TOP 5 ARCHITECTURAL WEAKNESSES

  **1. Thread-mode async has no durability.**
  `ThreadPoolExecutor.submit()` + no recovery = silent job loss on process crash. This is the default mode. Every job dispatched in the default
  configuration is ephemeral.

  **2. Event emission and data writes share a session but are in separate transactions.**
  `_persist_system_event` calls `db.flush()` on a session that may have pending handler changes. This causes handler data to be committed as a side effect
  of event emission. These are not atomic. Data and event consistency cannot be guaranteed.

  **3. Agent execution lifecycle has a permanent liveness gap.**
  `AgentRun` has no watchdog for the `approved → executing` transition. A process crash at any point in `execute_run()` leaves the run permanently stranded.
  With scheduled autonomous triggers, this accumulates indefinitely with no self-healing.

  **4. ContextVar trace context is not propagated to ThreadPoolExecutor threads.**
  Trace_id, eu_id, and parent_event_id are ContextVar-based. Python `ThreadPoolExecutor.submit()` does not copy ContextVar state. Events and logs from async
  threads have no trace continuity. Observability breaks exactly where it matters most — at async execution boundaries.

  **5. `require_scope()` and `AuthPrincipal` are fully built but never used.**
  The entire scoped API key authorization system (`AINDY/auth/api_key_auth.py`) is unreachable from any route. API key scope is nominally enforced (scopes
  are stored in the DB) but never verified at the route level. Any API key with any scopes has the same access as an admin JWT for all routes that accept
  it.

  ---

  ## 14. TOP 5 STRENGTHS

  **1. The execution pipeline is architecturally sound and crash-safe.**
  The `finally` block guarantees `_safe_rm_mark_completed` always executes. The `_safe_*` pattern prevents cascade failures. The pipeline degrades
  gracefully — EU registration failure doesn't abort the request. This is mature defensive design.

  **2. Flow rehydration is correct and durable.**
  `ResumeWatchdog` + Redis wait registry ensures WAITING flows survive restarts. The two-level durability (DB state + Redis pub/sub with fallback re-query)
  is the right design for distributed resumption.

  **3. Resource quota enforcement is multi-instance aware.**
  `ResourceManager` uses Redis-backed counters for the tenant concurrency check. Fallback to in-process counters on Redis failure with graceful degradation.
  The `resource_available` event fires on capacity release, allowing waiting flows to re-enqueue without polling.

  **4. EffectRecord idempotency for syscall-level EXACTLY_ONCE semantics.**
  `db.flush()` + `db.commit()` for pending EffectRecords, plus explicit exclusion of pending records from TTL cleanup, makes the idempotency gate correct
  under the crash scenarios where it matters.

  **5. The distributed queue backend is a complete upgrade path that requires no code changes.**
  `EXECUTION_MODE=distributed` switches the dispatch path end-to-end: thread pool → Redis queue, DLQ, stale-job recovery, retry backoff, cross-process trace
  restoration. The architecture was designed to support this mode without invasive changes, and the implementation is production-grade.

  ---

  ## 15. FINAL VERDICT

  The architecture is **sound as a foundation but incomplete as a production system**. 
  
  The execution primitives are well-designed: the pipeline is resilient, flow rehydration is correct, idempotency is properly scoped, and the distributed upgrade path is built. What undermines production
  trustworthiness is a cluster of correctness gaps at the lifecycle boundaries — the thread-mode async path has no durability, agent execution has no
  liveness guarantee after crash, memory writes are silently losable under load, and the entire scoped-auth system is dead code, leaving API keys
  functionally unscoped. None of these require redesign. Each is a contained failure to wire up what is already built: the worker DLQ exists but isn't the
  default; the watchdog is documented but unimplemented; the scope enforcement layer is complete but not called. The system is three or four concrete
  changes away from reliable production operation, not three or four architectural rewrites.Completed a full 15-section System Architecture Integrity Audit.
  No code changes were made — the audit is ready for your review. No pending actions.


---
# TIER 1 PRODUCTION BLOCKERS — ADDRESSED 2026-06-06

## Blocker 1 — AGENT-APPROVE-001b: Orphan Watchdog
STATUS: ALREADY COMPLETE (predated this audit session)

Finding: `_recover_orphaned_approved_runs()` was fully implemented in `AINDY/platform_layer/scheduler_service.py:480-528`
and registered as a scheduler job (every 5 min) at line 296-304. TECH_DEBT.md already showed "All gaps closed (2026-06-06)".
CLAUDE.md was stale — updated to reflect closed status.

Tests confirmed passing: `tests/unit/test_agent_approve_watchdog.py` — 4/4 green.

Behavior: Queries AgentRun rows with status='approved' and approved_at older than 10 minutes (ORPHANED_APPROVED_THRESHOLD_MINUTES),
re-dispatches execute_run() in a fresh daemon thread for each, capped at 50 per sweep. execute_run() guards on
status=='approved' at entry so re-dispatch is safe if the original thread recovered late.

CLAUDE.md updated: AGENT-APPROVE-\* entry now shows watchdog closed 2026-06-06.

---

## Blocker 2 — Thread-mode async has no durability (OPER-EXEC-001)
STATUS: CLOSED 2026-06-06

Finding: The distributed queue backend (Redis + worker + DLQ + stale-job recovery) was already production-grade.
The gap was purely operational: `docker-compose.yml` worker service did not set EXECUTION_MODE=distributed,
so `--profile full` brought Redis and the worker online while the API continued dispatching to its own
ThreadPoolExecutor (worker was idle, jobs still ephemeral).

Changes made:
- `docker-compose.yml` worker service: added `EXECUTION_MODE: distributed` to environment block — workers
  always consume from the distributed queue, overriding whatever is in .env.
- `docker-compose.yml` header: updated "Production-shaped" comment to flag that EXECUTION_MODE=distributed
  must also be set in AINDY/.env for the API to route jobs to the worker.
- `AINDY/.env.example`: added WARNING under EXECUTION_MODE=thread documenting the durability gap and
  directing operators to distributed + --profile full for production.
- `TECH_DEBT.md`: added OPER-EXEC-001 entry (closed).
- `CLAUDE.md` prefix registry: added OPER-EXEC-\* family.

No application code was changed. The fix closes the configuration gap that made the production architecture
invisible to operators running the standard compose deployment.

---

## Blocker 3 — ContextVar not propagated to ThreadPoolExecutor threads (OPER-EXEC-002)
STATUS: CLOSED 2026-06-06

Finding: `ThreadPoolExecutor.submit(fn)` runs fn in a fresh context. trace_id, parent_event_id, pipeline_active
(trace_context.py) and syscall_trace_id, syscall_eu_id (syscall_dispatcher.py) were lost at every async
execution boundary. Events and logs from worker threads had no trace continuity.

Fix: `copy_context()` (Python stdlib 3.7+) captures a snapshot of the current context before submit.
`ctx.run(fn)` executes fn inside that snapshot. Applied at both submit sites:
- `AINDY/core/execution_dispatcher.py:453` — primary async dispatch path
- `AINDY/platform_layer/async_job_service.py:620` — submit_async_job() thread-pool path

New tests: `tests/unit/test_contextvar_thread_propagation.py` — 3 shapes (trace_id, eu_id, pipeline_active) — 3/3 green.
TECH_DEBT.md: added OPER-EXEC-002 entry (closed).
CLAUDE.md prefix registry: included in OPER-EXEC-\* family.

---
# TIER 2 AUTH WIRING — ADDRESSED 2026-06-06/07

PRs: #46 (auth wiring V1/V4/V6), #47 (guard split hotfix — reverted over-tightening)
Tests: `tests/unit/test_auth_wiring.py` — 14 tests, all green

---

## V1 — Duplicate auth implementation (AINDY/auth/__init__.py was 211-line verbatim copy)
STATUS: CLOSED 2026-06-06

Finding: `AINDY/auth/__init__.py` contained a 211-line verbatim copy of the canonical
`api_key_auth.py` implementation. Two divergent copies of `AuthPrincipal`, `Scopes`,
`get_authenticated_principal`, and `require_scope` — any bug fix or scope addition
in one would silently not apply to the other.

Fix: Replaced `AINDY/auth/__init__.py` with a 7-line re-export shim:
```python
"""auth package — canonical implementation lives in api_key_auth.py."""
from AINDY.auth.api_key_auth import (  # noqa: F401
    AuthPrincipal,
    Scopes,
    get_authenticated_principal,
    require_scope,
)
```
All imports from `AINDY.auth` now resolve to the single canonical source.
`TECH_DEBT.md`: AUTH-V1 entry (closed).

---

## V4 — Frontend logout did not call server-side logout endpoint
STATUS: CLOSED 2026-06-06 (ui-kit repo — separate commit)

Finding: Platform SPA `logout()` in `AuthContext.jsx` cleared localStorage and set
`isAuthenticated=false` but never called `POST /auth/logout`. The server-side JWT
invalidation endpoint (token version bump) was effectively unreachable from the SPA.

Fix (in `C:\dev\aindy-ui-kit\src\`):
- `api/_routes.js`: added `LOGOUT: \`${BASE}/auth/logout\`` to the `AUTH` routes object
- `api/auth.js`: added `logoutUser()` best-effort function calling `POST /auth/logout`
- `context/AuthContext.jsx`: `logout()` now calls `logoutUser()` before clearing local
  state. Best-effort — client-side state is always cleared regardless of server response.
`TECH_DEBT.md`: AUTH-V4 entry (closed).

---

## V6 — admin_invalidate_sessions lacked scope enforcement for API keys
STATUS: CLOSED 2026-06-06

Finding: `POST /auth/admin/invalidate-sessions` accepted any authenticated caller and
relied on an in-handler `is_admin` check — but this check only applied to JWT users.
An API key (which lacks `is_admin`) would fail the check, but the gate was inconsistent
with the rest of the platform's auth model.

Fix: Introduced two distinct admin guards in `AINDY/services/auth_service.py`:

1. `require_platform_admin_access` (REVERTED to original semantics):
   - API keys: always pass (scope enforcement per-endpoint)
   - JWT: requires `is_admin=True`
   - Used on `/platform` router boundary — over-tightening this broke API key access
     to all platform endpoints (PR #47 hotfix reverted the V6a change)

2. `require_admin_principal` (NEW — strict guard):
   - API keys: requires `platform.admin` scope
   - JWT: requires `is_admin=True`
   - Used only on `admin_invalidate_sessions` and similarly privileged endpoints

`AINDY/routes/auth_router.py`: `admin_invalidate_sessions` dependency changed from
`Depends(get_current_user)` (+ manual in-handler guard) to `Depends(require_admin_principal)`.
Manual in-handler scope check and `is_admin` check removed.
`TECH_DEBT.md`: AUTH-V6 entry (closed).

---

## V2/V3 — API key scope enforcement not wired on any platform endpoint
STATUS: CLOSED 2026-06-06 (scope guard wired; V3 parallel auth system deferred)

Finding: `Scopes` constants and `require_scope()` in `api_key_auth.py` were fully
implemented but called by no route handler. API keys with any scope (or no scope)
could call any `/platform` endpoint without restriction.

Fix: Introduced `enforce_api_key_scope(scope: str)` dependency factory in
`AINDY/services/auth_service.py`. Uses `Depends(get_current_user)` internally — FastAPI
dependency caching means no second DB lookup occurs when the route also declares
`current_user: dict = Depends(get_current_user)`.

- JWT users: always pass (full trust at platform boundary)
- API key users: must have the required scope OR `platform.admin`

Wired on:
- `AINDY/routes/platform/flows_router.py`:
  - `GET /platform/flows` → `Scopes.FLOW_READ`
  - `GET /platform/flows/{name}` → `Scopes.FLOW_READ`
  - `POST /platform/flows/{name}/run` → `Scopes.FLOW_EXECUTE`
- `AINDY/routes/platform/platform_ops_router.py`:
  - `GET /platform/memory` → `Scopes.MEMORY_READ`
  - `GET /platform/memory/tree` → `Scopes.MEMORY_READ`
  - `GET /platform/memory/trace` → `Scopes.MEMORY_READ`
  - `POST /platform/syscall` → domain-level enforcement inline (see below)

`POST /platform/syscall` inline domain enforcement:
Maps syscall name prefix to required scope for API key callers:
- `sys.v1.memory.*` → `memory.write`
- `sys.v1.flow.*` → `flow.execute`
- `sys.v1.agent.*` → `agent.run`
- `sys.v1.webhook.*` → `webhook.manage`
`platform.admin` scope bypasses all domain checks.

Deferred: `get_authenticated_principal` / `require_scope()` parallel auth system in
`api_key_auth.py` — fully built but not integrated; deferred (V3 parallel system gap).
`TECH_DEBT.md`: AUTH-V2V3 entry (scope enforcement wired, V3 deferred).

---
# TIER 3 STRUCTURAL CLEANUP — ADDRESSED 2026-06-07

PR: #48 (fix/tier3-structural)
Tests: `tests/unit/test_tier3_structural.py` — 13 tests, all green

---

## Item 8 — MemoryIngestQueue silent drops (no observable signal on loss)
STATUS: CLOSED 2026-06-07

Finding: `MemoryIngestQueue.enqueue()` incremented `_dropped_total` and a Prometheus
counter on queue-full and not-accepting conditions but emitted no log message. Silent
drops in production left no trace in logs — only the Prometheus counter (if scraped)
signaled the loss.

Fix: Added `logger.warning(...)` in both drop paths in `AINDY/memory/ingest_queue.py`:
- Not-accepting path: `"[MemoryIngestQueue] not accepting (stopped or not started); dropped write (total_dropped=%d)"`
- Queue-full path: `"[MemoryIngestQueue] queue full (depth=%d capacity=%d); dropped write (total_dropped=%d)"`

Both messages include the running `total_dropped` count.
`TECH_DEBT.md`: TIER3-8 entry (closed).

---

## Item 9 — db.flush() in _persist_system_event pushed all pending ORM objects
STATUS: CLOSED 2026-06-07

Finding: `AINDY/core/system_event_service.py` called bare `db.flush()` after adding a
SystemEvent to the session. SQLAlchemy's bare flush pushes ALL pending identity map
objects — meaning any uncommitted handler changes already in the session (e.g., an
executing FlowRun state update) would be flushed to the DB as a side effect of event
emission, before the handler's own commit/rollback decision.

Fix: Changed `db.flush()` → `db.flush([event])` at line 107. SQLAlchemy's
selective flush accepts a list of objects and flushes only those instances.
```python
db.flush([event])  # flush only this object — avoids committing pending handler changes as a side effect
```
`TECH_DEBT.md`: TIER3-9 entry (closed).

---

## Item 10 — async_job_service / AutonomousController tight coupling
STATUS: DEFERRED (architectural — no bounded fix this session)

Finding: `async_job_service.py` directly imports from and calls into `AutonomousController`,
mixing job orchestration with agent-policy evaluation in a single module. No clear seam
for extraction without a larger refactor of the job orchestration path.

Deferred: The fix would require extracting `evaluate_live_trigger()` and the job
orchestration responsibilities into separate concerns. No bounded change available
without risk of regression. Tracked as `TIER3-10` in `TECH_DEBT.md`.
`TECH_DEBT.md`: TIER3-10 entry (open).
