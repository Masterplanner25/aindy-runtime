"""FR-62 guard: every CLI command that reaches the database loads the model registry first.

The behavioural test is `tests/integration/test_cli_fresh_interpreter.py`, which runs one command in a
new interpreter. This census keeps the rest from regressing: it derives, from the AST of
`runtime_only.py`, every command function that imports a database layer, and requires a call to
`_load_model_registry()` in it. It is a supplement to that test, not a substitute.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.runtime_only

SOURCE = Path(__file__).resolve().parents[2] / "AINDY" / "runtime_only.py"

# A module under one of these reaches the database.
DB_LAYERS = (
    "AINDY.db.database",
    "AINDY.memory.",
    "AINDY.platform_layer.provider_keys",
    "AINDY.platform_layer.async_job_service",
)
# `bootstrap-schema` loads the registry through `AINDY.db.schema_contract`, which imports it at
# module scope; the schema check itself needs every table, so that import can't go missing silently.
EXEMPT = {"_bootstrap_schema"}


def _db_commands() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name in EXEMPT:
            continue
        modules = [n.module or "" for n in ast.walk(node) if isinstance(n, ast.ImportFrom)]
        if any(m == layer or m.startswith(layer) for m in modules for layer in DB_LAYERS):
            found[node.name] = node
    return found


def _calls_loader(fn: ast.FunctionDef) -> bool:
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_load_model_registry"
        for n in ast.walk(fn)
    )


def test_the_census_finds_the_commands():
    names = set(_db_commands())
    assert {"_backfill_memory_paths", "_rotate_provider_keys", "_settle_stale_job_units"} <= names, names
    assert len(names) >= 6, names


def test_every_database_command_loads_the_model_registry():
    missing = sorted(name for name, fn in _db_commands().items() if not _calls_loader(fn))
    assert not missing, f"these commands open the database without _load_model_registry(): {missing}"
