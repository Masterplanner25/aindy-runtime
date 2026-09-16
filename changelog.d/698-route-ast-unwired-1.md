### Changed — route execution contract: the claim is corrected, the unwired boot-time validator is deleted (`ROUTE-AST-UNWIRED-1`, DEC-023, #698)

- `AINDY/core/route_execution_guard.py::validate_registered_route_execution` — an AST walk that
  was documented as a "boot-time refusal" of routes bypassing the execution pipeline — is
  removed, with its call-graph machinery. It was never called by the application and, by its
  own test, rejected a working route (a module-level alias of `execute_with_pipeline`). No
  behaviour changes: the request-time wrapper installed by `enforce_registered_route_execution`
  was always the only enforcement, and still is.
- What is enforced, stated precisely for the first time in the module docstring and
  `EXECUTION_CONTRACT.md`: on each request, a router that declared `require_execution_context`
  and returned without entering the pipeline fails with `RouteExecutionViolation`; routers
  registered without the dependency (admin, user-owned agents, automation logs) are wrapped
  but not required.
- What CI now proves: every non-exempt route `register_routes` installs on the real app is
  wrapped — a derived census replacing a one-route check.
