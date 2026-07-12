"""effect_records tenant/session attribution columns (MEB-3b)

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-11

MEB-3b (Mediated Effect Boundary program) adds two nullable attribution columns to
``effect_records`` so each idempotency/effect row records *which* tenant/session produced
it: ``tenant_id`` (== user_id in the single-user-per-tenant model) and ``session_id`` (e.g.
a multi-tenant MCP session). Attribution/audit only — neither column is part of the
``action_id`` dedup hash, so this is purely additive and cannot change dedup behavior.

Idempotent and blank-DB safe (ALEMBIC-FRESH-DB-1). On a blank database ``alembic upgrade
head`` runs before the ORM ``create_all`` schema guard, so ``effect_records`` may not exist
yet; the table-existence guard skips and Phase 5 bootstraps the full table (columns
included) from ORM metadata. On an existing deployment the block runs and
``ADD COLUMN IF NOT EXISTS`` no-ops if a column is already present. ``downgrade()`` drops
both columns.
"""

from alembic import op


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='effect_records' AND schemaname='public'
          ) THEN
            ALTER TABLE effect_records ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(256);
            ALTER TABLE effect_records ADD COLUMN IF NOT EXISTS session_id VARCHAR(256);
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE effect_records DROP COLUMN IF EXISTS session_id")
    op.execute("ALTER TABLE effect_records DROP COLUMN IF EXISTS tenant_id")
