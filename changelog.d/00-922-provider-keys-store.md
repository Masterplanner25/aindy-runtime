### Added — per-user provider keys, phase 1: the encrypted store, its routes and the resolver (FR-59, #922)

- **Schema step: Alembic `0021` adds `user_provider_keys`** (schema contract `2026-11-08`). An upgraded stack takes the bootstrap-schema exit-3 path once. On a blank database the migration skips the table and `create_all` builds it.
- **`GET`, `PUT` and `DELETE /auth/provider-keys/{provider}`:** a signed-in user sets, replaces, deletes and lists their own keys for `openai`, `anthropic`, `deepseek`, `azure_openai` and `perplexity`. A list shows the last four characters, never the key, and no route returns a stored key. Session only: an API key gets 403.
- **Encrypted with AES-GCM, bound to the user and the provider.** The master key is the secret `provider_key_encryption_keys` (`AINDY_SECRET_PROVIDER_KEY_ENCRYPTION_KEYS` in the env backend). It's a comma-separated list where the first key seals and every key opens; `aindy-runtime provider-keys rotate` re-seals stored keys with the first. It is never derived from `SECRET_KEY`.
- **Nothing pays with a stored key yet.** `AINDY_PROVIDER_KEYS` defaults to `server`, and no model call resolves through the store until phase 2. With `per_user` set and no master key, startup refuses.
- **Action for operators:** none for upgrading. To prepare for `per_user`, set a master key first. Without one, `PUT` answers 503 and stores nothing.
