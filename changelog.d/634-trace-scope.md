### Changed — `trace_scope()`: establish a trace id for a block, never for the rest of the thread (#634)

- `AINDY.platform_layer.trace_context.trace_scope(trace_id=None)` — the token-holding form of
  `ensure_trace_id`: an ambient trace is reused untouched; an absent one is set for the block and
  reset on exit. `ensure_trace_id` is unchanged (app flow nodes call it where a trace is always
  already set) but now documents that its establish half is never released — on a scheduler
  thread that pins the thread's `trace_id` for every later unit of work, which is what
  `PersistentFlowRunner.start()` did until #633.
- `agents/runtime_api.py`'s two `ensure_trace_id` sites now use `trace_scope`. No observable
  change today — their only caller is the agent route, where the middleware already owns the
  trace — so this closes the latent shape, not a live bug.
