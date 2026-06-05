---
title: "Runtime → SDK Contract"
last_verified: "2026-06-04"
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
| `sys.v1.memory.search` | `memory.read` |
| `sys.v1.memory.tree` | `memory.read` |
| `sys.v1.memory.trace` | `memory.read` |
| `sys.v1.flow.run` | `flow.run` |
| `sys.v1.event.emit` | `event.emit` |
| `sys.v1.nodus.execute` | `nodus.execute` |
| `sys.v1.job.submit` | `job.submit` |
| `sys.v1.flow.execute_intent` | `flow.run` |

Experimental syscalls (`stable=False`) may change between minor releases.

---

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
