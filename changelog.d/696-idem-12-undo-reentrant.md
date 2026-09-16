### Fixed — a second `sys.v1.agent.undo` re-invoked every compensator (`IDEM-12`, #696)

- `undo_run_effects` (`AINDY/core/effect_compensation.py`) is now re-entrant: an effect that
  already carries a `reversed` row in `effect_reversals` is skipped and listed under a new
  `already_reversed` key in the summary, so a deliberate second undo — or a retry with the
  idempotency gate off — never runs a compensator twice and never writes a duplicate
  `reversed` audit row. Only `reversed` suppresses; `irreversible` and `failed` rows leave the
  effect eligible, so a transient compensator failure stays retryable.
- Latent until now: zero compensators are registered at HEAD, so no deployment has double-
  compensated, and with none registered the only visible change today is the new key. An
  effect with no compensator is still re-surfaced (and re-logged) as `irreversible` on every
  undo, by design — surfaced, not hidden.
- Consumer-visible: `sys.v1.agent.undo`'s response gains `already_reversed: [action_type…]`
  (additive; the required keys are unchanged). No schema change.
