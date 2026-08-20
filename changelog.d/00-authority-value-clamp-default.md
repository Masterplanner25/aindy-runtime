### Changed — a nested syscall context can no longer widen its capability grant (AUTHORITY-VALUE-1)

**Operators: this is a security default moving to ON.** `AINDY_CHILD_CONTEXT_CLAMP` now defaults
**true**. `child_context()` narrows the parent's capability grant and never widens it; a widening
request is dropped and logged at WARNING. Set `AINDY_CHILD_CONTEXT_CLAMP=0` to restore the
previous behaviour, in which the widening was granted and only warned about.

**What this changes for you.** If any of your syscall handlers dispatch a nested syscall using
`child_context(context, capabilities=[...])` with a capability the *parent* context does not
hold, that nested dispatch will now be denied — an error envelope, not an exception. Every such
widening has been logged at WARNING since 2026-08-16, so `grep` your logs for
`child_context WIDENED authority` to see whether you have any before upgrading.

#### Why the default moved, and why the previous reasoning was wrong in its conclusion only

The flag shipped opt-in on one claim: clamping intersects `aindy-apps-monolith`'s
`_dispatch_owner_syscall` pattern to the **empty set**, and therefore "denies a call that works
today." **The mechanic is real and is still pinned by test.** What was never measured is what the
empty set costs.

Measured against the monolith:

| | |
|---|---|
| Functions that widen via `_dispatch_owner_syscall` | **19** |
| Registered — reachable by the dispatcher | **1** |
| Unregistered — dead code a clamp cannot break | **18** |

The one live caller widens for an **optional** cached-suggestions lookup, wrapped in
`try/except` with a full recomputation beneath it. Denied, it logs a warning and recomputes.

**Count: 1 degradation, 0 outages.** This repository's own rule is to tighten a boundary on a
count rather than an argument, and the count supports the flip.

#### The transferable part

An **executable fact** — the intersection is empty — had an **inference** layered on it — therefore
an outage — and the inference was never re-measured for three months while the fact was cited as
though it carried the conclusion. The test keeps the fact and now explicitly refuses the
inference.

Three tests added, including the one the original reasoning never checked: a starved context makes
`dispatch` return an **error envelope** rather than raising, which is the entire reason a caller's
`try/except` degrades instead of failing.
