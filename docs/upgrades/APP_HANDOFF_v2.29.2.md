---
title: "App Handoff — Runtime v2.29.2"
api_version: "1.0"
last_verified: "2026-11-03"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.29.2

Bump the `constraints.txt` pin to `==2.29.2` (your contract test moves the floor with it), then
**rebuild the image**. No ui-kit release: `@aindy/ui-kit` 2.1.1 stays current.

**What this release is.** It is a patch with two changes:
- a dependency batch, including `pydantic` 2.14.2, which fixes two security advisories (#881);
- the stale-log cleanup now closes what it fails (#882, DEC-105).

There is no route, scope, env var or schema change.

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v, pydantic; print(v.__version__, list(AINDY.__path__), pydantic.VERSION)"
> # expect: 2.29.2 ['/usr/local/lib/python3.11/site-packages/AINDY'] 2.14.2
> ```

---

## 0. Schema: none

No change under `AINDY/db/models/`. Alembic head stays **`0020`** and the schema contract stays
**`2026-09-20`**. `bootstrap-schema` exits 0 on a current stack. The Upgrade Path Guard therefore
passes trivially on this release; its `negative-control` job is the half that carries meaning here.

## 1. `pydantic` 2.14.2: two medium advisories (#881)

| Advisory | What it takes to be affected |
|---|---|
| GHSA-qpmf-fgh3-ff25 | untrusted JSON validated into a model with `extra='allow'` and a key type annotated on `__pydantic_extra__` |
| GHSA-3r8g-cqwr-jr29 | untrusted data validated into a `TypedDict` or dataclass with aliased fields and extra data allowed |

**We searched `AINDY/` and your `apps/` and found neither.** No `extra='allow'`,
`__pydantic_extra__`, `extra_items` or `pydantic.dataclasses` in either, and no `model_validate_json`
or `alias_generator` in either. That is our source, not every third-party package that uses pydantic.
You may want to confirm it against your own tree, since you know what it holds beyond `apps/`.

2.14.2 also changes JSON-mode validation: a key matching only an unused alias or field name is now
extra data, so `extra='forbid'` refuses it. This only reaches `model_validate_json` and the like.

## 2. The stale-log cleanup closes what it fails (#882, DEC-105)

This comes from your `RUNTIME_2_29_1_UPGRADE.md` §4. You were right about FR-56, and the dated
correction at the top of [`APP_HANDOFF_v2.29.1.md`](./APP_HANDOFF_v2.29.1.md) says what we had wrong.

`_cleanup_stale_logs` fails a job row that sat `pending` for an hour. It did nothing else. Now:

| When a stale row is failed | 2.29.1 | 2.29.2 |
|---|---|---|
| its execution unit | stays `pending` forever | goes `failed` in the same commit (a unit already terminal keeps its status) |
| `job_log.written` | not fired | fired per row, after the commit |

Your `_handle_job_log_written` reads the row and syncs `automation_logs`, so **the drift half of your
`JOB-LOG-MIRROR-STALE-1` closes when you adopt this.** Rows already failed get no new event; you
corrected those by hand on 11-02.

**Your question: no.** `memory settle-embedding-logs` will not take the `failed` rows. "Never
started" is true of those jobs, they hold no admission slot, and a rewrite to `success` loses the
record. Leave them as they are.

## 3. Other pins

- **OpenTelemetry 1.45.2 / 0.66b2:** the SDK caps each metric instrument at 2000 attribute sets;
  past that they fold into one `otel.metric.overflow` series. This reaches only the GenAI
  `gen_ai.client.*` meter (DEC-037), not the `aindy_*` Prometheus metrics. An incoming `traceparent`
  is capped at 512 characters.
- **openai 2.54.0:** 2.52.0 honours `Retry-After` delays up to two minutes, so a rate-limited call
  can wait longer. Still 2.x on `httpx`; 3.x stays deferred.
- **pymongo 4.19.0:** requires Python 3.11+, which you already meet.
- **react-router-dom 7.18.5, postcss 8.5.32:** the platform UI build only.

`publish.yml` job names and the `production` gate are unchanged, so your release watcher is
unaffected.

## 4. Asks

1. Bump the pin to `==2.29.2`, rebuild, and run the in-container check above.
2. **Report the stranded-unit count**, read before or after the rebuild; this release does not change
   old units. We will build a command to close them only if it is large.

   ```sql
   SELECT count(*) FROM execution_units eu
   JOIN job_logs j ON eu.source_id = j.id
   WHERE eu.source_type = 'job_log' AND eu.status = 'pending' AND j.status = 'failed';
   ```
3. **Report `aindy_memory_recall_failures_total`** across a window you state, with its
   recall volume beside it. Turning `AINDY_MEMORY_RECALL_OWN_SESSION` on is the next planned default
   change (DB-NODUS-BUDGET-1, DEC-083), and it waits on this reading and the owner's go. Your last
   reading we can find is in your `RUNTIME_2_25_0_UPGRADE.md`. "No samples" needs the volume beside
   it: with no recalls in the window, it says nothing.
4. **Search the api log for `closing superseded embedding logs failed`** since your 2.29.1 recreate.
   The sweep's JSON filter on `job_logs.payload` has only been tested on SQLite. A failure on Postgres
   is a warning and nothing else, and your settle dry run does not exercise that filter. Report
   whether it appears, and any non-zero `logs_closed`.
