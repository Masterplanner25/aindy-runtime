import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from AINDY.core.execution_helper import execute_with_pipeline_sync
from AINDY.db.database import get_db
from AINDY.platform_layer.rate_limiter import limiter
from AINDY.auth.api_key_auth import Scopes
from AINDY.services.auth_service import enforce_api_key_scope, get_current_user, require_platform_admin_access

router = APIRouter(
    prefix="/observability",
    tags=["Observability"],
    dependencies=[Depends(require_platform_admin_access)],
)

# ── HTTP-SCOPE-GAP-1 / KEY-SCOPE-ESCALATION-1 — per-endpoint scopes on the /platform tree ──
#
# `require_platform_admin_access` on the parent router returns **any** authenticated API key
# unconditionally, on the stated assumption that "scope enforcement happens per-endpoint or
# per-syscall". For most of this tree it did not. Demonstrated: a `flow.read`-only key reached
# every route here, drained the dead-letter queue and **rotated the platform signing key**.
#
# For JWT callers nothing changes — the parent gate already required `is_admin`, and an admin
# session derives `platform.admin` and `webhook.manage`. Only API keys are newly constrained,
# which is the point.
_REQUIRE_PLATFORM_ADMIN = Depends(enforce_api_key_scope(Scopes.PLATFORM_ADMIN))


class DrainDlqRequest(BaseModel):
    max_items: int = Field(default=10, ge=1, le=100)
    requeue: bool = False


def _run_flow_observability(flow_name: str, payload: dict, db: Session, user_id: str):
    from AINDY.runtime.flow_engine import run_flow
    result = run_flow(flow_name, payload, db=db, user_id=user_id)
    if result.get("status") == "FAILED":
        error = result.get("error", "")
        if error.startswith("HTTP_"):
            parts = error.split(":", 1)
            code = int(parts[0].replace("HTTP_", ""))
            msg = parts[1] if len(parts) > 1 else error
            raise HTTPException(status_code=code, detail=msg)
        raise HTTPException(status_code=500, detail=error or f"{flow_name} failed")
    return result.get("data")


def _execute_observability(request: Request, route_name: str, handler, *, db: Session, user_id: str, input_payload=None):
    return execute_with_pipeline_sync(
        request=request,
        route_name=route_name,
        handler=handler,
        user_id=user_id,
        input_payload=input_payload,
        metadata={"db": db, "disable_memory_capture": True},
    )


@router.get("/llm/status")
@limiter.limit("60/minute")
def get_llm_status(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _scope: None = _REQUIRE_PLATFORM_ADMIN,
):
    from AINDY.platform_layer.deepseek_client import get_deepseek_circuit_breaker
    from AINDY.platform_layer.openai_client import get_openai_circuit_breaker

    user_id = str(current_user["sub"])

    def handler(ctx):
        openai_breaker = get_openai_circuit_breaker()
        deepseek_breaker = get_deepseek_circuit_breaker()
        return {
            "openai": {
                "state": openai_breaker.state.value,
                "failure_count": openai_breaker.failure_count,
            },
            "deepseek": {
                "state": deepseek_breaker.state.value,
                "failure_count": deepseek_breaker.failure_count,
            },
        }

    return _execute_observability(request, "observability_llm_status", handler, db=db, user_id=user_id)


@router.get("/rippletrace/status")
@limiter.limit("60/minute")
def get_rippletrace_status(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _scope: None = _REQUIRE_PLATFORM_ADMIN,
):
    user_id = str(current_user["sub"])

    def handler(ctx):
        from AINDY.platform_layer.registry import get_all_health_checks

        rippletrace_health = get_all_health_checks().get("rippletrace")
        if rippletrace_health is None:
            return {"status": "unknown", "engines": {}, "error": "rippletrace health check not registered"}
        payload = rippletrace_health() or {}
        return {
            "status": "healthy" if payload.get("status") == "ok" else payload.get("status", "unknown"),
            "engines": payload.get("engines") or {},
        }

    return _execute_observability(
        request,
        "observability_rippletrace_status",
        handler,
        db=db,
        user_id=user_id,
    )


