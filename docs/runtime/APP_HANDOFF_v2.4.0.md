---
title: "App Handoff — Runtime v2.4.0"
api_version: "1.0"
last_verified: "2026-08-19"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.4.0

**Read §1 before upgrading.** This release enforces scopes on 91 of 126 routes; before it, 29
enforced anything. Nothing changes for a signed-in user, and that claim is tested. **Platform API
keys are the callers to check.**

No schema change. No migration. No new env var required.

> **★ Upgrade to `2.4.1`, not `2.4.0`.** Everything below still applies unchanged — `2.4.1` is a
> patch on top of it with no schema change, no migration, no new env var, and no further scope
> movement. It exists because `2.4.0` shipped with `nodus-lang` pinned at `5.0.1`, and
> `nodus-lang <= 5.0.2` shares one guest memory dict across every `NodusRuntime` in a process.
>
> **Whether that reached you depends on one flag: `AINDY_NODUS_WARM_POOL`.** It is off by
> default. Left off, worker processes are not reused and this is latent — upgrade at your
> convenience. **Turned on, two tenants' `.nd` scripts served by the same warm worker could read
> each other's `memory_put`/`memory_get` values** — upgrade before anything else in this
> handoff, or turn the flag off until you have.
>
> No app-side change is needed either way; the fix is entirely in the pin.
>
> Full detail — including the two dependency bumps that need a second look on your side — is in **`APP_HANDOFF_v2.4.1.md`**.

