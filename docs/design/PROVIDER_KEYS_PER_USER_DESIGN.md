---
title: "Per-User Provider Keys (FR-59) — Design"
api_version: "1.0"
last_verified: "2026-11-08"
status: current
owner: "platform-team"
---

# FR-59 — whose key pays for a model call — design

**ACCEPTED 2026-11-08 (DEC-121): the owner took every recommendation in §8 (D1 build now, D2 a dedicated master key
through `secret_broker`, D3 no check on set, D4 embeddings stay on the server's key, D5 the server's key for a call with
no user, D6 all five providers). Phase 1 built in #922: the store, the routes, the resolver and the rotate
command, inert under the default `server`. One change from §4: the `provider_key.set` / `.deleted` system events are
not in phase 1, because a new event type means a vocabulary and baseline change; they come with phase 2. Phase 2
built in #923: a client and breaker per user key (`get_llm_client_for_user`, `UserKeyLLMClient`), the planner paying
with the run's user's key, `ProviderKeyRejected` on a 401, the `provider_key.set` / `.deleted` events (retention
class audit), and whose key paid as a new counter, `aindy_llm_key_source_total{provider,key_source}`, plus
`key_source` on the planner's external-call events. A counter rather than a label on `aindy_llm_tokens_total`, so no
existing series changes. Guest Nodus model calls go through app syscalls, so routing them is the app's phase 3.** Originally a proposal under `AGENT_WORKING_RULES.md`
§8: it changes how a credential is resolved, stores user secrets, and needs a schema step.

## 1. The problem, measured in this repo

The app's request is `RUNTIME_FEATURE_REQUESTS.md` FR-59 in `aindy-apps-monolith`. Every paid call spends the operator's
key, whoever made it:

- **One key per provider, process-wide.** `config.Settings` holds `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`,
  `ANTHROPIC_API_KEY` and `AZURE_OPENAI_API_KEY`. Each client reads its key at construction (`openai_client.py:110`,
  `deepseek_client.py:98`, `anthropic_client.py:82`, `azure_openai_client.py:58`).
- **Clients are process singletons.** `get_openai_client()` caches one `CircuitBreakerLLMClient`, with one breaker per
  provider. `get_llm_client(provider)` and `get_llm_client_chain()` build on the same factories.
- **The runtime's own paid paths:**
  - the agent planner, `planning.py:250`, which knows the user and goes through `perform_external_call`;
  - embeddings: `embedding_providers.py` builds `get_openai_client()`, and `generate_embedding(text)` takes no user,
    because the sweep embeds every user's nodes together.
- **`secret_broker.py` is not this.** It resolves *process* secrets at call time, gated by capability. It has no secret
  that belongs to a user, and nothing in this proposal changes it.
- **Limits don't stop the spending.** Per-account limits (rate limits, the token cap, the app's $10 a month Resolution
  Check) mean N accounts get N allowances, all paid by one key. FR-57 (2.31.6) lets an operator close registration,
  which stops strangers. This stops anyone who is let in.

## 2. What gets built

Four pieces, all inert until `AINDY_PROVIDER_KEYS=per_user`:

1. **A per-user key store:** a table, a service, and routes a user manages their own keys through (§3, §4).
2. **One resolver:** `resolve_provider_key(provider, user_id)` decides whose key pays (§5).
3. **Clients built per key:** a client and circuit breaker per (provider, key), so one user's bad key can't trip the
   operator's breaker (§6).
4. **The runtime's own paid paths resolve for their user:** the planner, and embeddings depending on decision D4 (§7).

The app then routes its own call sites through the resolver.

## 3. Storage

**A new runtime table, `user_provider_keys`, and Alembic `0021`.** That is a schema step, so the app takes the exit-3
path once.

| Column | Notes |
|---|---|
| `id` | UUID primary key |
| `user_id` | FK `users.id`, `ON DELETE CASCADE`: deleting an account deletes its keys |
| `provider` | `openai`, `anthropic`, `deepseek`, `azure_openai` and `perplexity` (§D6); unique with `user_id` |
| `ciphertext` | the encrypted key |
| `key_hint` | the last four characters, for display only |
| `key_version` | which encryption key sealed it, for rotation |
| `created_at`, `updated_at` | |

**Encryption: AES-GCM through `cryptography`, which is already pinned (50.0.1).**

- **A dedicated master key:** `AINDY_PROVIDER_KEY_ENCRYPTION_KEYS`, an ordered list where the first entry seals and all
  of them open. It is fetched through `secret_broker`, so an operator can supply it from env, a Docker secret
  (`/run/secrets`) or Vault without new code.
- **Never derived from `SECRET_KEY`.** That key signs every JWT, rotates on SIGHUP, and a leak of it shouldn't also open
  every user's provider key.
- **Rotation:** add a new key in front, run `aindy-runtime provider-keys rotate` to re-seal every row, then drop the old
  key.
- **Fails closed:** with `per_user` set and no master key, the runtime refuses to start, rather than storing keys in the
  clear or silently falling back to `server`.
- **AAD:** the user id and provider, so a ciphertext copied to another row or provider won't open.

## 4. The API a user sees

Session-only, under `/auth/provider-keys`, like `/watcher/keys`:

