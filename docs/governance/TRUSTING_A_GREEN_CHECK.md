---
title: "Trusting a Green Check — the catalogue of ways a check looked green and was not"
api_version: "1.0"
last_verified: "2026-09-16"
status: current
owner: "platform-team"
---

# Trusting a green check

> Moved here verbatim from `CLAUDE.md` on 2026-09-16 when that file was trimmed to the 40 KB
> limit. **This is the live catalogue, not an archive** — the section below expects a sixteenth
> variant, and it is added here. `CLAUDE.md` keeps a one-paragraph summary and the rules; this
> file keeps the evidence. The pre-trim `CLAUDE.md` is `docs/archive/CLAUDE_md_2026-09-16_pre_trim.md`.

## The catalogue — read this before citing CI as evidence

**Fifteen separate times** this repo has shipped something that *looked* covered and was not.
*(The fourteenth arrived 2026-09-10 and the fifteenth 2026-09-15, each predicted by this very
sentence — which is the point.)* Assume there will be a sixteenth — the catalogue exists so you
can recognise the shape, and the rules below are what it cost to learn:

| # | Variant | How it looked green | Entry |
|---|---|---|---|
| 1 | Claimed and absent | 6 docs cited 8 test files that never existed | `DOCS-COVERAGE-CLAIM-1` |
| 2 | Exists, not collected | 268 unit tests in 24 unmarked files ran in no job | `CI-MARKER-1` |
| 3 | Collected, skipped | native suite skipped — nothing built the crate; a skip reads green | `NATIVE-CI-1` |
| 4 | Runs, doesn't gate | 6 checks could be red without blocking | branch protection |
| 5 | Gates, doesn't cover | Integration Tests never executed `EventBus.publish()` | `EVENTBUS-COVERAGE-1` |
| 6 | Covers, asserts nothing | a test asserting an *absence* passes when the wire is broken | `EVENTBUS-COVERAGE-1` |
| 7 | Asserts the source, not the behaviour | route tests read the handler as *text*, never called it — 500 instead of 409 for a day | `ROUTE-GUARD-1` |
| 8 | Verification that never runs | the boot-time route AST proof has no call site in the app (deleted 2026-09-16, DEC-023) | `ROUTE-AST-UNWIRED-1` |
| 9 | **Green because there was nothing to catch** | a check whose condition this release does not contain — `Upgrade Path Guard` passes trivially with no schema change | `FR-8`/`FR-14` |
| 10 | **The instrument cannot see the thing** | `caplog` silently captured nothing for a warning emitted on a WORKER THREAD by a module logger — so the assertion could not tell *"the mechanism did not fire"* from *"I failed to observe it"* | soak harness |
| 11 | **The answer went stale, not wrong** | seven PRs carried a green `pip-audit` for a week while the advisory refuting it was published — the check asks a question about the OUTSIDE WORLD, and the world moved without the diff moving | `security-audit.yml` |
| 12 | **The check is right; its CENSUS is hand-written** | `test_every_provider_client_meters_its_response` walked the AST — correctly, per rule 7 — over three file paths typed out by hand, and a fourth client shipped unmetered | `COST-GOVERNOR-1` ph.0 |
| 13 | **The FIXTURE blinds the test** | the suite patched `_ensure_tools_loaded` to a no-op to isolate the registry, so no test in it could observe that the code under test *called* it — and the call was the bug | `AUTHORITY-NEGOTIATION-1` ph.0 |
| 14 | **The check is fine; its SUBJECT is dead** | a test read `inspect.signature` on a builtins class **nothing instantiates** — green, gating, over real source that no execution path can reach; the fix it guards was applied to unreachable code | `GUEST-BUILTINS-DEAD-1` |
| 15 | **The FIXTURE shares the writer's transaction, so flush reads as commit** | a route test asserted an EU was `completed` and passed for a day on code that only FLUSHED the status and rolled it back on close — the test session and the app's session sat on ONE connection inside ONE outer transaction, where an uncommitted write is fully readable; the live table said `executing`, 900 rows deep | `EU-FINALIZE-UNCOMMITTED-1` |