**No feature requests were closed in this release.** `FR-12b` and `FR-16` appear in the changelog
only as context for other work; the last FR movement was in v2.3.0 (`FR-14`'s branchable exit
codes, `FR-16`'s nodus bump). Nothing on `APP-FR-*` moved here, and **FR-6 items 2+3** and
**FR-14's remaining half** are still where they were. This release is authorization, dependency
adoption and packaging.

---

## 1. Scope enforcement — what to check before upgrading

### 1.1 Interactive users are unaffected

A JWT session derives its scopes from the user row per request. The ordinary set is
`flow.read`, `flow.execute`, `memory.read`, `memory.write`, `agent.run`, `execution.read`; an
admin session adds `webhook.manage` and `platform.admin`. **Every gate added this release is
satisfiable by one of those two sets**, and a test drives the real routes to prove it — so no
grant has to be issued to anyone and no session is invalidated.

If you see a 403 from a browser session after upgrading, that is a bug in this runtime, not a
configuration step you missed. Report it.

### 1.2 Platform API keys are where to look

An API key carries exactly the scopes it was issued with. Routes that previously accepted any
authenticated key now require a specific scope.

| Routes | Scope now required |
|---|---|
| `/memory/*` reads — `GET /nodes`, `/nodes/{id}`, `/history`, `/links`, `/traverse`, `/performance`, `/agents`, `/agents/{ns}/recall`; `POST /nodes/search`, `/nodes/expand`, `/recall`, `/recall/v3`, `/federated/recall`, `/suggest` | `memory.read` **or** `memory.write` |
| `/memory/*` writes — `POST /nodes`, `PUT /nodes/{id}`, `POST /links`, `/nodes/{id}/share`, `/nodes/{id}/feedback` | `memory.write` |
| `POST /memory/nodus/execute`, `/memory/execute`, `/memory/execute/complete` | `flow.execute` |
| `/coordination/*` agents — `agents`, `agents/status`, `agents/register`, `agents/{id}/heartbeat`, `DELETE agents/{id}`, `graph`, `messages/inbox`, `messages/{id}/acknowledge` | `agent.run` |
| `/coordination/runs`, `/runs/{id}/children`, `/conflict/run` | `execution.read` |
| `/coordination/memory/shared`, `/conflict/memory` | `memory.read` **or** `memory.write` |
| `/platform/agents` (all five, incl. `{slug}/restore`) | `agent.run` |
| `/platform/keys`, `/platform/queue/*`, `/platform/nodes`, `/platform/observability/*`, `/platform/flows/runs*`, `POST`+`DELETE /platform/flows`, `/platform/ops/rotate-secret-key` | `platform.admin` |
| `/platform/webhooks` (all four) | `webhook.manage` |
| `/platform/nodus/*` — run, upload, list, schedule, flow | `flow.execute` |
| `/platform/tenants/{id}/usage` | `execution.read` |

`platform.admin` satisfies any gate.

**Two routes stay ungated at the route level, deliberately:** `POST /platform/syscall` and
`GET /platform/syscalls`. Their authority is resolved per syscall, so the SDK keeps working with
narrow scopes like `memory.read`. A route-level scope there would either constrain nothing or
break every SDK caller.

### 1.3 The one first-party caller affected

`aindy-runtime nodus run` and `aindy-runtime nodus upload` post to `/platform/nodus/*`. **A
platform key used with the CLI now needs `flow.execute`.** A Bearer JWT for an admin is
unaffected.

Nothing else in the runtime, the SDK, or the app monolith sends `X-Platform-Key` — the SDK's
`client.memory.*` goes through `POST /platform/syscall`, which is one of the two routes above.

### 1.4 Audit your issued keys

Until this release, **any** authenticated API key could reach the entire `/platform` tree —
including minting new keys, draining the dead-letter queue, and **rotating the platform signing
key**. Whoever calls that last one chooses the new secret, so they can then forge tokens for any
user.

Recommended before or immediately after upgrading:

- Revoke any platform key you cannot account for.
- Review `users.is_admin` for accounts you did not promote yourself.
- Rotate `SECRET_KEY` deliberately if you cannot account for every key that has existed.

**Escape hatch:** `AINDY_JWT_SCOPE_ENFORCEMENT=0` disables scope enforcement for JWT sessions
only. It does not affect API-key enforcement and is a hatch, not a supported mode.

---

## 2. `nodus-lang` 5.0.1

The runtime now pins `nodus-lang==5.0.1` and `nodus-mcp>=0.1.3`. **Nothing is required of the
app.** The monolith constructs `NodusRuntime` nowhere, so 5.x's deny-by-default embedding change
does not reach it.

If you install `nodus-lang` directly anywhere, note the pin is **exact** — `pip install
nodus-lang==X` will leave your environment inconsistent with the runtime's declared requirement,
and an editable install moves it back.

---

## 3. Compatibility window

`recommended_runtime_requirement` stays **`>=2.0,<3.0`** — it derives from the major series, so
2.4.0 moves no consumer pin.

| Consumer | Status against 2.4.0 |
|---|---|
| `aindy-sdk` | **No change needed.** Its whole surface is `POST /platform/syscall`, which is deliberately not route-gated; per-syscall scope resolution is unchanged. `client.memory.*` keeps working with a `memory.read`-scoped key. |
| `@aindy/ui-kit` / platform SPA | **No change needed.** Every route the console calls is reachable by an admin session, which derives `platform.admin` and `webhook.manage`. No envelope or response shape changed. |
| `aindy-apps-monolith` | **No change needed** for JWT traffic, which is all of it. `smoke_autonomy.py` calls `/apps/coordination/*` with a Bearer token and is unaffected. |

The only surface that can regress is a **platform API key** issued without the scopes in §1.2.

---

## 4. Security fixes in this release

| Finding | What it was |
|---|---|
| `KEY-SCOPE-ESCALATION-1` | A `flow.read`-only API key could mint itself a `platform.admin` key, then promote its own user row to admin — persisting after the key was revoked |
| `KEY-SCOPE-ESCALATION-1` (2) | The same key could rotate the platform JWT signing key, choosing the new secret |
| `HTTP-SCOPE-GAP-1` D + remainder | 62 routes gated that previously checked identity only |

All three were demonstrated end to end against real PostgreSQL, not inferred.

---

## 5. Upgrade path

**No runtime-owned schema changed in this release.** `git diff v2.3.0..v2.4.0 -- AINDY/db/models/
AINDY/memory/memory_persistence.py` is empty, so `bootstrap-schema` exits 0 against an existing
database and there is nothing to reconcile.

**What that means for the `Upgrade Path Guard`:** its main job passes *trivially* here — there is
no drift for it to catch. The half that carries meaning on a release like this is the
`negative-control` job, which injects synthetic drift and requires the guard to detect it as exit
3. That control passed. "The guard was green" means different things on different releases; on
this one it means the control worked.

---

## 6. What the installed package now contains

Packaging changed in this release, and it is in the published artifact:

- **`AINDY/llms.txt` and `AINDY/llms-full.txt` now ship.** They previously existed only at the
  repo root, so they reached neither the wheel nor the sdist — they served a reader who had
  already found the repo, which is the audience that needed them least. If you point any tooling
  at an orientation file, it can now read one from the installed package.
- **`CONTRIBUTORS.md` ships**, as `dist-info/licenses/CONTRIBUTORS.md` in the wheel and at the
  root of the sdist.
- **The Rust scorer's source ships in the sdist** (`Cargo.toml`, `Cargo.lock`, `build.rs`,
  `src/*.rs`) so it can be built locally. The **compiled** artifact deliberately does not: the
  wheel is `py3-none-any`, and a `.pyd`/`.so` inside one installs a broken binary for anyone on a
  different OS/arch/CPython. `native_bridge.py` falls back to the Python scorer, which is the
  supported configuration — **installed users have always run the Python path**, and now the
  README says so.
