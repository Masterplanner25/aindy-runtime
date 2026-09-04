### Changed — the response envelope can say "partly applied" and "outcome unobserved" (`EFFECT-PARTIAL-1`, `EFFECT-OUTCOME-UNKNOWN-1`)

**Operators and API consumers must read this before upgrading.**

- **`status` in the syscall response envelope may now be `partial` or `unknown`, not only
  `success` or `error`.** A batched effect where some units applied and some did not was
  previously forced through a binary envelope, making it either a **lie** (`success`, silently
  partial) or a **waste** (`error`, discarding the units that landed). Neither was recoverable
  afterwards, because nothing recorded which units applied.
- **The contract for consumers: treat any status that is not `success` as not-success and
  reconcile.** Do not branch on `== "error"`. Four in-runtime sites did, and at each of them a
  `partial` would have fallen through to the success path — they were fixed here, and a test now
  prevents a fifth.
- **Nothing emits the new values yet, so this release changes no response you receive.** A
  handler opts in explicitly via `AINDY.kernel.syscall_outcome`; a test asserts none does today.
  The widening ships first precisely so that consumers can be ready before the first emitter.
- **The envelope gains an `outcome` key** — `None` normally, and `{"units": [...], "detail": ...}`
  for a partial or unknown outcome. Present on every envelope including errors, so the key shape
  is stable.
- **`EffectRecord.status` is now written from the same resolution.** #560 added `partial` to the
  column and the dispatcher's ledger write passed a hardcoded `"success"`, so the value could be
  stored and no code path could ever store it. The record is what an operator reconciles from
  once the response is gone, which makes this the half that mattered most.
- **A `partial` that cannot name its units is refused**, returning an error envelope and
  recording `failed` — the entry's own reading of that situation. It is refused rather than
  raised, because the effect has already happened and raising would discard the handler's data.
- New metrics: `aindy_syscall_outcome_total{syscall,status}` and
  `aindy_syscall_outcome_refused_total{syscall,reason}`. A non-zero refusal count is a handler
  bug, not a workload property.
