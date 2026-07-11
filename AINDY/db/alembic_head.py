"""Runtime Alembic head revision + a stamp helper for the bootstrap-schema command.

Why a packaged constant instead of reading the scripts dir? The ``alembic/`` scripts
directory lives at the repo root, and ``[tool.setuptools.packages.find]`` only ships
``AINDY*`` — so the migration scripts are **not** in the installed wheel. A blessed
``aindy-runtime bootstrap-schema`` must therefore know the runtime head from packaged
code, not from the (absent) scripts dir, so it works from a bare ``pip install`` and not
only inside the Docker image (which COPYs ``alembic/`` separately).

BUMP PROTOCOL: when you add ``alembic/versions/NNNN_*.py``, set
``RUNTIME_ALEMBIC_HEAD_REVISION`` to the new head revision id.
``tests/unit/test_runtime_alembic_head.py`` fails if this drifts from the actual
scripts-dir head, so a forgotten bump is caught in CI.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

# Alembic version table for the runtime (distinct from the monolith's plain
# ``alembic_version``). Set in ``alembic/env.py`` via ``version_table=``.
RUNTIME_ALEMBIC_VERSION_TABLE = "alembic_version_runtime"

# The current head of the runtime migration chain (alembic/versions/0001..NNNN).
RUNTIME_ALEMBIC_HEAD_REVISION = "0010"


def stamp_runtime_alembic_head(
    engine: Engine,
    revision: str = RUNTIME_ALEMBIC_HEAD_REVISION,
) -> str:
    """Stamp ``alembic_version_runtime`` to ``revision`` without running migrations.

    Mirrors ``alembic stamp head`` for the runtime's linear, single-head history:
    ensures the version table exists, then replaces its single row with ``revision``.
    Idempotent — re-running writes the same value. Runs in one transaction.

    Used after a ``create_all``-style bootstrap so a database built from packaged ORM
    metadata carries a proper Alembic baseline; a later ``alembic upgrade head`` then
    sees the DB already at head instead of replaying the whole chain onto live tables.

    The table name is a fixed module constant (never user input), so interpolating it
    into the DDL/DML strings carries no injection surface.
    """
    table = RUNTIME_ALEMBIC_VERSION_TABLE
    with engine.begin() as conn:
        conn.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {table} "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(
            text(f"INSERT INTO {table} (version_num) VALUES (:rev)"),
            {"rev": revision},
        )
    return revision
