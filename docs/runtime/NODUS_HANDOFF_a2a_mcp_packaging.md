---
title: "Nodus — A2A and MCP Packaging Handoff"
api_version: "1.0"
last_verified: "2026-08-19"
status: current
owner: "platform-team"
---

# Nodus — A2A and MCP packaging handoff

Written from `aindy-runtime` for whoever is working in the Nodus ecosystem. **Every fix here is
Nodus-side**; nothing in this document is work for the runtime. It is written down because the
runtime is what surfaced it, and because one item is a **live-package hazard** that gets more
expensive the longer it sits.

Companion to `NODUS_HANDOFF_v5.0.1.md`, which covers the language pin itself.

---

## 0. What is actually published, measured 2026-08-19

Against PyPI, not from memory:

| Package | Live version | What it is | `aindy-runtime` depends on it? |
|---|---|---|---|
| `nodus-lang` | **5.0.4** | language + embedded runtime | **yes** — `==5.0.4` |
| `nodus-mcp` | **0.1.3** | *"bidirectional client + server"* | **yes** — `>=0.1.3` |
| `nodus-mcp-server` | **0.1.12** | *"MCP server powered by the Nodus runtime"* | no |
| `nodus-a2a` | **0.1.0** | *"coordination: registry, delegation, dead letter, watchdog"* | no |

Two of the four have no consumer in this runtime, and that is fine on its own. What follows is
where they collide with each other.

---

## 1. ★ BLOCKING — two different packages both claim `nodus-a2a` at `0.1.0`

**This is the item to action.** It is not tidiness; it is a hazard against a package that is
already published.

```
PyPI, live:      nodus-a2a 0.1.0   →  coordination: registry, delegation, dead letter, watchdog
                                      (source: C:\dev\nodus-a2a)

Unpublished:     name    = "nodus-a2a"
                 version = "0.1.0"   →  A2A 1.0.0 (Linux Foundation) HTTP+JSON wire adapter
                                      (source: C:\codev\a2a-wire-pub)
```

**Same name, same version, entirely different library.** PyPI refuses a same-version re-upload, so
the wire cannot ship as-is. And shipping it under a *bumped* version would be worse than being
blocked: `pip install nodus-a2a` would begin returning a different library, and anyone with the
coordinator installed would be upgraded onto an HTTP wire adapter that shares none of its API.

### Recommended resolution — the shape that already worked once

`nodus-mcp` solved exactly this problem by putting **client and server in one package** and
describing itself as *"bidirectional client + server."* A2A should follow it rather than invent a
second answer:

> **One `nodus-a2a`.** Coordinator in the base package; the wire behind an extra —
> `pip install nodus-a2a[wire]` — so the HTTP/transport dependencies stay optional for consumers
> who only want the in-process coordinator.

That is the same shape as `aindy-runtime[mcp]`, it keeps the published name meaning what it
already means for existing installs, and it does not lose the wire code.

**The alternative** — publishing the wire as `nodus-a2a-wire` — is also fine and is a smaller
change. What is **not** fine is either package taking the other's name.

### Not a separate repo

`C:\codev\nodus-a2a-wire` is a **linked worktree** of `C:\codev\a2a-wire-pub` — its `.git` is a
55-byte pointer and every tracked file is byte-identical. It is a checkout, not a second project.
Removing it costs nothing and removes one source of "which one is current?".

---

## 2. ★ The wire caps `nodus-lang` below the current major — third instance of a known pattern

```toml
# C:\codev\a2a-wire-pub/pyproject.toml
dependencies = ["nodus-lang>=4.0.0,<5.0.0"]
```

`aindy-runtime` pins `nodus-lang==5.0.4`. So `pip install nodus-a2a` (wire) alongside this runtime
is `ResolutionImpossible` **before** any integration question is reached.

**This is the third time a first-party Nodus package has capped a fast-moving first-party
dependency below our pin.** The runtime tracks it as `MCP-SDK-2X-1`:

1. `nodus-mcp 0.1.2` built against `mcp` 1.x, capped `<2` — still live and still correct.
2. `nodus-mcp` capped `nodus-lang<5.0.0`, which blocked the 5.0.0 adoption entirely until
   **`nodus-mcp 0.1.3` floated it to `>=4.0.0` unbounded.** That fix is the precedent.
3. The A2A wire now carries the same `<5.0.0` cap.

**The ask: float it to `>=4.0.0` (unbounded), as `nodus-mcp 0.1.3` did.**

The general rule, learned the expensive way on instance 2: **a prophylactic upper cap on a
fast-moving first-party dependency turns every major into a two-repo release train.** A cap
against a *third-party* SDK with a known breaking change (`mcp<2`) is sound. A cap against
`nodus-lang`, by a package in the same ecosystem, mostly buys a coordination problem.

