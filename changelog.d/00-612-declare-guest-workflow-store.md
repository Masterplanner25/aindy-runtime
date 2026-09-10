### Changed — the runtime now declares the guest workflow store (#612)

**Operators: read this before upgrading if you run guest Nodus workflows.** Two of the three
changes below alter where guest run state is written and how long it survives.

Nodus's workflow framework keeps its own run records, created inside the guest VM. The runtime
reads none of them — it resumes through `PersistentFlowRunner` — but it had never *declared* the
substrate they live on, and at Nodus 6.0.0 the default store flips from `LocalWorkflowStore`
(file-backed JSON) to `SQLiteWorkflowStore`, which cannot read the other's records. An undeclared
host changes durability substrate on a schedule it does not set, so the runtime now states its
choice. See `docs/runtime/WORKFLOW_STORE_DECLARATION_PROPOSAL.md`.

- **The store backend is now `sqlite`.** `NODUS_WORKFLOW_STORE_BACKEND` is declared by the Nodus
  worker itself, so no configuration is required. One database file replaces one JSON file per
  run. **Existing JSON records are not migrated and are not read** — they have no consumer in
  this runtime, so nothing that mattered is lost. Do **not** run `nodus workflow migrate-store`
  expecting completeness: it enumerates only what `list_runs()` returns, which silently drops
  records whose file mtime is older than 30 days (measured: 432 of 629 carried, reported as a
  clean success). Details in `docs/runtime/NODUS_HANDOFF_workflow_store_migration.md`.
- **Nodus's background sweep thread is now off.** `NODUS_WORKFLOW_AUTOSWEEP=0`. Nodus auto-starts
  a 30-second sweep per long-lived process, which the default-on warm pool made real. It can
  terminally dead-letter a waiting guest record and — calling `expire_wait_timeouts()` without
  `release_schedules=True` — cannot resume anything. Leaving an unowned thread mutating a store
  the runtime now preserves durably would have been worse than the ephemeral status quo.
- **Guest run state is durable in Docker for the first time.** `docker-compose.yml` sets
  `NODUS_RUN_STATE_ROOT=/var/lib/aindy/nodus-state` on both `api` and `worker`, backed by a new
  `nodus_state` named volume. Previously the store rooted at the worker process's working
  directory — `/home/aindy`, with no volume — so guest run state was lost on every container
  recreate. **This grows over time and nothing prunes it**; Nodus's `terminal_max_age_days` bounds
  the *scan*, not the directory.

**Both declared values respect an operator who has already set them** — a non-blank existing value
is never overwritten, so `NODUS_WORKFLOW_STORE_BACKEND=local` remains a supported way to pin the
JSON store deliberately.

**Two things to get right if you set the root yourself:** use `NODUS_RUN_STATE_ROOT`, not the
legacy `NODUS_WORKFLOW_STORE_ROOT` (which relocates only the record half and leaves
`.nodus/graphs/` behind); and point it at local storage, because the SQLite store runs in WAL
mode, which is unsafe on NFS/CIFS.

The Docker image now creates `/var/lib/aindy/nodus-state` owned by `aindy` before the volume is
mounted over it — a named volume covering a path absent from the image is created `root:root`, and
the non-root runtime could not write to it.

Tracked as `ORCHESTRATOR-SPLIT-1` store 4. This declares the store's configuration; it does not
resolve the split, and the ownership contract is still owed.
