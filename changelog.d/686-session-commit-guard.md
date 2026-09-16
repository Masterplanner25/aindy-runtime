### Fixed — a flow wait claimed by another instance is now actually resumed (`SESSION-COMMIT-1`, #686)

- **Multi-instance deployments in thread mode (the default):** when the instance holding a
  parked flow run died and another instance claimed its wait from the Redis registry, the
  claim moved only the run's execution unit — on a session that then rolled it back — and never
  resumed the run. The run stayed `waiting` forever while the log said
  `Cross-instance resume claimed run_id=…`. The claimed callback now rebuilds the real resume
  (claim → unit → flow) from the registry entry and commits; when the run cannot be rebuilt on
  the claiming instance (its flow is not registered there, or the entry predates FR-15 and
  carries no run id) the unit is moved and committed and a WARNING names the run that was not
  resumed. Single-instance deployments and distributed mode are unaffected.
- **Runs already stuck by this** (`flow_runs.status='waiting'` with no scheduler entry on any
  live instance) resume on the next boot's rehydration, which re-registers every waiting run.
- **CI now guards the class** (`tests/unit/test_own_session_commits.py`): any function that opens
  its own session and writes through it without committing fails the unit job. Four such
  callbacks were found and fixed across #673, #679, #685 and this PR; this is what a green check
  means for that shape from now on.
