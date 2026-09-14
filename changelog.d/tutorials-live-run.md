### Fixed — the tutorials, corrected against a live 2.13.0 server; four runtime defects filed (docs/tutorials-live-run)

The three tutorials' complete scripts were run verbatim against a real server with the published
`aindy-sdk 1.0.0`. Tutorials 1 and 3 now complete end to end. What the run corrected in the docs:
the login and Nodus-run responses are pipeline envelopes (`data.access_token`,
`data.output_state`); the flow-run GET is `data.flow_run_get_result`; the execution graph is
`data.observability_rippletrace_result`; `nodus_status` is lowercase / `None` on a WAIT; webhook
subscriptions need `owner_class: first-party-app`; the schedule routes answer unwrapped and
`next_run_at` is `None` on the listing; the tenant helper must be inlined per script; the SDK's
`events.emit` and `upload_script` send the wrong keys and are routed around.

**Tutorial 2 cannot complete on 2.13.0 and now says so at the top.** Running it found that a
Nodus script can suspend a run but can never receive what resumed it — the WAIT patch carrying
`nodus_wait_event_type` is never merged into run state, so the resume bridge never fires
(`NODUS-RESUME-BRIDGE-1`, subsuming `WAIT-PAYLOAD-PATH-1`). Three more, all observed live:
`RESUME-FANOUT-UNSCOPED-1` (the resume route injects into every run waiting on that event, any
tenant), `ACTIVE-COUNT-WAIT-LEAK-1` (a waiting run holds a concurrency slot until restart; four
parked waits 429'd a GET), `ASYNC-JOB-UNREGISTERED-STORM-1` (an unregistered handler is
re-dispatched ~87×/s forever with no backoff). `NODUS_DEVELOPER_GUIDE` §4 and the SDK handoff
updated; the handoff is now the list of 1.0.0's four wire mismatches.
