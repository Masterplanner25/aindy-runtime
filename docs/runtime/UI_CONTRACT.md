---
title: "Runtime → UI Contract"
last_verified: "2026-06-04"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Runtime → UI Contract

Defines what the platform SPA (`@aindy/ui-kit` + `platform/src/`) can rely on from
`aindy-runtime` across releases.

See `CROSS_REPO_COMPATIBILITY.md` for the breaking-change policy.

---

## Boot Mode Detection

`GET /api/version` → `data.system.runtime.boot_mode`

Used by `PlatformHomeRedirect` (via `bootIdentity` in `aindy-ui-kit/src/api/auth.js`)
to choose the post-login redirect destination (`/agent` vs `/flows`).

**Invariant:** The `boot_mode` string must always be present in the version response.
Removing it or changing the nesting path (`data.system.runtime.boot_mode`) is a
breaking change for the platform SPA.

**Stable values:** `"runtime-only"`, `"full"`.

**Test:** `tests/unit/test_cross_repo_compatibility.py::test_boot_mode_field_in_version_metadata`

---

## Auth Flow

The platform SPA calls auth endpoints via `loginUser`, `registerUser`, and
`bootIdentity` from `aindy-ui-kit/src/api/auth.js`. All three call
`.then(unwrapEnvelope)` on the response.

**Invariant:** The unwrapped response must include:

| Field | Used by |
|---|---|
| `access_token` | `AuthContext` — stored as Bearer token |
| `is_admin` | `PlatformGuard` — determines redirect vs `<NotAdmin />` |

Removing either field breaks the login flow. Adding fields is safe.

---

## ROUTES Table Invariants

The platform SPA imports `ROUTES` from `@aindy/ui-kit`. For every `ROUTES.*.*`
constant used in `platform/src/api/*.js` or `platform/src/components/**/*.jsx`,
the target path must:

1. Be served by a registered runtime router.
2. Return a meaningful response (not 404) for authenticated requests.

**Enforced by:** `FEATURE_FLAGS` gates in `platform/src/api/_routes.js` — NavLinks
for unserved routes must be gated by a `false` flag.

**Test:** `tests/unit/test_cross_repo_compatibility.py::test_served_platform_routes_match_expected_prefixes`

**Current flag state:**

| Flag | Value | Notes |
|---|---|---|
| `OPERATOR_SCHEDULER_STATUS` | `true` | Fixed 2026-06-04 |
| `OPERATOR_FLOW_STRATEGIES` | `false` | OPER-DEFER-001: not yet served |
| `OPERATOR_AUTOMATION_LOGS` | `false` | OPER-DEFER-002: lives in monolith |
| `RIPPLETRACE_VIEWER` | `false` | RIPPLE-ROUTES-001: bare monolith path |

---

## SPA Static File Serving

`GET /platform/` and all SPA client-side routes must return `200 + index.html`.

**Asset 404 discrimination:** Paths under `/platform/assets/` must return `404` (not
index.html fallback) when the file does not exist. This is enforced by
`_SPAStaticFiles.get_response()` in `AINDY/routing.py`. Changing this to an
unconditional fallback would cause Vite-emitted asset 404s to silently serve HTML.

**Test:** `tests/unit/test_operability_contracts.py`

---

## Operator Endpoint Availability

All paths prefixed with `/platform/` and listed in `PLATFORM_ROUTERS` must be served.
The following operator surfaces are stable for the platform SPA:

| Surface | Prefix |
|---|---|
| Flow definitions | `/platform/flows/` |
| Observability (LLM, requests, scheduler) | `/platform/observability/` |
| DB verification | `/platform/db/` |
| Syscall catalog | `/platform/syscalls` |

---

## Leakage Risks

1. **ROUTES constants referencing unserved paths** — tracked in TECH_DEBT.md as
   `OPER-DEFER-001`, `OPER-DEFER-002`, `RIPPLE-ROUTES-001`. These are gated by
   `FEATURE_FLAGS`; do not remove the gates before the backend routes land.

2. **ui-kit publish gap** — any quarantine commit to `@aindy/ui-kit` source that
   removes ROUTES groups used by the monolith must not be published until
   ROUTES-CONSUMER-SPLIT-1 is resolved.

3. **`VITE_API_BASE_URL` bakes `localhost` into the bundle** — see PLATFORM-UI-ENV-1.
   Remote deployments require explicit `VITE_API_BASE_URL` at build time.

---

## Regression Tests

```bash
pytest tests/unit/test_cross_repo_compatibility.py -v -k ui
pytest tests/unit/test_operability_contracts.py -v
```
