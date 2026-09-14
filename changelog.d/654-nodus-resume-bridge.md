### Fixed — a suspended Nodus script now receives the payload that resumed it (`NODUS-RESUME-BRIDGE-1`) (#654)

A guest script could suspend a flow run (`set_state("nodus_wait_requested", true)`) but could
never learn what resumed it — through any path, since the feature was written. The flow runner
merged **SUCCESS** node patches only, so the WAIT patch carrying `nodus_wait_event_type` was
recorded in `flow_history` and never reached `flow_runs.state`; on re-entry the `nodus.execute`
bridge found no pending wait type, discarded the payload `POST /platform/flows/runs/{id}/resume`
had injected, and the script re-parked. Observed live as `WAIT, WAIT, WAIT…` in a run's history.

- **The runner now merges WAIT patches** (`runner_steps._MERGED_STATUSES = {SUCCESS, WAIT}`).
  FAILURE and RETRY patches still do not land. The DUR-4 history fold applies the same rule and
  a test pins the two sets equal. A WAIT is refused inside a fan-out group before anything is
  written, so a WAIT patch never conflict-resolves against sibling branches.
- **Behaviour change for any custom node that returns `WAIT` with an `output_patch`:** that patch
  is now visible to the re-run and on the run's state. Census: `nodus.execute` is the only
  WAIT-with-patch node in the runtime; the app's one WAIT node (`genesis_track_message`) sends
  no patch. Nothing relied on the drop (the patch was recorded in history, never read back).
- `nodus.execute`'s WAIT patch also carries `nodus_output_state`, so what the script set before
  parking is readable on the waiting run (it is **not** seeded back into the re-run's
  namespace — the re-run still starts from the top with only `nodus_received_events`). The
  execution record's `nodus_status` is therefore `"waiting"` on a WAIT rather than `None`.
- A wake **without** a payload (the event bus, `sys.v1.event.emit`) now logs
  `[nodus.execute] Resumed WITHOUT a payload while waiting on '<type>'` at WARNING before the
  script re-parks — previously indistinguishable from a first wait. A re-run that completes
  clears the pending type so a later payload cannot bridge into a wait that no longer exists.
- **Second defect, found by the test and latent in production:** `PersistentFlowRunner.resume()`
  aliased the ORM `run.state` dict as its working state, so after in-place merges the later
  `run.state = _json_safe(state)` assigned a value equal to what SQLAlchemy recorded as the
  original and produced **no UPDATE**. Production sessions expire on commit, which broke the
  alias before the first write; a session with `expire_on_commit=False` lost the snapshot
  outright. The runner now works on a copy. Correctness no longer depends on session expiry.
- `tests/unit/test_nodus_resume_bridge.py` drives start → WAIT → inject → resume with the real
  guest interpreter and asserts the script's **second** run; mutation-checked 4/4.
  `docs/tutorials/02-event-driven-automation.md` Step 6 now completes on a runtime carrying
  this change (its "after the fix" output is from that test; 2.13.0 still stops where it says).
