---
title: "Working Agent Experience — problems agents hit here, keyed by what they saw"
api_version: "1.0"
last_verified: "2026-11-11"
status: current
owner: "platform-team"
---

# Working agent experience

What coding agents (Claude Code, Codex, any other) have run into while working on this repo, how it
was diagnosed, and what fixed it. **It is experience, not policy.** `CLAUDE.md` says what an agent
must do; this file says what has happened. Nothing here binds until it is promoted (below).

**How to use it.** On an unfamiliar failure, **grep this file for the exact text you are looking at**
(an error message, a warning, a check name) before investigating. Each entry is headed by its symptom
for that reason. If one matches, read its **Applies when** line before acting on it.

**How to add one.** When you solve a failure another agent could plausibly hit again, add an entry in
the same PR. Rules:

- **Head it with the literal symptom**: the error string, the check name, the warning. Not a topic.
- **Cite evidence**: a PR, a commit, a test. An entry with no evidence is `Unverified`.
- **One home.** If the lesson is already in `TRUSTING_A_GREEN_CHECK.md` (green checks that were not),
  `TECH_DEBT.md` (a finding about the runtime itself) or `CLAUDE.md`, link it; don't restate it.
- **Status** is one of:
  - `Verified`: reproduced, and the resolution worked;
  - `Unverified`: seen once, cause not confirmed;
  - `Guarded`: a test or check now makes it fail loudly or impossible. Name the guard and keep the entry
    short; it explains the guard;
  - `Obsolete`: the cause is gone. Leave it one line with the date and why, so an old report still
    resolves.
- **Host-specific** entries (one machine, one OS) say so in **Scope**. This repo is public: nothing
  about an unfixed security issue goes here, ever.

**Promotion.** An entry becomes a rule in `CLAUDE.md` only when it has bitten more than once or the
cost of missing it is high, and it is `Verified`. Then the rule goes in `CLAUDE.md` and the entry
links to it. `CLAUDE.md` is held under 40 KB; this file is how it stays there.

---

## Index

| ID | Symptom | Category | Status |
|---|---|---|---|
| EXP-001 | `UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97` in `assemble_changelog.py` | Tooling | Verified |
| EXP-002 | Every MCP test **skipped** locally | Testing · Windows host | Verified |
| EXP-003 | `test_dependency_pin_agreement`: "our declared pins cannot be installed alongside packages already in this environment" | Testing · local env | Verified |
| EXP-004 | `warning: in the working copy of '…', LF will be replaced by CRLF` after a scripted edit | Tooling | Verified |
| EXP-005 | A stacked PR turns CONFLICTING after its base squash-merges | Git | Verified |
| EXP-006 | Boot Smoke: "is on PyPI (JSON API 200) but pip cannot resolve it yet (attempt 1/10)" | Release | Guarded |
| EXP-007 | Stale `dependabot/*` branches on origin with no open PR | Repo hygiene | Verified |
| EXP-008 | CI red on a test stub or exact-dict assert after a signature or shape change that passed locally | Testing | Verified |
| EXP-009 | A census test fails in CI on a syscall or type no source file declares | Testing | Verified |
| EXP-010 | `test_every_enum_event_type_has_a_seed_class` red after adding an event type | Testing | Guarded |
| EXP-011 | A consumer's loop "sends nothing" after a library release, with no error | Cross-repo | Verified |
| EXP-012 | An audit event write undoes the change it records | Runtime | Verified |
| EXP-013 | MCP handler sees the wrong user under `mcp` 1.x stateful HTTP | Runtime · MCP | Verified |
| EXP-014 | `ModuleNotFoundError` in the Docker runtime stage for a package pip "installed" | Docker | Guarded |
| EXP-015 | Code walking `app.routes` for `APIRoute` finds zero routes | FastAPI | Guarded |
| EXP-016 | A version or path printed from Python is not the one deployed | Diagnosis | Verified |
| EXP-017 | A green check on a PR that is not the commit you are about to merge | CI | Verified |
| EXP-018 | `git commit -m` from PowerShell 5.1 mangles quotes or splits the message | Tooling · Windows host | Verified |
| EXP-019 | SPA: agent run `status` is `PENDING_APPROVAL` in one response, `pending_approval` in another | Platform UI | Verified |

---

## EXP-001 — `UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97` in `assemble_changelog.py`

**Status:** Verified · **Category:** Tooling · **Scope:** Windows hosts

- **Symptom:** the release step `python scripts/assemble_changelog.py` crashes reading a `changelog.d/` fragment.
- **Cause:** the fragment was written as cp1252. On Windows, Python's `open(p, "w")` uses the ANSI code page unless told
  otherwise; an em dash became byte `0x97`. Per-PR CI never reads fragments as UTF-8, so it merged green.
- **Resolution:** re-encode it (`bytes.decode("cp1252").encode("utf-8")`), then fold. Write every tracked file with
  `encoding="utf-8"`.
- **Evidence:** #959's fragment, fixed in the release PR #960.
- **Applies when:** a script on Windows writes or rewrites a tracked text file.

