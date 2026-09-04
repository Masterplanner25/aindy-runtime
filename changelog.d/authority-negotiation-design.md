### Added — designs for the three remaining P1 entries that are not code-shaped

- `docs/runtime/AUTHORITY_NEGOTIATION_DESIGN.md` (`AUTHORITY-NEGOTIATION-1`) — design only, no
  code. **It overturns the primitive the entry itself proposed:** `amend_token` is not needed for
  the downgrade path, because a fallback requiring capabilities the run's token *already grants*
  needs nothing minted, amended, or re-approved. The executable condition is
  `required(fallback) ⊆ token.allowed_capabilities` — not a subset of the *denied* capability,
  which is the tempting formulation and says nothing about whether the token grants it either.
- `docs/runtime/WITNESS_AND_BASELINE_SCOPE.md` (`SUBSTRATE-WITNESS-1`, `PERF-BASELINE-1`) — both
  re-measured rather than read from the registry. `PERF-BASELINE-1`'s blocking half is closed
  (metric readback 0→7 files, concurrency drivers 0→7, soak suites 0→4); what remains is one
  latency assertion, and the scope argues against closing it with wall-clock thresholds.
  `SUBSTRATE-WITNESS-1` is unchanged: `execute_tool` and `EffectRecord` still appear zero times
  in the flagship consumer's own source.
- No behaviour change. Documentation and registry accuracy only.
