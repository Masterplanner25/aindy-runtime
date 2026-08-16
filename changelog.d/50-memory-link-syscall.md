### Added — `sys.v1.memory.link`, and `POST /memory/links` now dispatches (`ROUTE-EFFECT-BYPASS-1` C, #461)

`POST /memory/links` reached `MemoryNodeDAO.create_link` directly, so building the memory graph
passed **no capability check, no tenant-isolation check and no effect ledger**. Unlike items A+B
this was a build, not a rewire — no link syscall existed.

**★ It carries its own `memory.link` capability, which `memory.write` does not grant.** A syscall
that adds a mediation hop and no authority granularity would just relocate the same
undifferentiated power behind a longer call path. Writing a *node* and wiring the *graph between
nodes* are different powers; `memory.delete` already set the precedent of a memory capability
`memory.write` does not confer. A test drives the dispatcher with a `memory.write`-only context
and requires refusal, so the split is a boundary rather than a label.

Declared **`EXACTLY_ONCE`** (`IDEM-11`): `create_link` inserts a row, so a retry builds a *second*
edge between the same pair. Registry floor `SYSCALL_REGISTRY_MIN_COUNT` 23 → 24.

**Tenant scoping is the syscall's, not the route's.** Both endpoints resolve through a
tenant-scoped `get_by_id` before the write, and a node belonging to another tenant is reported
identically to one that does not exist — distinguishing them would make the route an existence
oracle for other tenants' ids, which is the `/auth/register` enumeration shape somewhere else.
The route keeps its status contract: **404** for an unresolvable node, **422** for a link the DAO
refuses, rather than collapsing both to 400.

**Deliberately off the `POST /platform/syscall` dispatch surface.** `memory.link` is absent from
`_DISPATCH_CAPABILITY_SCOPES`, so SDK callers get an empty grant and the dispatcher denies it;
the syscall is reachable only from the HTTP route that already had the caller. That is the
conservative order for a `stable=False` entry — publishing an experimental syscall to SDK callers
is the half that cannot be withdrawn. Two tests pin the omission as a decision. Adding it later
means a `Scopes.MEMORY_LINK` of its own; mapping it onto `MEMORY_WRITE` would undo at the scope
layer exactly the split the capability makes above.

Direct-DAO routes in `memory_router.py`: **2 → 1**. The last is `POST /nodes/search`, which calls
`dao.find_similar` with `min_similarity` — `sys.v1.memory.search` neither accepts nor uses it
(it calls `dao.recall`), so rewiring would change search *semantics* under cover of a mediation
fix. A test pins the count in both directions: a drop means the remaining work landed, a rise
means a new bypass was introduced.
