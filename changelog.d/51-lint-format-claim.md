### Fixed — the documented lint command now matches the one CI runs (#494)

`CLAUDE.md`'s Commands section listed `ruff check AINDY/` and `ruff format AINDY/`. CI's
`Runtime Lint` runs neither of those literally — it runs
`ruff check AINDY tests --config AINDY/ruff.toml` — and **`ruff format --check` reports 457 of
559 files would be reformatted**, so the second command had never been true of this tree.
Following it as documented produces a ~450-file diff on top of whatever the agent was asked to
do. The section now states the enforced command and warns against running `format` casually;
filed as `LINT-FORMAT-1` with the measurements and the reason not to close it with a repo-wide
sweep.
