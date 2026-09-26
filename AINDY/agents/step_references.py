"""FR-46 — a plan step's argument may take an earlier step's result.

A plan step's ``args`` were literals the planner wrote before any step ran, so "research X, then
use it" ran its second half blind: the app's first real goal stored the planner's
pre-research sentence as its "findings", and every step said ``success``.

Design: ``docs/design/FR46_STEP_REFERENCES_DESIGN.md``. Decisions:

* **DEC-073:** one form. An argument value, at any depth, may be exactly
  ``{"$from_step": N, "path": "a.b"}`` (``path`` optional). It is replaced whole by tool step N's
  result, or a field inside it. N is the tool-step ordinal (WAIT steps take no index) and must be
  strictly earlier. The path grammar is `core/result_path.py`, shared with the verifier. It is
  checked at plan time by `validate_plan_references`.
* **DEC-074:** resolved by `resolve_step_references` in the two callers of `execute_tool`, before
  it (`agent_execute_step`; the worker's `run_agent_tool`), so the idempotency key,
  ``args_schema`` validation and the recorded ``tool_args`` all see the value.
* **DEC-075:** a reference that cannot be resolved fails the step with ``failure_class:
  "invalid"``; the tool is never called with the placeholder. Behind
  ``AINDY_PLAN_STEP_REFERENCES`` (default off); when on, the runtime's tool catalog carries
  `PLANNER_REFERENCE_LINE`.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional

from AINDY.core.result_path import MISSING, path_is_well_formed, resolve_path

REFERENCE_KEY = "$from_step"
PATH_KEY = "path"

#: One line appended to the tool catalog the planner sees (`planning._build_planner_prompt`), so
#: every planner learns the form whichever system prompt the app supplies.
PLANNER_REFERENCE_LINE = (
    'A step\'s argument value may be {"$from_step": N, "path": "a.b"}: the result of tool step N '
    "(0-based, counting tool steps only, earlier than this one), or a field inside it (dot path; "
    "list items by number, e.g. \"results.0.id\"). Use it when a step needs what an earlier step "
    "found; never write a value a step has not produced yet."
)

#: What a lookup returns for one step: its status and its result.
StepEntry = dict
Lookup = Callable[[int], Optional[StepEntry]]


def step_references_enabled() -> bool:
    return os.getenv("AINDY_PLAN_STEP_REFERENCES", "").strip().lower() in {"1", "true", "yes", "on"}


def _looks_like_reference(value: Any) -> bool:
    return isinstance(value, dict) and REFERENCE_KEY in value


def contains_reference(value: Any) -> bool:
    if _looks_like_reference(value):
        return True
    if isinstance(value, dict):
        return any(contains_reference(v) for v in value.values())
    if isinstance(value, list):
        return any(contains_reference(v) for v in value)
    return False


def _reference_errors(ref: dict, *, own_index: Optional[int]) -> list[str]:
    errors: list[str] = []
    extra = set(ref) - {REFERENCE_KEY, PATH_KEY}
    if extra:
        errors.append(f"a reference takes only {REFERENCE_KEY!r} and {PATH_KEY!r}, not {sorted(extra)}")
    target = ref.get(REFERENCE_KEY)
    if isinstance(target, bool) or not isinstance(target, int) or target < 0:
        errors.append(f"{REFERENCE_KEY} must be a non-negative integer step index, got {target!r}")
    elif own_index is not None and target >= own_index:
        errors.append(f"{REFERENCE_KEY} {target} is not an earlier step than step {own_index}")
    if PATH_KEY in ref and not path_is_well_formed(ref[PATH_KEY]):
        errors.append(f"{PATH_KEY} must be a dot path of non-empty segments, got {ref[PATH_KEY]!r}")
    return errors


def _walk_references(value: Any, where: str):
    if _looks_like_reference(value):
        yield where, value
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_references(item, f"{where}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _walk_references(item, f"{where}.{i}")


def validate_plan_references(plan: Any) -> list[str]:
    """Every reference in a plan, checked against the plan's own tool-step ordinals (DEC-073).

    Returns human-readable errors; empty means valid. WAIT steps take no index, the same way
    `split_agent_plan` and the verifier count.
    """
    from AINDY.runtime.agent_plan_compiler import _is_wait_step

    errors: list[str] = []
    steps = (plan or {}).get("steps") if isinstance(plan, dict) else None
    ordinal = 0
    for step in steps or []:
        if not isinstance(step, dict) or _is_wait_step(step):
            continue
        for where, ref in _walk_references(step.get("args"), "args"):
            for err in _reference_errors(ref, own_index=ordinal):
                errors.append(f"step {ordinal} {where}: {err}")
        ordinal += 1
    return errors


def resolve_step_references(args: Any, lookup: Lookup) -> tuple[Any, list[str]]:
    """``(resolved_args, errors)``. Any error means the step must fail and not run (DEC-075).

    ``lookup(step_index)`` returns ``{"status", "result"}`` or None. Only a ``success`` step
    resolves. A ``skipped`` step (the authority gate, DEC-069) produced nothing, and a failed one
    produced nothing usable.
    """
    errors: list[str] = []

    def _resolve(value: Any, where: str) -> Any:
        if _looks_like_reference(value):
            shape = _reference_errors(value, own_index=None)
            if shape:
                errors.extend(f"{where}: {e}" for e in shape)
                return None
            target = int(value[REFERENCE_KEY])
            try:
                entry = lookup(target)
            except Exception as exc:  # noqa: BLE001 — unreadable is unresolved: fail, never guess
                errors.append(f"{where}: step {target}'s result could not be read ({type(exc).__name__})")
                return None
            if entry is None:
                errors.append(f"{where}: step {target} has no recorded result")
                return None
            status = str(entry.get("status") or "")
            if status != "success":
                errors.append(f"{where}: step {target} is {status or 'unknown'}, not success; it has no result to give")
                return None
            result = entry.get("result")
            if PATH_KEY not in value:
                return result
            found = resolve_path(result, value[PATH_KEY])
            if found is MISSING:
                errors.append(f"{where}: step {target}'s result has no {value[PATH_KEY]!r}")
                return None
            return found
        if isinstance(value, dict):
            return {k: _resolve(v, f"{where}.{k}") for k, v in value.items()}
        if isinstance(value, list):
            return [_resolve(v, f"{where}.{i}") for i, v in enumerate(value)]
        return value

    resolved = _resolve(args, "args")
    return resolved, errors


def reference_failure(errors: list[str]) -> dict:
    """The step's outcome when a reference cannot be resolved: `invalid`, never retried."""
    return {
        "success": False,
        "result": None,
        "error": "step reference could not be resolved: " + "; ".join(errors),
        "failure_class": "invalid",
    }


