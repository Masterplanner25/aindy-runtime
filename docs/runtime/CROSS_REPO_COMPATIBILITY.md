---
title: "Cross-Repo Compatibility"
last_verified: "2026-06-04"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Cross-Repo Compatibility

Defines the compatibility obligations that `aindy-runtime` must satisfy before
a release reaches `aindy-sdk` or `aindy-ui-kit` consumers.

Companion documents:
- `RELEASE_GATES.md` — gate policy (when to block a release)
- `RELEASE_CHECKLIST.md` — operator verification steps
- `PUBLIC_RUNTIME_SURFACES.md` — surface stability classification

---

## What Must Hold Before Any Release

These obligations apply to every release regardless of risk class.

### 1. Version Envelope Shape

`GET /api/version` must return the declared envelope shape:

```json
{
  "status": "ok",
  "data": {
    "version": "<semver>",
    "api_version": "<N.N>",
    "compatibility": {
      "breaking_change_policy": "<non-empty>",
      "min_client_version": "<semver>",
      "runtime_package": {"name": "aindy-runtime", "version": "<semver>"}
    },
    "system": {
      "runtime": {
        "boot_mode": "<non-empty>"
      }
    }
  }
}
```

Consumers: `aindy-sdk` version checks, platform SPA `bootIdentity` / `PlatformHomeRedirect`.

**Test:** `tests/unit/test_cross_repo_compatibility.py::test_api_version_envelope_shape_stable`

### 2. Stable Syscall Names

No stable syscall in `SYSCALL_REGISTRY` may be renamed or removed without a
major version bump. Current stable syscalls (`stable=True`):

| Syscall | Capability |
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

Consumers: `aindy-sdk` syscall dispatch, `aindy-apps` flow definitions.

**Test:** `tests/unit/test_cross_repo_compatibility.py::test_stable_syscall_names_present`

### 3. Watcher Endpoint Path

`POST /watcher/signals` and `GET /watcher/signals` must remain accessible with
API-key authentication. These paths are hardcoded in `aindy-sdk`'s watcher client.

Consumers: `aindy-sdk`.

**Test:** `tests/unit/test_cross_repo_compatibility.py::test_watcher_endpoint_registered`

### 4. Auth Envelope Shape

`POST /auth/login` and `POST /auth/register` must return an envelope that unwraps to
an object with at least `access_token` and `is_admin` fields. The ui-kit `loginUser`,
`registerUser`, and `bootIdentity` functions call `.then(unwrapEnvelope)` and
destructure the result.

Consumers: `aindy-ui-kit` auth API (`src/api/auth.js`).

### 5. Health/Ready HTTP Semantics

- `GET /health` must return HTTP 200 for healthy/degraded; 503 only for unhealthy.
- `GET /ready` must return HTTP 503 before startup completes and HTTP 200 after.
- Both must return a JSON body with a `status` field.
- `GET /health/deep` must include `syscall_registry` in `checks` (IDEM-7 guarantee).

Consumers: `aindy-sdk` readiness polling, operator load balancers, platform SPA.

**Test:** `tests/unit/test_operability_contracts.py`

---

## aindy-sdk Dependencies

The SDK requires these runtime surfaces to be stable:

| Surface | Path | Stability | Notes |
|---|---|---|---|
| Version metadata | `GET /api/version` | stable | `compatibility`, `system.runtime.boot_mode` |
| Liveness check | `GET /health` | stable | `status` field |
| Readiness check | `GET /ready` | stable | HTTP 200/503 semantics |
| Auth tokens | `POST /auth/login` | stable | `access_token` in unwrapped response |
| Watcher signal ingestion | `POST /watcher/signals` | stable | API-key auth |
| Watcher signal read | `GET /watcher/signals` | stable | API-key auth |
| Memory read | `GET /apps/memory/...` | conditionally stable | stable envelope shape |
| Memory write | `POST /apps/memory/...` | conditionally stable | stable envelope shape |
| Agent runs | `GET /apps/agent/runs/...` | experimental | may change |

SDK compatibility window: `aindy-runtime >= 1.0, < 2.0` (PEP 440, declared in `/api/version`).

---

## aindy-ui-kit / Platform SPA Dependencies

The platform SPA (via `@aindy/ui-kit`) requires:

| Surface | Notes |
|---|---|
| ROUTES table constants match served paths | Verified by `test_cross_repo_compatibility.py` |
| `GET /api/version` → `data.system.runtime.boot_mode` | Used by `PlatformHomeRedirect` |
| `POST /auth/login` → unwrapped `access_token`, `is_admin` | Used by `AuthContext` |
| `GET /platform/*` | All platform operator endpoints in PLATFORM_ROUTERS |
| `GET /apps/*` | App-layer endpoints in APP_ROUTERS |
| SPA asset 404 discrimination | `/platform/assets/` must 404, not fall back to HTML |

Any removal of a ROUTES constant from `@aindy/ui-kit` that maps to a served runtime
endpoint is a breaking change for the platform SPA.

---

## Compatibility Verification Before Release

Before any release that touches stable surfaces, run:

```bash
pytest tests/unit/test_cross_repo_compatibility.py -v
pytest tests/unit/test_runtime_compatibility_metadata.py -v
pytest tests/unit/test_runtime_public_contract.py -v
```

If any test fails, do not release until the surface is restored or the test is
updated to reflect a documented, intentional contract change.

---

## Breaking-Change Policy

A **breaking change** is any modification to a surface marked `stable` in
`PUBLIC_RUNTIME_SURFACES.md` or `RUNTIME_STABILITY_INDEX.md` that:

- removes a field from a response envelope
- changes an HTTP status code for an established input
- changes a path that SDK or UI hardcodes
- removes or renames a stable syscall
- removes or renames a stable HTTP route

Breaking changes require:
1. A major version bump in `AINDY/_version.py`
2. An explicit entry in `CHANGELOG.md`
3. A `min_client_version` bump in `/api/version` compatibility metadata
4. Advance notice in release notes before the change ships

The experimental surfaces listed in `PUBLIC_RUNTIME_SURFACES.md` may change between
minor releases without the above ceremony.
