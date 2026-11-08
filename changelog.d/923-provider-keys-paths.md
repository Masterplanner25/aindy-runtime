### Added — per-user provider keys, phase 2: a user's own key pays for the planner (FR-59, #923)

- **`get_llm_client_for_user(provider, user_id)`** returns the client whose key should pay. With the server's key it is exactly today's client. With a user's own key it is a separate client with **its own circuit breaker**, so a user's revoked key can't open the breaker everyone else shares. Clients are cached by key fingerprint, never the key, in a bounded cache.
- **The agent planner pays with the run's user's own key** in `per_user` mode. A non-operator without a key gets `ProviderKeyRequired`, and the run fails with that message instead of spending the operator's key. In `server` mode the planner is unchanged.
- **A key the provider refuses (401) raises `ProviderKeyRejected`**, a subclass of `LLMCallError`, naming the provider and the last four characters. It is never retried on the server's key.
- **Whose key paid:** a new counter, `aindy_llm_key_source_total{provider,key_source}`, plus `key_source` on the planner's `external.call.*` events. No existing metric's labels change.
- **Audit:** `provider_key.set` and `provider_key.deleted` system events carry the provider and hint, never the key, and are retained as audit.
