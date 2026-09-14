---
title: "Tutorials"
last_verified: "2026-09-13"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Tutorials

Three tutorials. Each takes under ten minutes, runs against a **bare runtime** — no app
plugins, no custom nodes — and ends with something you can see in memory.

| # | Tutorial | What you'll see |
|---|---|---|
| 1 | [Memory-Driven Task Analyzer](./01-memory-driven-workflow.md) | Write → read → analyze in a Nodus script → write back → emit |
| 2 | [Event-Driven Automation](./02-event-driven-automation.md) | A script suspends the run, an external signal resumes it with a payload |
| 3 | [Scheduled Intelligence](./03-scheduled-execution.md) | The same kind of script on a cron, with a webhook on its event |

> **Corrected 2026-09-13.** Every call in these three was checked against the SDK source, the
> runtime's syscall registry and routes, and the installed Nodus interpreter (5.13.0). The
> previous versions had been "re-validated on relocation" in June with inline *Runtime note*
> callouts — but the callouts described things that do not exist (`event.wait()`, `emit()`,
> `sys.v1.event.wait`, a `flat` key on `memory.tree`, an `analyze_tasks` flow), and every
> script in them failed to parse on the current interpreter (`if`/`while` need parentheses
> since nodus 5). The largest correction is in Tutorial 2: a guest script does not "pause
> mid-execution and pick up where it left off" — it exits, and **runs again from the top** on
> resume. The tutorial now shows that shape.

## Prerequisites

**1. A running server.** Follow [`docs/operations/QUICKSTART.md`](../operations/QUICKSTART.md)
up to and including the *Create the first admin* step. You need Postgres; SQLite is for unit
tests only.

**2. An admin token.** The SDK accepts either a platform API key (`aindy_…`, sent as
`X-Platform-Key`) or a JWT (sent as `Bearer`). The simplest path is the JWT of the admin you
just created — an admin JWT carries every scope, including `platform.admin`, which Tutorial 2's
resume step needs:

```bash
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "changeme"}'
# {"access_token": "eyJ...", "token_type": "bearer"}

export AINDY_BASE_URL="http://localhost:8000"
export AINDY_API_KEY="eyJ..."        # the access_token — the SDK's parameter is named api_key
```

If you would rather use a scoped key, mint one at `POST /platform/keys` as that admin with
scopes `memory.read`, `memory.write`, `flow.execute`, `event.emit` (Tutorials 1 and 3) plus
`platform.admin` (Tutorial 2's resume) and `webhook.manage` (the optional webhook steps).
`KEY-SCOPE-ESCALATION-1`: a key can only be granted scopes its creator holds.

**3. Your tenant id.** Memory paths are `/memory/{tenant}/{namespace}/{type}/{id}` and the
tenant is your user id — the JWT's `sub`. Tutorial 1 Step 1 derives it (`tenant_from_jwt`);
Tutorials 2 and 3 import that helper. A path under any other tenant is refused with
`TENANT_VIOLATION` — which is what every previous version of these tutorials did on its first
line of real work (`/memory/demo/…` put `demo` in the tenant slot).

**4. The SDK.**

```bash
pip install aindy-sdk          # imported as `aindy_sdk`
```

## What the tutorials deliberately do not use

- **Custom flow nodes.** `POST /platform/flows` requires `platform.admin` *and* every node
  named in the flow to exist in `NODE_REGISTRY` — which is empty on a bare runtime; nodes come
  from app plugins. The analysis step in Tutorial 1 is therefore a Nodus script run through
  `POST /platform/nodus/run` (`flow.execute` scope), which the runtime owns end to end.
- **`sys.v1.flow.run` from a script.** Same reason: there is no flow to run on a bare runtime.
- **Bus-delivered event payloads.** An event emitted through `sys.v1.event.emit` can *wake* a
  waiting run, but the event bus carries no payload — the resumed script would see nothing.
  Tutorial 2 resumes through `POST /platform/flows/runs/{run_id}/resume`, the one path that
  injects the payload. (`WAIT-PAYLOAD-PATH-1` in `TECH_DEBT.md` records the asymmetry.)
