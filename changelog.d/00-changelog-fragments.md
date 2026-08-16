### Changed — changelog entries are now files in `changelog.d/` (#452)

A PR still writes its own entry in the same PR — the protocol in `CLAUDE.md` is unchanged. It is
now a **new file** in `changelog.d/` rather than an edit to `CHANGELOG.md`'s `## Unreleased`.

Editing one shared section made every concurrent PR collide, three times in one afternoon
(#449/#450/#451). The failure mode was worse than the annoyance: the reflexive "keep mine"
resolution **silently reverted another PR's entry**, and a dropped changelog paragraph breaks no
build. A new file cannot conflict with another new file.

- Create `changelog.d/<PR>-<slug>.md`; prefix **`00-`** if an operator must read it before
  upgrading, which is how the protocol's "at the top, not buried" rule becomes mechanical.
- `python scripts/assemble_changelog.py` folds fragments in and deletes them; `--check` verifies
  none are stranded. **A release step, never a per-PR gate** — fragments are supposed to exist
  during development, so gating on their absence would invert the design.

This entry is itself a fragment.