## EXP-002 — every MCP test skipped locally

**Status:** Verified · **Category:** Testing · **Scope:** one Windows host with Smart App Control

- **Symptom:** `test_mcp_*` pass with many `s`; `-rs` shows `importorskip` reasons.
- **Cause:** Windows Application Control blocked pydantic-core 2.50.4's DLL in a fresh venv, so `mcp` failed to import
  and the MCP test files skipped.
- **Resolution:** run with `-rs` and require zero skips. Locally, use a venv with pydantic 2.14.2 + pydantic-core 2.50.2
  (one patch behind the pin). CI is the judge: it sets `AINDY_REQUIRE_MCP=1`, under which
  `tests/unit/test_mcp_stack_required.py` fails instead of skipping.
- **Evidence:** reported 2026-11-09; guard added with FR-66 phase 1 (#951).
- **Applies when:** running MCP tests anywhere but CI.

## EXP-003 — `test_dependency_pin_agreement`: "our declared pins cannot be installed alongside packages already in this environment"

**Status:** Verified · **Category:** Testing · **Scope:** a local environment that has drifted

- **Symptom:** locally, the test lists OpenTelemetry packages at 1.44.x against a pin of 1.45.2.
- **Cause:** the local interpreter has older installed packages. The test reads the environment, not the change.
- **Resolution:** not a regression. Deselect it locally, or `pip install` the pins. CI runs it on a fresh install.
- **Evidence:** 2026-11-11, while testing #959.
- **Applies when:** that test fails locally on packages your change didn't touch.

## EXP-004 — `LF will be replaced by CRLF` after a scripted edit

**Status:** Verified · **Category:** Tooling

- **Symptom:** git warns after an edit, or a diff shows every line changed.
- **Cause:** tracked files are CRLF. `sed -i` rewrote a whole file as LF (`runtime_only.py`, 2026-11-11), and inserting
  `"\n"` lines into a CRLF file left it mixed (`TECH_DEBT.md`, #962).
- **Resolution:** edit as bytes in Python, insert `"\r\n"`, then count: `t.count(b"\r\n")` vs bare `\n`. Normalize a
  mixed file before committing.
- **Applies when:** editing tracked files outside the Edit tool.

## EXP-005 — a stacked PR turns CONFLICTING after its base squash-merges

**Status:** Verified · **Category:** Git

- **Symptom:** PR B was branched from PR A's branch. A is squash-merged; B now conflicts on A's own lines.
- **Cause:** the squash commit has a different id from A's commits, so B still carries A's originals.
- **Resolution:** `git fetch && git rebase --onto origin/main <A's branch> <B's branch>`, then
  `git push --force-with-lease`.
- **Evidence:** #199–#202 (2026-07-08); #957 after #956 (2026-11-11).
- **Applies when:** any PR stacked on another.

## EXP-006 — Boot Smoke: "is on PyPI (JSON API 200) but pip cannot resolve it yet (attempt 1/10)"

**Status:** Guarded · **Category:** Release

- **Symptom:** that warning in the publish run's Boot Smoke, then `installed … on attempt 2`.
- **Cause:** PyPI's simple index lags its JSON API by a minute or two after upload.
- **Resolution:** none needed. `smoke-postgres.yml` retries 10 times, 30 s apart. Record the attempt number in the gate
  entry. Worry only if all ten fail.
- **Evidence:** 2.36.0 (attempt 2), 2.33.0, 2.32.0.

## EXP-007 — stale `dependabot/*` branches on origin with no open PR

**Status:** Verified · **Category:** Repo hygiene

- **Cause:** `delete_branch_on_merge` fires on merge only. A Dependabot PR closed unmerged, because a batch PR
  superseded it, keeps its branch.
- **Resolution:** at the end of each Dependabot batch, check each branch's PR is CLOSED
  (`gh pr list --state all --head <branch>`), then `git push origin --delete <branches>`. Owner-approved routine.
- **Evidence:** 10 deleted 2026-11-11 (#849–#945).

## EXP-008 — CI red on a test stub or exact-dict assert after a change that passed locally

**Status:** Verified · **Category:** Testing

- **Symptom:** Runtime Contracts fails in a file you never opened.
- **Cause:** a local sweep picked test files by name, or searched `tests/unit` only. Missed:
  - a fixed-signature stub written as a class `__call__` (#763);
  - `tests/api/test_version_api.py` pinning an exact response dict (#770);
  - an integration file pinning an old return value (#791).
- **Resolution:** `grep -rln <symbol> tests | xargs pytest`, over ALL of `tests/`. For a changed return value or
  shape, grep the old value too. Re-run the integration files from the previous phase.

## EXP-009 — a census test fails in CI on a syscall or type no source file declares

**Status:** Verified · **Category:** Testing

- **Cause:** an earlier test registered probes into a global registry (`SYSCALL_REGISTRY`) and didn't remove them; a
  census later in alphabetical order read them (#791).
- **Resolution:** a test that registers into a global registry pops what it added (an autouse fixture: snapshot the
  keys, pop the difference). Never clear and refill; the registry refuses duplicate writes.

## EXP-010 — `test_every_enum_event_type_has_a_seed_class` red after adding an event type

**Status:** Guarded · **Category:** Testing

- **Cause:** a new `SystemEventTypes` value needs a retention class in `AINDY/core/system_event_retention.py`, besides
  the contract baseline (`tests/baselines/system_event_contract.json`).
- **Resolution:** add both. Before pushing a new event type, route or model, run the census tests by name:
  `test_system_event_*`, `test_route_inventory*`, `test_coordination_agent_scopes`, `test_runtime_schema_contract`.
- **Evidence:** #953 went red on it; the guard is `tests/unit/test_system_event_retention.py`.

## EXP-011 — a consumer's loop "sends nothing" after a library release, with no error

**Status:** Verified · **Category:** Cross-repo

- **Cause:** aindy-sdk 1.2.0 changed how its watcher loop called `classifier.classify`. The app's launcher
  monkeypatches exactly that, so every sample raised `TypeError`, which the loop's catch-all logged and swallowed.
- **Resolution:** before changing a call shape a consumer may patch, read the consumer's code. A loop that logs and
  continues turns a crash into silence; test the consumer's replacement against the new loop.
- **Evidence:** fixed in aindy-sdk 1.2.1 (#20), guard `test_a_one_argument_classify_replacement_keeps_working`.

## EXP-012 — an audit event write undoes the change it records

**Status:** Verified · **Category:** Runtime

- **Cause:** `emit_system_event` (`AINDY/core/system_event_service.py`) rolls the session back when it fails. Emitting
  the audit row on your own session before the change commits means a failed audit undoes, say, a revocation.
- **Resolution:** commit the change first, then emit. Inside a pipeline, `queue_system_event` adds the row to the
  handler's session without rolling back.
- **Evidence:** FR-66 grant audit events (#953).

## EXP-013 — MCP handler sees the wrong user under `mcp` 1.x stateful HTTP

**Status:** Verified · **Category:** Runtime · MCP

- **Cause:** a contextvar set per request is stale in `mcp` 1.x stateful mode: the session task outlives the request.
- **Resolution:** take identity from the request's `context["user"]` / `context["auth"]`, never a contextvar.
- **Evidence:** FR-66 phase 2 (#952).

## EXP-014 — `ModuleNotFoundError` in the Docker runtime stage for a package pip "installed"

**Status:** Guarded · **Category:** Docker

- **Cause:** `pip install --prefix=/install` skips packages the builder already has in its system site-packages
  (`packaging`, `setuptools`), so `COPY --from=builder /install` leaves them out.
- **Resolution:** declare it in `pyproject.toml` and force it in the builder:
  `pip install --prefix=/install --ignore-installed "<pkg>"`.
- **Evidence:** `TECH_DEBT.md` PACKAGING-DEP-1 (`packaging` via `slowapi` → `limits`); Docker setuptools in 2.29.1.

## EXP-015 — code walking `app.routes` for `APIRoute` finds zero routes

**Status:** Guarded · **Category:** FastAPI

- **Cause:** since FastAPI 0.137, `include_router()` stores lazy `_IncludedRouter` wrappers.
- **Resolution:** the rule is in `CLAUDE.md` (Import hazards): use `_iter_api_routes`. `_IncludedRouter` isn't hashable;
  key de-duplication on `id()`.

## EXP-016 — a version or path printed from Python is not the one deployed

**Status:** Verified · **Category:** Diagnosis

- **Cause:** an interpreter's imports depend on its working directory, and `pythonpath = . AINDY` makes the checkout
  shadow an installed package. A version read on the host isn't the container's.
- **Resolution:** print `AINDY.__path__` beside the version, inside the container
  (`docker exec <api> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"`).
  Every app handoff carries this check.

## EXP-017 — a green check on a PR that is not the commit you are about to merge

**Status:** Verified · **Category:** CI

- **Cause:** checks read a minute after a push belong to the previous commit.
- **Resolution:** compare `gh pr view --json headRefOid` with `git rev-parse HEAD` before calling it green. For a
  release, wait for every `main` workflow on the merge commit before tagging. See also `TRUSTING_A_GREEN_CHECK.md`.

## EXP-018 — `git commit -m` from PowerShell 5.1 mangles quotes or splits the message

**Status:** Verified · **Category:** Tooling · **Scope:** Windows hosts

- **Resolution:** commit from bash with the message on stdin: `git commit -F - <<'EOF' … EOF`. Same for
  `gh pr create --body-file -`.

## EXP-019 — SPA: agent run `status` is `PENDING_APPROVAL` in one response, `pending_approval` in another

**Status:** Verified · **Category:** Platform UI

- **Cause:** two serializers. `POST /apps/agent/run` returns `to_execution_response` (uppercase status, no top-level
  `run_id`, result under `result.plan`); `GET /apps/agent/runs` returns `run_to_dict` (lowercase, flat).
- **Resolution:** after create, approve or reject, reload from the list endpoint and use that object for UI state.
