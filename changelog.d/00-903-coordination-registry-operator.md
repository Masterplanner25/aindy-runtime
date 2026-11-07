### Changed — the agent registry's routes now need an operator (DEC-114, #903)

- **Read before upgrading if anything calls `/apps/coordination/agents*` with a non-admin login.** `GET /agents`, `GET /agents/status`, `POST /agents/register`, `POST /agents/{id}/heartbeat` and `DELETE /agents/{id}` now answer 403 unless the caller is an admin session or holds a `platform.admin` key.
- The registry has no owner, so any signed-in user could read every agent's state, change any agent, or add one that delegation would rank.
- `GET /coordination/graph` stays open. A non-operator now sees only the agents in their own events.
- The other coordination routes are unchanged, and so are the runtime's own self-registration and delegation ranking.
