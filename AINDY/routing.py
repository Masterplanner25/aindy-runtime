import os
from pathlib import Path

from fastapi import Depends
from fastapi.staticfiles import StaticFiles
from prometheus_client import make_asgi_app as _make_metrics_asgi
from starlette.exceptions import HTTPException as StarletteHTTPException

from AINDY.core.execution_guard import require_execution_context
from AINDY.spa_fallback import SPAFallbackMiddleware
from AINDY.core.route_execution_guard import enforce_registered_route_execution
from AINDY.platform_layer.metrics import REGISTRY as _METRICS_REGISTRY
from AINDY.platform_layer.registry import get_legacy_root_routers, get_routers
from AINDY.routes.version_router import router as version_router
from AINDY.routes import (
    APP_ROUTERS,
    LEGACY_ROOT_ROUTERS,
    PLATFORM_ROUTERS,
    ROOT_ROUTERS,
    platform_router,
)
from AINDY.routes.platform.admin_router import router as admin_router

_PLATFORM_UI_DIST = Path(__file__).parent / "platform" / "dist"


class _SPAStaticFiles(StaticFiles):
    """StaticFiles with SPA fallback: unknown routes → index.html, missing assets → 404."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # Only fall back for route misses, not missing asset files.
            # Vite emits all static assets under assets/; a 404 there is a real
            # missing file, not a client-side route that React Router should handle.
            if exc.status_code == 404 and not path.startswith("assets/"):
                return await super().get_response("index.html", scope)
            raise


def home():
    return {"message": "A.I.N.D.Y. API is running!"}


def register_routes(app) -> None:
    app.mount("/metrics", _make_metrics_asgi(registry=_METRICS_REGISTRY))
    app.get("/")(home)
    app.include_router(version_router)

    for route in ROOT_ROUTERS:
        app.include_router(route, dependencies=[Depends(require_execution_context)])

    for route in PLATFORM_ROUTERS:
        app.include_router(route, prefix="/platform", dependencies=[Depends(require_execution_context)])
    app.include_router(platform_router, dependencies=[Depends(require_execution_context)])
    # Admin routes bypass the execution contract — they are plain DB-query handlers.
    # Auth is enforced per-handler via require_admin_principal.
    app.include_router(admin_router, prefix="/platform")

    for route in APP_ROUTERS:
        app.include_router(route, prefix="/apps", dependencies=[Depends(require_execution_context)])

    application_routers = get_routers()
    for route in application_routers:
        app.include_router(route, prefix="/apps", dependencies=[Depends(require_execution_context)])

    if os.getenv("AINDY_ENABLE_LEGACY_SURFACE", "false").lower() in {"1", "true", "yes"}:
        for route in APP_ROUTERS:
            app.include_router(route, dependencies=[Depends(require_execution_context)])
        for route in application_routers:
            app.include_router(route, dependencies=[Depends(require_execution_context)])
        for route in get_legacy_root_routers():
            app.include_router(route, dependencies=[Depends(require_execution_context)])
        for route in LEGACY_ROOT_ROUTERS:
            app.include_router(route, dependencies=[Depends(require_execution_context)])

    enforce_registered_route_execution(app)

    if _PLATFORM_UI_DIST.is_dir():
        app.mount("/platform", _SPAStaticFiles(directory=str(_PLATFORM_UI_DIST), html=True), name="platform-ui")

    # Add LAST → outermost wrapper → runs before routing on every request.
    app.add_middleware(SPAFallbackMiddleware)
