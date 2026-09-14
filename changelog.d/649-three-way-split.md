### Changed — `docs/` is split by the question it answers; `docs/platform/governance/` is gone (#649)

`docs/runtime/` had been holding three kinds of document. It now holds only what the runtime
guarantees (42 files); `docs/operations/` (12) is how to run it; `docs/governance/` (19) is how
work is done on the repo — including the three governance docs that were buried at
`docs/platform/governance/`, the monolith's path copied verbatim in June and never chosen.
`docs/architecture/` and `docs/platform/` dissolve. `docs/README.md` is the new index and lists
every live file (the old one listed 32 of 70). Every path citation was rewritten — 41 files
across `AINDY/`, `tests/`, workflows, `CLAUDE.md`, `README.md`, `TECH_DEBT.md` and the docs
themselves — and 215 live markdown links were checked to resolve. **Operator-visible:** the
sdist now ships `docs/operations/` and `docs/governance/` beside `docs/runtime/`, and the
package's `Documentation` URL points at `docs/` rather than `docs/runtime/`. Three files
archived on the way: the pre-split monolith changelog (renamed so it no longer collides with
the real one), the finished `RUNTIME_DOCSET_BOUNDARY` plan, and the old index.
