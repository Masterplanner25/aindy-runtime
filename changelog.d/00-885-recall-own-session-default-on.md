### Changed — memory recall uses its own session by default (#885)

- `AINDY_MEMORY_RECALL_OWN_SESSION` defaults ON (DEC-106). Recall ran on the caller's session, which
  left it inside a transaction that the flow runner then held across node execution. Recall now takes
  its own short-lived session.
- ★ **What changes:** recall no longer sees the caller's uncommitted writes, and each recall takes a
  second pooled connection for the length of the read. If your pool is tight, watch
  `aindy_db_pool_checkedout` and `aindy_memory_recall_failures_total` after upgrading.
- Only `0` / `false` / `no` / `off` disable it. A blank value used to mean off and now means on.
- The evidence is one stack's: the counter read 0 at every stage from 2026-10-02 to 10-09, over 405
  recalls, with no pool exhaustion. No agent ran in that window.
