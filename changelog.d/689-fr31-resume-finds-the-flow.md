### Fixed — a run parked across a restart resumes on the first wake of a fresh process (app FR-31, `RESUME-FLOW-UNREGISTERED-1`, #689)

- **A Nodus run parked before a restart could not be resumed until some script had run in the new
  process**, and the first attempt silently orphaned it (`resumed: true` on the wire, run `waiting`
  forever, one `not in FLOW_REGISTRY` WARNING). `nodus_execute` is now registered at boot — in the
  API and in the worker, which never registered the runtime-owned flows at all — and the resume
  path resolves it on a miss as well. Pre-existing on every release; found by the app team on the
  2.17.0 upgrade's own verification step. The "run any script first" workaround is no longer needed.
- **An agent run parked by the authority WAIT gate (2.17.0) now survives a restart.** Its flow
  name, `agent_execution`, was never registered anywhere. It is resolvable for **resume only** —
  deliberately not registered publicly, since a `FLOW_REGISTRY` entry is startable through
  `sys.v1.flow.run` and the agent flow checks tool capability only when an `execution_token` is
  present (DEC-020).
- **A wake whose flow this process genuinely does not hold re-arms the wait** instead of consuming
  it; the run is not claimed and the next wake (after the plugin loads, or on another instance)
  resumes it.
- **`POST /platform/flows/runs/{id}/resume` now reports `woken`** on each result and `resumed`
  means *woken*, not "payload stored". A resume with nothing registered to wake answers
  `payload_injected: true, woken: false, resumed: false` (200) and warns; the payload stays on the
  row and is delivered on the next wake after rehydration. Clients that keyed on `resumed: true`
  to mean "stored" should read `payload_injected`.
