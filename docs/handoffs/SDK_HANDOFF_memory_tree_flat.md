---
title: "aindy-sdk — memory.tree docstring promises a key the runtime never returns"
last_verified: "2026-09-13"
api_version: "1.0"
status: current
owner: "platform-team"
---
# aindy-sdk — `memory.tree` promises `data["flat"]`; the runtime returns `tree` + `node_count`

Written from `aindy-runtime` for whoever is working in `aindy-sdk` (`C:\dev\aindy-sdk`,
`aindy_sdk/memory.py`). Found 2026-09-13 while checking the runtime tutorials against source.

## The mismatch

`MemoryAPI.tree()`'s docstring:

> Returns: Syscall envelope. ``result["data"]["tree"]`` is the nested dict;
> ``result["data"]["flat"]`` is the depth-first ordered list.

`sys.v1.memory.tree`'s handler (`AINDY/kernel/syscall_registry.py:612`) returns
`{"tree": ..., "node_count": N, "path": ...}` and its output schema requires exactly `tree` and
`node_count`. There is no `flat`, and there never has been on the runtime side — `flatten_tree`
exists in `memory_address_space.py` but no handler calls it. The runtime's own Tutorial 1 read
`data["flat"]` and raised `KeyError`; the docstring is where it got the idea.

## Ask

Either delete the `flat` sentence, or have `tree()` flatten client-side (the shape is
`{path: {"node": …, "children": [...]}}`, so a depth-first walk is ten lines) and document that
it is computed in the SDK, not returned by the runtime. The runtime will not add `flat` to a
stable syscall's output for this — the recursive read (`memory.read(path="/memory/<t>/**")`)
already answers "give me the nodes".

## Two smaller things seen in passing

- `MemoryAPI.write()` sends `extra`; the v1 schema does not declare it. The runtime **merges**
  it (`ROUTE-EFFECT-BYPASS-1`) and does not reject unknown keys, so it works — but it works by
  the absence of `additionalProperties: false`, which is not a promise. Worth a docstring note.
- The tutorials in this repo now derive the tenant from the JWT `sub` client-side because
  there is no whoami route. If the SDK wants to expose `client.tenant_id`, that is where it
  would come from; a platform API key has no equivalent, which is a runtime gap
  (`INITIATOR-IDENTITY-1` is adjacent, not the same).

## Status

| Ask | State |
|---|---|
| `tree()` docstring / client-side flatten | open |
| `extra` docstring note | open |