# ------------------------------
# SCHEDULER STATUS
# ------------------------------
def _build_scheduler_status_payload(db: Session) -> dict:
    """Build scheduler status directly — no flow engine dependency."""
    from AINDY.agents.stuck_run_watchdog import get_last_scan_result
    from AINDY.config import settings
    from AINDY.platform_layer import scheduler_service
    from AINDY.platform_layer.registry import get_symbol

    # --- scheduler running state ---
    scheduler_running = False
    try:
        scheduler = scheduler_service.get_scheduler()
        scheduler_running = bool(getattr(scheduler, "running", False))
    except Exception:
        scheduler = None

    # --- tasks-domain leader lease (not available in platform-only profile) ---
    is_leader_fn = get_symbol("task_is_background_leader")
    if is_leader_fn is not None:
        try:
            is_leader = is_leader_fn()
        except Exception:
            is_leader = None
    else:
        is_leader = None

    lease = None
    if is_leader_fn is not None:
        try:
            from AINDY.db.models.background_task_lease import BackgroundTaskLease
            _lease_name = get_symbol("task_background_lease_name") or "task_background_runner"
            lease_row = db.query(BackgroundTaskLease).filter(
                BackgroundTaskLease.name == _lease_name
            ).first()
            if lease_row:
                lease = {
                    "owner_id": lease_row.owner_id,
                    "acquired_at": lease_row.acquired_at.isoformat() if lease_row.acquired_at else None,
                    "heartbeat_at": lease_row.heartbeat_at.isoformat() if lease_row.heartbeat_at else None,
                    "expires_at": lease_row.expires_at.isoformat() if lease_row.expires_at else None,
                }
        except Exception:
            pass

    result = {
        "observability_scheduler_status_result": {
            "scheduler_running": scheduler_running,
            "is_leader": is_leader,
            "lease": lease,
            "tasks_domain_available": is_leader_fn is not None,
        }
    }

    # --- stuck-run watchdog ---
    last_scan = get_last_scan_result()
    if scheduler is None or not scheduler_running:
        result["stuck_run_watchdog"] = {
            "registered": False,
            "next_run_time": None,
            "last_run_at": last_scan["last_run_at"],
            "last_recovered": last_scan["recovered"],
            "last_dead_lettered": last_scan["dead_lettered"],
            "last_had_error": last_scan["had_error"],
            "last_error_message": last_scan["error_message"],
            "recovery_sla_minutes": settings.AINDY_WATCHDOG_INTERVAL_MINUTES,
            "stuck_threshold_minutes": settings.STUCK_RUN_THRESHOLD_MINUTES,
        }
        return result

    job = None
    if callable(getattr(scheduler, "get_job", None)):
        job = scheduler.get_job("stuck_run_watchdog")
    elif callable(getattr(scheduler, "get_jobs", None)):
        job = next(
            (c for c in scheduler.get_jobs() if getattr(c, "id", None) == "stuck_run_watchdog"),
            None,
        )
    result["stuck_run_watchdog"] = {
        "registered": job is not None,
        "next_run_time": (
            job.next_run_time.isoformat()
            if job is not None and getattr(job, "next_run_time", None) is not None
            else None
        ),
        "interval_minutes": settings.AINDY_WATCHDOG_INTERVAL_MINUTES,
        "last_run_at": last_scan["last_run_at"],
        "last_recovered": last_scan["recovered"],
        "last_dead_lettered": last_scan["dead_lettered"],
        "last_had_error": last_scan["had_error"],
        "last_error_message": last_scan["error_message"],
        "recovery_sla_minutes": settings.AINDY_WATCHDOG_INTERVAL_MINUTES,
        "stuck_threshold_minutes": settings.STUCK_RUN_THRESHOLD_MINUTES,
    }
    return result


@router.get("/scheduler/status")
@limiter.limit("60/minute")
def get_scheduler_status(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _scope: None = _REQUIRE_PLATFORM_ADMIN,
):
    user_id = str(current_user["sub"])

    def handler(ctx):
        return _build_scheduler_status_payload(db)

    return _execute_observability(request, "observability_scheduler_status", handler, db=db, user_id=user_id)


# ------------------------------
# REQUEST METRICS
# ------------------------------
@router.get("/requests")
@limiter.limit("60/minute")
def get_request_metrics(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
    error_limit: int = Query(25, ge=1, le=200),
    window_hours: int = Query(24, ge=1, le=168),
    _scope: None = _REQUIRE_PLATFORM_ADMIN,
):
    user_id = str(current_user["sub"])
    def handler(ctx):
        return _run_flow_observability(
            "observability_requests",
            {"limit": limit, "error_limit": error_limit, "window_hours": window_hours},
            db, user_id,
        )
    return _execute_observability(request, "observability_requests", handler, db=db, user_id=user_id)


