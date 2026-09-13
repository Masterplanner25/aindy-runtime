---
title: "App Handoff — aindy-runtime v2.0.0"
api_version: "1.0"
last_verified: "2026-08-02"
status: current
owner: "platform-team"
---
# App Handoff — `aindy-runtime` v2.0.0

**Published to PyPI 2026-08-02 as `aindy-runtime==2.0.0`** (verified against the package
index). Release commit `bd8f352`, tag `v2.0.0`, full pipeline green, Linux sandbox-escape
gate 17/17 PASS (`SANDBOX_ESCAPE_AUDIT.md` Entry 012).

**Major.** The breaking changes are concentrated in auth, and every one is a deliberate
security tightening — none is a rename or a refactor. Supersedes
`APP_HANDOFF_v1.11.0.md`.

---

## 1. Two changes must land together, in one deploy

**`aindy-runtime>=2.0.0,<3.0` on its own will break registration in the UI.**

`POST /auth/register` no longer returns an access token, so any client that auto-logs-in
from the register response has nothing to read. The pin move and the "check your email"
flow are **one change**, not two.

This is also why the upgrade cannot happen passively: `recommended_runtime_requirement` now
reports `>=2.0,<3.0`, so a pin of `>=1.11.0,<2.0` simply will not resolve to it. That is
correct semver and deliberate — it forces the coordination this section describes.

## 2. Operational actions, in order of consequence

### ⚠️ Purge or rotate anything holding pre-2.0.0 execution records

`POST /auth/register` and `POST /auth/login` passed `body.model_dump()` as the pipeline's
`input_payload`, which is **persisted on the ExecutionUnit**. Both bodies carry the
plaintext password, so **every registration and every login wrote the user's raw password
into the execution record**, where it was also visible to anything reading trace data.

**Pre-existing and unrelated to any feature request** — found while changing the register
route. Fixed in this release; both now pass non-secret fields only.

Treat retained execution records as containing plaintext credentials for any period before
2.0.0. This is the only item here that is not resolved simply by upgrading.

### Every session ends at deploy

Access tokens now carry and require a `purpose` claim. Tokens minted before the upgrade lack
it and are rejected, so all users log in again. Tokens expired after 24h regardless; this
brings that forward to the moment of upgrade. Expected, not a fault.

### Run migrations

Schema `2026-08-02`, Alembic `0014` (`users.is_verified`, `users.verified_at`). **Existing
accounts are backfilled to verified**, so the upgrade alone cannot lock anyone out.

### Check any pinned `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS`

Default moved `30000` → `60000` in 1.11.0, because the flow runner's session is held
idle-in-transaction for the whole of node execution while a nodus run may occupy 45s. **A
default change only helps deployments that do not pin the variable.** Your notes record
`120000`, which already clears it — so no action, but do not lower it below ~45s.

---

## 3. Auth surface — the full contract

| Route | | |
|---|---|---|
| `POST /auth/register` | **202**, no token | Verification mail on a new address; *"someone tried to register"* notice on an existing one. **Identical response either way** |
| `POST /auth/verify-email` | `{token}` → access token | Idempotent — a re-used link succeeds rather than erroring |
| `POST /auth/password/change` | Bearer only, `5/min` | Returns a re-versioned token — **store it** |
| `POST /auth/password/forgot` | **Always 200**; `503` if no email channel | `3/min` per IP **and** per email |
| `POST /auth/password/reset` | `{token, new_password}` | Returns **no** token — the caller has not proven they hold a session |

**Why register stopped returning a token.** The enumeration oracle could not be closed while
registration also authenticated the caller: a duplicate cannot be handed a token, so *some*
difference was unavoidable. Moving the token behind verification is what makes a uniform 202
possible. The distinction now lives in the mailbox, where the real owner is informed — and
learns their address is being probed.

**The 503 on `/forgot` is not an inconsistency.** It discloses a property of the
*deployment*, identical for every caller, and reveals nothing about any account — so the
uniform-response rule does not apply. A startup warning reports the same thing at boot.

**Password floor** (`MIN_PASSWORD_LENGTH`, 8) now applies to registration as well as change.
No stored password is invalidated and login is unaffected; only new registrations under the
length are rejected. **Check any seeding/fixture/smoke script that registers users
programmatically.**

## 4. Email delivery — hybrid, and one rule worth knowing

A registered `email` connector is used when one exists; otherwise runtime-owned SMTP
(`AINDY_SMTP_*`). Both go through the same `outbound.email` capability.

**A registered connector that *fails* does not fall back to SMTP.** The fallback exists for
**absence**, not failure — falling back would route mail somewhere you did not choose,
silently, precisely when the route you did choose is broken.

## 5. FR-7 — you can drop both workarounds

- **Declaring both policy keys** — the engine now reads `significance`/`base_score`, the
  keys the validator demands, with `default_significance` kept as a fallback.
- **Passing `trace_id`/`source_event_id` to force impact computation** — impact is now
  **floored by the declared policy significance**, so a domain that says a memory matters
  can cause it to be recalled.

Two details worth carrying into your policies:

- The floor uses the **policy-declared** significance, not the computed per-capture score.
  The computed score folds in `context["significance"]`, and forced system captures pass
  `1.0` for every one — scoring off it would have lifted exactly the noise this ranks below.
- It is a **floor, not a sum**. A well-connected failure (high downstream count) still
  outranks a fully-declared decision; a bare failure at 1.5 no longer does.

**On `execution.started`:** it stays in `AUTO_MEMORY_EVENT_TYPES`. Dropping it — your first
suggestion — would have silently undone an invariant RT-MEMTXN-LEAK-1 deliberately
preserved, so ordinary jobs keep emitting the loop-closure signal your own FR-3 adoption
depends on. There is a regression test asserting it, and it failed when that was tried.
Instead, **an explicit policy `min_significance` is now honoured for forced captures** — the
suppression lever you correctly said you did not have. A *missing* key still means force
wins, so nothing changes where no policy is written.

## 6. New settings

| | |
|---|---|
| `AINDY_SMTP_HOST/PORT/USER/PASSWORD/FROM/STARTTLS` | SMTP fallback. HOST **and** FROM both required to count as configured |
| `AINDY_PASSWORD_RESET_TTL_MINUTES` | 30 |
| `AINDY_PASSWORD_RESET_URL_TEMPLATE` | `{token}` substituted; empty sends the bare token |
| `AINDY_EMAIL_VERIFY_TTL_HOURS` | 48 |
| `AINDY_EMAIL_VERIFY_URL_TEMPLATE` | as above |
| `AINDY_REQUIRE_VERIFIED_LOGIN` | **default off** — the enumeration fix does not depend on it, and enabling it is a lockout risk. Turn on once your users are verified |
| `AINDY_MEMORY_RECALL_OWN_SESSION` | default off — DB-NODUS-BUDGET-1 root-cause fix, pending soak |

## 7. Still open on our side

- **FR-3 verb broadening** (`retry`, `schedule_follow_up`) — nobody has asked yet; say if you
  need it.
- **`/auth/register` returns 409 on a duplicate *email*… no longer** — closed by the 202. But
  note the related first-admin gap from your walk log (`admin/users/{id}/promote` has no UI
  path) is **still open** and unaddressed here.
- **Soak-and-flip** — the default-off flags remain yours to exercise; that is the phase
  you are in.
