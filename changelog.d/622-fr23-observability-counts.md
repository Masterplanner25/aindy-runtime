### Fixed — `/platform/observability/system` no longer reports 0 syscalls and 0 tools on a live deployment (FR-23) (#622)

- `registry.syscall_count` now counts `kernel.syscall_registry.SYSCALL_REGISTRY` — the
  registry the dispatcher resolves against — instead of `platform_layer.registry._syscalls`,
  a dict **nothing dispatches from** (every app registers through the kernel path). On a full
  app boot it read **0** while ~90 were live.
- `registry.tool_count` now counts `agents.tool_registry.TOOL_REGISTRY` — what `execute_tool`
  resolves against — instead of the static `register_agent_tool` model, which no app uses. It
  read **0** while 16 tools were live.
- New field `registry.run_tool_provider_run_types` (list of run types with a registered tool
  provider), so the tool model apps actually use is visible on the surface. Additive; no
  existing key changed name or type.
- `platform_layer.register_syscall` now logs a **WARNING** naming the reachable path. It is a
  capability-gated in-process ABI entry that validates a handler and stores it where dispatch
  never looks — it accepted work silently. It is not removed here (that is an ABI decision,
  filed as FR-23's open half); it just stops being silent. Its handler contract is also
  single-parameter, unlike the kernel's `(payload, ctx)`, which is the tell that the two were
  never one registry.

**Operator note:** an app that registers through `platform_layer.register_syscall` will see
one WARNING per registration at boot. That app's syscalls have never been callable; the line
says where to register them.
