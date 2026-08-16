### Added — CI now exercises the upgrade path against an existing database (`FR-8` / `FR-14`, #455)

The class of failure no other check could see. **Every existing job builds a fresh database**,
where `create_all` produces whatever columns the current build declares and there is nothing to
reconcile — which is why the app team's own `deploy-bootstrap-guard.yml` passed while their live
stack was crash-looping on 2.1.0. The failure only exists when a database **predates** the schema
change, which is true of every real deployment and no CI run.

`Upgrade Path Guard` builds that state deliberately: install the **previous released wheel from
PyPI**, `bootstrap-schema` against a fresh database, install **this build** over it, and
`bootstrap-schema` again. That last step is the one that took a stack down. It must either
succeed or exit **3** (`additive reconcile required` — the branchable code from `FR-14`), and
`--reconcile` must then resolve it and stay stable on re-run. It finishes by booting `serve`,
because `FR-14`'s actual symptom was a container that never reached it.

**★ Read this before treating a green run as proof.**

**This release contains no runtime schema change, so the guard passes trivially here.** There is
no drift to detect, and on such a release *a broken guard and a clean release look identical* —
which is precisely the "green because there was nothing to catch" trap this repo has catalogued
seven times.

That is why the workflow ships with a **`negative-control` job** that injects synthetic drift
(dropping `agents.updated_at`, reproducing `FR-13`'s shape) and **requires** the guard to report
exit 3. The control is the load-bearing half on any release without a schema change: if it ever
passes silently, the upgrade-path job is decorative and should not be trusted.

**Not yet a required check.** Promote it only after reading a real run — and read the
`negative-control` result, not just the overall green.

*Correction, recorded because it contradicts a note elsewhere in this repo:* **this workflow DID
run on the pull request that added it.** `CLAUDE.md`'s `NATIVE-CI-1` entry says a new workflow
file does not trigger on its own PR; that holds for `push`-triggered workflows, but a
`pull_request` trigger fires from the PR's merge ref and so does run. It failed on that first
run — for two setup reasons of its own (a missing `CREATE EXTENSION vector`, and a one-shot PyPI
lookup that hit a connection reset) — which is a better outcome than a first run that only
happens after merge.
