### Changed — cancelling a run now stops it at the next effect, not the next segment (`CANCEL-REACH-1`)

- `sys.v1.agent.cancel` commits a terminal status in a separate session, and the execution chain
  observed that only **between segments**. Every remaining tool in the current segment ran to
  completion. A cancelled run now refuses its **next tool call**, which narrows the window from
  segment granularity to effect granularity.
- **It is cooperative, and that is the contract, not a limitation to discover later.** A tool
  already executing is not interrupted; the next one is refused, and the result says so
  (`{"success": false, "cancelled": true}`). The runtime can hard-kill a Nodus worker and a
  sandboxed plugin and cannot hard-kill a tool it invoked in-process — that asymmetry is
  `TOOL-SEAM-ISOLATION-1`'s half of the same design, where terminate strength follows the
  isolation class.
- **It fails open, deliberately and unlike every other guard here.** An unreadable cancellation
  state means "not cancelled". Refusing an effect because a database blip made the answer
  unreadable would abort live work nobody cancelled — a missed cancel costs one more effect, a
  false cancel costs the run.
- **It does not query per effect.** A cancellation check on a hot tool path is exactly the shape
  that exhausted the connection pool once (`RT-MEMTXN-LEAK-1`) and produced an N+1 (`MEM-RECALL-N1-1`),
  so it uses its own short-lived session, at most once per run per two seconds, and caches a
  `cancelled` answer permanently because cancellation is terminal. It never touches the caller's
  session.
- **New metric `aindy_run_cancel_observed_total{surface}`** — without it, a run that stopped early
  and a run that ran three more tools look identical from outside, and the narrowing this change
  claims would be unmeasurable.
