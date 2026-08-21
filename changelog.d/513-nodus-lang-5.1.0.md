### Changed — `nodus-lang` 5.0.4 → 5.1.0 (#513)

`nodus-lang==5.1.0` across all three pin sites (`pyproject.toml`, `AINDY/requirements.txt`, and
the `Install MCP extra` CI step). `nodus-mcp` is unchanged at `>=0.1.3`, and it caps `nodus-lang`
only at `>=4.0.0` — unbounded above — so this is a one-repo bump, not a two-repo release train.

**The one behaviour change worth an operator's minute, and it is upstream's, not ours.** Before
5.1.0, `run_source(source, filename=...)` ran the **file** named by `filename` whenever such a
file existed — discarding the `source` the caller passed and returning `ok=True` with the other
program's output. Present since nodus v0.4.0 (upstream #521). `filename` is now purely a label; a
real path still resolves relative imports against its directory, and `run_file` is unchanged.

**This runtime was never exposed, and that is now asserted rather than read.** Every `filename`
reaching `run_source` is built by `NodusRuntimeAdapter.run_script` as `<nodus:eu:{id}>`, with
`nodus_worker` falling back to the same angle-bracket form; `<...>` names no file under any
working directory. Our own `run_file` reads the script itself and passes the *source* through the
same path. Two guards in `tests/unit/test_nodus_upgrade_contract.py` keep it that way — one pins
the upstream guarantee, one calls the adapter and fails if it ever passes a resolvable path.

*Worth recording because of the shape:* this is the same failure mode as `GUEST-CONFINE-1`'s
residual — behaviour depending on a process CWD the runtime never sets. There the worker inherited
the server's directory (`/home/aindy` in Docker, which holds `alembic/`). We escaped this one by a
formatting convention, not by a decision.

**New in the guest workflow DSL** (available to `.nd` scripts; the runtime does not consume it
yet): a step can carry a guard (`step ship after review when reached("approved")`) and declare
which dependency outcomes satisfy it (`with { on: ["failed"] }`); a `state` cell can declare how
concurrent writes merge and whether it is durable; every task now reports a terminal status
(`completed`, `failed`, `upstream_failed`, `skipped`, `omitted`, `cancelled`, `abandoned`) where
anything that never got a turn was previously just absent from the result; and a failed step
drains the run instead of tearing the scheduler down, so a timed-out step still gets its `finally`
blocks and its siblings finish.

Those first two are worked references for open runtime entries — declared per-cell merge policy is
what `FLOW-PARALLEL-1` says any fan-out fix must have (the flow layer is `state.update(patch)`,
last-write-wins, today), and the status vocabulary is `EFFECT-PARTIAL-1`'s three-outcome problem
solved one layer down. Neither entry changes here; they now have an implementation to point at.

### Fixed — `nodus_worker_pool` module docstring contradicted its own function (#513)

The module docstring still described `AINDY_NODUS_WARM_POOL` as *"Opt-in (default off)"* and
credited that default with bounding the `nodus-lang <= 5.0.2` shared-guest-memory exposure, while
`warm_pool_enabled()` ~200 lines below has said **default ON** since 2026-08-19. One file, two
answers — the `ISOLATION-DOC-STATUS-1` shape.

Not cosmetic: that docstring's standing rule is *"before enabling the pool after any dependency
bump, re-run the guest-memory isolation guard."* With the pool already enabled, re-running it is a
precondition of **every** dependency bump, not of a flag flip that has already happened. It was
re-run for this bump.
