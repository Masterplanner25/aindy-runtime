### Fixed — a bad memory path is refused as `invalid`, not `transient` (FR-66 side note, #947)

- A memory read, list, tree or search with a path outside `/memory` (`/`, say) was refused correctly, but the
  envelope said `failure_class: "transient"`, so an MCP client was told to retry a call that can never succeed.
  The dispatcher classified a handler's exception by its message, which matched nothing in the fallback table.
- The dispatcher now keeps a `failure_class` the exception declares, the convention the tool seam already
  followed (RETRY-CLASSIFY-1). `normalize_path` raises `InvalidMemoryPath`, a `ValueError` declaring `invalid`,
  so every existing `except ValueError` still catches it and `GET /platform/memory` still answers 400. An
  exception that declares nothing is classified as before.

### Fixed — `GET /watcher/signals` filters by the types `POST /watcher/signals` stores (FR-65, #947)

- The query service kept its own signal-type list, which had drifted: filtering by `focus_achieved`,
  `context_switch` or `heartbeat` answered 422, while `app_focus`, `app_switch` and `idle_detected`, which the
  ingest route refuses, were allowed. Both now read `AINDY.watcher.constants.VALID_SIGNAL_TYPES`.
- The rest of FR-65 (idle detection, `session_ended` on stop, the focused-seconds sum) is the `aindy-sdk`
  watcher's. Its first ask, sending a per-user key as `X-Platform-Key`, shipped in `aindy-sdk` 1.1.0.
