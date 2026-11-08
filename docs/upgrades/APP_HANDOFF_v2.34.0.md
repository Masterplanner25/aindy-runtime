---
title: "App Handoff — Runtime v2.34.0"
api_version: "1.0"
last_verified: "2026-11-08"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.34.0

**Answers FR-61, FR-62, FR-63 and FR-64**, filed in your `RUNTIME_2_32_0_UPGRADE.md`. **It also includes our review of your
FR-59 phase 3 routing (#566), as asked in the 2.32.0 handoff.** No migration.

Bump the `constraints.txt` pin to `==2.34.0` (your contract test moves the floor with it), then **rebuild the image**. No
ui-kit release: `@aindy/ui-kit` 2.1.1 stays current.

**You are on 2.32.0, so this also carries 2.33.0** ([`APP_HANDOFF_v2.33.0.md`](./APP_HANDOFF_v2.33.0.md)): pgvector
0.5.1, and memory expansion's similar half behind `AINDY_MEMORY_EXPAND_SIMILAR`, off. You call no expansion path, so it
changes nothing for you.

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v, importlib.metadata as m; print(v.__version__, m.version('pgvector'), list(AINDY.__path__))"
> # expect: 2.34.0 0.5.1 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. ★ Run two commands once

```bash
docker exec <api> aindy-runtime events backfill-call-users          # dry run: what it would fill
docker exec <api> aindy-runtime events backfill-call-users --yes
docker exec <api> aindy-runtime memory backfill-paths --yes         # FR-62: this failed before
```

## 1. FR-61: outbound-call events name their user (#931, DEC-123)

- **New events:** each `external.call.*` event now records the user whose call it was. That is:
  - the call site's own `user_id`; or
  - one attributed with `external_calls_attributed_to(user_id)`; or
  - the current request's caller.

  The runtime's embedding job attributes its model call to the memory node's owner. That was the hub of your trace
  `d534a3fd…`.
- **Old events:** `events backfill-call-users` gives them their trace's user, **only when every other event in that
  trace belongs to one user**. A trace with no user, or with two, is left alone. FR-60's per-user rule is unchanged.
- **Please report** what the command filled and skipped. Also re-read `d534a3fd…` scoped to its owner. We expect
  something close to the unscoped 584 nodes and 583 edges, not 291 edges at depth 1.

## 2. FR-62: `memory backfill-paths --yes` works (#930)

A fresh `aindy-runtime` process never loaded the `users` and `system_events` models, so the first write failed. All seven
database commands now load them. A test runs the command in a new interpreter.

A writing run also keeps `would_fill` as what it found to do. It used to report 0.

## 3. FR-63: key errors come out of the chat helpers as themselves (#930)

`chat_completion` and `chat_completion_deepseek` now raise `ProviderKeyRejected` and `ProviderKeyRequired` as
themselves, with no cause cycle. Your bounded walk in `apps/_shared/provider_keys.key_problem` keeps working, and is now
only a fallback.

## 4. FR-64: what we found (#932, #933, DEC-124)

We reproduced it on Postgres with your call shape: a flow node dispatching a syscall with the flow's own session as
`_db`, and a handler that raises.

| Handler on failure | Real session | Shared outer transaction (a test fixture) |
|---|---|---|
| raises, session untouched | run survives | run survives |
| rolls back the caller's session | run survives | **"FlowRun … disappeared"** |

- **The run does not disappear live.** The runner commits the `FlowRun` when it starts. Your report came from
  `test_genesis_through_its_syscall_answers_402`, which runs under a fixture where the app's and the test's sessions
  share one outer transaction. There the runner's commit isn't a real commit.
- **Fixed in #932: the message was lost.** A node that ran out of retries failed with
  `Node … failed after 3 retries` and dropped the handler's sentence. `result.error` now ends with the node's last error,
  so your `raise_problem_in` finds the `HTTP_402:` sentence.
- **Fixed in #933 (DEC-124): runtime handlers.** A failing runtime write handler on a caller's session rolls back only its
  own work, inside a savepoint, not your whole session. **This can't cover a handler of yours that calls
  `db.rollback()` itself.**

**Two things on your side:**

1. **`_syscall_node` turns every syscall error into `RETRY`,** in `automation/flows/flow_definitions.py` and
   `system_flows.py`. We measured it: **a failing handler runs 3 times.** For a refused user key inside the Genesis flow,
   that's three calls with a key the provider has already refused. Return `FAILURE` for a key problem. The cheapest
   check is `key_problem(...)` on the error, or the `HTTP_402:` prefix.
2. **Run a route test that answers 4xx/5xx on a private engine,** not the shared fixture. Otherwise a rollback erases the
   test's own rows. The runtime ships the recipe as `tests/fixtures/db.py::build_private_engine`.

## 5. Our review of your FR-59 phase 3 routing (#566)

**It's sound, and nothing blocks turning on `per_user` once the Settings card ships.** What we checked:

- **Who pays:** every one of the 23 call sites gets the paying user from the signed-in request, the syscall's verified
  identity, a run's owner, or a job row's owner. None takes it from a request body.
- **Clients:** none is shared across users. User keys go through `get_llm_client_for_user` or `user_key_client`, so each
  key gets its own breaker.
- **Refused keys:** a refused key on a raw HTTP call is caught by `refused()` before any other failure, becomes a 402, and
  is never retried. The exception is §4's `RETRY` mapping inside flows.
- **Scheduled jobs:** they skip a user who can't pay.
- **Server keys on purpose:** owner notices, and the Anthropic planner for a run with no user. Real agent runs always carry
  their creator's user (`creation.py`, `replay.py`), so that second case never reaches a user's run.

**Three small notes:**

1. You import `_count_key_source`, which is private. Use **`count_key_source`**, public from 2.34.0. The old name stays as
   an alias.
2. `resolution_engines._call` puts up to 200 characters of the provider's error body in its exception. A refused key is
   caught first, so no key can be echoed back that way. Keep that order if you touch it.
3. Embeddings stay on the server's key (DEC-121 D4), as your routing assumes.

## 6. Asks

1. Bump the pin to `==2.34.0`, rebuild, run §0, and run the in-container check.
2. Report what `backfill-call-users` filled and skipped, and the re-read of `d534a3fd…` (§1).
3. Fix the two items in §4 before you turn on `per_user`.
4. Run your full test suite on it, and report anything that changed.

Report in your `RUNTIME_2_34_0_UPGRADE.md`.
