### Added — a tool can declare the isolation it needs (TOOL-SEAM-ISOLATION-1 step B)

`register_tool(..., isolation=...)` takes an **assurance class** — `"insecure-dev"`,
`"container-grade-sandbox"` or `"strong-sandbox-tier"` — naming the minimum the host must provide
for that tool to run. `None` (the default, and every existing tool) declares nothing and behaves
exactly as before.

A tool declaring more than the host provides is **refused**, fail-closed, before the handler runs
and before it is handed anything.

#### ★ This declares; it does not confine

A tool that is *allowed* to run still runs **in-process with the process's ambient authority**.
It can still `import os`, spawn a thread, or open a socket. **`TOOL-SEAM-ISOLATION-1` remains
open** — step C is the process boundary and is not built.

Reading a satisfied declaration as confinement would be exactly the *"gated path that does not
actually confine"* failure the scope warns against, so it is stated in the parameter docstring,
the module, and a test that asserts an allowed tool runs in the **same process id**.

#### ★ An assurance class, not a mechanism

The entry originally proposed `isolation="in_process" | "subprocess" | "container" | "strong_vm"`.
That asks a caller to state a *mechanism* the runtime cannot verify — and `in_process` and
`subprocess` are indistinguishable as **assurance**, because a bare subprocess is not a sandbox
and both report `insecure-dev`.

Declaring against `EXEC-ENV-BIND-1`'s existing assurance vocabulary reuses what is already there
instead of growing a second one beside it — the same argument that keeps `FS-SCOPE-1` a field on
that descriptor rather than a peer of `egress_scope`. The runtime owns the *request* vocabulary;
mechanism stays behind the provider boundary.

#### Three properties worth knowing

- **A misspelled class raises at registration**, not at execution and not silently. That is the
  `register_syscall` lesson from `IDEM-11`, where an unforwarded parameter left every plugin
  syscall at the weakest setting with no way to opt in. Downgrading a typo would hand a tool a
  weaker boundary than it asked for — the one direction that must never be quiet.
- **A refusal is an envelope, not an exception.** `execute_tool`'s contract is
  `{success, result, error}` and every caller reads it that way; a refusal that raised would be
  caught by the seam's own broad handler and reported as a tool *failure*, which reads as "the
  tool broke" rather than "this host cannot run it" — the status-code confusion `ROUTE-GUARD-1`
  was. The error names both the requested and the provided class, so an operator can tell a
  misconfigured host from an over-strict declaration.
- **A host-resolution failure refuses.** `_host_assurance` reports the weakest class on any error,
  so a broken provider denies a strict declaration rather than admitting it.

12 tests, mutation-tested **6/6** — including that flipping `>=` to `>`, accepting an unknown
class, or returning success on refusal all go red.