---

## 3. `nodus-mcp-server` duplicates `nodus-mcp` rather than composing with it

Lower priority — nothing is broken, and it has a real consumer.

`nodus-mcp` 0.1.3 already ships `client.py`, `server.py`, `server_transport.py` and describes
itself as bidirectional. `nodus-mcp-server` 0.1.12 solves the same problem again:

```toml
# nodus-mcp-server
dependencies = ["nodus-lang>=4.0.5", "mcp>=1.0.0"]   # NOT nodus-mcp
```

It depends on the language and the MCP SDK **directly, never on `nodus-mcp`** — so the two are
parallel implementations, down to `nodus-mcp-server` carrying its own `memory_store.py`.

**Its consumer is the CrewAI showcase**, which documents desktop-client MCP as riding on *"the
separate `nodus-mcp-server` (#21)"*. So consolidating is a **deprecation with a migration note**,
not a deletion — anyone who followed those instructions has an install path that would change.

`aindy-runtime` has no dependency on it; the only mention here is a `TECH_DEBT.md` line already
noting exactly that.

**Suggested shape if it is taken up:** fold the four modules into `nodus-mcp` (it already has the
server half), keep the `nodus-mcp-server` console script as a thin alias for a release or two, and
update the showcase. Not urgent.

---

## 4. What the runtime would need from the wire, if A2A is ever wired up

Recorded so the packaging decisions above are made with the eventual integration in view. **None
of this is a request** — the runtime has not committed to A2A, and `TECH_DEBT.md`'s `ECOGAP-4`
still has it out of scope.

**The good news, and it is genuinely good:** the wire is already factored for host reuse.

- **`A2AHttpServer(config, invoke, tool_names, tools)`** takes `invoke` as a plain
  `Callable[[str, dict], object]`. It is **not** coupled to `NodusRuntime` — the README's "wired
  to `ToolRegistry.invoke()`" is the intended usage, not a constraint. A host can point it at its
  own tool executor.
- **`handle_request(...)` is a pure function** returning `(status, headers, body)`; the tests call
  it directly without starting a server. So a host can **mount the protocol without adopting the
  transport** — which for this runtime is the difference between an A2A surface inside its
  execution pipeline and scope enforcement, and a second HTTP server beside them.

Please keep both properties through any refactor. They are what make the package adoptable.

**Three things a host would still have to solve on its own side**, listed so they are not mistaken
for gaps in the wire:

1. **Message-only is a real boundary, not a bug.** Task management returns `501` by the v0.1 D5
   decision. A host whose primary unit is a durable task can expose **tools** over A2A but not
   **runs**.
2. **`token_validator: Callable[[str], bool]` authenticates the connection, not the peer.** A bool
   cannot say *who*. Every remote caller collapses to one identity — which this runtime tracks as
   `INITIATOR-IDENTITY-1`, and which is the actual blocker on A2A here, well ahead of transport.
3. **It fails open.** Without a validator the server accepts all requests in dev mode. The README
   leads with that warning, which is the honest thing to do — but a fail-**closed** default with an
   explicit opt-out would match where the rest of this ecosystem has been heading.

---

## 5. Priority, from the runtime's point of view

| # | Item | Cost | Why now |
|---|---|---|---|
| 1 | Resolve the `nodus-a2a` name collision | small | a live package is involved; the wire cannot ship until it is settled, and the risk grows if the coordinator gains installs |
| 2 | Float the wire's `nodus-lang<5.0.0` cap | one line | third instance of a known pattern; blocks any install alongside the current runtime |
| 3 | Delete the `nodus-a2a-wire` worktree | free | it is a checkout, not a project |
| 4 | Deprecate `nodus-mcp-server` into `nodus-mcp` | moderate | duplication, not breakage; has a documented showcase consumer |

**Nothing here blocks `aindy-runtime`.** The runtime depends on `nodus-lang` and `nodus-mcp` only,
both of which resolve cleanly today.

---

## 6. Provenance

Everything above was measured against PyPI and the working trees on 2026-08-19, not taken from
prior notes. One prior note was wrong and is worth correcting where it is read:

**`TECH_DEBT.md`'s `ECOGAP-4` says "A2A is out — `nodus-a2a` is NOT a wire protocol … zero
transport/HTTP/agent-cards."** That verify-first pass (2026-07-11) examined `C:\dev\nodus-a2a`,
the coordinator, and was **correct about the package it looked at**. It did not know a second
package with the same name held the wire. The observation was right; the conclusion about A2A was
not, and that entry will be corrected in the runtime repo.
