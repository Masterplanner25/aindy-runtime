### Added — the nodus upgrade checklist is now executable (`NODUS-UPGRADE-1`, #467)

`nodus-lang` is pinned **exactly**, so an app cannot adopt a nodus release on its own and bumping
promptly is the runtime's obligation. What to re-verify before each bump lived as prose in
`CLAUDE.md`. It is now `tests/unit/test_nodus_upgrade_contract.py`, asserted against the
**installed nodus package** rather than a mock — the whole point being `GUEST-CONFINE-1`'s note
that *a renamed argument leaves the guest unconfined while every VM-mocking test still passes*.

What it pins:

- the three confinement flags (`allow_subprocess`, `allow_network`, `allow_env`) are still
  accepted, and still **keyword-only**;
- `NodusRuntime._get_active_vm` still exists — a *private* method `nodus_worker.py:409` depends
  on, referenced nowhere else in the repo and therefore carrying no compatibility promise;
- `nodus.services.syscall_runtime.call_syscall` is still where the `std:sys` fail-loud guard
  patches it (`NODUS-SYS-SURFACE-1`);
- the installed `nodus-lang` matches the pin in `pyproject.toml`.

**★ Two things this turned up.**

**`NodusRuntime.__init__` has no `**kwargs`.** That is good news worth pinning: a renamed
confinement flag raises `TypeError` at construction instead of being silently swallowed, so the
worker fails closed. Had there been a catch-all, `GUEST-CONFINE-1` could recur invisibly — which
is how it went unnoticed the first time. A test now asserts the absence of the catch-all, because
it is a property of someone else's code that our confinement depends on.

**"nodus forbids overriding a builtin" was only ever a docstring.** `NODUS-SYS-SURFACE-1`'s
fail-loud guard rests on that refusal — if a nodus release allowed overrides, a guest could
redefine `syscall` and bypass the guard, and every existing test would still pass because they
all *assume* the refusal rather than check it. Now asserted against the real VM for `print`,
`len` and `syscall`, so a failure distinguishes *"this builtin became overridable"* from *"the
refusal mechanism is gone"*.

### Fixed — CI had been testing `nodus-lang 4.1.0` while the wheel required 4.2.0

**★ The version check found this on its first CI run, and it is the reason the check exists.**

`Runtime Contracts` and `Integration Tests` both install with:

```
python -m pip install -r AINDY/requirements.txt
python -m pip install -e .[test] --no-deps --no-build-isolation
```

**`--no-deps` means `pyproject.toml`'s pins are never applied in CI.** The effective environment
is `AINDY/requirements.txt` — which still said `nodus-lang==4.1.0`. The pin moved to `4.2.0` in
#451 (FR-16, the app team's requested nodus upgrade) and that PR did not update the second file,
which had carried `4.1.0` since the initial repo extraction.

So since #451 every green run — **including the ones that signed off FR-16** — exercised the
version being upgraded *away from*, while the published wheel required the new one. The nodus
4.2.0 adoption was never actually tested.

`AINDY/requirements.txt` is corrected to `4.2.0`, and
`tests/unit/test_dependency_pin_agreement.py` now fails when the two sources disagree about any
shared package. Exactly one had drifted, so this was a missed edit rather than systemic rot —
which is what a guard is for, since the next bump can miss it identically.

**Adoption note for the next nodus release: bump both files.** The guard will say so if you
don't.
