### Fixed — two memory routes now reach effects through the dispatcher (`ROUTE-EFFECT-BYPASS-1` A+B, #460)

`POST /memory/nodes` and `POST /memory/recall` called `MemoryNodeDAO` directly with the request's
own session, so the effect passed **no capability check, no tenant-isolation check, no quota
accounting and no effect ledger**. A scope decorator would not have helped — the effect never
reached the chokepoint that reads scopes.

Both now dispatch. `POST /nodes` goes through `sys.v1.memory.write`, which since the `IDEM-11`
audit declares **`EXACTLY_ONCE`**, so it gains at-most-once as well.

**★ `sys.v1.memory.write` now merges the caller's `extra` instead of replacing it.** It hard-set
`extra={"execution_unit_id": …}`, discarding anything the caller sent. The route passes
`extra=body.extra`, so rewiring without this fix would have been **silent data loss behind a
201** — not a failure. `execution_unit_id` still wins a key collision, so provenance stays
non-forgeable.

The dispatch helper hands the **request's own session** to the handler via `_db`, keeping the
write inside the caller's transaction; opening a second session per request is the shape
`RT-MEMTXN-LEAK-1` traced to pool exhaustion. A non-success envelope raises `HTTPException`
rather than returning 200 with an error body (`ROUTE-GUARD-1`).

**Two routes deliberately not rewired**, and a test pins which: `POST /links` has **no syscall
equivalent** (a build, not a rewire), and `POST /nodes/search` calls `dao.find_similar` with
`min_similarity`, which `sys.v1.memory.search` neither accepts nor uses — it calls `dao.recall`.
Rewiring that one would change search *semantics* under cover of a mediation fix.
