### Fixed — a runtime failure now arrives where it can be read (FR-25 a + c) (#620)

Filed by the app team after two sessions lost to the same shape: the runtime had the message,
computed it, and put it somewhere nobody looks.

- **Every dispatcher error path now logs once, at `WARNING`, from the `_error_envelope` funnel**
  — `[SyscallDispatcher] <syscall> -> error (eu=… trace=…): <message>`, carrying the same
  string the envelope returns. Before this, 7 of the 13 error paths (unknown syscall, unknown
  version, **permission denied**, tenant violation, runtime-owned-call metadata, quota exceeded,
  input validation) said nothing anywhere; the message existed only
  inside a returned dict that a correctly defensive caller (`!= "success"` → return 0) discards.
  Since 2.9.0 `aindy_syscall_outcome_total{status="error"}` told an operator *that* a syscall
  failed; now the log says *why*. The six paths that already logged (with a traceback, or the
  reason a fail-closed quota check failed) are unchanged and do not double-log.
- **A plugin-load failure in `_ensure_tools_loaded` is now a `WARNING` that names the resolved
  manifest and the exception**, instead of `DEBUG`. In the Nodus worker subprocess this is the
  only plugin-load entry point; when it failed, the run silently continued on the runtime-only
  registry and the caller's first symptom was `Unknown syscall` for a name correctly registered
  in the parent. Logged once per distinct failure (the function is re-entered on every tool
  call; repeats stay at DEBUG). The worker's `Unknown syscall` error now appends the recorded
  load failure when there is one — only for that error, and cleared on the next successful load.
  The fallback behaviour itself is unchanged.

**Operator note:** a deployment with a chronic misconfiguration (a caller lacking a capability
it asks for every request, a worker that cannot import the app package) will start emitting
WARNING lines it never emitted before. That is the fix working; the lines name the syscall and
the cause.

FR-25 (b) — `parent_run_id: str` answering 500 rather than 422 on a malformed id — is filed and
not in this release (route contract change).
