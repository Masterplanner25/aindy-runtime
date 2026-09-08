### Fixed — the DeepSeek client did not meter token usage (`COST-GOVERNOR-1` phase 0, #597)

- `DeepSeekLLMClient.chat_completion_response` returned the provider response without calling
  `observe_llm_usage`, so `aindy_llm_tokens_total` recorded **nothing** for DeepSeek. Phase 0
  (#564, in v2.9.0) metered the raw response path for anthropic, openai and azure_openai and
  missed the fourth client. Nothing routes through it today, so no spend was lost from a bill —
  but any adoption of that provider would have measured zero, which is the precise failure
  phase 0 existed to prevent.
- **The guard that should have caught it existed and was not wrong — its census was.**
  `test_every_provider_client_meters_its_response` parsed the AST rather than matching strings
  (deliberately, per `CLAUDE.md`), but iterated **three file paths written by hand**. A test
  named *every* meant *these three*. The census is now derived from the source: any
  `AINDY/platform_layer/*_client.py` defining `messages_create` or `chat_completion_response`
  must meter exactly once, so a fifth provider is covered on arrival rather than on remembering.
- A liveness assertion pins the discovery at four or more clients and names `deepseek`
  explicitly, so a census that silently stops matching the layout fails instead of passing
  vacuously — the empty-set-satisfies-everything shape catalogued in `CLAUDE.md`.
- Mutation-verified 3/3: deleting the deepseek meter fails two guards, metering in `chat()` as
  well fails the exactly-once guard, and breaking discovery fails the liveness guard.
- `docs/runtime/LLM_SEAM_ADOPTION_SCOPE.md` §4 and §7 corrected — they described phase 0 as
  outstanding work when the same PR that wrote them (#564) had already done it, which made the
  seam look blocked when it was not.
