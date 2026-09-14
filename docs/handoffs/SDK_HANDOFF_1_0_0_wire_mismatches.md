---
title: "aindy-sdk 1.0.0 — four wire mismatches against the runtime, found by running the tutorials"
last_verified: "2026-09-13"
api_version: "1.0"
status: current
owner: "platform-team"
---
# aindy-sdk 1.0.0 — wire mismatches against `aindy-runtime` 2.13.0

Written from `aindy-runtime` for whoever is working in `aindy-sdk` (`C:\dev\aindy-sdk`). Found
2026-09-13: first by reading the SDK source against the syscall registry, then by running the
three runtime tutorials against a live server with the **published** `aindy-sdk==1.0.0`. Two of
the four were only visible live. None is fixed at the SDK repo's HEAD (`94b5a44`) either.

| # | Method | SDK sends | Runtime requires | Effect on 1.0.0 |
|---|---|---|---|---|
| 1 | `client.events.emit(event_type, payload)` (`events.py:62`) | `{"type": …}` | `sys.v1.event.emit` requires `event_type` | **422 on every call.** Has never worked against any runtime release. |
| 2 | `client.nodus.upload_script(name, source, overwrite)` (`nodus.py:~110`) | `{"name", "source", "overwrite"}` | `POST /platform/nodus/upload` body `NodusScriptUpload` requires `content` | **422 on every call.** |
| 3 | `client.memory.tree(path)` docstring | promises `data["flat"]` | `sys.v1.memory.tree` returns `{"tree", "node_count", "path"}`; no handler calls `flatten_tree` | `KeyError` for anyone who trusts the docstring — the runtime's own Tutorial 1 did. |
| 4 | `client.flow.run(name, input)` | PyPI 1.0.0 sends `input` | the syscall reads `initial_state` | Fixed in the SDK repo (#3, `94b5a44`) but **1.0.0 on PyPI predates it** — a release is owed. |

The runtime tutorials now use `client.syscalls.call("sys.v1.event.emit", {...})` and
`client.post("/platform/nodus/upload", {...})` in place of 1 and 2, and say why inline. They will
switch back when a release fixes them.

## Two smaller things seen in passing

- `MemoryAPI.write()` sends `extra`; the v1 schema does not declare it. The runtime **merges**
  it (`ROUTE-EFFECT-BYPASS-1`) and does not reject unknown keys, so it works — but by the absence
  of `additionalProperties: false`, which is not a promise. Worth a docstring note.
- There is no whoami route, and memory paths are `/memory/{tenant}/…` with the tenant equal to
  the user id. The tutorials decode the JWT `sub` client-side. A `client.tenant_id` property
  would be the SDK-shaped answer for JWTs; a platform API key has no equivalent (runtime gap,
  adjacent to `INITIATOR-IDENTITY-1`).

## Status

| Ask | State |
|---|---|
| 1 — `events.emit` key | open |
| 2 — `upload_script` key | open |
| 3 — `tree()` docstring / client-side flatten | open |
| 4 — release with the `initial_state` fix | open |
| `extra` docstring note | open |
