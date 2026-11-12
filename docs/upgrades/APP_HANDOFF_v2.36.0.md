---
title: "App Handoff — Runtime v2.36.0"
api_version: "1.0"
last_verified: "2026-11-11"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.36.0

**Answers FR-67 and FR-68, and picks up nodus-lang 5.16.0. No schema step**: Alembic stays at `0022` and the schema
contract at `2026-11-09`. No ui-kit or aindy-sdk release.

Bump the `constraints.txt` pin to `==2.36.0`, **rebuild the image** (the `mcp` service shares it, so recreate both), and
run §1 once.

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v, importlib.metadata as m; print(v.__version__, m.version('nodus-lang'), list(AINDY.__path__))"
> # expect: 2.36.0 5.16.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 1. ★ FR-67: a node's path ends with its own id (DEC-131, #957)

- **What was wrong** is what you found. `memory.write`'s path resolver minted a UUID for the path's last segment and
  dropped it, and the row was stored under a different id. Reads by path worked. Taking the id from the path did not.
- **The fix is at the chokepoint.** `MemoryNodeDAO.save` now takes the row's id from the path's last segment, so no
  writer can drift again.
- **Your 81 nodes, and any written since 11-08.** `memory backfill-paths` now also rewrites each stored path that does
  not end with its node's id so that it does. **Ids never change; the old address stops resolving.** Anything that
  stored one of the 81 *paths* (not ids) will no longer find it there. Run it once, dry run first:

  ```bash
  docker exec <api-container> aindy-runtime memory backfill-paths
  # dry-run: 0 node(s) would get a stored path; N path(s) would be rewritten to end with their node's id
  #   (the old address stops resolving; ids do not change); left alone: … Re-run with --yes.

  docker exec <api-container> aindy-runtime memory backfill-paths --yes
  # ok: stored a path on 0 node(s) in … batch(es); rewrote N path(s) to end with their node's id; left alone: …
  ```

  Expect **N ≥ 81**: every node written through `memory.write` until you upgrade drifted the same way. A stored path
  that is not an address is reported as `stored path(s) are not addresses and were left alone`; tell us if that count is not 0. Running it again
  should print `ok: … every stored path ends with its node's id`.
- **Changed: `memory.write` refuses a `path` that names its own last segment** (`failure_class: invalid`). Pass
  `/memory/{you}/{namespace}/{type}`, or `namespace` and `addr_type`; the id is assigned. Before, a supplied last
  segment was stored as given under a row with another id. We found no caller of yours that passes one.

## 2. FR-68: the consent page behind your tunnel (DEC-132, #958)

No runtime setting was added, because uvicorn already has the one you asked for: `FORWARDED_ALLOW_IPS`. The server
`mcp-server --transport http` starts is plain `uvicorn.run`, which reads it. With the proxy trusted, uvicorn takes the
right-most `X-Forwarded-For` hop it does not trust as the client, and the consent page's limit keys on that. A forged
hop to its left buys nothing. Its default trusts only `127.0.0.1,::1`, which is why every request from your `mcp-tunnel`
container (`172.19.0.8`) shared one bucket.

**The fix is one line in `docker-compose.prod.yml`, on the `mcp` service** (not the api):

```yaml
      FORWARDED_ALLOW_IPS: "${FORWARDED_ALLOW_IPS:-172.19.0.0/16}"
```

- **Use your compose network's subnet, not the container's address.** The ngrok container's address can change on
  recreate. `docker network inspect <project>_default --format '{{(index .IPAM.Config 0).Subnet}}'` prints it; 172.19
  is what your FR reported.
- **Never `*`.** Port 8080 is published on the host's `127.0.0.1`, so a request made on the host arrives from the
  network's gateway (`172.19.0.1`), inside that subnet. That is the host itself, which is fine. `*` would let any
  caller that reaches the port name its own bucket.
- **Check it.** After recreating `mcp`, after ten wrong passwords in a minute from one client, its eleventh gets `429` while a sign-in
  from another network still gets the form. Or read `request.client.host` in the access log: it should be the
  caller's public address, not `172.19.0.8`.

**FR-68 closes when you have set it and report the check.**

## 3. nodus-lang 5.15.1 → 5.16.0 (#959)

Not a security release. Two changes reach you:

- **Column numbers in a Nodus script's errors and stack traces change.** An error raised by a call now points at what is
  called (`len` in `len(...)`), not its last argument. Lines are unchanged. Only matters if something parses them.
- **A Nodus rebuild that re-runs a top-level effect now warns** (`StagedFlipWarning`); nodus 6.0.0 will refuse it. The
  runtime never asks Nodus to rebuild a run, so this reaches only a script that calls `resume_workflow` itself. None
  of yours do, and none use `checkpoint`, whose rules also changed.

The rest of 5.16.0 (durable coroutines, a ~1.6x faster interpreter loop) is opt-in or invisible. You don't pin
`nodus-lang` yourselves, so the runtime's `==5.16.0` decides it.

## 4. Asks

1. Bump the pin to `==2.36.0`, rebuild, recreate `api` and `mcp`, and run the in-container check above.
2. Run `memory backfill-paths`, dry run then `--yes` (§1). **Report N** (we expect ≥ 81) and the not-addresses
   count. That closes FR-67.
3. Set `FORWARDED_ALLOW_IPS` on the `mcp` service to your compose subnet (§2) and report the check. That closes FR-68.

Still open from earlier handoffs, no new ask: the trace question in your 2.34.0 report (§5 there) gets its answer in a
later handoff.
