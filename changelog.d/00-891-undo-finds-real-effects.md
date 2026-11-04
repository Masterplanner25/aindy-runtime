### Fixed — `sys.v1.agent.undo` now finds and reverses real effects, so it can delete memory (#891)

**Read before upgrading if anything calls `sys.v1.agent.undo`.** It has been a no-op since it shipped. It now deletes the memory nodes a run wrote.

- Undo selected effects by `EffectRecord.execution_id`, and nothing wrote that column, so it returned an empty summary for every run and no compensator, first-party or consumer, ever ran. The tests seeded the column by hand. The ledger now links each effect to its execution unit (DEC-108). No schema change.
- Consumer `compensate=` hooks are now reachable. They run in a savepoint on undo's session, and `context` carries that session and the owner. A hook must flush, and never commit or roll back.
- `sys.v1.memory.write` now has a compensator. It deletes the node, and refuses if the node changed or has edit history, a link or a trace membership (DEC-109).
- Effects recorded before this release stay invisible to undo unless they re-run. Effects with no ledger row, including default Nodus guest memory writes, are not seen; an empty summary does not mean the run had no effects.
- Each effect the gate reserves costs one more query.

### Changed — the Verifier reports what it could reverse instead of claiming to roll back (#891)

- On `VERIFY_FAILED` the agent event now lists `reversible`, and nothing is changed. The rollback it described never did anything, and fixing undo would have made it delete memory automatically. `AINDY_VERIFY_FAILED_AUTO_UNDO=1` opts in (DEC-110).
