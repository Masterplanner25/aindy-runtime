"""FR-43 end to end: `bootstrap-schema` over a PostgreSQL column narrower than the model.

The app's first boot of 2.22.0 printed `(no table changes)` and `stamped … 0020` while
`system_events.source` was still `varchar(32)`. This rebuilds that database in a throwaway
PostgreSQL database: runtime schema from the packaged models, the column narrowed back to the
pre-0020 width, the version table at `0019`. It then runs the real command.

Only PostgreSQL can reproduce it: SQLite does not enforce a declared length, and the bound
check is skipped there.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


@pytest.fixture
def fresh_pg(monkeypatch):
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("FR-43 reproduces on PostgreSQL only")
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    name = f"aindy_fr43_{uuid.uuid4().hex[:10]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(make_url(url).set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        # `_bootstrap_schema` imports its engine from here
        monkeypatch.setattr("AINDY.db.database.engine", engine)
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _source_width(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_name='system_events' AND column_name='source'"
        )).scalar_one()


def _version(engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version_runtime")).scalar_one()


def _run(reconcile: bool) -> int:
    from AINDY import runtime_only

    with pytest.raises(SystemExit) as exc:
        runtime_only._bootstrap_schema(reconcile=reconcile)
    return int(exc.value.code)


def _pre_0020_database(engine):
    from AINDY.db.alembic_head import stamp_runtime_alembic_head
    from AINDY.db.schema_contract import ensure_runtime_schema
    from tests.helpers.runtime import import_runtime_model_registry

    import_runtime_model_registry()
    assert ensure_runtime_schema(engine, allow_bootstrap=True).ok
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE system_events ALTER COLUMN source TYPE VARCHAR(32)"))
    stamp_runtime_alembic_head(engine, "0019")
    assert _source_width(engine) == 32, "liveness: the database is in the app's pre-0020 state"


def test_a_widening_exits_3_stamps_nothing_and_reconcile_widens_then_stamps(fresh_pg, capsys):
    _pre_0020_database(fresh_pg)

    assert _run(reconcile=False) == 3, "the widening must be visible, as exit 3 (FR-14's code)"
    out = capsys.readouterr()
    assert "no table changes" not in out.out and "stamped" not in out.out
    assert "system_events" in out.err and "source" in out.err
    assert _version(fresh_pg) == "0019", "★ the head was stamped over a column 0020 never widened"
    assert _source_width(fresh_pg) == 32

    assert _run(reconcile=True) == 0
    out = capsys.readouterr().out
    assert _source_width(fresh_pg) == 128
    assert _version(fresh_pg) == "0020"
    assert "from revision 0019 to 0020" in out

    assert _run(reconcile=False) == 0, "stable on re-run: the reconcile stuck"


def test_a_narrowing_exits_4_and_stamps_nothing(fresh_pg):
    """The other direction is never automatic: existing values may not fit."""
    _pre_0020_database(fresh_pg)
    with fresh_pg.begin() as conn:
        conn.execute(text("ALTER TABLE system_events ALTER COLUMN source TYPE VARCHAR(256)"))

    assert _run(reconcile=True) == 4
    assert _source_width(fresh_pg) == 256
    assert _version(fresh_pg) == "0019"
