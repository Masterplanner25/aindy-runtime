"""Unit tests for the RTR-1a native-workflow compiler (compile_nodus_flow).

Covers step-DAG extraction from the native nodus-lang `workflow {}` / `goal {}`
construct, error handling, and an end-to-end proof that the composed source
(definition + run_workflow invocation) actually executes its steps in dependency
order through the nodus VM.
"""
from __future__ import annotations

import pytest

from AINDY.runtime.nodus_flow_compiler import compile_nodus_flow, parse_nodus_workflow


_LINEAR = '''workflow build {
  step fetch {
    let n = 1
  }
  step compile after fetch {
    let m = 2
  }
  step publish after compile, fetch {
    let p = 3
  }
}'''


def test_extracts_linear_dag():
    g = compile_nodus_flow(_LINEAR)
    assert g["workflow_name"] == "build"
    assert g["execution_kind"] == "workflow"
    assert g["steps"] == ["fetch", "compile", "publish"]
    assert g["start"] == ["fetch"]                  # only root
    assert g["end"] == ["publish"]                  # only leaf
    assert g["edges"]["fetch"] == ["compile", "publish"]
    assert g["edges"]["compile"] == ["publish"]
    assert g["edges"]["publish"] == []


def test_multiple_roots_and_leaves():
    # a and b are independent roots; c depends on both; d depends on a only.
    src = '''workflow fan {
      step a { let x = 1 }
      step b { let y = 2 }
      step c after a, b { let z = 3 }
      step d after a { let w = 4 }
    }'''
    g = compile_nodus_flow(src)
    assert set(g["start"]) == {"a", "b"}
    assert set(g["end"]) == {"c", "d"}
    assert set(g["edges"]["a"]) == {"c", "d"}


def test_goal_kind():
    g = compile_nodus_flow("goal win {\n  step a { let x = 1 }\n  step b after a { let y = 2 }\n}")
    assert g["execution_kind"] == "goal"
    assert g["start"] == ["a"]
    assert g["end"] == ["b"]


def test_no_workflow_block_raises():
    with pytest.raises(ValueError, match="must contain a `workflow"):
        compile_nodus_flow("let x = 1")


def test_multiple_blocks_without_name_raises():
    src = "workflow a { step s1 { let x=1 } }\nworkflow b { step s2 { let y=2 } }"
    with pytest.raises(ValueError, match="multiple workflow"):
        compile_nodus_flow(src)


def test_named_disambiguation():
    src = "workflow a { step s1 { let x=1 } }\nworkflow b { step s2 { let y=2 } }"
    assert compile_nodus_flow(src, "b")["workflow_name"] == "b"
    assert parse_nodus_workflow(src, "a").name == "a"


def test_unknown_name_raises():
    with pytest.raises(ValueError, match="no workflow/goal named"):
        compile_nodus_flow("workflow a { step s1 { let x=1 } }", "missing")


# --------------------------------------------------------------------------- #
# End-to-end: the composed source actually executes steps via the nodus VM
# --------------------------------------------------------------------------- #

def test_composed_source_executes_steps_in_dependency_order():
    """Proves RTR-1a: definition + run_workflow(name) runs steps in dep order."""
    from AINDY.nodus.runtime.embedding import NodusRuntime

    g = compile_nodus_flow(_LINEAR)
    # Mirror how run_nodus_workflow composes the runnable source.
    composed = f"{_LINEAR}\nrun_workflow({g['workflow_name']})\n"

    rt = NodusRuntime()
    src = composed.replace("let n = 1", 'print("fetch")') \
                  .replace("let m = 2", 'print("compile")') \
                  .replace("let p = 3", 'print("publish")')
    result = rt.run_source(src, filename="<wf>", initial_globals={}, host_globals={})

    assert result.get("ok") is True, result.get("error")
    out = result.get("stdout") or ""
    # fetch must precede compile and publish (dependency order).
    assert out.index("fetch") < out.index("compile")
    assert out.index("fetch") < out.index("publish")
    assert out.index("compile") < out.index("publish")
