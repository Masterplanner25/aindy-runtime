---
title: "App Handoff — Runtime v2.6.0"
api_version: "1.0"
last_verified: "2026-08-22"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.6.0

**This one IS a `pip install`.** No schema step, no migration, no default flip. Verified across
`v2.5.0..2.6.0`: nothing under `AINDY/db/models/`, `alembic/versions/` or
`memory/memory_persistence.py` moved, so `bootstrap-schema` has nothing to reconcile and exits 0.

**Six feature requests closed — FR-17 through FR-22, all filed 2026-08-16/22.** Still open where
`v2.5.0` left them: **FR-14's remaining half** (the upgrade path is never exercised against an
existing database) and **FR-6 items 2+3** (blocked on the email-connector decision). No route
began enforcing a new scope, so no caller loses access. `nodus-lang` moved 5.0.4 → 5.1.0 in
`2.6.0`'s window; no other pin changed.

---

## 1. ★ FR-18 does not reclaim what is already on disk

The liveness fix stops the growth; it does not delete the rows already written. On the stack that
reported it, that is **3.3 GB of `health.liveness.completed`** still sitting in `system_events`
after the upgrade.

```sql
DELETE FROM system_events
 WHERE type = 'health.liveness.completed'
   AND timestamp < now() - interval '7 days';
```

A plain `DELETE` leaves the TOAST pages allocated — follow with `VACUUM FULL system_events`
(exclusive lock, so schedule it) or `pg_repack` to return the space to the filesystem.

**What changes going forward:** `/health` records a digest — status, degraded domains, warnings, a
posture fingerprint, `changed_keys`, and the byte size of the snapshot it did *not* store — and
only when the posture changes, at boot, or once an hour. The route's response body is unchanged;
`GET /health/detail` still serves the full snapshot on demand.

Expect **two or three rows immediately after a restart**: several posture providers populate
lazily, so a cold process registers real changes before settling. `changed_keys` names which.

Off switches, all read per call (no restart): `AINDY_HEALTH_LIVENESS_EVENTS=0`,
`AINDY_HEALTH_LIVENESS_EVENT_PAYLOAD=full`, `AINDY_HEALTH_LIVENESS_EVENT_INTERVAL_SECONDS`.

---

## 2. ★ FR-19 — the client rule you can now write once

Enveloped responses carry a header:

```
X-AINDY-Envelope: v1
```

**Unwrap `data` when the header is present; use the body as-is when it is not.** That replaces the
per-route knowledge your 11 API modules were each carrying.

Three things to code against:

- **`X-Trace-ID` is not a discriminator** — middleware sets it on every response.
- The header is **absent** on error responses, handler-built `Response` objects, and routes with a
  registered response adapter. Absence means *not enveloped*, never *unknown*.
- **A blanket unwrap is still wrong**, for the reason you found: a bare body may legitimately
  carry a `data` key.

### The part that may explain a past mystery

**No runtime response header was readable cross-origin before this release** — `CORSMiddleware`
had `allow_headers=["*"]` and no `expose_headers`, and `allow_headers` governs the *request*
direction. A browser exposes only the CORS safelist unless the server names the rest. So on your
Vite dev server against `:8000`, `X-Trace-ID` has never been readable by the page that wanted it.
Now exposed: `X-AINDY-Envelope`, `X-Trace-ID`, `X-Request-ID`, `X-EU-ID`, `X-API-Version`,
`X-Version-Warning`.

**Your preference 1 remains yours** — making every `/apps/*` route enter the pipeline removes the
two shapes rather than labelling them, and it is app-side routing work.

---

## 3. FR-21 — you can retire your operator panels

`WebhooksPanel` and `DeadLetterQueuePanel` now ship in the runtime console, routed at
`/platform/webhooks` and `/platform/dead-letters`, with nav entries and admin gates.

**The gap was two panels, not the five you offered** — the console already shipped flow engine,
agent registry, admin users and executions. Safe to delete from your repo once you are running a
version that has them:

