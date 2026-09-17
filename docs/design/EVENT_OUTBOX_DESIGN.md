---
title: "Event Outbox — Design"
api_version: "1.0"
last_verified: "2026-09-17"
status: current
owner: "platform-team"
---

# `EVENT-OUTBOX-1` — the event rides the handler's transaction — design

**DESIGN ONLY — nothing shipped. Proposal under `AGENT_WORKING_RULES.md` §8 (a change to when a
system event is written relative to the work it records).** §1 corrects the entry's premise in two
places that shrink the work; §2 answers the question the entry said to answer first; §3 is the
mechanism, which is the entry's own "cheaper fix" made precise; §5 is what not to build.

---

## 1. The finding, verified — and narrower than filed

The entry traces: `queue_system_event` appends to an in-memory bucket on the execution
context; the handler commits its own work; the pipeline flushes the bucket after the handler
returns (`signals.py:111` `_apply_event_signals`), each emit in a swallowing `try`. A crash or an
emit failure between the handler's commit and the flush loses the record of work that landed.

Two corrections, both from reading `execution_signal_helper.py` and `system_event_service.py`
at HEAD:

1. **The buffer engages only inside a request pipeline.** `queue_system_event` buffers
   `if is_pipeline_active() and ctx is not None`; otherwise it calls `emit_system_event(db=…)`
   directly. And `_persist_system_event` **writes on the caller's session** — `db.add(event)`,
   `db.flush([event])`, then `db.commit()` — so on the scheduler thread, the worker, a resume
   callback and a rehydrated flow, **the event already rides the caller's transaction**. The
   entry's "cheaper fix" is the non-pipeline path's existing behaviour. The window exists for
   route-driven work only.
2. **A lost event is a lower score, not a missed capture.** `memory_capture_engine.py:93` reads
   `get_downstream_effects` inside a significance formula (`len(downstream) + trace_depth × 0.75
   + failure_bonus`); `:499` is the same shape. A missing row lowers a number; it does not gate
   the capture. **This is an observability item, not a memory-loop item** — the question the
   entry asked to settle first, settled.

What remains is real: on every route, the `execution.*` trio, `flow.node.*`, `agent.step.*` and
`syscall.executed` events for the request are written *after* the handler, on separate commits,
under a `try` that swallows. `EVENT-OUTBOX-1`'s "better index, weaker record" holds for the
request path.

---

## 2. Why the buffer exists, and which of its reasons survive

| Reason the entry gives | Holds? |
|---|---|
| provisional IDs let a handler reference an event it has not written yet | **no longer needed for that** — the provisional id *is* a client-side `uuid4`, and `SystemEvent.id` is client-assigned (`default=uuid.uuid4`); a row added with that id is referenceable before commit exactly as a buffered dict is |
| batching keeps emission off the handler's critical path | **weak** — an `INSERT` on an already-open session is a flush, not a round-trip-plus-commit; the cost the buffer avoids is the per-event `db.commit()` that the non-pipeline path pays and the pipeline path pays later anyway |
| events whose content is only known after the handler returns (the log signal, memory hints) | **holds** — those stay post-handler |
| eager emission records work that then rolls back | **holds, and is the constraint**: an event must never commit *before* its work |

The shape that satisfies the last row without the window: **add the row to the handler's own
session and do not commit it** — the event becomes part of whatever transaction the handler
commits next, and rolls back with the work if the work rolls back. That is DBOS's property
("the event store *is* the workflow store, one connection") without an outbox table or relay.

---

## 3. The mechanism

```python
# execution_signal_helper.queue_system_event, inside a pipeline (proposed)
event = SystemEvent(id=uuid4(), type=…, …)      # the same client-side id the bucket would hold
db.add(event); db.flush([event])                 # on the HANDLER's session; no commit
link parent (link_events) on the same session   # as _persist_system_event does today
bucket["events"].append({"id": event.id, "persisted": True, ...})   # for the post-handler pass
return event.id
```

- **The post-handler flush (`_apply_event_signals`) skips entries marked `persisted`** and
  emits only what the handler could not have written (memory hints, the log signal, anything
  queued with no `db`). It keeps its swallowing `try`, which is now correct: what it emits is
  derived, not the record.
- **The handler's next commit carries the events.** Flow nodes commit per node
  (`runner.py` `FlowHistory` + `db.commit()`), the request EU finalize commits (`FR-30`), routes
  commit through `_execute_flow`. An event queued after the last commit rides the EU finalize —
  which is the last commit of every request since `EU-FINALIZE-UNCOMMITTED-1`.
- **Rollback semantics fall out:** a handler that raises rolls back its session, and the
  events it queued go with it — no record of work that did not happen. The pipeline's error
  event (`emit_error_event`) is written on its own session after the rollback, as today.
- **`required=True`** keeps its meaning (the emit may raise) and gains the property the entry's
  scope note wanted: a required event is on the session before the handler's commit, so it
  cannot be lost after it.

**What this does not change:** `emit_system_event`'s own path (non-pipeline callers), the
`ExecutionContract` gate (`execution.*` outside a pipeline still raises/warns), the event bus
(`DEC-013`: the bus never carries a payload; the row is written first — this makes that
ordering *transactional* rather than sequential).

---

## 4. The one hazard, and the test that pins it

The handler's session is the request's `get_db` session. `_persist_system_event` today calls
`db.commit()` — "flush only this object — avoids committing pending handler changes as a side
effect" says its comment, and then commits everything two lines later. Under the new shape
nothing in the event path commits; **the handler decides when**. The hazard is a route whose
handler never commits at all (a pure read) queuing an event: the event rolls back at `get_db`
close. Two answers, and the test must cover both:

- the pipeline's EU finalize commits on every request (FR-30), so a read-only handler's events
  ride that commit — **assert it through a separate connection** (`test_request_eu_finalize_
  commits_fr30.py`'s method; the shared fixture cannot see a flush-vs-commit difference,
  catalogue variant 15);
- a handler that raises must leave **no** event row — the liveness control for the first.

---

## 5. What not to build

- **Not an outbox table + relay.** Two stores to span is the reason for an outbox; there is one.
- **Not eager emit-and-commit** — the entry's own prohibition, and the rollback row in §2.
- **Not a change to `emit_system_event`'s non-pipeline commit** — that path already co-locates,
  and its commit is what a scheduler-thread caller relies on.
- **Not `required=True` bypassing the buffer** — the entry's interim mitigation; superseded by
  §3, which gives every event the property.

## 6. Decisions this design asks for (`DEC-NNN` in the implementing PR)

1. Inside a pipeline, a queued event is **added to the handler's session without commit** and
   rides the handler's next commit; the post-handler flush emits only what is not yet persisted.
2. The event's id stays client-assigned; nothing changes for callers that hold the returned id.
3. A handler that raises leaves no event row (rollback semantics are the point, not a side
   effect).

## 7. Tests

- Through the real pipeline on a route: crash the flush (patch `_apply_event_signals` to raise)
  and read the events through a **separate connection** — present.
- A handler that raises → zero rows for its queued events (control).
- A read-only handler's event rides the EU finalize commit (separate connection).
- Non-pipeline path unchanged: `test_event_*` suites green; the ExecutionContract gate still
  refuses `execution.*` outside a pipeline.
- Mutation: reinstate the buffer-only path → the crash test goes red.
