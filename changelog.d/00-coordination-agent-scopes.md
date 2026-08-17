### Changed — the last identity-only routes now check authority (`HTTP-SCOPE-GAP-1`, #464)

**Operators read this before upgrading.** Eighteen more routes now require a scope: all 13 under
`/coordination/*` and the 5 user-owned agent routes under `/platform/agents`. They previously
depended on `get_current_user` alone — agent registration, heartbeats, deregistration, the
inter-agent inbox and agent CRUD were reachable by anyone who could authenticate.

| Routes | Scope |
|---|---|
| `/coordination/agents`, `/agents/status`, `/agents/register`, `/agents/{id}/heartbeat`, `DELETE /agents/{id}`, `/graph`, `/messages/inbox`, `/messages/{id}/acknowledge` | `agent.run` |
| `/coordination/runs`, `/runs/{id}/children`, `/conflict/run` | `execution.read` |
| `/coordination/memory/shared`, `/conflict/memory` | `memory.read` **or** `memory.write` |
| `GET/POST/PATCH/DELETE /platform/agents`, `/platform/agents/{slug}/restore` | `agent.run` |

`platform.admin` continues to satisfy any gate.

**Interactive users lose nothing.** All four scopes are in the ordinary derived session set, and
a test drives the real routes to prove it. As with the memory router, the callers to check are
**platform API keys** issued without these scopes.

**Three gates rather than one, deliberately.** `/coordination/memory/shared` queries
`memory_nodes` directly and `/conflict/memory` inspects a memory path — gating them on
`agent.run` because they live in the agent router would make that router a second door onto
memory. Tests assert the split in both directions: an agent-scoped caller cannot read shared
memory, and a memory-scoped caller cannot register an agent.

**No `agent.read`/`agent.manage` was invented.** The agent surface is gated as one authority
because the vocabulary has no finer grain, and adding one would oblige every consumer to grant a
scope that answers no question they ask today. If that split is wanted later it should be a
deliberate vocabulary change, not a side effect of adding gates.

**★ `/platform/agents` never inherited the `/platform` admin gate** — it is mounted on the app
directly rather than through `platform_router`, by design (FR-12b exists so an ordinary user can
own an agent). That is also why it had no authority check at all. Owner scoping is unchanged and
still does the work a scope cannot: a scope answers *"may you touch agents"*, never *"may you
touch **this** agent"*.

**Both prefixes are covered.** The coordination handlers are registered at `/coordination/*` and
at `/apps/coordination/*`; the gate is on the endpoint, so it applies to either. The app's
`smoke_autonomy.py` calls the `/apps` form with a Bearer JWT and is unaffected.

**Census after this change** — 126 registered routes: **47 scope-gated, 56 admin-gated, 21
public, 2 identity-only.** The two are `POST /auth/logout` and `POST /auth/password/change`,
which act only on the caller's own account, where a scope is a permission nobody could be
denied. A test pins that set by equality, so both adding an ungated route and gating one of
those two fail in CI.
