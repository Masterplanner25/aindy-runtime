### Fixed — memory nodes saved without a path can be found by path (#917)

- **Most memory nodes had no stored path, so `memory.tree`, a wildcard `memory.list` and `walk_path` could not find them.** Those filter on the stored column, while every reader was shown a path derived on the fly. On the app's stack 1,371 of 1,397 nodes were affected. `MemoryNodeDAO.save` now stores that same derived path, `/memory/{user}/_legacy/{memory_type}/{id}`, for an owned node written without one. Nothing a caller has seen changes, except that the path tools now find the node. A node with no owner still has no stored path.

### Added — `aindy-runtime memory backfill-paths` (#917)

- **Action for operators:** run `aindy-runtime memory backfill-paths` once after upgrading. Without `--yes` it only reports how many nodes would get a path. With `--yes` it stores the same derived path on older rows, committing per batch (`--batch-size`, default 500). Running it again finds nothing to do.
