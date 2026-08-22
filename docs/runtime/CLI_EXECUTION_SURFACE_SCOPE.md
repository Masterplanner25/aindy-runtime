---
title: "CLI as an execution surface — scope (CLI-EXEC-SURFACE-1)"
api_version: "1.0"
last_verified: "2026-08-22"
status: current
owner: "platform-team"
---

# CLI as an execution surface — scope

**Scoping only. Nothing here is a decision to build, and the recommendation at the end is
"decide the pipeline question first", not "add these commands".**

This document exists because nine comparative audits examined systems that are *all* driven
from a terminal, and not one of them asked what our own terminal surface is. See
`TECH_DEBT.md` → `CLI-EXEC-SURFACE-1` for the finding; this is the "what would it take" half.

---

## 1. Three CLIs, three different questions

The stack has a CLI at every level. They are not three implementations of one idea — each
answers a different question, and only one of them can be asked to *do* anything.

| Level | Binary | Surface (verified 2026-08-22) | Question it answers |
|---|---|---|---|
| Language | `nodus` | `run` `check` `fmt` `test` `repl` `status` · `init` `install` `update` `add` `remove` `deps` `cache` · `ast` `dis` `debug` `profile` · **`workflow run\|list\|resume\|cleanup`** `goal-run` `graph run` · **`snapshot` `snapshots` `restore`** · `serve` `worker` · `lsp` | *"run this program"* |
| **Runtime** | **`aindy-runtime`** | `init` `serve` `sandbox` `bootstrap-schema` `mcp-server` `memory reembed` `memory prune-cascade-debris` `auth promote-admin` | *"administer this server"* |
| App / agent | `claw` | `start` `stop` `status` `check` `doctor` · `agents list\|add` · `workspace index\|create\|list\|share` · `backup` `restore` · `weave status\|nodes\|connect\|disconnect` | *"run this daemon"* |

**All eight runtime subcommands administer the server. None executes anything.** There is no
`aindy-runtime run`, no `agent run`, no `flow run`, no `syscall`. Verified by reading the
`add_parser` table and the `args.command ==` dispatch block in `AINDY/runtime_only.py`.

The consequence is directional, and it is the finding: **nodus, *below* the runtime, can
execute from a terminal; the runtime cannot.** A person at a prompt who wants agentic work
either stands up HTTP, or drops to `nodus run` — which reaches the interpreter without
passing the dispatcher, the capability token, the effect ledger, the egress guard or the
quota. The terminal path routes *around* the runtime, the same shape as `FLOW-PARALLEL-1`
("apps needing parallelism route around the flow engine") and `GUEST-CONFINE-1`.

---

## 2. The transport already exists — it is just addressed to a machine

`aindy-runtime mcp-server --transport stdio` accepts work over a terminal-native transport and
dispatches it into the kernel. The whole mechanism is four lines
(`AINDY/platform_layer/mcp_server.py:188-196`):

```python
def _handler(args: dict) -> dict:
    from AINDY.kernel.syscall_dispatcher import dispatch_syscall
    ...
    return dispatch_syscall(syscall_name, args or {}, user_id=user_id)
```

`dispatch_syscall` is a plain function call — no HTTP, no ASGI, no server. It builds a
`SyscallContext` granting exactly one inferred capability and calls
`get_dispatcher().dispatch()`. So the gap is **not** "no non-HTTP execution path". It is that
the one we have is spawned by an MCP client rather than typed by a person.

**That is why this is scoping and not invention: the hard part is already built and running.**

---

## 3. ★ The central design question — and the live bug it exposed

**Does a CLI command run inside `ExecutionPipeline`, or beside it?**

Every route handler runs inside `ExecutionPipeline` (`core/execution_pipeline/pipeline.py`):
it sets the trace/pipeline ContextVars, **claims and releases an `ExecutionUnit`**, records
Prometheus metrics, captures memory signals, and emits `SystemEvent` rows. `SyscallDispatcher`
enforces capability, tenant isolation and quota independently, so the tempting conclusion is
that a non-pipeline caller keeps the *kernel* guarantees and loses only the *pipeline* ones.

**That conclusion is wrong, and this section originally stated it.** It was written from
reading the source, labelled "measured", and was corrected only by executing it — the exact
mistake this repo catalogues as *asserting the source, not the behaviour*. What the source
reading produced was "the quota is vacuous for an id-less caller". What running it produced is
the opposite failure. The record of both is kept deliberately.

### The two halves of one lifecycle

