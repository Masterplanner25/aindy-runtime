### Added — `ROUTE-EFFECT-BYPASS-1` filed: four memory routes reach effects without the dispatcher (#459)

Split out of `HTTP-SCOPE-GAP-1` because the fix is different work — that entry is about scope
checks not reaching routes; this is about routes not reaching the chokepoint at all. **A scope
decorator on a route that skips the dispatcher still leaves the effect unmediated.**

Documentation only; no behaviour change.

**Measured smaller than the parent entry implied.** `HTTP-SCOPE-GAP-1` notes `memory_router.py`
has "zero `SyscallDispatcher` references", which reads as if all 22 routes bypass. **18 of 22 go
through `_mem_run_flow` → `run_flow` → the dispatcher.** Four do not: `POST /nodes` and
`POST /links` (writes), `POST /nodes/search` and `POST /recall` (reads) — and the file carries
**no scope checks either**, so those four have neither gate.
