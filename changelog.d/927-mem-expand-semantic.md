### Changed — pgvector 0.5.1; memory expansion's similar half can be switched on, and is off (MEM-EXPAND-DEAD-1, #927)

- **pgvector 0.4.2 → 0.5.1.** A stored embedding now reads back as a `list`, not a `numpy.ndarray`. That type is why the similar half of `MemoryNodeDAO.expand()` returned `[]` on every call it has ever made: its guard accepts only a `list`. numpy stays installed through its own pin.
- **`AINDY_MEMORY_EXPAND_SIMILAR` (default off, DEC-122)** gates that half, so taking the dependency changes no answer. When off, expansion's similar list is `[]` as before, and it no longer fetches each node to find that out. It is planned to default on in the next release. Affects `POST /memory/nodes/expand` and recall with `expand_results: true`.
- **`AINDY_MEMORY_EXPAND_MIN_SIMILARITY` (default 0.75):** with the half on and no floor, it also returned an unrelated node, measured on PostgreSQL. An unrelated pair scores 0.5 on that scale.
- **Cheaper:** `find_similar()` no longer runs two `count()` queries that fed only a debug line, so every recall with a query issues two fewer queries. With the half on, expansion costs 1 + n queries (it was 4n), held by `test_work_budget.py`.
- The Python fallback no longer truth-tests a stored embedding, which raised on an `ndarray`.
