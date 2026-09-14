# Examples

**The runnable examples are [`docs/tutorials/`](../docs/tutorials/index.md).** Three of them,
each executed verbatim against a live 2.13.0 server on 2026-09-13, each going through the
runtime's supported surfaces — the SDK / HTTP routes, `POST /platform/nodus/run` for guest
scripts, `sys.v1.*` for everything a script does. Two complete end to end; the third reaches
a documented runtime defect and shows you how to observe it. Start there.

**The real consumer is [infinityclaw](https://github.com/Masterplanner25/infinityclaw)** — a
personal-assistant application that integrates with this runtime through `aindy-sdk`. It is the
thing `examples/openclaw/` used to imitate. Read it with `SUBSTRATE-WITNESS-1` in mind: as of
2026-09-13 its own source touches the runtime in three places (`memory`, `job.submit`,
`event.emit` via the SDK), its HEAD is from 2026-07-03, and its environment carries runtime
1.4.0 against a 2.x floor. It is a consumer, not yet a witness — nothing in it would notice if
the runtime's effect guarantees broke. That entry is where the work to change that lives.

## What was here, and why it is gone

`examples/openclaw/` — "OpenClaw Infinite Weave", a June 2026 spike (CHANGELOG 1.2.0, 2026-06-11;
live-pass fixes in 1.3.1) — was removed 2026-09-13. It ran **in-process**: it imported `dispatch_syscall` directly and built
its own `NodusRuntime(allowed_paths=None)`, an unconfined guest with host filesystem access,
bypassing the plugin host, the guest floor, the tool registry and `EffectRecord`. That is the
boundary bypass `GUEST-CONFINE-1` closed and `docs/runtime/SANDBOX_CONTRACT.md` now forbids —
so as an example it taught the one pattern the runtime is built to prevent. It also targeted
nodus 4.0.3, had no inbound references, and its `schedule_reminder` skill left `openclaw.reminder`
job rows in a development database that, three months later, drove
`ASYNC-JOB-UNREGISTERED-STORM-1`. It is in git history (`git log -- examples/openclaw`) if you need it.

## What an example in this repo must satisfy

If one is added here, it is held to what the tutorials are held to:

- **It goes through a supported surface.** `aindy-sdk` or the HTTP routes; guest code through
  `POST /platform/nodus/run` or a registered flow; tools through `execute_tool`. Never a direct
  `dispatch_syscall` from outside the process, never a self-constructed `NodusRuntime`.
- **It runs on a bare runtime** — no app plugins, no custom nodes — or says exactly which
  consumer it needs.
- **It has been run.** The `last_verified` date is the date someone executed it against a live
  server and read the output, and every Nodus block in it parses on the pinned interpreter.
- **It is checked by CI.** Put its prose under a folder `Runtime Docs Validation` covers (the
  tutorials are; this `README` is not, deliberately — it is a pointer, not a document that can
  drift).
