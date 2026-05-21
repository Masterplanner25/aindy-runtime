import json
import logging
import os
import sys
import time
import uuid
from AINDY.platform_layer.log_config import configure_logging
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import create_engine
from contextlib import asynccontextmanager

_log_env = os.getenv("ENV", "development")
_log_level = os.getenv("LOG_LEVEL", "INFO")
configure_logging(env=_log_env, log_level=_log_level)

from AINDY.platform_layer import scheduler_service
from AINDY.platform_layer import registry
from AINDY.platform_layer.bootstrap_contract import validate_bootstrap_manifest
from AINDY.platform_layer.deployment_contract import (
    PROCESS_ROLE_API,
    background_tasks_enabled,
    background_leadership_mode_for_profile,
    clear_api_runtime_condition,
    event_bus_required,
    publish_api_runtime_state,
    reset_runtime_state,
    resolve_boot_mode_for_profile,
    resolve_api_deployment_profile,
    RUNTIME_ONLY_BOOT_MODE,
    set_api_runtime_condition,
    validate_api_deployment_profile,
)
from AINDY.platform_layer.cache_backend import NoOpCacheBackend
from AINDY.platform_layer.rate_limiter import limiter
from AINDY.platform_layer.registry import (
    emit_event,
    get_active_plugin_profile,
    get_active_plugin_profile_source,
    get_plugin_boot_order,
    get_legacy_root_routers,
    get_registered_apps,
    get_routers,
    load_plugins,
    run_startup_hooks,
)
from AINDY.core.system_event_service import emit_error_event
from AINDY.db.database import SessionLocal
from AINDY.db.mongo_setup import ensure_mongo_ready, ping_mongo

# Backward compatibility for tests that monkeypatch main.init_mongo directly.
init_mongo = ensure_mongo_ready
from AINDY.core.execution_guard import require_execution_context, validate_execution_contract
from AINDY.routes import (
    APP_ROUTERS,
    LEGACY_ROOT_ROUTERS,
    PLATFORM_ROUTERS,
    ROOT_ROUTERS,
    platform_router,
)
from AINDY.config import settings
from AINDY.core.distributed_queue import QueueSaturatedError, validate_queue_backend
from AINDY.core.observability_events import emit_observability_event, emit_recovery_failure
from AINDY.kernel.circuit_breaker import CircuitOpenError
from AINDY.kernel.errors import BootstrapDependencyError
from AINDY.platform_layer.extension_policy import (
    external_python_override_state,
)
from AINDY.platform_layer.extension_runtime_inventory import (
    trusted_python_execution_summary,
)
from AINDY.platform_layer.health_service import check_redis_available
from AINDY.platform_layer.otel import init_otel
from AINDY.platform_layer.trace_context import (
    _trace_id_ctx,
    reset_current_request,
    reset_current_trace_id,
    set_current_request,
    set_current_trace_id,
)
from AINDY.db.schema_contract import (
    SCHEMA_STATE_INCOMPATIBLE_MANUAL,
    SCHEMA_STATE_UPGRADE_REQUIRED,
    ensure_runtime_schema,
    inspection_contract,
)

try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    _OTEL_FASTAPI_AVAILABLE = True
except ImportError:
    FastAPIInstrumentor = None
    _OTEL_FASTAPI_AVAILABLE = False

# --- Ensure root path is importable ---
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


# For in-memory caching
# If you want to use Redis (uncomment and configure):
# from fastapi_cache.backends.redis import RedisBackend
# from redis import asyncio as aioredis



logger = logging.getLogger("AINDY.main")
_OPENAI_PROJECT_KEY_PREFIX = "sk-" + "proj-"
_pool_was_near_exhaustion: bool = False

_SAFE_DEGRADED = "safe_degraded"
_UNSAFE_DEGRADED = "unsafe_degraded"
_STARTUP_FATAL = "startup_fatal"


def _record_runtime_condition(
    *,
    code: str,
    component: str,
    classification: str,
    detail: str,
    production_behavior: str,
) -> None:
    set_api_runtime_condition(
        code=code,
        component=component,
        classification=classification,
        detail=detail,
        production_behavior=production_behavior,
    )


def _handle_runtime_degradation(
    *,
    code: str,
    component: str,
    classification: str,
    detail: str,
    production_message: str | None = None,
) -> None:
    production_behavior = "startup-fatal" if classification != _SAFE_DEGRADED else "explicitly degraded"
    _record_runtime_condition(
        code=code,
        component=component,
        classification=classification,
        detail=detail,
        production_behavior=production_behavior,
    )
    if settings.is_prod and classification != _SAFE_DEGRADED:
        raise RuntimeError(production_message or detail)

def _publish_boot_runtime_state() -> None:
    active_profile = get_active_plugin_profile()
    boot_mode = resolve_boot_mode_for_profile(active_profile)
    deployment_profile, deployment_profile_source = resolve_api_deployment_profile()
    app_plugin_count = len(get_registered_apps())
    override_state = external_python_override_state()
    trusted_python = trusted_python_execution_summary()
    publish_api_runtime_state(
        process_role=PROCESS_ROLE_API,
        boot_mode=boot_mode,
        boot_profile=active_profile,
        boot_profile_source=get_active_plugin_profile_source(),
        deployment_profile=deployment_profile,
        deployment_profile_source=deployment_profile_source,
        background_leadership_mode=background_leadership_mode_for_profile(
            deployment_profile
        ),
        app_plugins_loaded=app_plugin_count > 0,
        app_plugin_count=app_plugin_count,
        external_python_override_active=bool(override_state["enabled"]),
        external_python_override_execution_model=str(
            override_state["execution_model"]
        ),
        trusted_python_execution=trusted_python,
    )


def _enforce_external_python_override_policy() -> None:
    override_state = external_python_override_state()
    if not override_state["enabled"]:
        clear_api_runtime_condition("external_python_override_enabled")
        publish_api_runtime_state(
            external_python_override_active=False,
            external_python_override_execution_model=str(
                override_state["execution_model"]
            ),
        )
        return

    publish_api_runtime_state(
        external_python_override_active=True,
        external_python_override_execution_model=str(
            override_state["execution_model"]
        ),
    )
    _record_runtime_condition(
        code="external_python_override_enabled",
        component="extension_policy",
        classification=_SAFE_DEGRADED,
        detail=(
            "AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS is set, but external third-party "
            "Python no longer executes in-process. Manifest bootstrap remains blocked, "
            "and third-party plugin nodes must use the isolated plugin-host boundary."
        ),
        production_behavior=(
            "operator-visible legacy configuration with no direct in-process effect"
        ),
    )
    logger.warning(
        "[startup] AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS is set, but external "
        "third-party Python does not execute in-process. Third-party manifest "
        "bootstrap remains unsupported, and plugin nodes use the isolated "
        "plugin-host boundary."
    )


