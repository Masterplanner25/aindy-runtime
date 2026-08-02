---
title: "FR-6 Password Recovery — build scope"
api_version: "1.0"
last_verified: "2026-08-02"
status: current
owner: "platform-team"
---
# FR-6 Password Recovery — build scope

Scoping for FR-6 items 2+3 (`/auth/password/forgot`, `/auth/password/reset`) and the
`/auth/register` enumeration fix that shares their machinery. Item 1
(`/auth/password/change`) shipped in v1.11.0.

**Everything except the build is already decided** — see `TECH_DEBT.md` →
`DECISIONS-2026-08-01`. Delivery is hybrid; the token is stateless, carrying `user_id` +
`token_version`; TTL 30–60 min; `/forgot` always returns 200; rate limit 3/min per IP and
per email. This document turns those decisions into a build, and records the two hazards
found while scoping.

---

## ⚠️ Hazard 1 — a reset token would be a valid access token

**This is the finding that shapes the whole design.** Verified in source, not assumed:

- `decode_access_token` (`auth_service.py`) accepts **any** HS256 token that verifies
  against a `KeyRing` secret. It checks the signature and nothing else.
- **No `purpose` / `typ` claim is checked anywhere** in the auth path — `grep` returns
  nothing.
- `_resolve_authenticated_jwt_user` then accepts the token if `sub` resolves to a user and
  `tv` matches `user.token_version`.

The obvious implementation of the agreed token design — mint a JWT carrying `user_id` and
`token_version`, signed with the usual key — therefore produces a **fully valid bearer
access token for that user**. A password-reset link emailed to a victim would be a
ready-made session credential, and anything that leaks it (mail relay logs, a forwarded
message, browser history, a referrer header) is a silent account takeover. It would also
completely bypass the reset step: the holder could just use it as a login.

**Mitigation — domain separation, not just a claim.** Derive the reset-signing key from the
active signing key:

```python
reset_key = hmac.new(signing_key().encode(), b"aindy-password-reset-v1", hashlib.sha256).hexdigest()
```

A reset token then simply does not verify against the access key, and an access token does
not verify as a reset token. This composes with `KeyRing` rotation for free, because it
derives from the active key and the grace-window key list.

**Belt-and-braces on top:** include `"purpose": "password_reset"` in the claims and require
it on the reset path. The claim alone is *not* sufficient — it would be checked only by the
new code, while `decode_access_token` (used by every existing authenticated route) would
still accept the token. Domain separation is what makes it safe; the claim is defence in
depth.

**Recommended follow-up, out of scope here:** have `decode_access_token` require
`purpose == "access"` and start minting that claim on login. That closes the general class
rather than this one instance — but it is a token-format change with a rollout ordering
problem (verify-tolerant first, then mint, then require), so it is its own piece of work.

## ⚠️ Hazard 2 — the timing side channel

`/forgot` returning 200 unconditionally is necessary but not sufficient. If the "email
exists" path sends mail and hashes, while the "unknown email" path returns immediately, the
response time discloses the answer just as loudly as a status code would.

**Requirement:** both paths must do comparable work. Send the mail via the same deferred
path in both cases (a real mail on hit; nothing, or a suppressed no-op, on miss) and do not
let the miss path short-circuit before the equivalent work. The same applies to
`/auth/register`, where the duplicate path currently returns *before* `hash_password` and is
measurably faster (already recorded under the decision-3 entry).

---

## Phases

Deliberately three, because Phase C carries a schema change and a breaking response
contract while A and B do not. A and B together deliver the FR-6 ask.

### Phase A — the email channel (hybrid)

The decided shape: dispatch a registered `email` connector when one exists, otherwise fall
back to runtime-owned SMTP config. This is the piece that makes "the runtime sends it"
true, and it is independently useful and testable.

- New `AINDY/platform_layer/email_channel.py` — `send_email(to, subject, body, *, db, user_id)`.
- Resolution order: `get_connector("email")` → `dispatch_connector("email", action, …)`
  (signature verified: `dispatch_connector(connector_type, action, *, user_id, db, metadata)`,
  returns a normalized `{success, result, error}` envelope and never raises); else the
  built-in SMTP sender.
