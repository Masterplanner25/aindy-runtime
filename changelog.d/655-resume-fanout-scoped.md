### Fixed — `POST /platform/flows/runs/{id}/resume` resumes that run only (`RESUME-FANOUT-UNSCOPED-1`) (#655)

The per-run resume route checked that the named run belonged to the caller and was waiting on
the event, then injected the payload into — and woke — **every** run parked on that event name,
any tenant (`route_event` peeked all waits by event type with no run or tenant filter). Observed
live as `results: [{run_id: <B>…}, {run_id: <A>…}]`. Event names are conventional strings
(`review.approved`), so collisions are the normal case. `platform.admin`-gated, so an isolation
defect an operator could trigger by using the route as documented, not an exploit.

- The wake is now scoped to the named run **end to end**: `route_event(run_id=)` injects into
  that run only; `publish_event` / `notify_event` take `run_id`, filter the local scan, carry it
  through the pre-rehydration buffer, put it on the Redis pub/sub message and pass it to the
  cross-instance fallback. `results` has exactly one entry.
- **Wire change, additive:** the event-bus message gains a `run_id` key. An instance that
  predates it ignores the key and fans out as before, for the length of a rolling deploy. No
  schema change.
- Scoped by **run id, not correlation**: a flow WAIT's correlation is the run's `trace_id`, and
  sibling runs started under one request share a trace, so correlation-only scoping would still
  fan out to them. A test pins that.
- Without `run_id`, `route_event` keeps its broadcast semantics. No runtime caller uses that
  form; a broadcast resume, if ever wanted, is a separate verb with its own scope.
- Internal: the scheduler's pre-rehydration buffer entries are now `(event_type,
  correlation_id, run_id)`.