# ------------------------------
# DASHBOARD
# ------------------------------
@router.get("/dashboard")
@limiter.limit("60/minute")
def get_observability_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    window_hours: int = Query(24, ge=1, le=168),
    request_limit: int = Query(80, ge=1, le=200),
    event_limit: int = Query(60, ge=1, le=200),
    agent_limit: int = Query(30, ge=1, le=100),
    health_limit: int = Query(20, ge=1, le=100),
    _scope: None = _REQUIRE_PLATFORM_ADMIN,
):
    user_id = str(current_user["sub"])
    def handler(ctx):
        return _run_flow_observability(
            "observability_dashboard",
            {"window_hours": window_hours, "request_limit": request_limit, "event_limit": event_limit},
            db, user_id,
        )
    return _execute_observability(request, "observability_dashboard", handler, db=db, user_id=user_id)


# ------------------------------
# EXECUTION GRAPH
# ------------------------------
@router.get("/execution_graph/{trace_id}")
@limiter.limit("60/minute")
def get_execution_graph(
    request: Request,
    trace_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _scope: None = _REQUIRE_PLATFORM_ADMIN,
):
    user_id = str(current_user["sub"])
    def handler(ctx):
        return _run_flow_observability("observability_execution_graph", {"trace_id": trace_id}, db, user_id)
    return _execute_observability(request, "observability_execution_graph", handler, db=db, user_id=user_id,
                                  input_payload={"trace_id": trace_id})


@router.get("/queue/metrics")
@limiter.limit("60/minute")
def get_queue_metrics(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _scope: None = _REQUIRE_PLATFORM_ADMIN,
):
    from AINDY.core.distributed_queue import get_queue
    from AINDY.platform_layer.health_service import get_memory_ingest_queue_status
    from AINDY.worker.worker_loop import get_failure_rate_stats

    user_id = str(current_user["sub"])

    def handler(ctx):
        metrics = dict(get_queue().get_metrics())
        metrics.update(get_failure_rate_stats())
        metrics["memory_ingest_queue"] = get_memory_ingest_queue_status()
        return metrics

    return _execute_observability(
        request,
        "observability_queue_metrics",
        handler,
        db=db,
        user_id=user_id,
    )


@router.get("/dead-letter")
@limiter.limit("60/minute")
def list_dead_letter(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    user_id: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _scope: None = _REQUIRE_PLATFORM_ADMIN,
):
    from AINDY.agents.dead_letter_service import list_dead_lettered_runs

    caller_user_id = str(current_user["sub"])

    def handler(ctx):
        flows = list_dead_lettered_runs(db, limit=limit, user_id=user_id)
        return {"flows": flows, "count": len(flows)}

    return _execute_observability(
        request,
        "observability_dead_letter_list",
        handler,
        db=db,
        user_id=caller_user_id,
        input_payload={"limit": limit, "user_id": user_id},
    )


@router.get("/dead-letter/{flow_run_id}")
@limiter.limit("60/minute")
def get_dead_letter_run(
    request: Request,
    flow_run_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _scope: None = _REQUIRE_PLATFORM_ADMIN,
):
    caller_user_id = str(current_user["sub"])

    def handler(ctx):
        from AINDY.agents.dead_letter_service import _flow_run_to_dict
        from AINDY.db.models.flow_run import FlowRun

        run = db.query(FlowRun).filter(
            FlowRun.id == flow_run_id,
            FlowRun.status == "dead_letter",
        ).first()
        if not run:
            raise HTTPException(status_code=404, detail="Dead-lettered flow run not found")
        return _flow_run_to_dict(run)

    return _execute_observability(
        request,
        "observability_dead_letter_get",
        handler,
        db=db,
        user_id=caller_user_id,
        input_payload={"flow_run_id": flow_run_id},
    )


