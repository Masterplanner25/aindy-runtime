"""
MCP-SDK-2X-1 — the `mcp` SDK cap must hold in BOTH places that install it.

`mcp 2.0.0` removed the 1.x low-level `Server.list_tools()` decorator that
`nodus-mcp` 0.1.2 is built on, so an unbounded `mcp>=1.0.0` resolves to an SDK
that breaks `NodusServer.__init__` — reddening CI and shipping a broken
`pip install aindy-runtime[mcp]`.

The cap lives in two independent files: the `[mcp]` extra in `pyproject.toml`, and
the "Install MCP extra" step in `runtime-ci.yml`, which installs the packages
directly rather than through the extra. Capping only one leaves CI resolving past
it — that asymmetry is exactly what this test exists to catch.

Delete this test when the cap is lifted (a nodus-mcp release targeting mcp 2.x).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.runtime_only

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_CI_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "runtime-ci.yml"

# Any `mcp` requirement string that is not part of a longer package name
# (`nodus-mcp`, `mcp-types`) — the quoted spec as pip would receive it.
_MCP_REQUIREMENT = re.compile(r'"(?<![\w-])(mcp(?![\w-])[^"]*)"')


def _mcp_specs(text: str) -> list[str]:
    return [m for m in _MCP_REQUIREMENT.findall(text) if not m.startswith("mcp-")]


def _is_capped(spec: str) -> bool:
    return "<2" in spec.replace(" ", "")


def test_pyproject_mcp_extra_caps_the_sdk():
    specs = _mcp_specs(_PYPROJECT.read_text(encoding="utf-8"))
    assert specs, "no `mcp` requirement found in pyproject.toml — did the extra move?"
    for spec in specs:
        assert _is_capped(spec), (
            f"pyproject.toml declares {spec!r} with no upper bound; "
            "mcp 2.x breaks nodus-mcp (MCP-SDK-2X-1)"
        )


def test_ci_workflow_mcp_install_caps_the_sdk():
    """The CI step installs mcp directly, so it needs its own copy of the cap."""
    specs = _mcp_specs(_CI_WORKFLOW.read_text(encoding="utf-8"))
    assert specs, "no `mcp` requirement found in runtime-ci.yml — did the step move?"
    for spec in specs:
        assert _is_capped(spec), (
            f"runtime-ci.yml installs {spec!r} with no upper bound; CI will resolve "
            "to mcp 2.x and fail the live round-trip test (MCP-SDK-2X-1)"
        )
