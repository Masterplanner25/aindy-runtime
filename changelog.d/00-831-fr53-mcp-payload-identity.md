### Fixed — a payload `user_id` written outside the runtime can no longer choose the user a syscall acts on; `mcp-server` serves app syscalls (#831)

- **Read before upgrading if you run `aindy-runtime mcp-server` or Nodus scripts that call `sys()`.**
- **Over MCP and from a Nodus script's `sys()`, a payload could name any user.** The dispatcher
  checked that the caller had an identity but never compared it with a `user_id` in the payload,
  and app handlers read the payload's. An outside AI over MCP (cross-tenant under multi-tenant SSE),
  or a Nodus script calling `sys()`, could therefore read whichever user it named. The Nodus seam's
  `setdefault` kept a script-supplied id on purpose. Now the MCP server, the Nodus `sys()` seam and
  `POST /platform/syscall` fill an absent `user_id` with the caller's and **refuse** a different one
  (`TENANT_VIOLATION`, `failure_class: permission`; 403 on the route). A script that passes its own
  run owner's id is unaffected (FR-53 (b), DEC-099). In-process dispatch is not yet checked
  (DEC-100).
- **`mcp-server` never loaded plugins**, so every app syscall in `AINDY_MCP_SERVER_TOOLS` was
  skipped as "not registered". It now loads the manifest selected as at boot (`AINDY_PLUGIN_MANIFEST`
  / `AINDY_BOOT_PROFILE`), with plugin output sent to stderr (stdout is the stdio channel). **A
  plugin that fails to load now stops the server** instead of serving fewer tools than you configured
  (FR-53 (a), DEC-101). Runtime-only selects an empty manifest, so nothing changes there.
- `register_syscall`'s docstring now states that name-inferred grants (MCP, `sys()`) reach only a
  syscall whose declared capability matches its name (FR-53 (c)).