- `client/src/components/platform/WebhooksPanel.jsx`
- `client/src/components/platform/DeadLetterQueuePanel.jsx`
- their routes and nav entries

**Timing caveat, and it is the one that bites:** a UI change reaches no container until a release
is cut **and the image pin is bumped** — the SPA ships as package data inside the wheel. Your
running container serves the last *released* console, so verify against the new image, not the
old one.

`RippleTraceViewer` stays yours; it reads an app domain.

---

## 4. FR-22 — the route inventory, and a correction to a premise

`AINDY/route_inventory.json` ships inside the wheel: every `(method, path)` the runtime serves in
the `runtime-only` profile, with tags. Read it for the version you installed, without booting:

```python
import json
from importlib.resources import files

inventory = json.loads(files("AINDY").joinpath("route_inventory.json").read_text())
served = {(entry["method"], entry["path"]) for entry in inventory["routes"]}
```

`tests/unit/test_route_inventory.py` fails in `Runtime Contracts` when the file and the served
surface disagree **in either direction** — the removal direction matters more to you, since that
is what breaks a pinned consumer.

### ★★ `/apps/*` is not an ownership boundary

**35 routes under `/apps/*` — coordination, memory, agent — are served by the runtime alone, with
no plugins loaded.** So `check_api_reference.py`, scoped to `APP_PREFIX = "/apps/"`, is already
enforcing over runtime-owned routes, and the "app half / runtime half" split in your reference
does not fall where the prefix suggests.

The useful consequence is better than what you asked for: **subtract this inventory from your
booted surface to derive the genuinely app-owned set**, instead of curating one by hand.

Two exclusions to read precisely: the legacy alias surface
(`AINDY_ENABLE_LEGACY_SURFACE=true`) is not in the file — it publishes supported routes, not
compatibility shims. And there is no version field; the wheel identifies the version.

---

## 5. FR-17 and FR-20 — smaller, and both change what you will see

**FR-17 — async jobs now record that they started and finished.** Your report covered the
submit-side drop. The larger half was the worker: `_execute_job_inline` activated the
async-execution context only under `AINDY_ASYNC_JOB_LOOP_CLOSURE` (**off by default**), so *every*
async job's `execution.completed` / `execution.failed` was discarded. Traces started and never
ended.

*What you will see:* **more rows in `system_events` for async jobs** — one `execution.started` per
submission that previously produced none, and a terminal event per job. That is the fix working.
`AINDY_ASYNC_JOB_LOOP_CLOSURE` now gates only what its name says (score emission); it no longer
decides whether a job's execution is recorded at all.

**FR-20 — a deliberately raised 4xx survives.** A route under the execution contract that raises
`HTTPException` before entering the pipeline now returns that status instead of 500. Your stale
masterplan link 404s.

*Note the runtime already disagreed with itself here:* an `HTTPException` from a **dependency**
always kept its status; only the endpoint body lost it. The violation is still recorded — on
`aindy_route_contract_violations_total{route,outcome}` (`status_preserved` | `converted_500`) plus
an ERROR log — because before this the 500 *was* the record, and preserving the status without
somewhere else to put it would have traded a wrong status for a silent one.

---

## 6. Verification

Every change above shipped with a mutation-tested suite (each 2/2 — the fix reverted, the test
fails), and all twelve required checks were green on each merge commit. Two verification traps
worth knowing, because they will bite anyone testing the same surfaces:

- **An HTTP probe cannot tell a missing `/platform/*` route from a served one.**
  `_SPAStaticFiles` is mounted at `/platform` and falls back to `index.html`, so a typo'd path
  answers **200 with HTML**, not 404. Compare against the OpenAPI schema instead.
- **Walking `app.routes` drops the mount prefix.** FastAPI ≥ 0.137 stores an included router as a
  lazy `_IncludedRouter`, so a walk reports `/webhooks`, never `/platform/webhooks`.
