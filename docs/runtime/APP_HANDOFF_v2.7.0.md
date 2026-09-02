---
title: "App Handoff — Runtime v2.7.0"
api_version: "1.0"
last_verified: "2026-09-02"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.7.0

**This is a plain `pip install`.** No schema step, no migration, no flag to flip, no route
newly enforcing a scope. Verified rather than assumed:

```bash
git diff v2.6.0..v2.7.0 -- AINDY/db/models/ AINDY/memory/memory_persistence.py   # empty
git diff v2.6.0..v2.7.0 -- alembic/versions/                                     # empty
git diff v2.6.0..v2.7.0 -- AINDY/routes/ | grep enforce_api_key_scope            # no hits
```

Two things below are worth reading before you upgrade anyway — one because it changes
behaviour you did not ask to change, and one because it is a security fix in a dependency.
Everything after §3 is informational.

---

## 1. ★ The scheduler now hands queued work to a thread pool — *if* you run thread mode

`AINDY_ASYNC_SCHEDULER_DISPATCH` is new and defaults to **on**. Where it applies, a queued
item is handed to the thread pool instead of being executed inside the 1-second scheduler
tick. That is the fix for `FR-15`, the defect behind the 177-second dispatch gap you reported:
`schedule()` was the only queue drainer, ran each item synchronously, and was registered
`max_instances=1`, so one slow flow blocked every other queued item — and wait expiry, and
stale-wait cleanup, which share that tick.

**Whether it applies to you depends on `EXECUTION_MODE`, and the answer is probably "no".**

| Your deployment | Effect |
|---|---|
| `EXECUTION_MODE=thread` (dev, single-instance, anything not on the prod overlay) | Dispatch behaviour changes as described |
| `EXECUTION_MODE=distributed` (**what `docker-compose.prod.yml` sets**) | **Nothing changes.** The setting is refused, and the `FR-15` serialisation is still present |

**Do not read that second row as a caveat — it is the honest scope.** The distributed
transport cannot carry the scheduler's resume callback across a process boundary, and routing
it there would have enqueued a job no worker could resolve. A worker treats an unresolvable
job as *finished* — it logs `JobLog not found`, acknowledges the message rather than
dead-lettering it, and reports success — so every scheduler resume would have been lost
silently, with no dead-letter entry and no retry. That is worse than the starvation being
fixed, so the runtime refuses it outright rather than trusting the variable to be unset.

Fixing `FR-15` for distributed deployments requires reconstructing the resume from `run_id`
instead of carrying a closure. That is a build, it is still open, and we will tell you when it
lands.

**How to tell which behaviour you actually have** — and reading the environment variable
cannot answer it, because distributed mode overrides the setting:

```
aindy_execution_dispatch_total{mode="async"}   # moving  → the new behaviour
aindy_execution_dispatch_total{mode="inline"}  # moving  → the old one
```

That metric is also new in this release. Before it, whether an execution ran on the caller's
thread or the pool was not observable at all.

**To opt out:** `AINDY_ASYNC_SCHEDULER_DISPATCH=0`.

---

## 2. ★ `nodus-lang` 5.9.0 carries three security fixes — read this if you run guest Nodus code

We moved `nodus-lang` 5.1.0 → 5.9.0, eight releases. Three of them fix security issues, and
none was visible from the version number:

- **A capability policy could be bypassed by spelling a call differently.** `agent_call` is
  governed by the `agent.call` capability; `agent_call_async` carried **no capability at all**.
  A `DenyList("agent.call")` refused one spelling and permitted the other — same agent, same
  policy. If you rely on a capability policy to deny agent calls from guest code, **it had a
  hole until this release.**
- **A relocated workflow store fell outside the guest filesystem jail.** The floor decided what
  counted as runtime state by matching a literal `.nodus` path segment, so the supported way to
  move the store also moved it out of the jail. Only reachable if you set
  `NODUS_WORKFLOW_STORE_ROOT`; we do not, and neither did you unless you set it yourself.
- **A graph response could name another request's graph** — id, status, and full task map
  including step return values. A cross-request leak on any server handling more than one
  caller.

**No action beyond upgrading.** We changed no runtime code for this; the verification is in the
changelog entry.

---

## 3. A new refusal you will not hit today, and what would trip it

`dispatch()` now raises `UndistributableWorkError` rather than enqueueing work onto the
distributed queue with no `log_id` in its context. Every existing job-submission path passes
one, so **nothing that works today is refused** — that was previously the only thing keeping it
correct, and it was an accident rather than a check.

You would trip this only by adding a new dispatch call site. If you do, the work needs a
`JobLog` row (`async_job_service.submit_async_job`) or it must stay out of distributed dispatch.
The error message says both.

---

## 4. Informational

- **`nltk` 3.10.0 → 3.10.3** clears `PYSEC-2026-3726` / `CVE-2026-62383`, a symlink-based
  arbitrary file read. This runtime never reached the affected code path; the pin exists only to
  control what `textstat` resolves to.
- **`pip-audit (OSV)` now runs on pushes to `main`, not only on pull requests.** It previously
  gated every PR into `main` and never gated `main` itself, so a newly published advisory could
  turn it red on an unchanged branch with nothing surfacing it for up to a week. That is exactly
  what happened on 2026-08-31.
- **`starlette` 1.3.1 → 1.6.0, `setuptools` 83 → 84, `pymongo` 4.16 → 4.17,
  `python-json-logger` 4.1 → 4.2.** No breaking changes and no advisories in any of them; the
  release notes for each were read. One behaviour fix you may notice: python-json-logger no
  longer mutates a `dict` you log — `exc_info` and `stack_info` were previously added to your
  dictionary.
- **`AINDY_ASYNC_HEAVY_EXECUTION` is now route-facing only.** It still controls whether
  `POST /agents` and the nodus execute route answer `202` queued instead of a result. It no
  longer has anything to do with scheduler dispatch. If you set it for either reason, re-read
  which one you wanted.

---

## 5. Verification

Full CI green on the release commit, including `Integration Tests (PostgreSQL + Redis)`,
`Platform UI Build`, `Runtime Package Build` and `Install Smoke Test`.

**`Upgrade Path Guard`: passed trivially, and that is the accurate statement.** The guard
installs the previous released wheel, builds its schema, then runs this build's
`bootstrap-schema` over that database. **This release contains no runtime schema change, so
there was no drift for it to detect** — a broken guard and a clean release are indistinguishable
in that job. The half that carries meaning here is its `negative-control`, which injects
synthetic drift and requires exit 3; that is what was actually proven.

Sandbox escape gate: see the entry appended to `SANDBOX_ESCAPE_AUDIT.md` for this tag.
