### Changed — `nodus-lang` 5.13.0 → 5.14.0, `nodus-mcp` floor → 0.1.4 (`NODUS-UPGRADE-1`, #727)

- Bumped across **all three pin sites** the entry names — `pyproject.toml`,
  `AINDY/requirements.txt`, and the `Install MCP extra` CI step, which installs directly and so
  re-resolves a constraint fixed only in the first two.
- **No change at our call site, verified rather than assumed.** `NodusRuntime.__init__` has no
  `**kwargs`, so a renamed confinement argument raises instead of silently unconfining the guest.
  The public surface was diffed between 5.13.0 and 5.14.0 in a throwaway venv: **identical** — the
  version string is the only line that differs. All five arguments `nodus_runtime_kwargs()` passes
  (`allowed_paths`, `allow_subprocess`, `allow_network`, `allow_env`, `max_memory_mb`) are present.
- **Read the notes, per `NODUS-UPGRADE-2`: 5.14.0 fixes paths this runtime does not walk.**
  `#862` (a step over budget hung a `nodus serve` request forever — a one-line denial of service)
  and `#857`/`#858` (`--time-limit`, double-run of a self-running program) are `nodus serve` / CLI
  paths with **zero references** in `AINDY/`; we embed `NodusRuntime` directly. `#856` (a coroutine
  spawned through a foreign closure was owned by the wrong VM and dropped) is `spawn`/`run_loop`
  across module VMs. `#855` builds the TLS trust store **once per process** instead of once per VM —
  every guest's first HTTP call had paid ~0.5 s for it, so that is the one change a guest here
  observes.
- **`nodus-mcp` 0.1.4 is the release `MCP-SDK-2X-1` was waiting for** — `NodusServer` now branches
  per `mcp` SDK major at import, and the client adapter reads 2.x's `input_schema` (under 0.1.3
  every tool discovered from a 2.x server arrived with an empty schema and no error). The floor is
  raised to `>=0.1.4` here; the `<2` cap is decided on a test run, not on the release note.

### Changed — the `[mcp]` extra no longer caps `mcp<2` (`MCP-SDK-2X-1` CLOSED, #727)

- **What a green check means changed:** the CI `Install MCP extra` step now resolves to the newest
  `mcp` 2.x, so `Runtime Contracts` exercises `nodus-mcp`'s 2.x branch (`add_request_handler`),
  and no longer its 1.x branch. `nodus-mcp`'s own suite drives both.
- The cap had been right since 2026-07-31: `nodus-mcp` 0.1.2/0.1.3 called `Server.list_tools()`,
  which `mcp 2.0.0` removed, so an uncapped extra was broken at server-construction time. It was
  also hiding a second defect — under 0.1.3 every tool discovered from a 2.x server arrived with an
  **empty schema and no error** (`inputSchema` → `input_schema`).
- Lifted **in both places** the entry names (`pyproject.toml`, the CI step) — a cap fixed in one is
  re-resolved by the other — and **verified by a run, not the release note**: a throwaway venv with
  the runtime's `[test,mcp]` extras forced to `mcp 2.2.0` ran the three MCP suites plus
  `test_quota_accrual_orphan.py`: **43 passed, 0 skipped**, the live SSE round-trip included.
- `tests/unit/test_mcp_sdk_pin.py` deleted, as its docstring instructed. Operators who installed
  `mcp` by hand beside the extra may now move to 2.x; nothing requires it.
