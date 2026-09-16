### Added — a resume payload is checked against what the waiting node declared (`WAIT-TYPED-CONTRACT-1` phase 1, #677)

- A flow node returning `WAIT` may declare **`resume_schema`** — in the syscall registry's own
  schema dialect (`required` + `properties[<name>].type`), validated by the same function
  `SyscallDispatcher.dispatch()` applies to syscall inputs. The runtime records it on the run
  (`state["__pending_request"] = {node, event, schema}`, only when declared) and
  **`POST /platform/flows/runs/{id}/resume` now answers `422`** — with the validator's errors
  under `detail.errors` — for a payload that does not satisfy it. Nothing is injected, nothing
  is woken; the run stays `waiting` and the caller may correct and retry.
- Nodus scripts declare it with a third state key beside the two wait keys:
  `set_state("nodus_wait_resume_schema", {...})`.
- New counter `aindy_flow_resume_payload_total{outcome="accepted|rejected|untyped"}`.
- **No behaviour change for any existing flow**: a wait that declares nothing is `untyped` and
  accepts any payload, exactly as before. Why it was worth adding: an untyped resume with an
  empty payload was *accepted*, the wait consumed, and Tutorial 2's script failed on its re-run
  — the malformed resume was not refused at the door, it destroyed the run one step later.
- Deliberately a state key, not a column — no schema step, no `bootstrap-schema --reconcile`
  owed. Consequence: the DUR-4 history fold does not reconstruct the record, so a run recovered
  from a torn snapshot resumes untyped (absent is never a mismatch).
- Recorded decision: a resumed Nodus script does **not** get its prior `nodus_output_state`
  seeded back — the two-phase run-from-the-top shape stays the contract.
