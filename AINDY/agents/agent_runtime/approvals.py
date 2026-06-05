from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from AINDY.agents.capability_service import mint_token
from AINDY.core.execution_signal_helper import record_agent_event
from AINDY.core.system_event_service import emit_error_event
from AINDY.platform_layer.trace_context import get_parent_event_id, get_trace_id

from AINDY.agents.agent_runtime.shared import get_runtime_compat_module, logger


def approve_run(run_id: str, user_id: str, db: Session) -> Optional[dict]:
    try:
        compat = get_runtime_compat_module()
        from AINDY.db.models import AgentRun

        user_db_id = compat._db_user_id(user_id)
        db_run_id = compat._db_run_id(run_id)
        run = db.query(AgentRun).filter(AgentRun.id == db_run_id).first()
        if not run or not compat._user_matches(run.user_id, user_db_id):
            return None

        # Atomic CAS: only the first concurrent caller wins; subsequent callers see rowcount=0.
        from sqlalchemy import update as sqla_update
        rows = db.execute(
            sqla_update(AgentRun)
            .where(AgentRun.id == db_run_id, AgentRun.status == "pending_approval")
            .values(status="approved", approved_at=datetime.now(timezone.utc))
            .execution_options(synchronize_session=False)
        ).rowcount
        if rows == 0:
            db.expire(run)
            db.refresh(run)
            return compat._run_to_dict(run)
        db.expire(run)
        run = db.query(AgentRun).filter(AgentRun.id == db_run_id).first()

        token = mint_token(
            run_id=str(run.id),
            user_id=user_db_id,
            plan=run.plan,
            db=db,
            approval_mode="manual",
            agent_type=getattr(run, "agent_type", "default"),
        )
        if not token:
            db.rollback()
            run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
            if run:
                run.error_message = "Capability preflight failed; run not approved."
                db.commit()
                db.refresh(run)
                return compat._run_to_dict(run)
            return None

        run.execution_token = token.get("execution_token")
        run.capability_token = token
        run.error_message = None
        db.commit()
        record_agent_event(
            run_id=str(run.id),
            user_id=user_db_id,
            event_type="APPROVED",
            db=db,
            correlation_id=getattr(run, "correlation_id", None),
            payload={"auto_executed": False},
            required=True,
        )
        _run_dict = compat._run_to_dict(run)
        _bg_run_id = run.id

        def _bg_execute():
            try:
                from AINDY.db.database import SessionLocal
                bg_db = SessionLocal()
                try:
                    compat.execute_run(run_id=_bg_run_id, user_id=user_db_id, db=bg_db)
                finally:
                    bg_db.close()
            except Exception as exc:
                logger.warning(
                    "[AgentRuntime] background execute_run failed for %s: %s", _bg_run_id, exc
                )

        threading.Thread(target=_bg_execute, daemon=True).start()
        return _run_dict
    except Exception as exc:
        compat = get_runtime_compat_module()
        logger.warning("[AgentRuntime] approve_run failed for %s: %s", run_id, exc)
        compat.emit_error_event(
            db=db,
            error_type="agent_approve_run",
            message=str(exc),
            user_id=user_id,
            trace_id=get_trace_id(),
            parent_event_id=get_parent_event_id(),
            source="agent",
            payload={"run_id": str(run_id)},
            required=True,
        )
        return None


def reject_run(run_id: str, user_id: str, db: Session) -> Optional[dict]:
    try:
        compat = get_runtime_compat_module()
        from AINDY.db.models import AgentRun

        user_db_id = compat._db_user_id(user_id)
        db_run_id = compat._db_run_id(run_id)
        run = db.query(AgentRun).filter(AgentRun.id == db_run_id).first()
        if not run or not compat._user_matches(run.user_id, user_db_id):
            return None
        if run.status != "pending_approval":
            return compat._run_to_dict(run)

        run.status = "rejected"
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        record_agent_event(
            run_id=str(run.id),
            user_id=user_db_id,
            event_type="REJECTED",
            db=db,
            correlation_id=getattr(run, "correlation_id", None),
            payload={},
            required=True,
        )
        return compat._run_to_dict(run)
    except Exception as exc:
        compat = get_runtime_compat_module()
        logger.warning("[AgentRuntime] reject_run failed for %s: %s", run_id, exc)
        compat.emit_error_event(
            db=db,
            error_type="agent_reject_run",
            message=str(exc),
            user_id=user_id,
            trace_id=get_trace_id(),
            parent_event_id=get_parent_event_id(),
            source="agent",
            payload={"run_id": str(run_id)},
            required=True,
        )
        return None
