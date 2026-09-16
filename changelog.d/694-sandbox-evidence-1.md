### Fixed — an unverified strong-sandbox worker could be left running after a restart (`SANDBOX-EVIDENCE-1`, #694)

- `_start_record` (`AINDY/platform_layer/plugin_host.py`) now has one failure path for every
  post-launch check: mark the host with the failure's kind, force-kill the worker, re-raise —
  for every caller. Before, a failed strong-sandbox live verification
  (`_verify_post_launch_state` ≠ passed) only raised; `start_plugin_host` caught and killed
  it, but `restart_plugin_host` and `execute_plugin_host`'s restart sites do not wrap the
  call, so through them the unverified worker stayed alive with the host reporting `running`.
- `start_plugin_host` no longer marks a launch failure a second time. One hostile-third-party
  attestation violation used to be recorded as two failures with `last_failure_kind`
  relabelled `runtime_failure`; it now reads `contract_violation` once, and the restart
  backoff is computed from one failure, not two.
- Behaviour visible to an operator: after a failed post-launch check on any path, the host
  snapshot reports `lifecycle_state: backoff`, `pid: null`, and the real failure kind. A
  quarantined host asked to start again is no longer counted as a fresh failure.
- Test: `tests/unit/test_plugin_host_attestation_kill.py` — the real strong runner over a
  fake process, one attestation field broken, the process asserted dead through both entry
  points; mutation-tested 5/5. Closes the contract's invariant-11 coverage gap.
