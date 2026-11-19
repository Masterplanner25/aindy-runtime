### Added — `prior_state(key)`: a resumed Nodus script reads what it saved before it waited (FR-55 (b), DEC-135) (#986)

- **New guest host function `prior_state(key)`.** A script that waits runs again from the top when it is resumed,
  with an empty namespace. Before, what it had saved with `set_state` was recorded on the run but unreachable from
  the script, so a loop that waits between batches could not tell where it had got to: every wake started again at
  the first batch and repeated its external calls. `prior_state` returns what this node saved in any earlier run,
  or `nil`.
- **`get_state` is unchanged** (DEC-012 is narrowed, not reversed): seeding saved values into it would double-count
  `set_state("n", get_state("n") + 1)`, flip "is this key nil yet?" checks in existing scripts, and hand the wait
  keys back. The runtime's own keys (`nodus_*`, `__*`) are never returned.
- Saved keys carry across parks until the node finishes, under the run-state key `nodus_prior_state`, scoped to the
  node. Nothing changes for a script that doesn't call it.
