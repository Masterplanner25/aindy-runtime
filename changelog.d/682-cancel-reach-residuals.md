### Fixed — a cancelled agent run is refused at the syscall dispatcher, and its isolated tool worker is killed (`CANCEL-REACH-1`, #682)

- **Second chokepoint:** a syscall dispatched inside a cancelled run's execution span is now
  refused before its handler runs (`aindy_run_cancel_observed_total{surface="syscall"}`). The
  run identity is read from the execution span (`llm_attribution_scope`), so nothing changes
  for routes that are not agent runs. If the syscall had reserved an `EXACTLY_ONCE` effect
  record, the refusal completes it `failed` rather than leaving it `pending`.
- **Isolated tools (`register_tool(isolation=…)`):** a cancelled run's isolated tool is no longer
  spawned at all — the isolated branch used to return before the cancel check — and one that
  is already running is **terminated and killed** when the run is cancelled (polled every 0.5 s;
  the predicate's 2 s TTL bounds database reads), reported as
  `{surface="tool_worker"}`. Before this the worker died only to its 120 s budget; the claim
  that the path was "hard-killable by its isolation class" described a mechanism nothing
  invoked.
- The cancel check on the in-process tool path now also finalizes a reserved idempotent effect
  record `failed` on refusal (it returned without doing so).
- Unchanged: cancellation is still cooperative for an in-process tool already executing, still
  fails open on an unreadable status, and still never queries per effect.
