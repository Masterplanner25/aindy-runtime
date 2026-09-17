### Fixed — on the `nodus_vm` backend, a tool step's LLM usage now reaches `/metrics`, the tenant window, and the run (FR-35, #712)

- A tool that calls an LLM inside the Nodus worker was metered in the **worker's** registry —
  never scraped — and attributed to nothing; the app measured 1,965 DeepSeek tokens spent and
  every API-side reading zero, so the cost governor saw planning only. The worker now appends
  each call to a ledger (`llm_usage`, a fourth deferred collection on the worker reply beside
  `memory_writes` / `emitted_events` / `simulated_effects`) and **counts nothing itself**; the
  API process records the ledger under the reply's explicit subject (`run_id` /
  `execution_unit_id` / `user_id`). `aindy_llm_tokens_total`, `aindy_llm_calls_total
  {attributed="run"}`, the tenant window and `score.computed.dimensions.llm_tokens` read the
  spend on this backend for the first time.
- The `chat {model}` span (#706) the worker could never export is replayed by the parent with
  the worker's timestamps, marked `aindy.deferred`, under the current span.
- The governor's *reserve* runs in the worker under the forwarded attribution scope; it is real
  only with a Redis-backed resource manager (in-memory: the worker's window is empty — stated).
  `LLMBudgetExceededError` now declares `failure_class: "transient"`.
- New `AINDY_NODUS_LLM_LEDGER_MAX` (default 256): per-call records carried; the rest aggregated
  per (provider, model).
- **Also fixed, found building this:** the worker seam had dropped `failure_class` and
  `cancelled` from every tool result, so on `nodus_vm` a cancelled / refused / budget-exceeded
  step was still substring-classified in the compiled plan's guard (`RETRY-CLASSIFY-1`).
- Decisions recorded: DEC-040 … DEC-045. Design: `docs/design/FR35_GUEST_LLM_USAGE_DESIGN.md`.
