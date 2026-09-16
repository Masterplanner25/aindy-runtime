### Added — `system_events` retention: a class per event type, pruning leaves only (`SYSEVENT-RETENTION-1`, #704)

- New leader-only scheduler job `system_event_retention`, **registered only when
  `AINDY_SYSEVENT_RETENTION` is `report` or `prune`** — unset (the default) registers nothing, so
  upgrading changes no behaviour. `report` runs the selection on the interval and logs per-type
  counts without deleting; read one before the first `prune`. Any other value is treated as unset.
- Retention is a class per event **type**: `audit` (never pruned by age — every failure-shaped
  event, `capability.*`, `auth.*`, `platform.*`, dead-letter and recovery events), `operational`
  (90 days — the execution ledger, traces, signals, `autonomy.decision`) and `keepalive` (7 days —
  `watchdog.scan.completed`, `health.liveness.completed`). Overrides:
  `AINDY_SYSEVENT_RETENTION_OPERATIONAL_DAYS`, `_KEEPALIVE_DAYS`, `_INTERVAL_HOURS` (24),
  `_BATCH` (1000). **A type with no class is kept forever**; apps declare theirs with
  `register_event_retention(event_type, class)` (exact names or globs).
- **The job prunes leaves only.** A row referenced by `parent_event_id`,
  `agent_events.system_event_id`, `memory_nodes.source_event_id`/`root_event_id`, or either end
  of an `event_edges` row is never eligible regardless of class — four of those constraints would
  make the delete fail; the fifth (`event_edges`, `CASCADE`) would silently remove the causal
  edge `build_trace_graph` reads, which is why it is checked in the predicate.
- Deletes in committed batches. New metrics: `aindy_system_events_pruned_total{type}` and
  `aindy_system_events_unclassified_rows` (rows kept only because their type has no class —
  growth there is the ask to classify).
- Decisions recorded: DEC-026 … DEC-029. Design: `docs/design/SYSEVENT_RETENTION_DESIGN.md`.
