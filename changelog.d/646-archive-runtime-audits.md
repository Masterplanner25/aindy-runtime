### Changed — five completed audit/plan documents archived out of `docs/runtime/` (#646)

`INFINITY_LOOP_AUDIT.md` (all five gaps closed 07-08), `MONETIZATION_AUDIT.md` (every finding a
self-contained `TECH_DEBT.md` entry), `LOCAL_AND_CLOUD_AUDIT.md`, and the 05-31
`RUNTIME_DOC_ALIGNMENT_AUDIT.md` + `HIGH_CONFLICT_DOC_RECONCILIATION_PLAN.md` pair (executed the
day they were written) move to `docs/archive/`. Six low-severity findings whose only record was
the local/cloud audit (`CLOUD-1..4`, `COMPAT-3`, `DATA-2`) were folded into `DEPLOY-TARGET-2`
first so nothing is lost. `RUNTIME_DOC_INDEX.md` loses a dangling entry for a file archived five
weeks ago and a "read these before editing" instruction for a reconciliation that was done in
May. `SANDBOX_ESCAPE_AUDIT.md` stays — it is a live log.
