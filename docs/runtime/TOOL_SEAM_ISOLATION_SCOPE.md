---
title: "Tool Seam Isolation — Scope (TOOL-SEAM-ISOLATION-1)"
api_version: "1.0"
last_verified: "2026-08-19"
status: current
owner: "platform-team"
---

# Tool seam isolation — scope

**Status: SCOPE. No code, no design commitment.** `TOOL-SEAM-ISOLATION-1` has been cited by three
independent comparative audits and carries accumulated recommendations from four more. This
document measures the seam against source at `03d5a87` before any of that is acted on, because
several of the accumulated claims turn out to be wrong in ways that change what should be built.

Read the `TECH_DEBT.md` entry for the argument. This document is only what is **true at HEAD**.

---

## 1. What the entry gets right, verified

| Claim | Verified |
|---|---|
| The chokepoint is `agents/tool_registry.py:366` — `entry["fn"](args=args, user_id=user_id, db=db)` | ✅ exact line |
| Exactly three call sites | ✅ `extension_worker.py:345`, `nodus_adapter.py:263`, `nodus_worker.py:144` (entry says `:135`; drifted) |
| `create_sandbox_runner` is bound to the plugin host | ✅ the only **execution** sites are `plugin_host.py:346` and `:816`; `deployment_contract.py` and `sandbox_certification.py` only read `.metadata()` |
| Every authority check precedes an unconstrained call | ✅ token, tools, capabilities, policy, rate limit, egress, secret scope — all resolved before the call, none binding on it |

The structural finding stands. What follows are four corrections that change the *shape* and the
*urgency*, not the existence, of the gap.

---

## 2. ★ Correction 1 — a tool is a Python callable, not a command

This is the one that matters most, because the entry records the application mechanism as
**settled**: a `codex-rs`-style *command transform*, "one spawn path; the isolation rides in argv."

**There is no argv.** `register_tool(...)` stores a Python function object:

```python
TOOL_REGISTRY[name] = {"fn": fn, "risk": ..., "capability": ..., ...}
```

and the chokepoint calls it directly. A command transform has nothing to transform.

The transform idea is not wrong — it is **one level of indirection off**. To confine a Python
callable the runtime must first put it somewhere it can wrap:

```
   what codex-rs transforms          what this runtime would transform
   ────────────────────────          ────────────────────────────────
   the tool's own argv          →    the argv of a WORKER that will import
                                     and run the tool by name
```

That worker is not hypothetical — `SandboxRunner` already spawns exactly that shape
(`sandbox_runner.py:1284`: `[sys.executable, "-m", "AINDY.platform_layer.extension_worker", "--host"]`).
So the borrow still holds, but the delta is **"a per-invocation tool worker + a policy-to-argv
transform"**, not "a transform at line 366". Anyone reading the entry as *"slot a transform in
immediately before that call, needs no backend implementations at all"* will start from a false
premise.

**Consequence for cost:** the work is a serialization boundary (tool name + args + result +
errors, across a process) before it is an isolation problem. That boundary is the expensive part
and the entry does not mention it.

---

## 3. ★ Correction 2 — the three call sites are in three different processes

The entry says the tool runs *"in the runtime process, handing the tool the live database
session."* True of **one** of the three.

| Call site | Process it runs in | Already confined? |
|---|---|---|
| `extension_worker.py:345` | **inside the Tier-2 sandbox** — this module *is* what `SandboxRunner` spawns | ✅ by the selected runner |
| `nodus_adapter.py:263` | **the host/API process** (imported by `nodus_execution_service`) | ❌ **this is the unconfined one** |
| `nodus_worker.py:144` | the Nodus worker subprocess | partially — separate process, but full ambient Python authority |

So the seam is not uniformly unconfined, and **the sharpest instance is one call site, not three**.
An extension that invokes a tool is already inside a sandbox and is calling *back out* to run it —
which is its own question (a confined caller delegating to a less-confined executor) and arguably
more interesting than the one filed.

**This also re-sizes the fix.** Confining the `nodus_adapter` path is the whole P0 in practice.
The other two want the *vocabulary* (so the confinement they already have is declared and
recorded) far more than they want new machinery.

---

## 4. ★ Correction 3 — no foreign code runs at this seam today

The entry's risk statement is *"foreign code executes unconfined."* Measured at HEAD:

| Source | Count | What the `fn` actually is |
|---|---|---|
| Runtime-owned (`runtime_agent_defaults.py`) | **3** — `memory.recall`, `memory.write`, `runtime.selftest` | first-party |
| MCP (`mcp_client.py`) | dynamic | **a runtime-owned proxy** (`_make_tool_fn(url, name, timeout)`) — the foreign code runs on the remote server, not in-process |
| App-registered (`aindy-apps-monolith`) | **20 registrations / 15 fns** across 8 apps | first-party; thin adapters that dispatch back through `invoke_tool_syscall` |

**Zero third-party in-process tools exist.** The MCP path — the one that sounds most like foreign
code — is the *least* exposed, because the boundary is already a network call to another process.

**This does not make the entry wrong; it makes it structural rather than live.** The exposure is
*"the runtime cannot bound what a consumer registers"*, which is a real substrate obligation.
It is not *"untrusted code is running unconfined right now"*, and the difference should decide how
much is built before there is a consumer to serve. Pair with `SUBSTRATE-WITNESS-1`: no first-party
consumer routes effects through `execute_tool` at all.

---

## 5. ★ Correction 4 — the `db` pointer is passed to everything and used by nothing

The entry's cheapest step (Linux Lesson 10) is *"stop passing a pointer"* — replace the live
`Session` with a revocable, validatable handle. It flags this as independently shippable.

**Measured across all 18 tool functions that exist — 3 runtime-owned and 15 in the app monolith:**

