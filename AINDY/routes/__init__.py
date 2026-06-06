"""
routes/__init__.py - runtime route registry.

AINDY/routes owns root and platform routers. App-layer /apps/* surfaces
are registered by plugin bootstraps (aindy-apps-monolith) via register_router().

Extracted to plugin layer (no longer runtime-owned):
  - agent_router       → apps.agent.routes.agent_router
  - memory_metrics_router → apps.memory.routes.memory_metrics_router
  - memory_trace_router   → apps.memory.routes.memory_trace_router
"""
import os

from AINDY.routes.auth_router import router as auth_router
from AINDY.routes.coordination_router import router as coordination_router
from AINDY.routes.db_verify_router import router as db_verify_router
from AINDY.routes.flow_router import router as flow_router
from AINDY.routes.health_router import router as health_router
from AINDY.routes.memory_router import router as memory_router
from AINDY.routes.observability_router import router as observability_router
from AINDY.routes.platform_router import router as platform_router
from AINDY.routes.watcher_router import router as watcher_router


ROOT_ROUTERS = [
    health_router,
    auth_router,
    watcher_router,   # API-key authenticated; client targets /watcher/signals
]

LEGACY_ROOT_ROUTERS = []

PLATFORM_ROUTERS = [
    flow_router,
    observability_router,
    db_verify_router,  # operator schema inspection at /platform/db/verify
]

# Remaining runtime-owned /apps surface (memory CRUD/search/recall + coordination).
# agent_router, memory_metrics_router, memory_trace_router moved to plugin layer.
APP_ROUTERS = [
    memory_router,
    coordination_router,
]

if os.getenv("AINDY_ENABLE_LEGACY_SURFACE", "false").lower() in {"1", "true", "yes"}:
    LEGACY_ROOT_ROUTERS.append(flow_router)
    LEGACY_ROOT_ROUTERS.append(observability_router)

ROUTERS = ROOT_ROUTERS + [platform_router] + PLATFORM_ROUTERS + APP_ROUTERS + LEGACY_ROOT_ROUTERS
