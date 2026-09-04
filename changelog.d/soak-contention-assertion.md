### Fixed — a soak assertion could fail on a correct runtime (`IDEM-11`'s contention test)

- `test_the_gate_degrades_to_at_least_once_under_contention` asserted
  `1 <= len(runs) < WORKERS`. The upper bound is a **race outcome, not a guarantee**: the
  idempotency contract says a caller that loses the insert race to a live pending row degrades
  to `AT_LEAST_ONCE`, and with barrier-synchronised callers it is entirely legal for *all* of
  them to lose it. It failed CI with `8 < 8` and passed on an immediate re-run of the same
  commit — the signature of an assertion on timing rather than a regression.
- **Replaced rather than deleted, because it was reaching for something real** ("the gate must
  not be a no-op"). A **second wave** now dispatches the same action after the first completes:
  the record is committed and terminal, nothing is racing, so every caller must replay it. That
  is the guarantee the contract actually makes, and it holds regardless of scheduler timing.
- **Mutation-verified against live Postgres and Redis**, and the first mutation was not good
  enough: disabling the gate entirely was caught by the *pre-existing* degradation-counter
  assertion before the new one ran, which would have proved nothing about the new code. Killing
  only the completed-record replay reaches it, and it fires with its own message.
