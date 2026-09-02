### Changed — `nodus-lang` 5.1.0 → 5.9.0 (all three pin sites)

Eight releases. Read this before upgrading a deployment that runs guest Nodus code: three of the
fixes in the gap are **security** fixes, and none of them was obvious from the version distance.

- **A capability policy could be bypassed by spelling a call differently** (upstream #616).
  `agent_call` is governed by the `agent.call` capability; `agent_call_async` carried no
  capability at all, so a `DenyList("agent.call")` refused one spelling and permitted the other on
  the same agent under the same policy. Seven builtins could also be shadowed by a host
  `register_function` — including `chr` — because the "cannot override a builtin" guard read a
  hand-maintained name set that had drifted from the VM's actual dispatch table by seven entries.
- **A relocated workflow store fell outside the guest filesystem floor.** `DEFAULT_FLOOR` decided
  what counted as the runtime's own state by matching a literal `.nodus` path segment, so the
  *supported* way to move the store also moved it out of the jail — a guest write of
  `../relocated/pwned.txt` landed in the live run store while the same write to the default
  location was denied.
- **A graph response could name another request's graph** (upstream #584) — id, status and full
  task map including step return values. A cross-request leak on any server handling more than one
  caller, not merely a wrong label.
- **The bytecode cache could run a stale program.** The cache key was `sha256(abspath + mtime_ns)`,
  so any edit landing inside the platform's mtime resolution was invisible and the previous program
  ran. Entries now carry a hash of the source bytes.
- Plus closure-across-module fixes (#691, #696) that made a module-exported factory function or a
  callback passed into a step body execute against the wrong chunk — with five different symptoms
  depending on what happened to sit at that address, including silently running nothing.

**No runtime code change was needed.** Verified rather than assumed: all eight host functions this
runtime registers are still accepted (the tightened builtin-shadowing guard refuses none of them),
`NodusRuntime.__init__` still takes no `**kwargs` so a renamed confinement flag would raise rather
than silently unconfine a guest, every confinement argument it is given still exists, and the
`[mcp]` extra still resolves (`nodus-mcp` 0.1.3 requires `nodus-lang>=4.0.0` unbounded).

One upstream change is a **breaking** change that does not affect this repo: a named import of a
builtin name (`import { sleep } from "./mod.nd"`) is now refused rather than silently ignored. No
`.nd` source here uses that form.
