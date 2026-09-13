### Added — `docs/upgrades/`: the per-release handoffs, and the index that did not exist (#644)

Fifteen `APP_HANDOFF_v*.md` files (v1.11.0 → v2.13.0) move from `docs/runtime/` (and one from
`docs/archive/`) into `docs/upgrades/`, with a `README.md` that indexes them: one row per release
with its Alembic head, schema-contract value, whether `bootstrap-schema --reconcile` is owed, and
the consumer-visible change. Until now an operator on 2.4.0 wanting 2.13.0 had nine files to
open in an order nothing stated, and no way to see which carried a schema step without reading
each. `Runtime Docs Validation` now checks the new folder with the same rules, so the move does
not take fifteen documents out of CI. `RELEASE_CHECKLIST.md` gains the step that keeps the index
maintained.
