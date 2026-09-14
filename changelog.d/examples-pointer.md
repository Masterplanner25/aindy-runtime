### Removed — `examples/openclaw/`; `examples/README.md` is now a pointer (docs/examples-pointer)

The June 2026 OpenClaw spike ran in-process — a direct `dispatch_syscall` and a self-built
`NodusRuntime(allowed_paths=None)` — bypassing the plugin host, guest floor, tool registry and
`EffectRecord`: the boundary `GUEST-CONFINE-1` closed and `SANDBOX_CONTRACT.md` forbids. As an
example it taught the one pattern the runtime prevents. It targeted nodus 4.0.3, had no inbound
references, and its `schedule_reminder` left the job rows behind `ASYNC-JOB-UNREGISTERED-STORM-1`.
`examples/README.md` points at `docs/tutorials/` (live-verified) and at infinityclaw, states
`SUBSTRATE-WITNESS-1`'s caveat, and sets the bar an example here must meet. In git history if
needed.
