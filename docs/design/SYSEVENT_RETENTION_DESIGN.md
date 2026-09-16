---
title: "System Event Retention — Design"
api_version: "1.0"
last_verified: "2026-09-16"
status: current
owner: "platform-team"
---

# `system_events` retention — design

**`SYSEVENT-RETENTION-1`. DESIGN ONLY — nothing shipped. Proposal under `AGENT_WORKING_RULES.md`
§8 (a background job that deletes rows is a runtime behaviour change).** The entry's shape —
a retention *class per type*, default keep, log what was dropped — holds. What the entry did
not have is §2: the table's foreign keys already decide most of the question, and they decide
it in a way that makes the naïve `DELETE` fail rather than over-delete. §3 is the census the
entry's "per type" needs and does not have; §7 is what not to build.

---

## 1. The finding, restated with what has changed since filing

`system_events` is the table every execution, signal and causal edge lands in, and nothing
prunes it. FR-18 removed the per-probe health snapshot (3.3 GB of a 3.8 GB database); growth is
now proportional to work, which is the right shape, and still unbounded. Two other entries
depend on rows *not* disappearing: `EVENT-OUTBOX-1` reads a missing row as "the work never
happened", and `AUDIT-CORRELATION-1` joins `EffectRecord.action_id` to this table by convention
with no FK. A blanket age policy would delete the audit trail and keep the keepalives.

Nothing about that has changed. What has: the model has been read for what it *enforces*.

---

## 2. ★★ The foreign keys decide more than the entry assumed — and they fail closed

Five columns reference `system_events.id` (measured at HEAD, `grep ForeignKey("system_events`):

| Referrer | Column | `ondelete` | Effect of deleting the referenced event |
|---|---|---|---|
| `system_events` (self) | `parent_event_id` | none → `NO ACTION` | **the DELETE fails** while any child row exists |
| `agent_events` | `system_event_id` | none → `NO ACTION` | **the DELETE fails** |
| `memory_nodes` | `source_event_id`, `root_event_id` | none → `NO ACTION` | **the DELETE fails** |
| `event_edges` | `source_event_id`, `target_event_id` | **`CASCADE`** | **the edge row vanishes silently** |

Two consequences, and they shape the whole design:

1. **A referenced event is structurally audit, whatever its type.** An event that is a parent,
   that an `AgentEvent` cites, or that a memory node was derived from cannot be deleted at all
   under three of the five constraints — Postgres refuses. So the prune's eligibility predicate
   is not "type is old enough"; it is **"type is old enough AND nothing references the row"**.
   Written the other way round, a batch `DELETE` on a real deployment aborts on the first
   parent row it meets, and a job that aborts every hour is `SYSMAX-5`'s maintenance brownout
   with a new cause.

2. **The one `CASCADE` is on the causal graph.** `build_trace_graph` and
   `get_downstream_effects` (`event_trace_service.py:80`, `:125`) read `event_edges`. Deleting
   an event that is an edge's source or target does not fail — it takes the edge with it, and
   the graph reads as if that causal link never existed. **That is exactly the "missing row
   reads as never happened" failure `EVENT-OUTBOX-1` describes, reachable by a prune that
   passes.** So `event_edges` must be in the referrer check *even though the database would
   let the delete through* — the one place the FK's silence is the hazard.

**The rule that follows: prune leaves only.** Eligible rows are those with no inbound reference
from any of the five columns. A leaf is, by construction, an event nothing was derived from,
nothing descends from, and nothing links to — which is what a keepalive or a per-step trace
row *is*, and what an audit row almost never is. The type classes below refine that; they do
not replace it.

---

## 3. ★ The census — "per type" needs the set of types, and the enum is not it

`SystemEventTypes` (`core/system_event_types.py`) declares 46 names. Measured at HEAD, the
runtime also emits **22 literal type strings that appear in no enum** (a regex over
`type="…"` literals, minus the enum's values), among them `watchdog.scan.completed` (the entry's 16,648-row
example), `capability.denied`, `auth.login.completed`, `platform.secret_key.rotated`,
`dlq.drained`, `external.call.*`. And the app registers its own through
`register_event_type` (`registry.py:757`) — `autonomy.decision`'s 25,377 rows on the FR-18
stack are one runtime type; the app's are invisible from here.

Three consequences:

- **The class registry cannot be a literal table keyed on the enum** (green-check variant 12
  — a hand-written census inside a guard). It is a registry that the runtime seeds for its own
  types and the app extends through the same `register_*` surface it already uses.
- **Unclassified means keep.** The entry says so; the census says why it is not optional — at
  HEAD roughly a third of the runtime's own types would be "unclassified" on day one, and
  every app type would be.
- **The pressure to classify must be a number, not a reminder.** A gauge of rows in
  unclassified types (§5) is what turns "someone should add a class" into something an
  operator sees grow.

---

## 4. The classes, and the seed table this design proposes

```python
# AINDY/core/system_event_retention.py (proposed)
RETENTION_CLASSES = ("audit", "operational", "keepalive")

# Defaults, overridable per deployment; None = never by age
DEFAULT_MAX_AGE_DAYS = {"audit": None, "operational": 90, "keepalive": 7}
```