**Variant 9 is the one to design against, not just record:** it cannot be fixed by making the
check better, because the check is fine — the *release* lacks the condition. The only answer
is a **negative control that injects the condition**, which is why `upgrade-path-guard.yml`
ships with one. A new check is at its least proven exactly when it is newest, and "it went
green" is worth nothing until something has made it go red.

**★ Variant 10 is the one that bites hardest in a CONCURRENT or CROSS-PROCESS test, and this
repo is about to write more of those.** It surfaced when a soak assertion failed on a
docstring-only commit — the signature of an unreliable instrument, not a regression. Three
instruments were tried before one worked: `caplog` (could not see across the thread boundary), a
logger spy (thread-safe, but observes a log line, which is not what an operator has), and finally
a **Prometheus counter** — thread-safe, and the same signal production reads.

**Rules that follow, and they generalise past logging:**

- **Prefer the signal an operator would actually read.** If the assertion and the ops dashboard
  disagree about where to look, the test is measuring a proxy.
- **`caplog` is not thread-safe for practical purposes.** Anything asserting on a log emitted off
  the main thread needs a different instrument. A test asserting the *absence* of such a log is
  vacuous by construction — variant 6 with a specific, easy-to-miss cause.
- **An instrument that can be absent must fail loudly when it is.** `soak_harness.read_metric`
  raises on an unknown metric family rather than reading 0, because `None`-as-zero makes "did not
  move" and "does not exist" indistinguishable. **Its first real use immediately found the other
  half:** a labelled counter has no sample until `.labels()` is first called, so
  "family exists, this label combination unobserved" must read 0 while "no such metric" still
  raises. The guard was right to refuse; the rule was too coarse.

**★ Variant 11 is the only one where the check was RIGHT when it ran, and this is the class to
expect from every dependency, advisory or license check.** Such a check does not ask a question
about the branch — it asks one about the OUTSIDE WORLD, so its answer decays on its own, with no
commit to mark the moment. On 2026-08-31 `pip-audit (OSV)` went red on an **unchanged `main`**:
`a2fe25c` passed it on 08-24 and failed it on 08-31, because `PYSEC-2026-3726` was published
against a pinned `nltk` in between.

**The four PRs it turned red were not the hazard — the seven it left green were.** The red ones
were loud and got looked at. The seven older ones kept a green from 08-24 that any re-run would
have refuted, and nothing about `gh pr checks` says so: it prints a duration, never a date. A
week-old green on an external-input check is not evidence, and it is indistinguishable from a
fresh one at a glance.

**Rules that follow:**

- **For a check whose input is external, the age of the result is part of the result.** Read the
  run date before citing a dependency/advisory/license check — `gh run list --workflow=<f>
  --branch <b>` prints `createdAt`; the PR checks view does not.
- **A required check must gate the branch it protects, not only the PRs into it.** This one ran
  on `pull_request` + a weekly `schedule` and had no `push` trigger, so `main` could sit red on a
  CVE for up to a week with nothing surfacing it — and it only surfaced at all because a PR
  happened to be open. Fixed by adding `push: branches: [main]`; the same question is worth
  asking of any check whose value is time-varying.

**★ Variant 12 is the one that survives every rule above it, which is why it is worth its own
line.** The test was not lazy: it parsed the AST specifically so a comment could not satisfy it
(rule 7), it ran in a collecting job (rule 2), it gated (rule 4), and breaking the thing it
covered *did* turn it red. All of that rigour went into **how** it checked, and none into **what
it checked over** — a literal set of three paths inside a test named `every`. **★ The census was
incomplete the day it was authored, not through drift:** `deepseek_client.py` had existed since
the initial repo extraction, three and a half months before the guard was written. This is what
separates it from variant 11 — nothing decayed, the check never covered what its name claimed.

**The rule: a guard that iterates a collection must DERIVE that collection from the source, and
assert the derivation is non-empty.** A hand-maintained census inside a check is a second thing
to keep in sync, and it is the half nobody re-reads — the check's own name becomes the lie. Where
a literal is genuinely wanted (pinning an exact expected set), it must be compared *against* a
derived set, never used *as* one. **The derivation then needs its own liveness assertion**, or an
empty census silently satisfies every guard built on it — variant 6 arriving one level up.

