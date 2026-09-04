### Fixed — two registry entries described gaps their own PRs had already closed, and one closed gap was not closed

- `CANCEL-REACH-1` and `EXEC-ENV-BIND-1` were re-filed accurately after #566 and #567 shipped
  against them. Both registry lines still described the pre-fix state; anyone scanning for open
  work got a wrong answer and nothing said so. This is the third such correction in a week
  (#557, #565), which is why the *reason* is recorded here rather than only the fix: an entry is
  most likely to be stale in the hours right after its own PR merges.
- **`CANCEL-REACH-1` keeps two residuals that were previously unrecorded.** The syscall
  dispatcher chokepoint the entry proposed was never taken — `SyscallContext` carries no run
  identity, so it needs a new field rather than a hot-path lookup, and that is the same missing
  identity `INITIATOR-IDENTITY-1` and `COST-GOVERNOR-1` hit at their own seams.
- **An over-claim shipped with #566 and is corrected here.** The out-of-process tool worker
  passes `run_id=None`, documented as safe "because that path is hard-killable by its isolation
  class instead." That capability exists and **nothing invokes it on cancel** — the worker is
  killed by `subprocess.run(timeout=…)` and by nothing else, so a cancelled run's in-flight
  isolated tool still runs to completion. Source and test docstrings now say so. No behaviour
  changes in this PR; what changes is that the gap is visible.
