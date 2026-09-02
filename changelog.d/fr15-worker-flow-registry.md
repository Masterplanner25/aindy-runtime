### Fixed — a flow resume in a worker process would have been acknowledged and lost (`FR-15`)

- **The worker never populated `FLOW_REGISTRY`.** `load_plugins()` only *collects* flow
  registrations; `register_flows()` invokes them, and only the invoke fills the registry. The
  API does both at startup; the worker entrypoint did only the first. Harmless while a worker
  ran jobs, and not harmless now that it also rebuilds flow resumes. The worker now registers
  flows.
- **And the rebuild now refuses a flow this process does not hold**, rather than returning a
  callable that does nothing. The built callback checks the registry itself and, finding
  nothing, logs a warning and returns *normally* — correct for the restart sweep, where a flow
  that is not ours is legitimately skipped, and catastrophic for a caller that has already
  given up the closure: it sees a clean return, acknowledges the message, and reports the
  resume completed while the run stays `waiting` forever.
- The refusal surfaces as a dead-lettered message instead. **Both fixes are needed** — the
  first makes resumes work, the second makes their failure visible if a deployment ever gets
  the first wrong again.
- Found while preparing to lift the distributed refusal, which is **not** taken in this entry.
  It would have been the wrong call: with an empty registry, flipping would have stranded every
  flow resume in production, silently, which is worse than the starvation it fixes.
