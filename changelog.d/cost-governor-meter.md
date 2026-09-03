### Added — LLM token usage is now measured (`COST-GOVERNOR-1`, the meter half)

- Two new metrics: **`aindy_llm_tokens_total{provider, model, kind}`** (`kind` is `prompt` or
  `completion`) and **`aindy_llm_usage_unreadable_total{provider, model}`**. Recorded for the
  OpenAI, Azure OpenAI and Anthropic clients.
- **Why this was a gap and not merely an omission:** the runtime enforces a 300-second wall-clock
  ceiling and a 256 MiB memory ceiling on execution units whose dominant cost is **tokens**, which
  it did not measure at all. Four quota dimensions existed — wall time, memory, syscalls,
  concurrency — and none of them was the one that matters for an LLM runtime.
- **And the quantity was discarded, not just uncapped.** Every provider client returned
  `str(response.choices[0].message.content)`, so the usage object on the response lived for one
  stack frame and was dropped. Nothing downstream could have metered spend; there was nothing
  left to meter.
- **A response whose usage cannot be read is counted, not ignored.** Without that second counter,
  a flat token count would mean either "no calls happened" or "every call was made and none of
  its usage could be read" — states that demand opposite responses from an operator.
- **Metering can never fail a call that already succeeded.** The tokens are spent either way; an
  accounting problem must not become a user-visible error.
- **Labels stop at provider and model deliberately.** Tenant is the more useful partition for a
  governor and is omitted on purpose: a Prometheus label is a time series per distinct value, so
  a tenant label grows cardinality with the customer list. Per-tenant accounting belongs in the
  counter a governor checks — a cache, keyed and expiring — not in the observability surface.

**This is the meter, not the governor.** Nothing here refuses a call and no budget exists yet.
Admission control needs *reserve → call → reconcile*, and it needs this first: you cannot
reconcile against an actual you never recorded.
