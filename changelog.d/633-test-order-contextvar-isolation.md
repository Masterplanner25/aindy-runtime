### Changed/Fixed — the unit sweep now fails a test that leaks a runtime ContextVar; two production leaks it found are fixed (`TEST-ORDER-CONTEXTVAR-1`, `TEST-ORDER-REGISTRY-1`, #633)

Test/CI change that alters what a green `Runtime Contracts` means, plus two small runtime fixes the new check surfaced.

- **What green used to hide:** pytest runs every test in one `contextvars` context, and
  `tests/unit/test_contextvar_thread_propagation.py` set `pipeline_active=True`, a trace id and
  the dispatcher's `_EU_ID_CTX` without resetting them. Everything alphabetically after it —
  roughly 180 of 221 unit files — ran with the ExecutionContract gate vacuously satisfied and
  stale trace/unit ids. A second leaker (`test_mcp_server.py`, effect attribution left at
  `tenant-7`) surfaced the moment the guard existed.
- **What green means now:** `tests/unit/conftest.py` snapshots every ContextVar the runtime has
  imported (a derived census, asserted non-empty), and after each test restores what changed and
  fails the test that changed it, naming the vars. Victims stay clean; the error lands on the
  leaker. The guard's own liveness is proven by `tests/unit/test_contextvar_isolation_guard.py`
  (a real pytest subprocess against a generated leaker + victim; mutation-tested 3/3).
- **Two production leaks the guard found, fixed here (`Fixed`):** `_execute_job_inline` left
  the trace and parent-event ContextVars set on its early-return paths (harmless in production —
  each job runs in a `copy_context()`); `PersistentFlowRunner.start()` established a trace id via
  `ensure_trace_id` and never released it, so on a scheduler thread with no ambient trace the
  first flow pinned that thread's `trace_id` for every later flow — unrelated runs sharing a
  trace id. `start()` now takes a token for the trace it establishes and releases it.
- **`TEST-ORDER-REGISTRY-1` closed by measurement, not by a fix:** its reproduction passes 0/6
  at the commit that filed it, 0/13 across four orderings on `main`, and the registry-isolation
  fixture it prescribed has existed since the repo's first commit. Its symptom ("an EMPTY
  capability set") is `FLAKY-1`'s subprocess-provider signature, read as ordering from one sample.
- Not adopted, by decision: randomised test order. It detects without attributing, and would
  convert every latent order-dependence into an unattributed CI flake on day one.
