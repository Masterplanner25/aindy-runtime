---
title: "App Handoff — aindy-runtime v1.11.0"
api_version: "1.0"
last_verified: "2026-08-01"
status: current
owner: "platform-team"
---
# App Handoff — `aindy-runtime` v1.11.0

**Published to PyPI 2026-08-01 as `aindy-runtime==1.11.0`.** Release commit `4e8f917`,
tag `v1.11.0`, full pipeline green, Linux sandbox-escape gate 17/17 PASS
(`SANDBOX_ESCAPE_AUDIT.md` Entry 011).

Reciprocal of the app team's *"Runtime Feature Requests — handoff to aindy-runtime"*.
Minor, not patch: it adds a public endpoint.

---

## 1. Read this first — the one thing you can get wrong by doing nothing

`DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` default moved **30000 → 60000**.

**Why:** verified against real PostgreSQL that the flow runner's session is held
`idle in transaction` for the *entire* duration of node execution, while a nodus run may
legitimately occupy **45s** (`AINDY_NODUS_MAX_EXECUTION_MS` 30s + `AINDY_NODUS_BOOT_ALLOWANCE_MS`
15s). At a 30s cap, a slow-but-in-budget nodus run had its connection terminated mid-flight:

```
psycopg2.OperationalError: server closed the connection unexpectedly
  → sqlalchemy.exc.PendingRollbackError: Can't reconnect until invalid transaction is rolled back
```

**The default change only helps deployments that do not pin the variable.** If you set it
explicitly, you keep the old value and therefore keep the bug.

**Your case specifically — you are already fine, but check before changing it.** Your
RT-MEMTXN-LEAK-1 notes record raising this to `120000` for the nodus cold-start work. 120000
clears the 45s ceiling comfortably, so **no action is required**. The action item is negative:
**do not lower it below ~45s**, and if you ever raise `AINDY_NODUS_MAX_EXECUTION_MS` or
`AINDY_NODUS_BOOT_ALLOWANCE_MS`, raise this above their sum.

**What is NOT fixed:** the transaction is still held across node execution. v1.11.0 raises the
ceiling above the hold; it does not remove it. The root-cause fix ships **opt-in** — see §4.

Tracked as `DB-NODUS-BUDGET-1` in `TECH_DEBT.md`.

---

## 2. FR-6 item 1 — `POST /auth/password/change` is available

Closes the "a signed-in user cannot rotate their own password" half of your FR-6. You can wire
the UI now.

```http
POST /auth/password/change
Authorization: Bearer <jwt>
Content-Type: application/json

{"current_password": "…", "new_password": "…"}
```

**Response** — the canonical envelope, same shape as `/auth/login`:

```json
{"status": "success", "data": {"access_token": "<jwt>", "token_type": "bearer"}}
```

So `unwrapEnvelope` applies, exactly as for `loginUser` — you can reuse your existing
token-store path verbatim.

### ⚠️ You must store the returned token

The change bumps `token_version`, which invalidates **every** session — including the caller's.
The returned token is freshly-versioned so the user stays signed in. **Keep the old token and
the next request 401s.** Recorded in `UI_CONTRACT.md` and `SDK_CONTRACT.md`.

### Contract details

| | |
|---|---|
| Auth | **Bearer JWT only.** A platform API key has no password to rotate → 401 (same guard as `/auth/logout`) |
| Rate limit | `5/minute` (stricter than login's `10/minute`) |
| Min length | 8 (`MIN_PASSWORD_LENGTH`) — enforced on **change only** |
| 401 | current password incorrect (hash and `token_version` left untouched) |
| 400 | new password too short, **or** identical to current |
| 403 | account disabled |
| 404 | user not found |

Neither password reaches `input_payload` or the emitted `auth.password.changed` event — both
are trace-logged surfaces, and there is a test asserting it.

**Note on scope:** `register_user` still has **no** password policy. `MIN_PASSWORD_LENGTH`
guards only one of the paths that can set a password. Adding it to register would reject
existing callers, so it is deliberately a separate decision — flag if you want it.

---

## 3. FR-6 items 2+3 (forgot / reset) — still open, and why

Not built. The auth half is small — both reuse `hash_password` / `verify_password` and the
`token_version` bump item 1 established. **The blocker is delivery, not auth:** a reset token is
worthless unless it reaches the user, which needs an email channel — i.e. FR-1 connector/egress
territory.

That forces a decision the runtime should not make unilaterally:

- **(a)** the runtime sends the mail itself via an `email` connector — needs a registered
  connector, a `CapabilityPolicy`, and secret-broker credentials, all currently vacuous by
  default; or
- **(b)** the runtime returns the token and **you** deliver it — smaller runtime surface, but it
  puts a live credential-reset token in an HTTP response body, so it is only safe behind an
  admin/service-authenticated caller, never the public forgot endpoint.

Also undecided: token storage (new table vs. a signed stateless token carrying `user_id` +
`token_version`, the latter self-invalidating and schema-free), single-use enforcement, TTL, and
whether `/auth/password/forgot` must return 200 for unknown emails to avoid becoming an
account-enumeration oracle (it should).

**We need your call on (a) vs (b)** before this can be built. Tracked as `APP-FR-*` → FR-6 in
`TECH_DEBT.md`.

---

## 4. New opt-in flag

**`AINDY_MEMORY_RECALL_OWN_SESSION`** (default off) — the DB-NODUS-BUDGET-1 root-cause fix.
Memory recall is read-only, but running it on the caller's session leaves that session inside a
transaction, which is what the flow runner then holds across node execution. When on, recall uses
its own short-lived session and returns the connection immediately.

Falls back to the caller's session if one cannot be obtained, so recall can never become
unavailable. Off by default because a caller relying on seeing its own uncommitted writes through
recall would see a behaviour change — **if you have such a caller, tell us before we flip it.**

---

## 5. If you install the `[mcp]` extra

The extra is now capped at **`mcp>=1.0.0,<2`**. `mcp 2.0.0` removed the 1.x low-level
`Server.list_tools()` decorator that `nodus-mcp 0.1.2` is built on, so an uncapped install
resolves to an SDK that raises `AttributeError` at server construction — the extra is broken at
import-of-server time, not merely in tests.

**If you install `mcp` yourself rather than through the extra, apply the same cap.** Lifted when
a `nodus-mcp` release targets the 2.x API. Tracked as `MCP-SDK-2X-1`.

---

## 6. Suggested floor

```
aindy-runtime>=1.11.0,<2.0
```

Nothing in v1.11.0 is source-breaking for app code. The only behavioural change that can reach
you is §1, and only if you pin the timeout.

---

## Also in this release

- **`aindy-runtime memory prune-cascade-debris`** — one-time cleanup for deployments that ran
  before the RT-MEMTXN-LEAK-1 fix and accumulated memory nodes recording nothing but the
  runtime's own embedding jobs. Report-only by default; `--yes` to delete, in committed batches.
  Scoped by `extra.event_payload.task_name`, so no user- or app-authored memory can match.

## Not in this release

Everything on the soak-then-flip list stays default-off: `AINDY_DURABLE_CONTINUATION[_ALL]`,
`AINDY_MEMORY_IDEMPOTENCY`, `AINDY_NEXT_ACTION_ACTING`, `AINDY_PLANNER_MEMORY_INJECTION`,
`AINDY_ASYNC_JOB_LOOP_CLOSURE`, `AINDY_DELEGATION_PRIVATE_MEMORY`, `AINDY_NODUS_WARM_POOL`,
`AINDY_AUTONOMOUS_EXECUTE_WINDOW`. Several are things you have been waiting on — say which
matter and we will prioritise the soak.
