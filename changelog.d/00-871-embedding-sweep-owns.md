### Fixed — embedding jobs were recorded `pending` and never run, holding async admission slots (#871)

- A memory embedding job submitted while the scheduler ran was written to `job_logs` as
  `pending` and never executed. `enqueue_embedding` passed `execute_inline_in_test_mode=False`,
  and the submit path returned on that flag before the executor. The one-minute sweep embedded
  every node anyway, so memory was complete, but the rows never closed. One live stack had
  2,387 of them, the newest `success` two weeks old.
- Those rows count against `AINDY_ASYNC_MAX_CONCURRENT_PER_USER` and
  `AINDY_ASYNC_MAX_CONCURRENT_GLOBAL`. With either cap set below the stranded count, every async
  job for the affected user (or every user) is refused with a 429.
- Now (DEC-104, FR-56): on the instance running the scheduler the sweep owns embedding and no
  job row is written. Elsewhere the job is still submitted and closes its own row. The early
  return is gone, and the sweep closes any open row for a node it embeds.

**Operator action after upgrading:** rows already stranded stay `pending` until you settle them.

```bash
aindy-runtime memory settle-embedding-logs          # reports only
aindy-runtime memory settle-embedding-logs --yes    # settles them
```

It settles only a never-started `memory.generate_embedding` row whose node is already embedded,
as `success` with `result.superseded_by: memory.embedding_sweep`, in committed batches. Rows
whose node is still pending or deleted are left alone and counted. Leave the async caps unset
until it has run.
