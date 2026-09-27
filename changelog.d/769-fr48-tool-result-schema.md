### Added — tools declare what they return; step-reference paths are checked at plan time (FR-48, DEC-077..079, #769)

- `register_tool(..., result_schema=)`, in the `args_schema` dialect and nested through
  `properties`, `items` and `additionalProperties`. The planner catalog renders it as `returns=…`.
  **Why:** the planner was shown every tool's arguments and no tool's result, so a
  `{"$from_step": N, "path"}` path was a guess. The app's first FR-46 evidence run referenced
  `results` on a tool returning `{raw_result}`. The path was well-formed, so planning accepted it,
  and the step failed only after the research before it had run and been paid for.
- With `AINDY_PLAN_STEP_REFERENCES` on, a reference's path is checked against the referenced
  tool's `result_schema` at plan time. A node that declares `properties` is closed unless it sets
  `additionalProperties`; a node that declares nothing is open; a tool with no schema is checked
  for form only, as before. Results are never validated at run time.
- A refused plan fails run creation, as before; the runtime does not re-plan (DEC-078).
- A malformed `result_schema` raises at registration, at any depth.
- FR-46's default stays off until this has shipped (DEC-079).
