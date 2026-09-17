### Changed — `CLAUDE.md` trimmed to the 40 KB Claude Code limit; the CI-evidence catalogue is now a governance doc (#717)

- `CLAUDE.md` went from 152,649 to ~36,000 chars. Claude Code warns and degrades at 40,000, so the
  authoritative agent-instruction surface had been over the limit it is loaded under. Every rule
  survives as one line; the narrative moved to where it belongs — `TECH_DEBT.md` (the record),
  `docs/governance/DECISION_LOG.md`, and the new **`docs/governance/TRUSTING_A_GREEN_CHECK.md`**,
  which holds the 15-variant "green check" catalogue, the vendored-shim rule and the three standing
  rules verbatim as a *living* doc (it expects a sixteenth variant).
- The pre-trim file is archived verbatim at `docs/archive/CLAUDE_md_2026-09-16_pre_trim.md`
  (outside the frontmatter check by design), so nothing that was written is lost — it is citable,
  not maintained.
- **What CI enforces changed:** `tests/unit/test_debt_registry_accuracy.py` ratchets the per-entry
  registry caps 1144/833 → **500/400 UTF-8 bytes**, the new high-water marks (472/379). The ratchet
  was the test's own stated maintenance action; the caps are never raised to fit a new entry.
