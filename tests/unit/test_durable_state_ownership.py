"""`ORCHESTRATOR-SPLIT-1` (b) — the durable-state ownership contract's INV-OWN-003, pinned.

Contract: ``docs/runtime/DURABLE_STATE_OWNERSHIP_CONTRACT.md`` §4, §8.

INV-OWN-003: **the host never resumes a guest workflow from the guest's store, and no guest-side
resumer exists.** The contract's argument that the entry's crash-overlap "needs an actor that
does not exist" rests on three facts. Two are pinned elsewhere — the worker declares
``NODUS_WORKFLOW_AUTOSWEEP=0`` (`test_guest_state_declaration.py`) and a resumed node runs a
fresh VM (DEC-012) — and the third is pinned here: nothing under ``AINDY/`` imports the guest's
workflow store or runner. The day something does, this goes red and the contract's §4 must be
re-decided (which store is authoritative for the SAME run) before the import stays.

Derived from the source (green-check variant 12): every ``.py`` under ``AINDY/`` is parsed; the
census is asserted non-empty.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

pytestmark = pytest.mark.runtime_only

_ROOT = pathlib.Path(__file__).resolve().parents[2] / "AINDY"

#: The guest's durability vocabulary (store 4). Importing any of it from the host is the moment
#: the host starts READING guest run state — the trigger the contract's §7 names for reopening
#: option (a).
_GUEST_STORE_MODULES = ("nodus_lang_workflow",)


def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_host_imports_nothing_from_the_guest_workflow_store():
    files = [p for p in _ROOT.rglob("*.py") if "__pycache__" not in p.parts]
    assert len(files) > 200, "census is implausibly small — the derivation broke"
    offenders = [
        p.relative_to(_ROOT.parent).as_posix()
        for p in files
        if any(name == m or name.startswith(m + ".") for name in _imports_of(p) for m in _GUEST_STORE_MODULES)
    ]
    assert not offenders, (
        "INV-OWN-003: the host now imports the guest's workflow store — "
        f"{offenders}. Before this stays, decide which of `flow_runs` and the guest store is "
        "authoritative for the SAME run (DURABLE_STATE_OWNERSHIP_CONTRACT.md §4, §7); the "
        "crash-overlap the entry describes becomes reachable the moment the host READS it."
    )


def test_the_guest_sweep_is_declared_off_by_the_worker():
    """Cross-reference to the other pinned half: a benign duplicate of #611's assertion, kept
    here so this file states INV-OWN-003 whole."""
    from AINDY.runtime.nodus_worker import declare_guest_state_environment

    env: dict[str, str] = {}
    declare_guest_state_environment(env)
    assert env["NODUS_WORKFLOW_AUTOSWEEP"] == "0"


def test_only_flow_and_agent_units_are_reconstructible():
    """INV-OWN-002's tail: a resume that crosses a transport is rebuilt from the AUTHORITY row
    for exactly the two unit types that have one; anything else must never be routed where the
    live closure cannot follow it."""
    from AINDY.core.resume_reconstruction import RECONSTRUCTIBLE_EU_TYPES

    assert RECONSTRUCTIBLE_EU_TYPES == frozenset({"flow", "agent"})
