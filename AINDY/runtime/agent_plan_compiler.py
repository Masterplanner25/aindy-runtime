"""
agent_plan_compiler.py — Compile an agent plan into a native Nodus workflow
(RTR-1 Phase 2b).

An agent ``plan`` is a flat, ordered list of single-tool steps:

    {
      "steps": [
        {"tool": "<name>", "args": {...}, "risk_level": "low|medium|high",
         "description": "..."},
        ...
      ]
    }

``compile_agent_plan(plan)`` turns it into a native Nodus ``workflow {}`` whose
steps run in order and invoke AINDY tools through the RTR-1 Phase 2a
``call_tool`` seam (capability-enforced). The compiled source is a **pure
structural skeleton** keyed only by step index — tool names and args are passed
via the run's ``input_payload`` (the channel the ``nodus.execute`` node forwards
into the script), never embedded as source. That makes the compilation
injection-safe: no planner-derived value (which may originate from an LLM) is
ever turned into code.

RTR-1 Phase 2d — per-step **retry** + **halt-on-first-failure**. Each step:

  1. calls its tool via ``call_tool`` and records the result under
     ``__step_N_result`` (so it always reaches the output state, even on the
     failing attempt);
  2. retries the tool up to ``max_attempts`` times while the result is a
     logical failure (``success != true``) *and* the error is retryable
     (``is_retryable_error`` host function, mirroring AGENT_FLOW's non-transient
     short-circuit);
  3. ``throw``s when the final attempt still failed.

``max_attempts`` is resolved at **compile time** from the step's ``risk_level``
via ``resolve_retry_policy(execution_type="agent", ...)`` — low/medium → 3,
high → 1 (immediate fail). The throw is what gives halt-on-first-failure: a
native ``workflow {}`` step that raises fails its task, and the task graph then
never schedules the dependent (``after``) steps, so no downstream step runs on a
predecessor's bad output. This matches AGENT_FLOW, which returns ``FAILURE`` from
a failed step and stops the flow.

Note: the native step ``retries`` option is deliberately **not** emitted. In
nodus's workflow runner that schedules a *durable* retry (``status:
retry_scheduled``) that requires a resume call — it does not retry in-process,
so the single-shot VM path would strand the run. The in-step loop keeps retry
synchronous and self-contained (mid-plan durable resume is RTR-1 Phase 2e).

Generated shape (for a 2-step plan; low-risk step_0, high-risk step_1):

    workflow agent_plan {
      step step_0 {
        let __attempt_0 = 1
        let __result_0 = call_tool(input_payload["__step_0_tool"], input_payload["__step_0_args"])
        while ((__result_0["success"] != true) && (__attempt_0 < 3) && is_retryable_error(__result_0["error"])) {
          __attempt_0 = __attempt_0 + 1
          __result_0 = call_tool(input_payload["__step_0_tool"], input_payload["__step_0_args"])
        }
        set_state("__step_0_result", __result_0)
        if (__result_0["success"] != true) {
          throw "agent step step_0 failed"
        }
      }
      step step_1 after step_0 {
        let __attempt_1 = 1
        let __result_1 = call_tool(input_payload["__step_1_tool"], input_payload["__step_1_args"])
        while ((__result_1["success"] != true) && (__attempt_1 < 1) && is_retryable_error(__result_1["error"])) {
          __attempt_1 = __attempt_1 + 1
          __result_1 = call_tool(input_payload["__step_1_tool"], input_payload["__step_1_args"])
        }
        set_state("__step_1_result", __result_1)
        if (__result_1["success"] != true) {
          throw "agent step step_1 failed"
        }
      }
    }

The returned ``input_payload`` seeds the run's input payload; each step writes its
``call_tool`` result to ``__step_N_result`` via ``set_state`` (so it returns in
the worker's output state). The returned ``steps`` metadata lets the VM-backed
adapter (Phase 2c) map each ``__step_N_result`` back to an ``AgentStep`` row; a
step whose ``__step_N_result`` is absent from the output state was halted before
it ran (its predecessor failed).
"""
from __future__ import annotations

from typing import Any

from AINDY.core.retry_policy import resolve_retry_policy