- **`SyscallDispatcher` accrues.** Step 4 of every dispatch calls
  `record_usage(context.execution_unit_id, {"syscall_count": 1, "wall_time_ms": …})`
  (`syscall_dispatcher.py:766-771`), which **creates the usage snapshot if absent**.
- **`ExecutionPipeline` reaps.** `mark_completed` is called from
  `core/execution_pipeline/resources.py` and the flow engine's completion/failure paths — and
  **nowhere else**. Verified by grep across `AINDY/`.

So the dispatcher creates usage and the pipeline destroys it. **Call the dispatcher without
the pipeline and you get accrual that is never reaped.**

### What that does to an id-less caller, executed

`mcp_server.py` has **zero** references to `ExecutionPipeline`, and its handler calls
`dispatch_syscall(name, args, user_id=...)` with no `execution_unit_id` and no `trace_id`, so
the context is built with `run_id=""` (`syscall_dispatcher.py:904-910`). Every call therefore
checks and records against the key `""`, in a process-level singleton
(`get_resource_manager()`, double-checked locking at `resource_manager.py:984`).

Run against a real `ResourceManager` with `is_testing` patched off — it is a pydantic
*property*, so it must be patched on the class or `check_quota` short-circuits to `(True,
None)` and proves nothing:

```
call 1: check_quota('') -> (True, None)      # no snapshot yet — the one free call
call 2: check_quota('') -> (True, None)
call 3: check_quota('') -> (True, None)
shared bucket after 3 calls: {'eu_id': '', 'tenant_id': '', 'wall_time_ms': 15, 'syscall_count': 3}

...after 108 accrued syscalls:
check_quota('') -> (False, "RESOURCE_LIMIT_EXCEEDED: eu '' exceeded syscall_count limit (108 > 100)")

liveness control — a normal eu id behaves identically:
  fresh eu, first call: (True, None)
  fresh eu, over cap  : (False, "RESOURCE_LIMIT_EXCEEDED: eu 'eu-1' exceeded syscall_count limit (105 > 100)")
```

Three distinct behaviours, in order:

1. **The first call escapes the quota entirely** — there is no snapshot to exceed.
2. **Every subsequent id-less call shares one global bucket keyed `""`**, accumulating across
   callers, sessions and MCP tools. The per-*execution* budget has silently become a
   per-*process* one.
3. **After `MAX_SYSCALLS_PER_EXECUTION` (100) the bucket trips and never resets**, because the
   only reaper is the pipeline this path does not use. From then on **every** id-less call is
   refused with `RESOURCE_LIMIT_EXCEEDED: eu '' exceeded syscall_count limit`.

`MAX_WALL_TIME_MS` (300 000) accrues the same way and gives a second, slower path to the same
lockout.

### Tracked as its own entry

The bug is **`QUOTA-ACCRUAL-ORPHAN-1`** in `TECH_DEBT.md` — read that for severity, promotion
triggers and the fix constraints (notably: *do not* fix it by early-returning on an empty id).
It is split out because the mechanism is not CLI-specific; MCP is simply the only caller
exercising it today. What stays here is why it decides this document's central question.

### Why this matters beyond scoping

**`aindy-runtime mcp-server --transport stdio` is a long-lived process.** A Claude Desktop
session that makes more than 100 tool calls will hit a hard stop, and the error names an
execution unit that does not exist. Nothing distinguishes it from a real quota breach.

Severity is bounded by adoption, not by design: the MCP server is opt-in, behind the `[mcp]`
extra, read-only by default. **With Redis configured it gets worse, not better** —
`_backend_get_syscalls("")` is a shared key, so the bucket is shared across every instance in
the deployment rather than one process.

**The rule this yields, and it is the whole answer to the section's question:** a caller that
uses `dispatch_syscall` must own an `ExecutionUnit` lifecycle — claim it, and reap it. Not for
metrics. For the quota to have a subject that is *its own* and that someone eventually clears.
A CLI built the obvious way inherits this bug on day one.

## 4. Tiers, in increasing order of what they need

These are separable. Tier 1 is nearly free; tier 3 is a program.

### Tier 1 — `aindy-runtime syscall <name> <json>`

One dispatch, one inferred capability, an envelope on stdout. Reuses `dispatch_syscall`
exactly as `mcp-server` does.

Needs: an identity (§5), an ExecutionUnit (§3), `--json` vs human rendering
(`_format_sandbox_summary` is the precedent), and a **non-zero exit code on an error
envelope** — a CLI whose failure mode is "prints `{"status":"error"}` and exits 0" is unusable
in a script and will be wrapped in `jq` by every consumer.

