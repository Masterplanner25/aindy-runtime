### Fixed — `bootstrap-schema` sees a column widening, reconciles it, and no longer stamps a head over a schema that does not match (FR-43, #761)

- **Why it was wrong:** the drift check compared type NAMES only. `VARCHAR(32)` and
  `VARCHAR(128)` were the same type to it. On a stack built before 2.22.0, the release's
  widening of `system_events.source` (Alembic 0020) was reported as
  `(no table changes)`. `bootstrap-schema` exited 0 and **stamped `0020` over a `varchar(32)`**,
  so the revision was recorded as applied although its DDL never ran. The 2.22.0 handoff's
  promise of exit 3 was not reachable for that change: even a detected type mismatch was an
  exit-4 offline migration.
- String length and `numeric` precision/scale are now compared. A **widening** (the packaged
  bound admits every existing value) is a new drift class, `additive_column_widen`:
  `bootstrap-schema` exits **3**, and `--reconcile` applies `ALTER COLUMN … TYPE`, which is
  metadata-only in PostgreSQL. A **narrowing**, or a `numeric` scale change, stays
  `column_type_mismatch` (exit 4). `Enum` and `Float` are not compared (their reflected bounds
  differ from the model's by construction), and neither is anything on SQLite.
- `(no table changes)` is printed only when the report is ok. The stamp line now names the
  revision it moved from (`stamped … from revision 0019 to 0020`).
- **Upgrade Path Guard:** the upgrade job now checks every bounded string column's width in
  `information_schema` against the models (89 columns), independently of the drift check. The
  negative control also injects a narrowed `system_events.source` and requires exit 3, no stamp,
  and 128 after `--reconcile`. Before this, the guard recorded column names only, and 2.22.0
  passed it.
- **Operators:** a deployment that remedied 2.22.0 out of band (as the app did) sees no change. A
  deployment that did not, and still has `system_events.source` at `varchar(32)`, now gets
  exit 3 on this release. `bootstrap-schema --reconcile` widens it. Check with `SELECT
  character_maximum_length FROM information_schema.columns WHERE table_name='system_events' AND
  column_name='source'`.
