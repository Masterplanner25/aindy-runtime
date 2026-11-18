"""SQLAlchemy 2.1: a bare `postgresql://` means psycopg 3, and this package ships psycopg2.

`AINDY.config.normalize_database_url` names the driver. Every engine the runtime creates must pass its
URL through it; the census below is derived from the source, so a new `create_engine` that skips it
fails here rather than on the first PostgreSQL deployment.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
import sqlalchemy
from sqlalchemy.engine import make_url

from AINDY.config import normalize_database_url

pytestmark = pytest.mark.runtime_only

_ROOT = Path(__file__).resolve().parents[2]
_ENGINE_FACTORIES = {"create_engine", "engine_from_config", "create_async_engine"}


@pytest.mark.parametrize(
    "given,expected",
    [
        ("postgresql://u:p@h:5432/aindy", "postgresql+psycopg2://u:p@h:5432/aindy"),
        ("POSTGRESQL://u@h/aindy", "postgresql+psycopg2://u@h/aindy"),
        # the rest of the URL is untouched, byte for byte (2.1 changed str(URL)'s escaping)
        ("postgresql://u:p%40ss%2Fx@h/my%20db?sslmode=require&options=-c%20a%3Db",
         "postgresql+psycopg2://u:p%40ss%2Fx@h/my%20db?sslmode=require&options=-c%20a%3Db"),
        ("postgresql+psycopg2://u@h/aindy", "postgresql+psycopg2://u@h/aindy"),
        ("postgresql+psycopg://u@h/aindy", "postgresql+psycopg://u@h/aindy"),
        ("sqlite:///x.db", "sqlite:///x.db"),
        ("sqlite://", "sqlite://"),
        ("", ""),
    ],
)
def test_normalize(given, expected):
    assert normalize_database_url(given) == expected
    assert normalize_database_url(expected) == expected  # idempotent


def test_a_normalized_bare_url_resolves_to_psycopg2():
    dialect = make_url(normalize_database_url("postgresql://u@h/aindy")).get_dialect()
    assert dialect.driver == "psycopg2"


def test_control_the_bare_url_alone_resolves_to_the_installed_sqlalchemys_default():
    """Why the function exists: on 2.1+ the bare URL means psycopg 3."""
    major, minor = (int(p) for p in sqlalchemy.__version__.split(".")[:2])
    expected = "psycopg" if (major, minor) >= (2, 1) else "psycopg2"
    assert make_url("postgresql://u@h/aindy").get_dialect().driver == expected


def _engine_sites() -> list[tuple[str, int, ast.Call]]:
    files = sorted((_ROOT / "AINDY").rglob("*.py")) + [_ROOT / "alembic" / "env.py"]
    sites = []
    for path in files:
        if "tests" in path.relative_to(_ROOT).parts:
            continue
        # utf-8-sig: a few modules start with a byte-order mark, which Python accepts and ast does not
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in _ENGINE_FACTORIES:
                sites.append((str(path.relative_to(_ROOT)), node.lineno, node))
    return sites


def _is_normalized(expr: ast.AST | None) -> bool:
    if isinstance(expr, ast.Call):
        name = getattr(expr.func, "id", None) or getattr(expr.func, "attr", None)
        return name in {"normalize_database_url", "get_url"}
    return False


def _url_argument(call: ast.Call) -> ast.AST | None:
    name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
    if name == "engine_from_config":
        config = call.args[0] if call.args else None
        if isinstance(config, ast.Dict):
            for key, value in zip(config.keys, config.values):
                if isinstance(key, ast.Constant) and key.value == "sqlalchemy.url":
                    return value
        return None
    if call.args:
        return call.args[0]
    return next((kw.value for kw in call.keywords if kw.arg == "url"), None)


def test_every_engine_the_runtime_creates_names_its_driver():
    sites = _engine_sites()
    assert len(sites) >= 6, sites  # derived census: database, schema_ops, health x2, startup, alembic

    unwrapped = [f"{path}:{line}" for path, line, call in sites if not _is_normalized(_url_argument(call))]
    assert unwrapped == [], (
        "these engines take a URL that skips normalize_database_url, so a bare postgresql:// "
        f"reaches psycopg 3 under SQLAlchemy 2.1: {unwrapped}"
    )


def test_workflow_inline_scripts_name_the_driver_too():
    """CI runs Python inline in workflow YAML; #981's first run failed on one such `create_engine`.

    `psycopg2.connect(...)` is deliberately not covered: it is libpq, not SQLAlchemy, and takes the
    bare URL (a `+psycopg2` scheme would break it).
    """
    workflows = sorted((_ROOT / ".github" / "workflows").glob("*.yml"))
    sites = [
        (str(path.relative_to(_ROOT)), number, line.strip())
        for path in workflows
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1)
        if any(f"{factory}(" in line for factory in _ENGINE_FACTORIES)
    ]
    assert sites, "no engine sites found in workflows; the scan is broken"
    unwrapped = [f"{path}:{number}" for path, number, line in sites if "normalize_database_url(" not in line]
    assert unwrapped == [], unwrapped


def test_alembic_get_url_normalizes():
    """`get_url` is accepted above on the strength of this check."""
    tree = ast.parse((_ROOT / "alembic" / "env.py").read_text(encoding="utf-8"))
    get_url = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "get_url")
    returns = [n.value for n in ast.walk(get_url) if isinstance(n, ast.Return)]
    assert returns and all(
        isinstance(r, ast.Call) and getattr(r.func, "id", None) == "normalize_database_url" for r in returns
    )