WORKFLOW_NAME = "agent_plan"


def _step_result_key(index: int) -> str:
    return f"__step_{index}_result"


def _step_tool_key(index: int) -> str:
    return f"__step_{index}_tool"


def _step_args_key(index: int) -> str:
    return f"__step_{index}_args"


def _resolve_max_attempts(risk_level: str) -> int:
    """Compile-time attempt budget for a step, mirroring AGENT_FLOW.

    low/medium → 3, high (and any unknown risk) → 1 (immediate fail).
    """
    policy = resolve_retry_policy(execution_type="agent", risk_level=risk_level)
    return max(1, int(policy.max_attempts))


def _step_source(
    index: int,
    tool_key: str,
    args_key: str,
    result_key: str,
    max_attempts: int,
    prev_index: int | None,
) -> str:
    """Emit one native workflow step with per-step retry + throw-on-failure.

    ``prev_index`` is the global index of the preceding step *within the same
    compiled workflow* (``None`` for a workflow's first step, so it has no
    ``after`` dependency). Step names use the global plan index, so a segmented
    plan (RTR-1 Phase 2e) keeps stable ``step_N`` / ``__step_N_result`` keys
    across segment boundaries even though each segment compiles independently.

    The retry loop degenerates to a single attempt when ``max_attempts == 1``
    (the ``while`` guard ``__attempt_N < 1`` is immediately false), so high-risk
    steps make exactly one tool call — no ``is_retryable_error`` evaluation.
    """
    after = f" after step_{prev_index}" if prev_index is not None else ""
    attempt_var = f"__attempt_{index}"
    result_var = f"__result_{index}"
    call_expr = f'call_tool(input_payload["{tool_key}"], input_payload["{args_key}"])'
    return (
        f"  step step_{index}{after} {{\n"
        f"    let {attempt_var} = 1\n"
        f"    let {result_var} = {call_expr}\n"
        f"    while (({result_var}[\"success\"] != true) && "
        f"({attempt_var} < {max_attempts}) && "
        f"is_retryable_error({result_var}[\"error\"])) {{\n"
        f"      {attempt_var} = {attempt_var} + 1\n"
        f"      {result_var} = {call_expr}\n"
        f"    }}\n"
        f'    set_state("{result_key}", {result_var})\n'
        f'    if ({result_var}["success"] != true) {{\n'
        f'      throw "agent step step_{index} failed"\n'
        f"    }}\n"
        f"  }}"
    )


def _is_wait_step(step: Any) -> bool:
    """A plan step is a WAIT step when it carries ``wait_for`` and no ``tool``."""
    return isinstance(step, dict) and step.get("wait_for") is not None and not step.get("tool")


def compile_agent_segment(
    tool_steps: list[dict[str, Any]],
    *,
    base_index: int = 0,
    workflow_name: str = WORKFLOW_NAME,
) -> dict[str, Any]:
    """Compile a contiguous run of TOOL steps into one native Nodus workflow.

    ``base_index`` is the global plan index of the first step in this run, so
    ``step_N`` / ``__step_N_result`` keys stay stable across the segments of a
    plan split at WAIT boundaries (RTR-1 Phase 2e). Within the segment the first
    step has no ``after`` dependency; the rest chain to their global predecessor.

    Same return shape as :func:`compile_agent_plan`. Raises ValueError if the
    segment has no steps or a step lacks a tool name / has non-object args.
    """
    if not tool_steps:
        raise ValueError("agent segment has no steps to compile")

    input_payload: dict[str, Any] = {}
    step_meta: list[dict[str, Any]] = []
    step_sources: list[str] = []
    prev_index: int | None = None

    for offset, step in enumerate(tool_steps):
        index = base_index + offset
        if not isinstance(step, dict):
            raise ValueError(f"plan step {index} is not an object: {step!r}")
        tool = step.get("tool")
        if not isinstance(tool, str) or not tool.strip():
            raise ValueError(f"plan step {index} has no tool name")
        args = step.get("args")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ValueError(f"plan step {index} args must be an object, got {type(args).__name__}")

        result_key = _step_result_key(index)
        tool_key = _step_tool_key(index)
        args_key = _step_args_key(index)

        input_payload[tool_key] = tool
        input_payload[args_key] = args

        risk_level = step.get("risk_level", "high")
        max_attempts = _resolve_max_attempts(risk_level)

        step_meta.append(
            {
                "index": index,
                "tool": tool,
                "args": args,
                "risk_level": risk_level,
                "description": step.get("description", ""),
                "result_key": result_key,
                "max_attempts": max_attempts,
            }
        )

        step_sources.append(
            _step_source(index, tool_key, args_key, result_key, max_attempts, prev_index)
        )
        prev_index = index

    source = f"workflow {workflow_name} {{\n" + "\n".join(step_sources) + "\n}"

    # Defensive: the skeleton must always parse as a native workflow. This also
    # validates step count / dependency wiring via the shared compiler.
    from AINDY.runtime.nodus_flow_compiler import compile_nodus_flow

    compile_nodus_flow(source)  # raises ValueError if malformed

    return {
        "source": source,
        "workflow_name": workflow_name,
        "input_payload": input_payload,
        "steps": step_meta,
    }