- **Cargo build output no longer leaks into the sdist.** `recursive-include AINDY *.json` had
  been matching ~200 fingerprint files under the crate's `target/`, some embedding the building
  machine's absolute paths. It never reached PyPI — the published 2.3.0 wheel was checked and
  contains none — because CI builds where `target/` is unpopulated. It was a local-build hazard.

Nothing here requires action from you.

---

## 7. Soak flags — still off, still yours to exercise

Unchanged from v2.3.0. These ship default-off and need app-side soak before the runtime flips
them:

| Flag | What it enables | Blocking |
|---|---|---|
| `AINDY_ASYNC_HEAVY_EXECUTION` | Async routing for flow/agent/nodus/job work (`FR-15` (a)) | Dispatch is still serialised through one scheduler slot until this is on |
| `AINDY_SYSCALL_IDEMPOTENCY` | At-most-once for the 7 `EXACTLY_ONCE` syscalls (`IDEM-11`) | Audit done; only the flip remains |
| `AINDY_CHILD_CONTEXT_CLAMP` | Clamps `child_context()` so it cannot widen capabilities (`AUTHORITY-VALUE-1`) | **Do not flip yet** — the monolith's `_dispatch_owner_syscall` grants the nested syscall's capability while holding only the outer one, so clamping intersects to empty and denies a working call |
| `AINDY_DELEGATION_PRIVATE_MEMORY` | Delegation-scoped memory (`RTR-4`) | Soak + flip |
| `AINDY_MEMORY_RECALL_OWN_SESSION` | Recall on its own DB session (`DB-NODUS-BUDGET-1`) | Soak + flip |
| `AINDY_NODUS_WARM_POOL` | Warm worker pool for nodus cold-start | Soak + flip |
| `AINDY_DURABLE_CONTINUATION` | Transparent crash continuation (`ECOGAP-1` Phase 3) | Soak + flip |

---

## 8. Known-open, so you are not surprised

- **`IDEM-12`** — a second `sys.v1.agent.undo` re-invokes every compensator. Latent only because
  **zero compensators are registered**; it goes live with the first one.
- **`ROUTE-EFFECT-BYPASS-1` D** — `POST /memory/nodes/search` still reaches the DAO directly. It
  uses `find_similar` + `min_similarity`, which `sys.v1.memory.search` neither accepts nor uses,
  so rewiring would change search semantics under cover of a mediation fix.
- **`CAPABILITY-PROVIDER-TIMEOUT-1`** — fixed by caching, but the *first* capability lookup in a
  process still spawns one subprocess per provider. On a heavily contended host that can still
  time out once; it now retries rather than persisting. Symptom would be a tool refused with
  *"has no registered capability mapping"*.

---

## 9. Landed after the tag — **not** in v2.4.0

So you do not go looking for these in `2.4.0`:

- **Grouped dependency bumps** — `SQLAlchemy` 2.0.52, `uvicorn` 0.52.3, `Mako` 1.4.1, `regex`
  2026.7.19, `prometheus-fastapi-instrumentator` 8.1.0, plus the Rust `cc`/`uuid` lockfile
  bumps. Merged to `main` after the tag; they ship in the next release.
