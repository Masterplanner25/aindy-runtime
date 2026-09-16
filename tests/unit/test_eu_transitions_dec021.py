"""DEC-021 — an execution unit completes only from ``executing``; ``waiting`` has no
``completed`` edge, on purpose.

``waiting`` is a runtime obligation: the unit is parked on an event the scheduler will
deliver and its work is unfinished. The truthful exits are resuming (``waiting → resumed →
executing``, then completing) or ``failed``. These tests drive the real service on a real
unit, so adding the edge — or removing one of the two truthful exits — is a red test, not
a silent widening.

Read with the app's ``TASK-EU-NOT-PERSISTED-1`` (#363), which stepped a paused task's unit
``waiting → executing → completed`` through the backward-compat edge; the mapping
``paused → waiting`` is the consumer-side mismatch, recorded in the decision.
"""

from __future__ import annotations

import uuid

import pytest

from AINDY.core.execution_unit_service import _STATUS_TRANSITIONS, ExecutionUnitService

pytestmark = [pytest.mark.runtime_only, pytest.mark.usefixtures("db_session")]


def _waiting_unit(db):
    eu = ExecutionUnitService(db).create(
        eu_type="task", user_id=str(uuid.uuid4()), source_type="task",
        source_id=str(uuid.uuid4()), status="waiting",
    )
    db.flush()
    return eu


def _status(db, eu_id):
    from AINDY.db.models.execution_unit import ExecutionUnit

    db.expire_all()
    return db.query(ExecutionUnit.status).filter(ExecutionUnit.id == eu_id).scalar()


def test_waiting_has_no_completed_edge(db_session):
    assert "completed" not in _STATUS_TRANSITIONS["waiting"]
    eu = _waiting_unit(db_session)

    assert ExecutionUnitService(db_session).update_status(eu.id, "completed") is False
    assert _status(db_session, eu.id) == "waiting"


def test_waiting_completes_only_after_resume(db_session):
    eu = _waiting_unit(db_session)
    eus = ExecutionUnitService(db_session)

    assert eus.resume_execution_unit(eu.id) is True
    assert _status(db_session, eu.id) == "executing"
    assert eus.update_status(eu.id, "completed") is True
    assert _status(db_session, eu.id) == "completed"


def test_waiting_may_fail_without_resuming(db_session):
    """The asymmetry is the design: abandoning a parked unit is a truthful terminal exit."""
    eu = _waiting_unit(db_session)

    assert ExecutionUnitService(db_session).update_status(eu.id, "failed") is True
    assert _status(db_session, eu.id) == "failed"


def test_only_executing_reaches_completed():
    """Derived over the whole table, not a literal: any new state that can complete without
    executing is a decision, not an edit."""
    sources = {state for state, targets in _STATUS_TRANSITIONS.items() if "completed" in targets}
    assert sources == {"executing"}