# ------------------------------
# SYSTEM STATE (connected apps + execution health)
# ------------------------------
@router.get("/system")
@limiter.limit("60/minute")
def get_system_state(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _scope: None = _REQUIRE_PLATFORM_ADMIN,
):
    user_id = str(current_user["sub"])

    def handler(ctx):
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import func

        from AINDY.db.models.agent_run import AgentRun
        from AINDY.db.models.execution_unit import ExecutionUnit
        from AINDY.db.models.flow_run import FlowRun
        from AINDY.platform_layer.registry import (
            get_all_health_checks,
            get_bootstrap_registrations,
            get_core_domains,
            get_degraded_domains,
            get_event_types,
            get_loaded_extensions,
            get_registered_apps,
            get_scheduled_jobs,
            iter_agent_tools,
            iter_syscalls,
        )

        registered_apps = get_registered_apps()
        registrations = get_bootstrap_registrations()
        degraded = set(get_degraded_domains())
        health_checks = get_all_health_checks()
        core_domains = get_core_domains()

        connected_apps = [
            {
                "name": app_name,
                "owner_class": registrations.get(app_name, {}).get("owner_class"),
                "trust_class": registrations.get(app_name, {}).get("trust_class"),
                "execution_model": registrations.get(app_name, {}).get("execution_model"),
                "dependencies": registrations.get(app_name, {}).get("dependencies", []),
                "module_name": registrations.get(app_name, {}).get("module_name"),
                "has_health_check": app_name in health_checks,
            }
            for app_name in registered_apps
        ]

        domain_health = [
            {"domain": d, "status": "degraded" if d in degraded else "healthy"}
            for d in sorted(core_domains)
        ]

        event_types = sorted(get_event_types())

        flow_rows = (
            db.query(FlowRun.status, func.count(FlowRun.id).label("cnt"))
            .group_by(FlowRun.status)
            .all()
        )
        flow_by_status = {r.status: r.cnt for r in flow_rows}

        agent_rows = (
            db.query(AgentRun.status, func.count(AgentRun.id).label("cnt"))
            .group_by(AgentRun.status)
            .all()
        )
        agent_by_status = {r.status: r.cnt for r in agent_rows}

        window_start = datetime.now(timezone.utc) - timedelta(hours=24)
        eu_rows = (
            db.query(
                ExecutionUnit.status,
                func.count(ExecutionUnit.id).label("cnt"),
                func.avg(ExecutionUnit.wall_time_ms).label("avg_ms"),
            )
            .filter(ExecutionUnit.created_at >= window_start)
            .group_by(ExecutionUnit.status)
            .all()
        )
        eu_total = sum(r.cnt for r in eu_rows)
        eu_failed = sum(r.cnt for r in eu_rows if r.status == "failed")
        eu_completed = sum(r.cnt for r in eu_rows if r.status == "completed")
        avg_ms_vals = [r.avg_ms for r in eu_rows if r.avg_ms is not None]
        eu_avg_ms = round(sum(avg_ms_vals) / len(avg_ms_vals)) if avg_ms_vals else 0

        return {
            "connected_apps": connected_apps,
            "domain_health": domain_health,
            "registry": {
                "syscall_count": sum(1 for _ in iter_syscalls()),
                "tool_count": sum(1 for _ in iter_agent_tools()),
                "extension_count": len(get_loaded_extensions()),
                "scheduled_job_count": len(get_scheduled_jobs()),
                "event_type_count": len(event_types),
                "event_types": event_types[:30],
            },
            "execution_summary": {
                "flow_runs": {
                    "total": sum(flow_by_status.values()),
                    "by_status": flow_by_status,
                },
                "agent_runs": {
                    "total": sum(agent_by_status.values()),
                    "by_status": agent_by_status,
                },
                "execution_units_24h": {
                    "total": eu_total,
                    "completed": eu_completed,
                    "failed": eu_failed,
                    "avg_wall_time_ms": eu_avg_ms,
                    "error_rate_pct": round(eu_failed / eu_total * 100, 1) if eu_total > 0 else 0.0,
                },
            },
        }

    return _execute_observability(
        request, "observability_system_state", handler, db=db, user_id=user_id
    )


@router.post("/queue/dlq/drain")
@limiter.limit("30/minute")
def drain_queue_dlq(
    request: Request,
    body: DrainDlqRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _scope: None = _REQUIRE_PLATFORM_ADMIN,
):
    from AINDY.worker.worker_loop import drain_dead_letters

    user_id = str(current_user["sub"])
    logger_payload = {
        "user_id": user_id,
        "max_items": body.max_items,
        "requeue": body.requeue,
    }

    def handler(ctx):
        return drain_dead_letters(
            db=db,
            max_items=body.max_items,
            requeue=body.requeue,
        )

    result = _execute_observability(
        request,
        "observability_queue_dlq_drain",
        handler,
        db=db,
        user_id=user_id,
        input_payload=body.model_dump(),
    )
    import logging
    logging.getLogger(__name__).info(
        "[Observability] queue_drain_dlq user_id=%s max_items=%s requeue=%s inspected=%s requeued=%s",
        logger_payload["user_id"],
        logger_payload["max_items"],
        logger_payload["requeue"],
        result.get("inspected"),
        result.get("requeued"),
    )
    return result

