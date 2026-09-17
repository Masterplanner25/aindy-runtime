---
title: "Audit Correlation — Design"
api_version: "1.0"
last_verified: "2026-09-17"
status: current
owner: "platform-team"
---

# `AUDIT-CORRELATION-1` — the three joins, re-measured; two payload keys close what is left — design

**DESIGN ONLY — nothing shipped. Proposal under `AGENT_WORKING_RULES.md` §8 only nominally —
additive payload keys on existing events, no schema, no behaviour change.** The entry lists
three joins the audit trail cannot make and says two "fall out" of other entries. §1 re-measures
all three at HEAD: one has already fallen out, one was mis-described, and the third is two
keys. §3 is what not to build (a foreign key).

---

## 1. The three joins, re-measured

| # | Join | Entry said | At HEAD |
|---|---|---|---|
| 1 | capability → event | *falls out of `AUTHORITY-VALUE-1`'s `ExecutionAuthority`* | **`ExecutionAuthority` does not exist** — `AUTHORITY-VALUE-1` closed as the `child_context` clamp, no object. `SYSCALL_EXECUTED`'s payload (`syscall_dispatcher.py:1128`) carries `syscall_name`, `execution_unit_id`, `status`, `extension_call` — **not the capability the entry required, nor the guarantee**. The tool path's `capability.allowed` event does carry `allowed_capabilities` + `granted_tools`. So (1) is open on the syscall path, closed on the tool path, and has no other entry to fall out of. |
| 2 | environment → execution | *falls out of `EXEC-ENV-BIND-1`* | **fell out**: `execution_units.env_applied` (JSONB) records what bound to the unit. The *attestation* half (the strong runner's self-reported `mount_mode`/`network_policy`) is `SANDBOX-EVIDENCE-2`, a different entry. (2) closes here by citation. |
| 3 | `EffectRecord.action_id` → `SystemEvent` | *joined by `trace_id` convention, no FK* | **`EffectRecord` has no `trace_id` column.** It has `execution_id` (FK `execution_units.id`); `SystemEvent` has `trace_id` (equal to the unit id only in "standard `PersistentFlowRunner` runs", `syscall_registry.py:64`) and `payload.execution_unit_id`. The real join today is `effect_records.execution_id = system_events.payload->>'execution_unit_id'` — unindexed JSONB on one side, and it yields *every* event of the unit, not the one dispatch that produced the effect. The `action_id`, the one key that names the dispatch, is on neither event. |

**So the standalone work is two additive keys on the `SYSCALL_EXECUTED` payload, both already
in scope at the emit site** (`_gate_action_id`, `entry.capability`, `entry.execution_guarantee`
are locals of `dispatch`; `_emit_syscall_event` needs two more arguments):

```python
payload = {
    "syscall_name": name, "execution_unit_id": …, "status": status, "extension_call": …,
    "capability": entry.capability,            # (1) — the authority the dispatch required
    "guarantee": entry.execution_guarantee,    # (1) — what the gate was asked to guarantee
    "action_id": _gate_action_id,              # (3) — None unless the idempotency gate engaged
}
```

and the mirror on the tool path: `capability.allowed` gains `action_id` once `_action_id` is
computed (it is computed *after* that event today — the event moves below the gate, or a
second key rides `agent.step.*`; the implementing PR measures which is cheaper).

---

## 2. The join, written down

With the keys in place, the reconstruction query the entry wants is one line each way and is
recorded in `docs/runtime/IDEMPOTENCY_CONTRACT.md` as the *documented* join:

- effect → dispatch: `system_events WHERE type='syscall.executed' AND payload->>'action_id' = :action_id`
- dispatch → effect: `effect_records WHERE action_id = :payload_action_id` — hits
  `uq_effect_records_action_id`, already unique-indexed.

**Retention, checked rather than assumed:** `SYSEVENT-RETENTION-1` classes `syscall.executed`
as **`operational`** (`system_event_retention.py:108`), not `audit` — a window, then pruned. The
ledger side has its own window too (`_cleanup_expired_effect_records` reaps completed rows by
TTL). So the join is **time-bounded on both sides by construction**, and that is the honest
shape: an effect record is a dedup key with a TTL, not a permanent audit row. What the retention
entry protected is the `error.*` / `capability.*` audit classes, which stay KEEP. This design
does not reclass `syscall.executed`; a deployment that wants the join for longer sets
`AINDY_SYSEVENT_RETENTION_OPERATIONAL_DAYS` to match its effect TTL (recorded as a decision
below, so it is not re-derived).

## 3. What not to build

- **Not a foreign key** in either direction. `EffectRecord` is written inside the gate's own
  session and committed before the handler runs (`_resolve_effect_record` commits — CLAUDE.md
  EffectRecord rules); the event is written after, on a separate session, under a swallowing
  `try` (`EVENT-OUTBOX-1`). An FK from effect to event would require the event first; from
  event to effect it would make a swallowed-emit failure a constraint violation. Convention
  keyed on a unique-indexed column is the honest shape; `SYSEVENT-RETENTION-1` protects it.
- **Not a `trace_id` column on `EffectRecord`** — `execution_id` is the stronger key and it is
  already there.
- **Not an `ExecutionAuthority` object** to satisfy the entry's wording — the capability string
  plus the run's granted set (`capability.allowed`) is what admitted the call.
- **Not a GIN index on `payload`** — `action_id` lookups are forensic, not hot.

## 4. Decisions this design asks for (`DEC-NNN` in the implementing PR)

1. Joins (1) and (3) close by three additive keys on `SYSCALL_EXECUTED` (`capability`,
   `guarantee`, `action_id`) and `action_id` on the tool path's admission event; no schema.
2. Join (2) is closed by `EXEC-ENV-BIND-1`'s `env_applied`; the attestation remainder is
   `SANDBOX-EVIDENCE-2`, not this entry.
3. The join stays a documented convention on a unique-indexed column; no FK in either
   direction.
4. `syscall.executed` stays `operational`; the join is time-bounded by the shorter of the
   event window and the effect TTL, and the contract doc says so.

## 5. Tests

- Dispatch a gated `EXACTLY_ONCE` syscall through the real dispatcher; read the emitted
  `syscall.executed` row and the `effect_records` row through a separate session; assert
  `payload['action_id'] == effect.action_id` and `payload['capability'] == entry.capability`.
- A non-gated syscall → `action_id` is `None`, `capability` present (control for the gate
  branch).
- Tool path: `execute_tool` with idempotency on → the admission event carries the same
  `action_id` the ledger row has.
- Mutation: drop the key from the payload → both joins go red.
- The system-event contract baseline (`tests/baselines/system_event_contract.json`) is
  payload-additive-safe; pinned by the existing contract test staying green.
