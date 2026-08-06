---
title: "Error Handling Policy (runtime-owned)"
last_verified: "2026-08-05"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Error Handling Policy (runtime-owned)

This document distinguishes current behavior from required policy rules. It does not
redesign the system and does not assume unimplemented mechanisms. Undefined behavior is
explicitly marked.

> **Scope (editorial split, 2026-07-17 — DOCS-BUCKET-A-1 residual 2):** This is the
> **runtime-owned** Error Handling Policy for `aindy-runtime`. The **Policy Rules** in each
> section are normative and **repo-agnostic** — they bind the runtime *and* any app plugin.
> The **Runtime Implementation** notes observe runtime code (`AINDY/...`) only. This doc
> originated as a combined-monolith audit; the app-domain implementation observations
> (`apps/...` routers/services — genesis, ARM, social, bridge, dashboard, authorship,
> network_bridge, rippletrace, search/seo, tasks) have been split out to the app repo — see
> [App-owned implementation](#app-owned-implementation-aindy-apps-monolith). The Policy Rules
> here remain the normative upstream those app surfaces must satisfy.

## 1. HTTP Error Classification (4xx vs 5xx)

### A. Runtime Implementation
- **There is a centralized error formatter.** `AINDY/exception_handlers.py`
  `register_exception_handlers(app)` is called unconditionally at `AINDY/main.py:81`, and
  registers nine handlers including a catch-all `app.add_exception_handler(Exception, ...)`.
  Runtime-only mode is covered too: `AINDY/runtime_only.py` serves the same `AINDY.main:app`
  object. *(Corrected 2026-08-05 — this section previously said no such formatter existed and
  that unhandled exceptions returned FastAPI defaults. Both were false.)*
- Typed handlers map infrastructure failures to deliberate status codes rather than a generic
  500: `QueueSaturatedError`, `MongoUnavailableError`, `CircuitOpenError`,
  `SATimeoutError` (pool exhaustion), `SAOperationalError` (database unavailable),
  `RequestValidationError`, `RateLimitExceeded`, and `HTTPException`.
- Individual routes may still handle their own errors; `AINDY/routes/db_verify_router.py`
  carries no broad `try/except` and relies on the app-level handlers above.
- `SyscallDispatcher.dispatch()` (`AINDY/kernel/syscall_dispatcher.py`) additionally wraps
  every handler in a uniform `{status, data, trace_id, duration_ms, error}` envelope, with
  explicit typed re-raises (e.g. `SyscallContractViolation`) placed *before* its
  belt-and-suspenders `except Exception`.
- `SyscallDispatcher.dispatch()` (`AINDY/kernel/syscall_dispatcher.py`) is the exception:
  it wraps every handler in a uniform `{status, data, trace_id, duration_ms, error}`
  envelope, with explicit typed re-raises (e.g. `SyscallContractViolation`) placed *before*
  its belt-and-suspenders `except Exception`.

### B. Policy Rules
- 4xx errors are used for client/input/auth/validation issues.
- 5xx errors are used for server, database, model provider, and unexpected failures.
- Backend must not return HTML error pages for API routes.
- API responses for errors must be JSON with a consistent error structure.

## 2. Model Provider Failure Handling

### A. Runtime Implementation
- The runtime's LLM boundary is `AINDY/platform_layer/llm_client.py`. Provider failures are
  contained, not fatal: `FallbackLLMClient` + `get_llm_client_chain()` /
  `resolve_provider_chain()` (config-driven via `LLM_PROVIDER` + `LLM_FALLBACK_PROVIDERS`,
  AGENT-HARDEN-5) fail an open-breaker primary over to a secondary provider.
- `CircuitBreaker` (`AINDY/kernel/circuit_breaker.py`) trips a provider after
  `failure_threshold` consecutive failures and fails fast (`CircuitOpenError`) until the
  recovery timeout elapses, so a degraded provider cannot stall the process.
- Runtime callers surface provider errors as 5xx envelopes; there is no runtime code path
  that crashes the process on a model error. (App-domain model orchestration — Genesis /
  ARM — is app-owned; see the app companion.)

### B. Policy Rules
- Model failures must not crash the application process.
- Model provider errors must surface as 5xx unless explicitly client-caused.
- Fallback responses must be clearly structured and machine-parseable.

## 3. Database Transaction Handling

### A. Runtime Implementation
- Per-request sessions are created in `AINDY/db/database.py:get_db()` and closed in `finally`.
- `AINDY/memory/memory_persistence.py` performs `rollback()` on SQLAlchemy errors before
  re-raising.
- Scheduler jobs (`AINDY/platform_layer/scheduler_service.py`) follow the documented pattern:
  open `SessionLocal()` inside `try`, `commit()` + `close()` inside `try`, `logger.error` on
  failure — each job isolates its own failure.
- EffectRecord resolution/completion uses `db.commit()` (not `flush()`) so effect state is
  durable across session close (`AINDY/kernel/syscall_dispatcher.py`).

### B. Policy Rules
- Any exception during DB mutation must trigger `rollback()`.
- Sessions must always close after use.
- No cross-thread session sharing.
- Background loops must isolate failures per iteration and always close sessions.

## 4. Logging and Severity Mapping

### A. Runtime Implementation
- Logging is configured in `AINDY/config.py`; `_build_log_handler` guards file-handler setup
  with `except OSError` so a read-only subprocess cwd (site-packages) never crashes logging.
- `AINDY/main.py` logs requests and responses in middleware.
- **Structured logging is centralized.** `AINDY/platform_layer/log_config.py`
  `configure_logging()` sets up the root logger, defaulting to JSON output in production
  (`LOG_FORMAT=json` forces it in any environment). Runtime modules use the stdlib `logging`
  module, never `print`.
- **Stack-trace exposure is controlled.** `unhandled_exception_handler` logs the exception
  server-side, then returns a fixed
  `{"error": "internal_error", "message": "Internal server error", "details": null}` —
  the exception text never reaches the client. *(Corrected 2026-08-05: this section
  previously said neither was controlled at the code level.)*

### B. Policy Rules
- Severity levels:
  - `DEBUG`: internal tracing.
  - `INFO`: lifecycle events and expected transitions.
  - `WARNING`: recoverable anomalies.
  - `ERROR`: failed operations.
  - `CRITICAL`: invariant violation or unsafe state.
- Do not expose stack traces in production API responses.

## 5. Error Response Contract

Required shape for API errors — **implemented and enforced app-wide** by the handlers in
`AINDY/exception_handlers.py`:

```json
{
  "error": "<error_code>",
  "message": "<human-readable summary>",
  "details": "<optional structured detail>"
}
```

Observed live against a running server (unauthenticated request to a protected route):

```json
{"error": "http_error", "message": "Authentication required", "details": null}
```

- Every registered handler emits this shape, including the catch-all for otherwise-unhandled
  exceptions. A route that raises without its own `try/except` still produces the contract.
- The syscall envelope (`{status, data, trace_id, duration_ms, error}`) is a *different*,
  richer shape covering the dispatch surface. Both are intentional: the envelope carries
  execution metadata a caller needs (trace id, duration), the error contract is the HTTP
  boundary. Do not collapse one into the other.

## 6. Known Gaps (runtime-scoped)

**All three previously-listed gaps are closed** (verified against source 2026-08-05). They
are retained here, struck through, because a "Known Gaps" list that silently loses entries
is indistinguishable from one that was never re-checked — and because a reader who saw the
old list might otherwise set out to fix something already fixed.

- ~~Inconsistent error handling across `AINDY/routes/*`: no centralized error-response
  formatter, so unhandled exceptions return FastAPI defaults rather than the §5 contract.~~
  **Closed** — `register_exception_handlers` at `AINDY/main.py:81`, catch-all included.
- ~~No centralized error response formatter for HTTP routes.~~ **Closed** — same mechanism;
  see §5 for the live-observed output.
- ~~Structured logging is not centralized; no stack-trace suppression is enforced.~~
  **Closed on both counts** — `log_config.configure_logging()` (JSON by default in
  production) and `unhandled_exception_handler`'s fixed generic response body.

No runtime-scoped gaps are currently open in this area. If you find one, add it here rather
than to a commit message.

## App-owned implementation (aindy-apps-monolith)

This policy began as a combined-monolith audit. The app-domain **Current Implementation**
observations it originally carried — Genesis (masterplan) routes/services (`genesis_router`,
`genesis_ai`, `masterplan_factory`), ARM DeepSeek (`apps/arm/services/deepseek/`), and the
`social`, `bridge`, `dashboard`, `authorship`, `network_bridge`, `rippletrace`,
`search/seo`, and `tasks` surfaces — are **app-owned** and were split out on 2026-07-17.
They are not reproduced here.

- The **Policy Rules** in §§1–5 above are the normative upstream those app surfaces must
  satisfy; the app repo should reference them rather than restate them.
- The full pre-split observations remain in this file's **git history** and the pre-split
  archive; the app team owns authoring an app-side error-handling implementation companion in
  `aindy-apps-monolith` (`DOCS-MIGRATION-2` on the app board).
