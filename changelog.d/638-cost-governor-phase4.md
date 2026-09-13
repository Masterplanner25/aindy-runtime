### Added — the LLM token governor: reserve → call → reconcile, refusing on breach (`COST-GOVERNOR-1` phase 4, #638)

**Opt-in.** Two new ceilings, both default `0` = unlimited, so nothing changes until an operator sets them:

- `AINDY_QUOTA_MAX_TOKENS` — tokens per execution (the attributed agent run, else the bound execution unit).
- `AINDY_QUOTA_MAX_TENANT_TOKENS` — tokens per tenant window (the window is `TENANT_KEY_TTL_SECONDS`, 24 h rolling).
- `AINDY_LLM_BUDGET_DEFAULT_RESERVE` (2048) — completion tokens reserved when a call passes no `max_tokens`.

How it works: at the LLM seam (`CircuitBreakerLLMClient`, every `chat()`/`call_method()`), **outside the circuit breaker**, an estimate (`max_tokens` + a 4-chars-per-token prompt guess) is reserved atomically against the caller's budgets — INCRBY-then-compare on Redis, one critical section in memory — so N concurrent callers cannot all pass. The meter records the actual inside the call; the estimate is released after. A refusal raises `LLMBudgetExceededError` (an `LLMCallError`, message prefixed `RESOURCE_LIMIT_EXCEEDED`) **before** the provider is called, and never counts toward opening the circuit. Store failures follow the runtime's existing policy: admit in dev/test (counted), refuse in prod.

- New metric `aindy_llm_budget_outcomes_total{scope=execution|tenant, outcome=reserved|refused|degraded}`.
- `POST /apps/agent/run` (and any consumer of `create_agent_run_runtime`) answers a budget refusal with **429** and the reason, not a generic 500 — including when an app planner rewraps the seam's error (`find_budget_refusal` walks `__cause__`).
- Only token-spending calls are governed: `token_meter.METERED_METHODS` (`chat`, `messages_create`, `chat_completion_response`). Embeddings ride the same seam and are neither metered nor reserved.
- `ResourceManager.reserve_tokens` / `release_tokens` / `reserve_tenant_tokens` / `release_tenant_tokens`; `get_tenant_summary()["quota_limits"]` reports both ceilings.

Sizing note: the reservation is the caller's own `max_tokens`, so a window smaller than one reservation admits nothing. Set the tenant window against the planner's `max_tokens` (4096 in the app) times the calls you mean to allow, not against typical actuals.
