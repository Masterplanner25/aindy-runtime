---
title: "Error Handling Policy"
last_verified: "2026-06-27"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Error Handling Policy

This document distinguishes current behavior from required policy rules. It does not redesign the system and does not assume unimplemented mechanisms. Undefined behavior is explicitly marked.

> **Post-split note (2026-06-27):** Relocated into `aindy-runtime` from the
> pre-split monolith archive. The **Policy Rules** subsections (B) are the
> normative, repo-agnostic governance. The **Current Implementation**
> subsections (A) are historical snapshots of monolith behavior at authoring
> time; the module paths in them have been updated for the runtime/apps split.
> Paths under `AINDY/...` are runtime-owned in this repo; paths under
> `apps/...` are app-owned and now live in the **aindy-apps-monolith** repo.
> Most of the routers and services observed below are app-owned — this policy
> originated as a combined-monolith audit and is a candidate for a later
> runtime-only / app-only editorial split (tracked in TECH_DEBT.md).

## 1. HTTP Error Classification (4xx vs 5xx)

### A. Current Implementation
- Exceptions are handled inconsistently across routes.
- `HTTPException` is used for client errors and some server errors:
- `apps/masterplan/routes/genesis_router.py` _(app-owned)_: raises `HTTPException` for missing parameters, not found sessions, and error conditions during masterplan lock.
- `apps/arm/routes/arm_router.py` _(app-owned)_: wraps service calls in `try/except` and raises `HTTPException(status_code=500, ...)` on any exception.
- `apps/bridge/routes/bridge_router.py` _(app-owned)_: raises `HTTPException(status_code=403, ...)` on permission failures.
- `apps/social/routes/social_router.py` _(app-owned)_: raises `HTTPException(status_code=404, ...)` when profile not found.
- Many routes do not wrap exceptions; unhandled exceptions will propagate to FastAPI and return 500 responses with default behavior.
- Routes with minimal or no explicit error handling (no `try/except` and limited `HTTPException` usage) include:
- `apps/authorship/routes/authorship_router.py` _(app-owned)_
- `apps/dashboard/routes/dashboard_router.py` _(app-owned)_
- `AINDY/routes/db_verify_router.py`
- `apps/dashboard/routes/health_dashboard_router.py` _(app-owned)_
- `apps/network_bridge/routes/network_bridge_router.py` _(app-owned)_
- `apps/rippletrace/routes/rippletrace_router.py` _(app-owned)_
- `apps/search/routes/seo_routes.py` _(app-owned)_
- `apps/tasks/routes/task_router.py` _(app-owned)_
- Behavior for unhandled exceptions: Not explicitly defined in current implementation; FastAPI default behavior returns 500.

### B. Policy Rules
- 4xx errors are used for client/input/auth/validation issues.
- 5xx errors are used for server, database, model provider, and unexpected failures.
- Backend must not return HTML error pages for API routes.
- API responses for errors must be JSON with a consistent error structure.

## 2. Model Provider Failure Handling

### A. Current Implementation
- `apps/masterplan/services/genesis_ai.py` _(app-owned)_:
  - `call_genesis_llm()`: Expects JSON from `response.choices[0].message.content` and uses `json.loads`. On JSON parsing failure, returns a fixed fallback dict: `{"reply": "I need a bit more clarity. Can you elaborate?", "state_update": {}, "synthesis_ready": False}`. No retry logic. No explicit timeout.
  - `call_genesis_synthesis_llm()`: Uses `response_format={"type": "json_object"}`. On JSON parse failure, returns a minimal valid structure. No retry logic. No explicit timeout.
  - ✅ **`validate_draft_integrity()` (added 2026-03-17 Genesis Block 4):** Implements 3-attempt retry loop with `for attempt in range(retry_limit)`. On all-retry failure, returns a structured fail-safe dict with `audit_passed=False` and a `confidence_concern` finding. Uses `response_format={"type": "json_object"}`.