**★ Variant 13 is variant 12's cousin, and the difference is where the blindness comes from.**
In 12 the check enumerated its subjects by hand; here the check was fine and the **fixture**
removed what it needed to see. A phase-0 sweep called `_ensure_tools_loaded()`, which performs a
trusted bootstrap registration and so was not inert at all — but every test in its own suite
patched that function to a no-op *in order to isolate the registry*, which is a correct thing to
isolate. **The isolation and the blindness were the same line.** CI caught it through an
unrelated audit-surface assertion (`bootstrap_registration_count: 0` became `1`).

**★ Variant 15 is variant 13 with a transaction instead of a patch.** The shared `db_session` /
`runtime_only_app` fixtures bind the app's request session and the test's reader to one
connection holding one outer transaction — correct for isolating tests from each other, and
exactly what makes a `flush()` indistinguishable from a `commit()` to every assertion inside it.
The FR-29 route tests read `completed` on code whose finalize was rolled back on every request.
**The rule: an assertion about DURABILITY must read through a connection that did not share the
writer's transaction, and the file must carry a liveness control proving a flush-then-close reads
as rolled back** (`test_request_eu_finalize_commits_fr30.py`; the class is now also caught at the source by `test_own_session_commits.py` — `SESSION-COMMIT-1`). The storm test's note from 09-13
— "the shared fixture's outer transaction erases the code's `rollback()`" — was the same fixture
seen from the other side; a fixture that hides a rollback also hides a missing commit.

**The rule: a fixture that neutralises a dependency also neutralises any test of HOW that
dependency is used.** Stubbing something out is a claim that the interaction does not matter —
so when it does, assert on the interaction *outside* the fixture that hides it. A no-op patch can
never prove a call did not happen; only a spy or a real invocation can. **When a change claims to
be inert, at least one test must exercise the real entry point**, because inertness is a property
of the whole path and a suite scoped to one layer cannot see the other.

Variants 2 and 3 are fixed at the mechanism level (`tests/unit/conftest.py` defaults the marker;
`AINDY_REQUIRE_NATIVE_BRIDGE=1` turns a skip into a failure) — but both stay listed, because the
failure mode is general and only those two paths are immune. Variant 8 is the first found in a
runtime mechanism rather than a test.

**Rules that follow:**

- **Before citing a check as evidence for a change, confirm it executes that code.** The job
  name is not evidence. `Runtime Contracts` runs `pytest tests -m runtime_only`, *not*
  `tests/unit/`; `pytest.integration.ini` sets `testpaths = tests/integration`.
- **Mutation-test a new suite.** Break the thing it covers and confirm it fails, and how many
  tests fail. This is cheap and it is the only check that a test asserts anything: a first-draft
  wire suite scored 4/7, because the absence-assertion passed with the wire broken. **A test
  asserting an absence needs a liveness control** or it is vacuous by construction.
- **A new test file must be selected by some job.** Under `tests/unit/` this is now automatic —
  `tests/unit/conftest.py` applies `runtime_only` to every item that does not already carry it
  or a marker handing it to another job (CI-MARKER-1) — so write `pytestmark` for readability,
  not for safety. **Everywhere else the old rule still bites:** nothing marks a file outside
  `tests/unit/`, and `pytest.integration.ini` only reaches `tests/integration`. Adding a test
  directory means giving it a job, or its tests run nowhere.
- **When a test can legitimately skip, make skipping loud where it must not happen** — an
  env-gated assertion that fails in CI beats a silent skip.
- **★ A route test must call the route.** Reading the handler's source proves the guard was
  written, not that the caller receives its answer — and the status code *is* the contract:
  a client cannot tell "rejected" from "the server broke" by a 500. Source assertions are fine
  as a *supplement* (they catch a deleted guard cheaply); they are never the coverage.

**★ Trusting your own verification — three rules that each cost something on 2026-08-20:**

