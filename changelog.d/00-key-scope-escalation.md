### Fixed — an API key could mint itself a wider API key (`KEY-SCOPE-ESCALATION-1`, #463)

**Security. Operators should read this and audit existing keys before upgrading.**

`POST /platform/keys` validated only that each requested scope *exists* (membership in
`Scopes.ALL`), never that the caller was entitled to grant it. Demonstrated end to end against
real PostgreSQL, starting from an API key holding the single scope `flow.read`:

1. `POST /platform/keys {"scopes": ["platform.admin","memory.delete","event.emit"]}` → **201**,
   key issued with exactly those scopes
2. `GET /platform/admin/users` with the new key → **200**, every user's email and admin flag
3. `POST /platform/admin/users/{own_id}/promote` → **200**, `is_admin: true`

Step 3 lands in the **user row**, so revoking the minted key does not undo the escalation, and
every subsequent JWT session for that account is an admin session.

Nothing upstream would have stopped it: `require_platform_admin_access` admits **any**
authenticated API key to the whole `/platform` tree, on the stated assumption that *"scope
enforcement happens per-endpoint or per-syscall"* — which `keys_router` did not do.

**The fix is a delegation rule: you cannot grant what you do not hold.** A new
`grantable_scopes(principal)` bounds key creation by the creator's own authority — an API key's
own scopes, or a session's derived scopes. A request naming any scope outside that set is
refused with **403 `scope_not_grantable`**, listing only the scopes that were not grantable. An
unknown scope still returns **422**, because *"that is not a scope"* and *"you may not grant that
scope"* are different failures.

A holder of `platform.admin` may still grant anything. That is deliberate, not a loophole:
`platform.admin` already satisfies every scope gate and reaches user promotion, and it preserves
the documented affordance that a key *can* carry `memory.delete`/`event.emit`, which no session
inherits.

**What this does not change:** no existing key loses any access it already had — the rule only
governs what a key can *grant*. **What it does not close:** `require_platform_admin_access`
admitting any API key to 56 `/platform/*` routes is the broader hole and is tracked separately;
this removes the escalation ladder, not the reach.

**Audit advice:** any key carrying `platform.admin`, `memory.delete` or `event.emit` that you did
not deliberately issue should be revoked, and `users.is_admin` reviewed for accounts you did not
promote yourself.

**★ Why no test caught this, and the trap for whoever tests it next:** `platform_api_keys.scopes`
is a PostgreSQL `ARRAY`. On SQLite the insert fails at the driver (`type 'list' is not
supported`) **after** the authorization gate has been passed, so the harness turns a 201 into a
500 and the finding reads as an unrelated bug.
