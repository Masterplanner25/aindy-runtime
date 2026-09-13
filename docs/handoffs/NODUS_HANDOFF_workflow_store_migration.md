---
title: "Nodus — Workflow Store Migration Handoff"
api_version: "1.0"
last_verified: "2026-09-09"
status: current
owner: "platform-team"
---

# Nodus — workflow store migration handoff

Written from `aindy-runtime` for whoever is working in the Nodus ecosystem. **Every fix here is
Nodus-side**; nothing in this document is work for the runtime. It is written down because the
runtime is what surfaced it, and because the item in §1 makes the **6.0.0 store flip's own
mitigation report a number that is not the number of records in the store** — which is more
expensive the closer 6.0.0 gets.

Measured against `nodus-lang` **5.13.0**, installed, on 2026-09-09. Companion to
`NODUS_HANDOFF_a2a_mcp_packaging.md`.

Runtime-side context lives in `TECH_DEBT.md` → `ORCHESTRATOR-SPLIT-1` (store 4).

---

## 1. ★★ `migrate-store` silently migrates a truncated census, and reports success

**This is the important one.** `nodus workflow migrate-store` exists specifically so the 6.0.0
default flip (`LocalWorkflowStore` JSON → `SQLiteWorkflowStore`) does not strand records. Its own
docstring is explicit about the stakes:

> *"Runs recorded in the JSON store are **invisible** to a SQLite one, so flipping the default
> without this would silently make every in-flight `waiting` run unresumable at the moment of
> upgrade — a data-loss bug that looks like nothing at all until someone waits for a webhook that
> never lands."*

**The migration inherits exactly that failure mode from one layer down.**

### Measured

Against this repo's store, `.nodus/workflow_framework/runs`, holding **629** run records:

```
$ nodus workflow migrate-store --to sqlite --dry-run
{"dry_run": true, ..., "migrated_count": 432, "skipped_count": 0, "failed_count": 0,
 "waiting_count": 0}
```

**432 of 629.** `skipped_count: 0`, `failed_count: 0`. Nothing in the report accounts for the
missing **197**.

### Cause

`migrate_workflow_store` (`store.py:1446`) iterates `source.list_runs()`. For the JSON store that
reaches `LocalWorkflowStore._list_runs_unlocked` (`store.py:952`), which applies a scan-cost
filter before it loads anything:

```python
cutoff_s = time.time() - self.terminal_max_age_days * 86_400.0   # default 30.0
...
if cutoff_s is not None and entry.stat().st_mtime < cutoff_s:
    continue  # skip files not touched in terminal_max_age_days (old completed runs)
```

`terminal_max_age_days` defaults to `30.0`. An independent count of file mtimes against a 30-day
cutoff gives **432 within / 197 outside** — an exact match with the migration report, so the
mechanism is confirmed, not surmised.

### Why the report cannot reveal it

`skipped` means *"already present in the target"*; `failed` means *"raised while copying"*. There
is no third bucket for *"never enumerated"*, because from `migrate_workflow_store`'s point of view
those records **do not exist**. The truncation happens below the layer that writes the report, so
every count in the report is internally consistent and collectively wrong.

The `--dry-run` reports the same truncated number, so rehearsing the migration cannot surface it
either. This is the one property an operator would reasonably rely on.

### Why it matters more than "old records are old"

The filter's comment says *"old completed runs"*, but the predicate is **file mtime**, not status.
It does not consult `status` at all. A `waiting` run parked on a webhook that has not fired for 31
days is untouched by definition — **waiting is precisely the state that produces no writes** — so
the population the flip endangers is the population most likely to age past the cutoff. The
migration's `waiting_count`, the number its docstring says an operator should check, is computed
over a census that structurally under-represents waiting runs.

The same filter also means such a run is *already* invisible to `_rehydratable_run_records`, so
this compounds an existing behaviour rather than introducing one.

### Suggested fixes, Nodus-side

1. **`list_runs()` must not silently truncate.** A scan-cost optimisation belongs behind an
   explicit argument (`list_runs(max_age_days=…)`), not in the default path that a migration, a
   rehydration sweep and an operator's `nodus check` all read.
2. **Failing that, exempt non-terminal records from the filter.** The comment already claims the
   skip is for *"old completed runs"*; making the predicate match the comment (`status in
   TERMINAL_RUN_STATUSES and mtime < cutoff`) removes the dangerous half at negligible cost — a
   `stat` is already being taken.
3. **Have the migration report its denominator.** Count files in the source directory and compare
   against what was enumerated; report the difference as its own field. A migration that cannot
   see a record should still be able to see that it cannot see it.

(1) or (2) is the fix; (3) is the guard that would have caught it and is worth having regardless.

---

## 2. `--dry-run` creates the target store file

Minor, but surprising in a command whose entire value is being safe to rehearse.

```
$ ls .nodus/*.sqlite3          # before: no such file
$ nodus workflow migrate-store --to sqlite --dry-run
$ ls -la .nodus/workflow_framework.sqlite3
-rw-r--r-- 16384 .nodus/workflow_framework.sqlite3     # created; workflow_runs table, 0 rows
```

The target `WorkflowStore` is constructed before the `dry_run` flag is consulted, so the SQLite
file and its schema are created. **No rows are written** — verified, `select count(*)` returns 0 —
and the source is untouched, so this is not data loss. But a rehearsal that leaves a new store
behind is a side effect, and an empty SQLite store sitting at the exact default path is
indistinguishable, on inspection, from a completed migration that carried nothing.

Suggested fix: construct the target lazily, or delete it on the dry-run path when it did not exist
beforehand.

---

## 3. `NODUS_WORKFLOW_STORE_ROOT` is documented in a docstring that no longer owns the behaviour

Not a defect — a documentation drift worth one line, because it sent this runtime's own tech-debt
entry at the wrong variable for three weeks.

`runner.default_store_root()` (`runner.py:1421`) carries the full explanatory docstring for
`NODUS_WORKFLOW_STORE_ROOT`, and reads like the place the variable is defined. The behaviour
actually lives in `nodus/runtime/state_paths.py`, whose module docstring is emphatic that
`NODUS_WORKFLOW_STORE_ROOT` is now **the narrower, legacy gesture**, that `NODUS_RUN_STATE_ROOT`
is the supported knob because it moves both halves of a run's state together, and that using the
legacy one alone is the half-relocated state `#585` exists to prevent.

Anyone reading `default_store_root()` first — the natural entry point from
`configure_default_workflow_runner` — gets the superseded advice with no hint that it is
superseded. A one-line pointer in that docstring would close it.

---

## 4. What the runtime is doing about its side

For completeness, so this document is not read as a request:

- The runtime declares **no** store configuration today — zero references to
  `nodus_lang_workflow`, `WorkflowStore`, `NODUS_RUN_STATE_ROOT`, `NODUS_WORKFLOW_STORE_BACKEND`
  or `configure_default_workflow_runner` under `AINDY/`. That is tracked as
  `ORCHESTRATOR-SPLIT-1` store 4 and needs a proposal under `AGENT_WORKING_RULES` §8 before it
  changes, because choosing a durability substrate is a runtime behaviour change.
- Nothing in this repo is in-flight in that store: of 629 records, **zero are `waiting`**. Two are
  `running` with `claim: null` — orphans of processes that died mid-run, not live work.
- So the runtime is **not blocked** by §1, and is not asking for a fix on a deadline. It is
  reporting a defect it can currently absorb, on the theory that a host with a live `waiting` run
  cannot.