- **A check read moments after a push is the PREVIOUS commit's.** `gh pr checks` returned green
  about a minute after a push; those were the prior head's results, the PR was merged on them, and
  **a pushed commit was silently lost** (the design doc's settled decisions, recovered later from
  an orphan). The merge succeeding proves the *merged* head was green, not that it was your
  latest. **Compare `gh pr view --json headRefOid` against `git rev-parse HEAD` before merging.**
  The tell that was missed: the remote branch survived `--delete-branch`.
- **A local suite run that stops partway measures how far it got, not whether it passed.** Four
  full-suite runs died at 31%, 57%, killed and 65%; each time "zero failures so far" was reported
  as evidence and CI then found real failures in files the run never reached alphabetically. A
  partial sweep is a *different measurement*, not a weaker one — report it as progress, and a
  targeted subset that finishes is worth more than a broad one that does not.

  **★★ CORRECTED 2026-09-09 — the cause is the MACHINE'S MEMORY, and it is checkable before you
  start.** This bullet used to say "on this machine" as though the box could never finish a
  sweep, and that inference outlived its evidence: after clearing ~14 GB of commit and rebooting,
  `pytest -m runtime_only` completed **2,556 tests, exit 0, zero failures** — the first clean
  local sweep on record. (#605 said 1,760; that was counted off progress dots rather than from
  `--collect-only`, and was wrong. The count is the only thing that changed.) Same suite, same commit; the only variable was the host.

  The four kills were memory pressure, measured: **9,593 hard page faults/sec with 575 MB
  available**, against **18/sec with 1,248 MB available** on the run that finished. Check before
  blaming the suite:

  ```powershell
  (Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue      # want > ~1500
  (Get-Counter '\Memory\Pages Input/sec').CounterSamples[0].CookedValue       # want < ~100
  ```

  **★ The host is 7.7 GB of ON-PACKAGE memory — platform max 8 GB, four channels populated, not
  upgradable.** So the lever is never "get more RAM", it is running less at once: Docker Desktop
  idles at ~3.8 GB with zero containers, and stray `npm run dev` / `vite` servers were found
  holding 1.85 GB three days after anyone used them. `explorer.exe` also leaks (1.1 GB / 11k
  handles over 15 days), which a reboot clears.

  **★ The rule that survives the correction: a partial run is still not evidence.** What changed
  is that "it got killed again" is now a symptom with a cause and a number, not a property of the
  box — so the first question is *how much memory is free*, not *which test is flaky*.
- **A low mutation score is often bad mutations, not weak tests.** Two runs scored 2/4, and in
  both cases every survivor was a defective mutation — one edited code the fixture disabled, one
  added an unused class while the real branch still ran. **A mutation that does not change
  behaviour proves nothing about the test.** Verify the mutation bites before concluding the
  test is weak; the reverse mistake (weakening a good test to "fix" a score) is worse.

- **★ A version number read from an interpreter is cwd-sensitive, and it bit BOTH sides of a
  handoff on the same day (2026-09-11).** `importlib.metadata.version`, `pip show` and
  `import AINDY._version` all answer for whichever `AINDY` `sys.path` resolves first, and `-c` /
  `-m` put cwd first: from `C:\dev\aindy-runtime` the monolith's venv reports **2.11.0**; from the
  monolith's own root the same venv reports **2.6.0**, which is what its `pytest` imports. We
  measured 2.6.0 and inferred their deployment; they measured 2.11.0 and inferred an editable
  install that does not exist. **Print `AINDY.__path__` beside the number, from the directory the
  thing under test runs in** — if the path is not the one you expect, the number is not the one
  you think. The container is the only place the answer is unambiguous. (`DEBT-COMPAT-1`.)

**Chasing a flaky test:** never pipe the run through `tail`. Three observed failures of
`FLAKY-1` were run as `pytest ... -q | tail`, which discarded the traceback and kept the summary
— the evidence was destroyed at the moment it was produced, three times. Write to a file. And do
not conclude from small samples: that test produced **three** wrong readings (deterministic,
branch-caused, confined to `tests/unit/`) before the fourth run refuted each. Against a ~50% base
rate, four clean runs happen ~6% of the time by luck.

---


---

## Vendored shims on `pythonpath` — untested by construction

