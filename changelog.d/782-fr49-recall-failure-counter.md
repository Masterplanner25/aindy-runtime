### Added — memory recall failures are counted by caller and stage (FR-49, DEC-083, #782)

- New counter `aindy_memory_recall_failures_total{site, stage}`. `stage="recall"` means the recall
  failed and returned an empty context. `stage="own_session"` means
  `AINDY_MEMORY_RECALL_OWN_SESSION` could not open its own session and fell back to the caller's
  (the recall still ran). `stage="setup"` means the request pipeline failed before the recall
  started. **Why:** a recall failure's only witness was a WARNING log line, and a container
  recreate loses it. That made the soak evidence for flipping `AINDY_MEMORY_RECALL_OWN_SESSION`
  unreadable after the fact.
- `MemoryOrchestrator.get_context()` takes an optional `site=` so an app's own call sites can label
  themselves; unlabelled calls count as `site="unspecified"`.
- The pipeline's pre-recall failure is logged at WARNING (it was DEBUG).