def split_agent_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Split a plan into execution SEGMENTS at WAIT-step boundaries (Phase 2e).

    A plan is a flat ordered list mixing TOOL steps (``{"tool": ...}``) and WAIT
    steps (``{"wait_for": "<event.type>", "correlation_key"?: str}``). This
    returns an ordered list of segments::

        {
          "tool_steps": [<tool step dicts>],   # may be empty (a bare wait)
          "base_index": int,                    # global index of the 1st tool step
          "wait": {"event_type": str,           # the wait that FOLLOWS this segment
                   "correlation_key": str | None} | None,   # None on the final segment
        }

    Only TOOL steps consume the global index space (so ``AgentStep.step_index``
    stays contiguous); WAIT steps are control points and get no index. A plan
    with no wait steps yields a single segment with ``wait=None`` — identical
    execution to the pre-2e single-shot path.

    Raises ValueError if a wait step's ``wait_for`` is not a non-empty string.
    """
    steps = (plan or {}).get("steps") or []
    if not steps:
        raise ValueError("agent plan has no steps to compile")

    segments: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    base_index = 0
    tool_count = 0

    for position, step in enumerate(steps):
        if _is_wait_step(step):
            event_type = step.get("wait_for")
            if not isinstance(event_type, str) or not event_type.strip():
                raise ValueError(f"plan step {position} wait_for must be a non-empty string")
            correlation_key = step.get("correlation_key")
            if correlation_key is not None and not isinstance(correlation_key, str):
                raise ValueError(f"plan step {position} correlation_key must be a string")
            segments.append(
                {
                    "tool_steps": current,
                    "base_index": base_index,
                    "wait": {"event_type": event_type, "correlation_key": correlation_key},
                }
            )
            base_index += tool_count
            current = []
            tool_count = 0
        else:
            current.append(step)
            tool_count += 1

    # Trailing tool steps (or an empty tail after a wait) form the terminal segment.
    segments.append({"tool_steps": current, "base_index": base_index, "wait": None})
    return segments


def compile_agent_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Compile an agent plan into a single native Nodus workflow.

    Backward-compatible whole-plan entry point (no WAIT steps). Returns:
        {
          "source": str,              # runnable native workflow source
          "workflow_name": "agent_plan",
          "input_payload": {"__step_0_tool": "<tool>", "__step_0_args": {...}, ...},
          "steps": [{"index": 0, "tool": "<tool>", "args": {...}, "risk_level": ...,
                     "description": ..., "result_key": "__step_0_result",
                     "max_attempts": ...}, ...],
        }

    For plans that may contain WAIT steps, use :func:`split_agent_plan` +
    :func:`compile_agent_segment` (the segment-split executor path). Raises
    ValueError if the plan has no steps or a step lacks a tool name.
    """
    steps = (plan or {}).get("steps") or []
    if not steps:
        raise ValueError("agent plan has no steps to compile")
    return compile_agent_segment(steps, base_index=0, workflow_name=WORKFLOW_NAME)