def _initialize_runtime_bootstrap() -> None:
    try:
        active_profile = get_active_plugin_profile()
        boot_mode = resolve_boot_mode_for_profile(active_profile)
        _resolved_boot_order = get_plugin_boot_order()
        if _resolved_boot_order:
            logger.info("Boot order resolved: %s", " -> ".join(_resolved_boot_order))
        load_plugins()
        validate_bootstrap_manifest(registry)
        _publish_boot_runtime_state()
        trusted_python = trusted_python_execution_summary()
        if trusted_python["present"]:
            logger.info(
                "Trusted in-process Python inventory: manifest_modules=%d "
                "bootstrap_registrations=%d plugin_nodes=%d owner_classes=%s",
                trusted_python["manifest_module_count"],
                trusted_python["bootstrap_registration_count"],
                trusted_python["plugin_node_count"],
                ",".join(trusted_python["owner_classes_present"]) or "none",
            )
        else:
            logger.info("Trusted in-process Python inventory: no trusted extension code loaded.")
        if boot_mode == RUNTIME_ONLY_BOOT_MODE:
            logger.info(
                "Startup mode selected: %s -> profile %s (runtime boot without app plugins).",
                boot_mode,
                active_profile,
            )
        else:
            logger.info(
                "Startup mode selected: %s -> profile %s (%d registered app plugin%s).",
                boot_mode,
                active_profile,
                len(get_registered_apps()),
                "" if len(get_registered_apps()) == 1 else "s",
            )
    except BootstrapDependencyError as exc:
        logger.critical("Bootstrap dependency validation failed:\n%s", exc)
        raise RuntimeError(str(exc)) from exc
    except RuntimeError:
        raise
    except Exception as exc:
        logger.critical("Runtime bootstrap failed: %s", exc)
        raise RuntimeError(f"Runtime bootstrap failed: {exc}") from exc


_initialize_runtime_bootstrap()


def _check_runtime_schema() -> None:
    """Warn at startup if the runtime-owned schema does not match packaged metadata."""
    try:
        engine = create_engine(settings.DATABASE_URL)
        report = ensure_runtime_schema(engine, allow_bootstrap=False)
        if report.ok:
            logger.info("[startup] Runtime-owned schema matches packaged metadata.")
        else:
            logger.warning("[startup] Runtime schema check failed: %s", report.summary())
    except Exception as exc:
        logger.warning("[startup] Could not verify runtime schema: %s", exc)

def _ensure_dev_api_key():
    try:
        from AINDY.platform_layer.api_key_service import hash_key
        from AINDY.db.models.api_key import PlatformAPIKey
        from AINDY.db.models.user import User   # âœ… ADD THIS
        import uuid

        db = SessionLocal()
        try:
            raw_key = settings.AINDY_API_KEY
            if not raw_key:
                logger.warning("No AINDY_API_KEY set; skipping dev key bootstrap")
                return

            key_hash = hash_key(raw_key)

            existing = db.query(PlatformAPIKey).filter_by(key_hash=key_hash).first()
            if existing:
                user = db.query(User).filter(User.id == existing.user_id).first()
                if user and not user.is_admin:
                    user.is_admin = True
                    db.commit()
                    logger.info("Existing dev key user elevated to admin.")
                logger.info("Dev API key already exists.")
                return

            # ðŸ”¥ ensure a valid user exists
            user = db.query(User).first()
            if not user:
                user = User(
                    id=uuid.uuid4(),
                    email="dev@aindy.local",
                    hashed_password="dev",
                    is_active=True,
                    is_admin=True,
                )
                db.add(user)
                db.commit()
                logger.info("Dev user created.")
            elif not user.is_admin:
                user.is_admin = True
                db.commit()
                logger.info("Dev user elevated to admin.")

            dev_key = PlatformAPIKey(
                key_hash=key_hash,
                key_prefix=raw_key[:12],
                name="dev-key",
                user_id=user.id,
                scopes=["platform.admin"],
                is_active=True,
            )

            db.add(dev_key)
            db.commit()
            logger.info("Dev API key created and registered.")

        finally:
            db.close()

    except Exception as e:
        logger.warning(f"Dev API key bootstrap skipped (non-fatal): {e}")


def _check_redis_available() -> bool:
    return check_redis_available(use_cache=False)


def _enforce_redis_startup_guard() -> None:
    if settings.is_testing:
        return
    if not event_bus_required():
        return
    if not settings.REDIS_URL:
        raise RuntimeError(
            "REDIS_URL is required in non-development deployments. "
            "Set REDIS_URL in your environment or set "
            "AINDY_REQUIRE_REDIS=false to allow single-instance mode."
        )
    if not _check_redis_available():
        raise RuntimeError(
            "Redis is configured but not reachable at startup. "
            "Verify REDIS_URL and Redis availability before starting."
        )
    logger.info("[startup] Redis connectivity verified.")


def _enforce_event_bus_startup_guard() -> None:
    if settings.is_testing:
        return
    if not event_bus_required():
        return
    if os.getenv("AINDY_EVENT_BUS_ENABLED", "true").lower() in {"0", "false", "no", "off"}:
        raise RuntimeError(
            "AINDY_EVENT_BUS_ENABLED=false is not permitted when Redis-backed deployment "
            "contracts are required. Enable the event bus for production-safe WAIT/RESUME behavior."
        )


def _enforce_cache_backend_coherence() -> None:
    """
    Reject configurations where requires_redis=True but the cache backend
    is set to memory. Two instances cannot share a memory cache; one would
    silently serve stale data to the other's clients.
    """
    if settings.is_testing:
        return
    if not settings.requires_redis:
        return

    cache_backend = settings.AINDY_CACHE_BACKEND.lower()

    if cache_backend == "memory":
        message = (
            "AINDY_CACHE_BACKEND=memory is not permitted when Redis is required "
            f"(ENV={settings.ENV!r}, AINDY_REQUIRE_REDIS={settings.AINDY_REQUIRE_REDIS}). "
            "Multiple instances cannot share an in-memory cache â€” each instance would "
            "serve inconsistent data. "
            "Set AINDY_CACHE_BACKEND=redis and provide REDIS_URL, "
            "or set AINDY_CACHE_BACKEND=off to explicitly disable caching."
        )
        if settings.is_prod:
            raise RuntimeError(message)
        logger.warning("[startup] Cache backend misconfiguration: %s", message)

    if cache_backend == "redis" and not settings.REDIS_URL:
        if not settings.is_prod:
            logger.warning(
                "[startup] AINDY_CACHE_BACKEND=redis but REDIS_URL is not set. "
                "Caching will be disabled. Set REDIS_URL or change "
                "AINDY_CACHE_BACKEND=off to suppress this warning."
            )


