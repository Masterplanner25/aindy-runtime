### Fixed — a route's deliberate 4xx is no longer replaced by a 500 (FR-20, #520)

A route registered under the execution contract that raised `HTTPException` **before** entering
the pipeline had its status discarded: the guard converted every endpoint exception into a
`RouteExecutionViolation`, which surfaces as a 500. A stale link that should 404 returned 500, so
the user-visible symptom of an app contract slip was a wrong status code rather than a recorded
violation — and a client cannot tell "rejected" from "the server broke" by a 500.

The runtime already disagreed with itself here: an `HTTPException` raised by a **dependency**
passed through with its status (401 stayed 401), while the same exception from the endpoint body
became a 500. The two now agree.

**The violation is still recorded — it just stopped being recorded in the status code.** That was
the part worth getting right: before this, the 500 was the *only* evidence a violation occurred,
so preserving the status without somewhere else to put it would have traded a wrong status for a
silent one. New metric:

```
aindy_route_contract_violations_total{route, outcome}
  outcome=status_preserved   # a deliberate HTTPException, now passed through intact
  outcome=converted_500      # anything else — still a RouteExecutionViolation
```

plus an ERROR log naming the route and the outcome. Only a deliberate `HTTPException` is
preserved; an unexpected exception from a managed route is still a violation and still a 500.

Both halves of the path had to change together — the route guard and the contract middleware —
because the middleware re-raises independently. Reverting either one alone puts the 500 back,
which is now pinned by a test.
