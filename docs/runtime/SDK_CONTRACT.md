---
title: "Runtime → SDK Contract"
last_verified: "2026-08-22"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Runtime → SDK Contract

Defines what `aindy-sdk` can rely on from `aindy-runtime` across releases.

See `CROSS_REPO_COMPATIBILITY.md` for the breaking-change policy and release gate
requirements.  See `PUBLIC_RUNTIME_SURFACES.md` for the full stability index.

---

## Version Contract

`GET /api/version` returns a JSON envelope. The SDK uses this for version negotiation
and boot-mode detection.

**Stable fields:**

| Field | Type | Notes |
|---|---|---|
| `data.version` | string | Package version (semver) |
| `data.api_version` | string | API major.minor |
| `data.compatibility.breaking_change_policy` | string | Non-empty; describes the policy |
| `data.compatibility.min_client_version` | string | Minimum SDK-compatible runtime version |
| `data.compatibility.runtime_package.name` | string | Always `"aindy-runtime"` |
| `data.compatibility.runtime_package.version` | string | Same as `data.version` |
| `data.system.runtime.boot_mode` | string | `"runtime-only"` or `"full"` |

**Compatibility window for SDK:** `aindy-runtime >= 1.0, < 2.0`

---

## Authentication Contract

`POST /auth/login` accepts `{"username": ..., "password": ...}` and returns:

```json
{"status": "ok", "data": {"access_token": "<jwt>", "is_admin": false, ...}}
```

The SDK unwraps the envelope and passes `access_token` as a Bearer token on
subsequent requests. **The `access_token` field must always be present in the
unwrapped data.**

`POST /auth/register` follows the same envelope shape.

`POST /auth/password/change` (Bearer JWT required; `{"current_password", "new_password"}`)
returns the **same** `{access_token, token_type}` payload inside that envelope. The change
invalidates every prior session, so a client must replace its stored token with the returned
one or the next request will 401.

---

## Watcher Endpoint Contract

The watcher client (`aindy_sdk/watcher/signal_emitter.py`) calls:

| Method | Path | Auth | Notes |
|---|---|---|---|
| `POST` | `/watcher/signals` | `X-API-Key` header | Batched signal ingestion |
| `GET` | `/watcher/signals` | `X-API-Key` header | Paginated signal query |

**Stability: stable.** These paths must not change without a major version bump.
The watcher client hardcodes these paths.

**Request shape for POST:**
```json
{
  "signals": [
    {
      "signal_type": "<string>",
      "user_id": "<uuid-string>",
      "metadata": {}
    }
  ]
}
```

---

## Memory API Contract

The SDK reads and writes memory nodes via:

| Method | Path | Stability |
|---|---|---|
| `GET` | `/apps/memory/nodes` | conditionally stable |
| `POST` | `/apps/memory/nodes` | conditionally stable |
| `GET` | `/apps/memory/nodes/{id}` | conditionally stable |
| `GET` | `/apps/memory/search` | conditionally stable |

All endpoints return the runtime standard envelope: `{"status": "ok", "data": {...}}`.
The SDK must call `.then(unwrapEnvelope)` or equivalent to extract `data`.

---

## Syscall Contract

The SDK may dispatch syscalls via `POST /platform/syscall` or via the flow engine.
The following syscalls are stable and must not be renamed or removed before the
next major version:

| Syscall Name | Capability Required |
|---|---|
| `sys.v1.memory.read` | `memory.read` |
| `sys.v1.memory.write` | `memory.write` |
| `sys.v1.memory.delete` | `memory.delete` |
| `sys.v1.memory.search` | `memory.read` |
| `sys.v1.memory.tree` | `memory.read` |
| `sys.v1.memory.trace` | `memory.read` |
| `sys.v1.flow.run` | `flow.run` |
| `sys.v1.flow.execute_intent` | `flow.execute` |
| `sys.v1.event.emit` | `event.emit` |
| `sys.v1.nodus.execute` | `nodus.execute` |
| `sys.v1.job.submit` | `job.submit` |
| `sys.v1.agent.execute` | `agent.execute` |
| `sys.v1.observability.support_metrics` | `execution.read` |

Verified against the registry 2026-08-05. Three corrections, all of which would have
misled an integrator:

- **`sys.v1.flow.execute_intent` requires `flow.execute`, not `flow.run`.** The table said
  `flow.run`, so a caller granted exactly what the doc specified would have been denied.
- **`sys.v1.memory.delete` and `sys.v1.agent.execute` were missing.** Both are in the
  enforced stable set, so both carry the same no-rename-before-major guarantee — an
  integrator had no way to know that from this document.
- **`sys.v1.execution.get` was listed here but is not in the enforced stable set.** It is a
  real, registered syscall (capability `execution.read`) and remains documented in
  `SYSCALL_REFERENCE.md`; it simply does not carry the stability guarantee this table
  confers. Listing it here promised something CI does not enforce, which is the worse
  direction for a contract to be wrong in.

