### Changed — nodus-lang 5.16.0 (#959)

- **Error columns in a Nodus script's errors and stack traces change** (nodus #921). An error raised by a call used to point at the call's last argument; it now points at what is called (`len` in `len(...)`). Lines are unchanged. Anything that parses the column out of a failed run's error will see new numbers.
- **A Nodus rebuild that re-runs a top-level effect now warns** (`StagedFlipWarning`, nodus #917), and nodus 6.0.0 will refuse it. The runtime never asks Nodus to rebuild a run (it re-drives runs itself), so this reaches us only through a guest's own `resume_workflow`. No script in this repo or the app does that, or uses `checkpoint`, whose rules also changed.
- Also in 5.16.0, opt-in and unused here: durable coroutines (`durable_spawn`), a faster dispatch loop (~1.6x on CPython), and `nodus workflow resume`/`replay` exiting 1 on failure. Not a security release.
- Bumped at all three sites: `pyproject.toml`, `AINDY/requirements.txt`, and both CI install steps. `nodus-mcp` is unchanged (`>=0.1.6`, current).