def _check_worker_presence(log) -> bool:
    """
    Warn at startup when EXECUTION_MODE=distributed but no worker heartbeat is detected.

    This is a non-fatal advisory check. The server starts regardless â€” but operators
    need to know that jobs will queue silently if no worker is running.
    """
    from AINDY.config import settings

    if not settings.REDIS_URL:
        log.error(
            "[startup] EXECUTION_MODE=distributed requires REDIS_URL. "
            "Jobs will fail to enqueue. Set REDIS_URL or change EXECUTION_MODE=thread."
        )
        return False

    heartbeat_key = "aindy:worker:heartbeat"
    try:
        import redis as _redis

        client = _redis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
        last_beat = client.get(heartbeat_key)
        if last_beat is None:
            log.warning(
                "[startup] EXECUTION_MODE=distributed: no worker heartbeat found in Redis "
                "(key=%s). If no worker process is running, enqueued jobs will not be "
                "processed. Start a worker with: "
                "WORKER_CONCURRENCY=1 python -m AINDY.worker.worker_loop",
                heartbeat_key,
            )
            return False
        else:
            log.info(
                "[startup] Worker heartbeat detected (last_beat=%s).", last_beat.decode()
            )
            return True
    except Exception as exc:
        log.warning(
            "[startup] Could not check worker heartbeat (Redis error: %s). "
            "If EXECUTION_MODE=distributed, ensure a worker process is running.",
            exc,
        )
        return False


def _check_nodus_importable() -> tuple[bool, str]:
    """Return whether the Nodus VM is importable plus a detail string."""
    try:
        import nodus.runtime.embedding as _nodus_emb
        if not hasattr(_nodus_emb, "NodusRuntime"):
            return False, "nodus package installed but NodusRuntime not found"
        return True, f"nodus {getattr(__import__('nodus'), '__version__', 'unknown')}"
    except ImportError as exc:
        return False, f"nodus package not importable: {exc}. Run: pip install -r AINDY/requirements.txt"


def _enforce_nodus_gate() -> None:
    """Enforce Nodus availability when any registered flow node requires it."""
    nodus_available, nodus_detail = _check_nodus_importable()

    from AINDY.runtime.flow_engine import NODE_REGISTRY as _NODE_REGISTRY

    registered_nodus_nodes = sorted(
        name
        for name in _NODE_REGISTRY
        if name == "nodus.execute" or name.startswith("nodus.")
    )

    if not registered_nodus_nodes:
        if not nodus_available:
            logger.info("[startup] Nodus VM not available; no Nodus nodes registered, skipping.")
        return

    if nodus_available:
        logger.info(
            "[startup] Nodus VM verified for %d registered nodus.* node(s).",
            len(registered_nodus_nodes),
        )
        return

    message = (
        "[startup] Registered Nodus nodes require the Nodus VM, but it is unavailable. "
        f"Registered nodes: {registered_nodus_nodes}. "
        f"{nodus_detail}. "
        "Run: pip install -r AINDY/requirements.txt to install the nodus package."
    )
    if settings.is_prod:
        raise RuntimeError(message)
    logger.warning(message)


def _verify_flow_engines_started() -> None:
    """Log and validate the dual-engine runtime registration state."""
    from AINDY.runtime import get_engine_status, verify_engine_registration

    status = verify_engine_registration()
    logger.info(
        "[startup] Flow engines ready: dag_nodes=%d nodus_nodes=%d nodus_available=%s",
        status["dag_engine"]["registered_nodes"],
        status["nodus_engine"]["registered_nodes"],
        status["nodus_engine"]["available"],
    )


def _verify_required_syscalls_registered() -> None:
    """Verify that required syscalls are present after bootstrap."""
    from AINDY.kernel.syscall_registry import get_registered_syscalls
    from AINDY.platform_layer.registry import get_required_syscalls

    _required_syscalls = get_required_syscalls()
    if not _required_syscalls:
        return

    _registered_syscalls = set(get_registered_syscalls())
    _missing_syscalls = [
        name for name in _required_syscalls if name not in _registered_syscalls
    ]
    if _missing_syscalls:
        _syscall_message = (
            "[startup] Required syscalls missing after bootstrap: %s. "
            "Syscall-dependent flows will fail at runtime. "
            "Check that domain bootstrap modules loaded without errors."
        )
        if settings.is_prod and not settings.is_testing:
            logger.error(_syscall_message, _missing_syscalls)
            raise RuntimeError(
                f"Required syscalls not registered after bootstrap: {_missing_syscalls}"
            )
        logger.warning(_syscall_message, _missing_syscalls)
    else:
        logger.info(
            "[startup] Syscall registration verified: %d required syscall(s) present.",
            len(_required_syscalls),
        )


def _log_async_job_capacity_advisory() -> None:
    """Log startup guidance for async job capacity in thread mode."""
    if settings.is_testing:
        return
    if not settings.AINDY_JOB_WARN_CAPACITY:
        return
    if settings.EXECUTION_MODE != "thread":
        return

    _pool_size = settings.AINDY_ASYNC_JOB_WORKERS
    _queue_max = settings.AINDY_ASYNC_QUEUE_MAXSIZE
    _ai_job_duration_s = 15
    _throughput = _pool_size / _ai_job_duration_s

    if _pool_size < 8:
        logger.warning(
            "[startup] Thread pool is small for AI workloads: "
            "AINDY_ASYNC_JOB_WORKERS=%d. At ~%ds/job this sustains "
            "%.1f jobs/second. Recommend at least 8 workers, or switch "
            "to EXECUTION_MODE=distributed for multi-user deployments.",
            _pool_size,
            _ai_job_duration_s,
            _throughput,
        )
    else:
        logger.info(
            "[startup] Thread pool configured: workers=%d queue_max=%d "
            "(estimated throughput=%.1f jobs/s at 15s/job). "
            "For multi-user or high-throughput deployments, consider "
            "EXECUTION_MODE=distributed.",
            _pool_size,
            _queue_max,
            _throughput,
        )

    if settings.AINDY_ASYNC_MAX_CONCURRENT_PER_USER == 0:
        logger.warning(
            "[startup] AINDY_ASYNC_MAX_CONCURRENT_PER_USER=0 (no per-user cap). "
            "A single user can exhaust the full thread pool. "
            "Set AINDY_ASYNC_MAX_CONCURRENT_PER_USER=2 to enforce fairness."
        )


def _cache_behavior_mode() -> str:
    if settings.is_testing or os.getenv("PYTEST_CURRENT_TEST"):
        return "testing"
    if settings.is_dev:
        return "development"
    return "production"