`pytest.ini` sets **`pythonpath = . AINDY`**, so `import apscheduler` resolves to the
hand-written shim in **`AINDY/apscheduler/`** for *every test in this repo*, not to the
installed package. **Anything the runtime calls that the shim does not implement is untested by
construction** — and where the call sits inside a `try/except`, it fails *silently*: the test
passes, production takes a different branch.

This has now bitten three times:

1. `executors.pool` missing → the dedicated-executor branch shipped unexercised (`FR-15` (b)).
2. `events` + `add_listener` missing → the starvation listener shipped unexercised (`SYSMAX-5`).
3. `remove_job` missing → `_remove_from_scheduler` swallowed an `AttributeError` under a comment
   claiming it was for an already-deleted job. **Removal could have been a permanent no-op with
   every test green.**

**Rule: grow the shim to match the guard, never weaken the guard to match the shim.** A
source-derived guard now exists (`tests/unit/test_apscheduler_shim_parity.py`) — it scans
`AINDY/` for scheduler method calls and fails if the shim cannot express one, so a fourth
instance is a CI failure rather than a discovery.

**`nodus` is the other shadowed name.** `AINDY/nodus/` shares the installed package's name and
`AINDY/nodus/runtime/embedding.py` shares the exact module path `GUEST-CONFINE-1`'s tests import
`NodusRuntime` from. It currently resolves to the **installed** package (pinned by a test), and
the collision is self-limiting only because that file is a re-export — a real definition there
would turn a loud failure into a silent one.

---


---

## Standing rules — the same family, found in runtime code rather than tests

- **★★ A TEST-MODE SHORT-CIRCUIT PLACED ABOVE THE REAL DECISION MAKES THE REAL PATH UNTESTABLE
  WHILE EVERY TEST PASSES.** Two instances, both found in `FR-15`'s own path within a fortnight,
  and both make a soak vacuous rather than failing:

  - `async_heavy_execution_enabled()` returns False under `TESTING`/`TEST_MODE` **before** reading
    its flag. `pytest.integration.ini` sets both, so **the ASYNC dispatch branch was unreachable
    from every test in this repo and always had been.**
  - `get_queue()` returns an `InMemoryQueueBackend` under the same two variables, **before**
    checking `REDIS_URL`. So **no test here can reach the Redis backend.** A soak written the
    obvious way enqueued and dequeued inside one process and passed 6/6 while proving nothing.

  **This is worse than an untested path, because the test that appears to cover it passes.** The
  second case was caught only by an unrelated hunch — asserting `backend_name == "redis"` on the
  strength of `QUEUE-DURABILITY-CLASS-1` — which then failed immediately and named the cause.

  **Rules:** when writing a soak, **assert the mechanism you think you are exercising is actually
  the one running** (the backend, the branch, the mode) before asserting anything about its
  behaviour. And when adding a test-mode guard, put it **below** the switch it is guarding, or
  give it an explicit opt-in that test mode cannot veto — `async_scheduler_dispatch_enabled()` is
  the worked example. Expect a third instance; grep for `TEST_MODE` above a decision, not after it.

- **★ A SOURCE-TEXT ASSERTION IS A SUPPLEMENT, NEVER THE COVERAGE — four failures in one
  fortnight.** A test that reads code as text cannot tell code from a comment, cannot tell
  presence from reachability, and cannot tell order from behaviour. Observed: an assertion
  matching its own explanatory comment that *quoted* the bad pattern; a `register_flows()` string
  match satisfied by `# register_flows()`; a two-line ordering check that passed with the branch
  disabled. **Prefer the AST when you must read source** (a comment cannot satisfy a `Call` node),
  and prefer driving the real entry point when you can. `ROUTE-GUARD-1` said this about routes; it
  generalises.

- **★ Module-import-time env reads are invisible to behavioural tests.** Three bugs share this shape: FR-10 (`settings = Settings()` at import crash-looped the container), `ResourceManager._get_backend()` (caches the Redis-vs-in-process choice on first call), and the `AINDY_REDIS_URL` alias in `rate_limiter.py` — which survived a cleanup that believed it had removed the alias everywhere, because **nothing about the running limiter differs when the alias is honoured**. **When auditing env-var handling, grep the source; do not trust a passing suite.**
