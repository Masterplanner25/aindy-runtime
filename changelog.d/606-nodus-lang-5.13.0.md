### Changed — `nodus-lang` 5.9.0 → 5.13.0 (`NODUS-UPGRADE-1`, #606)

- Bumped across **all three pin sites** the entry names — `pyproject.toml`,
  `AINDY/requirements.txt`, and the `Install MCP extra` CI step, which installs directly and so
  re-resolves a constraint fixed only in the first two.
- **No breaking change at our call site, verified rather than assumed.** `NodusRuntime.__init__`
  has no `**kwargs`, so a renamed confinement argument raises instead of silently unconfining the
  guest. Signatures were diffed between 5.9.0 and 5.13.0 in a throwaway venv: **nothing removed**,
  two parameters added (`extensions`, `max_memory_mb`). All four arguments
  `nodus_runtime_kwargs()` passes — `allowed_paths`, `allow_subprocess`, `allow_network`,
  `allow_env` — are present in both.
- **`aindy-runtime[mcp]` stays installable** (`MCP-SDK-2X-1`): `nodus-mcp` 0.1.3 requires only
  `nodus-lang>=4.0.0`, so no cap blocks the major-line move, and a clean-venv plan resolves
  `nodus-lang 5.13.0` + `nodus-mcp 0.1.3` + `mcp 1.30.0` with the `<2` cap intact.
- **★ 5.13.0 carries a security fix we were not exposed to** — `nodus serve` confined the
  filesystem by default (`#843`); code posted to `POST /execute` could previously read and write
  anywhere the server process could. `RuntimeService` and `nodus serve` have **zero references**
  in `AINDY/`; we embed `NodusRuntime` directly and pass `allowed_paths` explicitly. Recorded
  because `NODUS-UPGRADE-2`'s rule is to read the intervening notes before assigning severity —
  a severity taken from version distance is not an assessment.
- New capability worth noting against open entries: **`max_memory_mb`** on the guest runtime is
  the per-execution memory ceiling `SYSMAX-3` records as "needs OS integration", now available on
  the nodus path. Not adopted here; this bump changes pins only.