def _update_db_pool_metrics() -> None:
    """Scrape SQLAlchemy pool stats into Prometheus gauges."""
    try:
        from AINDY.db.database import get_pool_status
        from AINDY.platform_layer.metrics import (
            db_pool_exhaustion_events_total,
            db_pool_checkedout,
            db_pool_overflow,
            db_pool_pressure,
            db_pool_size,
        )

        global _pool_was_near_exhaustion

        status = get_pool_status()
        pool_size = status.get("pool_size", 0)
        checkedout = status.get("checkedout", 0)
        overflow = status.get("overflow", 0)
        db_pool_size.set(pool_size)
        db_pool_checkedout.set(checkedout)
        db_pool_overflow.set(overflow)

        capacity = settings.DB_POOL_SIZE + settings.DB_MAX_OVERFLOW
        pressure = checkedout / capacity if capacity > 0 else 0.0
        db_pool_pressure.set(pressure)

        threshold = 0.8
        near_exhaustion = pressure >= threshold
        if near_exhaustion and not _pool_was_near_exhaustion:
            db_pool_exhaustion_events_total.inc()
            logger.warning(
                "DB pool near exhaustion: checkedout=%s pool_size=%s overflow=%s pressure=%.3f threshold=%.1f",
                checkedout,
                pool_size,
                overflow,
                pressure,
                threshold,
            )
            emit_observability_event(
                event_type="db_pool_near_exhaustion",
                payload={
                    "checkedout": checkedout,
                    "pool_size": pool_size,
                    "overflow": overflow,
                    "pressure": round(pressure, 3),
                    "threshold": threshold,
                },
            )
            try:
                from AINDY.db.database import SessionLocal as _SL
                from AINDY.core.system_event_service import emit_system_event as _emit

                _db = _SL()
                try:
                    _emit(
                        db=_db,
                        event_type="platform.db_pool.near_exhaustion",
                        payload={"checkedout": checkedout, "pressure": round(pressure, 3)},
                        required=False,
                    )
                finally:
                    _db.close()
            except Exception as _exc:
                logger.debug("db_pool_exhaustion event emit failed (non-fatal): %s", _exc)
        _pool_was_near_exhaustion = near_exhaustion
    except Exception as exc:
        logger.warning("DB pool metrics scrape failed (non-fatal): %s", exc)


def _remaining_shutdown_budget(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _initialize_cache_backend() -> str:
    """Initialize FastAPICache with explicit multi-instance semantics.

    Returns one of: ``redis``, ``memory``, ``disabled``.
    """
    cache_backend = settings.AINDY_CACHE_BACKEND.lower()
    behavior_mode = _cache_behavior_mode()

    if behavior_mode == "testing":
        FastAPICache.init(InMemoryBackend(), prefix="fastapi-cache")
        logger.info("Cache backend initialized: memory (testing mode)")
        return "memory"

    if cache_backend == "redis":
        try:
            from redis import asyncio as aioredis
            from fastapi_cache.backends.redis import RedisBackend
        except Exception as exc:
            if behavior_mode == "production":
                FastAPICache.init(NoOpCacheBackend(), prefix="fastapi-cache")
                logger.warning(
                    "Redis cache backend unavailable in production; caching disabled "
                    "to avoid instance-local divergence: %s",
                    exc,
                )
                return "disabled"
            logger.warning("Redis cache backend unavailable; falling back to memory cache: %s", exc)
            FastAPICache.init(InMemoryBackend(), prefix="fastapi-cache")
            return "memory"

        if not settings.REDIS_URL:
            if behavior_mode == "production":
                FastAPICache.init(NoOpCacheBackend(), prefix="fastapi-cache")
                logger.warning(
                    "[cache] AINDY_CACHE_BACKEND=redis but REDIS_URL is not set in production: "
                    "caching DISABLED. All cache misses will hit the database. "
                    "Set REDIS_URL to enable distributed caching."
                )
                return "disabled"
            logger.warning(
                "AINDY_CACHE_BACKEND=redis but REDIS_URL is not set; "
                "falling back to in-memory cache for local development."
            )
            FastAPICache.init(InMemoryBackend(), prefix="fastapi-cache")
            return "memory"

        try:
            redis = aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf8",
                decode_responses=True,
            )
            FastAPICache.init(RedisBackend(redis), prefix="fastapi-cache")
            logger.info("Cache backend initialized: redis")
            return "redis"
        except Exception as exc:
            if behavior_mode == "production":
                FastAPICache.init(NoOpCacheBackend(), prefix="fastapi-cache")
                logger.warning(
                    "Redis cache initialization failed in production; caching disabled "
                    "to avoid instance-local divergence: %s",
                    exc,
                )
                return "disabled"
            logger.warning(
                "Redis cache initialization failed; falling back to in-memory cache for development: %s",
                exc,
            )
            FastAPICache.init(InMemoryBackend(), prefix="fastapi-cache")
            return "memory"

    if cache_backend == "memory":
        if behavior_mode == "production":
            FastAPICache.init(NoOpCacheBackend(), prefix="fastapi-cache")
            logger.warning(
                "[cache] AINDY_CACHE_BACKEND=memory in production: caching DISABLED. "
                "In-memory caches are not safe for multi-instance deployments. "
                "Use AINDY_CACHE_BACKEND=redis with REDIS_URL, or "
                "AINDY_CACHE_BACKEND=off to silence this warning."
            )
            return "disabled"
        FastAPICache.init(InMemoryBackend(), prefix="fastapi-cache")
        logger.info("Cache backend initialized: memory")
        return "memory"

    if cache_backend in {"off", "disabled", "none"}:
        FastAPICache.init(NoOpCacheBackend(), prefix="fastapi-cache")
        logger.info("Cache backend initialized: disabled")
        return "disabled"

    raise RuntimeError(
        f"Unsupported AINDY_CACHE_BACKEND={settings.AINDY_CACHE_BACKEND!r}. "
        "Expected one of: redis, memory, off."
    )


def _validate_startup_config() -> None:
    deployment_profile = validate_api_deployment_profile()
    publish_api_runtime_state(
        process_role=PROCESS_ROLE_API,
        deployment_profile=deployment_profile["name"],
        deployment_profile_source=deployment_profile["source"],
        background_leadership_mode=deployment_profile["background_leadership_mode"],
    )
    # SECRET_KEY guard Ã¢â‚¬â€ reject insecure placeholder outside local dev/test
    _enforce_external_python_override_policy()
    _placeholder = "dev-secret-change-in-production"
    if settings.SECRET_KEY == _placeholder:
        if settings.requires_redis:
            raise RuntimeError(
                "SECRET_KEY is using the insecure default placeholder. "
                "Set a strong SECRET_KEY in your .env before running in non-development deployments."
            )
        else:
            logger.warning(
                "SECRET_KEY is using the insecure default placeholder. "
                "This is acceptable for local development but MUST be changed before production."
            )
    _min_key_length = 32
    if not settings.is_testing and len(settings.SECRET_KEY) < _min_key_length:
        if settings.requires_redis:
            raise RuntimeError(
                f"SECRET_KEY is too short ({len(settings.SECRET_KEY)} chars). "
                f"Minimum required: {_min_key_length} characters for non-development deployments."
            )
        else:
            logger.warning(
                "SECRET_KEY is short (%d chars). Use at least %d chars in production.",
                len(settings.SECRET_KEY), _min_key_length,
            )

    # Redis production guard
    _enforce_redis_startup_guard()
    _enforce_event_bus_startup_guard()
    _enforce_cache_backend_coherence()
    if not settings.is_testing and not os.getenv("PYTEST_CURRENT_TEST"):
        if not settings.REDIS_URL and not event_bus_required():
            _record_runtime_condition(
                code="redis_single_instance_mode",
                component="redis",
                classification=_SAFE_DEGRADED,
                detail=(
                    "REDIS_URL is unset. The runtime is operating in single-instance mode "
                    "and cross-instance coordination is unavailable."
                ),
                production_behavior="explicitly degraded",
            )
            logger.warning(
                "[startup] Redis is not configured (REDIS_URL is unset). "
                "Running in single-instance mode. WAIT/RESUME events will not "
                "propagate across multiple instances. Set REDIS_URL and "
                "AINDY_REQUIRE_REDIS=true for multi-instance deployments."
            )
        else:
            clear_api_runtime_condition("redis_single_instance_mode")
    logger.info(
        "Startup config: ENV=%s deployment_profile=%s execution_mode=%s redis_required=%s cache=%s",
        settings.ENV,
        deployment_profile["name"],
        settings.EXECUTION_MODE,
        event_bus_required(),
        settings.AINDY_CACHE_BACKEND,
    )


