---
title: "App Handoff — Runtime v2.32.0"
api_version: "1.0"
last_verified: "2026-11-08"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.32.0

**Answers FR-59 in the runtime: users can store their own provider keys, and a user's own key can pay for the runtime's
model calls.** Phases 1 and 2 of `docs/design/PROVIDER_KEYS_PER_USER_DESIGN.md` (DEC-121). **It's off by default:** with
`AINDY_PROVIDER_KEYS` unset, every call still pays with the server's keys, exactly as today. Phase 3 is yours (§5).

Bump the `constraints.txt` pin to `==2.32.0` (your contract test moves the floor with it), then **rebuild the image**. No
ui-kit release: `@aindy/ui-kit` 2.1.1 stays current.

**You are on 2.31.5 at last report, so this also carries 2.31.6.** Go straight to 2.32.0, and do the asks in
[`APP_HANDOFF_v2.31.6.md`](./APP_HANDOFF_v2.31.6.md) too (the memory path backfill, `AINDY_REGISTRATION`, your trace
helpers' `user_id`).

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.32.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. ★ Schema step: Alembic `0021`, a new table

The runtime Alembic head moves **`0020` → `0021`**, and the schema contract moves **`2026-09-20` → `2026-11-08`**. It
adds one table, `user_provider_keys`. Nothing existing changes.

**This is what CI observed on real Postgres, upgrading a database built by 2.31.6** (the Upgrade Path Guard, run
`42452678653`):

```text
$ aindy-runtime bootstrap-schema
error: runtime-owned schema is not ready: ... Missing required runtime table 'user_provider_keys'.
state=upgrade_required operator_action=startup_reconcile exit_code=3

$ aindy-runtime bootstrap-schema --reconcile
ok: reconciled runtime-owned tables to packaged metadata.
ok: stamped alembic_version_runtime from revision 0020 to 0021 (the schema report matched the packaged models).

$ aindy-runtime bootstrap-schema
ok: stamped alembic_version_runtime to revision 0021.
```

So your entrypoint, which branches on exit 3, stops on the first boot unless `AINDY_BOOTSTRAP_RECONCILE` is on. Then run
`docker exec <api> aindy-runtime bootstrap-schema --reconcile` once and start again. It only adds a table. Confirm
afterwards that `alembic_version_runtime` reads `0021`.

## 1. Users can store their own provider keys (#922)

- **Routes:** a signed-in session can set, replace, delete and list its own keys at `/auth/provider-keys`, for `openai`,
  `anthropic`, `deepseek`, `azure_openai` and `perplexity`.
  - `PUT /auth/provider-keys/{provider}` takes `{"key": "..."}`.
  - A listing shows `{provider, key_hint, updated_at}`. The hint is the last four characters.
  - **No route ever returns a stored key.**
  - An API key gets 403, so a leaked key can't swap whose account pays.
- **Encryption:** AES-GCM, bound to the user and the provider. The master key is the secret
  `provider_key_encryption_keys`, which is `AINDY_SECRET_PROVIDER_KEY_ENCRYPTION_KEYS` in the env backend. It's a
  comma-separated list where the first key seals and every key opens. `aindy-runtime provider-keys rotate --yes`
  re-seals stored keys after you put a new key first. It is never derived from `SECRET_KEY`.
- **No master key set:** `PUT` answers 503 and stores nothing. Generate one with
  `python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('='))"`.
- **Audit:** setting or deleting a key writes a `provider_key.set` or `provider_key.deleted` event, with the provider
  and hint only. Both are retained as audit.

## 2. Whose key pays (#922, #923)

`AINDY_PROVIDER_KEYS` is `server` (the default) or `per_user`.

| Mode | Caller | Pays |
|---|---|---|
| `server` | anyone | the server's key, as today |
| `per_user` | has their own key | their key |
| `per_user` | an operator without one | the server's key |
| `per_user` | a call with no user | the server's key |
| `per_user` | anyone else | nobody: `ProviderKeyRequired`, before any call is made |

- **Requires a master key:** `per_user` without one makes startup refuse.
- **A rejected key:** if the provider refuses a user's key (401), the caller gets `ProviderKeyRejected`, a subclass of
  `LLMCallError` with the provider and hint. It is **never retried on the server's key**.

## 3. The runtime's own paid call, the agent planner (#923)

The planner now pays with the run's user's key under `per_user`. In `server` mode it's unchanged. Each user key gets its
own client and **its own circuit breaker**, so one user's revoked key can't open the breaker everyone else shares.
Embeddings stay on the server's key (DEC-121 D4).

**Whose key paid is now measured:** `aindy_llm_key_source_total{provider,key_source}`, and `key_source` on the planner's
`external.call.*` events. No existing metric changes.

## 4. What does not change on upgrade

Everything, as long as you don't set `AINDY_PROVIDER_KEYS=per_user`. The routes exist, but nothing pays with a stored key
in `server` mode.

## 5. Phase 3 is yours

These are what FR-59 said you would do with it:

1. **Route your ~25 paid call sites through it.** Use `AINDY.platform_layer.llm_client.get_llm_client_for_user(provider,
   user_id)` for a provider the runtime has a client for. For anything else, including Perplexity, use
   `AINDY.platform_layer.provider_keys.resolve_provider_key(provider, user_id)`, which returns `.value`, `.source` and
   `.key_hint`.
2. **Handle the two errors.** On `ProviderKeyRequired`, say *"add your key in Settings"*. On `ProviderKeyRejected`, say
   *"your key was refused"*. Never fall back to the server's key.
3. **Add a Settings card** on the write-only API (§1).
4. **Set a master key, then `AINDY_PROVIDER_KEYS=per_user`.** The operator stays on the server's keys.

## 6. Asks

1. Bump the pin to `==2.32.0`, rebuild, run `bootstrap-schema --reconcile` once (§0), and run the in-container check.
   Report that `alembic_version_runtime` reads `0021`.
2. Do the 2.31.6 asks against this version.
3. Run your full test suite on it, and report any unexpected 401, 403 or 503.
4. Tell us when phase 3 is on a branch. We'd like to read your call-site routing before you flip `per_user`.

Report in your `RUNTIME_2_32_0_UPGRADE.md`.