**The authoritative list is `_STABLE_SYSCALLS` in
`tests/unit/test_cross_repo_compatibility.py`** — it is CI-enforced, so it cannot drift from
the registry silently. This table is a human-readable copy of it; when they disagree, the
test wins. Keep them in step when adding a stable syscall.

Experimental syscalls (`stable=False`) may change between minor releases.

### Capability grant through `POST /platform/syscall`

The dispatch route grants **exactly the requested syscall's own required
capability** (least-privilege, one per dispatch). JWT (`/auth/login`) callers
receive it without a scope check. Platform-API-key callers must additionally
carry an authorizing scope (or `platform.admin`):

| Capability | Backing SDK call | API-key scope |
|---|---|---|
| `memory.read` | `client.memory.read/search/list/tree/trace` | `memory.read` or `memory.write` |
| `memory.write` | `client.memory.write` | `memory.write` |
| `flow.run` | `client.flow.run` | `flow.execute` |
| `event.emit` | `client.events.emit` | `event.emit` |
| `execution.read` | `client.execution.get`, observability support-metrics fetch | `execution.read` |

`client.flow.run` (`sys.v1.flow.run`) is authorized by the **`flow.execute`**
scope — the same scope that gates `POST /platform/flows/{name}/run`. `client.events.emit`
(`sys.v1.event.emit`) requires the **`event.emit`** scope (added 2026-07-07);
emitting can resume waiting flow/agent runs, so it is a side-effecting grant.
`client.execution.get` (`sys.v1.execution.get`) is read-only and tenant-scoped —
only ExecutionUnit rows owned by the caller's tenant — and requires the
`execution.read` scope. `sys.v1.observability.support_metrics` (same `execution.read`
capability/scope) is the read-only, tenant-scoped aggregate the app-side Infinity
support layer fetches (request/health + agent/async/loop-event rollup;
INFINITY-RUNTIME-1 item 3). `client.nodus.*` uses the dedicated `/platform/nodus/*`
routes, not syscall dispatch. Off-surface syscalls (`agent.*`, `job.submit`,
`nodus.execute`, admin) are not dispatchable through this public route.

---

## Response Envelope Contract (FR-19)

**A response says whether its body is the execution envelope. Read the header; do not guess
from the route.**

Routes that pass through `ExecutionPipeline` return the canonical envelope:

```json
{"status": "success", "data": {...}, "trace_id": "...", "duration_ms": 12}
```

Every other route returns a bare body. Both live under the same URL space, so since
2026-08-22 the runtime marks the enveloped ones:

```
X-AINDY-Envelope: v1
```

**Client rule:** unwrap `data` when the header is present; use the body as-is when it is not.
That puts the knowledge in one helper instead of in every API module — which is the defect this
closes: a consumer that guessed wrong rendered a **blank surface with no error**, because an
envelope has no `.length`, so the empty-state branch did not fire either.

**Three things that are easy to get wrong:**

- **`X-Trace-ID` is not a discriminator.** Middleware sets it on *every* response.
- **A blanket unwrap is not a substitute.** A bare body may legitimately carry a `data` key, and
  unwrapping it corrupts the response.
- **The header is absent on error responses, on handler-built `Response` objects, and on routes
  with a registered response adapter** — because those bodies are not the envelope. Absence means
  "not enveloped", never "unknown".

Browser clients on another origin can read it: the runtime lists it in
`Access-Control-Expose-Headers` along with `X-Trace-ID`, `X-Request-ID`, `X-EU-ID` and
`X-API-Version`. Before FR-19 none of those were readable cross-origin — `allow_headers` governs
the *request* direction, and a browser exposes only the CORS safelist unless the server names the
rest.

**Versioning:** `v1` is the current envelope shape. A future shape bumps the value; the header
name is stable. Treat any unrecognised value as enveloped and check the value only if you branch
on shape.

## Health/Readiness Contract

| Endpoint | HTTP Status | Semantics |
|---|---|---|
| `GET /health` | `200` | Healthy or degraded (not unhealthy) |
| `GET /health` | `503` | Unhealthy |
| `GET /ready` | `200` | Ready for traffic |
| `GET /ready` | `503` | Not ready (startup_incomplete, restore_pending, etc.) |

The SDK may poll `GET /ready` before dispatching requests. The response always
includes `{"status": "..."}`.

---

## Leakage Risks

The following SDK assumptions rely on currently-stable but not formally-frozen behavior:

1. **Envelope shape of `/apps/*` routes** — the `{"status": "ok", "data": {...}}`
   wrapper is consistent but not part of a published contract document yet.  Any
   change to envelope shape is a breaking change for SDK consumers.

2. **Agent run status values** — `pending_approval`, `approved`, `executing`,
   `completed`, `failed` are used by the SDK but not formally part of a versioned
   schema contract.

3. **Syscall input/output schema fields** — individual field names within stable
   syscall schemas are not individually versioned.  Adding optional fields is safe;
   removing fields requires a major bump.

---

## Regression Tests

```bash
pytest tests/unit/test_cross_repo_compatibility.py -v -k sdk
pytest tests/unit/test_runtime_compatibility_metadata.py -v
```
