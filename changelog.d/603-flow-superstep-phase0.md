### Changed — flow history ordinals and state merge are now per-superstep (`FLOW-PARALLEL-1` phase 0, #603)

- **No behaviour change.** A superstep is one node today, so ordinals and merges resolve exactly
  as before. This widens the *transaction* so fan-out has a reviewed seam to arrive into; it does
  not add concurrency, and the runtime concurrency model is untouched.
- `FlowHistory.sequence_number` is allocated by `_allocate_sequence_numbers(run, count)` for a
  whole superstep **at the barrier**, in declaration order. The `max(sequence_number) + 1` it
  replaces carried a comment stating its own precondition — *"a run's nodes execute sequentially
  (no concurrent writers)"* — which is precisely what fan-out removes. With `count=1` the result
  is identical, pinned by a parametrised equality test.
- The state merge moved out of `_handle_node_status`'s SUCCESS branch into `_merge_superstep` on
  the runner. **Called per node, `merge_state` can only ever see one patch, and one patch at a
  time is completion order** — which defeats `last_write_wins`, the policy module's entire point.
  Only SUCCESS branches contribute, preserving exactly what the previous location did with a
  WAIT branch's patch (nothing).
- **★ The relocation nearly removed a guarantee.** The pre-existing seam test drove
  `_handle_node_status` — the function the runner calls — so it proved *wiring* for free. Driving
  `_merge_superstep` directly left the runner free to stop calling the seam with every merge test
  green. An AST wiring guard restores it. **When a test moves with its code, check whether the
  old location was carrying a property the new one does not.**
- Mutation-verified 4/4: per-branch ordinal allocation, per-branch merging, dropping the SUCCESS
  filter, and unwiring the seam each fail a specific guard.
- Design and impact analysis: `docs/runtime/FLOW_PARALLEL_DESIGN.md`. **Phase 1 introduces
  concurrency and needs its own approval** — branches cannot share the runner's DB session, and a
  fan-out width of N spends N connections from a budget shared with request handling.
