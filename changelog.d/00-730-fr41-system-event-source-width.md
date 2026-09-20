### Changed — ★ schema step: `system_events.source` widens 32 → 128; an over-width source is refused at pipeline entry (FR-41, #730)

- **Operators: this release changes the schema** — Alembic `0020`, contract `2026-09-20`. An existing
  deployment's bare `aindy-runtime bootstrap-schema` exits **3** (additive-reconcile-required):
  run `bootstrap-schema --reconcile` or branch on exit code 3 (FR-14's path). Widening only; no
  row is rewritten. `downgrade()` narrows with `left(source, 32)`, which is lossy for rows written
  with a longer name after the upgrade.
- **Why it was wrong:** the pipeline writes every request's route name as `source`. At 32, a 33+
  character name raised `StringDataRightTruncation` inside the REQUIRED `execution.started` emit
  on every request; the pipeline caught it, logged a WARNING, recorded the side effect `failed`,
  and the request proceeded with **no execution record at all**. The app that found it had 8 of
  230 route names over the width, unrecorded since 2026-09-10.
- **Fail once, not per request:** a `source` that cannot fit the column is now a contract violation
  checked at pipeline ENTRY, before the handler — under `ENFORCE_EXECUTION_CONTRACT` (the default)
  the request fails; otherwise it proceeds and the violation is logged at ERROR once per name.
  Never truncated: a shortened source would collide across routes. The width is documented on
  `execute_with_pipeline`'s `route_name` and readable via
  `system_event_service.system_event_source_max_length()`.
