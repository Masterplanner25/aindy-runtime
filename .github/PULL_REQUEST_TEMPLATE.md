## Summary

-
-

## Scope

<!-- Runtime area touched: startup, packaging, routes, kernel, DB infra, docs, CI, etc. -->

## Validation

- [ ] Runtime-owned CI/test impact was considered
- [ ] `python -m pytest tests -m runtime_only -q` passes locally, or the gap is explained
- [ ] Coverage floor (≥ 35%) is met — check the `coverage-sqlite` artifact from CI
- [ ] Runtime-only `/api/version` and boot behavior remain correct if affected
- [ ] `aindy-runtime` / `aindy-runtime-api` packaging or entrypoint behavior was checked if affected
- [ ] `python -m build` / artifact viability was checked if packaging changed
- [ ] No `AINDY -> apps.*` imports were introduced

## Docs And Contracts

- [ ] Runtime docs/contracts were updated if behavior or public surface changed
- [ ] Compatibility/version metadata was updated if required
- [ ] CI/workflow ownership remains runtime-only and does not pull app scope back in

## Reviewer Notes

<!-- Risks, tradeoffs, deferred follow-ups, or environment-specific caveats. -->
