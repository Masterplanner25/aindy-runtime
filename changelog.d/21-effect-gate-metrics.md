### Added — the idempotency gate is now observable (`aindy_effect_gate_outcomes_total`)

**Until now nothing observed the gate at all.** `aindy_durable_effects` and
`aindy_effect_attribution` are ContextVars, not metrics — so with `AINDY_SYSCALL_IDEMPOTENCY`
enabled an operator had no way to tell whether the gate was firing, replaying, or **silently
degrading**. That absence was the real blocker on a production soak: there was nothing to read.

`aindy_effect_gate_outcomes_total{outcome=…}` counts every resolution:

| `outcome` | meaning |
|---|---|
| `reserved` | this caller won the slot and will execute the effect |
| `replayed` | a completed record was returned instead of executing |
| **`degraded`** | **lost the race to a live pending row — downgraded to `AT_LEAST_ONCE` for this call** |
| `reclaimed` | took over a stale or failed slot |

**`degraded` is the label the counter exists for.** `EXACTLY_ONCE` is not exactly-once under
contention: when the gate loses the insert race to a live pending row it downgrades for that
call. That is correct and documented in `IDEMPOTENCY_CONTRACT.md` — and it was **invisible**. A
deployment where `degraded` is a meaningful fraction of `reserved` is one where the guarantee the
operator believes they enabled is not the one they have.

**Metrics failures never change execution.** `_count_gate` is best-effort and import-local: the
ledger is the correctness path and the counter is observability, and inverting that would let a
Prometheus problem become a duplicate side effect — the exact class the gate exists to prevent.

The contention soak now asserts on this counter rather than on a log line. Three instruments were
tried: `caplog` could not see a warning emitted on a worker thread, a logger spy was thread-safe
but observed the wrong signal, and the counter is both thread-safe and what production reads.
Recorded as **variant 10** in the trusting-a-green-check catalogue — *the instrument cannot see
the thing* — because it generalises to every concurrent or cross-process test.

Using the harness for real also improved it: `read_metric` now distinguishes an **unknown metric
family** (still raises) from a **label combination not yet observed** (reads 0), because
prometheus_client does not materialise label combinations until `.labels()` is first called. The
guard was right to refuse; the rule was too coarse.
