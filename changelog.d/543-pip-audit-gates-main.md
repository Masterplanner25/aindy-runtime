### Changed — `pip-audit (OSV)` now runs on pushes to `main`, not only on PRs (#543)

- `security-audit.yml` gains `push: branches: [main]`. It previously ran on `pull_request` and a
  weekly `schedule` only, so the audit gated every PR *into* `main` and never gated `main` itself.
- **Why that mattered:** on 2026-08-31 `PYSEC-2026-3726` was published against a pinned `nltk` and
  turned the check red on an *unchanged* branch — `a2fe25c` passed it on 08-24 and failed it on
  08-31. It was noticed only because Dependabot PRs happened to be open and inherited the failure.
  With an empty queue, `main` would have sat red on a dependency CVE until the following Monday.
- **What a green check now means:** a passing `pip-audit` against `main` is a live statement about
  `main`, not a claim inherited from whichever PR last merged. Expect one extra job per merge.
- The trigger is deliberately **not** `paths:`-filtered — a filtered required check never reports
  on unrelated PRs and blocks them forever. `push` is scoped to `main`, so PR branches do not
  double-run.
- Also filed as variant 11 in the trusting-a-green-check catalogue in `CLAUDE.md`: the first entry
  where the check was *correct when it ran*. A dependency audit asks a question about the outside
  world, so its answer decays with no commit to mark the moment — and `gh pr checks` prints a
  duration but never a date, which makes a week-old pass indistinguishable from a fresh one.
