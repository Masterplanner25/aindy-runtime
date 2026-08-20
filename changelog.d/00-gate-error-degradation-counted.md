### Fixed — the idempotency gate had a second, uncounted path to `AT_LEAST_ONCE`

**Operators running `AINDY_SYSCALL_IDEMPOTENCY` (on by default since 2.5.0): the degradation
counter under-reported.** `aindy_effect_gate_outcomes_total{outcome="degraded"}` counted only one
of the two ways a call gets downgraded.

| Path | Meaning | Before | Now |
|---|---|---|---|
| `effect_ledger` — lost the insert race to a **live pending row** | contention; expected | `degraded` | `degraded` |
| `SyscallDispatcher` — the **gate machinery itself raised** | the gate is broken | **counted nothing** | `degraded_gate_error` |

Both drop the caller to `AT_LEAST_ONCE`, so a dashboard watching only `degraded` would have shown
a clean gate while calls were silently losing at-most-once. They stay separate labels because the
remediations differ: one says *you have contention*, the other says *investigate the gate*.

**Found in CI by the contention soak**, which asserts that a second handler run is never silent.
It failed with the handler having run twice while `degraded` stayed flat — the downgrade had come
through the dispatcher branch. The soak now asserts across **both** labels, because the property
that matters is *"a downgrade was never silent"*, not *"the contention path fired"*. Pinning it to
one label made a correct runtime look broken and would have let a real silent downgrade through
the other path.

If it ever fires again, the assertion message now says what to look for: **a third path to
`AT_LEAST_ONCE` that nothing counts.**