- Built-in sender goes through `authorized_external_call(service_name=…, capability=…,
  operation=…, …)` so egress policy, capability enforcement, and secret-brokering apply to
  the runtime's own mail exactly as they do to a connector's.
- Capability `outbound.email`. Config `AINDY_SMTP_HOST/PORT/USER/PASSWORD/FROM/STARTTLS`,
  password resolved through `SecretBroker` rather than read from env directly where
  available.
- **Fail-closed and explicit:** if neither a connector nor SMTP config exists, `send_email`
  reports unavailable. Phase B uses that to decide whether `/forgot` is enabled at all — see
  the open question below.
- Tests: connector-present path, SMTP-fallback path, neither-configured path, and that a
  connector failure does **not** silently fall back to SMTP (a registered connector failing
  is an error, not a reason to route mail somewhere the operator did not intend).

### Phase B — forgot / reset

- `POST /auth/password/forgot` — always 200, rate limited 3/min per IP **and** per email.
  On a hit, mints a domain-separated reset token (Hazard 1) with `sub`, `tv`, `purpose`,
  `exp` (TTL 30–60 min) and sends it. On a miss, does equivalent work and sends nothing.
- `POST /auth/password/reset` — verifies against the derived key, requires
  `purpose == "password_reset"`, checks `tv` still matches `user.token_version`, applies
  `MIN_PASSWORD_LENGTH`, rehashes, bumps `token_version`.
- **Single-use falls out**: the bump invalidates the token that authorised the reset. Replay
  fails on the `tv` comparison with no revocation list and no table.
- **No schema change.** Stateless token, `token_version` already exists.
- Reuses `change_user_password`'s validation shape so the three password-setting paths stay
  consistent.
- Tests: happy path, expired token, replayed token, token minted for another user, a reset
  token rejected by `get_current_user` (**the Hazard 1 regression test — the important
  one**), unknown-email 200, rate limiting, and timing-comparability of hit vs miss.

### Phase C — email verification + register enumeration fix ⚠️ breaking + schema

Only this phase closes decision 3, and only this phase is expensive.

- `User.is_verified` (+ `verified_at`) → **schema contract bump + Alembic migration**, and
  the three-step protocol in `CLAUDE.md` (bump `SCHEMA_CONTRACT_VERSION`, regenerate the
  baseline, update the two assertions in `test_runtime_schema_contract.py`).
- `POST /auth/register` returns a neutral **202 with no token**; a verification mail is sent
  on a new email, and a *"someone tried to register with your address"* mail on a duplicate.
  Identical response either way — that is what actually closes the oracle.
- New verify endpoint consuming a domain-separated verification token; issues the access
  token only then.
- **Breaking twice over**: register stops returning a token, and stops returning 409. Both
  land in the 2.0.0 window that `main` is already committed to.
- Requires an app-side UI change (the app repo wires "check your email" instead of
  auto-login), so it needs coordination, not just a merge.

---

## Open questions for the owner

1. **What should `/forgot` do when no email channel is configured?** Fail-closed (503, honest
   but tells an attacker the deployment cannot reset) or still return 200 (uniform, but
   silently drops a real user's request)? **Recommend 503 at the route level plus a startup
   warning**, so a misconfigured deployment is loud to the operator rather than quiet to the
   user.
2. **Is Phase C in scope now, or later?** B closes the FR-6 ask and the app-side "Forgot
   password?" flow. C additionally closes decision 3 but costs a migration, a breaking
   register contract, and app coordination. They can ship separately — B does not depend on C.
3. **Should `decode_access_token` require `purpose == "access"`?** The general fix for
   Hazard 1's class. Recommended, but it is a token-format rollout with ordering constraints
   and belongs in its own change.

## Not in scope

Password strength beyond length; account lockout; MFA; session listing/revocation UI;
`admin/users/{id}/promote` UI (the sibling first-admin gap from the app team's walk log).
