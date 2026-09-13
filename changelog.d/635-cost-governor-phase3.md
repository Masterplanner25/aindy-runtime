### Added — LLM tokens become a resource dimension with a subject (`COST-GOVERNOR-1` phase 3, #635)

- `token_meter.observe_llm_usage` now also accrues each call's tokens into `ResourceManager`:
  onto the attributed **run**, else the **bound execution unit** (`UsageSnapshot.tokens`;
  Redis `aindy:rm:eu:{id}:tokens`), and onto a **per-tenant rolling window**
  (`record_tenant_tokens` / `get_tenant_tokens`; Redis `aindy:rm:tenant:{id}:tokens`,
  `TENANT_KEY_TTL_SECONDS`). `get_usage()` and `get_tenant_summary()` report them
  (`tokens`, `total_tokens`, `window_tokens`). **Nothing is enforced** — the ceiling is phase 4.
- New metric **`aindy_llm_calls_total{provider, attributed}`**, `attributed ∈ run|unit|tenant|none`.
  The `none` fraction is the set of call sites no budget could reach; unattributed calls are
  allowed and counted, never refused (`INITIATOR-IDENTITY-1`).
- `token_meter.llm_attribution_scope(tenant_id=, run_id=)` declares who a span's LLM calls
  belong to. The runtime sets it in `generate_plan` (tenant — planning runs before the
  `AgentRun` row exists, so a per-run budget cannot cover it) and in `execute_run` (tenant +
  run). An app-registered planner backend runs inside that span, so no app change is needed.
- An agent run's total tokens land on its `SCORE_COMPUTED` record as `dimensions.llm_tokens`.
- `ResourceManager.observed_unit(tenant, eu)` — a snapshot with a subject, purged on exit,
  **without** admission or `mark_started`: wrapping a run in the admitting scope would make it
  one 100-syscall unit by side effect, which is a separate decision (`EXEC-ENV-BIND-1` ph4).
- Known boundaries: a call made inside the nodus worker subprocess, or from a thread outside
  any span, lands as `attributed="none"` — that is the metric doing its job, not a bug.
