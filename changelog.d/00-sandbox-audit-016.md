### Added — `SANDBOX_ESCAPE_AUDIT.md` Entry 016 for the `v2.3.0` gate run (#457)

17 / 17 PASS on the `v2.3.0` tag (`python:3.11-alpine`, native Linux containers, commit
`c911312`). The certified boundary is untouched — `git diff v2.2.0..v2.3.0` over
`sandbox_runner.py`, `plugin_host.py`, `sandbox_certification.py` and `tests/sandbox/` is empty.

**★ The entry names the one dependency change and why a green gate here does not cover it.**
`nodus-lang` 4.1.0 → 4.2.0 does not touch the Tier-2 OCI runner this suite certifies, but it
*does* touch the **guest** boundary `GUEST-CONFINE-1` closed, because confinement is expressed
as VM constructor arguments. Had one been renamed, the guest would run unconfined **while this
suite still reported 17/17** — the two boundaries are independent. That was verified against the
real VM before the bump landed, not inferred from this result.
