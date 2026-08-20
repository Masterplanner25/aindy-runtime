### Changed — two execution defaults are now ON: the idempotency gate and the warm Nodus worker pool

**Operators: both change runtime behaviour on upgrade. Each has a documented off switch.**

| Flag | Was | Now | Off switch |
|---|---|---|---|
| `AINDY_SYSCALL_IDEMPOTENCY` | off | **on** | `AINDY_SYSCALL_IDEMPOTENCY=0` |
| `AINDY_NODUS_WARM_POOL` | off | **on** | `AINDY_NODUS_WARM_POOL=0` |

Both accept `0`, `false`, `no`, `off` (case-insensitive), each pinned by a parametrised test —
a security or execution default that cannot be turned off is a different kind of problem.

---

#### `AINDY_SYSCALL_IDEMPOTENCY` — what it does, and precisely what it does not guarantee

The gate dedups an `EXACTLY_ONCE` syscall on `(action_type, input, scope)` where the scope is the
**execution unit id**. So a retry *within one run* replays the cached result instead of
re-executing, and **two legitimate calls in different runs are untouched.** That scoping is what
makes this safe to default on.

Eight syscalls declare `EXACTLY_ONCE` and are affected: `memory.write`, `memory.link`,
`event.emit`, `flow.run`, `flow.execute_intent`, `nodus.execute`, `job.submit`, `agent.undo`.

**★ It is NOT exactly-once under contention.** When the gate loses the insert race against a live
pending row it degrades to `AT_LEAST_ONCE` for that call and logs a warning — strict at-most-once
needs advisory locking. Measured: **8 concurrent identical calls ran the handler twice.** Watch
`aindy_effect_gate_outcomes_total{outcome="degraded"}`; a deployment where that is a meaningful
fraction of `reserved` has a weaker guarantee than the name suggests.

**★ This does not close `IDEM-12`.** `undo_run_effects` selects effects by `status == "success"`
and never consults `effect_reversals`, so a deliberate second `sys.v1.agent.undo` still
re-invokes every compensator. The gate is defence-in-depth, not the fix — and making reversal
correctness depend on an env var is the shape `IDEM-10` already paid for.

#### `AINDY_NODUS_WARM_POOL` — soaked before flipping

Reuses a bounded pool of warm worker subprocesses so plugin cold-start is paid once rather than
per execution. **Any warm-path failure falls back to a fresh subprocess**, so enabling it cannot
make execution worse than the path it replaces — asserted at the adapter, where that claim lives.

**The prior evidence was not what it looked like.** CI had set this flag for months, but the
integration suite is *sequential*: it showed the pool serves requests, not that it serves
*concurrent* ones correctly. Every pool test ran against **fake** processes, and end-to-end was
deferred to "app-side PG-tier integration" — a consumer that does not exercise it.

`tests/unit/test_soak_warm_pool_contention.py` closes that with six concurrent callers against a
pool of two **real** worker subprocesses, mutation-tested 4/4.

#### ★ What flipping found

**The warm path had never been asserted to carry DUR-2b's durable-effects signal.** That signal
must survive the process boundary because a ContextVar cannot cross it — and the warm pool is now
the *default* path. A warm path that dropped it would have silently disabled at-most-once for
every continued run while every existing DUR-2b test stayed green. It does carry it; there is now
a test saying so.

One existing test asserted on the fresh-subprocess payload and went red on the flip, correctly —
it now pins `AINDY_NODUS_WARM_POOL=0` explicitly, because it is about that path specifically.
