---
title: "Error Handling Policy (runtime-owned)"
last_verified: "2026-07-17"
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
- Exceptions are handled per-route; there is no centralized runtime error formatter, so an
  unhandled exception propagates to FastAPI and returns a default 500.
- `AINDY/routes/db_verify_router.py`: minimal explicit error handling (no broad
  `try/except`); relies on FastAPI default behavior on failure.
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
- Structured logging is not centralized; runtime modules use the stdlib `logging` module
  (not `print`) for operational and error messages.
- Stack-trace exposure in API responses is not explicitly controlled at the code level.

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

- The syscall envelope (`{status, data, trace_id, duration_ms, error}`) satisfies this for
  the dispatch surface; unhandled route exceptions still return FastAPI defaults (no
  centralized formatter — see Known Gaps).

## 6. Known Gaps (runtime-scoped)
- Inconsistent error handling across `AINDY/routes/*`: no centralized error-response
  formatter, so unhandled exceptions return FastAPI defaults rather than the §5 contract.
- No centralized error response formatter for HTTP routes (the syscall envelope covers only
  the dispatch surface).
- Structured logging is not centralized; no stack-trace suppression is enforced in
  production API responses at the code level.

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
