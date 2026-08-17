---
title: "App Handoff — Runtime v2.4.0"
api_version: "1.0"
last_verified: "2026-08-17"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.4.0

**Read §1 before upgrading.** This release enforces scopes on 91 of 126 routes; before it, 29
enforced anything. Nothing changes for a signed-in user, and that claim is tested. **Platform API
keys are the callers to check.**

No schema change. No migration. No new env var required.

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

## 6. Soak flags — still off, still yours to exercise

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

## 7. Known-open, so you are not surprised

- **`IDEM-12`** — a second `sys.v1.agent.undo` re-invokes every compensator. Latent only because
  **zero compensators are registered**; it goes live with the first one.
- **`ROUTE-EFFECT-BYPASS-1` D** — `POST /memory/nodes/search` still reaches the DAO directly. It
  uses `find_similar` + `min_similarity`, which `sys.v1.memory.search` neither accepts nor uses,
  so rewiring would change search semantics under cover of a mediation fix.
- **`CAPABILITY-PROVIDER-TIMEOUT-1`** — fixed by caching, but the *first* capability lookup in a
  process still spawns one subprocess per provider. On a heavily contended host that can still
  time out once; it now retries rather than persisting. Symptom would be a tool refused with
  *"has no registered capability mapping"*.
