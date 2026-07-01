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

Generated shape (for a 2-step plan):

    workflow agent_plan {
      step step_0 {
        set_state("__step_0_result", call_tool(input_payload["__step_0_tool"], input_payload["__step_0_args"]))
      }
      step step_1 after step_0 {
        set_state("__step_1_result", call_tool(input_payload["__step_1_tool"], input_payload["__step_1_args"]))
      }
    }

The returned ``input_payload`` seeds the run's input payload; each step writes its
``call_tool`` result to ``__step_N_result`` via ``set_state`` (so it returns in
the worker's output state). The returned ``steps`` metadata lets the VM-backed
adapter (Phase 2c) map each ``__step_N_result`` back to an ``AgentStep`` row.
"""
from __future__ import annotations

from typing import Any

WORKFLOW_NAME = "agent_plan"


def _step_result_key(index: int) -> str:
    return f"__step_{index}_result"


def _step_tool_key(index: int) -> str:
    return f"__step_{index}_tool"


def _step_args_key(index: int) -> str:
    return f"__step_{index}_args"


def compile_agent_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Compile an agent plan into a native Nodus workflow.

    Returns a dict:
        {
          "source": str,              # runnable native workflow source
          "workflow_name": "agent_plan",
          "input_payload": {          # seed as the run's input_payload
              "__step_0_tool": "<tool>", "__step_0_args": {...}, ...
          },
          "steps": [                  # per-step metadata for AgentStep mapping
              {"index": 0, "tool": "<tool>", "args": {...},
               "risk_level": "...", "description": "...",
               "result_key": "__step_0_result"},
              ...
          ],
        }

    Raises ValueError if the plan has no steps or a step lacks a tool name.
    """
    steps = (plan or {}).get("steps") or []
    if not steps:
        raise ValueError("agent plan has no steps to compile")

    input_payload: dict[str, Any] = {}
    step_meta: list[dict[str, Any]] = []
    step_sources: list[str] = []

    for index, step in enumerate(steps):
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

        step_meta.append(
            {
                "index": index,
                "tool": tool,
                "args": args,
                "risk_level": step.get("risk_level", "high"),
                "description": step.get("description", ""),
                "result_key": result_key,
            }
        )

        after = f" after step_{index - 1}" if index > 0 else ""
        step_sources.append(
            f"  step step_{index}{after} {{\n"
            f'    set_state("{result_key}", call_tool(input_payload["{tool_key}"], input_payload["{args_key}"]))\n'
            f"  }}"
        )

    source = f"workflow {WORKFLOW_NAME} {{\n" + "\n".join(step_sources) + "\n}"

    # Defensive: the skeleton must always parse as a native workflow. This also
    # validates step count / dependency wiring via the shared compiler.
    from AINDY.runtime.nodus_flow_compiler import compile_nodus_flow

    compile_nodus_flow(source)  # raises ValueError if malformed

    return {
        "source": source,
        "workflow_name": WORKFLOW_NAME,
        "input_payload": input_payload,
        "steps": step_meta,
    }
