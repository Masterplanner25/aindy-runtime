"""`agents.metadata` and `agents.updated_at` (APP-FR-* FR-13).

The durable identity of an agent is its **role** — `id` and `memory_namespace` are both
provider-independent. The *swappable* half (which vendor client is currently driving it)
had nowhere structured to live, so switching provider either looked like a brand-new agent
with no history, or got encoded as `provider=codex;workspace=...` inside `description`,
which works right up until something needs to query it.

Both columns are nullable and additive: unlike FR-8's `users.is_verified` there is nothing
to backfill, because "no metadata recorded" is exactly what `NULL` means. Reading code must
therefore treat absent metadata as empty regardless of row age — asserted below.

Marked `runtime_only` — without it CI collects nothing here (CI-MARKER-1).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from AINDY.db.models.agent import Agent

pytestmark = pytest.mark.runtime_only

_MIGRATION = Path("alembic/versions/0015_agents_metadata.py")


class TestColumnDefinition:
    def test_metadata_column_exists_and_is_jsonb(self):
        column = Agent.__table__.columns["metadata"]
        assert type(column.type).__name__ == "JSONB"

    def test_updated_at_column_exists(self):
        assert "updated_at" in Agent.__table__.columns

    def test_both_columns_are_nullable(self):
        """Additive means additive: a NOT NULL column would need a backfill, which is
        the difference between an additive migration and an outage (see FR-8)."""
        assert Agent.__table__.columns["metadata"].nullable is True
        assert Agent.__table__.columns["updated_at"].nullable is True

    def test_the_attribute_is_agent_metadata_but_the_column_is_metadata(self):
        """`metadata` is reserved on a SQLAlchemy declarative class (`Base.metadata`),
        so the ORM attribute has to differ. The **column** is what the app asked for and
        what raw SQL sees; this pins both halves so neither drifts."""
        assert Agent.agent_metadata.expression.name == "metadata"
        assert not isinstance(getattr(Agent, "metadata", None), type(Agent.agent_metadata))

    def test_declaring_metadata_directly_would_have_broken_the_class(self):
        """Documents *why* the attribute is renamed, rather than leaving it folklore:
        `Base.metadata` is SQLAlchemy's own MetaData object."""
        from AINDY.db.database import Base

        assert Base.metadata is Agent.metadata
        assert type(Agent.metadata).__name__ == "MetaData"

    def test_updated_at_refreshes_on_update(self):
        assert Agent.__table__.columns["updated_at"].onupdate is not None

    def test_no_existing_column_was_altered(self):
        """FR-13 is purely additive — the original eight columns must be untouched."""
        original = {
            "id", "name", "agent_type", "description",
            "owner_user_id", "is_active", "memory_namespace", "created_at",
        }
        assert original <= set(Agent.__table__.columns.keys())
        assert set(Agent.__table__.columns.keys()) == original | {"metadata", "updated_at"}


class TestOrmRoundTrip:
    @pytest.fixture
    def session(self):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Agent.__table__.create(bind=engine)
        maker = sessionmaker(bind=engine)
        db = maker()
        try:
            yield db
        finally:
            db.close()
            engine.dispose()

    def _agent(self, **kwargs):
        base = {
            "id": "a1", "name": "Runtime", "agent_type": "system",
            "memory_namespace": "runtime",
        }
        base.update(kwargs)
        return Agent(**base)

    def test_metadata_round_trips_a_dict(self, session):
        session.add(self._agent(agent_metadata={"provider": "codex", "workspace": "w1"}))
        session.commit()

        stored = session.query(Agent).one()
        assert stored.agent_metadata == {"provider": "codex", "workspace": "w1"}

    def test_absent_metadata_is_none_not_an_empty_dict(self, session):
        """The contract readers must code against: nothing was recorded. Every row that
        predates this migration looks exactly like this, and there is no backfill to
        change that."""
        session.add(self._agent())
        session.commit()

        assert session.query(Agent).one().agent_metadata is None

    def test_metadata_can_be_replaced_without_touching_identity(self, session):
        """The point of FR-13: swapping the vendor client must not look like a new
        agent. `id` and `memory_namespace` — the durable identity — are unchanged."""
        session.add(self._agent(agent_metadata={"provider": "codex"}))
        session.commit()

        stored = session.query(Agent).one()
        stored.agent_metadata = {"provider": "claude"}
        session.commit()

        refreshed = session.query(Agent).one()
        assert refreshed.agent_metadata == {"provider": "claude"}
        assert refreshed.id == "a1"
        assert refreshed.memory_namespace == "runtime"

    def test_nested_structures_survive(self, session):
        payload = {"provider": "codex", "limits": {"rpm": 60}, "tags": ["a", "b"]}
        session.add(self._agent(agent_metadata=payload))
        session.commit()

        assert session.query(Agent).one().agent_metadata == payload


