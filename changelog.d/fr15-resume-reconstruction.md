### Added — a resume can be rebuilt from its run id, not only carried as a closure (`FR-15`)

- New `AINDY/core/resume_reconstruction.py`: `build_resume_callback(run_id=…, eu_type=…, db=…)`
  returns the zero-argument call that resumes a run, or `None` when the run cannot be resumed
  from its identifier. `require_resume_callback(…)` is the raising variant, for a caller that
  has already discarded the live callback and cannot do anything sensible with `None`.
- **No schema change and no behaviour change in this entry.** Nothing routes through it yet;
  this is the primitive the distributed half of `FR-15` needs, landed on its own so it can be
  reviewed and reverted independently of the transport work that will use it.
- *Why this is smaller than it sounds:* the durable representation was never missing. The
  rehydration sweeps rebuild exactly this on every boot, because a restart destroys the live
  closure. This gives that an entry point for **one** run instead of only a whole sweep.
- `build_flow_resume_callback` is now module-level in `flow_run_rehydration.py` rather than
  nested inside the sweep, so the sweep and a by-id rebuild are one implementation. It mirrors
  `_build_agent_resume_callback`, which already had this shape and three callers — the agent
  half of the runtime was already reconstruction-primary and the flow half was not.
