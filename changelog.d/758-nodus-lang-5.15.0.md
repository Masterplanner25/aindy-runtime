### Changed — `nodus-lang` 5.14.0 → 5.15.0 (`NODUS-UPGRADE-1`, #758)

- Bumped at **all three pin sites**: `pyproject.toml`, `AINDY/requirements.txt`, and the
  `Install MCP extra` CI step. That step installs directly, so a pin changed only in the first
  two would not reach it.
- **Our call site does not change. This was checked, not assumed.** We diffed the public surface
  we import between 5.14.0 and 5.15.0 in throwaway venvs: `NodusRuntime.__init__` and its public
  methods, `tokenize`, `Parser`, the `GoalDef`/`WorkflowDef` fields, and
  `memory_metering_available`. It is **identical**. `nodus check --staged` on
  `AINDY/nodus/stdlib/memory.nd` gives output identical to 5.14.0.
- **Why take it:** 5.15.0 fixes what a workflow loses when it parks at `workflow_wait` and
  resumes in another process. Two of the fixes are **confinement fixes on the guest side**:
  - A resume now inherits the caller's bounds (nodus #873). Before, a guest could escape its
    instruction budget and its deadline by parking.
  - A derived VM now inherits the host state it works for, and a module function's `agent_call`
    no longer reaches the process-global registry (nodus #868).

  The host resumes through `PersistentFlowRunner`, not `resume_workflow`, so the three behaviour
  changes the release lists (#870 checkpoint replay, #873's 200 ms `nodus run` default, #875
  `max_terminal_runs`) do not reach any runtime call path. We set none of them.
- `nodus-mcp` 0.1.5 is compatible and resolves under the existing `>=0.1.4` floor. It only fixes
  the version string that 0.1.4 reported. The floor is not raised, because nothing here reads
  that string.
