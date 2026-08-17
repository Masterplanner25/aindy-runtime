"""KEY-SCOPE-ESCALATION-1 (second half) — the `/platform` tree checks scopes per endpoint.

`require_platform_admin_access` on the parent router returns **any** authenticated API key
unconditionally:

```python
if current_user.get("auth_type") == "api_key":
    return current_user
```

Its docstring justified that with *"API keys are pre-authorized at the platform level (scope
enforcement happens per-endpoint or per-syscall)"*. For 46 of 53 routes it did not. Demonstrated
from a key holding the single scope `flow.read`, owned by a non-admin user:

| Route | Before |
|---|---|
| `GET /platform/keys`, `/nodes`, `/webhooks`, `/nodus/*`, `/queue/*` | 200 |
| `POST /platform/queue/dead-letters/drain` | 200 — drained the queue |
| `POST /platform/ops/rotate-secret-key` | **200 — rotated the platform signing key** |

★ The rotation is the one that matters, and it is worse than destructive. The caller supplies
the new key, so afterwards they know the signing secret and can mint tokens that verify — every
user impersonable, admin included. `KEY-SCOPE-ESCALATION-1`'s delegation rule does not touch it;
that rule bounds what a key may *grant*, not what it may *do*.

**The fix could not be "require `platform.admin` on the router gate".** `POST /platform/syscall`
is the SDK's entire surface and is used with narrow scopes like `memory.read`; demanding
`platform.admin` there would break every SDK caller. The fix is the per-endpoint enforcement the
docstring already assumed.

**For JWT callers nothing changes at all** — the parent gate already required `is_admin`, and an
admin session derives `platform.admin` and `webhook.manage`. Only API keys are newly constrained,
which is the entire point.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.runtime_only


def _scopes():
    from AINDY.auth.api_key_auth import Scopes

    return Scopes


@pytest.fixture
def weak_key(db_session):
    """A real, persisted API key holding exactly `flow.read`, owned by a non-admin user.

    Inserted with raw SQL rather than the ORM: `platform_api_keys.scopes` is a PostgreSQL
    `ARRAY`, and the ORM insert fails on SQLite with `type 'list' is not supported`. The auth
    path reads the column with raw SQL and `json.loads`, so a JSON string round-trips correctly
    on both backends — which is what lets this whole file run without a database container.
    """
    from AINDY.db.models.user import User
    from AINDY.platform_layer.api_key_service import generate_key
    from AINDY.services.auth_service import hash_password

    uid = uuid.uuid4()
    db_session.add(
        User(
            id=uid,
            email=f"weak-{uid}@x.test",
            hashed_password=hash_password("pw12345678"),
            is_admin=False,
        )
    )
    db_session.commit()

    raw, key_hash = generate_key()
    db_session.execute(
        text(
            "INSERT INTO platform_api_keys "
            "(id, user_id, name, key_prefix, key_hash, scopes, is_active, created_at, updated_at) "
            "VALUES (:i, :u, :n, :p, :h, :s, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {
            "i": uuid.uuid4().hex,
            "u": uid.hex,
            "n": "weak",
            "p": raw[:16],
            "h": key_hash,
            "s": json.dumps([_scopes().FLOW_READ]),
        },
    )
    db_session.commit()
    db_session.close()
    return {"X-Platform-Key": raw}


# --------------------------------------------------------------------------------------
# ★ The reproduction
# --------------------------------------------------------------------------------------


def test_a_narrow_key_cannot_rotate_the_signing_key(runtime_only_client, weak_key):
    """★ The sharpest one. This returned **200** and completed the rotation.

    Asserted with a *distinct, valid* new key on purpose. The first probe used a 40-character
    key that happened to equal the active one and came back 400 — which reads like a refusal and
    is not: it is `new_key is the same as the current active key`, raised **after** authorization
    and after the length check. A test that sent the same value would pass against the
    unfixed code.
    """
    response = runtime_only_client.post(
        "/platform/ops/rotate-secret-key",
        json={"new_key": "a-distinct-and-sufficiently-long-key-123456"},
        headers=weak_key,
    )

    assert response.status_code == 403, (
        f"a flow.read key rotated the platform signing key ({response.status_code}). Whoever "
        f"calls this chooses the new secret, so afterwards they can forge tokens for any user."
    )


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("GET", "/platform/keys", None),
        ("POST", "/platform/keys", {"name": "x", "scopes": ["flow.read"]}),
        ("GET", "/platform/nodes", None),
        ("GET", "/platform/webhooks", None),
        ("GET", "/platform/queue/dead-letters", None),
        ("POST", "/platform/queue/dead-letters/drain", None),
        ("GET", "/platform/nodus/scripts", None),
        ("POST", "/platform/nodus/run", {"script": "print(1)"}),
        ("GET", "/platform/nodus/schedule", None),
        ("GET", "/platform/observability/system", None),
        ("GET", "/platform/flows/runs", None),
    ],
)
def test_a_narrow_key_is_refused_across_the_platform_tree(
    method, path, body, runtime_only_client, weak_key
):
    """Every one of these answered 200 to this key before the per-endpoint gates."""
    response = runtime_only_client.request(method, path, json=body, headers=weak_key)

    assert response.status_code == 403, (
        f"{method} {path} answered {response.status_code} to a flow.read-only key. "
        f"Body: {response.text[:200]}"
    )


# --------------------------------------------------------------------------------------
# Liveness — the key must still work where it is genuinely entitled
# --------------------------------------------------------------------------------------


def test_the_key_still_reaches_what_its_scope_covers(runtime_only_client, weak_key):
    """★ Without this, "refuses everything" would satisfy every test above.

    `GET /platform/flows` is gated on `flow.read`, which this key holds. A 403 here would mean
    the tree was locked down rather than scoped.
    """
    response = runtime_only_client.get("/platform/flows", headers=weak_key)

    assert response.status_code != 403, (
        f"the key was refused a route its own scope covers: {response.text[:200]}"
    )


def test_the_sdk_dispatch_surface_is_deliberately_not_route_gated(runtime_only_client, weak_key):
    """★ An exception recorded as a decision, because it looks like an omission.

    `POST /platform/syscall` and `GET /platform/syscalls` are the SDK's surface. Their authority
    is resolved **per syscall** by `_resolve_dispatch_capabilities`, which grants only the
    requested syscall's own capability and scope-checks API-key callers there. A route-level
    scope would have to be one every SDK key holds, which is no constraint at all — or it would
    break every SDK caller.

    So they stay ungated at the route level, and the check that matters lives one layer in. This
    test exists so that "these two are open" reads as a decision rather than the 47th route
    someone forgot.
    """
    listing = runtime_only_client.get("/platform/syscalls", headers=weak_key)
    assert listing.status_code != 403, "the syscall catalogue is meant to stay readable"

    # ...and the per-syscall check is the thing actually holding the line.
    denied = runtime_only_client.post(
        "/platform/syscall",
        json={"name": "sys.v1.memory.delete", "payload": {"node_id": str(uuid.uuid4())}},
        headers=weak_key,
    )
    assert denied.status_code != 200 or "success" not in denied.text, (
        "a flow.read key completed a memory.delete syscall — the per-syscall grant is the only "
        "thing gating this route and it is not holding"
    )


# --------------------------------------------------------------------------------------
# Coverage, derived from the registered app
# --------------------------------------------------------------------------------------


def _platform_routes(app):
    from fastapi.routing import APIRoute, _IncludedRouter

    def dep_names(dependant):
        out = set()

        def walk(d):
            for sub in d.dependencies:
                out.add(getattr(sub.call, "__name__", ""))
                walk(sub)

        walk(dependant)
        return out

    found = {}

    def walk(routes, inherited):
        for route in routes:
            if isinstance(route, APIRoute):
                deps = dep_names(route.dependant) | inherited
                if "require_platform_admin_access" in deps:
                    found[f"{sorted(route.methods)[0]} {route.path}"] = deps
            elif isinstance(route, _IncludedRouter):
                extra = {
                    getattr(d.dependency, "__name__", "")
                    for d in (getattr(route.include_context, "dependencies", None) or [])
                }
                extra |= {
                    getattr(d.dependency, "__name__", "")
                    for d in (route.original_router.dependencies or [])
                }
                walk(route.original_router.routes, inherited | extra)

    walk(app.routes, set())
    return found


def test_only_the_two_sdk_routes_lack_a_per_endpoint_gate(runtime_only_app):
    """Pinned by equality so a new ungated `/platform` route fails here.

    The router gate admits any API key, so on this tree a missing per-endpoint scope is not a
    partial control — it is no control.
    """
    ungated = {
        key
        for key, deps in _platform_routes(runtime_only_app).items()
        if not any(n.startswith("enforce_scope_") for n in deps)
    }

    assert ungated == {
        "GET /syscalls",
        "POST /syscall",
    }, f"unexpected set of ungated /platform routes: {sorted(ungated)}"


def test_the_platform_tree_is_actually_populated(runtime_only_app):
    """Liveness for the walk itself — an empty result would satisfy the test above."""
    assert len(_platform_routes(runtime_only_app)) >= 50


def test_no_route_relies_on_the_permissive_guard_alone(runtime_only_app):
    """★ The invariant this whole entry reduces to.

    The runtime has **two** admin dependencies that do different things, which is how the hole
    survived review:

    * `require_admin_principal` demands `platform.admin` on an API key — a real check.
    * `require_platform_admin_access` returns **any** API key unconditionally — not a check at
      all, for a key.

    So a route whose only protection is the second one is unprotected against every key ever
    issued. This asserts that no such route exists: anything without a scope gate must be behind
    the *strict* guard, and the two SDK routes are named exceptions whose authority is resolved
    per syscall one layer in.

    Written this way rather than as "count the gates" because the number will drift and the
    invariant will not.
    """
    from fastapi.routing import APIRoute, _IncludedRouter

    def dep_names(dependant):
        out = set()

        def walk(d):
            for sub in d.dependencies:
                out.add(getattr(sub.call, "__name__", ""))
                walk(sub)

        walk(dependant)
        return out

    unprotected = set()

    def walk(routes, inherited):
        for route in routes:
            if isinstance(route, APIRoute):
                deps = dep_names(route.dependant) | inherited
                if any(n.startswith("enforce_scope_") for n in deps):
                    continue
                if "require_admin_principal" in deps:
                    continue
                if "require_platform_admin_access" in deps:
                    unprotected.add(f"{sorted(route.methods)[0]} {route.path}")
            elif isinstance(route, _IncludedRouter):
                extra = {
                    getattr(d.dependency, "__name__", "")
                    for d in (getattr(route.include_context, "dependencies", None) or [])
                }
                extra |= {
                    getattr(d.dependency, "__name__", "")
                    for d in (route.original_router.dependencies or [])
                }
                walk(route.original_router.routes, inherited | extra)

    walk(runtime_only_app.routes, set())

    assert unprotected == {
        "GET /syscalls",
        "POST /syscall",
    }, (
        "these routes are protected only by `require_platform_admin_access`, which admits any "
        f"authenticated API key — i.e. they are open to every key ever issued: {sorted(unprotected)}"
    )
