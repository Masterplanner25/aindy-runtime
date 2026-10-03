### Changed — plan step references are ON by default (FR-46, DEC-084, #786)

- **Read before upgrading if your planner or plans could contain `{"$from_step": …}`.** With
  `AINDY_PLAN_STEP_REFERENCES` unset, three things now happen:
  - The planner's tool catalog carries one line teaching the reference form.
  - Every reference is checked at plan time. With a tool's `result_schema` (FR-48), the path must
    exist in it, or the plan is refused and run creation fails.
  - A reference that cannot be resolved at run time fails its step `invalid`. The tool is never
    called with the placeholder.

  A plan with no references is unaffected and pays nothing. Set `AINDY_PLAN_STEP_REFERENCES=0`
  (or `false` / `no` / `off`) to keep the old behaviour. **Why now:** the flip was held until FR-48
  shipped (DEC-079), so the planner sees each tool's result shape before it is invited to
  reference one. The app's evidence run stored an earlier step's result byte for byte, and its
  failed first attempt is now refused at plan time.
