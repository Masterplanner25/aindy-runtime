### Changed — `nodus-lang` pinned to 5.0.4 (was 5.0.1) (#488)

**Operators: read this if you have enabled `AINDY_NODUS_WARM_POOL`.** It is off by default and
this is latent for every deployment that left it that way.

- Bumped `nodus-lang` `5.0.1` → `5.0.4` in `pyproject.toml` and `AINDY/requirements.txt`.
- **This is a security fix, not a routine bump.** `nodus-lang <= 5.0.2` bound its
  `GLOBAL_MEMORY_STORE` at **import**, so every `NodusRuntime` constructed in one process shared a
  single guest memory dict. `memory_put`/`memory_get` are guest builtins available to any `.nd`
  script, so one script could read another's stored values. Upstream 5.0.3 gives each runtime its
  own store; sharing is now opt-in.
- **Why it reached the runtime:** `AINDY/runtime/nodus_worker_pool.py` reuses worker processes
  across requests. Its docstring claimed a reused process "never leaks state between runs" on the
  strength of `run_one` rebuilding per-request state — but `run_one` cannot reset a module global
  inside a dependency. **With `AINDY_NODUS_WARM_POOL` enabled on an affected pin, two tenants'
  scripts served by the same warm worker could read each other's guest memory.** The pool is
  opt-in and off by default, so this was latent rather than live. The docstring has been corrected.
- Regression guard added:
  `tests/unit/test_nodus_upgrade_contract.py::test_two_runtimes_in_one_process_do_not_share_guest_memory`,
  mutation-tested against 5.0.1.
- `nodus-mcp` is unchanged at `>=0.1.3` and resolves against 5.0.4, so `aindy-runtime[mcp]`
  remains installable. This retires the second instance of `MCP-SDK-2X-1`, where a
  `nodus-lang<5.0.0` cap in `nodus-mcp` had blocked a nodus major.
