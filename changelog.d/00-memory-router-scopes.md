### Changed — the memory router now enforces authority, not just identity (`HTTP-SCOPE-GAP-1` D, #462)

**Operators read this before upgrading.** All 22 routes under `/memory/*` now require a scope.
Previously they depended on `get_current_user` alone — `grep -c enforce_api_key_scope` in
`AINDY/routes/memory_router.py` was **0** while that file reached memory writes, graph edits and
Nodus script execution. Anyone who could authenticate could do all of it, and an API key issued
with, say, `flow.read` only could too.

**Scopes required:**

| Routes | Scope |
|---|---|
| all reads — `GET /nodes`, `/nodes/{id}`, `/history`, `/links`, `/traverse`, `/performance`, `/agents`, `/agents/{ns}/recall`, and `POST /nodes/search`, `/nodes/expand`, `/recall`, `/recall/v3`, `/federated/recall`, `/suggest` | `memory.read` **or** `memory.write` |
| `POST /nodes`, `PUT /nodes/{id}`, `POST /links`, `POST /nodes/{id}/share`, `POST /nodes/{id}/feedback` | `memory.write` |
| `POST /nodus/execute`, `POST /execute`, `POST /execute/complete` | `flow.execute` |

`platform.admin` continues to satisfy any gate.

**Interactive users lose nothing.** An ordinary JWT session derives `memory.read`, `memory.write`
and `flow.execute` from the user row (`derive_session_scopes`), so every gate here is satisfiable
without issuing anyone a grant. A test asserts exactly that against the real routes, so if it
ever stops being true it fails in CI rather than as scattered 403s that read as a frontend bug.

**API keys are where to look.** A platform key that calls `/memory/*` over HTTP and was issued
without these scopes will now get **403**. Grant the scope, or use `POST /platform/syscall`,
which was already gated. No first-party caller is affected: the SDK's `client.memory.*` goes
through the syscall route, not these HTTP routes.

**★ The read gate accepts `memory.write` as well**, matching `_DISPATCH_CAPABILITY_SCOPES`
exactly. Without that, one key would read fine through `POST /platform/syscall` and be refused
on `GET /memory/nodes` — two answers to one authority question from one credential.
`enforce_api_key_scope` gained any-of alternatives for this; existing single-scope call sites are
unchanged.

**★ Execution is not a memory scope.** `/memory/execute` and `/memory/nodus/execute` compile and
run caller-supplied workflow code. Filing them under `memory.write` would have made "may I
remember this" and "may I run this" the same permission, so they take `flow.execute`.

**Behaviour note:** `POST /memory/execute/complete` has returned **410 Gone** since completion
moved inside `POST /memory/execute`. A caller without `flow.execute` now gets **403** there
instead — authorization is checked before the deprecation notice.

Still open on `HTTP-SCOPE-GAP-1`: the other routers. This closes the router the entry named; it
does not close the entry.
