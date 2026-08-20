### Added — the warm Nodus worker pool is soaked under contention against real workers (NODUS-WARMPOOL-1)

`NODUS-WARMPOOL-1`'s remaining work was recorded as "soak, then flip." This is the soak, and it
exists because of a gap the existing suite named in its own docstring:

> *"…against fake processes/workers (no real subprocess) … End-to-end (a real warm worker serving
> a nodus script) is **app-side PG-tier integration**."*

**That deferral is the whole problem.** It handed the only end-to-end evidence to a consumer that
does not exercise it (`SUBSTRATE-WITNESS-1`), so the pool ran against fakes here and against
nothing there. Meanwhile CI has had `AINDY_NODUS_WARM_POOL=1` on every PR — real evidence, but
**functional and sequential**: it shows the pool serves requests, not that it serves *concurrent*
ones correctly.

`tests/unit/test_soak_warm_pool_contention.py` adds seven tests against **real worker
subprocesses**, six concurrent callers against a pool of two:

- **Response correlation** — every caller gets back the marker *it* sent. The pool speaks
  length-prefixed JSON over one worker's stdin/stdout, so if `_checkout`/`_checkin` exclusion is
  ever wrong, two callers interleave frames and one receives another's result — a silent
  cross-tenant wrong answer that fakes cannot detect and sequential tests cannot reach.
- **Worker reuse under load** — with a long acquire timeout, callers queue and a worker is handed
  from one to the next, which is where a stale frame in the pipe would surface.
- **Boundedness**, **backpressure as `PoolBusy`**, and **no cross-caller state bleed**.

#### ★ The layer mattered, and the first draft got it wrong

`pool.execute()` **raises `PoolBusy`**; the **adapter** is what spills to a fresh subprocess. The
first draft asserted the *pool* spills and produced three reds that looked like a product defect
but were entirely the test's fault. The claim *"enabling the pool can never make execution worse
than the default"* is about the adapter path, and it is now asserted there.

Mutation-tested **4/4** — including that removing the size bound in `_checkout` and swapping
`PoolBusy` for a bare `RuntimeError` both go red. An earlier run scored 2/4 and **both survivors
were defective mutations**, not weak tests: one edited `prewarm()`, which the fixture disables,
and one added an unused class while `PoolBusy` was still raised. A mutation that does not change
behaviour proves nothing.

#### ★ It also found a trap in the soak harness itself

`drive_concurrently` returned results in **completion order**, so a per-caller positional
assertion paired the wrong result with the wrong caller. It failed deterministically 3/3 and read
exactly like a cross-request state bleed *in the pool*.

An unordered result list is a trap in a concurrency harness specifically: the natural way to write
a per-caller assertion is positional, and the failure it produces **accuses the product**.
`drive_concurrently` now returns results in **worker-index order**, guarded by a test that inverts
completion order, with the partial-failure caveat documented.
