### Changed — a trace graph scoped to one user keeps that user's edges to their own events in other traces (#990)

- Filtered to one user (`build_trace_graph(..., user_id=…)`, FR-60), the trace graph kept only edges whose both ends
  were nodes. Queued work runs under its own trace, so every `async_child` edge to it crosses traces: filtered to
  its owner, one app trace lost 290 such edges, all to the owner's own events. The unfiltered graph keeps them, so the
  two views also disagreed on which events are terminal.
- Such an edge is now kept, pointing to the event in the other trace, which is not added as a node (DEC-137).
  An edge to another user's event, or to an event with no user, is still dropped. The filtered depth counts only edges
  between two nodes.
