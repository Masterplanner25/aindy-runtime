### Fixed — the effect gate had a **third** silent path to `AT_LEAST_ONCE` (#516)

**Operators running `AINDY_SYSCALL_IDEMPOTENCY` (on by default since 2.5.0): duplicate handler
runs were under-reported again, and this time by the most common route, not the rarest.**

`resolve_effect_record` opens with a `SELECT`. A caller that finds an existing row gets there by
one of two routes, and which one is decided purely by whether its `SELECT` lands before or after
the winner's `COMMIT`:

| Route | What the caller sees | Before | Now |
|---|---|---|---|
| Lost the `INSERT` race | `IntegrityError` → re-query → live `pending` | `degraded` | `degraded` |
| **Read the committed row** | the opening `SELECT` already returned `pending` | **counted nothing** | `degraded` |
| **Read a `failed` row** | the opening `SELECT` returned `failed` | **counted nothing, and did not reclaim** | `reclaimed` |

Both of the bottom two run the handler a second time. Neither moved
`aindy_effect_gate_outcomes_total`, which is the only signal an operator has that `EXACTLY_ONCE`
did not hold.

**This is the larger half of the duplicates, not an edge case.** Under contention most losing
callers do not race the insert at all — they arrive slightly later and read the committed
`pending` row. So the counter was reporting the *rarer* route and silently dropping the common
one.

**Also fixed, on the same path:** reading a `failed` row skipped the reclaim, so the row kept the
previous attempt's attribution and `created_at` while a new attempt ran against it. That left its
staleness clock running from the *first* attempt, and left the slot marked `failed` during
re-execution — so a third caller arriving in that window also fell through uncounted. It is now
reclaimed exactly as the race path already did: `pending`, clock reset, re-attributed.

**Root cause worth recording: the decision existed in only one of the two places that reach it.**
It was written for the `IntegrityError` branch and never mirrored for the direct read, which then
fell through to a bare `return False, None`. It is now one `_resolve_existing_row` helper called
from both, so the two cannot diverge again.

**How it was found, and why it took three rounds.**
`test_the_gate_degrades_to_at_least_once_under_contention` fired the exact message it was written
to carry after the *second* fix (#511): *"look for a THIRD path to `AT_LEAST_ONCE` that nothing
counts."* It fired on a docs-only PR.

★ **The soak could only ever catch this by luck, and that is the more transferable lesson.** Its
degradation assertion is guarded by `if len(runs) > 1`, so a run where the threads happen not to
collide skips the assertion and reports green — `trusting-a-green-check` **variant 9**, *green
because there was nothing to catch*. A sibling PR containing the same commit passed the same job
for exactly that reason, which is why "re-run it until it goes green" would have laundered the
finding rather than fixed it.

The regression guard is therefore **deterministic and sequential**, in
`tests/integration/test_effect_ledger_gate_accounting.py` — no concurrency is needed to
demonstrate any of this, which is itself the point. Mutation-tested: reverting the fix fails the
two bug tests and correctly leaves the liveness control and the replay test passing.
