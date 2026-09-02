### Fixed — a resume callback no longer carries a database session to the scheduler thread (`FR-15`)

- Two wait registrations passed an inline closure that captured request-bound state:
  `runner_steps.py` registered `lambda: self.resume(run_id)`, holding the flow runner and its
  session, and `execution_pipeline/waits.py` registered a closure over the **request-scoped**
  session directly. Both now build the callback from identifiers and open their own session.
- **`AGENT_WORKING_RULES` §5** — never share a SQLAlchemy session across threads or requests —
  and these did both: the callback is handed to the scheduler and fires later, on a scheduler
  thread, after the request that owned the session has returned.
- *Why it had not bitten:* a closed SQLAlchemy session is not a dead one — it transparently
  checks out a new connection on next use — so the violation was latent rather than visible.
  That is the kind that stops being latent under concurrency, and the `FR-15 (a)` flip made
  scheduler resumes concurrent.
- **Every wait registration in the runtime is now reconstruction-primary**, so the live path and
  the restart-rehydration sweep build the *same* callback for a run rather than two different
  ones. A new guard walks the AST of `AINDY/` and fails if any `resume_callback=` argument is an
  inline lambda.
