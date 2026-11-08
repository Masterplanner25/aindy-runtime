---
title: "App Handoff — Runtime v2.33.0"
api_version: "1.0"
last_verified: "2026-11-08"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.33.0

**pgvector moves 0.4.2 → 0.5.1, and memory expansion's "similar" half can be switched on. It is off, so no answer
changes on upgrade.** `MEM-EXPAND-DEAD-1`, DEC-122, design `docs/design/MEM_EXPAND_SEMANTIC_DESIGN.md`.

Bump the `constraints.txt` pin to `==2.33.0` (your contract test moves the floor with it), then **rebuild the image**. No
ui-kit release: `@aindy/ui-kit` 2.1.1 stays current. You don't pin pgvector yourselves, so it comes in with the wheel.

**You are on 2.31.5 at last report, so this also carries 2.31.6 and 2.32.0.** Go straight to 2.33.0, but do both of
their steps:

- [`APP_HANDOFF_v2.32.0.md`](./APP_HANDOFF_v2.32.0.md) **§0, the schema step.** Alembic moves `0020` → `0021`, so the
  first boot exits 3 until you run `aindy-runtime bootstrap-schema --reconcile` once. 2.33.0 adds no migration of its
  own.
- [`APP_HANDOFF_v2.31.6.md`](./APP_HANDOFF_v2.31.6.md): the memory path backfill, `AINDY_REGISTRATION`, and your trace
  helpers' `user_id`.

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v, importlib.metadata as m; print(v.__version__, m.version('pgvector'), list(AINDY.__path__))"
> # expect: 2.33.0 0.5.1 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 1. What was wrong

`MemoryNodeDAO.expand()` adds neighbours to a set of memory nodes: linked ones, and semantically similar ones. **The
similar half never returned anything.** pgvector 0.4.2 read a stored embedding back as a `numpy.ndarray`, and the check in
front of the similarity search accepts only a `list`. It logged at `debug` and returned `[]`.

You never saw this, because you call neither path that uses it. Your only recall call
(`client/src/api/memory.js::recallMemory`, from `MemoryBrowser.jsx`) passes `expand_results: false`, and nothing calls
`POST /memory/nodes/expand`.

## 2. What changes (#927)

- **pgvector 0.5.1.** A stored embedding reads back as a `list`. numpy stays installed through the runtime's own pin.
- **`AINDY_MEMORY_EXPAND_SIMILAR`, default off.** With it off, expansion's `similar` list is `[]`, exactly as before.
  With it on, `POST /memory/nodes/expand` and recall with `expand_results: true` return similar nodes.
- **`AINDY_MEMORY_EXPAND_MIN_SIMILARITY`, default 0.75.** The floor for a similar node, on the same scale as the
  `similarity` field each result already carries, `1 - cosine_distance / 2`. An unrelated node scores 0.5. Without a
  floor, expansion returned an unrelated node in our measurement.
- **Fewer queries:** every recall with a query text runs two fewer database queries, with the same results. With the
  switch on, expansion costs one query plus one per expanded node.

## 3. Next release

The switch is planned to **default on in the next release**, as its own decision. Because you don't call expansion, that
changes nothing for you either, until you choose to use it. If you'd like to try it first, set
`AINDY_MEMORY_EXPAND_SIMILAR=true` and send us a few `similarity` values from real nodes. Those would tell us whether
0.75 is the right floor for your embedding model.

## 4. Asks

1. Bump the pin to `==2.33.0`, rebuild, do 2.32.0's `bootstrap-schema --reconcile` once, and run the in-container
   check. Report that `alembic_version_runtime` reads `0021` and that pgvector reads `0.5.1`.
2. Do the 2.31.6 and 2.32.0 asks against this version.
3. Run your full test suite on it, and report anything memory-related that changed.

Report in your `RUNTIME_2_33_0_UPGRADE.md`.