def lookup_from_step_results(step_results: Any) -> Lookup:
    """agent_flow: the flow state's ``step_results`` (``{step_index, status, result, …}``)."""
    by_index: dict[int, StepEntry] = {}
    for entry in step_results or []:
        if isinstance(entry, dict) and isinstance(entry.get("step_index"), int):
            by_index[entry["step_index"]] = entry  # a later entry for the same index wins
    return by_index.get


def lookup_from_guest_state(state: Any) -> Lookup:
    """Simulation: the guest's ``__step_N_result`` (``{success, result, error}``)."""

    def _lookup(index: int) -> Optional[StepEntry]:
        entry = (state or {}).get(f"__step_{index}_result") if isinstance(state, dict) else None
        if not isinstance(entry, dict):
            return None
        return {"status": "success" if entry.get("success") else "failed", "result": entry.get("result")}

    return _lookup


def lookup_from_agent_steps(session_factory: Any, run_id: str) -> Lookup:
    """nodus_vm: the ``agent_steps`` row by ``(run_id, step_index)``. It is committed by the
    worker seam before the next step runs, so it covers earlier segments, WAIT boundaries and
    durable step granularity, where the guest's own state has forgotten them."""

    def _lookup(index: int) -> Optional[StepEntry]:
        from AINDY.db.models import AgentStep
        from AINDY.runtime.nodus_adapter import _db_run_id

        db = session_factory()
        try:
            row = (
                db.query(AgentStep.status, AgentStep.result)
                .filter(AgentStep.run_id == _db_run_id(run_id), AgentStep.step_index == index)
                .first()
            )
        finally:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass
        if row is None:
            return None
        return {"status": str(row[0]), "result": row[1]}

    return _lookup