- ARM DeepSeek service _(app-owned, aindy-apps-monolith)_ — the pre-split `deepseek_arm_service.py` was refactored into the `apps/arm/services/deepseek/` package (analyzer: `deepseek_code_analyzer.py`), wired via `apps/arm/bootstrap.py` and `apps/arm/syscalls.py`:
  - Calls DeepSeek analyzer functions without wrapping exceptions.
  - Exceptions are handled at the route level in `apps/arm/routes/arm_router.py` _(app-owned)_, which returns HTTP 500.
  - No retry logic. No explicit timeout.
  - ✅ **`DeepSeekCodeAnalyzer._call_openai()` (ARM Phase 1, 2026-03-17):** Implements retry with configurable `retry_limit` and `retry_delay_seconds`.

### B. Policy Rules
- Model failures must not crash the application process.
- Model provider errors must surface as 5xx unless explicitly client-caused.
- Fallback responses must be clearly structured and machine-parseable.

## 3. Database Transaction Handling

### A. Current Implementation
- Per-request sessions are created in `AINDY/db/database.py:get_db()` and closed in `finally`.
- Many services explicitly `commit()` after writes.
- `AINDY/memory/memory_persistence.py` performs `rollback()` on SQLAlchemy errors before re-raising.
- Many routes and services do not explicitly rollback on exceptions.
- ✅ **`apps/masterplan/services/masterplan_factory.py: create_masterplan_from_genesis()` _(app-owned)_ (Genesis Block 5, 2026-03-17):** All DB mutations (masterplan insert + session status freeze + commit) wrapped in try/except with `db.rollback()` in the except clause before re-raise. Atomic unit.
- Background loops in `apps/tasks/services/task_service.py` _(app-owned; renamed from `task_services.py`)_ create a new `SessionLocal()` per iteration and close it in `finally`.
- DB session created in `AINDY/main.py` startup event is not explicitly closed.

### B. Policy Rules
- Any exception during DB mutation must trigger `rollback()`.
- Sessions must always close after use.
- No cross-thread session sharing.
- Background loops must isolate failures per iteration and always close sessions.

## 4. Logging and Severity Mapping

### A. Current Implementation
- Logging module configured in `AINDY/config.py` with file and stream handlers.
- `AINDY/main.py` logs requests and responses in middleware.
- Many modules use `print(...)` for operational and error messages (e.g., `apps/tasks/services/task_service.py`, `apps/network_bridge/routes/network_bridge_router.py`, `apps/social/routes/social_router.py` — all app-owned).
- Structured logging is not implemented.
- Stack trace exposure in API responses is not explicitly controlled in the code.

### B. Policy Rules
- Severity levels:
- `DEBUG`: internal tracing.
- `INFO`: lifecycle events and expected transitions.
- `WARNING`: recoverable anomalies.
- `ERROR`: failed operations.
- `CRITICAL`: invariant violation or unsafe state.
- Do not expose stack traces in production API responses.

## 5. Error Response Contract

Policy requirement for API errors (even if not fully implemented):

```json
{
  "error": "<error_code>",
  "message": "<human-readable summary>",
  "details": "<optional structured detail>"
}
```

- Current implementation uses this shape for core routes, but unhandled exceptions still return FastAPI defaults.

## 6. Known Gaps
- Inconsistent error handling across routes (`apps/*/routes/*` _(app-owned)_ and `AINDY/routes/*`).
- ✅ **PARTIALLY RESOLVED (2026-03-17):** Retry logic added to `validate_draft_integrity()` (3-attempt) and `DeepSeekCodeAnalyzer._call_openai()` (configurable). Still missing in `call_genesis_llm()`, `call_genesis_synthesis_llm()`.
- No explicit timeout handling for external model calls (all three OpenAI service functions).
- Mixed use of `print(...)` and logging; no structured logging.
- No centralized error response formatter; default FastAPI error handling is used.
- DB session created in `AINDY/main.py` startup is not explicitly closed.
- ✅ **PARTIALLY RESOLVED (2026-03-17):** `create_masterplan_from_genesis()` now has atomic rollback. Most other services/routes still lack explicit rollback.
- Health check uses `/tools/seo/*` endpoints in `AINDY/routes/health_router.py`, which are not defined in `apps/search/routes/seo_routes.py` _(app-owned)_. This can produce failing endpoint checks and degraded health classification unrelated to actual service health.
