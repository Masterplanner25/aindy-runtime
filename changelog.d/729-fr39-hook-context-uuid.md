### Fixed — FR-39: the planner-context and tools-for-run hook contexts crossed the extension boundary as a `uuid.UUID`, which the boundary redacts (#729)

- `shared.py` built both contexts with `"user_id": _db_user_id(user_id)` — a `uuid.UUID` — and
  `sanitize_extension_context` turned it into `{"_redacted_type": "UUID"}` (and dropped `db`, by
  design). #708 (FR-36) fixed and tested the completion-hook builder only; these two had the same
  bug. **On the app's stack the planner-context provider had returned the bare base prompt on
  every plan since 2026-05-20 — every plan was made without the context the provider exists to
  supply — and nothing raised.**
- One builder, `build_provider_hook_context()`, for both: `user_id` is a **string**, a missing
  tenant stays `None`. `PROVIDER_HOOK_PRIMITIVE_KEYS` is the documented key set.
- The boundary test now runs over **every** tenant-bearing hook context the runtime builds, with a
  real `uuid.UUID`; an AST census refuses any hook call site that hands the registry an inline dict
  (how FR-36 and FR-39 both got in).
- `register_planner_context_provider` / `register_run_tool_provider` now document the contract:
  one sanitized argument, `user_id` is a str, **no `db` — open your own session.**
- **App side:** a planner-context / tool provider that reads `user_id` as a UUID or expects `db`
  in the context must change; the app's `AGENT-PLANNER-CONTEXT-BOUNDARY-1` is that change.
- Intake: FR-37, FR-38, FR-40, FR-41 filed as open in `TECH_DEBT.md`; the ledger's next is FR-42.
