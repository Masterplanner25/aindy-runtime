### Changed — the two dead extension-registration seams are deprecated (FR-23 ABI half) (#626)

`platform_layer.registry.register_syscall` and `register_agent_tool` are **deprecated** and will
be removed **no earlier than two minor releases after 2.11** (the stable-ABI minimum window).

- Both write to dicts nothing reads: `register_syscall` → `_syscalls`, which the dispatcher never
  resolves against (it uses `kernel.syscall_registry.SYSCALL_REGISTRY`); `register_agent_tool` →
  the static `_agent_tools` model, which `execute_tool` does not resolve against. A handler or
  tool registered through either has never been reachable. (FR-23's metric half, #622, stopped
  `/observability/system` from counting these dead dicts.)
- Both now emit a `DeprecationWarning` naming the replacement, and keep their existing
  operator-facing WARNING log. They still record what they are given, so an out-of-tree extension
  is not broken during the window.
- **Replacements:** register a syscall through `AINDY.kernel.syscall_registry.register_syscall`
  (note: the kernel handler contract is `(payload, ctx)`, while the deprecated seam validated a
  single-parameter handler — it is an adaptation, not a rename); register a tool through
  `register_run_tool_provider` (the provider model apps use) or `agents.tool_registry.register_tool`.
- The `INPROC_CAP_REGISTER_SYSCALL` / `INPROC_CAP_REGISTER_AGENT_TOOL` capabilities remain in the
  audited capability set until removal. `EXTENSION_ABI.md` gains a "Deprecated registration
  functions" section; removal will be announced in the changelog per the ABI deprecation policy.
