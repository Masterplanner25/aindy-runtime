### Fixed — one correlation rule for waking a wait, on every instance; a run-scoped wake is decisive (`WAIT-PAYLOAD-PATH-1`, #678)

- **`POST /platform/flows/runs/{id}/resume` no longer fails silently when the payload carries a
  `correlation_id` key.** The route read that key as the wake's correlation; if it differed from
  the run's trace (a client's own reference always does), the payload was injected and the wake
  was vetoed — `resumed: true` on the wire, run parked forever. A wake that names a run is now
  never vetoed by correlation. If you have runs stuck `waiting` with `state.event` already set,
  this is why; resume them again.
- The local scan and the cross-instance fallback applied different correlation rules — a wait
  registered without one resumed locally on any emit and never on another instance (it waited
  for the resume watchdog). One predicate now (`scheduler/common.py::correlation_admits`): veto
  only when both the wait and the emit carry a correlation and they differ.
- Decided and documented: the event bus never carries a payload. The payload's home is the run's
  row, committed before the wake — which is what makes a resume reconstructible from `run_id`
  alone. Any future payload path writes the row, then wakes by `run_id`.
