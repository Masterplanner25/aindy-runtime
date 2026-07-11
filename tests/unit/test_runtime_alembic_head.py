"""Guards for the packaged runtime Alembic head constant + the stamp helper.

The bootstrap-schema command stamps alembic_version_runtime from a packaged constant
(the alembic/ scripts dir is not in the wheel). These tests keep that constant honest
against the real migration chain and pin the stamp helper's idempotent behavior.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.runtime_only

from AINDY.db.alembic_head import (
    RUNTIME_ALEMBIC_HEAD_REVISION,
    RUNTIME_ALEMBIC_VERSION_TABLE,
    stamp_runtime_alembic_head,
)

# tests/unit/ -> repo root is parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_DIR = _REPO_ROOT / "alembic"


def test_head_constant_matches_scripts_dir():
    """RUNTIME_ALEMBIC_HEAD_REVISION must equal the actual scripts-dir head.

    Fails if a new migration is added without bumping the constant. The scripts dir
    exists in the repo/CI checkout (it is not shipped in the wheel), so this runs in
    CI without needing a database or env.py execution.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    if not _ALEMBIC_DIR.exists():  # pragma: no cover - only in a stripped wheel checkout
        pytest.skip("alembic scripts dir not present (installed-wheel layout)")

    cfg = Config()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()

    assert len(heads) == 1, f"expected a single linear head, got {heads}"
    assert heads[0] == RUNTIME_ALEMBIC_HEAD_REVISION, (
        f"packaged head constant {RUNTIME_ALEMBIC_HEAD_REVISION!r} != scripts head "
        f"{heads[0]!r} — bump RUNTIME_ALEMBIC_HEAD_REVISION in AINDY/db/alembic_head.py"
    )


def _version_rows(engine):
    from sqlalchemy import text
    with engine.connect() as conn:
        return [
            r[0]
            for r in conn.execute(
                text(f"SELECT version_num FROM {RUNTIME_ALEMBIC_VERSION_TABLE}")
            )
        ]


def test_stamp_creates_table_and_sets_head():
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    rev = stamp_runtime_alembic_head(engine)
    assert rev == RUNTIME_ALEMBIC_HEAD_REVISION
    assert _version_rows(engine) == [RUNTIME_ALEMBIC_HEAD_REVISION]


def test_stamp_is_idempotent_single_row():
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    stamp_runtime_alembic_head(engine)
    stamp_runtime_alembic_head(engine)
    # Never accumulates rows — always exactly one, at head.
    assert _version_rows(engine) == [RUNTIME_ALEMBIC_HEAD_REVISION]


def test_stamp_overwrites_stale_revision():
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    # Simulate a database stamped at an older revision.
    stamp_runtime_alembic_head(engine, revision="0001")
    assert _version_rows(engine) == ["0001"]
    stamp_runtime_alembic_head(engine)
    assert _version_rows(engine) == [RUNTIME_ALEMBIC_HEAD_REVISION]