| Class | Meaning | Seed members (runtime-emitted; ★ = the decision this design asks for) |
|---|---|---|
| `audit` | a record someone may need to *prove* something with; never pruned by age | `execution.failed`, `flow.node.failed`, `agent.step.failed`, `capability.*`, `auth.*`, `platform.*`, `dlq.drained`, `flow_run.dead_lettered`, `startup.recovery.*`, `next_action.*`, `external.call.failed`, `error.*`, `WAIT_TIMEOUT` |
| `operational` | the execution ledger; valuable for a window, then only as volume | `execution.started`, `execution.completed`, `execution.waiting`, `execution.step.completed`, `flow.node.started/completed`, `flow.waiting`, `agent.step`, `agent.step.completed`, `async_job.*`, `nodus.*`, `embedding.*`, `syscall.executed`, `scheduler.queued`, `memory.write`, `reasoning.signal`, `recall.used`, `score.computed`, `autonomy.decision` ★, `autonomy.window`, `external.call.started/completed`, `feedback.*`, `client.*` |
| `keepalive` | proves a loop ran; worthless after the next one | `watchdog.scan.completed`, `health.liveness.completed` |

★ **`autonomy.decision` is the one to decide, not derive.** It is 25k rows of "deferred"
verdicts on the FR-18 stack and reads as operational; but it is also the only record of *why*
an autonomous trigger did not fire, which an operator asks about after the fact. Proposed:
`operational` with the 90-day default, on the grounds that a decision old enough to prune is
one nobody is still asking about — and that the leaf rule keeps any decision that led to a
dispatch (it becomes a parent). If that is wrong, the class is one registry line.

**Failure-shaped events are audit regardless of family** (`execution.failed` is audit while
`execution.started` is operational). A failure is the row a support conversation starts from;
its siblings are the rows that say the run existed.

---

## 5. The job

Reference shape: `_cleanup_expired_effect_records` for the scheduler-job pattern,
`prune_cascade_debris` for committed batches.

```
_prune_system_events()           # scheduler job, interval AINDY_SYSEVENT_RETENTION_INTERVAL_HOURS (24)
  for each (type, class) with a max age:
    loop:
      ids = SELECT id FROM system_events e
            WHERE e.type = :type AND e.timestamp < :cutoff
              AND NOT EXISTS (child)        -- parent_event_id
              AND NOT EXISTS (agent_event)  -- agent_events.system_event_id
              AND NOT EXISTS (memory ref)   -- memory_nodes.source_event_id / root_event_id
              AND NOT EXISTS (edge)         -- event_edges.source_event_id / target_event_id
            LIMIT :batch
      DELETE WHERE id IN ids; COMMIT
      until fewer than :batch
  log one line per type: "pruned N rows of <type> (class=<c>, older than <d>d)"
  counter aindy_system_events_pruned_total{type}  += N
```

- **Committed batches**, default 1,000, so a five-week backlog is never one transaction
  holding a pooled connection — the `RT-MEMTXN-LEAK-1` lesson that `prune_cascade_debris`
  already encodes.
- **A silent prune is indistinguishable from a lost write** (the entry's own line), so the
  job logs *per type* and counts *per type*. The counter is the operator's signal; the log line
  is the human one. Both say zero explicitly when nothing was eligible.
- **The pressure gauge:** `aindy_system_events_unclassified_rows` — rows whose type has no
  registered class, sampled by the job from a `GROUP BY type` it already has to run. Growth
  there is the ask to classify; it never deletes.
- **Dry-run first.** `AINDY_SYSEVENT_RETENTION=report` (the default on first ship) runs the
  selection and logs the counts without deleting; `=prune` deletes. An operator reads one
  report before the first real run. `unset` = off, the current behaviour.
- **Leader-only**, like every maintenance job; see `LEASE_FENCE_DESIGN.md` — this is one of
  the few jobs whose re-run by a stale leader is *not* harmless in the audit sense, which is
  why it is a candidate for the fence's first consumer.

---

## 6. What this does not need

- **No schema change.** Class lives in a registry, not a column; eligibility is computed from
  the FKs that exist. `type` is already indexed and `timestamp` is already indexed; the
  selection is a per-type range scan plus four anti-joins on indexed columns.
- **No new marker on emit.** `emit_system_event` is untouched; a type's class is looked up at
  prune time, so classifying a type later applies to rows already written.
- **No change to `event_edges`' `CASCADE`.** Turning it into `NO ACTION` would make the
  database refuse what the job already refuses, at the cost of a migration; the leaf rule is
  the guard, and a test pins that an event with an edge is never selected.

---

## 7. What not to build

- **Not a documented `DELETE` for the operator** — the entry's own prohibition; that is what
  every FR-18 deployment runs by hand today.
- **Not an age-only policy with a type allowlist.** Default-delete with exceptions is the
  shape that deletes a new type by omission; the class registry with unclassified=keep is the
  inverse and the only safe default.
- **Not a class column on `system_events`.** It would need a contract bump, and a row's class
  is a property of its *type*, decided after the fact — a column freezes the decision at write
  time, which is the one time nobody knows.
- **Not archival to a second table.** Rows worth keeping are kept; rows not worth keeping are
  not worth moving. If cold storage is ever wanted it is an export, not a retention class.

---

## 8. Decisions this design asks for (`DEC-NNN` in the implementing PR)

1. **Prune leaves only** — a referenced row is never eligible, including via `event_edges`
   despite the `CASCADE` (§2).
2. **Unclassified = keep**, with the pressure gauge as the classify signal (§3).
3. The seed table in §4, and specifically `autonomy.decision` = `operational`.
4. Defaults: operational 90 d, keepalive 7 d, audit never; ship as `report`, not `prune`.

## 9. Tests that must exist (and the liveness control for each)

- An event with a child / agent_event / memory node / edge is **not** selected — and the same
  event with the referrer removed **is** (the control; without it the anti-joins could be
  vacuously true).
- An unregistered type is never selected regardless of age; registering it makes it eligible.
- The batch loop commits between batches (a private engine, per the `ASYNC-JOB-…-STORM-1`
  note — the shared fixture's outer transaction hides a missing commit).
- `report` mode deletes nothing and logs the same counts `prune` would delete.
- Mutation: remove one anti-join and confirm exactly one test goes red.
