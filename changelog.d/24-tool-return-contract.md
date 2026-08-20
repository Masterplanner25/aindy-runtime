### Added — tool returns are measured against the process-boundary contract (TOOL-SEAM-ISOLATION-1 step C1)

`aindy_tool_return_contract_violations_total{reason, declared_isolation}` counts tool returns that
would not survive a process boundary: `not_a_dict`, or `not_json_serializable`.

**It measures; it does not reject.** By the time a return is inspected the handler has already run
and its effect is real — failing the call there would **discard a real effect**, which is strictly
worse than passing an awkward value through. `SyscallDispatcher` made the same judgement on the
syscall path (*"a ledger failure must never turn that into a caller-visible error"*), and the two
boundaries must not disagree.

#### Why it exists: it is the gate on step C

A tool cannot run behind a process boundary unless its return marshals — a `UUID`, a session, or
any live object crosses no pipe. Every tool that exists returns a dict and every one is typed
`-> dict`, but **nothing enforced it**, so "they all comply" was an assumption rather than a
measurement. A non-zero count is now exactly the list of tools that cannot be confined yet.

`not_json_serializable` is the case a plain `isinstance(result, dict)` check would miss, and it is
the one that actually bit: on the syscall path a `UUID` return came back as an error envelope
**after the effect had already landed**. That was fixed there and never here.

#### Two details

- **A tool that declared an isolation class is logged at ERROR and labelled separately.** It has
  opted into a boundary its return cannot cross, so that is a defect in the tool rather than an
  observation about an in-process one — and the label keeps the remediation list readable.
- **A metrics failure never affects the call.** Observability does not sit on the effect path, the
  same rule as the effect-gate counter.

7 tests, mutation-tested **6/6** — including that a mutation which *rejects* instead of measuring
goes red, because preserving a landed effect is the design and not an accident.
