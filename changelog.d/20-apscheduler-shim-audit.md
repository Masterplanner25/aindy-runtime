### Fixed — `remove_job` failures were swallowed under a misleading comment (#454)

Found by auditing the vendored `apscheduler` shim after the same gap appeared twice
(`FR-15` (b), `SYSMAX-5`).

`pytest.ini` sets `pythonpath = . AINDY`, so `import apscheduler` resolves to
`AINDY/apscheduler` — a hand-written shim — for **every test in this repo**. Anything the
runtime calls that the shim does not implement is untested by construction, and where the call
sits inside a `try/except` it fails *silently*.

- **`nodus_schedule_service._remove_from_scheduler` caught `Exception` and passed**, under a
  comment saying *"Job may already be gone"*. The shim had no `remove_job`, so under test the
  call raised `AttributeError` and was swallowed — **removal could have been a permanent no-op
  with every test green**, and a renamed scheduler API would have looked identical to a
  legitimately deleted job. Now only `JobLookupError` is silent; anything else warns.
- The shim gained `get_job`, `remove_job` and `jobstores.base.JobLookupError`, raising the same
  type production raises so a test exercises the same branch.
- **New guard, derived from source rather than a maintained list:** a test scans `AINDY/` for
  methods called on the scheduler and fails if the shim cannot express one. A hand-written list
  of expected methods would drift exactly as the shim did.

Also pinned: **`import nodus` must resolve to the installed package**, not `AINDY/nodus/`. That
directory shares the real package's name *and* `runtime/embedding.py` shares its exact module
path — the path `GUEST-CONFINE-1`'s tests import `NodusRuntime` from to assert 31 builtins are
refused. Today the collision is self-limiting (the file is a re-export, so shadowing would
self-import and fail loudly), but that depends on it staying a re-export.
