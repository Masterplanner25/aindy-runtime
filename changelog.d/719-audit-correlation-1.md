### Added — `AUDIT-CORRELATION-1`: the audit trail can join an effect to the dispatch that produced it (#719; DEC-052..055)

- `syscall.executed` payload gains three additive keys: `capability` (the authority the dispatch
  required), `guarantee` (the entry's declared execution guarantee) and `action_id` (the effect
  ledger row's unique key — `null` unless the idempotency gate engaged, so a reader can tell "no
  effect record" from "key dropped"). Every emit site, success and error.
- The tool path's admission event `capability.allowed` gains `action_id`. The id is now computed
  before the event (a pure hash of tool, args and run scope); the ledger is consulted where it
  always was, so event order is unchanged.
- The join is a **documented convention** on `uq_effect_records_action_id` — no foreign key in
  either direction, no schema change — and it is **time-bounded on both sides** (`syscall.executed`
  stays `operational` retention; effect records keep their TTL). The queries and rules are in
  `docs/runtime/IDEMPOTENCY_CONTRACT.md` §"Reconstruction join".
- Consumers: the keys are additive; nothing is renamed or removed.
