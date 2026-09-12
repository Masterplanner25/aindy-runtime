### Fixed — a malformed id in a path is now a 422, not a 500 (FR-25 b) (#623)

- Six runtime routes answered **500** with the UUID parser's own message
  (`badly formed hexadecimal UUID string`) when the path id was not a UUID, because the
  parameter was declared `str` and the handler parsed it itself. They now answer **422** with
  the structured validation error, and the OpenAPI schema no longer advertises the parameter
  as free text:
  `POST /apps/coordination/agents/{agent_id}/heartbeat`, `DELETE /apps/coordination/agents/{agent_id}`,
  `GET /apps/coordination/runs/{parent_run_id}/children`, `GET|DELETE /platform/keys/{key_id}`,
  `POST /platform/admin/users/{user_id}/promote`.
- **The population was measured, not guessed:** every parameterised runtime-served route was
  called with a malformed id through the booted app, on SQLite and again on live Postgres. These
  six were the only 500s on either engine; the Postgres-only class (a raw string bound against a
  UUID column) turned out to be empty.
- New shared type `AINDY.routes.path_params.UUIDPath` — `Annotated[str, …]`, validated with the
  same `uuid.UUID(value)` call the handlers already made, so **no id a handler accepted before is
  rejected now** (the compact 32-hex form included) and handlers keep receiving a `str`.
- `POST /platform/admin/users/{user_id}/promote` also no longer 500s on a **well-formed unknown**
  id under SQLite (`ADMIN-PROMOTE-UUID-1`, flagged in ROUTE-GUARD-1): the handler now normalises
  the id before comparing it to the `UUID` column. Postgres behaviour is unchanged (404).

**Client note:** a client that matched on the 500 to detect a bad id (there is no evidence one
did) will now see 422. A well-formed id that matches nothing is still the route's own 404.
