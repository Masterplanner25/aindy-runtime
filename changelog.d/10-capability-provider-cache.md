### Fixed — capability providers ran on every tool check, and a slow one denied tool execution (`CAPABILITY-PROVIDER-TIMEOUT-1`, #466)

`_load_capability_definition_providers` is reached from `get_capability_definitions`,
`get_capability_definition`, `get_capabilities_for_tool` and `get_capabilities_for_agent`, and
therefore from `check_tool_capability` — **the tool-execution path**. Capability providers are
subprocess-isolated, so **every tool capability check spawned a process per provider** and waited
on a 30-second budget.

Under CPU contention that budget was exceeded, the exception was swallowed into a
`logger.warning`, and the capability set came back empty. The observable symptom was tool
execution refused with *"tool 'x' has no registered capability mapping"* — a message that names
the tool and nothing about the cause.

**It fails closed.** `check_tool_capability` refuses a tool whose mapping is missing, so this is
an **availability** problem, not a security one: a slow host stops tool execution rather than
letting anything through. (The guard is conditional — `if not required_capabilities and tool_name
in TOOL_REGISTRY` — so it is now pinned by a test rather than assumed.)

**Measured**, 10 `get_capabilities_for_tool` lookups on an idle machine:

| | subprocess invocations | wall time |
|---|---|---|
| before | **10** | 56.4s |
| after | **1** | 11.4s |

That is ~5.6 seconds of subprocess per tool capability check, paid on every tool call, and it
scaled linearly with the number of checks. The remaining 11.4s is the one cold call.

**Three changes:**

- Each provider's bundle is **cached**, so a provider runs once instead of per check. The bundle
  is still *applied* on every call, so clearing the definition dicts repopulates correctly.
- A **failure is never cached** — a transient timeout is retried on the next call instead of
  persisting for the life of the process.
- The failure logs at **ERROR**, naming what it costs, rather than a warning nobody reads.

**★ The cache lives on the provider object, not in a module global.** A
`_capability_providers_loaded` latch would have to be added by hand to two separate
registry-reset dictionaries, and forgetting either leaves a stale `True` that empties the
capability set permanently — this same bug, reintroduced by its own fix. The provider list is
already reset by both, so a cache attached to the objects inside it is invalidated for free.

**Not done, deliberately:** this surface was *not* added to
`_STATEFUL_IN_PROCESS_CALLBACK_SURFACES`. That set is for callbacks that read live in-process
state a subprocess cannot reconstruct; `runtime_capability_bundle` returns a literal dict and
does not qualify. Moving it there would weaken a documented isolation boundary for a performance
reason.

**Residual:** the first capability lookup in a process still spawns one subprocess per provider,
so a sufficiently contended host can still fail it once — now retried rather than permanent. If
it recurs, `AINDY_RUNTIME_CALLBACK_TIMEOUT_SECS` is the next lever.
