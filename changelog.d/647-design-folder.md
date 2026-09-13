### Changed — `docs/design/`: the thirteen scope/design/program/proposal docs get their own folder and a status index (#647)

They were neither contracts (`docs/runtime/` says what the runtime guarantees now) nor dead
(`docs/archive/`) — source cites most of them by path as the reasoning behind live code. Four
had status headers wrong by one or more shipped phases (`EXECUTION_ENVIRONMENT_SPEC_DESIGN` said
phases 3–4 unbuilt; `TOOL_SEAM_ISOLATION_SCOPE` said "no code"; `FLOW_PARALLEL_DESIGN` was a
phase behind; `LLM_SEAM_ADOPTION_SCOPE` said the governor was not started), and three
`CLAUDE.md` key-file rows still said "awaiting approval" / "design only, no code". All corrected.
Path citations updated across `AINDY/`, `tests/`, `alembic/` and docs — except under
`AINDY/db/models/`, where a docstring edit costs a schema-version bump; `Runtime Docs Validation`
checks the new folder. `RUNTIME_DOC_INDEX.md` had never listed any of the thirteen.
