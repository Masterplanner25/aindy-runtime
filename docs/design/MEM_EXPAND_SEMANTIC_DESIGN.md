---
title: "Semantic Memory Expansion (MEM-EXPAND-DEAD-1) — Design"
api_version: "1.0"
last_verified: "2026-11-08"
status: current
owner: "platform-team"
---

# MEM-EXPAND-DEAD-1 — turning on the semantic half of memory expansion — design

**ACCEPTED 2026-11-08 (DEC-122): the owner took every recommendation in §7. Phase 1 built in #927: pgvector 0.5.1, the
switch (off), the floor, the count removal and batched fetch, `tests/integration/test_memory_expand_semantic.py` and a
work budget (measured 2 queries for one node, 4 for three).** Originally a proposal under `AGENT_WORKING_RULES.md` §8,
because it changes what a memory read returns. It also takes a dependency bump that Dependabot offered in August (#390),
which we held on purpose. Owning entry: `TECH_DEBT.md` `MEM-EXPAND-DEAD-1`.

## 1. The problem, measured

`MemoryNodeDAO.expand()` (`AINDY/db/dao/memory_node_dao.py:1473`) adds two kinds of neighbours to a set of nodes:
linked ones and semantically similar ones. **The similar half has never returned anything.** It reads each node's stored
embedding and passes it to `find_similar()`, which checks it with `_embedding_is_usable()` (`:171`). That check accepts
only a `list`. pgvector 0.4.2's SQLAlchemy column returns a `numpy.ndarray`, so the check fails, `find_similar()` logs
at `debug` and returns `[]`.

**Measured on real PostgreSQL (`pgvector/pgvector:pg16`, 2026-11-08).** Three shared nodes: `a`, a node `near` it, and
an unrelated node `far`. The probe ran `expand([a], include_linked=False, limit_per_node=3)`:

| pgvector | Stored embedding reads back as | `similar` | Queries for one node |
|---|---|---|---|
| 0.4.2 (pinned today) | `ndarray` | `[]` | 1 |
| 0.5.1 | `list` | `['near', 'far']` | 4 |

The table shows three things:

- **The August diagnosis holds.** 0.5.x's result processor is typed `-> list[float] | None`; the source of 0.4.2, 0.5.0
  and 0.5.1 was diffed.
- **Turning it on costs three more queries per node,** and two of them do no work (§4).
- **`far` came back.** `expand()` calls `find_similar()` with no similarity floor, so it returns the nearest N however far
  away they are. That is a second defect, hidden behind the first (§5).

**A second, narrower bug comes from the same mismatch.** The Python fallback (`:549`) runs `list(node.embedding or [])`.
On an `ndarray` that raises `ValueError: truth value of an array ... is ambiguous`. It is reachable only when the
pgvector query itself throws, which is why nobody has seen it. On 0.5.x the value is a `list` and it works.

**What does not change.** Recall's own semantic search (`:683`), `POST /memory/nodes/search` and the
`memory_nodes_search_similar` flow all pass a query embedding computed in memory, which is already a `list`. Those paths
run today on 0.4.2, so their pgvector query cost is already in production. Only the paths that read an embedding back
**from the database** change, and `expand()` is the only one.

## 2. Who is affected

- **`POST /memory/nodes/expand`.** `include_similar` defaults to `true`, and the route accepts up to 10 nodes.
- **Recall with `expand_results: true`** (`recall()` `:837` and the `memory_recall_v3` flow `:247`). It expands the top
  3 results with `limit_per_node=2`. `expand_results` defaults to `false`.
- **The app calls neither path today.** Its only caller, `client/src/api/memory.js::recallMemory`, is used from
  `MemoryBrowser.jsx` with `expandResults = false`, and nothing in the app calls `nodes/expand`. Turning this on
  therefore changes nothing on the app's stack until the app chooses to use it. It also means **an app-side soak would
  observe nothing**, which matters for D2.

**Dependency facts.**

- **Version:** pgvector 0.5.0 and 0.5.1 differ only in the `bit` and `sparsevec` types, which the runtime does not use.
- **numpy:** pgvector 0.5.x no longer requires numpy. The runtime pins `numpy==2.4.6` directly, so the install does not
  change.
- **Python:** 0.5.x requires Python >= 3.10. The runtime requires 3.11.
- **Pins elsewhere:** the app pins nothing pgvector itself, so it picks the bump up from our wheel.
- **API surface:** the runtime imports only `pgvector.sqlalchemy.Vector`. Nothing calls `.tolist()` or relies on an
  `ndarray`.

## 3. What gets built

1. **Bump `pgvector` 0.4.2 → 0.5.1** in `pyproject.toml` and `AINDY/requirements.txt`.
2. **A switch, `AINDY_MEMORY_EXPAND_SIMILAR`, read inside `expand()`.** When it is off, the similar half is skipped
   before any query, so the result is exactly today's `[]` without today's wasted node fetch. The bump alone would turn
   the feature on, so the switch is what keeps "taking the dependency" and "turning the feature on" as separate steps.
3. **A similarity floor for expansion** (§5).
4. **Cut the dead queries** (§4).
5. **Its first behaviour suite.** `MEM-EXPAND-DEAD-1` says no test caught the bug in either direction. On real
   PostgreSQL (`tests/integration/`), the suite checks:
   - **Switch on:** `near` is returned and `far` is not.
   - **Switch off:** `[]`, and no vector query is issued.
   - **Ownership:** another user's node is never returned, even when it is the nearest. This is a liveness control
     first, then the assertion.
   - **Shared nodes:** visibility for a caller with no user is unchanged.
   - **The read type:** a stored embedding reads back as a `list`. This pins the dependency fact itself, so a later
     pgvector that changes the type again fails loudly instead of silently switching the feature off.
   - **The fallback:** the Python path ranks correctly when the pgvector query raises.
6. **A work budget.** `test_work_budget.py` gains `expand()`: queries grow linearly with the number of nodes, and stay
   within a fixed number per node.

## 4. Cost, and the two dead queries

`find_similar()` runs `base_query.count()` and `embedded_query.count()` before the vector query (`:485`, `:491`). Their
only consumers are a `debug` log line and an early `return []` when the second count is zero. The vector query returns
no rows in that case anyway.

Removing both counts makes the similar half cost 2 queries per node: fetching the node, then the vector query. Fetching
all of `expand()`'s nodes in one query makes it **1 + n**, so **4 queries for recall's top 3 and 11 for the route's
maximum of 10**, instead of 12 and 40.

**This also makes every recall with a query cheaper.** `recall()` calls `find_similar()` at `:683` after it has already
counted complete embeddings itself (`_count_complete_embeddings`, `:677`). Today that path pays three counts before its
one vector query. Removing the two counts inside `find_similar()` changes no result. The recall work budget in
`test_work_budget.py` will move down, and the PR says so.

**No vector index exists.** `memory_nodes.embedding` has no HNSW or IVFFlat index, so each vector query scans the
caller's complete embeddings. Recall already pays that scan on every query, so this design adds at most n more of them
for an explicit expansion. An index is a separate decision with its own trade-offs (build time, recall accuracy,
operator DDL), and it is out of scope here (§9).

## 5. The similarity floor

`find_similar()` turns a cosine distance into `similarity = 1 - distance / 2`. That maps an unrelated pair (cosine 0) to
**0.5**, not 0, and the probe's `far` scored exactly 0.5. Expansion currently uses `min_similarity=0.0`, so it pads every
answer with the nearest nodes whatever they are. Recall doesn't need a floor because it ranks by a weighted resonance
score. Expansion has no other filter.

The proposal is `AINDY_MEMORY_EXPAND_MIN_SIMILARITY`, default **0.75** (cosine 0.5), applied only in `expand()`.
**0.75 is a starting value, not a measured one.** Similarity between unrelated texts depends on the embedding model.
Every returned neighbour already carries its `similarity`, so the first real readout can move the default without a code
change.

## 6. What does not change

- Recall's ranking, its semantic search, and `POST /memory/nodes/search`.
- The linked half of `expand()`.
- Ownership scoping: `find_similar()` keeps going through `apply_memory_owner_scope`, which is also the RTR-4 run-scope
  chokepoint.
- No schema change, and no new route.

## 7. Decisions for the owner

| # | Question | Recommendation | Why |
|---|---|---|---|
| D1 | Which version | **0.5.1** | It is 0.5.0 plus two fixes in types we don't use. Taking 0.5.0 would just mean a second bump later. |
| D2 | Default for `AINDY_MEMORY_EXPAND_SIMILAR` | **Off in the release that takes the bump, then on in the next release by its own decision** | DEC-106's one-default-change-per-release rule names this bump. Once the dependency has shipped, the switch is the only thing left to change. An app soak can't observe anything, because the app doesn't call expansion (§2). The evidence for the flip is the PostgreSQL suite and the work budget, which already exist by then. Waiting for an app window would wait for nothing. |
| D3 | A similarity floor for expansion | **Yes: `AINDY_MEMORY_EXPAND_MIN_SIMILARITY`, default 0.75** | Without one, "similar" means "nearest", and the probe returned an unrelated node. The value is a starting point (§5). |
| D4 | Remove `find_similar()`'s two counts, and batch `expand()`'s node fetch | **Yes, both, in phase 1** | They make the cost 1 + n instead of 4n, and every recall with a query gets cheaper too, with no change to results. |
| D5 | Widen `_embedding_is_usable()` to accept any sequence | **No** | After the bump everything that reaches it is a `list`. Widening it would only matter on 0.4.2, where it would turn the path on without the switch. The read-type test pins the dependency fact instead. |

## 8. Phases

1. **One PR:** the bump, the switch (off), the floor, the count removal and batched fetch, the PostgreSQL suite and the
   work budget. It also adds a `changelog.d` fragment saying the dependency moved and nothing changes while the switch is
   off, and updates `MEM-EXPAND-DEAD-1` to "built, off". It ships in its own release, with the handoff noting that an
   operator who sets the switch gets the feature.
2. **The flip:** `AINDY_MEMORY_EXPAND_SIMILAR` defaults on, recorded as its own DEC, in the release after phase 1.
   `MEM-EXPAND-DEAD-1` closes with it.

## 9. Not in this design

- **A vector index on `memory_nodes.embedding`.** Recall's scan already runs on every query, so an index is a recall
  decision, not an expansion one. It would be filed on its own, with a measurement, when a deployment's node count makes
  the scan show up in recall latency.
- **Changing recall's ranking** to use expansion.
- **A single-query expansion** (a lateral join over the n nodes). At n ≤ 10, 1 + n queries is bounded and readable, and
  the work budget will show if it stops being enough.
