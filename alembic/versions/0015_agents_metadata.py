"""agents metadata + updated_at (APP-FR-* FR-13)

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-15

FR-13 adds a structured ``metadata`` bag and an ``updated_at`` stamp to ``agents``.

The motivating shape: an agent's durable identity is its **role**
(``development.main-runtime``), with the vendor client as swappable detail. The durable
half already worked — ``id`` and ``memory_namespace`` are provider-independent — but the
swappable half had nowhere structured to live, so switching provider either looked like a
brand-new agent with no history, or got encoded as ``provider=codex;workspace=...`` inside
``description``, which works right up until something needs to query it.

``updated_at`` comes along because the table had ``created_at`` and no counterpart: a
metadata edit left no trace of when identity last changed.

**Purely additive.** Both columns are nullable with no NOT NULL constraint, so unlike
FR-8 (``users.is_verified``) there is nothing to backfill — an existing row with no
metadata is correctly represented by ``NULL``, and reading code must treat absent metadata
as "none recorded" regardless of when the row was written. That is why this migration has
no ``UPDATE`` and needs no ``reconcile_backfill`` marker on the ORM columns.

**Column vs attribute name.** The column is ``metadata`` — what the app asked for and what
raw SQL sees. The ORM attribute is ``Agent.agent_metadata``, because ``metadata`` is
reserved on a SQLAlchemy declarative class (``Base.metadata``).

Idempotent and blank-DB safe (ALEMBIC-FRESH-DB-1). In Docker compose ``alembic upgrade
head`` runs before the ORM ``create_all`` schema guard, so ``agents`` may not exist yet:
the table-existence guard skips and Phase 5 bootstraps the whole table, these columns
included, from ORM metadata. On an existing deployment the block runs and
``ADD COLUMN IF NOT EXISTS`` no-ops when the columns are already present. Note that
``IF NOT EXISTS`` on the column alone would **not** be sufficient — ``ALTER TABLE
missing_table`` still raises ``UndefinedTable``.

``downgrade()`` drops both columns.
"""

from alembic import op


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='agents' AND schemaname='public'
          ) THEN
            ALTER TABLE agents
              ADD COLUMN IF NOT EXISTS metadata JSONB NULL;
            ALTER TABLE agents
              ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NULL DEFAULT now();
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='agents' AND schemaname='public'
          ) THEN
            ALTER TABLE agents DROP COLUMN IF EXISTS updated_at;
            ALTER TABLE agents DROP COLUMN IF EXISTS metadata;
          END IF;
        END $$
        """
    )
