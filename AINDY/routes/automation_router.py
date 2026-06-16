"""
automation_router.py — /automation/logs endpoints for the operator panel.

Plain DB-query handlers; no ExecutionPipeline required.
Auth: require_admin_principal (platform admin via JWT Bearer).

Endpoints:
  GET  /automation/logs                   — list logs with status/source/limit filters
  GET  /automation/logs/{log_id}          — single log detail
  POST /automation/logs/{log_id}/replay   — replay a failed/retrying log
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from AINDY.db.database import get_db
from AINDY.db.models.job_log import JobLog
from AINDY.services.auth_service import require_admin_principal

router = APIRouter(prefix="/automation", tags=["Automation"])


def _serialize_log(log: JobLog) -> dict:
    return {
        "id": log.id,
        "task_name": log.task_name,
        "source": log.source,
        "status": log.status,
        "attempt_count": log.attempt_count,
        "max_attempts": log.max_attempts,
        "error_message": log.error_message,
        "payload": log.payload,
        "result": log.result,
        "started_at": log.started_at.isoformat() if log.started_at else None,
        "completed_at": log.completed_at.isoformat() if log.completed_at else None,
        "created_at": log.created_at.isoformat() if log.created_at else None,
        "updated_at": log.updated_at.isoformat() if log.updated_at else None,
        "scheduled_for": log.scheduled_for.isoformat() if log.scheduled_for else None,
        "trace_id": log.trace_id,
        "user_id": str(log.user_id) if log.user_id else None,
    }


@router.get("/logs", response_model=None)
def list_automation_logs(
    request: Request,
    status: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin_principal),
):
    q = db.query(JobLog).order_by(JobLog.created_at.desc())
    if status:
        q = q.filter(JobLog.status == status)
    if source:
        q = q.filter(JobLog.source == source)
    logs = q.limit(limit).all()
    return {"logs": [_serialize_log(log) for log in logs], "count": len(logs)}


@router.get("/logs/{log_id}", response_model=None)
def get_automation_log(
    request: Request,
    log_id: str,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin_principal),
):
    log = db.query(JobLog).filter(JobLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail=f"Log {log_id!r} not found")
    return _serialize_log(log)


@router.post("/logs/{log_id}/replay", status_code=200, response_model=None)
def replay_automation_log(
    request: Request,
    log_id: str,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin_principal),
):
    from AINDY.platform_layer.scheduler_service import replay_task

    replayed = replay_task(log_id)
    if not replayed:
        log = db.query(JobLog).filter(JobLog.id == log_id).first()
        if not log:
            raise HTTPException(status_code=404, detail=f"Log {log_id!r} not found")
        raise HTTPException(
            status_code=409,
            detail=f"Log {log_id!r} cannot be replayed: status={log.status!r}",
        )
    return {"replayed": True, "log_id": log_id}
