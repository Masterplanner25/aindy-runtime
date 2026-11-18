### Added — `get_activity_caller`: an app route can admit the user's watcher key (FR-70, DEC-133) (#978)

- **New public auth dependency, `AINDY.services.auth_service.get_activity_caller`,** for an app route that ingests the
  caller's own activity. It takes a signed-in session, or an `X-Platform-Key` holding `watcher.signals` or
  `platform.admin`, including the watcher-only key a user mints at `POST /watcher/keys`. Before, that key was refused
  on every route except `/watcher`, and app code had no public way to opt in. An unattended client on a user's
  machine therefore needed an ordinary platform key, which reaches almost every app route from a file on disk.
- The watcher-only key is still refused everywhere else (DEC-117). Its reach grows by exactly the routes that name
  this dependency.
- **Narrower than a session:** a key without one of those scopes is refused there, even one `get_current_user` would
  take. It never takes the shared `X-API-Key`: the caller is always a user. The principal has `get_current_user`'s
  shape (`sub` is the user id).
- Use it **instead of** `get_current_user`, not beside it: `enforce_api_key_scope` resolves through
  `get_current_user`, so a route that adds one refuses the watcher key again.
- The `/watcher` routes now resolve keys through the same rule; their behaviour is unchanged.
