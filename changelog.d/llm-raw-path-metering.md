### Fixed — the LLM path a structured caller actually uses was not metered (`COST-GOVERNOR-1`)

- `observe_llm_usage` was wired into `chat()` only. `chat()` returns **a string**, so a caller
  needing tool blocks or the raw response cannot use it — and the raw methods
  (`messages_create`, `chat_completion_response`) were unmetered. The meter therefore covered the
  path a real consumer cannot take.
- Metering now sits on the raw response path in each client, which `chat()` delegates to. The
  meter in `chat()` was **removed at the same time**: leaving both would double-count every chat
  call, and a number that is silently 2× is a fabricated measurement — worse than a gap, and the
  one failure this meter's design rejects outright. A governor reserving against a doubled meter
  would refuse calls that were within budget.
- Pinned two ways: a `chat()` call is asserted to record exactly its prompt tokens, and each
  client is asserted over its AST to hold exactly one metering call site — two means the
  double-count, zero means an unmetered provider.

### Added — scope for routing a real consumer through the LLM seam

`docs/runtime/LLM_SEAM_ADOPTION_SCOPE.md`. **Read it before building `COST-GOVERNOR-1`'s
governor half.** The seam has no consumer: nothing in the runtime outside `platform_layer` imports
an LLM client, and every real call in the ecosystem constructs its own SDK client directly. A
budget enforcer built there would refuse zero calls while passing every test written for it.

The scope records why the obvious integration does not work (the seam is text-shaped; its one real
candidate needs structured tool output), the path that does, the diagnosability regression it
trades for, and what the governor still needs beyond adoption.