def _init_mongodb() -> None:
    try:
        ensure_mongo_ready(required=settings.MONGO_REQUIRED)
        mongo_status = ping_mongo()
        if mongo_status.get("status") != "ok":
            _record_runtime_condition(
                code="mongo_optional_unavailable",
                component="mongo",
                classification=_SAFE_DEGRADED,
                detail=str(mongo_status.get("reason") or "MongoDB unavailable"),
                production_behavior="explicitly degraded",
            )
            logger.warning(
                "MongoDB unavailable at startup â€” social features degraded: %s",
                mongo_status.get("reason"),
            )
        else:
            clear_api_runtime_condition("mongo_optional_unavailable")
    except Exception as exc:
        _handle_runtime_degradation(
            code="mongo_required_unavailable" if settings.MONGO_REQUIRED else "mongo_optional_unavailable",
            component="mongo",
            classification=_STARTUP_FATAL if settings.MONGO_REQUIRED else _SAFE_DEGRADED,
            detail=str(exc),
            production_message="MongoDB is required in production and could not be initialized.",
        )
        logger.warning("MongoDB init failed â€” social features degraded: %s", exc)


def _bootstrap_dev_api_key() -> None:
    if settings.ENV == "dev":
        _ensure_dev_api_key()


def _validate_queue_and_workers() -> None:
    if settings.is_prod and str(settings.OPENAI_API_KEY).startswith(_OPENAI_PROJECT_KEY_PREFIX):
        logger.warning(
            "OPENAI_API_KEY uses the project-key prefix in production; verify rotation after any potential exposure."
        )

    if not settings.is_testing and not os.getenv("PYTEST_CURRENT_TEST"):
        backend = validate_queue_backend()
        if getattr(backend, "degraded", False):
            detail = str(
                getattr(backend, "fallback_reason", None)
                or "Queue backend is running in degraded fallback mode."
            )
            _handle_runtime_degradation(
                code="queue_backend_fallback",
                component="queue",
                classification=_UNSAFE_DEGRADED if event_bus_required() else _SAFE_DEGRADED,
                detail=detail,
                production_message=(
                    "Queue backend fell back to an in-memory transport. "
                    "Production requires the configured durable backend before startup."
                ),
            )
        else:
            clear_api_runtime_condition("queue_backend_fallback")
    if (
        not settings.is_testing
        and not os.getenv("PYTEST_CURRENT_TEST")
        and event_bus_required()
    ):
        if not _check_worker_presence(logger):
            _handle_runtime_degradation(
                code="distributed_worker_unavailable",
                component="worker",
                classification=_UNSAFE_DEGRADED,
                detail=(
                    "Distributed API profile requires at least one healthy worker heartbeat "
                    "before startup."
                ),
                production_message=(
                    "Distributed API startup blocked: no worker heartbeat detected. "
                    "Start a worker process before bringing the API into service."
                ),
            )
        else:
            clear_api_runtime_condition("distributed_worker_unavailable")
    try:
        from AINDY.kernel.resource_manager import get_resource_manager as _get_rm
        from AINDY.platform_layer.metrics import quota_redis_mode as _quota_mode

        _quota_mode.set(1 if _get_rm().is_redis_mode() else 0)
    except Exception as exc:
        logger.debug("quota_redis_mode gauge init failed (non-fatal): %s", exc)
    _log_async_job_capacity_advisory()


