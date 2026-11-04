---
title: "App Handoff — Runtime v2.29.1"
api_version: "1.0"
last_verified: "2026-11-03"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.29.1

> **Correction (2026-11-03, after publication; reported by the app).** This handoff told you to
> expect about 2,387 rows to settle, and said those rows held admission slots. Both were wrong. The
> 2,387 were `pending` only in your `automation_logs`. In `job_logs` our `_cleanup_stale_logs` had
> already failed each one, an hour or two after creation, which is why your dry run found 0. A row
> held a slot only for that window. Read every claim below about stranded rows and the caps with
> that in mind. The cap of 2 you set is safe for embedding: a refused embedding submission is caught
> and logged in `MemoryNodeDAO`, and the sweep still embeds the node.
>
> **Your question (your `RUNTIME_2_29_1_UPGRADE.md` §4): no.** `memory settle-embedding-logs` will
> not take the `failed` rows. "Never started" is true of those jobs, they hold no slot, and a
> rewrite to `success` loses the record (DEC-105). Leave them as they are.
>
> **What we fixed instead (#882, in the next release, not in 2.29.1):** the cleanup now closes the
> row's execution unit and fires `job_log.written`, so your handler re-syncs the mirror. That covers
> the drift half of your `JOB-LOG-MIRROR-STALE-1`.
>
> **One ask:** the old cleanup never closed those units, so yours are probably still `pending`.
> Please report this count. We will build a command to close them only if it is large.
>
> ```sql
> SELECT count(*) FROM execution_units eu
> JOIN job_logs j ON eu.source_id = j.id
> WHERE eu.source_type = 'job_log' AND eu.status = 'pending' AND j.status = 'failed';
> ```
>
> **Your "no new row" check:** a process with no scheduler, such as a scheduled callback's, still
> writes a `memory.generate_embedding` row, then runs it inline and closes it. "No row left `pending`
> past a few minutes" is the test that means something.

Bump the `constraints.txt` pin to `==2.29.1` (your contract test moves the floor with it), then
**rebuild the image**. No ui-kit release: `@aindy/ui-kit` 2.1.1 stays current.

**If you are still on 2.28.2, go straight to 2.29.1.** Nothing in 2.29.0 is lost by skipping its
image. Its behaviour change still applies, so **read [`APP_HANDOFF_v2.29.0.md`](./APP_HANDOFF_v2.29.0.md)
§1 and do its asks** (read the counter on 2.28.2 before recreating, and have #506/#507 in the
image). Install `aindy-runtime[mcp,security]` as you planned.

**What this release is.** It is a patch with three changes:
- FR-56: embedding jobs no longer strand `pending` rows that hold admission slots (#871, DEC-104);
- the runtime's own `Dockerfile` now upgrades setuptools (#870);
- a dated correction to 2.29.0's setuptools note (#869).

There is one new CLI subcommand, and no route, scope, schema, env var or pin change.

> ## ★ After the rebuild: confirm what you are running, then settle the stranded rows
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v, setuptools; print(v.__version__, list(AINDY.__path__), setuptools.__version__)"
> # expect: 2.29.1 ['/usr/local/lib/python3.11/site-packages/AINDY'] and setuptools >= 84.1.0 (with [security])
>
> docker exec <api-container> aindy-runtime memory settle-embedding-logs        # reports only
> docker exec <api-container> aindy-runtime memory settle-embedding-logs --yes  # settles
> ```

---

## 0. Schema: none

No change under `AINDY/db/models/`. Alembic head stays **`0020`** and the schema contract stays
**`2026-09-20`**. `bootstrap-schema` exits 0 on a current stack. The Upgrade Path Guard therefore
passes trivially on this release; its `negative-control` job is the half that carries meaning here.

## 1. Embedding jobs no longer strand `pending` rows (FR-56, #871, DEC-104)

Your report was right on every point. `enqueue_embedding` was the only caller passing
`execute_inline_in_test_mode=False`. The queued submit path returned on that flag after
committing the `pending` row and before the executor. The sweep did all the embedding.

| Where `enqueue_embedding` runs | 2.29.0 | 2.29.1 |
|---|---|---|
| instance with the scheduler running (lease holder) | row written `pending`, **never run** | **no row**; the one-minute sweep embeds the node |
| no scheduler in the process (a follower, or before boot finishes) | runs inline, row closed | unchanged: runs inline (or on the distributed queue), row closed |

The sweep also closes any open embedding row for a node it embeds; `logs_closed` appears in its
result. It never touches rows for other nodes.

**The rows already stranded stay `pending` until you run the command.** The release does not
change your rows on upgrade, on purpose.

- **What it settles:** only a row that meets all of these:
  - `job_name = 'memory.generate_embedding'`
  - `status = 'pending'`
  - `started_at IS NULL` and `attempt_count = 0`
  - the payload's node exists with `embedding_pending = false`
- **What it writes:** each such row becomes `success`, with `completed_at` set and
  `result = {"memory_id": …, "superseded_by": "memory.embedding_sweep"}`. Its execution unit goes
  `pending → executing → completed`.
- **What it leaves:** a row whose node is still pending or has been deleted is left alone and
  counted in the report.
- **How it runs:** in committed batches of 500 (`--batch-size`), so no long transaction holds a
  pooled connection.

On your 2026-11-01 numbers (2,387 rows, all 3,548 nodes complete), the dry run should report
about 2,387 to settle and 0 left. A large "node gone" count means nodes were deleted with their
job rows still open; those rows still count against the caps. Report it rather than deleting them.

**Keep `AINDY_ASYNC_MAX_CONCURRENT_PER_USER` and `_GLOBAL` unset until the command has run.**
After that, the boot warning's recommendation is safe to follow.

We used `success` rather than a new `superseded` status. A new status is a value every reader of
`job_logs` would have to learn, and `result.superseded_by` already says what did the work.

## 2. The runtime's `Dockerfile` upgrades setuptools (#870)

The image's runtime stage now runs `pip install --upgrade "setuptools>=84.1.0"`. Your `Dockerfile`
is single-stage and already installs `[mcp,security]`, so **nothing changes for you**. This matters
only to someone deploying the runtime's own image. The 2.29.0 handoff's §2 correction (#869) still
stands: the floor reaches a wheel install only through `[security]`.

`publish.yml` job names and the `production` gate are unchanged, so your release watcher is
unaffected.

## 3. Asks

1. If you are still on 2.28.2: do the asks in the 2.29.0 handoff's §1 (read the counter before
   recreating; #506/#507 in the image).
2. Bump the pin to `==2.29.1`, install `[mcp,security]`, rebuild, and run the in-container check above.
3. Run `memory settle-embedding-logs`: a dry run first, then `--yes`. Report the four counts
   (examined, settled, node pending, node gone).
4. Optionally, after a day, check that no new `memory.generate_embedding` row has stayed
   `pending` with `started_at` NULL for longer than a minute.
