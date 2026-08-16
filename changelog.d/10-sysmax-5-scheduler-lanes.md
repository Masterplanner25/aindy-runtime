### Fixed — scheduler jobs no longer starve each other (`SYSMAX-5`, #453)

The scheduler ran ~33 jobs (12 runtime + ~21 app-registered) against a **single pool of 10
workers**, with two unbounded holders: `scheduler_heartbeat_tick` occupies a worker for the whole
duration of an INLINE execution (~13 minutes in the `FR-15` incident), and DB-heavy jobs can
block for `DB_POOL_TIMEOUT` (60s) under connection-pool exhaustion.

The failure mode was a **maintenance brownout**: the pool saturates and the remaining jobs
silently stop running — including the recovery jobs whose purpose is cleaning up after the
condition that saturated it. Nothing raised.

**Three lanes, sized for isolation rather than capacity:**

| Lane | Workers | Holds |
|---|---|---|
| `default` | 10 | ordinary maintenance + every app-registered job |
| `recovery` | 2 | the six jobs whose value *peaks* when the scheduler is saturated |
| `waits` | 1 | time-wait firing (`FR-15` (b)) |

**★ Raising `default` would have been the wrong fix**, not merely a weaker one:
`DB_POOL_SIZE` (10) + `DB_MAX_OVERFLOW` (20) = **30 connections shared with request handling**.
Twenty concurrent scheduler jobs each holding a session would leave ten for the API — the
RT-MEMTXN-LEAK-1 shape, where a login took 42 seconds. A test asserts the lanes stay within half
the DB budget, so that trade cannot be made accidentally.

`queue_backend_reconnect` is the sharpest case: if the queue backend is down *and* the pool is
saturated, the job that would reconnect it could not run — a self-sustaining outage.

**New metric `aindy_scheduler_job_starved_total{job_id,reason}`.** APScheduler reports
saturation only as a per-job log line (*"maximum number of running instances reached"*) — which
is exactly what the `FR-15` incident printed once per starved second while nobody could see it
as a signal. `reason` separates `max_instances` (previous run still going) from `missed` (no
worker free); they have different causes and different fixes.
