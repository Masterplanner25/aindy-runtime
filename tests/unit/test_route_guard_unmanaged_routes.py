"""ROUTE-GUARD-1 — a deliberate HTTPException from an unmanaged route is not a bypass.

`enforce_registered_route_execution` wraps every registered route. Its **success** path
(`_assert_execution_context_entered`) always asked two questions: did this request enter
the execution pipeline, and *was it required to*. Its **failure** path asked only the
first. So any exception raised by a route registered deliberately outside the contract
became a `RouteExecutionViolation`, which the exception handlers render as a 500.

Three routers are registered without `require_execution_context` on purpose —
`admin_router`, `agents_router`, `automation_router` — because they are plain DB-query
handlers, and routing.py says so at each call site. Every `raise HTTPException` in them
was therefore returning **500** with `{"error": "internal_error"}` instead of its own
status.

That includes FR-12's reserved-namespace guard, shipped the day before this was found:
`POST /platform/admin/agents/register` with `memory_namespace: "runtime"` correctly
refused the write, then reported it as an internal error. The guard worked; only its
answer was wrong. Nothing caught it because the FR-12 tests assert on the route's
*source text* rather than calling it — the "covers, asserts nothing" family again.

Mutation-checked: restore the old condition
(`not hasattr(request.state, "execution_context")`) in `_wrap_route_call` and every
test in `TestUnmanagedRoutesKeepTheirStatusCodes` fails with 500.
`TestManagedRoutesStillViolate` is the liveness control in the other direction — without
it, "no violations anywhere" would satisfy this file trivially.
"""
from __future__ import annotations

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from AINDY.core.execution_guard import require_execution_context
from AINDY.exception_handlers import register_exception_handlers
from AINDY.core.route_execution_guard import (
    RouteExecutionViolation,
    enforce_registered_route_execution,
)
from AINDY.services.auth_service import require_admin_principal

pytestmark = pytest.mark.runtime_only

_ADMIN = {
    "user_id": "00000000-0000-0000-0000-000000000001",
    "is_admin": True,
    "auth_type": "jwt",
}


class TestUnmanagedRoutesKeepTheirStatusCodes:
    """Real routes, called over HTTP — not source inspection."""

    @pytest.fixture(autouse=True)
    def _as_admin(self, runtime_only_app):
        runtime_only_app.dependency_overrides[require_admin_principal] = lambda: _ADMIN

    def test_reserved_namespace_is_409_not_500(self, runtime_only_client, mock_db):
        """The FR-12 guard. It blocked the write all along; it reported 500."""
        response = runtime_only_client.post(
            "/platform/admin/agents/register",
            json={"name": "Hijack", "memory_namespace": "runtime"},
        )
        assert response.status_code == 409, response.text
        assert "reserved" in response.text

    def test_missing_agent_is_404_not_500(self, runtime_only_client, mock_db):
        response = runtime_only_client.delete("/platform/admin/agents/does-not-exist")
        assert response.status_code == 404, response.text

    def test_missing_agent_on_restore_is_404_not_500(self, runtime_only_client, mock_db):
        response = runtime_only_client.post("/platform/admin/agents/nope/restore")
        assert response.status_code == 404, response.text

    # `POST /admin/users/{user_id}/promote` is deliberately NOT asserted here. It also
    # returns 500 for a missing user, but for an unrelated reason this fix does not and
    # should not change: it passes the raw path string into `User.id == user_id`, and the
    # SQLite UUID binding raises `AttributeError: 'str' object has no attribute 'hex'`
    # before the 404 branch is reached. That is a genuine 500 — the route really did
    # fail — and it is confined to the SQLite test harness, since psycopg2 casts the
    # string on PostgreSQL. Tracked separately rather than folded into a route-guard fix.


class TestManagedRoutesStillViolate:
    """Liveness control — the guard must still fire where it is supposed to.

    A fix that simply stopped raising would pass every assertion above.
    """

    @staticmethod
    def _managed_app(router: APIRouter) -> FastAPI:
        app = FastAPI()
        app.include_router(router, dependencies=[Depends(require_execution_context)])
        register_exception_handlers(app)
        enforce_registered_route_execution(app)
        return app

    def test_http_exception_from_a_managed_route_is_still_a_violation(self):
        router = APIRouter()

        @router.get("/managed-teapot")
        def managed_teapot(request: Request):
            raise HTTPException(status_code=418, detail="teapot")

        app = self._managed_app(router)
        with TestClient(app, raise_server_exceptions=True) as client:
            with pytest.raises(RouteExecutionViolation, match="/managed-teapot"):
                client.get("/managed-teapot")

    def test_unmanaged_route_in_the_same_shape_is_not_a_violation(self):
        """Same endpoint, same exception; the only difference is the contract dependency.

        This is the whole distinction the fix restores, isolated from any real router.
        """
        router = APIRouter()

        @router.get("/unmanaged-teapot")
        def unmanaged_teapot(request: Request):
            raise HTTPException(status_code=418, detail="teapot")

        app = FastAPI()
        app.include_router(router)  # deliberately no require_execution_context
        register_exception_handlers(app)
        enforce_registered_route_execution(app)

        with TestClient(app, raise_server_exceptions=True) as client:
            response = client.get("/unmanaged-teapot")
        assert response.status_code == 418, response.text
