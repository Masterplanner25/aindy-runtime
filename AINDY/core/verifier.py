"""
core/verifier.py - Post-condition verifier for agent runs (AGENT-HARDEN-6).

The missing stage in Plan → Dry-Run → Approve → Execute → **Verify**. After a plan
runs to completion, declared per-step post-conditions (``expects`` on a plan step)
are checked against the step's result. A run whose post-conditions fail is marked
``verify_failed`` and its reversible effects are rolled back via the AGENT-HARDEN-3
compensators.

A plan step declares post-conditions with an optional ``expects`` field — a single
condition dict or a list of them. Each condition is one of:

  - ``{"status": "success"}``
      the step's execution status must equal the given value.
  - ``{"field": "<dot.path>", "op": "<op>", "value": <v>}``
      resolve ``<dot.path>`` into the step's ``result`` payload and apply ``<op>``.
      ``op`` defaults to ``"truthy"``; ``value`` is required for the comparison ops.

Supported ops: exists, not_exists, eq, ne, contains, not_contains, gt, gte, lt,
lte, truthy, falsy. Unknown ops / malformed conditions fail closed (reported as a
verification failure, never silently passed).

Post-conditions key by **tool-step ordinal** (the value carried in each step
result's ``step_index``) — WAIT steps do not consume index space, matching
``split_agent_plan`` / ``AgentStep.step_index``. Plans with no ``expects`` verify
vacuously (``checked == 0``), so the stage is a no-op until authors opt in.
"""
from __future__ import annotations

from typing import Any

_MISSING = object()

_COMPARISON_OPS = {"eq", "ne", "contains", "not_contains", "gt", "gte", "lt", "lte"}
_PRESENCE_OPS = {"exists", "not_exists", "truthy", "falsy"}


def _is_wait_step(step: Any) -> bool:
    return isinstance(step, dict) and step.get("wait_for") is not None and not step.get("tool")


def extract_post_conditions(plan: Any) -> dict[int, list[dict]]:
    """Map tool-step ordinal → list of ``expects`` conditions from a plan.

    WAIT steps are skipped (they carry no result and consume no index space), so
    the ordinal matches the ``step_index`` in reconstructed step results.
    """
    conditions: dict[int, list[dict]] = {}
    steps = (plan or {}).get("steps", []) if isinstance(plan, dict) else []
    ordinal = 0
    for step in steps:
        if not isinstance(step, dict):
            continue
        if _is_wait_step(step):
            continue
        expects = step.get("expects")
        if expects:
            conditions[ordinal] = expects if isinstance(expects, list) else [expects]
        ordinal += 1
    return conditions


def _resolve_path(payload: Any, path: str) -> Any:
    cur = payload
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, (list, tuple)):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return _MISSING
        else:
            return _MISSING
    return cur


def _eval_condition(condition: Any, step_result: dict | None) -> tuple[bool, str]:
    if not isinstance(condition, dict):
        return False, f"malformed condition {condition!r}"
    if step_result is None:
        return False, "step did not run"

    # Status shorthand.
    if "status" in condition and "field" not in condition and "op" not in condition:
        actual = step_result.get("status")
        expected = condition["status"]
        return (actual == expected), f"status is {actual!r}, expected {expected!r}"

    op = str(condition.get("op", "truthy"))
    field = condition.get("field")
    actual = (
        _resolve_path(step_result.get("result"), field)
        if field is not None
        else step_result.get("result")
    )
    present = actual is not _MISSING

    if op == "exists":
        return present, f"field {field!r} does not exist"
    if op == "not_exists":
        return (not present), f"field {field!r} exists ({actual!r})"
    if not present:
        return False, f"field {field!r} does not exist"
    if op == "truthy":
        return bool(actual), f"field {field!r} is not truthy ({actual!r})"
    if op == "falsy":
        return (not bool(actual)), f"field {field!r} is not falsy ({actual!r})"

    if op in _COMPARISON_OPS:
        expected = condition.get("value")
        try:
            if op == "eq":
                return (actual == expected), f"{actual!r} != {expected!r}"
            if op == "ne":
                return (actual != expected), f"{actual!r} == {expected!r}"
            if op == "contains":
                return (expected in actual), f"{actual!r} does not contain {expected!r}"
            if op == "not_contains":
                return (expected not in actual), f"{actual!r} contains {expected!r}"
            if op == "gt":
                return (actual > expected), f"{actual!r} <= {expected!r}"
            if op == "gte":
                return (actual >= expected), f"{actual!r} < {expected!r}"
            if op == "lt":
                return (actual < expected), f"{actual!r} >= {expected!r}"
            if op == "lte":
                return (actual <= expected), f"{actual!r} > {expected!r}"
        except TypeError as exc:
            return False, f"op {op!r} not applicable to {actual!r}: {exc}"

    return False, f"unknown op {op!r}"


def verify_post_conditions(
    post_conditions: dict[int, list[dict]],
    step_results: list[dict],
) -> dict[str, Any]:
    """Evaluate ``post_conditions`` against reconstructed step results.

    Returns ``{"ok": bool, "checked": int, "failures": [...]}`` where each failure is
    ``{"step_index", "condition", "reason"}``. ``ok`` is True when nothing failed —
    including the vacuous case where no post-conditions were declared.
    """
    by_index = {r.get("step_index"): r for r in (step_results or []) if isinstance(r, dict)}
    failures: list[dict] = []
    checked = 0
    for index, conditions in (post_conditions or {}).items():
        result = by_index.get(index)
        for condition in conditions:
            checked += 1
            ok, reason = _eval_condition(condition, result)
            if not ok:
                failures.append(
                    {"step_index": index, "condition": condition, "reason": reason}
                )
    return {"ok": not failures, "checked": checked, "failures": failures}