Note this is a strictly *smaller* grant than `POST /platform/syscall`, which is route-ungated
by decision (`KEY-SCOPE-ESCALATION-1`).

### Tier 2 — `aindy-runtime agent run <objective>`

Not a syscall. It goes through `execute_run()` (`agents/agent_runtime/execution.py`), which
requires `AgentRun.status == "approved"`, a scoped capability token, tool resolution, and the
flow-backed Nodus path.

Needs everything in tier 1, plus **an approval answer.** The approve path deliberately
bypasses `SyscallDispatcher` and has no idempotency gate. A CLI must either refuse to
self-approve (print the run id, exit, let a human approve through the existing surface) or
introduce an explicit `--approve` recorded as an operator decision. **Do not let the CLI
quietly satisfy its own approval gate** — that converts a two-party control into a flag.

### Tier 3 — `aindy-runtime repl` / interactive

Needs tiers 1–2 plus a partial-output surface, which the runtime does not have:
`PROGRESS-CHANNEL-1` records **zero** `StreamingResponse` / `text/event-stream` on any
execution surface. That entry's own text says it was "surfaced only by an *interactive*
comparator" — this is why. **Do not build tier 3 before `PROGRESS-CHANNEL-1`**, and keep its
guard rails: a progress channel carries no authority, constitutes no effect, and must never
become a delivery guarantee.

---

## 5. Identity — the question with no good default

HTTP callers authenticate. A terminal caller has not.

`mcp-server` answers this by fiat: `AINDY_MCP_SERVER_USER_ID`, one configured identity for
every stdio call. That is defensible for a process an operator spawns; it is **not**
defensible as a general CLI default, because `tenant_id == user_id`
(`kernel/tenant_context.py:13`) — so a shared CLI identity is a shared memory namespace and a
shared concurrency cap. That is `INITIATOR-IDENTITY-1` arriving through a new door.

Options, in the order they should be considered:

1. **Reuse the existing auth surfaces** — a platform key or a JWT from the env or a config
   file. No new authority vocabulary, and revocation already works.
2. **Require `--as <email>` plus admin authority**, mirroring `auth promote-admin`, which is
   already an authenticated local admin action.
3. **A single configured identity like `mcp-server`.** Acceptable only for a single-operator
   deployment, and only if the CLI *says so on every invocation*.

**Not an option: a CLI that runs as an implicit superuser because it is local.** Local access
is not authorisation, and `KEY-SCOPE-ESCALATION-1` is what that assumption costs.

---

## 6. What would make this worth building

It is not obvious that it is. The honest case both ways:

**For.** It would be the cheapest possible first-party consumer that is *forced* through the
chokepoints — `SUBSTRATE-WITNESS-1` records that no existing consumer exercises
`execute_tool` / `EffectRecord` / `execution_token` at all, and unlike Claw a CLI cannot route
around them. It would give `EMBEDDED-FLOOR-1` a concrete shape (what does a single-process
terminal consumer actually need?). And it would let a person *feel* `PROGRESS-CHANNEL-1`
rather than infer it from someone else's product.

**Against.** The runtime is a server; "no execution CLI" is a coherent position, not an
oversight. Every tier above adds a second path to something that currently has exactly one,
and this repo's catalogue is mostly second paths that diverged from the first
(`ROUTE-EFFECT-BYPASS-1`, `EGRESS-INPROC-1`, the dispatcher's own second degradation path).

**The tiebreaker is §3, and it is already true regardless of what is decided here:** the
vacuous-quota path exists *today* via `mcp-server`. Fixing that is worth doing whether or not
a CLI is ever built, and doing it first is what would make a CLI safe to add.

---

## 7. Do not

- **Do not add an execution command that skips `ExecutionPipeline`** — §3.
- **Do not let the CLI approve its own agent runs** — §4, tier 2.
- **Do not build tier 3 before `PROGRESS-CHANNEL-1`** — §4.
- **Do not introduce a CLI-specific authority path** — §5. Any new one is a second vocabulary
  for a question `EXEC-ENV-BIND-1` and `HTTP-SCOPE-GAP-1` already ask, which is the mistake
  `FS-SCOPE-1` explicitly records.
- **Do not close `SUBSTRATE-WITNESS-1` with a CLI that only calls read syscalls.** That entry
  asks for a consumer that would *notice* if a guarantee broke.