```
tool fns taking `db` in their signature : 18
tool fns that reference `db.<anything>` :  0
```

**Not one tool uses the session it is handed.** The parameter is pure ambient authority — maximum
exposure, zero utility.

That makes this the same measurement `GUEST-CONFINE-1` made before denying its three capabilities,
with the same conclusion: **the narrowing breaks nothing that exists.** It is cheaper than the
entry assumes, and it is the only part of this item that can ship without a serialization
boundary, a provider negotiation, or a consumer to justify it.

---

## 6. What is independently shippable, cheapest first

| # | Step | Depends on | Breaks today? |
|---|---|---|---|
| **A** | ✅ **SHIPPED 2026-08-19** — the tool receives a `RevocableToolSession`, revoked in a `finally` when the call returns. Parameter name unchanged, so no tool signature moves | nothing | **no** — measured: 0 of 18 use it |
| **B** | `register_tool(..., isolation=…)` as a **declaration only** — recorded on the `ExecutionUnit`'s `env_spec`, refused when the host cannot satisfy it, applied by nobody | `EXEC-ENV-BIND-1` ✅ shipped | no — declaration is inert |
| **C** | A per-invocation **tool worker**: serialize `(tool_name, args)` out of process, run it, return the result | the serialization boundary in §2 | yes — changes how every tool runs |
| **D** | The policy→argv **transform** wrapping C's worker (`bwrap` / `sandbox-exec` / restricted token) | C | no further — C is the behaviour change |

**A and B are worth doing on their own merits. C is the real cost and it should wait for a
consumer that needs it** — which is `SUBSTRATE-WITNESS-1`'s decision, not this entry's.

---

## 7. Recommendation

**A is shipped. Declare B next. Sequence C behind a named consumer.**

- **A** is a measured no-op that removes the single widest piece of ambient authority at the seam,
  and it is the only step whose value does not depend on anything else landing.
- **B** completes the vocabulary story: `EXEC-ENV-BIND-1` gave execution units a way to declare an
  environment; letting a *tool* declare one costs a keyword argument and makes the seam's
  requirement recordable and refusable before anything can apply it. It also gives `FS-SCOPE-1`
  its enforcement *point* on paper, which is what that entry has been waiting for.
- **C** is a process boundary around every tool call. Its cost is real and its benefit is about
  code that does not exist **yet**.

  **★ Correction (owner, 2026-08-19): "nothing currently sends traffic through this path" and
  "we are not going to" are not the same statement, and the first must not be used to argue the
  second.** An earlier draft of this section leaned on the absence of traffic as though it settled
  the question. It does not: it is a fact about today, and every consumer is owner-controlled, so
  the traffic is a decision not yet taken rather than a constraint. **This is the second time that
  substitution has been made in this repository** — `SUBSTRATE-WITNESS-1` carried the same wording
  ("the flag backlog is stuck because no first-party consumer sends any") and was corrected the
  same day. Treat the pattern as a known failure mode when reading any entry whose priority rests
  on current usage.

  What survives the correction is narrower and still holds: **C should be sequenced behind a
  named consumer, because its shape depends on what that consumer registers** — a shell-out, an
  eval and a plugin loader do not want the same boundary, and building before knowing which one
  risks building the wrong one. That is a sequencing argument, not a "no".

**★ The honest counter-argument, recorded so it is not lost:** the Aider portability analysis
attaches exactly one safety precondition to routing an external agent through this runtime — *do
not route a shell-out through the tool seam until the isolation provider is wired*, because *"a
gated path that does not actually confine would be worse than the status quo it replaces."* That
is correct and it is the trigger for C. **C becomes urgent the moment any consumer registers a
tool that executes what it was given** — a shell-out, an eval, a plugin loader. Until then it is
building ahead of the requirement.

---

## 8. What not to do

- **Do not model isolation as a polymorphic execution ABC.** Three runners already exist; an ABC
  would make the runtime grow N execution environments it does not have. The entry is right about
  this and it survives the corrections above.
- **Do not absorb platform mechanism.** Seatbelt, Landlock, seccomp and OCI stay behind the
  provider boundary, as `SandboxRunner` already establishes. The runtime owns the *request type*
  and its policy vocabulary, nothing below it.
- **Do not treat A as a substitute for C.** A tool holding a scoped session can still `import os`,
  spawn a thread, or open a socket. A narrows one argument; C bounds a process. Shipping A and
  calling the entry closed would be exactly the "gated path that does not actually confine"
  failure the precondition warns about.
- **Do not read the sandbox-escape gate as covering this.** That suite certifies the Tier-2
  extension sandbox and passes 17/17 on every tag; the in-process tool seam has never been in its
  scope. Both statements are true at once.

---

## 9. Open questions for whoever takes C

1. **What crosses the boundary?** `args` and the result must serialize. Today a tool returns
   arbitrary Python. The syscall path already solved this (declared input/output schemas); the
   tool path has no equivalent, and inventing one is most of the work.
2. **What replaces `db` on the far side?** A worker cannot be handed a session. Every tool that
   needs data would have to reach back through a syscall — which is what the app's tools already
   do, so the migration may be smaller than it looks. **Confirm before assuming**: §5's
   measurement says none of them use `db`, which is evidence they already route through syscalls.
3. **Does the extension path double-confine?** `extension_worker` already runs inside a sandbox
   and calls back to the host to execute tools. Routing that through another worker nests two
   sandboxes. Decide whether the extension path opts out, or whether the callback is the thing
   that should be removed instead.
4. **Where does the policy come from?** `EXEC-ENV-BIND-1`'s descriptor is per-execution-unit; a
   tool declaration is per-tool. The effective policy is their intersection, and the clamp rule
   already settled in that design (`narrow only, never widen`) should apply unchanged.
