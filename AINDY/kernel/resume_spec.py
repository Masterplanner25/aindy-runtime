from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Callable, Protocol


RESUME_HANDLER_EU = "execution_unit.resume"


class ResumeCallbackBuilder(Protocol):
    def __call__(self, spec: "ResumeSpec") -> Callable[[], None]:
        ...


@dataclass
class ResumeSpec:
    handler: str
    eu_id: str
    tenant_id: str
    run_id: str
    eu_type: str | None = None


_RESUME_CALLBACK_BUILDERS: dict[str, ResumeCallbackBuilder] = {}


def spec_to_json(spec: ResumeSpec) -> str:
    return json.dumps(asdict(spec))


def spec_from_json(raw: str) -> ResumeSpec:
    return ResumeSpec(**json.loads(raw))


def register_resume_callback_builder(handler: str, builder: ResumeCallbackBuilder) -> None:
    _RESUME_CALLBACK_BUILDERS[handler] = builder


def _build_execution_unit_resume_callback(spec: ResumeSpec) -> Callable[[], None]:
    """The callback the CROSS-INSTANCE fallback runs for a wait whose registering instance is gone.

    ★ Until 2026-09-16 this was ``with SessionLocal() as db: resume_execution_unit(spec.eu_id)``
    — and that was two defects in one line. (1) The context manager exits without committing,
    so the unit's ``waiting → resumed → executing`` was ROLLED BACK on every fire: the fourth
    own-session-never-commits callback found in a week (`test_own_session_commits.py` now
    guards the class). (2) Even committed, it moved only the UNIT. The run it belongs to — the
    thing the wait exists for — was never resumed: in thread mode (the default since FR-15 (a))
    the scheduler runs THIS closure, so a flow parked on an instance that died was "claimed"
    cross-instance, logged as resumed, and stayed `waiting` forever. Only distributed mode,
    which discards the closure and rebuilds from `run_id`+`eu_type` on the worker, ever resumed
    it. `test_multi_instance_resume.py` could not see either: it patched
    `resume_execution_unit` to a spy and asserted the spy was called.

    Now the closure rebuilds the REAL resume from the spec at fire time — the same
    `build_resume_callback` the FR-15 worker uses (flow → claim + unit + `runner.resume()`,
    agent → the segment chain), each of which commits — and falls back to the unit-only
    transition, COMMITTED and logged at WARNING, only when the run cannot be rebuilt here
    (unknown type, unregistered flow, missing row).
    """
    import logging

    from AINDY.core.execution_unit_service import ExecutionUnitService
    from AINDY.db import SessionLocal

    _log = logging.getLogger(__name__)

    def _resume():
        from AINDY.core.resume_reconstruction import build_resume_callback

        rebuilt = None
        if spec.run_id and spec.eu_type:
            db = SessionLocal()
            try:
                rebuilt = build_resume_callback(run_id=spec.run_id, eu_type=spec.eu_type, db=db)
            finally:
                db.close()
        if rebuilt is not None:
            rebuilt()  # opens and commits its own session; claims atomically
            return
        _log.warning(
            "[resume_spec] run=%s (eu_type=%r) cannot be rebuilt in this process — moving only "
            "its execution unit %s to executing; the run itself is NOT resumed here",
            spec.run_id, spec.eu_type, spec.eu_id,
        )
        db = SessionLocal()
        try:
            ExecutionUnitService(db).resume_execution_unit(spec.eu_id)
            db.commit()
        finally:
            db.close()

    return _resume


register_resume_callback_builder(RESUME_HANDLER_EU, _build_execution_unit_resume_callback)


def build_callback_from_spec(spec: ResumeSpec):
    """Reconstruct an executable callback from a ResumeSpec."""
    builder = _RESUME_CALLBACK_BUILDERS.get(spec.handler)
    if builder is not None:
        return builder(spec)
    raise ValueError(f"Unknown resume handler: {spec.handler}")
