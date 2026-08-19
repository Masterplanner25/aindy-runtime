### Changed — the Nodus guest VM now asks for its environment instead of being hardcoded (EXEC-ENV-BIND-1 phase 2)

Phase 1 gave an execution unit a way to *declare* an environment. Phase 2 is the first place a
declared spec actually changes how something runs.

**The residual this closes was a wrong comment, not a missing line.** `nodus_worker.py` said
*"the VM already confines filesystem access: `allowed_paths` defaults to the cwd"*. True of
nodus — and false here, because **nothing sets the worker's cwd**. Neither
`nodus_worker_pool.WarmNodusWorker` nor `nodus_runtime_adapter`'s `subprocess.run` passes `cwd=`,
so the guest inherited the **server's** working directory: `/home/aindy` in Docker, which holds
`alembic/` — a guest could write migrations that run on next boot — and the repo root in dev,
which holds `AINDY/.env`. `GUEST-CONFINE-1` closed the *escape* in August; the **bound** stayed an
undeclared inherited default until now.

- Every confinement argument is now derived from an `ExecutionEnvironmentSpec` clamped to
  `GUEST_FLOOR`, rather than three hardcoded `False` literals at one construction site. The guest
  path has a *stated requirement* that can be recorded and audited.
- `allowed_paths` is passed **explicitly**, bounded to a per-execution temporary scratch root that
  is created before the VM and released after it. A warm worker no longer shares scratch between
  requests.
- With no declared spec the floor applies unchanged — byte-for-byte the confinement that shipped
  in August, plus the explicit bound.

#### Two behaviour changes for operators

- **`NODUS_ALLOWED_PATHS` no longer has any effect.** nodus reads that variable only on its
  unspecified-default branch, so passing `allowed_paths` explicitly makes it inert. If you were
  using it to widen the guest's filesystem bound, that is now closed — deliberately, and it is
  the safe direction. There is no replacement env var by design: a global flag re-opens the bound
  for every run at once, which is the shape `GUEST-CONFINE-1` refused.
- **A declared spec is clamped to the floor, never merged with it.** A guest cannot widen its own
  sandbox by arriving with a permissive descriptor; it can only ask for *more* confinement.

#### What it did not close

`ORCHESTRATOR-SPLIT-1` predicted the same missing `cwd=` closed both its store-4 data loss and
this residual. **It did not.** The residual was closed by bounding the VM's `allowed_paths` —
stronger than setting a cwd, but it leaves the **process** cwd untouched, so
`nodus_lang_workflow`'s `LocalWorkflowStore` still roots wherever the worker started. That entry
has been corrected rather than left to mislead the next reader.

Also: `nodus_worker.py` gained a module logger, which it had never had. That is not an oversight
anyone should fix casually — **its stdout is the JSON protocol channel**, so a stray `print()`
corrupts the frame the adapter parses. Logging defaults to stderr, which both spawn paths handle.

11 new tests against the real VM, mutation-tested **6/6**; the existing `GUEST-CONFINE-1` suite
re-run against the real VM and green.