| Route | Does |
|---|---|
| `GET /auth/provider-keys` | lists `{provider, key_hint, updated_at}`, never a key |
| `PUT /auth/provider-keys/{provider}` | sets or replaces it; body `{"key": "…"}`; answers like the list |
| `DELETE /auth/provider-keys/{provider}` | removes it |

- **No API-key access.** A leaked platform key shouldn't be able to swap whose account pays. This also mirrors DEC-117's
  "a key can't mint a key".
- **The value never enters the pipeline's records:**
  - `input_payload` records only `{provider}`;
  - no system event, log line or span carries it;
  - the system event `provider_key.set` / `provider_key.deleted` carries `provider` and `key_hint` only.
- **Read back never.** No route, syscall or MCP tool returns a stored key.

## 5. Resolution — the one place that decides

```python
resolve_provider_key(provider: str, user_id: str | None) -> ResolvedKey  # ResolvedKey(value, source="user"|"server", fingerprint)
```

| `AINDY_PROVIDER_KEYS` | Caller has a key | Caller is an operator | Result |
|---|---|---|---|
| `server` (default) | — | — | the server's key, exactly as today |
| `per_user` | yes | — | the caller's key |
| `per_user` | no | yes | the server's key |
| `per_user` | no | no | raises `ProviderKeyRequired(provider)` |
| `per_user` | — | — (no user: a system job) | the server's key; see §D5 |

- **Operator:** `User.is_admin` read at resolve time and cached for 60 seconds, as webhook delivery does (DEC-116), so a
  demotion takes effect on its own.
- **No silent fallback.** A user's key that the provider rejects raises `ProviderKeyRejected`. It never retries on the
  server's key, because that is the spending this exists to stop. For a non-operator, the provider failover chain
  (`LLM_FALLBACK_PROVIDERS`) skips any provider the caller has no key for, rather than reaching for the server's.
- **Errors carry no key**, only the provider and the hint.

## 6. Clients and breakers

- **The cache:** `get_llm_client(provider, *, user_id)` returns a client for the resolved key from a bounded LRU keyed
  by (provider, key fingerprint), say 256 entries. The fingerprint is a SHA-256 of the key, never the key itself.
- **The breaker follows the key.** One user's revoked key tripping the shared OpenAI breaker would cut off the operator
  and every other user. The server's key keeps today's breaker, so `server` mode is unchanged.
- **Today's entry points stay.** `get_openai_client()` with no user still returns the server-keyed singleton, so every
  existing caller behaves as before until it opts in.

## 7. The runtime's own paid paths

- **The planner** passes `user_id` through, which it already has.
- **Guest Nodus LLM calls (FR-35)** run on the host side, which knows the run's user. The guest never receives a key.
- **Embeddings** depend on §D4.
- **The token meter** already records `user_id`. It gains `key_source` (`user` / `server`), so an operator can see whose
  money each call spent.

## 8. Decisions for the owner

| # | Question | Options | Recommendation |
|---|---|---|---|
| **D1** | Build it now, given the schema step? | (a) yes, phased as in §9; (b) wait until registration is open to strangers | **(a)**. It's the right model even with signup closed, and phase 1 is inert. |
| **D2** | Where does the master key live? | (a) a dedicated env/secret, fetched through `secret_broker`; (b) derived from `SECRET_KEY` | **(a)**. `SECRET_KEY` signs sessions and rotates on its own schedule. |
| **D3** | Check a key when it's set? | (a) no, store it, and the first real call finds out; (b) a cheap provider call on set | **(a)**. A check costs money on every set, and a key can be revoked later anyway. The first failing call raises `ProviderKeyRejected`. |
| **D4** | Whose key pays for embeddings? | (a) the node owner's, so a non-operator without a key gets no embeddings and no semantic recall; (b) always the server's, bounded by the existing per-tenant caps; (c) (a), with a per-deployment switch | **(b) for now.** Embeddings are cheap, and making semantic recall depend on a key is a product change the app hasn't asked for. Revisit with token-meter data from `key_source`. |
| **D5** | A call with no user, such as a system job? | (a) server's key; (b) refuse | **(a)**. It's the operator's own work. |
| **D6** | Which providers? | the four the runtime has clients for, plus `perplexity`, which only the app calls | **All five in the store**, with resolution generic over the name, so the app can route Perplexity too. |

## 9. Phases

1. **Store and API, inert:** the table, encryption, routes, the resolver and the `ProviderKeyRequired` /
   `ProviderKeyRejected` types. `AINDY_PROVIDER_KEYS` defaults to `server`. This ships Alembic 0021.
2. **Runtime paths:** per-key clients and breakers, the planner, `key_source` on the meter, and embeddings per D4.
3. **The app:** it routes its ~25 call sites through the resolver and adds a Settings card for the write-only API.
4. **The flip:** the app sets `per_user`, with the operator still on the server's keys.

Each phase is its own PR with tests that call the real routes and resolve real keys. The encryption tests prove a
ciphertext moved to another row or provider won't open.

## 10. Not in this design

- **Per-user spending limits on the server's key:** an operator still pays for itself and for system jobs, bounded by
  today's caps.
- **Organisation or team keys:** one key per user per provider.
- **Using a user's key for another user's work,** including delegation. A delegated run resolves for the run's own user.
