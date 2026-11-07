### Fixed — `causal_trace` could return another user's memory node (`MEM-TRACE-SCOPE-1`, #897)

- **Read before upgrading if your deployment has more than one user.** `sys.v1.memory.trace` and `GET /platform/memory/trace` scoped their first lookup to the caller, and every hop after it to nobody. A node whose `source_event_id` matched another user's `root_event_id` returned that user's node, content included. Each hop is now scoped the same way as the first.
- We reproduced this with seeded rows. We have not seen it happen, and found no way for a caller to set those ids through `sys.v1.memory.write`, but we have not audited every writer. To see whether it ever could have fired, run: `SELECT count(*) FROM memory_nodes a JOIN memory_nodes b ON a.source_event_id = b.root_event_id AND a.user_id <> b.user_id;`
- A chain now stops at the first event with no node of the caller's, so a trace may be shorter than before. Only chains that crossed users change.