def _enforce_schema_guard(db_factory) -> None:
    enforce_schema = os.getenv("AINDY_ENFORCE_SCHEMA", "true").lower() in {"1", "true", "yes"}
    allow_reconcile = os.getenv("AINDY_SCHEMA_RECONCILE", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    if not enforce_schema and settings.is_prod:
        raise RuntimeError(
            "AINDY_ENFORCE_SCHEMA=false is not permitted in production (ENV=production). "
            "Schema enforcement is a required safety gate. "
            "Initialize or reconcile the runtime-owned schema before deployment."
        )
    if enforce_schema and not settings.is_testing and not os.getenv("PYTEST_CURRENT_TEST"):
        db = db_factory()
        try:
            report = ensure_runtime_schema(
                db,
                allow_bootstrap=True,
                allow_reconcile=allow_reconcile,
            )
            if report.bootstrapped:
                logger.info("Bootstrapped runtime-owned schema from packaged metadata.")
            if report.reconciled:
                logger.info("Reconciled runtime-owned schema to packaged metadata.")
            if not report.ok:
                if report.state == SCHEMA_STATE_UPGRADE_REQUIRED:
                    logger.error(
                        "Runtime-owned schema upgrade required: %s drift_classes=%s",
                        report.summary(),
                        list(report.drift_classes),
                    )
                    raise RuntimeError(
                        "Runtime-owned schema requires an explicit additive reconcile. "
                        "Set AINDY_SCHEMA_RECONCILE=true for startup-time reconcile, or "
                        "inspect the current drift with "
                        f"{inspection_contract()['entrypoints']['module']}, "
                        "then upgrade the database out of band before startup."
                    )
                if report.state == SCHEMA_STATE_INCOMPATIBLE_MANUAL:
                    logger.error(
                        "Runtime-owned schema is incompatible with packaged metadata: %s "
                        "drift_classes=%s remediation_categories=%s",
                        report.summary(),
                        list(report.drift_classes),
                        list(report.remediation_categories),
                    )
                    raise RuntimeError(
                        "Runtime-owned schema is incompatible with packaged metadata. "
                        f"Inspect the drift with {inspection_contract()['entrypoints']['module']} "
                        "or GET /health, then perform the required offline migration or "
                        "manual repair before startup."
                    )
                logger.error("Runtime-owned schema is not ready: %s", report.summary())
                raise RuntimeError(report.summary())
        finally:
            db.close()
    elif not settings.is_testing and not os.getenv("PYTEST_CURRENT_TEST"):
        logger.warning(
            "[startup] Schema enforcement is DISABLED (AINDY_ENFORCE_SCHEMA=false). "
            "The server will start even if the runtime-owned schema is not ready. "
            "This is only safe for development. Do not use in production."
        )


def _start_background_services(db_factory) -> bool:
    enable_background = background_tasks_enabled()
    deployment_profile, _deployment_profile_source = resolve_api_deployment_profile()
    publish_api_runtime_state(
        background_enabled=enable_background,
        background_leadership_mode=background_leadership_mode_for_profile(
            deployment_profile
        ),
    )

    startup_results = emit_event(
        "system.startup",
        {"enable": enable_background, "log": logger, "source": "main"},
    )
    is_leader = enable_background and all(result is not False for result in startup_results)
    scheduler_role = "disabled"
    if is_leader:
        scheduler_service.start()
        _sched = scheduler_service.get_scheduler()
        if not getattr(_sched, "running", False):
            raise RuntimeError(
                "APScheduler failed to start. Check apscheduler installation."
            )
        _update_db_pool_metrics()
        _sched.add_job(
            _update_db_pool_metrics,
            trigger="interval",
            seconds=30,
            id="db_pool_metrics_tick",
            name="DB pool metrics tick",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        watchdog_scan = __import__(
            "AINDY.agents.stuck_run_" "watchdog",
            fromlist=["watchdog_scan"],
        ).watchdog_scan
        _sched.add_job(
            watchdog_scan,
            trigger="interval",
            minutes=settings.AINDY_WATCHDOG_INTERVAL_MINUTES,
            id="stuck_run_watchdog",
            name="Stuck-run watchdog",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        logger.info(
            "[startup] Stuck-run watchdog registered: interval=%dm threshold=%dm",
            settings.AINDY_WATCHDOG_INTERVAL_MINUTES,
            settings.STUCK_RUN_THRESHOLD_MINUTES,
        )
        scheduler_role = "leader"
    elif enable_background:
        scheduler_role = "follower"
    publish_api_runtime_state(scheduler_role=scheduler_role)

    if not settings.is_testing and not os.getenv("PYTEST_CURRENT_TEST"):
        from AINDY.core.request_metric_writer import get_writer as get_metric_writer

        get_metric_writer().start()
    try:
        from AINDY.platform_layer.async_job_service import start_async_job_service
        from AINDY.memory.memory_ingest_service import configure_memory_ingest_queue

        start_async_job_service()
        configure_memory_ingest_queue().start()
    except Exception as exc:
        logger.warning("Async shutdown services startup failed: %s", exc)

    return is_leader


def _register_domain_handlers() -> None:
    # Register domain syscall handlers (must come before flow registration)
    from AINDY.kernel.syscall_handlers import register_all_domain_handlers
    register_all_domain_handlers()
    _verify_required_syscalls_registered()


def _register_flow_engine() -> None:
    # Register runtime-owned platform flows first, then let app-plugin
    # bootstrap callbacks register app/domain flows through the registry
    # boundary.
    from AINDY.runtime.flow_definitions import register_all_flows

    register_all_flows()
    registry.register_flows()
    _enforce_nodus_gate()
    _verify_flow_engines_started()

    # Verify that domain-declared required flow nodes were actually registered â€”
    # silent failures in flow modules can otherwise produce a running server with
    # a broken flow graph.
    from AINDY.platform_layer.registry import get_required_flow_nodes
    from AINDY.runtime.flow_engine import NODE_REGISTRY as _NODE_REGISTRY
    _required_nodes = get_required_flow_nodes()
    _missing_nodes = [n for n in _required_nodes if n not in _NODE_REGISTRY]
    if _missing_nodes:
        message = (
            "[startup] Required flow nodes missing from registry after bootstrap: %s. "
            "Cross-domain flows will be unavailable for these nodes."
        )
        if settings.is_prod:
            logger.error(message, _missing_nodes)
            raise RuntimeError(
                f"Required flow nodes missing after bootstrap: {_missing_nodes}"
            )
        logger.error(message, _missing_nodes)


async def _restore_dynamic_registry(db_factory) -> None:
    # Restore dynamic platform registrations (flows, nodes, webhook subs) from DB.
    # Runs after register_all_flows() so static nodes are available for flow validation.
    if not settings.is_testing and not os.getenv("PYTEST_CURRENT_TEST"):
        from AINDY.platform_layer.platform_loader import (
            load_dynamic_registry,
            verify_restore_completeness,
        )
        _loader_db = db_factory()
        try:
            _loader_stats = load_dynamic_registry(_loader_db)
            logger.info(
                "Dynamic registry restored: nodes=%d flows=%d webhooks=%d",
                _loader_stats.get("nodes_loaded", 0),
                _loader_stats.get("flows_loaded", 0),
                _loader_stats.get("webhooks_loaded", 0),
            )
        except Exception as _loader_exc:
            _handle_runtime_degradation(
                code="dynamic_registry_restore_failed",
                component="plugin_restore",
                classification=_UNSAFE_DEGRADED,
                detail=str(_loader_exc),
                production_message=(
                    "Dynamic registry restore failed. Runtime extensions were not restored "
                    "from the database."
                ),
            )
            logger.warning("Dynamic registry restore failed (non-fatal): %s", _loader_exc)
        finally:
            _loader_db.close()
        _restore_verify_db = db_factory()
        try:
            _restore_result = await verify_restore_completeness(_restore_verify_db)
            logger.info(
                "Registry restore complete: flows=%d/%d nodes=%d/%d webhooks=%d/%d",
                _restore_result["flows"]["registry_count"],
                _restore_result["flows"]["db_count"],
                _restore_result["nodes"]["registry_count"],
                _restore_result["nodes"]["db_count"],
                _restore_result["webhooks"]["registry_count"],
                _restore_result["webhooks"]["db_count"],
            )
            if not _restore_result["all_ok"]:
                _handle_runtime_degradation(
                    code="dynamic_registry_restore_incomplete",
                    component="plugin_restore",
                    classification=_UNSAFE_DEGRADED,
                    detail=(
                        "Persisted registry state did not fully restore: "
                        f"flows={_restore_result['flows']['registry_count']}/{_restore_result['flows']['db_count']} "
                        f"nodes={_restore_result['nodes']['registry_count']}/{_restore_result['nodes']['db_count']} "
                        f"webhooks={_restore_result['webhooks']['registry_count']}/{_restore_result['webhooks']['db_count']}"
                    ),
                    production_message=(
                        "Dynamic registry restore is incomplete. Refusing production startup "
                        "with missing runtime extensions."
                    ),
                )
                logger.error(
                    "Registry restore INCOMPLETE â€” some capabilities were not restored"
                )
            if _restore_result["all_ok"]:
                clear_api_runtime_condition("dynamic_registry_restore_failed")
                clear_api_runtime_condition("dynamic_registry_restore_incomplete")
        finally:
            _restore_verify_db.close()


def _validate_router_boundary() -> None:
    # Enforce execution boundary: no router may import services directly
    if not settings.is_testing and not os.getenv("PYTEST_CURRENT_TEST"):
        from AINDY.core.router_guard import RouterBoundaryViolation, validate_router_boundary
        try:
            validate_router_boundary()
        except RouterBoundaryViolation as _rbv:
            logger.error("EXECUTION BOUNDARY VIOLATED:\n%s", _rbv)
            raise RuntimeError(str(_rbv)) from _rbv


def _recover_stuck_runs(db_factory, enable_background: bool) -> None:
    # Sprint N+7: Recover any FlowRun/AgentRun rows stranded by prior crash
    if enable_background and not settings.is_testing and not os.getenv("PYTEST_CURRENT_TEST"):
        from AINDY.agents.stuck_run_service import scan_and_recover_stuck_runs
        _scan_db = db_factory()
        try:
            _scan_result = scan_and_recover_stuck_runs(
                _scan_db,
                staleness_minutes=settings.STUCK_RUN_THRESHOLD_MINUTES,
                include_wait_timeouts=True,
                return_stats=True,
            )
            _recovered = int(_scan_result.get("recovered", 0))
            _dead_lettered = int(_scan_result.get("dead_lettered", 0))
            if _recovered:
                logger.info("[startup] Stuck-run scan recovered %d run(s)", _recovered)
                try:
                    from AINDY.platform_layer.metrics import startup_recovery_runs_recovered_total

                    startup_recovery_runs_recovered_total.labels(
                        recovery_type="stuck_runs"
                    ).inc(_recovered)
                except Exception:
                    pass
            if _dead_lettered:
                logger.info(
                    "[startup] WAIT timeout scan dead-lettered %d run(s)",
                    _dead_lettered,
                )
        except Exception as _scan_exc:
            emit_recovery_failure("stuck_runs", _scan_exc, _scan_db, logger=logger)
        finally:
            _scan_db.close()


def _start_event_bus() -> None:
    # Distributed event bus: subscribe to Redis pub/sub on ALL instances so
    # that resume events emitted by any instance wake flows registered in this
    # instance's local _waiting dict.  Must start BEFORE rehydration so the
    # thread is ready when the first event arrives.  Non-fatal: if Redis is
    # unavailable the system falls back to local-only notify_event behaviour.
    if not settings.is_testing and not os.getenv("PYTEST_CURRENT_TEST"):
        try:
            from AINDY.kernel.event_bus import get_event_bus
            get_event_bus().start_subscriber()
            publish_api_runtime_state(event_bus_ready=True)
            clear_api_runtime_condition("event_bus_subscriber_unavailable")
        except Exception as _bus_exc:
            publish_api_runtime_state(event_bus_ready=False)
            if event_bus_required():
                raise RuntimeError(
                    f"Event bus subscriber failed to start: {_bus_exc}"
                ) from _bus_exc
            _record_runtime_condition(
                code="event_bus_subscriber_unavailable",
                component="event_bus",
                classification=_SAFE_DEGRADED,
                detail=str(_bus_exc),
                production_behavior="explicitly degraded",
            )
            logger.warning(
                "[startup] Event bus subscriber failed to start (non-fatal): %s", _bus_exc
            )
        try:
            from AINDY.kernel.event_bus import get_event_bus

            _bus = get_event_bus()
            _bus_status = _bus.get_status()
            if _bus_status.get("mode") == "local-only" and not event_bus_required():
                _record_runtime_condition(
                    code="event_bus_local_only",
                    component="event_bus",
                    classification=_SAFE_DEGRADED,
                    detail=(
                        "WAIT/RESUME propagation is local-only. Cross-instance resume delivery "
                        "is unavailable without Redis."
                    ),
                    production_behavior="explicitly degraded",
                )
                logger.warning(
                    "[startup] WAIT/RESUME is operating in LOCAL-ONLY mode. "
                    "Flows that enter WAIT on one instance CANNOT be resumed by "
                    "events received on a different instance. "
                    "For multi-instance deployments, set REDIS_URL and ensure "
                    "the event bus subscriber is running (AINDY_EVENT_BUS_ENABLED=true)."
                )
            elif _bus_status.get("mode") == "cross-instance":
                clear_api_runtime_condition("event_bus_local_only")
                logger.info(
                    "[startup] WAIT/RESUME propagation: cross-instance (Redis pub/sub active)."
                )
        except Exception as _status_exc:
            logger.debug("[startup] Could not check event bus propagation mode: %s", _status_exc)


def _rehydrate_waiting_state(db_factory, is_testing: bool) -> None:
    # WAIT rehydration: re-register all waiting EUs with the SchedulerEngine.
    # Must run after SchedulerEngine is initialised (above) and after the
    # stuck-run scan (which may transition some EUs out of waiting status).
    if not settings.is_testing and not os.getenv("PYTEST_CURRENT_TEST"):
        from AINDY.core.wait_rehydration import rehydrate_waiting_eus
        _rehydrate_db = db_factory()
        try:
            _n_rehydrated = rehydrate_waiting_eus(_rehydrate_db)
            if _n_rehydrated:
                logger.info("[startup] WAIT rehydration registered %d EU(s)", _n_rehydrated)
            clear_api_runtime_condition("wait_eus_rehydration_failed")
        except Exception as _rehydrate_exc:
            _handle_runtime_degradation(
                code="wait_eus_rehydration_failed",
                component="rehydration",
                classification=_UNSAFE_DEGRADED,
                detail=str(_rehydrate_exc),
                production_message=(
                    "WAIT execution-unit rehydration failed. Pending waits may be stranded."
                ),
            )
            emit_recovery_failure("wait_eus", _rehydrate_exc, _rehydrate_db, logger=logger)
        finally:
            _rehydrate_db.close()

    # FlowRun WAIT rehydration: reconstruct PersistentFlowRunner callbacks for
    # all FlowRuns with status="waiting" so they can be resumed when their
    # event fires.  Must run after register_all_flows() so FLOW_REGISTRY is
    # populated, and after EU rehydration so the scheduler entry for the same
    # run_id already has the EU-level callback when we add the flow callback.
    if not settings.is_testing and not os.getenv("PYTEST_CURRENT_TEST"):
        from AINDY.core.flow_run_rehydration import rehydrate_waiting_flow_runs
        _flow_rehydrate_db = db_factory()
        try:
            _n_flow_rehydrated = rehydrate_waiting_flow_runs(_flow_rehydrate_db)
            if _n_flow_rehydrated:
                logger.info(
                    "[startup] FlowRun rehydration registered %d run(s)", _n_flow_rehydrated
                )
            clear_api_runtime_condition("flow_run_rehydration_failed")
        except Exception as _flow_rehydrate_exc:
            _handle_runtime_degradation(
                code="flow_run_rehydration_failed",
                component="rehydration",
                classification=_UNSAFE_DEGRADED,
                detail=str(_flow_rehydrate_exc),
                production_message=(
                    "FlowRun rehydration failed. Waiting flows may not resume safely."
                ),
            )
            emit_recovery_failure(
                "flow_runs", _flow_rehydrate_exc, _flow_rehydrate_db, logger=logger
            )
        finally:
            _flow_rehydrate_db.close()

    if not settings.is_testing and not os.getenv("PYTEST_CURRENT_TEST"):
        try:
            from AINDY.kernel.scheduler_engine import get_scheduler_engine
            from AINDY.kernel.event_bus import get_event_bus
            get_scheduler_engine().mark_rehydration_complete()
            get_event_bus().drain_buffered_events()
            clear_api_runtime_condition("event_bus_rehydration_drain_failed")
        except Exception as _drain_exc:
            _handle_runtime_degradation(
                code="event_bus_rehydration_drain_failed",
                component="rehydration",
                classification=_UNSAFE_DEGRADED,
                detail=str(_drain_exc),
                production_message=(
                    "Buffered event drain after rehydration failed. Resume events may be lost."
                ),
            )
            emit_recovery_failure("event_drain", _drain_exc, None, logger=logger)
    else:
        try:
            from AINDY.kernel.scheduler_engine import get_scheduler_engine

            get_scheduler_engine().mark_rehydration_complete()
        except Exception as exc:
            logger.debug("[startup] Test-mode scheduler rehydration mark skipped: %s", exc)


def _run_startup_hooks(db_factory) -> None:
    run_startup_hooks(
        {
            "is_testing": settings.is_testing or bool(os.getenv("PYTEST_CURRENT_TEST")),
            "log": logger,
            "session_factory": db_factory,
            "source": "main",
        }
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    reset_runtime_state()
    deployment_profile, deployment_profile_source = resolve_api_deployment_profile()
    publish_api_runtime_state(
        process_role=PROCESS_ROLE_API,
        startup_complete=False,
        background_enabled=False,
        scheduler_role="disabled",
        background_leadership_mode=background_leadership_mode_for_profile(
            deployment_profile
        ),
        event_bus_ready=False,
        boot_mode=resolve_boot_mode_for_profile(get_active_plugin_profile()),
        boot_profile=get_active_plugin_profile(),
        boot_profile_source=get_active_plugin_profile_source(),
        deployment_profile=deployment_profile,
        deployment_profile_source=deployment_profile_source,
        app_plugins_loaded=bool(get_registered_apps()),
        app_plugin_count=len(get_registered_apps()),
    )
    # Phase 1: validate startup configuration and deployment guards.
    _validate_startup_config()
    init_otel(service_name="aindy")
    if _OTEL_FASTAPI_AVAILABLE and not getattr(app.state, "_otel_instrumented", False):
        FastAPIInstrumentor.instrument_app(app)
        app.state._otel_instrumented = True
        logger.info("[otel] FastAPI instrumented")
    # Cache backend selection.
    cache_mode = _initialize_cache_backend()
    logger.info("Cache behavior mode: %s", cache_mode)
    # Phase 3: initialize MongoDB and warn on degraded mode.
    _init_mongodb()
    # Phase 4: validate queue backend and worker capacity.
    _validate_queue_and_workers()
    # Phase 5: bootstrap or validate the runtime-owned schema before DB writes.
    _enforce_schema_guard(SessionLocal)
    # Phase 6: bootstrap development API key state.
    _bootstrap_dev_api_key()
    # Phase 7: start background services and determine background role.
    _start_background_services(SessionLocal)
    enable_background = background_tasks_enabled()
    # Phase 8: register domain syscall handlers.
    _register_domain_handlers()
    # Phase 9: register flow engine definitions and nodes.
    _register_flow_engine()
    # Phase 10: restore dynamic registry state from the database.
    await _restore_dynamic_registry(SessionLocal)
    # Phase 11: validate router execution boundaries.
    _validate_router_boundary()
    # Phase 12: recover stuck runs.
    _recover_stuck_runs(SessionLocal, enable_background)
    # Phase 13: start distributed event bus subscription.
    _start_event_bus()
    # Phase 14: rehydrate WAIT state and drain buffered events.
    _rehydrate_waiting_state(SessionLocal, settings.is_testing)
    # Phase 15: run startup hooks.
    _run_startup_hooks(SessionLocal)
    publish_api_runtime_state(startup_complete=True)

    yield
    # --- Shutdown ---
    shutdown_deadline = time.monotonic() + float(settings.AINDY_SHUTDOWN_TIMEOUT_SECONDS)
    publish_api_runtime_state(startup_complete=False, event_bus_ready=False)
    emit_event("system.shutdown", {"log": logger, "source": "main"})
    try:
        from AINDY.platform_layer.async_job_service import stop_async_job_service

        stop_async_job_service(
            timeout_seconds=_remaining_shutdown_budget(shutdown_deadline),
            reopen=settings.is_testing,
        )
    except Exception as exc:
        logger.warning("Async job shutdown failed (non-fatal): %s", exc)
    try:
        from AINDY.memory.memory_ingest_service import configure_memory_ingest_queue

        configure_memory_ingest_queue().stop(
            timeout=_remaining_shutdown_budget(shutdown_deadline),
            drain=True,
        )
    except Exception as exc:
        logger.warning("Memory ingest queue shutdown failed: %s", exc)
    try:
        from AINDY.core.request_metric_writer import get_writer as get_metric_writer

        get_metric_writer().stop(timeout=_remaining_shutdown_budget(shutdown_deadline))
    except Exception as exc:
        logger.warning("Request metric writer shutdown failed: %s", exc)
    try:
        scheduler_service.stop(
            timeout_seconds=_remaining_shutdown_budget(shutdown_deadline)
        )
    except Exception as exc:
        logger.warning("Scheduler shutdown failed (non-fatal): %s", exc)
    try:
        from AINDY.kernel.event_bus import get_event_bus

        get_event_bus().stop(timeout=_remaining_shutdown_budget(shutdown_deadline))
    except Exception as exc:
        logger.warning("Event bus shutdown failed (non-fatal): %s", exc)
    if settings.is_testing:
        try:
            from AINDY.kernel.scheduler_engine import get_scheduler_engine

            get_scheduler_engine().reset()
        except Exception as exc:
            logger.debug("Scheduler engine reset skipped during test teardown: %s", exc)
    try:
        from AINDY.db.mongo_setup import close_mongo_client

        close_mongo_client()
    except Exception as exc:
        logger.warning("MongoDB shutdown failed (non-fatal): %s", exc)
    try:
        from AINDY.db.database import engine

        if not settings.is_testing:
            engine.dispose()
    except Exception as exc:
        logger.warning("Database engine disposal failed (non-fatal): %s", exc)
    logger.info("shutdown complete")


