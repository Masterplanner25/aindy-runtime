---
title: "App Handoff — Runtime v2.37.0"
api_version: "1.0"
last_verified: "2026-11-17"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.37.0

**Answers FR-70.** A new public auth dependency lets your directing route admit the person's watcher key. **No schema
step**: Alembic stays at `0022` and the schema contract at `2026-11-09`. No ui-kit or aindy-sdk release. No dependency
pin moved. Nothing changes for a route that doesn't use the new dependency.

Bump the `constraints.txt` pin to `==2.37.0` (your contract test moves the floor with it), **rebuild the image**
(`mcp` shares it, so recreate both), then make the one-line change in §1.

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; from AINDY.services.auth_service import get_activity_caller; print(v.__version__, get_activity_caller.__name__, list(AINDY.__path__))"
> # expect: 2.37.0 get_activity_caller ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 1. ★ FR-70: `get_activity_caller` (DEC-133, #978)

The owner chose your option 1, a public dependency. Option 2, an app-registrable exclusive scope, was declined for
now: it needs a registration API and minting for app scopes. It gets revisited if a second app needs a narrow key the
person should hold separately from their watcher key.

```python
from AINDY.services.auth_service import get_activity_caller

def _directing_caller(current_user: dict = Depends(get_activity_caller)) -> dict:
    return current_user
```

**What it takes:**

| Credential | Answer |
|---|---|
| A signed-in session (Bearer) | admitted |
| `X-Platform-Key` holding only `watcher.signals` (the key from `POST /watcher/keys`) | **admitted** (refused by every route that doesn't name this dependency, as before) |
| `X-Platform-Key` holding `watcher.signals` plus other scopes, or `platform.admin` | admitted |
| `X-Platform-Key` without `watcher.signals` or `platform.admin` | **403** (`API key scope 'watcher.signals' required.`), even though `get_current_user` takes it |
| The shared `X-API-Key` alone | **401**: it names no user, and your route must know whose activity it stores |
| A revoked or unknown key, no credential | 401 |

If a request carries both a platform key and a Bearer token, the key decides, the same as on `/watcher`.

**The principal has `get_current_user`'s shape.** `current_user["sub"]` is the user id, so `ingest_directing_events_route`
needs no other change. `auth_type` is `"api_key"` for a key.

**Use it instead of `get_current_user`, never beside it.** Don't add `enforce_api_key_scope(...)` to the same route:
that dependency resolves through `get_current_user`, which still refuses a watcher-only key, so the route would refuse
it again. A test pins this.

**Narrower than you had:** today `_directing_caller` takes any non-watcher platform key. After the swap, a key without
`watcher.signals` is refused there. If anything of yours posts to `/apps/directing/events` with such a key, give it a
key that holds `watcher.signals`, or use a session.

**FR-70 closes** when your route uses it and your phase-B hook posts with the person's watcher key: report the hook's
first 2xx with a watcher-only key, and a 403 from the same key on any other app route (the control).

## 2. Your register

- **FR-69: closed** on your 2.36.1 report (app #620/#621). The audit passed without the ignore.
- Still open from earlier handoffs, no new ask: the trace question in your 2.34.0 report (§5 there).
