"""
nodus_flow_compiler.py — Parse a native Nodus ``workflow {}`` / ``goal {}`` block
and extract its step dependency graph (RTR-1a).

nodus-lang 4.x has a first-class ``workflow``/``goal`` construct:

    workflow build {
        state version = "0.1.0"
        step fetch {
            ...
        }
        step compile after fetch {
            if version != "" { ... }
        }
        step publish after compile, fetch {
            ...
        }
    }

Steps contain real logic; dependencies are declared with ``after``; conditional
logic is ordinary ``if`` inside a step body (there is no ``when`` option).
Execution, parallelism, shared state, retries and checkpoints are handled by
nodus's own workflow runner — AINDY runs the workflow natively (via the
``nodus.execute`` path) rather than translating it into a PersistentFlowRunner
DAG of registered nodes.

``compile_nodus_flow(source, flow_name=None)`` parses the source **without
executing it** and returns the workflow's structure (step dependency DAG) for
validation and observability:

    {
        "workflow_name": "build",
        "execution_kind": "workflow",   # or "goal"
        "steps": ["fetch", "compile", "publish"],
        "start": ["fetch"],             # steps with no dependencies (roots)
        "edges": {"fetch": ["compile", "publish"], "compile": ["publish"], "publish": []},
        "end": ["publish"],             # steps nothing depends on (leaves)
    }

This replaces the pre-4.x host-object ``flow.step()`` DSL, which no longer parses
(``step`` is a reserved keyword and host-object method calls are unsupported).
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _load_frontend():
    """Import the nodus-lang frontend (lexer/parser/AST), or raise RuntimeError."""
    try:
        from nodus.frontend.lexer import tokenize
        from nodus.frontend.parser import Parser
        from nodus.frontend.ast.ast_nodes import GoalDef, WorkflowDef
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "Nodus VM not installed — run: pip install nodus"
        ) from exc
    return tokenize, Parser, WorkflowDef, GoalDef


def parse_nodus_workflow(source: str, flow_name: str | None = None):
    """Parse *source* and return its single ``WorkflowDef``/``GoalDef`` AST node.

    Raises ValueError when the source contains no workflow/goal block, or more
    than one and *flow_name* does not disambiguate.
    """
    tokenize, Parser, WorkflowDef, GoalDef = _load_frontend()

    try:
        statements = Parser(tokenize(source)).parse()
    except Exception as exc:  # nodus parse/lex error
        raise ValueError(f"Nodus workflow parse error: {exc}") from exc

    defs = [s for s in statements if isinstance(s, (WorkflowDef, GoalDef))]
    if not defs:
        raise ValueError(
            "flow-graph source must contain a `workflow { ... }` or `goal { ... }` block"
        )
    if flow_name:
        named = [d for d in defs if d.name == flow_name]
        if not named:
            raise ValueError(
                f"no workflow/goal named {flow_name!r} in source "
                f"(found: {[d.name for d in defs]})"
            )
        return named[0]
    if len(defs) > 1:
        raise ValueError(
            "source declares multiple workflow/goal blocks "
            f"({[d.name for d in defs]}); register them individually"
        )
    return defs[0]


def _build_step_graph(workflow_def) -> dict[str, Any]:
    """Build the {start, edges, end, steps} DAG from a workflow/goal AST node."""
    from nodus.frontend.ast.ast_nodes import GoalDef

    steps = [step.name for step in workflow_def.steps]
    step_set = set(steps)
    deps = {step.name: list(step.deps or []) for step in workflow_def.steps}

    # Parser already enforces unique names and valid deps, but guard anyway.
    for name, dep_list in deps.items():
        for dep in dep_list:
            if dep not in step_set:
                raise ValueError(
                    f"step {name!r} depends on unknown step {dep!r}"
                )

    # Forward edges: dep -> dependent. Every step is a key (leaves map to []).
    edges: dict[str, list[str]] = {name: [] for name in steps}
    depended_on: set[str] = set()
    for name in steps:
        for dep in deps[name]:
            edges[dep].append(name)
            depended_on.add(dep)

    start = [name for name in steps if not deps[name]]
    end = [name for name in steps if name not in depended_on]

    return {
        "workflow_name": workflow_def.name,
        "execution_kind": "goal" if isinstance(workflow_def, GoalDef) else "workflow",
        "steps": steps,
        "start": start,
        "edges": edges,
        "end": end,
    }


def compile_nodus_flow(source: str, flow_name: str | None = None) -> dict[str, Any]:
    """Parse a native Nodus ``workflow``/``goal`` source and return its step DAG.

    Does not execute the workflow. See module docstring for the return shape.

    Raises
    ------
    RuntimeError
        When the Nodus VM/frontend is not installed.
    ValueError
        When the source has no workflow/goal block, an ambiguous set of blocks,
        or a structural error.
    """
    workflow_def = parse_nodus_workflow(source, flow_name)
    graph = _build_step_graph(workflow_def)
    logger.info(
        "[NodusFlowCompiler] Parsed %s %r — steps=%s start=%s end=%s",
        graph["execution_kind"],
        graph["workflow_name"],
        graph["steps"],
        graph["start"],
        graph["end"],
    )
    return graph
