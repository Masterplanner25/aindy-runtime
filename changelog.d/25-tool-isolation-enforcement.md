### Added — a tool that declares isolation now runs out of process (TOOL-SEAM-ISOLATION-1 step C2)

Steps A and B narrowed one argument and let a tool *declare* a boundary. **This is the first thing
in this entry that applies one.**

A tool registered with `register_tool(..., isolation=<assurance class>)` executes in a one-shot
worker subprocess (`python -m AINDY.agents.tool_worker`) instead of in the runtime process.
**Opt-in per tool** — a tool that declares nothing is unaffected and keeps running in-process,
because a subprocess round-trip per call is real latency and must not be imposed on everything.

`AINDY_TOOL_ISOLATION=0` reverts to declare-and-refuse only: the declaration is still validated
and still refused when the host cannot meet it, but it is not applied.

#### ★ There is no fallback, and that is the design

A worker that **crashes, times out, or cannot be spawned means the tool does not run.**

This is deliberately the opposite of the Nodus adapter, which spills a warm-pool failure to a
fresh subprocess. There, both paths give the *same* guarantee and falling back is strictly better
than failing. Here they do not: falling back would execute a tool that asked to be confined
**unconfined** — precisely the *"gated path that does not actually confine"* failure this entry
exists to prevent. Mutation-tested: making a failed worker fall back goes red.

#### Three constraints worth knowing before declaring `isolation=`

- **`db` is `None` in the worker.** A session cannot cross a process boundary. This is safe by
  measurement rather than hope — all 18 tool functions take `db` and **none uses it** (step A). A
  tool that needs data reaches through a syscall, which is what every app tool already does.
- **A worker rebuilds `TOOL_REGISTRY` from the plugin stack.** A tool registered ad hoc in the
  parent is invisible there, and the worker says so specifically rather than failing generically —
  a registry mismatch is a deployment problem and a generic error would send an operator to the
  wrong place.
- **A non-marshalling return FAILS here**, where the in-process seam only counts it (step C1).
  In-process the effect has landed and rejecting would discard it; in a worker the value cannot
  cross the pipe, so there is nothing to carry back. That is exactly why C1's counter exists:
  check `aindy_tool_return_contract_violations_total` before declaring isolation on a tool.

#### Authority is not re-evaluated in the worker

The parent's `execute_tool` checks token, granted tools, capabilities, policy, rate limit, egress
and secret scope **before** delegating; the worker resolves the function and runs it. Re-checking
inside would put the authority decision in the very process the boundary distrusts — and calling
`execute_tool` there would recurse, since it routes declared tools to a worker.

12 tests, mutation-tested **7/7**, including a real `python -m AINDY.agents.tool_worker`
round-trip that proves the module is executable and that nothing else writes to stdout to corrupt
the response frame.
