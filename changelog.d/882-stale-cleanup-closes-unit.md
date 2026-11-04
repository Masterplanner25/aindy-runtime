### Fixed — the stale-log cleanup left the execution unit `pending` and the app's mirror behind (#882)

- `_cleanup_stale_logs` failed a job row that sat `pending` for an hour and did nothing else. Its
  execution unit stayed `pending` forever, for any job type, and `job_log.written` never fired, so
  an app mirroring `job_logs` kept showing the row as `pending`.
- Now (DEC-105) the unit goes `failed` in the same commit, and the event fires per row after it.
  Units stranded before this release are not changed.

### Changed — correction (2026-11-03) to 2.29.1's FR-56 entry (#882)

- The 2.29.1 entry says stranded embedding rows hold admission slots, citing 2,387 rows on one
  stack. It was wrong. Those rows were `pending` only in the app's mirror; in `job_logs` the stale
  cleanup had failed each one an hour or two after creation, so a row held a slot only for that
  window, and `memory settle-embedding-logs` found 0. The bug and its fix stand.
- `memory settle-embedding-logs` does not take `failed` rows (DEC-105).
