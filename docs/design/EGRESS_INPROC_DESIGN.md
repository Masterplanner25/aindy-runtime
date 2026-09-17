---
title: "Egress Enforcement Re-homing — Design"
api_version: "1.0"
last_verified: "2026-09-17"
status: current
owner: "platform-team"
---

# `EGRESS-INPROC-1` — the egress decision is made once and enforced where the tool runs — design

**DESIGN ONLY — nothing shipped. Proposal under `AGENT_WORKING_RULES.md` §8 (a change to where an
authority decision is enforced).** The entry says *"a re-homing, not a build: fold the egress
decision into `TOOL-SEAM-ISOLATION-1`'s provider"*. §1 finds the thing the entry did not say
and which makes it more than cosmetic; §2 separates decision from enforcement, which is the
whole design; §4 is what not to build.

---

## 1. The finding, verified — and one defect the entry did not name

`egress_guard.py` wraps `socket.getaddrinfo` / `socket.connect` under a ContextVar scope; its
own docstring names both bypasses (a thread that does not inherit the contextvar; a native
resolver). It is off by default (`AINDY_EGRESS_ENFORCEMENT`). All true at HEAD.

**★ The defect beside it:** `execute_tool` computes the domain allowlist (`_egress_domains`,
`tool_registry.py:900–995`) and enters `egress_scope` at `:1196` — *around the in-process
call only*. The **isolated** branch returns at `:1181–1193`, before that `with`. So:

> A tool that declared `isolation=` — the one the runtime distrusts enough to move out of
> process — runs with **no egress enforcement at all**, flag on or off. The allowlist is
> computed for it and dropped on the floor. The in-process tool, which asked for nothing, is
> the only one the guard ever wraps.

Not a bypass of the guard; the guard is never installed where that tool runs. This is
`CANCEL-REACH-1` residual 2's shape ("the isolated branch RETURNED before the check") on the
egress axis, and it is why this is a re-homing: **the decision is already made at the right
place; the enforcement is attached to the wrong branch.**

Two more facts that shape the answer:

- `ExecutionEnvironmentSpec.authority.network` exists (`execution_environment.py:107`,
  `none | scoped | open`), is clamped to the tool floor, and `subprocess_confinement` already
  turns it into `allow_network` (`:467`) — for the **guest** worker. The tool worker's
  `_worker_confinement` (`tool_registry.py:437`) builds `env=`/`cwd=` from the same spec and
  **ignores `authority.network`**, because `:406` is honest that a bare subprocess cannot
  enforce it.
- `NET_SCOPED` has no domain list on the spec; the list lives on the capability policy
  (`get_capability_policy(cap).domains`). Two vocabularies for one question — the entry's
  "fold" is folding these.

---

## 2. Decision at `execute_tool`; enforcement per provider

**The decision** — *this call may reach {domains} / nothing / anything* — is made once, at
`execute_tool`, from what already exists: the capability policies' `domains` (the allowlist)
and the effective spec's `authority.network` (the mode). Resolution: `none` → no egress;
`scoped` → the policy domains (empty set = no egress, fail-closed); `open` → unconstrained.
The result is an `EgressDecision(mode, domains)` computed **before the isolation branch**.

**The enforcement** is whatever the provider that runs the tool can honestly do, and the
provider reports which mechanism applied:

| Provider | Mechanism | Reported as |
|---|---|---|
| in-process (undeclared tool) | `egress_scope(domains)` — the socket guard, as today, with its two documented bypasses | `env_applied.network = "socket_guard"` |
| tool worker subprocess (`isolation=` declared) | the worker **installs the same guard at startup** from the decision carried in its request payload (`{"egress": {"mode", "domains"}}`), before resolving the function — the ContextVar-inheritance bypass disappears (the guard is process-global in the worker, not scoped), the native-resolver bypass remains | `env_applied.network = "socket_guard:worker"` |
| container runner (`create_sandbox_runner`, when the tool seam gets one — `EXEC-ENV-BIND-1`'s root) | `--network none` for `none`; a proxy allowlist for `scoped` | `env_applied.network = "netns"` |

- **`AINDY_EGRESS_ENFORCEMENT` stays the switch and stays default-off.** This design changes
  *where* the decision is enforced when it is on, not whether. Flipping is `MEB-2b`'s soak, not
  this entry.
- **`env_applied` is the honesty channel.** `EXEC-ENV-BIND-1` established that a declaration
  reports what bound (`resources_enforced`); `network` joins it. A consumer reading
  `socket_guard` knows the two bypasses apply; `netns` means they do not. **A tool that
  declares `authority.network="none"` on a provider that can only offer `socket_guard` is
  told so, not refused** — refusing would make every declared tool fail on every host without a
  container runner, which is every host today.
- **The worker gets the decision, not the policy.** The parent resolves capabilities → domains;
  the worker never consults `capability_service` (it has no db and re-checking authority in the
  distrusted process is the thing `tool_worker.py:11` forbids). The payload key is additive.

---

## 3. What this does not touch

`egress_guard.py`'s mechanism (the socket wrap, the raw-IP refusal, the fail-closed empty set);
`MEB-2a`'s static arg inspection; the guest worker's `allow_network` (already carried, already
enforced by nodus); `FS-SCOPE-1` (the filesystem half of the same descriptor, separate entry).

## 4. What not to build

- **Not a second egress vocabulary on the spec** (a `domains` list beside `authority.network`)
  — the policy owns the list; the spec owns the mode. Folding means one *decision*, not one
  *field*.
- **Not re-evaluating policy inside the worker** — `tool_worker.py`'s standing rule.
- **Not a refusal when the provider cannot enforce `none`** — report; the container runner is
  the enforcement upgrade and it is a separate re-homing (`EXEC-ENV-BIND-1`'s root note).
- **Not wrapping `threading.Thread`** to close the contextvar bypass in-process — the guard's
  own docstring declines it; the worker-global install closes it for the isolated case instead.

## 5. Decisions this design asks for (`DEC-NNN` in the implementing PR)

1. The egress decision `(mode, domains)` is resolved once at `execute_tool`, before the
   isolation branch, from capability-policy domains + the effective `authority.network`.
2. The tool worker installs the socket guard process-globally from the decision in its
   request payload; it never reads policy.
3. `env_applied.network` reports the mechanism that applied (`socket_guard`,
   `socket_guard:worker`, `netns`, `none`); a provider that cannot enforce a declared mode
   reports rather than refuses.
4. `AINDY_EGRESS_ENFORCEMENT` remains the switch, default off.

## 6. Tests

- **The finding, first:** flag on, a tool with a domain policy and `isolation=` declared →
  the worker resolves a non-allowlisted host successfully at HEAD (red-first); after → refused
  inside the worker, error names the host.
- In-process tool unchanged: allowlisted host resolves, other refused (existing
  `test_egress_guard` suite).
- Worker: a tool spawning a bare `threading.Thread` that resolves → refused in the worker
  (the contextvar bypass closed there), still open in-process (pinned, with the docstring).
- `env_applied.network` reads `socket_guard:worker` on the isolated path, `socket_guard`
  in-process, `none` when the flag is off.
- Mutation: drop the payload key → the first test goes red.
