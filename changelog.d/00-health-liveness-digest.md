### Fixed — a liveness probe no longer persists a full health snapshot (FR-18)

**Operators: read this before upgrading — the fix stops the growth, it does not reclaim what
was already written.**

Every successful `GET /health` wrote a `health.liveness.completed` SystemEvent whose payload was
the **entire health response**: 26 top-level keys, including `trusted_python_execution` (~52 kB
uncompressed), the deployment contract, the sandbox attestation and the full plugin inventory.
The write rate is set by a container healthcheck — **a timer, not traffic**. The published image
probes `/health` every 30s on its own (2,880 rows/day); a deployment whose compose adds its own
`/health` probe writes more. Measured on a real stack: **~98 MB/day, ~3 GB/month**, unbounded,
with no retention.

The app team found it when a `pg_dump` would not finish. On a dev stack with four accounts and
no real traffic: `system_events` at **3653 MB / 183,604 rows** against a 3795 MB database, of
which `health.liveness.completed` was **120,444 rows / 3317 MB — 99.6% of the database**.
`n_dead_tup` was 0, so this was not bloat and not a missing autovacuum; it was live, intended
data. `pg_dump --exclude-table-data=system_events` produced **17 MB**.

**What changed.** `/health` now records a **digest** — status, degraded domains, warnings, a
fingerprint of the posture blobs, and the byte size of the snapshot it did not store — and only
when something changed, on the first probe after boot, or once an hour. The full snapshot is
still available on demand from `GET /health/detail`. The route's own response is unchanged.

| | Before | Now |
|---|---|---|
| Payload per row | the whole ~28 kB health response | a few hundred bytes |
| Rows/day at the image's 30s probe | 2,880 | 24 + one per posture change |

Each row carries `changed_keys` — which posture keys moved — so a change is legible without
the snapshot. **Expect two or three rows immediately after a restart:** some posture providers
populate lazily, so a cold process registers real changes before it settles.

**Reclaiming the existing rows is an operator action.** Nothing prunes `system_events`, so an
upgraded deployment keeps whatever it already wrote:

```sql
DELETE FROM system_events
 WHERE type = 'health.liveness.completed'
   AND timestamp < now() - interval '7 days';
```

A plain `DELETE` leaves the TOAST pages allocated — follow with `VACUUM FULL system_events`
(takes an exclusive lock) or `pg_repack` to return the space to the filesystem.

**New environment variables**, all read per call, so none needs a restart:

- `AINDY_HEALTH_LIVENESS_EVENTS` (default `true`) — `0` makes a liveness probe a pure read.
- `AINDY_HEALTH_LIVENESS_EVENT_PAYLOAD` (default `digest`) — `full` restores the old payload.
- `AINDY_HEALTH_LIVENESS_EVENT_INTERVAL_SECONDS` (default `3600`) — heartbeat floor for an
  unchanged posture; `0` records changes only.

**New metric:** `aindy_health_liveness_events_total{outcome}` —
`persisted_boot|persisted_changed|persisted_interval|persisted_full|suppressed|disabled|failed`.
If `suppressed` stays flat while probes flow, change-detection is being defeated by a volatile
health field and the write rate is bounded only by the digest size — that is the tell.

**Consumers:** none. The event type had no reader in either repo before this change, which is
why the payload shape could move.
