### Added — reconcile an effect whose outcome is unknown (#793)

- An `unknown` effect is held on replay and never reaped (#790), so until now nothing could ever move
  it: the only way out was a hand-written UPDATE against `effect_records`, with no record of who did
  it or why.
- `GET /platform/effects/unknown` lists the unresolved effects, oldest first, with the detail the
  emitter recorded. `POST /platform/effects/{action_id}/resolve {"status": "success"|"failed", "note"}`
  settles one: `success` means it landed, so later calls replay it as an ordinary success; `failed`
  means it did not, so the slot is freed and a retry runs. Any row that is not `unknown` is refused
  with 409. The note is required.
- Each resolution writes an `effect.reconciled` system event (who, to what, the note, the recorded
  detail) in the same transaction as the change; if the event cannot be written, nothing changes. It is
  kept under the audit retention class. `aindy_effect_unknown_unresolved` is recounted.
- Admin only: the `/platform` admin gate, plus the `platform.admin` scope for an API key.
- EFFECT-OUTCOME-UNKNOWN-1 is CLOSED. The optional agent-run park was not built.