class TestMigrationContract:
    """The migration is verified against real Postgres by hand (blank-DB skip, additive
    upgrade, idempotent re-run, queryable JSONB, idempotent downgrade). These assertions
    guard the properties that are checkable statically and are easy to lose in an edit.
    """

    @pytest.fixture(scope="class")
    def source(self) -> str:
        assert _MIGRATION.exists(), f"{_MIGRATION} is missing"
        return _MIGRATION.read_text(encoding="utf-8")

    def test_revision_chain_is_correct(self, source):
        assert re.search(r'^revision = "0015"', source, re.M)
        assert re.search(r'^down_revision = "0014"', source, re.M)

    def test_head_constant_matches(self):
        """The packaged constant `bootstrap-schema` stamps — the alembic/ tree is not in
        the wheel, so this is the only head a wheel install can see."""
        from AINDY.db.alembic_head import RUNTIME_ALEMBIC_HEAD_REVISION

        assert RUNTIME_ALEMBIC_HEAD_REVISION == "0015"

    def test_ddl_is_wrapped_in_a_table_existence_guard(self, source):
        """ALEMBIC-FRESH-DB-1. In compose, `alembic upgrade head` runs *before* the ORM
        create_all guard, so `agents` may not exist. `ADD COLUMN IF NOT EXISTS` alone is
        not enough — `ALTER TABLE missing_table` still raises UndefinedTable.
        """
        assert "pg_catalog.pg_tables" in source
        assert "tablename='agents'" in source
        # Both directions need the guard, not just upgrade.
        assert source.count("pg_catalog.pg_tables") >= 2

    def test_upgrade_is_idempotent_by_construction(self, source):
        upgrade = source.split("def upgrade")[1].split("def downgrade")[0]
        assert upgrade.count("ADD COLUMN IF NOT EXISTS") == 2

    def test_downgrade_drops_what_upgrade_created(self, source):
        downgrade = source.split("def downgrade")[1]
        assert "DROP COLUMN IF EXISTS updated_at" in downgrade
        assert "DROP COLUMN IF EXISTS metadata" in downgrade

    def test_migration_adds_no_not_null_and_so_needs_no_backfill(self, source):
        """FR-8's lesson: `alembic/` is absent from the wheel, so a migration-only
        backfill never runs on a wheel install. This migration avoids needing one — both
        columns are nullable, so there is nothing an unmigrated row is missing.
        """
        upgrade = source.split("def upgrade")[1].split("def downgrade")[0]
        assert "NOT NULL" not in upgrade
        assert "UPDATE agents" not in upgrade

    def test_no_reconcile_backfill_marker_is_needed_on_the_columns(self):
        """The FR-8 mechanism (`info={"reconcile_backfill": ...}`) exists for columns
        whose meaning depends on a backfill. These do not — assert none was added, so
        the reasoning above stays true if someone revisits it."""
        for name in ("metadata", "updated_at"):
            assert "reconcile_backfill" not in (Agent.__table__.columns[name].info or {})
