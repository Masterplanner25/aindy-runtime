### Changed — `Upgrade Path Guard` is now a required check on `main` (#710)

- Both jobs of `upgrade-path-guard.yml` — `Upgrade Path — previous release → this build` and
  `Negative control — the guard must detect drift` — are required for merge, alongside the ten
  checks required since 2026-08-14 (`strict: true` unchanged). **This changes what a green PR
  means:** every merge now proves the previous release's database upgrades to this build
  (`bootstrap-schema` succeeds or exits 3 and `--reconcile` resolves it, then `serve` boots), and
  that the guard can see synthetic drift — so a release with no schema change cannot pass the
  main job vacuously without the control also passing.
- Promoted on the evidence `FR-14`'s entry asked for: 100/100 runs since it was built (#455), and
  on #705 (Alembic 0019) its `--reconcile` step ran rather than being skipped. Merge latency
  gains one ~3–4 minute job that already ran on every PR; nothing else changes for contributors.
- Docs only otherwise: `CLAUDE.md`'s branch-protection table (ten → twelve), and two stale
  lines corrected — FR-6 is fully shipped since 2.0.0 (the registry line said items 2+3 were
  open) and the `/auth/register` enumeration oracle is closed (the standing-decisions bullet
  said "to be fixed").
