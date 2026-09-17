### Added — a tool declares its argument contract; the planner sees it; `execute_tool` checks it (FR-33, #709)

- `register_tool(..., args_schema={...})` — a JSON-Schema-shaped object in the dispatcher's
  own dialect (`required` + `properties[].type`; other keywords carried, not checked). A
  malformed schema, or a `required` name not declared under `properties`, is refused at
  registration. `None` (the default) declares nothing — every existing tool is unchanged.
- The runtime's default `get_tools_for_run` surfaces `args_schema` on each tool dict, and the
  planner catalog renders `args={…}` for a tool that declares one — read from the dict, or from
  the registry by name when an app's provider omits the key.
- `execute_tool` checks `args` against the schema **before** dispatch (before any capability
  event), under **`AINDY_TOOL_ARGS_VALIDATION`**: `warn` (**default** — log + count, dispatch
  anyway; nothing changes on upgrade), `enforce` (refuse the step with `failure_class:
  "invalid"`, which `RETRY-CLASSIFY-1` never re-attempts), `off` (count only). New metric
  `aindy_tool_args_validation_total{tool, outcome, mode}` — read the `invalid` count under
  `warn` before flipping to `enforce`.
